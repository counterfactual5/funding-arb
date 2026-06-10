#!/usr/bin/env python3
"""Hermetic unit tests for pure-futures spread scanner (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cli.scan_pure_futures_spreads import (
    _annual_from_rate,
    _base_from_symbol,
    _scan_spreads,
)
from core.fee_providers import offline_fee_cache_from_by_base


def _scan(by_base, min_spread: float, min_edge: float):
    return _scan_spreads(
        by_base,
        min_spread,
        min_edge,
        fee_cache=offline_fee_cache_from_by_base(by_base),
    )


def test_base_from_symbol():
    assert _base_from_symbol("BTCUSDT") == "BTC"
    assert _base_from_symbol("ethusdt") == "ETH"
    assert _base_from_symbol("PENDLEUSDT") == "PENDLE"


def test_annual_from_rate():
    apy = _annual_from_rate(0.1, 8.0)
    assert apy > 90  # 0.1% per 8h → ~109.5% annual


def test_scan_spreads_basic_forward():
    """Short at higher rate (bitget 0.15), long at lower (binance 0.03)."""
    by_base = {
        "BTC": {
            "bitget": {
                "symbol": "BTCUSDT",
                "rate_pct": 0.15,
                "interval_h": 8.0,
                "next_funding_ts": 100000000,
                "mark_price": 100000.0,
            },
            "binance": {
                "symbol": "BTCUSDT",
                "rate_pct": 0.03,
                "interval_h": 8.0,
                "next_funding_ts": 100000100,
                "mark_price": 100001.0,
            },
        },
    }
    fwd, rev = _scan(by_base, min_spread=0.01, min_edge=0.001)
    assert len(fwd) == 1
    assert len(rev) == 0
    assert fwd[0]["base"] == "BTC"
    assert fwd[0]["short_venue"] == "bitget"  # shorts at higher rate
    assert fwd[0]["long_venue"] == "binance"  # longs at lower rate
    assert fwd[0]["direction"] == "forward"
    assert fwd[0]["spread_pct"] == 0.12  # 0.15 - 0.03
    # fee = 0.06 (bitget) + 0.05 (binance) = 0.11, net = 0.12 - 0.11 = 0.01
    assert fwd[0]["net_edge_pct"] == 0.01
    assert fwd[0]["annual_apy_pct"] > 0


def test_scan_spreads_basic_reverse():
    """Both negative: short at less-negative (-0.01), long at more-negative (-0.08)."""
    by_base = {
        "ETH": {
            "okx": {
                "symbol": "ETHUSDT",
                "rate_pct": -0.08,
                "interval_h": 8.0,
                "next_funding_ts": 100000000,
                "mark_price": 3000.0,
            },
            "bybit": {
                "symbol": "ETHUSDT",
                "rate_pct": -0.01,
                "interval_h": 8.0,
                "next_funding_ts": 100000100,
                "mark_price": 3001.0,
            },
        },
    }
    fwd, rev = _scan(by_base, min_spread=0.01, min_edge=0.001)
    # spread = (-0.01) - (-0.08) = 0.07, fee = 0.055 + 0.05 = 0.105 → net = -0.035 < 0.001 → filtered
    # Need spread > 0.11 to beat fees. Use larger gap.
    assert len(fwd) == 0
    assert len(rev) == 0  # Filtered by net edge

    # Now test with large enough spread to beat fees
    by_base2 = {
        "ETH": {
            "okx": {
                "symbol": "ETHUSDT",
                "rate_pct": -0.20,
                "interval_h": 8.0,
                "next_funding_ts": 100000000,
                "mark_price": 3000.0,
            },
            "bybit": {
                "symbol": "ETHUSDT",
                "rate_pct": -0.01,
                "interval_h": 8.0,
                "next_funding_ts": 100000100,
                "mark_price": 3001.0,
            },
        },
    }
    fwd2, rev2 = _scan(by_base2, min_spread=0.01, min_edge=0.001)
    assert len(fwd2) == 0
    assert len(rev2) == 1  # Both negative → direction=reverse
    assert (
        rev2[0]["short_venue"] == "bybit"
    )  # shorts at -0.01 (less negative, pays less to longs)
    assert (
        rev2[0]["long_venue"] == "okx"
    )  # longs at -0.20 (more negative, receives from shorts)
    # spread = -0.01 - (-0.20) = 0.19, fee = 0.105, net = 0.085
    assert rev2[0]["net_edge_pct"] == 0.085


def test_scan_spreads_below_min_spread_ignored():
    by_base = {
        "BTC": {
            "bitget": {
                "symbol": "BTCUSDT",
                "rate_pct": 0.02,
                "interval_h": 8.0,
                "next_funding_ts": 10000,
                "mark_price": 100000.0,
            },
            "binance": {
                "symbol": "BTCUSDT",
                "rate_pct": 0.01,
                "interval_h": 8.0,
                "next_funding_ts": 10100,
                "mark_price": 100001.0,
            },
        },
    }
    fwd, rev = _scan(by_base, min_spread=0.05, min_edge=0.001)
    assert len(fwd) == 0
    assert len(rev) == 0


def test_scan_spreads_below_net_edge_ignored():
    """Spread is there but fees eat it."""
    by_base = {
        "ETH": {
            "bitget": {
                "symbol": "ETHUSDT",
                "rate_pct": 0.12,
                "interval_h": 8.0,
                "next_funding_ts": 10000,
                "mark_price": 3000.0,
            },
            "binance": {
                "symbol": "ETHUSDT",
                "rate_pct": 0.08,
                "interval_h": 8.0,
                "next_funding_ts": 10100,
                "mark_price": 3001.0,
            },
        },
    }
    fwd, rev = _scan(by_base, min_spread=0.02, min_edge=0.05)
    # spread=0.04, fee=0.11 → net=-0.07 < 0.05 → filtered out
    assert len(fwd) == 0
    assert len(rev) == 0


def test_scan_spreads_three_venues():
    by_base = {
        "SOL": {
            "binance": {
                "symbol": "SOLUSDT",
                "rate_pct": 0.20,
                "interval_h": 8.0,
                "next_funding_ts": 10000,
                "mark_price": 150.0,
            },
            "bybit": {
                "symbol": "SOLUSDT",
                "rate_pct": 0.02,
                "interval_h": 8.0,
                "next_funding_ts": 10100,
                "mark_price": 150.1,
            },
            "okx": {
                "symbol": "SOLUSDT",
                "rate_pct": 0.06,
                "interval_h": 8.0,
                "next_funding_ts": 10200,
                "mark_price": 150.05,
            },
        },
    }
    fwd, rev = _scan(by_base, min_spread=0.01, min_edge=0.001)
    # binance(0.20)-bybit(0.02): spread=0.18, fee=0.05+0.055=0.105, net=0.075 → pass
    # binance(0.20)-okx(0.06): spread=0.14, fee=0.05+0.05=0.10, net=0.04 → pass
    # okx(0.06)-bybit(0.02): spread=0.04, fee=0.05+0.055=0.105, net=-0.065 → skip
    assert len(fwd) == 2


def test_settle_mismatch_detection():
    by_base = {
        "BTC": {
            "bitget": {
                "symbol": "BTCUSDT",
                "rate_pct": 0.20,
                "interval_h": 2.0,
                "next_funding_ts": 10000,
                "mark_price": 100000.0,
            },
            "binance": {
                "symbol": "BTCUSDT",
                "rate_pct": 0.02,
                "interval_h": 8.0,
                "next_funding_ts": 10100,
                "mark_price": 100001.0,
            },
        },
    }
    fwd, rev = _scan(by_base, min_spread=0.01, min_edge=0.001)
    assert len(fwd) == 1
    # Bitget shorts at 0.20 (2h interval), Binance longs at 0.02 (8h interval) → mismatch
    assert fwd[0]["settle_mismatch"] is True


def test_blacklist_ignored():
    base = _base_from_symbol("USDCUSDT")
    assert base == "USDC"
    # The blacklist check happens in scan_pure_futures_spreads via fetch_all_rate_rows_by_base
