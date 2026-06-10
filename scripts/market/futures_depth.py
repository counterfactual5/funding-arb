#!/usr/bin/env python3
"""永续合约盘口深度查询与开仓前流动性预检（4 所公开端点，无需鉴权）。

小币种盘口薄，taker 吃单滑点是实盘第一大隐性成本。开仓前检查
两腿盘口在价格偏离窗口内的累计名义额是否足够覆盖下单量，
不足则放弃，避免一笔滑点吃掉数个结算周期的费差收益。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Callable

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from venues.http_util import http_get_json  # noqa: E402

Book = dict[str, list[tuple[float, float]]]  # {"bids": [(px, qty)], "asks": [...]}

_OKX_CTVAL_CACHE: tuple[float, dict[str, float]] | None = None
_OKX_CTVAL_TTL_S = 3600.0


def _okx_ctval_map() -> dict[str, float]:
    """OKX 深度数量单位是「张」，需乘 ctVal 换算成币本位数量（1h 缓存）。"""
    global _OKX_CTVAL_CACHE
    now = time.time()
    if _OKX_CTVAL_CACHE and now - _OKX_CTVAL_CACHE[0] < _OKX_CTVAL_TTL_S:
        return _OKX_CTVAL_CACHE[1]
    payload = http_get_json(
        "https://www.okx.com/api/v5/public/instruments?instType=SWAP", timeout=20
    )
    out: dict[str, float] = {}
    for row in payload.get("data", []) if isinstance(payload, dict) else []:
        inst = str(row.get("instId", ""))
        ctval = float(row.get("ctVal", 0) or 0)
        if inst and ctval > 0:
            out[inst] = ctval
    _OKX_CTVAL_CACHE = (now, out)
    return out


def _parse_levels(raw: Any, qty_mult: float = 1.0) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for lvl in raw or []:
        try:
            px = float(lvl[0])
            qty = float(lvl[1]) * qty_mult
        except (TypeError, ValueError, IndexError):
            continue
        if px > 0 and qty > 0:
            out.append((px, qty))
    return out


def fetch_futures_depth(venue: str, base: str, quote: str = "USDT", limit: int = 50) -> Book:
    """拉取某所永续盘口（bids/asks 均为 (price, base_qty)，按优先级排序）。"""
    v = venue.lower()
    sym = f"{base.upper()}{quote.upper()}"
    if v == "binance":
        d = http_get_json(
            f"https://fapi.binance.com/fapi/v1/depth?symbol={sym}&limit={limit}",
            timeout=15,
        )
        return {"bids": _parse_levels(d.get("bids")), "asks": _parse_levels(d.get("asks"))}
    if v == "bybit":
        d = http_get_json(
            f"https://api.bybit.com/v5/market/orderbook"
            f"?category=linear&symbol={sym}&limit={min(limit, 200)}",
            timeout=15,
        )
        result = d.get("result", {}) if isinstance(d, dict) else {}
        return {"bids": _parse_levels(result.get("b")), "asks": _parse_levels(result.get("a"))}
    if v == "bitget":
        d = http_get_json(
            f"https://api.bitget.com/api/v2/mix/market/merge-depth"
            f"?productType=USDT-FUTURES&symbol={sym}&limit={min(limit, 50)}",
            timeout=15,
        )
        data = d.get("data", {}) if isinstance(d, dict) else {}
        return {"bids": _parse_levels(data.get("bids")), "asks": _parse_levels(data.get("asks"))}
    if v == "okx":
        inst = f"{base.upper()}-{quote.upper()}-SWAP"
        d = http_get_json(
            f"https://www.okx.com/api/v5/market/books?instId={inst}&sz={min(limit, 400)}",
            timeout=15,
        )
        rows = (d.get("data") or [{}])[0] if isinstance(d, dict) else {}
        ctval = _okx_ctval_map().get(inst, 0.0)
        if ctval <= 0:
            raise RuntimeError(f"okx ctVal unavailable for {inst}")
        return {
            "bids": _parse_levels(rows.get("bids"), ctval),
            "asks": _parse_levels(rows.get("asks"), ctval),
        }
    raise ValueError(f"不支持的 venue: {venue!r}")


def depth_usd_within(book: Book, side: str, max_dev_pct: float) -> float:
    """统计 mid 价偏离窗口内某一侧的累计名义额 (USD)。

    side="asks": 买入吃单消耗卖盘，窗口 [mid, mid×(1+dev)]；
    side="bids": 卖出吃单消耗买盘，窗口 [mid×(1−dev), mid]。
    """
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return 0.0
    mid = (bids[0][0] + asks[0][0]) / 2.0
    if mid <= 0:
        return 0.0
    total = 0.0
    if side == "asks":
        cap = mid * (1.0 + max_dev_pct / 100.0)
        for px, qty in asks:
            if px > cap:
                break
            total += px * qty
    else:
        floor_px = mid * (1.0 - max_dev_pct / 100.0)
        for px, qty in bids:
            if px < floor_px:
                break
            total += px * qty
    return total


def check_pair_depth(
    long_venue: str,
    short_venue: str,
    base: str,
    trade_usd: float,
    *,
    quote: str = "USDT",
    max_dev_pct: float = 0.3,
    min_multiple: float = 3.0,
    depth_fetcher: Callable[[str, str, str], Book] | None = None,
) -> tuple[bool, str]:
    """两腿流动性预检：偏离窗口内深度 ≥ min_multiple × trade_usd 才放行。

    返回 (ok, detail)。盘口拉取失败时放行（不让偶发接口故障阻塞交易），
    detail 中注明。多头腿吃 asks，空头腿吃 bids。
    """
    fetcher = depth_fetcher or (
        lambda v, b, q: fetch_futures_depth(v, b, q)
    )
    required = trade_usd * min_multiple
    details: list[str] = []
    for venue, side, label in (
        (long_venue, "asks", "long"),
        (short_venue, "bids", "short"),
    ):
        try:
            book = fetcher(venue, base, quote)
        except Exception as e:
            details.append(f"{label}@{venue}: depth_fetch_failed({e}), skipped")
            continue
        avail = depth_usd_within(book, side, max_dev_pct)
        if avail < required:
            return False, (
                f"{label}@{venue}: 仅 ${avail:.0f} 在 ±{max_dev_pct}% 窗口内 "
                f"(需 ${required:.0f} = {min_multiple}×${trade_usd:.0f})"
            )
        details.append(f"{label}@{venue}: ${avail:.0f} ok")
    return True, "; ".join(details)
