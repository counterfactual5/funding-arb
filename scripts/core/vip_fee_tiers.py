#!/usr/bin/env python3
"""Published VIP fee schedules (percentage points) for scanner fee estimation.

Used when exchange API credentials are not configured. Rates are approximate
public VIP tables — actual account rates may differ slightly.
"""

from __future__ import annotations

from typing import Any

# tier_id -> {spot_taker_pct, futures_taker_pct}
VenueTiers = dict[str, dict[str, float]]

CEX_TIERS: dict[str, VenueTiers] = {
    "binance": {
        "vip0": {"spot_taker_pct": 0.10, "futures_taker_pct": 0.050},
        "vip1": {"spot_taker_pct": 0.09, "futures_taker_pct": 0.040},
        "vip2": {"spot_taker_pct": 0.08, "futures_taker_pct": 0.035},
        "vip3": {"spot_taker_pct": 0.07, "futures_taker_pct": 0.032},
        "vip4": {"spot_taker_pct": 0.06, "futures_taker_pct": 0.030},
        "vip5": {"spot_taker_pct": 0.05, "futures_taker_pct": 0.027},
        "vip6": {"spot_taker_pct": 0.04, "futures_taker_pct": 0.025},
        "vip7": {"spot_taker_pct": 0.03, "futures_taker_pct": 0.022},
        "vip8": {"spot_taker_pct": 0.02, "futures_taker_pct": 0.020},
        "vip9": {"spot_taker_pct": 0.01, "futures_taker_pct": 0.017},
    },
    "bitget": {
        "vip0": {"spot_taker_pct": 0.10, "futures_taker_pct": 0.060},
        "vip1": {"spot_taker_pct": 0.08, "futures_taker_pct": 0.060},
        "vip2": {"spot_taker_pct": 0.07, "futures_taker_pct": 0.040},
        "vip3": {"spot_taker_pct": 0.06, "futures_taker_pct": 0.0375},
        "vip4": {"spot_taker_pct": 0.05, "futures_taker_pct": 0.035},
        "vip5": {"spot_taker_pct": 0.04, "futures_taker_pct": 0.032},
        "vip6": {"spot_taker_pct": 0.035, "futures_taker_pct": 0.030},
        "vip7": {"spot_taker_pct": 0.03, "futures_taker_pct": 0.020},
    },
    "bybit": {
        "vip0": {"spot_taker_pct": 0.10, "futures_taker_pct": 0.055},
        "vip1": {"spot_taker_pct": 0.080, "futures_taker_pct": 0.040},
        "vip2": {"spot_taker_pct": 0.0775, "futures_taker_pct": 0.0375},
        "vip3": {"spot_taker_pct": 0.075, "futures_taker_pct": 0.035},
        "vip4": {"spot_taker_pct": 0.060, "futures_taker_pct": 0.032},
        "vip5": {"spot_taker_pct": 0.050, "futures_taker_pct": 0.032},
        "supreme": {"spot_taker_pct": 0.045, "futures_taker_pct": 0.030},
    },
    "okx": {
        "vip0": {"spot_taker_pct": 0.10, "futures_taker_pct": 0.050},
        "vip1": {"spot_taker_pct": 0.09, "futures_taker_pct": 0.040},
        "vip2": {"spot_taker_pct": 0.08, "futures_taker_pct": 0.035},
        "vip3": {"spot_taker_pct": 0.07, "futures_taker_pct": 0.028},
        "vip4": {"spot_taker_pct": 0.065, "futures_taker_pct": 0.027},
        "vip5": {"spot_taker_pct": 0.06, "futures_taker_pct": 0.026},
        "vip6": {"spot_taker_pct": 0.055, "futures_taker_pct": 0.025},
        "vip7": {"spot_taker_pct": 0.050, "futures_taker_pct": 0.020},
        "vip8": {"spot_taker_pct": 0.045, "futures_taker_pct": 0.020},
        "vip9": {"spot_taker_pct": 0.040, "futures_taker_pct": 0.015},
    },
}

# Perp-only venues (no spot leg in cash-and-carry)
PERP_TIERS: dict[str, VenueTiers] = {
    "hyperliquid": {
        "default": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.045},
    },
    "aster": {
        "default": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.04},
    },
    "lighter": {
        "default": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0},
    },
    "edgex": {
        "default": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.038},
    },
    "dydx": {
        "default": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.05},
    },
}

ALL_VENUES = sorted(set(CEX_TIERS) | set(PERP_TIERS))


def list_venue_tiers(venue: str) -> list[dict[str, Any]]:
    """Return tier options for UI: [{id, label, spot_taker_pct, futures_taker_pct}, ...]."""
    v = venue.lower()
    tiers = CEX_TIERS.get(v) or PERP_TIERS.get(v) or {}
    out: list[dict[str, Any]] = []
    for tier_id, rates in tiers.items():
        label = tier_id.upper() if tier_id.startswith("vip") else tier_id.title()
        out.append(
            {
                "id": tier_id,
                "label": label,
                "spot_taker_pct": rates["spot_taker_pct"],
                "futures_taker_pct": rates["futures_taker_pct"],
            }
        )
    return out


def tier_rates(venue: str, tier_id: str | None = None) -> dict[str, float]:
    """Resolve spot/futures taker pct for a venue + tier."""
    v = venue.lower()
    tiers = CEX_TIERS.get(v) or PERP_TIERS.get(v)
    if not tiers:
        return {"spot_taker_pct": 0.1, "futures_taker_pct": 0.06}
    tid = (tier_id or "").lower()
    if tid not in tiers:
        tid = "vip0" if v in CEX_TIERS else "default"
    return dict(tiers[tid])
