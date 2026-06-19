#!/usr/bin/env python3
"""Shared HTTP helpers for CEX venue adapters."""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import requests

_DEFAULT_UA = "Mozilla/5.0 (compatible; funding-arb/1.0)"


def credentials_file() -> str:
    """Plaintext credentials JSON path used by venue adapters as an env fallback."""
    return os.path.expanduser("~/.funding-arb/credentials.json")

# Module-level session with connection pooling and keep-alive.
# Thread-safe: requests.Session is safe for concurrent use across threads.
_session = requests.Session()
_session.headers.update({"User-Agent": _DEFAULT_UA})
# Connection pool settings: reuse up to 20 connections per host, 100 total
_adapter = requests.adapters.HTTPAdapter(
    pool_connections=64, pool_maxsize=64, max_retries=0
)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


def http_get_json(
    url: str, timeout: int = 8, retries: int = 3, backoff: float = 0.5
) -> Any:
    """Fetch JSON from URL with retries and connection pooling."""
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            resp = _session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            last_err = e
            status = e.response.status_code if e.response is not None else 0
            # Client errors (bad symbol, auth, 404...) won't change on retry —
            # fail fast. 429 (rate limit) is transient, so keep retrying it.
            if 400 <= status < 500 and status != 429:
                break
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise last_err if last_err else RuntimeError("http_get_json failed")


def parse_kline_ohlcv(k: list) -> dict[str, Any]:
    """Normalize [ts, o, h, l, c, vol, ...] candle rows."""
    return {
        "ts": int(k[0]),
        "open": float(k[1]),
        "high": float(k[2]),
        "low": float(k[3]),
        "close": float(k[4]),
        "vol": float(k[5]),
    }


def rules_for_price(rules: dict[str, Any], price: float) -> dict[str, Any]:
    min_usdt = float(rules.get("min_trade_usdt", 0))
    min_base = float(rules.get("min_trade_base", 0))
    min_from_base = min_base * price if price > 0 else 0.0
    min_buy_usdt = max(min_usdt, min_from_base)
    min_buy_usdt = round(min_buy_usdt * 1.02, 2)
    qp = int(rules.get("quote_precision", 2))
    qty_prec = int(rules.get("quantity_precision", 6))
    return {
        "min_buy_usdt": min_buy_usdt,
        "min_sell_usdt": min_buy_usdt,
        "min_base_amount": min_base,
        "min_base_usdt_equiv": min_from_base,
        "quote_precision": qp,
        "quantity_precision": qty_prec,
    }
