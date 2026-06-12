#!/usr/bin/env python3
"""dYdX v4 funding provider — read-only via public indexer REST.

Indexer: https://indexer.dydx.trade
  GET /v4/perpetualMarkets              — all markets (nextFundingRate, oraclePrice)
  GET /v4/perpetualMarkets?ticker=...   — single market
  GET /v4/historicalFunding/{ticker}    — settled funding history (hourly)

Symbols are USD-quoted on-chain (BTC-USD); we normalize to BTCUSDT internally.
Trading (Cosmos wallet signing) is not implemented — scan / backtest only.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

from venues.http_util import http_get_json

_BASE_URL = "https://indexer.dydx.trade"
_INTERVAL_H = 1.0
_INTERVAL_MS = int(_INTERVAL_H * 3600 * 1000)


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
        return out

    def fetch_interval_map(self, quote: str = "USDT") -> dict[str, float]:
        return {row["symbol"]: _INTERVAL_H for row in self.fetch_all(quote)}

    def fetch_current(self, symbol: str) -> dict[str, Any]:
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
        return {
            "rate_pct": row["rate_pct"],
            "next_funding_ts": next_ts,
            "interval_ms": _INTERVAL_MS,
            "mark_price": row["mark_price"],
            "index_price": row["index_price"],
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
