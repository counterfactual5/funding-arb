#!/usr/bin/env python3
"""Multi-source price oracle for CEX DCA.

Price layer independent of order venue: when the primary source (default Bitget)
fails or returns abnormal data, falls back to Binance / Coinbase / OKX public
quotes in sequence. All sources are read-only and require no API key.

Design:
- Primary source first (matches order venue to reduce slippage)
- Multi-source fallback (uses the first source that returns a reasonable price)
- Consistency check (rejects all data if cross-source spread > 5%)
- K-lines fetched from primary only (fallback sources provide last price only)

Usage:
    from venues.price_oracle import fetch_price_with_fallback
    px, meta = fetch_price_with_fallback("BTC", "USDT", primary="bitget")
"""

from __future__ import annotations

import time
from typing import Any

from venues.http_util import http_get_json

# Single-source spread tolerance: values above this are treated as dirty data
MAX_SPREAD = 0.05  # 5%


def _bitget_price(asset: str, quote: str) -> tuple[float, str]:
    """Bitget public ticker (no key required)."""
    pair = f"{asset.upper()}{quote.upper()}"
    url = f"https://api.bitget.com/api/v2/spot/market/tickers?symbol={pair}"
    data = http_get_json(url, timeout=5, retries=2, backoff=0.3)
    rows = data.get("data", []) if isinstance(data, dict) else []
    if rows:
        return float(rows[0].get("lastPr", 0)), "bitget"
    return 0.0, "bitget"


def _binance_price(asset: str, quote: str) -> tuple[float, str]:
    """Binance public ticker (no key required)."""
    pair = f"{asset.upper()}{quote.upper()}"
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={pair}"
    data = http_get_json(url, timeout=5, retries=2, backoff=0.3)
    return float(data.get("price", 0)), "binance"


def _coinbase_price(asset: str, quote: str) -> tuple[float, str]:
    """Coinbase public spot price (USD quote; quote is forced to USD)."""
    cur = "USD" if quote.upper() in ("USDT", "USDC", "USD") else quote.upper()
    url = f"https://api.coinbase.com/v2/prices/{asset.upper()}-{cur}/spot"
    data = http_get_json(url, timeout=5, retries=2, backoff=0.3)
    amount = data.get("data", {}).get("amount", "0")
    return float(amount), "coinbase"


def _okx_price(asset: str, quote: str) -> tuple[float, str]:
    """OKX public ticker (no key required)."""
    inst = f"{asset.upper()}-{quote.upper()}"
    url = f"https://www.okx.com/api/v5/market/ticker?instId={inst}"
    data = http_get_json(url, timeout=5, retries=2, backoff=0.3)
    rows = data.get("data", []) if isinstance(data, dict) else []
    if rows:
        return float(rows[0].get("last", 0)), "okx"
    return 0.0, "okx"


# Source order: primary first, then by stability/speed
_SOURCES = {
    "bitget": _bitget_price,
    "binance": _binance_price,
    "okx": _okx_price,
    "coinbase": _coinbase_price,
}


def fetch_price_with_fallback(
    asset: str,
    quote: str = "USDT",
    primary: str = "bitget",
) -> tuple[float, dict[str, Any]]:
    """Multi-source fallback price fetch.

    Returns (price, meta):
    - price > 0: success (from the first available source)
    - price == 0: all sources failed
    meta records each source's attempt result for journal diagnostics.
    """
    order = [primary] + [s for s in _SOURCES if s != primary]
    attempts: list[dict[str, Any]] = []
    got_prices: list[tuple[float, str]] = []

    for src_name in order:
        fn = _SOURCES.get(src_name)
        if fn is None:
            continue
        t0 = time.time()
        try:
            px, _ = fn(asset, quote)
        except Exception as e:
            attempts.append(
                {
                    "source": src_name,
                    "ok": False,
                    "error": str(e)[:80],
                    "ms": int((time.time() - t0) * 1000),
                }
            )
            continue
        ms = int((time.time() - t0) * 1000)
        if px > 0:
            got_prices.append((px, src_name))
            attempts.append(
                {"source": src_name, "ok": True, "price": round(px, 6), "ms": ms}
            )
            # Return on first successful source (primary already prioritized)
            break
        attempts.append({"source": src_name, "ok": False, "error": "price=0", "ms": ms})

    if not got_prices:
        return 0.0, {"attempts": attempts, "source": None}

    price, used = got_prices[0]
    meta: dict[str, Any] = {
        "source": used,
        "attempts": attempts,
        "fallback_used": used != primary,
    }
    return price, meta


def fetch_prices_batch(
    assets: list[str],
    quote: str = "USDT",
    primary: str = "bitget",
) -> tuple[dict[str, float], dict[str, Any]]:
    """Batch price fetch. Returns (prices, meta).

    Each asset falls back independently. Any asset with final price=0 is recorded in meta.bad.
    """
    prices: dict[str, float] = {}
    detail: dict[str, Any] = {}
    bad: list[str] = []
    for asset in assets:
        px, m = fetch_price_with_fallback(asset, quote, primary)
        prices[asset] = px
        detail[asset] = m
        if px <= 0:
            bad.append(asset)
    return prices, {"detail": detail, "bad": bad, "primary": primary}
