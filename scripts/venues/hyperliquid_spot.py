#!/usr/bin/env python3
"""Hyperliquid spot market data — read-only price reference.

HL spot pairs are USDC-quoted (PURR/USDC, HYPE/USDC, ...). Data comes from the
same /info endpoint as perp funding (`spotMetaAndAssetCtxs`).

Usage notes (see PLAN.md §0.1):
  - Spot here is only a candidate for the FORWARD spot leg (few listed coins).
  - HL borrow is Portfolio Margin collateral lending (non-withdrawable, gated)
    → NOT usable for reverse borrow routes. Do not feed this into C&C borrow
    routing; it is a display / spread-reference source only.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

_MAINNET_URL = "https://api.hyperliquid.xyz"
_TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
_CACHE_TTL_SEC = 60.0

_cache: tuple[float, list[dict[str, Any]]] | None = None


def _base_url() -> str:
    """Resolve the Info host from env (HYPERLIQUID_BASE_URL override, else
    HYPERLIQUID_NETWORK=testnet selects the testnet host)."""
    override = os.environ.get("HYPERLIQUID_BASE_URL", "").strip()
    if override:
        return override
    net = os.environ.get("HYPERLIQUID_NETWORK", "mainnet").strip().lower()
    return _TESTNET_URL if net == "testnet" else _MAINNET_URL


def _post(body: dict[str, Any], base_url: str | None = None) -> Any:
    r = requests.post(f"{base_url or _base_url()}/info", json=body, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_spot_prices(*, refresh: bool = False) -> list[dict[str, Any]]:
    """Fetch all HL spot pairs with mid/mark prices.

    Returns [{base, pair, price, day_volume_usd}], USDC-quoted.
    Tokens are matched back to their canonical name (e.g. "UBTC" stays as-is;
    callers decide how to map wrapped assets).
    """
    global _cache
    now = time.time()
    if not refresh and _cache and now - _cache[0] < _CACHE_TTL_SEC:
        return _cache[1]

    data = _post({"type": "spotMetaAndAssetCtxs"})
    if not isinstance(data, list) or len(data) < 2:
        return []

    meta, ctxs = data[0], data[1]
    tokens = {t.get("index"): str(t.get("name", "")) for t in meta.get("tokens", [])}
    out: list[dict[str, Any]] = []
    for i, pair_meta in enumerate(meta.get("universe", [])):
        if i >= len(ctxs):
            break
        ctx = ctxs[i]
        token_indices = pair_meta.get("tokens", [])
        base = tokens.get(token_indices[0], "") if token_indices else ""
        if not base:
            continue
        try:
            mid = float(ctx.get("midPx", 0) or 0)
        except (ValueError, TypeError):
            mid = 0.0
        if mid <= 0:
            try:
                mid = float(ctx.get("markPx", 0) or 0)
            except (ValueError, TypeError):
                mid = 0.0
        try:
            day_vlm = float(ctx.get("dayNtlVlm", 0) or 0)
        except (ValueError, TypeError):
            day_vlm = 0.0
        out.append(
            {
                "base": base.upper(),
                "pair": str(pair_meta.get("name", f"{base}/USDC")),
                "price": mid,
                "day_volume_usd": day_vlm,
            }
        )

    _cache = (now, out)
    return out


def spot_price_map(*, refresh: bool = False) -> dict[str, float]:
    """{BASE: price} convenience map for spread reference."""
    return {
        row["base"]: row["price"]
        for row in fetch_spot_prices(refresh=refresh)
        if row["price"] > 0
    }
