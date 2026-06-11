#!/usr/bin/env python3
"""Perpetual futures order book depth query and pre-open liquidity check
(4-exchange public endpoints, no authentication required).

Small-cap coins have thin order books; taker slippage is the top hidden cost
in live trading. Before opening a position, check whether the cumulative
notional within the price deviation window on both legs is sufficient to
cover the order size. If insufficient, skip — a single slippage event can
eat up multiple settlement periods of spread profit.
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
    """OKX depth quantities are in contracts; multiply by ctVal to convert to base currency (1h cache)."""
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


def fetch_futures_depth(
    venue: str, base: str, quote: str = "USDT", limit: int = 50
) -> Book:
    """Fetch perpetual order book for a venue (bids/asks as (price, base_qty), sorted by priority)."""
    v = venue.lower()
    sym = f"{base.upper()}{quote.upper()}"
    if v == "binance":
        d = http_get_json(
            f"https://fapi.binance.com/fapi/v1/depth?symbol={sym}&limit={limit}",
            timeout=15,
        )
        return {
            "bids": _parse_levels(d.get("bids")),
            "asks": _parse_levels(d.get("asks")),
        }
    if v == "bybit":
        d = http_get_json(
            f"https://api.bybit.com/v5/market/orderbook"
            f"?category=linear&symbol={sym}&limit={min(limit, 200)}",
            timeout=15,
        )
        result = d.get("result", {}) if isinstance(d, dict) else {}
        return {
            "bids": _parse_levels(result.get("b")),
            "asks": _parse_levels(result.get("a")),
        }
    if v == "bitget":
        d = http_get_json(
            f"https://api.bitget.com/api/v2/mix/market/merge-depth"
            f"?productType=USDT-FUTURES&symbol={sym}&limit={min(limit, 50)}",
            timeout=15,
        )
        data = d.get("data", {}) if isinstance(d, dict) else {}
        return {
            "bids": _parse_levels(data.get("bids")),
            "asks": _parse_levels(data.get("asks")),
        }
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
    if v == "aster":
        # Aster fapi is Binance-compatible
        d = http_get_json(
            f"https://fapi.asterdex.com/fapi/v1/depth?symbol={sym}&limit={limit}",
            timeout=15,
        )
        return {
            "bids": _parse_levels(d.get("bids")),
            "asks": _parse_levels(d.get("asks")),
        }
    if v == "hyperliquid":
        return _fetch_hyperliquid_depth(base)
    if v == "lighter":
        return _fetch_lighter_depth(base, limit)
    if v == "edgex":
        return _fetch_edgex_depth(base, limit)
    raise ValueError(f"Unsupported venue: {venue!r}")


def _fetch_hyperliquid_depth(base: str) -> Book:
    """Hyperliquid L2 order book via the public Info endpoint (POST)."""
    import json as _json
    import urllib.request

    body = _json.dumps({"type": "l2Book", "coin": base.upper()}).encode()
    req = urllib.request.Request(
        "https://api.hyperliquid.xyz/info",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        d = _json.loads(resp.read().decode())
    levels = d.get("levels") or [[], []]
    # levels[0] = bids (descending), levels[1] = asks (ascending); entries {px, sz, n}
    def _conv(rows: Any) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for r in rows or []:
            try:
                px, qty = float(r["px"]), float(r["sz"])
            except (TypeError, KeyError, ValueError):
                continue
            if px > 0 and qty > 0:
                out.append((px, qty))
        return out

    return {"bids": _conv(levels[0]), "asks": _conv(levels[1])}


def _fetch_lighter_depth(base: str, limit: int = 50) -> Book:
    """Lighter order book via /api/v1/orderBookOrders (needs symbol → market_id map)."""
    from venues.lighter_funding import LighterFundingProvider

    market_id = LighterFundingProvider().market_id_for_base(base)
    if market_id is None:
        raise RuntimeError(f"lighter market_id unavailable for {base}")
    d = http_get_json(
        f"https://mainnet.zklighter.elliot.ai/api/v1/orderBookOrders"
        f"?market_id={market_id}&limit={min(limit, 100)}",
        timeout=15,
    )

    def _conv(rows: Any) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for r in rows or []:
            try:
                px = float(r["price"])
                qty = float(r["remaining_base_amount"])
            except (TypeError, KeyError, ValueError):
                continue
            if px > 0 and qty > 0:
                out.append((px, qty))
        return out

    return {"bids": _conv(d.get("bids")), "asks": _conv(d.get("asks"))}


def _fetch_edgex_depth(base: str, limit: int = 50) -> Book:
    """EdgeX order book via /quote/getDepth (needs base → contractId map).

    `level` only supports 15 or 200; levels are {"price","size"} objects.
    """
    from venues.edgex_funding import EdgexFundingProvider

    contract_id = EdgexFundingProvider().contract_id_for_base(base)
    if contract_id is None:
        raise RuntimeError(f"edgex contractId unavailable for {base}")
    level = 200 if limit > 15 else 15
    d = http_get_json(
        f"https://pro.edgex.exchange/api/v1/public/quote/getDepth"
        f"?contractId={contract_id}&level={level}",
        timeout=15,
    )
    data = d.get("data", {}) if isinstance(d, dict) else {}
    if isinstance(data, list):
        data = data[0] if data else {}

    def _conv(rows: Any) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for r in rows or []:
            try:
                px = float(r["price"])
                qty = float(r["size"])
            except (TypeError, KeyError, ValueError):
                continue
            if px > 0 and qty > 0:
                out.append((px, qty))
        return out

    return {"bids": _conv(data.get("bids")), "asks": _conv(data.get("asks"))}


def depth_usd_within(book: Book, side: str, max_dev_pct: float) -> float:
    """Calculate cumulative notional (USD) on one side within the mid-price deviation window.

    side="asks": buy taker consumes asks, window [mid, mid×(1+dev)];
    side="bids": sell taker consumes bids, window [mid×(1−dev), mid].
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
    fail_open: bool = True,
) -> tuple[bool, str]:
    """Two-leg liquidity pre-check: pass only if depth within deviation window ≥ min_multiple × trade_usd.

    Returns (ok, detail). Long leg consumes asks, short leg consumes bids.
    fail_open=True passes on fetch failure (don't block on transient API errors);
    fail_open=False blocks the open instead (recommended for thin DEX books).
    """
    fetcher = depth_fetcher or (lambda v, b, q: fetch_futures_depth(v, b, q))
    required = trade_usd * min_multiple
    details: list[str] = []
    for venue, side, label in (
        (long_venue, "asks", "long"),
        (short_venue, "bids", "short"),
    ):
        try:
            book = fetcher(venue, base, quote)
        except Exception as e:
            if not fail_open:
                return False, f"{label}@{venue}: depth_fetch_failed({e}), blocking open"
            details.append(f"{label}@{venue}: depth_fetch_failed({e}), skipped")
            continue
        avail = depth_usd_within(book, side, max_dev_pct)
        if avail < required:
            return False, (
                f"{label}@{venue}: only ${avail:.0f} within ±{max_dev_pct}% window "
                f"(need ${required:.0f} = {min_multiple}×${trade_usd:.0f})"
            )
        details.append(f"{label}@{venue}: ${avail:.0f} ok")
    return True, "; ".join(details)
