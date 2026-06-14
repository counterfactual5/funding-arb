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
    # Hyperliquid: fee = volume_tier base rate × (1 − staking_discount)
    # Below are the 7 volume tiers WITHOUT staking discount (staking=0%).
    # Users who stake HYPE get 5%–40% discount on top.
    # To model staked accounts, use the hl_stake_* tiers which apply the discount.
    # Volume tiers: t0=$0, t1=$5M, t2=$25M, t3=$100M, t4=$500M, t5=$2B, t6=$7B
    "hyperliquid": {
        # No staking (base rates)
        "t0": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.045},
        "t1": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.040},
        "t2": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.035},
        "t3": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.030},
        "t4": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.028},
        "t5": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.026},
        "t6": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.024},
        # Wood staking (>10 HYPE, 5% discount)
        "t0_wood": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0428},
        "t1_wood": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0380},
        "t2_wood": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0333},
        # Bronze staking (>100 HYPE, 10% discount)
        "t0_bronze": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0405},
        "t1_bronze": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0360},
        "t2_bronze": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0315},
        # Silver staking (>1K HYPE, 15% discount)
        "t0_silver": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0383},
        "t1_silver": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0340},
        "t2_silver": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0298},
        # Gold staking (>10K HYPE, 20% discount)
        "t0_gold": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0360},
        "t1_gold": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0320},
        "t2_gold": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0280},
        # Platinum staking (>100K HYPE, 30% discount)
        "t0_plat": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0315},
        "t1_plat": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0280},
        "t2_plat": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0245},
        # Diamond staking (>500K HYPE, 40% discount)
        "t0_diamond": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0270},
        "t1_diamond": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0240},
        "t2_diamond": {"spot_taker_pct": 0.0, "futures_taker_pct": 0.0210},
        # Alias for backward compat
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


# Human-readable labels for Hyperliquid fee tiers (volume × staking)
_HL_TIER_LABELS: dict[str, str] = {
    "default": "Default (0.045%)",
    "t0": "Tier 0 — No stake (0.045%)",
    "t1": "Tier 1 >$5M — No stake (0.040%)",
    "t2": "Tier 2 >$25M — No stake (0.035%)",
    "t3": "Tier 3 >$100M — No stake (0.030%)",
    "t4": "Tier 4 >$500M — No stake (0.028%)",
    "t5": "Tier 5 >$2B — No stake (0.026%)",
    "t6": "Tier 6 >$7B — No stake (0.024%)",
    "t0_wood": "T0 + Wood >10 HYPE (0.0428%)",
    "t1_wood": "T1 + Wood >10 HYPE (0.0380%)",
    "t2_wood": "T2 + Wood >10 HYPE (0.0333%)",
    "t0_bronze": "T0 + Bronze >100 HYPE (0.0405%)",
    "t1_bronze": "T1 + Bronze >100 HYPE (0.0360%)",
    "t2_bronze": "T2 + Bronze >100 HYPE (0.0315%)",
    "t0_silver": "T0 + Silver >1K HYPE (0.0383%)",
    "t1_silver": "T1 + Silver >1K HYPE (0.0340%)",
    "t2_silver": "T2 + Silver >1K HYPE (0.0298%)",
    "t0_gold": "T0 + Gold >10K HYPE (0.0360%)",
    "t1_gold": "T1 + Gold >10K HYPE (0.0320%)",
    "t2_gold": "T2 + Gold >10K HYPE (0.0280%)",
    "t0_plat": "T0 + Platinum >100K HYPE (0.0315%)",
    "t1_plat": "T1 + Platinum >100K HYPE (0.0280%)",
    "t2_plat": "T2 + Platinum >100K HYPE (0.0245%)",
    "t0_diamond": "T0 + Diamond >500K HYPE (0.0270%)",
    "t1_diamond": "T1 + Diamond >500K HYPE (0.0240%)",
    "t2_diamond": "T2 + Diamond >500K HYPE (0.0210%)",
}


def list_venue_tiers(venue: str) -> list[dict[str, Any]]:
    """Return tier options for UI: [{id, label, spot_taker_pct, futures_taker_pct}, ...]."""
    v = venue.lower()
    tiers = CEX_TIERS.get(v) or PERP_TIERS.get(v) or {}
    out: list[dict[str, Any]] = []
    for tier_id, rates in tiers.items():
        if v == "hyperliquid":
            label = _HL_TIER_LABELS.get(tier_id, tier_id)
        elif tier_id.startswith("vip"):
            label = tier_id.upper()
        else:
            label = tier_id.title()
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
