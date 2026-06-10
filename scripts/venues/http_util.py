#!/usr/bin/env python3
"""Shared HTTP helpers for CEX venue adapters."""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Optional

_DEFAULT_UA = "Mozilla/5.0 (compatible; funding-arb-cex/1.0)"


def http_get_json(url: str, timeout: int = 8, retries: int = 3, backoff: float = 0.5) -> Any:
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _DEFAULT_UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
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
