#!/usr/bin/env python3
"""Hermetic tests for backtest_pure_futures_spread."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.backtest_pure_futures_spread import (
    _settlements_crossed,
    load_snapshots,
    run_backtest,
)


def _snap(ts: datetime, rows: list[dict]) -> dict:
    return {
        "timestamp": ts.isoformat(),
        "venues": ["binance", "okx"],
        "total_assets_scanned": 1,
        "forward": rows,
        "reverse": [],
    }


def _row(
    base="BTC",
    long_venue="binance",
    short_venue="okx",
    spread=0.12,
    edge=0.02,
    mismatch=False,
) -> dict:
    return {
        "base": base,
        "direction": "forward",
        "long_venue": long_venue,
        "short_venue": short_venue,
        "long_rate_pct": 0.03,
        "short_rate_pct": 0.15,
        "spread_pct": spread,
        "fee_pct": 0.10,
        "net_edge_pct": edge,
        "annual_apy_pct": 109.5,
        "settle_mismatch": mismatch,
    }


def test_load_and_run_basic(tmp_path: Path):
    path = tmp_path / "spreads.jsonl"
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    snaps = [
        _snap(t0, [_row(spread=0.12, edge=0.02)]),
        _snap(t0 + timedelta(hours=8), [_row(spread=0.10, edge=0.01)]),
        _snap(t0 + timedelta(hours=16), [_row(spread=0.08, edge=0.005)]),
        _snap(t0 + timedelta(hours=24), [_row(spread=0.06, edge=0.00)]),
    ]
    path.write_text("\n".join(json.dumps(s) for s in snaps) + "\n")

    snapshots = load_snapshots(path)
    assert len(snapshots) == 4

    result = run_backtest(
        snapshots,
        initial_capital=100000.0,
        trade_usd=5000.0,
        max_concurrent_pairs=3,
        min_spread_pct=0.05,
        min_edge_pct=0.005,
        exit_edge_pct=0.005,
    )

    assert result.trade_count >= 1
    assert result.total_return_pct != 0.0
    assert result.sharpe_ratio is not None
    assert len(result.equity_curve) == 4


def test_no_profitable_spreads(tmp_path: Path):
    path = tmp_path / "empty.jsonl"
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    snaps = [
        _snap(t0, []),
        _snap(t0 + timedelta(hours=8), []),
    ]
    path.write_text("\n".join(json.dumps(s) for s in snaps) + "\n")

    result = run_backtest(load_snapshots(path))
    assert result.trade_count == 0
    assert result.total_return_pct == 0.0
    assert result.win_rate_pct == 0.0


def test_mismatch_filtered_by_default(tmp_path: Path):
    path = tmp_path / "mismatch.jsonl"
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    snaps = [
        _snap(t0, [_row(spread=0.15, edge=0.03, mismatch=True)]),
        _snap(t0 + timedelta(hours=8), [_row(spread=0.10, edge=0.02, mismatch=True)]),
    ]
    path.write_text("\n".join(json.dumps(s) for s in snaps) + "\n")

    # Default: mismatch filtered out
    result = run_backtest(load_snapshots(path), allow_mismatch=False)
    assert result.trade_count == 0

    # Allow mismatch
    result2 = run_backtest(load_snapshots(path), allow_mismatch=True)
    assert result2.trade_count >= 1


def test_max_holding_forces_close(tmp_path: Path):
    path = tmp_path / "maxhold.jsonl"
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    # Keep spread alive for many periods
    snaps = [_snap(t0 + timedelta(hours=8 * i), [_row(spread=0.12, edge=0.02)]) for i in range(10)]
    path.write_text("\n".join(json.dumps(s) for s in snaps) + "\n")

    result = run_backtest(
        load_snapshots(path),
        max_holding_hours=24.0,  # Force close after 24h
        min_edge_pct=0.01,
        exit_edge_pct=0.001,
    )

    assert result.trade_count >= 1
    # At least one trade should have close_reason starting with "max_holding"
    reasons = [t["close_reason"] for t in result.trades]
    assert any("max_holding" in r for r in reasons)


def test_equity_curve_monotonic_with_profit(tmp_path: Path):
    """With consistent positive spreads, equity should increase."""
    path = tmp_path / "profit.jsonl"
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    snaps = [_snap(t0 + timedelta(hours=8 * i), [_row(spread=0.12, edge=0.02)]) for i in range(20)]
    path.write_text("\n".join(json.dumps(s) for s in snaps) + "\n")

    result = run_backtest(
        load_snapshots(path),
        initial_capital=100000.0,
        trade_usd=5000.0,
        exit_edge_pct=0.001,
        min_edge_pct=0.01,
        max_holding_hours=200.0,
    )

    assert len(result.equity_curve) == 20
    assert result.total_return_pct > 0


def test_settlements_crossed_epoch_aligned():
    t0 = datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)
    # 07:00 → 09:00 crosses the 08:00 boundary (8h grid)
    assert _settlements_crossed(t0, t0 + timedelta(hours=2), 8.0) == 1
    # 07:00 → 07:30 crosses nothing
    assert _settlements_crossed(t0, t0 + timedelta(minutes=30), 8.0) == 0
    # 07:00 → 23:30 crosses 08:00 and 16:00
    assert _settlements_crossed(t0, t0 + timedelta(hours=16, minutes=30), 8.0) == 2
    # 2h grid: 07:00 → 13:00 crosses 08/10/12
    assert _settlements_crossed(t0, t0 + timedelta(hours=6), 2.0) == 3
    # degenerate inputs
    assert _settlements_crossed(t0, t0, 8.0) == 0
    assert _settlements_crossed(t0, t0 + timedelta(hours=1), 0.0) == 0


def test_funding_accrual_independent_of_snapshot_frequency(tmp_path: Path):
    """5 分钟采集与 8 小时采集的资金费结果应一致（按结算边界计费）。"""
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    row = _row(spread=0.12, edge=0.02)

    # Coarse: every 8h, 4 snapshots (0, 8, 16, 24h)
    coarse = tmp_path / "coarse.jsonl"
    coarse.write_text("\n".join(
        json.dumps(_snap(t0 + timedelta(hours=8 * i), [row])) for i in range(4)
    ) + "\n")

    # Fine: every 1h over the same 24h window
    fine = tmp_path / "fine.jsonl"
    fine.write_text("\n".join(
        json.dumps(_snap(t0 + timedelta(hours=i), [row])) for i in range(25)
    ) + "\n")

    kwargs = dict(
        initial_capital=100000.0,
        trade_usd=5000.0,
        min_edge_pct=0.01,
        exit_edge_pct=0.001,
        max_holding_hours=999.0,
    )
    res_coarse = run_backtest(load_snapshots(coarse), **kwargs)
    res_fine = run_backtest(load_snapshots(fine), **kwargs)

    # Same settlements crossed → same funding → same return
    assert abs(res_coarse.total_return_pct - res_fine.total_return_pct) < 1e-9
    assert res_coarse.total_funding_collected_pct == res_fine.total_funding_collected_pct


def test_mismatch_interval_accrual(tmp_path: Path):
    """2h vs 8h 错配腿按各自周期结算。"""
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    row = {
        "base": "BTC",
        "direction": "forward",
        "long_venue": "bitget",
        "short_venue": "binance",
        "long_rate_pct": -0.01,   # 2h leg: long receives 0.01 per 2h
        "short_rate_pct": 0.10,   # 8h leg: short receives 0.10 per 8h
        "long_interval_h": 2.0,
        "short_interval_h": 8.0,
        "spread_pct": 0.11,
        "fee_pct": 0.10,
        "net_edge_pct": 0.05,
        "settle_mismatch": True,
    }
    snaps = [_snap(t0 + timedelta(hours=8 * i), [row]) for i in range(3)]
    path = tmp_path / "mm.jsonl"
    path.write_text("\n".join(json.dumps(s) for s in snaps) + "\n")

    result = run_backtest(
        load_snapshots(path),
        allow_mismatch=True,
        min_edge_pct=0.0,
        min_spread_pct=0.0,
        exit_edge_pct=-999.0,
        max_holding_hours=999.0,
    )
    assert result.trade_count == 1
    trade = result.trades[0]
    # Open at 00:00; held to 16:00 (backtest end).
    # Long leg (2h): 8 settlements; short leg (8h): 2 settlements
    assert trade["long_settlements"] == 8
    assert trade["short_settlements"] == 2
    # funding = 2 × 0.10 − 8 × (−0.01) = 0.28
    assert abs(result.total_funding_collected_pct - 0.28) < 1e-9
