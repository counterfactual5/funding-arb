#!/usr/bin/env python3
"""dYdX v4 funding provider — read-only via public indexer REST.

Indexer: https://indexer.dydx.trade
  GET /v4/perpetualMarkets                       — all markets (nextFundingRate, oraclePrice)
  GET /v4/perpetualMarkets?ticker=...            — single market
  GET /v4/historicalFunding/{ticker}             — settled funding history (hourly)
  GET /v4/orderbooks/perpetualMarket/{ticker}    — L2 book (best bid/ask mid)

Symbols are USD-quoted on-chain (BTC-USD); we normalize to BTCUSDT internally.

mark vs index: the indexer only exposes `oraclePrice`. With mark == index the
basis-blend model degenerates to the linear rate. Opt into a real mark/index
split by using the orderbook mid as the *index* proxy (mark stays oracle):
  - fetch_current(symbol, include_index_mid=True)   — per-symbol opt-in
  - DYDX_INDEX_MID=1                                — enables it in fetch_all
    (bounded to liquid bases; override the set with DYDX_MID_BASES="BTC,ETH,...")

Trading lives in venues/dydx.py (dry-run wired; live gated by DYDX_ENABLE_LIVE).
"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from typing import Any

from market.parallel_fetch import run_io_parallel
from venues.http_util import http_get_json

_BASE_URL = "https://indexer.dydx.trade"
_INTERVAL_H = 1.0
_INTERVAL_MS = int(_INTERVAL_H * 3600 * 1000)

# Bounded universe for fetch_all orderbook-mid enrichment (one HTTP per base).
# Keep it to liquid majors by default; override with DYDX_MID_BASES.
_DEFAULT_MID_BASES = (
    "BTC ETH SOL XRP BNB DOGE ADA AVAX LINK SUI LTC BCH DOT NEAR APT ARB OP "
    "TIA SEI WLD TON TRX ATOM FIL INJ PEPE WIF AAVE UNI"
).split()
_DEFAULT_MID_WORKERS = 4


def _index_mid_enabled() -> bool:
    return os.environ.get("DYDX_INDEX_MID", "").strip().lower() in ("1", "true", "yes")


def _mid_bases() -> set[str]:
    env = os.environ.get("DYDX_MID_BASES", "").strip()
    if env:
        return {b.strip().upper() for b in env.split(",") if b.strip()}
    return set(_DEFAULT_MID_BASES)


def _mid_workers() -> int:
    try:
        return max(1, int(os.environ.get("DYDX_MID_WORKERS", _DEFAULT_MID_WORKERS)))
    except ValueError:
        return _DEFAULT_MID_WORKERS


def _orderbook_mid(base_url: str, ticker: str, timeout: int = 10) -> float:
    """Best bid/ask mid from the public orderbook; 0 on any failure."""
    try:
        data = http_get_json(
            f"{base_url}/v4/orderbooks/perpetualMarket/{ticker}",
            timeout=timeout,
            retries=1,
        )
    except Exception:  # noqa: BLE001
        return 0.0
    if not isinstance(data, dict):
        return 0.0
    bids = data.get("bids") or []
    asks = data.get("asks") or []
    if not bids or not asks:
        return 0.0
    try:
        best_bid = float(bids[0]["price"])
        best_ask = float(asks[0]["price"])
    except (TypeError, KeyError, ValueError):
        return 0.0
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return 0.0
    return (best_bid + best_ask) / 2.0


def _next_hour_ts(now_ms: int | None = None) -> int:
    n = now_ms or int(time.time() * 1000)
    return int(math.ceil(n / _INTERVAL_MS) * _INTERVAL_MS)


def _ticker_from_symbol(symbol: str) -> str:
    s = symbol.upper()
    base = s[:-4] if s.endswith("USDT") else s
    return f"{base}-USD"


def _symbol_from_ticker(ticker: str) -> str:
    base = str(ticker).split("-")[0].upper()
    return f"{base}USDT"


def _parse_iso_ms(raw: Any) -> int:
    if not raw:
        return 0
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def _market_row(ticker: str, m: dict[str, Any], *, next_ts: int) -> dict[str, Any]:
    try:
        rate = float(m.get("nextFundingRate", 0) or 0)
    except (TypeError, ValueError):
        rate = 0.0
    try:
        oracle = float(m.get("oraclePrice", 0) or 0)
    except (TypeError, ValueError):
        oracle = 0.0
    return {
        "symbol": _symbol_from_ticker(ticker),
        "rate_pct": rate * 100.0,
        "next_funding_ts": next_ts,
        "mark_price": oracle,
        "index_price": oracle,
    }


class DydxFundingProvider:
    """FundingProvider interface for dYdX v4 (read-only indexer)."""

    venue_id: str = "dydx"

    def __init__(self, base_url: str = _BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")

    def _get(self, path: str) -> Any:
        return http_get_json(f"{self._base_url}{path}", timeout=25, retries=2)

    def fetch_all(self, quote: str = "USDT") -> list[dict[str, Any]]:
        data = self._get("/v4/perpetualMarkets")
        markets = data.get("markets", {}) if isinstance(data, dict) else {}
        next_ts = _next_hour_ts()
        out: list[dict[str, Any]] = []
        for ticker, m in markets.items():
            if not isinstance(m, dict):
                continue
            if str(m.get("status", "")).upper() not in ("ACTIVE", "CLOSE_ONLY"):
                continue
            if not str(ticker).endswith("-USD"):
                continue
            out.append(_market_row(str(ticker), m, next_ts=next_ts))
        if _index_mid_enabled():
            self._enrich_index_mids(out)
        return out

    def _enrich_index_mids(self, rows: list[dict[str, Any]]) -> None:
        """Replace index_price with the orderbook mid for liquid bases (in place).

        One HTTP call per base, parallelized with a small worker pool and
        bounded to _mid_bases() so a full-universe scan (~300 markets) does
        not fan out into hundreds of orderbook requests.
        """
        wanted = _mid_bases()
        targets = [
            row
            for row in rows
            if row["symbol"][:-4] in wanted  # strip USDT suffix
        ]
        if not targets:
            return

        def _fetch(symbol: str) -> tuple[str, float]:
            return symbol, _orderbook_mid(self._base_url, _ticker_from_symbol(symbol))

        mids = run_io_parallel(
            [row["symbol"] for row in targets],
            _fetch,
            max_workers=_mid_workers(),
            swallow_errors=True,
        )
        for row in targets:
            mid = mids.get(row["symbol"], 0.0) or 0.0
            if mid > 0:
                row["index_price"] = mid

    def fetch_interval_map(self, quote: str = "USDT") -> dict[str, float]:
        return {row["symbol"]: _INTERVAL_H for row in self.fetch_all(quote)}

    def fetch_current(
        self, symbol: str, *, include_index_mid: bool = False
    ) -> dict[str, Any]:
        """Per-symbol funding snapshot.

        include_index_mid=True fetches the L2 orderbook and sets
        `index_price` to the mid quote, instead of the oracle price.
        This is required for proper mark-vs-index (basis) separation —
        dYdX v4 has no separate index endpoint, so the on-chain mid
        is the closest proxy. The extra HTTP is opt-in because watcher
        loops call fetch_current per symbol.
        """
        ticker = _ticker_from_symbol(symbol)
        data = self._get(f"/v4/perpetualMarkets?ticker={ticker}")
        markets = data.get("markets", {}) if isinstance(data, dict) else {}
        m = markets.get(ticker) or {}
        if not m:
            return {
                "rate_pct": 0.0,
                "next_funding_ts": 0,
                "interval_ms": _INTERVAL_MS,
                "mark_price": 0.0,
                "index_price": 0.0,
                "last_settle_ts": 0,
            }
        next_ts = _next_hour_ts()
        row = _market_row(ticker, m, next_ts=next_ts)
        index_price = row["index_price"]
        if include_index_mid:
            index_price = _orderbook_mid(self._base_url, ticker)
            if index_price <= 0:
                index_price = row["index_price"]
        return {
            "rate_pct": row["rate_pct"],
            "next_funding_ts": next_ts,
            "interval_ms": _INTERVAL_MS,
            "mark_price": row["mark_price"],
            "index_price": index_price,
            "last_settle_ts": next_ts - _INTERVAL_MS,
        }

    def fetch_since(
        self, symbol: str, start_ms: int, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        ticker = _ticker_from_symbol(symbol)
        out: list[dict[str, Any]] = []
        before: str | None = None
        for _ in range(max(1, max_pages)):
            path = f"/v4/historicalFunding/{ticker}?limit=100"
            if before:
                path += f"&effectiveBeforeOrAt={before}"
            data = self._get(path)
            rows = data.get("historicalFunding", []) if isinstance(data, dict) else []
            if not rows:
                break
            stop = False
            for row in rows:
                ts = _parse_iso_ms(row.get("effectiveAt"))
                if ts < start_ms:
                    stop = True
                    break
                try:
                    rate = float(row.get("rate", 0) or 0)
                except (TypeError, ValueError):
                    rate = 0.0
                out.append({"ts": ts, "rate_pct": rate * 100.0})
            if stop:
                break
            before = str(rows[-1].get("effectiveAt", ""))
            if not before:
                break
        out.sort(key=lambda r: r["ts"])
        return out
