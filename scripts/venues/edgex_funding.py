#!/usr/bin/env python3
"""EdgeX funding provider — StarkEx CLOB Perp DEX (read-only, V1 public REST).

Base: https://pro.edgex.exchange (sends a default User-Agent via http_util,
otherwise some endpoints return 403).

  GET /api/v1/public/meta/getMetaData          -> data.contractList[] (~292),
        contractId, contractName "BTCUSD", enableTrade, fundingRateIntervalMin
        (minutes as string, "240" = 4h), defaultTakerFeeRate, tick/step/min sizes.
  GET /api/v1/public/quote/getTicker?contractId -> data[0]: fundingRate (decimal
        per period), markPrice, lastPrice, fundingTime (last settle),
        nextFundingTime. NOTE: `data` is a one-element LIST, not a dict.

Funding settles per `fundingRateIntervalMin` (4h for majors). On-venue symbols
are USD-quoted (BTCUSD); we normalize to BTCUSDT internally. Trading needs the
V2 EIP-712 SDK — this module is scan/backtest only (`fetch_since` returns []
until the historical funding endpoint / V2 read path is wired in Phase 1.5).
"""

from __future__ import annotations

import os
import time
from typing import Any

from market.parallel_fetch import run_io_parallel
from venues.http_util import http_get_json

_BASE_URL = "https://pro.edgex.exchange"
_META_TTL_SEC = 300.0
_SNAPSHOT_TTL_SEC = 60.0
_DEFAULT_INTERVAL_H = 4.0

# EdgeX sits behind Cloudflare with strict per-IP rate limits and NO batch
# ticker endpoint — funding/mark live only on the per-contract getTicker. A
# wide fan-out (the full ~292-contract list) trips a Cloudflare bot challenge
# that blocks the IP for minutes. So the scan universe is bounded to a curated
# whitelist of liquid bases (override with EDGEX_SCAN_BASES="BTC,ETH,...") and
# fetched at low concurrency (EDGEX_SCAN_WORKERS, default 3). Tickers are cached
# for _SNAPSHOT_TTL_SEC so repeated scans don't re-hammer the endpoint.
_DEFAULT_SCAN_BASES = (
    "BTC ETH SOL XRP BNB DOGE ADA AVAX LINK SUI LTC BCH DOT NEAR APT ARB OP "
    "TIA SEI WLD TON TRX ATOM FIL INJ ORDI PEPE WIF AAVE UNI"
).split()
_DEFAULT_SCAN_WORKERS = 3


def _base_from_contract_name(name: str) -> str:
    """EdgeX contractName 'BTCUSD' -> internal base 'BTC'."""
    s = name.upper()
    return s[:-3] if s.endswith("USD") else s


class EdgexFundingProvider:
    """FundingProvider interface for EdgeX (read-only V1 public REST)."""

    venue_id: str = "edgex"

    def __init__(self, base_url: str = _BASE_URL) -> None:
        self._base_url = base_url
        # base(str) -> contract metadata; refreshed every _META_TTL_SEC.
        self._meta_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
        # (quote) -> (ts, rows); rate-limit guard, refreshed every _SNAPSHOT_TTL_SEC.
        self._snapshot_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def _scan_bases(self) -> list[str]:
        """Bounded scan universe (env EDGEX_SCAN_BASES overrides the default)."""
        env = os.environ.get("EDGEX_SCAN_BASES", "").strip()
        bases = (
            [b.strip().upper() for b in env.split(",") if b.strip()]
            if env
            else list(_DEFAULT_SCAN_BASES)
        )
        contracts = self._contracts()
        return [b for b in bases if b in contracts]

    def _scan_workers(self) -> int:
        try:
            return max(
                1, int(os.environ.get("EDGEX_SCAN_WORKERS", _DEFAULT_SCAN_WORKERS))
            )
        except ValueError:
            return _DEFAULT_SCAN_WORKERS

    # ------------------------------------------------------------------
    # HTTP + metadata (contractId map, intervals, fees, trade rules)
    # ------------------------------------------------------------------

    def _get(self, path: str) -> Any:
        payload = http_get_json(f"{self._base_url}{path}", timeout=20, retries=2)
        if isinstance(payload, dict):
            return payload.get("data", payload)
        return payload

    def _contracts(self) -> dict[str, dict[str, Any]]:
        """base -> {contract_id, interval_h, taker_pct, tick/step/min sizes}. 5min cache."""
        now = time.time()
        if self._meta_cache and now - self._meta_cache[0] < _META_TTL_SEC:
            return self._meta_cache[1]
        data = self._get("/api/v1/public/meta/getMetaData")
        rows = data.get("contractList", []) if isinstance(data, dict) else []
        out: dict[str, dict[str, Any]] = {}
        for c in rows:
            if not c.get("enableTrade", False):
                continue
            name = str(c.get("contractName", ""))
            if not name.endswith("USD"):
                continue
            base = _base_from_contract_name(name)
            if not base:
                continue
            interval_min = float(c.get("fundingRateIntervalMin", 0) or 0)
            out[base] = {
                "contract_id": str(c.get("contractId", "")),
                "interval_h": (interval_min / 60.0)
                if interval_min > 0
                else _DEFAULT_INTERVAL_H,
                "taker_pct": float(c.get("defaultTakerFeeRate", 0) or 0) * 100,
                "tick_size": float(c.get("tickSize", 0) or 0),
                "step_size": float(c.get("stepSize", 0) or 0),
                "min_order_size": float(c.get("minOrderSize", 0) or 0),
            }
        self._meta_cache = (now, out)
        return out

    def contract_id_for_base(self, base: str) -> str | None:
        """Resolve a base asset (e.g. 'BTC') to EdgeX's numeric contractId string."""
        m = self._contracts().get(base.upper())
        return m["contract_id"] if m and m.get("contract_id") else None

    def contract_meta_for_base(self, base: str) -> dict[str, Any] | None:
        """Full cached metadata row (contract_id, interval_h, sizes) for a base asset."""
        return self._contracts().get(base.upper())

    def contract_meta_map(self) -> dict[str, dict[str, Any]]:
        """Full base -> metadata map (cached); used for contractId reverse lookup."""
        return self._contracts()

    def _ticker_row(self, contract_id: str) -> dict[str, Any]:
        data = self._get(f"/api/v1/public/quote/getTicker?contractId={contract_id}")
        if isinstance(data, list):
            return data[0] if data else {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # FundingProvider interface
    # ------------------------------------------------------------------

    def fetch_all(
        self, quote: str = "USDT", workers: int | None = None
    ) -> list[dict[str, Any]]:
        """Funding/mark for the bounded scan universe (whitelist + snapshot cache).

        NOTE: not the full contract list — see the rate-limit comment at module
        top. Per-contract tickers are fetched at low concurrency and cached.
        """
        q = quote.upper()
        now = time.time()
        cached = self._snapshot_cache.get(q)
        if cached and now - cached[0] < _SNAPSHOT_TTL_SEC:
            return cached[1]

        contracts = self._contracts()
        bases = self._scan_bases()

        def _one(base: str) -> tuple[str, dict[str, Any] | None]:
            row = self._ticker_row(contracts[base]["contract_id"])
            if not row:
                return base, None
            return base, {
                "symbol": f"{base}{q}",
                "rate_pct": float(row.get("fundingRate", 0) or 0) * 100,
                "next_funding_ts": int(row.get("nextFundingTime", 0) or 0),
                "mark_price": float(row.get("markPrice", 0) or 0),
                "index_price": 0.0,  # EdgeX has no public index price
            }

        raw = run_io_parallel(
            bases,
            _one,
            max_workers=workers or self._scan_workers(),
            swallow_errors=True,
        )
        rows = [v for v in raw.values() if v]
        self._snapshot_cache[q] = (now, rows)
        return rows

    def fetch_interval_map(self, quote: str = "USDT") -> dict[str, float]:
        """Per-symbol settlement interval (hours) from metadata — majors are 4h."""
        try:
            contracts = self._contracts()
        except Exception:
            return {}
        q = quote.upper()
        return {f"{base}{q}": m["interval_h"] for base, m in contracts.items()}

    def fetch_current(self, symbol: str) -> dict[str, Any]:
        base = symbol.upper().removesuffix("USDT")
        meta = self._contracts().get(base)
        interval_h = meta["interval_h"] if meta else _DEFAULT_INTERVAL_H
        interval_ms = int(interval_h * 3600 * 1000)
        empty = {
            "rate_pct": 0.0,
            "next_funding_ts": 0,
            "interval_ms": interval_ms,
            "mark_price": 0.0,
            "last_settle_ts": 0,
        }
        if not meta:
            return empty
        try:
            row = self._ticker_row(meta["contract_id"])
        except Exception:
            # Rate-limited / transient: fall back to the cached snapshot if fresh.
            snap = self._snapshot_cache.get("USDT")
            if snap:
                hit = next((r for r in snap[1] if r["symbol"] == symbol.upper()), None)
                if hit:
                    return {
                        "rate_pct": hit["rate_pct"],
                        "next_funding_ts": hit["next_funding_ts"],
                        "interval_ms": interval_ms,
                        "mark_price": hit["mark_price"],
                        "last_settle_ts": hit["next_funding_ts"] - interval_ms,
                    }
            return empty
        if not row:
            return empty
        next_ts = int(row.get("nextFundingTime", 0) or 0)
        last_settle = int(row.get("fundingTime", 0) or 0)
        if last_settle <= 0 and next_ts > 0:
            last_settle = next_ts - interval_ms
        return {
            "rate_pct": float(row.get("fundingRate", 0) or 0) * 100,
            "next_funding_ts": next_ts,
            "interval_ms": interval_ms,
            "mark_price": float(row.get("markPrice", 0) or 0),
            "last_settle_ts": last_settle,
        }

    def fetch_since(
        self, symbol: str, start_ms: int, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        """Settled funding history — not yet available without account context.

        getFundingRatePage returns an empty list anonymously (plan §2.4). Phase 1.5
        will wire the historical endpoint or the V2 SDK read path.
        """
        return []
