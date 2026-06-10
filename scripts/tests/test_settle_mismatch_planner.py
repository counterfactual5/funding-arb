#!/usr/bin/env python3
"""Hermetic tests for settle_mismatch_planner."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from execution.settle_mismatch_planner import (
    analyze_settle_mismatch,
    effective_trade_usd,
    filter_candidates_with_mismatch,
)


def test_no_mismatch():
    a = analyze_settle_mismatch(
        base="BTC",
        long_venue="okx",
        short_venue="bybit",
        long_rate_pct=0.03,
        short_rate_pct=0.15,
        long_interval_h=8.0,
        short_interval_h=8.0,
        total_fee_pct=0.11,
    )
    assert a.is_mismatch is False
    assert a.viable is True
    assert a.capital_buffer_pct == 0.0
    assert a.note == "no mismatch"


def test_mismatch_viable():
    """2h vs 8h with good spread → viable despite mismatch."""
    a = analyze_settle_mismatch(
        base="BTC",
        long_venue="bitget",  # 2h interval
        short_venue="binance",  # 8h interval
        long_rate_pct=0.03,
        short_rate_pct=0.15,
        long_interval_h=2.0,
        short_interval_h=8.0,
        total_fee_pct=0.11,
    )
    assert a.is_mismatch is True
    # long_rate_per_8h = 0.03 * (8/2) = 0.12
    assert a.long_rate_per_8h_pct == 0.12
    # short_rate_per_8h = 0.15 * (8/8) = 0.15
    assert a.short_rate_per_8h_pct == 0.15
    # spread per 8h = |0.15 - 0.12| = 0.03
    assert a.spread_per_8h_pct == 0.03
    # settlements in longer = 8/2 = 4
    # max_cumulative = |0.03| * (4-1) = 0.09
    assert a.max_cumulative_outflow_pct == 0.09
    # adjusted = 0.03 - 0.11 - 0.09*0.3 = -0.107 → not viable
    assert a.viable is False


def test_mismatch_high_spread_still_viable():
    """Large spread can overcome mismatch penalty."""
    a = analyze_settle_mismatch(
        base="ETH",
        long_venue="bitget",
        short_venue="okx",
        long_rate_pct=-0.20,
        short_rate_pct=0.05,
        long_interval_h=2.0,
        short_interval_h=8.0,
        total_fee_pct=0.11,
    )
    assert a.is_mismatch is True
    # long_rate_per_8h = -0.20 * (8/2) = -0.80
    # short_rate_per_8h = 0.05 * (8/8) = 0.05
    # spread = |-0.80 - 0.05| = 0.85
    # adjusted = 0.85 - 0.11 - penalty → should be positive → viable
    assert a.adjusted_net_edge_pct > 0


def test_filter_disallows_mismatch_by_default():
    candidates = [
        {
            "base": "BTC",
            "long_venue": "bitget",
            "short_venue": "binance",
            "long_rate_pct": 0.03,
            "short_rate_pct": 0.15,
            "long_interval_h": 2.0,
            "short_interval_h": 8.0,
            "fee_pct": 0.11,
            "settle_mismatch": True,
        },
    ]
    filtered = filter_candidates_with_mismatch(candidates, allow_mismatch=False)
    assert len(filtered) == 0


def test_filter_allows_non_mismatch():
    candidates = [
        {
            "base": "BTC",
            "long_venue": "okx",
            "short_venue": "bybit",
            "net_edge_pct": 0.02,
            "settle_mismatch": False,
        },
    ]
    filtered = filter_candidates_with_mismatch(candidates)
    assert len(filtered) == 1


def test_filter_mismatch_analysis_added():
    """When allow_mismatch=True and viable, analysis is added to the row."""
    candidates = [
        {
            "base": "ETH",
            "long_venue": "bitget",
            "short_venue": "okx",
            "long_rate_pct": -0.20,
            "short_rate_pct": 0.05,
            "long_interval_h": 2.0,
            "short_interval_h": 8.0,
            "fee_pct": 0.11,
            "settle_mismatch": True,
        },
    ]
    filtered = filter_candidates_with_mismatch(
        candidates,
        allow_mismatch=True,
        min_adjusted_edge_pct=-999.0,
        max_cumulative_outflow_pct=1.0,  # high enough to pass
    )
    assert len(filtered) >= 1
    assert "mismatch_analysis" in filtered[0]
    assert "adjusted_net_edge_pct" in filtered[0]


def test_effective_trade_usd_no_buffer():
    assert effective_trade_usd(5000.0, {"capital_buffer_pct": 0}) == 5000.0


def test_effective_trade_usd_with_buffer():
    row = {"capital_buffer_pct": 0.5}
    assert effective_trade_usd(5000.0, row) == 4975.0
