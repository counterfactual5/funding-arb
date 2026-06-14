#!/usr/bin/env python3
"""Hermetic tests for funding_history_source (no network)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.backtest_pure_futures_spread import run_backtest
from backtest.funding_history_source import (
    build_snapshots,
    fetch_leg_history,
    infer_interval_h,
)


def test_no_public_history_venue_skipped():
    """EdgeX has no public funding history → fetch returns [] without network."""
    assert fetch_leg_history("edgex", "BTC", 30) == []


H = 3600 * 1000
T0 = int(datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)


def _hist(start_ms: int, interval_h: float, rates: list[float]) -> list[dict]:
    return [
        {"ts": start_ms + int(i * interval_h * H), "rate_pct": r}
        for i, r in enumerate(rates)
    ]


def test_infer_interval_h():
    assert infer_interval_h(_hist(T0, 8.0, [0.01] * 10)) == 8.0
    assert infer_interval_h(_hist(T0, 2.0, [0.01] * 10)) == 2.0
    assert infer_interval_h(_hist(T0, 4.0, [0.01] * 10)) == 4.0
    # Too few data points → default to 8h
    assert infer_interval_h(_hist(T0, 1.0, [0.01, 0.02])) == 8.0


def test_build_snapshots_structure_and_rates():
    histories = {
        ("binance", "BTC"): _hist(T0, 8.0, [0.01, 0.02, 0.03]),
        ("okx", "BTC"): _hist(T0, 8.0, [0.13, 0.14, 0.15]),
    }
    snaps = build_snapshots(histories)
    # Grid = 3 common settlement points
    assert len(snaps) == 3
    assert all("_ts" in s for s in snaps)
    # Visible rate at t=T0 = rate settling at T0
    row0 = snaps[0]["forward"][0]
    assert row0["long_venue"] == "binance"  # lower rate goes long
    assert row0["short_venue"] == "okx"
    assert abs(row0["long_rate_pct"] - 0.01) < 1e-9
    assert abs(row0["short_rate_pct"] - 0.13) < 1e-9
    assert abs(row0["spread_pct"] - 0.12) < 1e-9
    assert row0["settle_mismatch"] is False
    # Second settlement point carries second-period rates
    row1 = snaps[1]["forward"][0]
    assert abs(row1["spread_pct"] - 0.12) < 1e-9
    assert abs(row1["long_rate_pct"] - 0.02) < 1e-9


def test_build_snapshots_mismatch_flag():
    histories = {
        ("bitget", "ETH"): _hist(T0, 2.0, [-0.01] * 12),  # 2h leg
        ("binance", "ETH"): _hist(T0, 8.0, [0.10] * 3),  # 8h leg
    }
    snaps = build_snapshots(histories)
    # Grid = 2h leg 12 points ∪ 8h leg 3 points (overlapping) = 12
    assert len(snaps) == 12
    row = snaps[0]["forward"][0]
    assert row["settle_mismatch"] is True
    assert row["long_interval_h"] == 2.0
    assert row["short_interval_h"] == 8.0


def test_history_end_to_end_funding_matches_hand_calc():
    """10 periods of constant spread 0.12; verify backtest funding matches hand calculation."""
    n = 10
    histories = {
        ("binance", "BTC"): _hist(T0, 8.0, [0.01] * n),
        ("okx", "BTC"): _hist(T0, 8.0, [0.13] * n),
    }
    snaps = build_snapshots(histories)
    result = run_backtest(
        snaps,
        initial_capital=100000.0,
        trade_usd=5000.0,
        min_spread_pct=0.0,
        min_edge_pct=0.0,
        exit_edge_pct=-999.0,
        max_holding_hours=9999.0,
    )
    assert result.trade_count == 1
    trade = result.trades[0]
    # Open at T0, then cross 9 settlement boundaries
    assert trade["long_settlements"] == 9
    assert trade["short_settlements"] == 9
    # funding = 9 × (0.13 − 0.01) = 1.08
    assert abs(result.total_funding_collected_pct - 1.08) < 1e-9
    # fee = 2 × (binance 0.05 + okx 0.05) = 0.20 → pnl = 0.88% × 5000 = $44
    assert abs(result.total_return_pct - (1.08 - 0.20) * 5000 / 100000) < 1e-6


def test_mid_backtest_close_returns_margin():
    """Regression: mid-position close must release locked margin (previously only added pnl, causing phantom equity crash)."""
    # First 2 periods spread 0.12, then narrows to 0.01 → edge collapse triggers mid-position close
    histories = {
        ("binance", "BTC"): _hist(T0, 8.0, [0.01] * 4),
        ("okx", "BTC"): _hist(T0, 8.0, [0.13, 0.13, 0.02, 0.02]),
    }
    snaps = build_snapshots(histories)
    result = run_backtest(
        snaps,
        initial_capital=100000.0,
        trade_usd=5000.0,
        min_spread_pct=0.0,
        min_edge_pct=0.01,
        exit_edge_pct=0.01,
    )
    assert result.trade_count == 1
    assert "edge_collapse" in result.trades[0]["close_reason"]
    # Position crosses T0+8h (spread 0.12) and T0+16h (spread narrowed to 0.01) settlements,
    # triggers edge_collapse close at T0+16h snapshot → funding = 0.13, fee = 0.20
    expected_return = (0.13 - 0.20) * 5000 / 100000
    assert abs(result.total_return_pct - expected_return) < 1e-6


def test_interval_switch_uses_local_gap():
    """Regression: when an asset switches settlement interval mid-stream (e.g. 8h→1h), accrue by local gap.

    A global median inference would over-count funding by 8× in the 8h period using 1h boundaries.
    """
    h8 = [T0 + i * 8 * H for i in range(4)]  # 0,8,16,24h
    h1 = [T0 + 24 * H + (i + 1) * H for i in range(8)]  # 25..32h
    ts_all = h8 + h1
    histories = {
        ("binance", "BTC"): [{"ts": t, "rate_pct": 0.01} for t in ts_all],
        ("okx", "BTC"): [{"ts": t, "rate_pct": 0.13} for t in ts_all],
    }
    snaps = build_snapshots(histories)
    # Rows in the 8h period should report 8h interval, 1h period reports 1h
    assert snaps[1]["forward"][0]["long_interval_h"] == 8.0
    assert snaps[-1]["forward"][0]["long_interval_h"] == 1.0
    result = run_backtest(
        snaps,
        initial_capital=100000.0,
        trade_usd=5000.0,
        min_spread_pct=0.0,
        min_edge_pct=0.0,
        exit_edge_pct=-999.0,
        max_holding_hours=9999.0,
    )
    assert result.trade_count == 1
    trade = result.trades[0]
    # Actual settlements after opening at T0: 8h period 3 times + 1h period 8 times = 11
    assert trade["long_settlements"] == 11
    assert trade["short_settlements"] == 11
    assert abs(result.total_funding_collected_pct - 11 * 0.12) < 1e-9


def test_pre_listing_gap_no_phantom_accrual():
    """Regression: when one leg is listed later, the pair must not appear before listing
    (previously bridged the first rate backward, phantom-accruing weeks of funding).
    """
    n = 20
    late_offset = 10  # okx leg listed 10 periods later
    histories = {
        ("binance", "BTC"): _hist(T0, 8.0, [0.01] * n),
        ("okx", "BTC"): _hist(
            T0 + late_offset * 8 * H, 8.0, [0.13] * (n - late_offset)
        ),
    }
    snaps = build_snapshots(histories)
    # Snapshots before okx listing should not have BTC pair rows
    for s in snaps:
        ts_ms = s["_ts"].timestamp() * 1000
        rows = s["forward"] + s["reverse"]
        if ts_ms < T0 + (late_offset - 1) * 8 * H:
            assert rows == [], f"Pre-listing {s['timestamp']} should have no pairs"
    result = run_backtest(
        snaps,
        min_spread_pct=0.0,
        min_edge_pct=0.0,
        exit_edge_pct=-999.0,
        max_holding_hours=9999.0,
    )
    assert result.trade_count == 1
    # Can only accrue post-listing settlements (≤ n - late_offset periods)
    assert result.trades[0]["long_settlements"] <= n - late_offset


def test_data_hole_drops_leg():
    """Regression: mid-history gap (>8h×1.5) should drop that leg, forcing position close."""
    # binance leg has an 80h data hole between periods 5–14
    ts_with_hole = [T0 + i * 8 * H for i in range(5)] + [
        T0 + i * 8 * H for i in range(15, 20)
    ]
    histories = {
        ("binance", "BTC"): [{"ts": t, "rate_pct": 0.01} for t in ts_with_hole],
        ("okx", "BTC"): _hist(T0, 8.0, [0.13] * 20),
    }
    snaps = build_snapshots(histories)
    hole_start = T0 + 4 * 8 * H
    hole_end = T0 + 15 * 8 * H
    for s in snaps:
        ts_ms = s["_ts"].timestamp() * 1000
        if hole_start < ts_ms < hole_end:
            assert s["forward"] + s["reverse"] == [], (
                f"Inside data hole {s['timestamp']} should have no pairs"
            )
    result = run_backtest(
        snaps,
        min_spread_pct=0.0,
        min_edge_pct=0.0,
        exit_edge_pct=-999.0,
        max_holding_hours=9999.0,
    )
    # Pair opened before the hole should be closed at hole start; can reopen after hole
    assert result.trade_count == 2
    assert "spread_disappeared" in result.trades[0]["close_reason"]


def test_cc_forward_end_to_end():
    """cc_forward: single-venue positive rate, spot long + perp short; hand-verify funding and fees."""
    n = 10
    histories = {("binance", "BTC"): _hist(T0, 8.0, [0.30] * n)}
    snaps = build_snapshots(histories)
    assert all(
        len(s["forward"]) + len(s["reverse"]) == 0 for s in snaps
    )  # single leg has no pure pair
    row = snaps[0]["cc"][0]
    assert row["direction"] == "cc_forward"
    assert (
        abs(row["net_edge_pct"] - (0.30 - 0.15)) < 1e-9
    )  # fee = 0.10 spot + 0.05 binance perp
    result = run_backtest(
        snaps,
        initial_capital=100000.0,
        trade_usd=5000.0,
        min_spread_pct=0.0,
        min_edge_pct=0.0,
        exit_edge_pct=-999.0,
        max_holding_hours=9999.0,
        strategies={"cc"},
    )
    assert result.trade_count == 1
    # funding = 9 × 0.30 = 2.7, fee = 2 × 0.15 = 0.30
    assert abs(result.total_funding_collected_pct - 2.7) < 1e-9
    assert abs(result.total_return_pct - (2.7 - 0.30) * 5000 / 100000) < 1e-6


def test_cc_reverse_borrow_cost():
    """cc_reverse: negative rate + borrow cost accrued per settlement period."""
    n = 10
    borrow_apr = 17.52  # 17.52%/8760h × 8h = 0.016%/period, convenient for hand calc
    histories = {("binance", "BTC"): _hist(T0, 8.0, [-0.30] * n)}
    snaps = build_snapshots(histories, borrow_apr_pct=borrow_apr)
    row = snaps[0]["cc"][0]
    assert row["direction"] == "cc_reverse"
    assert abs(row["borrow_per_settle_pct"] - 0.016) < 1e-9
    result = run_backtest(
        snaps,
        initial_capital=100000.0,
        trade_usd=5000.0,
        min_spread_pct=0.0,
        min_edge_pct=0.0,
        exit_edge_pct=-999.0,
        max_holding_hours=9999.0,
        strategies={"cc"},
    )
    assert result.trade_count == 1
    trade = result.trades[0]
    # funding = 9 × 0.30, borrow interest = 9 × 0.016, fee = 0.30
    assert abs(result.total_funding_collected_pct - 2.7) < 1e-9
    assert abs(trade["borrow_paid_pct"] - 9 * 0.016) < 1e-9
    expected = (2.7 - 9 * 0.016 - 0.30) * 5000 / 100000
    assert abs(result.total_return_pct - expected) < 1e-6


def test_cc_capability_filter():
    """No spot → no cc_forward; not borrowable → no cc_reverse; borrowable uses real APR."""
    histories = {
        ("binance", "BTC"): _hist(T0, 8.0, [0.30] * 5),  # positive rate → cc_forward
        ("bybit", "ETH"): _hist(T0, 8.0, [-0.30] * 5),  # negative rate → cc_reverse
    }
    caps = {
        ("binance", "BTC"): {
            "has_spot": False,
            "borrowable": False,
            "borrow_apr_pct": 0.0,
        },
        ("bybit", "ETH"): {
            "has_spot": True,
            "borrowable": True,
            "borrow_apr_pct": 87.6,
        },
    }
    snaps = build_snapshots(histories, cc_capability=caps)
    cc = snaps[0]["cc"]
    # binance BTC has no spot → cc_forward filtered out; only bybit ETH cc_reverse remains
    assert len(cc) == 1
    row = cc[0]
    assert row["direction"] == "cc_reverse"
    # Real APR 87.6%/8760h × 8h = 0.08%/period
    assert abs(row["borrow_per_settle_pct"] - 0.08) < 1e-9
    # No capability info → no filtering (backward compatible)
    snaps_all = build_snapshots(histories)
    assert len(snaps_all[0]["cc"]) == 2


def test_strategy_filter_pure_only_ignores_cc():
    """Default strategies={'pure'} should not open cc positions."""
    histories = {("binance", "BTC"): _hist(T0, 8.0, [0.30] * 5)}
    snaps = build_snapshots(histories)
    result = run_backtest(
        snaps, min_spread_pct=0.0, min_edge_pct=0.0, exit_edge_pct=-999.0
    )
    assert result.trade_count == 0


def test_combined_picks_higher_edge():
    """pure and cc compete head-to-head: sorted by net_edge, higher-spread pure pair wins."""
    n = 6
    histories = {
        # binance 0.40 / okx 0.05 → pure spread 0.35 (edge 0.25);
        # cc_forward binance edge = 0.40 − 0.15 = 0.25 vs pure 0.25 tie,
        # adjust okx to -0.05 → pure spread 0.45 edge 0.35 wins
        ("binance", "BTC"): _hist(T0, 8.0, [0.40] * n),
        ("okx", "BTC"): _hist(T0, 8.0, [-0.05] * n),
    }
    snaps = build_snapshots(histories)
    result = run_backtest(
        snaps,
        min_spread_pct=0.0,
        min_edge_pct=0.0,
        exit_edge_pct=-999.0,
        max_holding_hours=9999.0,
        strategies={"pure", "cc"},
        max_concurrent_pairs=1,
    )
    assert result.trade_count == 1
    # Same base opens only one position; pure pair (edge 0.35) beats cc (0.25)
    assert result.trades[0]["direction"] == "forward"


def test_leg_history_exhaustion_closes_pair():
    """One leg's history ends early → pair row disappears; position should be closed, not left hanging."""
    histories = {
        ("binance", "BTC"): _hist(T0, 8.0, [0.01] * 10),
        ("okx", "BTC"): _hist(T0, 8.0, [0.13] * 5),  # ends 5 periods early
    }
    snaps = build_snapshots(histories)
    assert len(snaps) == 10
    result = run_backtest(
        snaps,
        min_spread_pct=0.0,
        min_edge_pct=0.0,
        exit_edge_pct=-999.0,
        max_holding_hours=9999.0,
    )
    assert result.trade_count == 1
    assert "spread_disappeared" in result.trades[0]["close_reason"]
