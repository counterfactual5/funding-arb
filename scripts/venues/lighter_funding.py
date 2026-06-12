#!/usr/bin/env python3
"""Lighter funding rate provider — zk-rollup order-book Perp DEX (read-only).

API base: https://mainnet.zklighter.elliot.ai
  GET /api/v1/funding-rates      — current rates for all markets (multi-exchange
                                   comparison feed; filter exchange == "lighter").
                                   `rate` is a DECIMAL per 1h period (1e-4 = 0.01%).
  GET /api/v1/orderBookDetails   — market_id ↔ symbol mapping + last_trade_price.
  GET /api/v1/fundings           — settled history per market_id, resolution=1h.
                                   Timestamps in SECONDS; `rate` is a PERCENTAGE
                                   string with sign carried by `direction`
                                   ("long" = longs pay = positive).

Funding settles hourly: rate = clamp(1h avg premium) / 8 (8h-style rate paid
hourly, same model as Hyperliquid). Trading requires the zk SDK — this module
is scan/backtest only.
"""

from __future__ import annotations

import math
import time
from typing import Any

from venues.http_util import http_get_json

_BASE_URL = "https://mainnet.zklighter.elliot.ai"
_INTERVAL_MS = 3600_000  # 1 hour
_META_TTL_SEC = 300.0


def _next_hour_ts(now_ms: int | None = None) -> int:
    n = now_ms or int(time.time() * 1000)
    return (math.ceil(n / _INTERVAL_MS)) * _INTERVAL_MS


class LighterFundingProvider:
    """FundingProvider interface for Lighter (read-only public REST)."""

    venue_id: str = "lighter"

    def __init__(self, base_url: str = _BASE_URL) -> None:
        self._base_url = base_url
        # symbol(base) → {market_id, last_trade_price}
        self._meta_cache: tuple[float, dict[str, dict[str, Any]]] | None = None

    # ------------------------------------------------------------------
    # market metadata (market_id mapping + mark price proxy)
    # ------------------------------------------------------------------

    def _get(self, path: str) -> Any:
        return http_get_json(f"{self._base_url}{path}", timeout=20, retries=2)

    def _market_meta(self) -> dict[str, dict[str, Any]]:
        now = time.time()
        if self._meta_cache and now - self._meta_cache[0] < _META_TTL_SEC:
            return self._meta_cache[1]
        payload = self._get("/api/v1/orderBookDetails")
        out: dict[str, dict[str, Any]] = {}
        for row in (
            payload.get("order_book_details", []) if isinstance(payload, dict) else []
        ):
            if str(row.get("market_type", "perp")) != "perp":
                continue
            if str(row.get("status", "active")) != "active":
                continue
            sym = str(row.get("symbol", "")).upper()
            if not sym:
                continue
            out[sym] = {
                "market_id": int(row.get("market_id", -1)),
                "last_trade_price": float(row.get("last_trade_price", 0) or 0),
                "size_decimals": int(row.get("supported_size_decimals", 4) or 4),
                "price_decimals": int(row.get("supported_price_decimals", 2) or 2),
                "min_base_amount": float(row.get("min_base_amount", 0) or 0),
                "min_quote_amount": float(row.get("min_quote_amount", 0) or 0),
                # taker/maker fees as decimal fractions (0 while Lighter is zero-fee)
                "taker_fee": float(row.get("taker_fee", 0) or 0),
                "maker_fee": float(row.get("maker_fee", 0) or 0),
            }
        self._meta_cache = (now, out)
        return out

    def market_id_for_base(self, base: str) -> int | None:
        """Resolve a base asset (e.g. 'BTC') to Lighter's numeric market_id."""
        m = self._market_meta().get(base.upper())
        if m is None or int(m.get("market_id", -1)) < 0:
            return None
        return int(m["market_id"])

    def market_meta_for_base(self, base: str) -> dict[str, Any] | None:
        """Full cached metadata row for a base asset (market_id, decimals, minimums)."""
        return self._market_meta().get(base.upper())

    # ------------------------------------------------------------------
    # FundingProvider interface
    # ------------------------------------------------------------------

    def fetch_all(self, quote: str = "USDT") -> list[dict[str, Any]]:
        payload = self._get("/api/v1/funding-rates")
        rows = payload.get("funding_rates", []) if isinstance(payload, dict) else []
        meta = self._market_meta()
        next_ts = _next_hour_ts()
        out: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("exchange", "")) != "lighter":
                continue
            base = str(row.get("symbol", "")).upper()
            if not base:
                continue
            m = meta.get(base, {})
            out.append(
                {
                    "symbol": f"{base}{quote.upper()}",
                    "rate_pct": float(row.get("rate", 0) or 0) * 100,
                    "next_funding_ts": next_ts,
                    "mark_price": float(m.get("last_trade_price", 0.0)),
                    "index_price": 0.0,  # Lighter has no public index/oracle price
                }
            )
        return out

    def fetch_interval_map(self, quote: str = "USDT") -> dict[str, float]:
        """Lighter settles every hour for all markets."""
        try:
            meta = self._market_meta()
        except Exception:
            return {}
        return {f"{base}{quote.upper()}": 1.0 for base in meta}

    def fetch_current(self, symbol: str) -> dict[str, Any]:
        base = symbol.upper().removesuffix("USDT")
        next_ts = _next_hour_ts()
        empty = {
            "rate_pct": 0.0,
            "next_funding_ts": next_ts,
            "interval_ms": _INTERVAL_MS,
            "mark_price": 0.0,
            "last_settle_ts": next_ts - _INTERVAL_MS,
        }
        try:
            rows = self.fetch_all()
        except Exception:
            return empty
        for r in rows:
            if r["symbol"].removesuffix("USDT") == base:
                return {
                    "rate_pct": r["rate_pct"],
                    "next_funding_ts": r["next_funding_ts"],
                    "interval_ms": _INTERVAL_MS,
                    "mark_price": r["mark_price"],
                    "last_settle_ts": r["next_funding_ts"] - _INTERVAL_MS,
                }
        return empty

    def fetch_since(
        self, symbol: str, start_ms: int, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        """Settled funding history. Lighter API uses second-precision timestamps."""
        if start_ms <= 0:
            return []
        base = symbol.upper().removesuffix("USDT")
        meta = self._market_meta()
        m = meta.get(base)
        if m is None or m["market_id"] < 0:
            return []
        market_id = m["market_id"]

        out: list[dict[str, Any]] = []
        cursor_s = start_ms // 1000
        now_s = int(time.time())
        page_span_s = 500 * 3600  # 500 hourly settlements per page
        for _ in range(max_pages):
            if cursor_s >= now_s:
                break
            # count_back counts back from end_timestamp, so walk forward in
            # bounded windows to avoid dropping the earliest settlements.
            end_s = min(now_s, cursor_s + page_span_s)
            url = (
                f"/api/v1/fundings?market_id={market_id}&resolution=1h"
                f"&start_timestamp={cursor_s}&end_timestamp={end_s}&count_back=500"
            )
            try:
                payload = self._get(url)
            except Exception:
                break
            rows = payload.get("fundings", []) if isinstance(payload, dict) else []
            for row in rows:
                ts = int(row.get("timestamp", 0) or 0) * 1000
                if ts <= start_ms:
                    continue
                rate = float(row.get("rate", 0) or 0)  # already a percentage
                if str(row.get("direction", "long")) == "short":
                    rate = -rate
                out.append({"ts": ts, "rate_pct": rate})
            cursor_s = end_s + 1
            time.sleep(0.15)

        # Dedup by ts and sort ascending
        seen: dict[int, float] = {}
        for r in out:
            seen[r["ts"]] = r["rate_pct"]
        return [{"ts": t, "rate_pct": seen[t]} for t in sorted(seen)]
