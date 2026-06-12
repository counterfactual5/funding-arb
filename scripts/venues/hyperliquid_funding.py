#!/usr/bin/env python3
"""Hyperliquid funding rate provider — direct HTTP to Info API.

Provides current and historical funding rates for all Hyperliquid perps.
Hyperliquid uses 1-hour funding intervals (vs CEX 8h), which can create
more volatile funding rates — ideal for cross-venue arbitrage.

API docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
"""

from __future__ import annotations

import math
import time
from typing import Any

import requests

_BASE_URL = "https://api.hyperliquid.xyz"
_INTERVAL_MS = 3600_000  # 1 hour


def _post(body: dict[str, Any], base_url: str = _BASE_URL) -> Any:
    r = requests.post(f"{base_url}/info", json=body, timeout=20)
    r.raise_for_status()
    return r.json()


def _next_hour_ts(now_ms: int | None = None) -> int:
    """Return the ms timestamp of the next whole-hour boundary (UTC)."""
    n = now_ms or int(time.time() * 1000)
    return (math.ceil(n / _INTERVAL_MS)) * _INTERVAL_MS


def _coin_to_symbol(coin: str) -> str:
    """HL coin name → CEX-style symbol. 'BTC' → 'BTCUSDT'."""
    return f"{coin.upper()}USDT"


def _symbol_to_coin(symbol: str) -> str:
    """CEX-style symbol → HL coin name. 'BTCUSDT' → 'BTC'."""
    s = symbol.upper()
    return s[:-4] if s.endswith("USDT") else s


class HyperliquidFundingProvider:
    """FundingProvider interface for Hyperliquid."""

    venue_id: str = "hyperliquid"

    # ------------------------------------------------------------------
    # fetch_all — bulk endpoint
    # ------------------------------------------------------------------

    def fetch_all(self, quote: str = "USDT") -> list[dict[str, Any]]:
        """Fetch funding rates for all Hyperliquid perps.

        Returns list of dicts with keys:
            symbol (str)   — e.g. "BTCUSDT"
            rate_pct (float) — funding rate as percentage
            next_funding_ts (int) — next settlement timestamp (ms)
            mark_price (float) — current mark price
        """
        data = _post({"type": "metaAndAssetCtxs"})
        if not isinstance(data, list) or len(data) < 2:
            return []

        universe = data[0].get("universe", [])
        ctxs = data[1]
        now_ms = int(time.time() * 1000)
        next_ts = _next_hour_ts(now_ms)

        out: list[dict[str, Any]] = []
        for i, entry in enumerate(universe):
            coin = str(entry.get("name", ""))
            if not coin or i >= len(ctxs):
                continue
            ctx = ctxs[i]
            try:
                rate_float = float(ctx.get("funding", "0") or 0)
            except (ValueError, TypeError):
                rate_float = 0.0
            try:
                mark = float(ctx.get("markPx", 0) or 0)
            except (ValueError, TypeError):
                mark = 0.0
            try:
                oracle = float(ctx.get("oraclePx", 0) or 0)
            except (ValueError, TypeError):
                oracle = 0.0

            out.append(
                {
                    "symbol": _coin_to_symbol(coin),
                    "rate_pct": rate_float * 100,  # decimal → percentage
                    "next_funding_ts": next_ts,
                    "mark_price": mark,
                    "index_price": oracle,
                }
            )
        return out

    def fetch_interval_map(self, quote: str = "USDT") -> dict[str, float]:
        """Hyperliquid uses 1-hour funding intervals for all perps."""
        return {
            _coin_to_symbol(entry.get("name", "")): 1.0
            for entry in _post({"type": "metaAndAssetCtxs"})[0].get("universe", [])
            if entry.get("name")
        }

    # ------------------------------------------------------------------
    # fetch_current — single coin
    # ------------------------------------------------------------------

    def fetch_current(self, symbol: str) -> dict[str, Any]:
        """Fetch current funding for a single symbol.

        Args:
            symbol: CEX-style, e.g. "BTCUSDT"
        Returns:
            {rate_pct, next_funding_ts, interval_ms, mark_price, last_settle_ts}
        """
        coin = _symbol_to_coin(symbol)
        data = _post({"type": "metaAndAssetCtxs"})
        if not isinstance(data, list) or len(data) < 2:
            return {
                "rate_pct": 0.0,
                "next_funding_ts": 0,
                "interval_ms": _INTERVAL_MS,
                "mark_price": 0.0,
                "last_settle_ts": 0,
            }

        universe = data[0].get("universe", [])
        ctxs = data[1]

        for i, entry in enumerate(universe):
            if str(entry.get("name", "")).upper() == coin.upper() and i < len(ctxs):
                ctx = ctxs[i]
                try:
                    rate_float = float(ctx.get("funding", "0") or 0)
                except (ValueError, TypeError):
                    rate_float = 0.0
                try:
                    mark = float(ctx.get("markPx", 0) or 0)
                except (ValueError, TypeError):
                    mark = 0.0
                try:
                    oracle = float(ctx.get("oraclePx", 0) or 0)
                except (ValueError, TypeError):
                    oracle = 0.0
                now_ms = int(time.time() * 1000)
                next_ts = _next_hour_ts(now_ms)
                return {
                    "rate_pct": rate_float * 100,
                    "next_funding_ts": next_ts,
                    "interval_ms": _INTERVAL_MS,
                    "mark_price": mark,
                    "index_price": oracle,
                    "last_settle_ts": next_ts - _INTERVAL_MS,
                }

        return {
            "rate_pct": 0.0,
            "next_funding_ts": 0,
            "interval_ms": _INTERVAL_MS,
            "mark_price": 0.0,
            "last_settle_ts": 0,
        }

    # ------------------------------------------------------------------
    # fetch_since — historical rates
    # ------------------------------------------------------------------

    def fetch_since(
        self, symbol: str, start_ms: int, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        """Fetch historical funding rates since start_ms.

        Returns list of {ts, rate_pct} sorted ascending.

        Hyperliquid returns max 500 records per call (≈20 days at 1h interval).
        We paginate by advancing startTime to the last timestamp + 1.
        """
        if start_ms <= 0:
            return []
        coin = _symbol_to_coin(symbol)
        out: list[dict[str, Any]] = []
        cursor = start_ms

        for _ in range(max_pages):
            try:
                data = _post(
                    {"type": "fundingHistory", "coin": coin, "startTime": cursor}
                )
            except Exception:
                break
            if not isinstance(data, list) or not data:
                break

            page_items: list[dict[str, Any]] = []
            for row in data:
                ts = int(row.get("time", 0) or 0)
                if ts <= start_ms:
                    continue
                try:
                    rate = float(row.get("fundingRate", 0) or 0) * 100
                except (ValueError, TypeError):
                    rate = 0.0
                page_items.append({"ts": ts, "rate_pct": rate})

            if not page_items:
                break
            out.extend(page_items)
            # Advance cursor past the last record to get the next page
            last_ts = max(r["ts"] for r in page_items)
            if last_ts <= cursor:
                break
            cursor = last_ts + 1
            # If we got fewer than 500, we've reached the end
            if len(data) < 500:
                break

        out.sort(key=lambda x: x["ts"])
        return out

    # ------------------------------------------------------------------
    # fetch_interval_map
    # ------------------------------------------------------------------

    def fetch_interval_map(self, quote: str = "USDT") -> dict[str, float]:
        """All Hyperliquid perps use 1-hour funding."""
        try:
            data = _post({"type": "metaAndAssetCtxs"})
        except Exception:
            return {}
        if not isinstance(data, list) or len(data) < 2:
            return {}
        universe = data[0].get("universe", [])
        return {
            _coin_to_symbol(str(e.get("name", ""))): 1.0
            for e in universe
            if e.get("name")
        }
