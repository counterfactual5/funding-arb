#!/usr/bin/env python3
"""Hermetic unit tests for funding-rate arbitrage (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from accounting.futures.delta_neutral_portfolio import (
    apply_borrow_fees,
    apply_funding_fees,
    apply_simulated_futures_trades,
    calculate_futures_nav,
    check_liquidations,
    default_futures_state,
    liquidation_price,
    margin_health,
    normalize_executed_for_ledger,
)
from execution.run_cash_and_carry import apply_live_safety
from strategies.futures.cash_and_carry import decide_cash_and_carry
from strategies.futures.cross_asset_arbitrage import decide_cross_asset_arbitrage


def approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def _base_cfg(**over):
    cc = {
        "maxConcurrentPairs": 1,
        "tradeUsdPerSlot": 1000.0,
        "entryFundingRatePct": 0.05,
        "exitFundingRatePct": 0.01,
        "reverseEntryFundingRatePct": -0.05,
        "reverseExitFundingRatePct": -0.01,
        "minReverseSpreadPct": 0.02,
        "minNetEdgePct": 0.02,
        "preemptionFrictionBufferPct": 1e9,
    }
    cc.update(over)
    return {"cash": "USDT", "crossAssetArbitrage": cc}


def test_apply_live_safety_disables_reverse():
    cfg = {"dry_run": False, "crossAssetArbitrage": {"reverseEntryFundingRatePct": -0.05}}
    out = apply_live_safety(cfg)
    assert out["crossAssetArbitrage"]["reverseEntryFundingRatePct"] == -999.0
    cfg2 = {"dry_run": False, "enableReverseArbitrage": True, "crossAssetArbitrage": {"reverseEntryFundingRatePct": -0.05}}
    assert apply_live_safety(cfg2)["crossAssetArbitrage"]["reverseEntryFundingRatePct"] == -0.05


def test_funding_direction():
    prices = {"BTC": 100.0}
    fs = default_futures_state()
    fs["positions"]["BTC"] = {"amount": 1.0, "entry_price": 100.0, "side": "short"}
    h, fs2 = apply_funding_fees({"USDT": 0.0}, fs, prices, {"BTC": 0.01})
    assert approx(h["USDT"], 0.01)
    assert approx(fs2["cumulative_funding_paid"], -0.01)


def test_hold_when_funding_missing():
    h = {"USDT": 5000.0, "BTC": 0.01}
    fs = default_futures_state()
    fs["positions"]["BTC"] = {"amount": 0.01, "entry_price": 100.0, "side": "short"}
    trades, meta = decide_cross_asset_arbitrage(
        h, fs, {"BTC": 100.0}, {"BTC": {"price": 100.0}}, _base_cfg(), {}, {"BTC": 0.0}
    )
    assert trades == []
    assert meta.get("held_without_funding") == ["BTC"]


def test_close_perp_only_when_spot_missing():
    h = {"USDT": 5000.0, "BTC": 0.0}
    fs = default_futures_state()
    fs["positions"]["BTC"] = {"amount": 0.01, "entry_price": 100.0, "side": "short"}
    trades, _ = decide_cross_asset_arbitrage(
        h, fs, {"BTC": 100.0}, {"BTC": {"price": 100.0}}, _base_cfg(), {"BTC": 0.0}, {"BTC": 0.0}
    )
    assert [t["type"] for t in trades] == ["close_short"]


def test_cash_and_carry_delegates():
    cfg = {
        "cash": "USDT",
        "assets": ["BTC"],
        "cashAndCarry": {"tradeUsd": 1000.0, "entryFundingRatePct": 0.05, "minNetEdgePct": 0.02},
    }
    trades, meta = decide_cash_and_carry(
        {"USDT": 10000.0, "BTC": 0.0},
        default_futures_state(),
        {"BTC": 100.0},
        {"BTC": {"price": 100.0}},
        cfg,
        {"BTC": 0.07},
        {"BTC": 0.0},
    )
    assert {t["type"] for t in trades} == {"buy", "open_short"}
    assert meta["strategy"] == "cash_and_carry"


def test_normalized_trades_are_booked():
    """回归：归一化丢 status/amount_usdt 会让账本静默跳过所有成交。"""
    prices = {"BTC": 50000.0}
    executed = [
        {"symbol": "BTC", "type": "open_short", "status": "filled",
         "exec_qty": 0.01, "exec_price": 50000.0},
        {"symbol": "BTC", "type": "buy", "status": "simulated",
         "amount_base": 0.01, "price": 50000.0},
        {"symbol": "BTC", "type": "open_short", "status": "failed",
         "amount_base": 0.5, "price": 50000.0},
    ]
    ux = normalize_executed_for_ledger(executed, prices)
    assert len(ux) == 2  # failed 被剔除
    assert all(t["status"] in ("simulated", "filled") for t in ux)
    assert all(approx(t["amount_usdt"], 500.0) for t in ux)

    h, fs = apply_simulated_futures_trades(
        {"USDT": 10000.0}, default_futures_state(), ux, prices, "USDT",
        spot_fee_rate=0.001, perp_fee_rate=0.0005,
    )
    pos = fs["positions"]["BTC"]
    assert approx(pos["amount"], 0.01)
    assert pos["side"] == "short"
    assert approx(pos["entry_price"], 50000.0)
    assert approx(h["BTC"], 0.01)
    # cash = 10000 - 500(现货买) - 0.5(现货费) - 0.25(永续费)
    assert approx(h["USDT"], 10000.0 - 500.0 - 0.5 - 0.25)


def test_weighted_entry_price_on_addon():
    prices = {"ETH": 2000.0}
    fs = default_futures_state()
    ux1 = normalize_executed_for_ledger(
        [{"symbol": "ETH", "type": "open_short", "status": "filled",
          "exec_qty": 1.0, "exec_price": 2000.0}], prices)
    h, fs = apply_simulated_futures_trades({"USDT": 10000.0}, fs, ux1, prices, "USDT")
    prices2 = {"ETH": 1000.0}
    ux2 = normalize_executed_for_ledger(
        [{"symbol": "ETH", "type": "open_short", "status": "filled",
          "exec_qty": 1.0, "exec_price": 1000.0}], prices2)
    h, fs = apply_simulated_futures_trades(h, fs, ux2, prices2, "USDT")
    assert approx(fs["positions"]["ETH"]["amount"], 2.0)
    assert approx(fs["positions"]["ETH"]["entry_price"], 1500.0)


if __name__ == "__main__":
    test_apply_live_safety_disables_reverse()
    test_funding_direction()
    test_hold_when_funding_missing()
    test_close_perp_only_when_spot_missing()
    test_cash_and_carry_delegates()
    test_normalized_trades_are_booked()
    test_weighted_entry_price_on_addon()
    print("ALL PASSED")
