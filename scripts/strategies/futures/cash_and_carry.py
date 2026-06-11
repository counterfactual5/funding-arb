#!/usr/bin/env python3
"""Cash and Carry Arbitrage (single asset) — a maxConcurrentPairs=1 special case of cross_asset_arbitrage.

Historically this file had an almost line-by-line duplicate implementation of cross_asset_arbitrage,
causing fix-one-miss-the-other bugs (the period mismatch bug hit both copies independently).
Now merged: this file only does config mapping and delegates to the canonical engine,
preserving the ``decide_cash_and_carry`` interface for backward compatibility with existing runners.

Rate unit convention is the same as cross_asset_arbitrage: funding/borrow are both
"percentage per funding settlement period"; borrow must be pre-normalized to the funding
period by the caller (runner).
"""

from __future__ import annotations

from typing import Any

from strategies.futures.cross_asset_arbitrage import decide_cross_asset_arbitrage


def decide_cash_and_carry(
    holdings: dict[str, float],
    futures_state: dict[str, Any],
    prices: dict[str, float],
    market: dict[str, dict[str, Any]],
    cfg: dict[str, Any],
    funding_rates: dict[str, float],
    borrow_rates: dict[str, float] | None = None,
    next_funding_times: dict[str, int] | None = None,
    current_time_ms: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Single-asset Forward/Reverse C&C: maps ``cashAndCarry`` config to a single-slot cross-asset delegation."""
    assets = cfg.get("assets", ["BTC"])
    sym = assets[0]
    cc = cfg.get("cashAndCarry") or {}

    derived = dict(cfg)
    derived["crossAssetArbitrage"] = {
        "maxConcurrentPairs": 1,
        "tradeUsdPerSlot": float(cc.get("tradeUsd", 1000.0)),
        "entryFundingRatePct": float(cc.get("entryFundingRatePct", 0.05)),
        "exitFundingRatePct": float(cc.get("exitFundingRatePct", 0.01)),
        "reverseEntryFundingRatePct": float(
            cc.get("reverseEntryFundingRatePct", -0.05)
        ),
        "reverseExitFundingRatePct": float(cc.get("reverseExitFundingRatePct", -0.01)),
        "minReverseSpreadPct": float(cc.get("minReverseSpreadPct", 0.02)),
        "minNetEdgePct": float(cc.get("minNetEdgePct", 0.02)),
        # Single-asset mode has no preemption semantics; disable swap-friction check (set huge buffer).
        "preemptionFrictionBufferPct": 1e9,
        "maxMinutesToSettlement": float(cc.get("maxMinutesToSettlement", 0.0)),
        "forwardRequiredCashMult": float(cc.get("forwardRequiredCashMult", 2.1)),
        "reverseRequiredCashMult": float(cc.get("reverseRequiredCashMult", 1.5)),
    }

    # Restrict to the primary asset only, prevent the engine from scanning other symbols.
    fr = {sym: float(funding_rates.get(sym, 0.0))}
    br = {sym: float((borrow_rates or {}).get(sym, 0.0))}

    trades, meta = decide_cross_asset_arbitrage(
        holdings,
        futures_state,
        {sym: prices.get(sym, 0.0)},
        market,
        derived,
        fr,
        br,
        next_funding_times=next_funding_times,
        current_time_ms=current_time_ms,
    )
    meta["strategy"] = "cash_and_carry"
    return trades, meta
