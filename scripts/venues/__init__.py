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


def supported_venues() -> list[str]:
    return sorted(_REGISTRY.keys())


def get_venue(cfg: dict[str, Any]) -> CexVenue:
    venue_cfg = resolve_venue_config(cfg)
    vtype = str(venue_cfg.get("type", "bitget")).strip().lower()
    cls = _REGISTRY.get(vtype)
    if cls is None:
        raise ValueError(f"不支持的交易所 venue.type={vtype!r}，可选: {', '.join(supported_venues())}")
    return cls()


def venue_quote(cfg: dict[str, Any]) -> str:
    return str(resolve_venue_config(cfg).get("quote", "USDT"))
