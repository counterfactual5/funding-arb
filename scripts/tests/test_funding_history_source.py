#!/usr/bin/env python3
"""Hermetic tests for funding_history_source (no network)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.backtest_pure_futures_spread import run_backtest
from backtest.funding_history_source import build_snapshots, infer_interval_h

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
    # 数据太少 → 默认 8h
    assert infer_interval_h(_hist(T0, 1.0, [0.01, 0.02])) == 8.0


def test_build_snapshots_structure_and_rates():
    histories = {
        ("binance", "BTC"): _hist(T0, 8.0, [0.01, 0.02, 0.03]),
        ("okx", "BTC"): _hist(T0, 8.0, [0.13, 0.14, 0.15]),
    }
    snaps = build_snapshots(histories)
    # 网格 = 3 个共同结算点
    assert len(snaps) == 3
    assert all("_ts" in s for s in snaps)
    # t=T0 的可见费率 = 在 T0 结算的费率
    row0 = snaps[0]["forward"][0]
    assert row0["long_venue"] == "binance"  # 低费率做多
    assert row0["short_venue"] == "okx"
    assert abs(row0["long_rate_pct"] - 0.01) < 1e-9
    assert abs(row0["short_rate_pct"] - 0.13) < 1e-9
    assert abs(row0["spread_pct"] - 0.12) < 1e-9
    assert row0["settle_mismatch"] is False
    # 第二个结算点携带第二期费率
    row1 = snaps[1]["forward"][0]
    assert abs(row1["spread_pct"] - 0.12) < 1e-9
    assert abs(row1["long_rate_pct"] - 0.02) < 1e-9


def test_build_snapshots_mismatch_flag():
    histories = {
        ("bitget", "ETH"): _hist(T0, 2.0, [-0.01] * 12),  # 2h 腿
        ("binance", "ETH"): _hist(T0, 8.0, [0.10] * 3),   # 8h 腿
    }
    snaps = build_snapshots(histories)
    # 网格 = 2h 腿 12 个点 ∪ 8h 腿 3 个点（重合）= 12
    assert len(snaps) == 12
    row = snaps[0]["forward"][0]
    assert row["settle_mismatch"] is True
    assert row["long_interval_h"] == 2.0
    assert row["short_interval_h"] == 8.0


def test_history_end_to_end_funding_matches_hand_calc():
    """10 期恒定 spread 0.12，验证回测资金费与手算一致。"""
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
    # T0 开仓，此后跨过 9 个结算边界
    assert trade["long_settlements"] == 9
    assert trade["short_settlements"] == 9
    # funding = 9 × (0.13 − 0.01) = 1.08
    assert abs(result.total_funding_collected_pct - 1.08) < 1e-9
    # fee = 2 × (binance 0.05 + okx 0.05) = 0.20 → pnl = 0.88% × 5000 = $44
    assert abs(result.total_return_pct - (1.08 - 0.20) * 5000 / 100000) < 1e-6


def test_mid_backtest_close_returns_margin():
    """回归：中途平仓必须归还锁定的保证金（曾只加 pnl 导致权益假性巨亏）。"""
    # 前 2 期 spread 0.12，之后收窄到 0.01 → edge collapse 中途平仓
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
    # 持仓跨过 T0+8h（spread 0.12）和 T0+16h（spread 已收窄至 0.01）两个结算，
    # 在 T0+16h 快照触发 edge_collapse 平仓 → funding = 0.13，fee = 0.20
    expected_return = (0.13 - 0.20) * 5000 / 100000
    assert abs(result.total_return_pct - expected_return) < 1e-6


def test_interval_switch_uses_local_gap():
    """回归：币种中途切换结算周期（如 8h→1h）时按局部间隔累计。

    全局中位数推断会在 8h 时段按 1h 边界数 8 倍多计资金费。
    """
    h8 = [T0 + i * 8 * H for i in range(4)]            # 0,8,16,24h
    h1 = [T0 + 24 * H + (i + 1) * H for i in range(8)]  # 25..32h
    ts_all = h8 + h1
    histories = {
        ("binance", "BTC"): [{"ts": t, "rate_pct": 0.01} for t in ts_all],
        ("okx", "BTC"): [{"ts": t, "rate_pct": 0.13} for t in ts_all],
    }
    snaps = build_snapshots(histories)
    # 8h 时段的行应报 8h 周期，1h 时段报 1h
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
    # T0 开仓后实际结算：8h 时段 3 次 + 1h 时段 8 次 = 11
    assert trade["long_settlements"] == 11
    assert trade["short_settlements"] == 11
    assert abs(result.total_funding_collected_pct - 11 * 0.12) < 1e-9


def test_pre_listing_gap_no_phantom_accrual():
    """回归：一腿晚上市时，上市前不得出现该配对（曾把首笔费率向前桥接，
    幻影累计数周资金费）。"""
    n = 20
    late_offset = 10  # okx 腿晚 10 期上市
    histories = {
        ("binance", "BTC"): _hist(T0, 8.0, [0.01] * n),
        ("okx", "BTC"): _hist(T0 + late_offset * 8 * H, 8.0, [0.13] * (n - late_offset)),
    }
    snaps = build_snapshots(histories)
    # okx 上市前的快照不应有 BTC 配对行
    for s in snaps:
        ts_ms = s["_ts"].timestamp() * 1000
        rows = s["forward"] + s["reverse"]
        if ts_ms < T0 + (late_offset - 1) * 8 * H:
            assert rows == [], f"上市前 {s['timestamp']} 不应有配对"
    result = run_backtest(
        snaps,
        min_spread_pct=0.0,
        min_edge_pct=0.0,
        exit_edge_pct=-999.0,
        max_holding_hours=9999.0,
    )
    assert result.trade_count == 1
    # 只可能累计上市后的结算（≤ n - late_offset 期）
    assert result.trades[0]["long_settlements"] <= n - late_offset


def test_data_hole_drops_leg():
    """回归：历史中段缺口（>8h×1.5）期间该腿应被剔除，持仓被平掉。"""
    # binance 腿在第 5~14 期之间有 80h 数据洞
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
                f"数据洞内 {s['timestamp']} 不应有配对"
            )
    result = run_backtest(
        snaps,
        min_spread_pct=0.0,
        min_edge_pct=0.0,
        exit_edge_pct=-999.0,
        max_holding_hours=9999.0,
    )
    # 洞前开仓的配对应在洞开始时被平掉，洞后可重新开仓
    assert result.trade_count == 2
    assert "spread_disappeared" in result.trades[0]["close_reason"]


def test_leg_history_exhaustion_closes_pair():
    """一腿历史提前结束 → 配对行消失，持仓应被平掉而非悬挂。"""
    histories = {
        ("binance", "BTC"): _hist(T0, 8.0, [0.01] * 10),
        ("okx", "BTC"): _hist(T0, 8.0, [0.13] * 5),  # 提前 5 期结束
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
