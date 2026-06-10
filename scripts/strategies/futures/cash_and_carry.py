#!/usr/bin/env python3
"""Cash and Carry Arbitrage（单资产）—— cross_asset_arbitrage 的 maxConcurrentPairs=1 特例。

历史上这里有一份和 cross_asset_arbitrage 几乎逐行重复的实现，导致改一个忘一个
（周期错配 bug 就在两边各踩一次）。现已合并：本文件只做配置映射并委托给规范引擎，
保留 ``decide_cash_and_carry`` 接口以兼容既有 runner 调用。

费率单位约定与 cross_asset_arbitrage 一致：funding/borrow 均为「百分比 / 每个资金费周期」，
borrow 需由调用方（runner）预先归一到资金费周期。
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
    """单资产 Forward/Reverse C&C：把 ``cashAndCarry`` 配置映射成单槽 cross-asset 后委托执行。"""
    assets = cfg.get("assets", ["BTC"])
    sym = assets[0]
    cc = cfg.get("cashAndCarry") or {}

    derived = dict(cfg)
    derived["crossAssetArbitrage"] = {
        "maxConcurrentPairs": 1,
        "tradeUsdPerSlot": float(cc.get("tradeUsd", 1000.0)),
        "entryFundingRatePct": float(cc.get("entryFundingRatePct", 0.05)),
        "exitFundingRatePct": float(cc.get("exitFundingRatePct", 0.01)),
        "reverseEntryFundingRatePct": float(cc.get("reverseEntryFundingRatePct", -0.05)),
        "reverseExitFundingRatePct": float(cc.get("reverseExitFundingRatePct", -0.01)),
        "minReverseSpreadPct": float(cc.get("minReverseSpreadPct", 0.02)),
        "minNetEdgePct": float(cc.get("minNetEdgePct", 0.02)),
        # 单资产模式没有抢占语义，关掉换仓摩擦判断（设极大 buffer）。
        "preemptionFrictionBufferPct": 1e9,
        "maxMinutesToSettlement": float(cc.get("maxMinutesToSettlement", 0.0)),
        "forwardRequiredCashMult": float(cc.get("forwardRequiredCashMult", 2.1)),
        "reverseRequiredCashMult": float(cc.get("reverseRequiredCashMult", 1.5)),
    }

    # 限定只看主资产，避免引擎扫描到其它 symbol。
    fr = {sym: float(funding_rates.get(sym, 0.0))}
    br = {sym: float((borrow_rates or {}).get(sym, 0.0))}

    trades, meta = decide_cross_asset_arbitrage(
        holdings, futures_state, {sym: prices.get(sym, 0.0)}, market, derived, fr, br,
        next_funding_times=next_funding_times, current_time_ms=current_time_ms
    )
    meta["strategy"] = "cash_and_carry"
    return trades, meta
