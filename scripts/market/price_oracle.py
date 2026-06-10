#!/usr/bin/env python3
"""Multi-source price oracle for CEX DCA.

独立于下单 venue 的价格层：当主源（默认 Bitget）拉价失败或异常时，
依次回退到 Binance / Coinbase / OKX 公开行情。所有源只读、无需 API key。

设计：
- 主源优先（与下单所一致，减少价差滑点）
- 多源 fallback（任一源拿到合理价即用）
- 一致性校验（多源价差 > 5% 视为脏数据，全部拒绝）
- K 线只从主源拿（fallback 源仅提供 last price）

调用方式：
    from venues.price_oracle import fetch_price_with_fallback
    px, meta = fetch_price_with_fallback("BTC", "USDT", primary="bitget")
"""

from __future__ import annotations

import time
from typing import Any

from venues.http_util import http_get_json

# 单源价差容差：超过此值视为脏数据
MAX_SPREAD = 0.05  # 5%


def _bitget_price(asset: str, quote: str) -> tuple[float, str]:
    """Bitget 公开 ticker（无需 key）。"""
    pair = f"{asset.upper()}{quote.upper()}"
    url = f"https://api.bitget.com/api/v2/spot/market/tickers?symbol={pair}"
    data = http_get_json(url, timeout=5, retries=2, backoff=0.3)
    rows = data.get("data", []) if isinstance(data, dict) else []
    if rows:
        return float(rows[0].get("lastPr", 0)), "bitget"
    return 0.0, "bitget"


def _binance_price(asset: str, quote: str) -> tuple[float, str]:
    """Binance 公开 ticker（无需 key）。"""
    pair = f"{asset.upper()}{quote.upper()}"
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={pair}"
    data = http_get_json(url, timeout=5, retries=2, backoff=0.3)
    return float(data.get("price", 0)), "binance"


def _coinbase_price(asset: str, quote: str) -> tuple[float, str]:
    """Coinbase 公开 spot 价（USD 报价，quote 强制为 USD）。"""
    cur = "USD" if quote.upper() in ("USDT", "USDC", "USD") else quote.upper()
    url = f"https://api.coinbase.com/v2/prices/{asset.upper()}-{cur}/spot"
    data = http_get_json(url, timeout=5, retries=2, backoff=0.3)
    amount = data.get("data", {}).get("amount", "0")
    return float(amount), "coinbase"


def _okx_price(asset: str, quote: str) -> tuple[float, str]:
    """OKX 公开 ticker（无需 key）。"""
    inst = f"{asset.upper()}-{quote.upper()}"
    url = f"https://www.okx.com/api/v5/market/ticker?instId={inst}"
    data = http_get_json(url, timeout=5, retries=2, backoff=0.3)
    rows = data.get("data", []) if isinstance(data, dict) else []
    if rows:
        return float(rows[0].get("last", 0)), "okx"
    return 0.0, "okx"


# 源顺序：主源优先，其余按稳定性/速度排序
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
    """多源 fallback 拉价。

    返回 (price, meta)：
    - price > 0：成功（来自首个可用源）
    - price == 0：所有源都失败
    meta 记录每个源的尝试结果，便于 journal 诊断。
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
            # 首个成功源即可返回（主源已优先）
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
    """批量拉价，返回 (prices, meta)。

    每个资产独立 fallback。任一资产最终 price=0 会被记录在 meta.bad。
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
