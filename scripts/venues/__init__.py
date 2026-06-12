#!/usr/bin/env python3
"""CEX venue factory."""

from __future__ import annotations

from typing import Any

from venues.base import CexVenue, resolve_venue_config
from venues.binance import BinanceSpotVenue
from venues.bitget import BitgetSpotVenue
from venues.bybit import BybitSpotVenue
from venues.okx import OkxSpotVenue

_REGISTRY: dict[str, type] = {
    "bitget": BitgetSpotVenue,
    "binance": BinanceSpotVenue,
    "bybit": BybitSpotVenue,
    "okx": OkxSpotVenue,
}


def _lazy_load(vtype: str) -> None:
    """Import perp-DEX adapters on first use (they may need optional deps)."""
    if vtype in _REGISTRY:
        return
    if vtype == "hyperliquid":
        from venues.hyperliquid import HyperliquidVenue

        _REGISTRY["hyperliquid"] = HyperliquidVenue
    elif vtype == "aster":
        from venues.aster import AsterVenue

        _REGISTRY["aster"] = AsterVenue
    elif vtype == "lighter":
        from venues.lighter import LighterVenue

        _REGISTRY["lighter"] = LighterVenue
    elif vtype == "edgex":
        from venues.edgex import EdgexVenue

        _REGISTRY["edgex"] = EdgexVenue
    elif vtype == "dydx":
        from venues.dydx import DydxVenue

        _REGISTRY["dydx"] = DydxVenue


_LAZY_VENUES = ("hyperliquid", "aster", "lighter", "edgex", "dydx")


def supported_venues() -> list[str]:
    return sorted(set(_REGISTRY) | set(_LAZY_VENUES))


def get_venue(cfg: dict[str, Any]) -> CexVenue:
    venue_cfg = resolve_venue_config(cfg)
    vtype = str(venue_cfg.get("type", "bitget")).strip().lower()
    if vtype in _LAZY_VENUES:
        _lazy_load(vtype)
    cls = _REGISTRY.get(vtype)
    if cls is None:
        raise ValueError(
            f"Unsupported exchange venue.type={vtype!r}, available: {', '.join(supported_venues())}"
        )
    return cls()


def venue_quote(cfg: dict[str, Any]) -> str:
    return str(resolve_venue_config(cfg).get("quote", "USDT"))
