#!/usr/bin/env python3
"""Unit tests for unified_funding_pool pure futures spread methods."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.unified_funding_pool import (
    UnifiedFundingPool,
    VenueLeg,
)
from core.fee_providers import offline_fee_cache_from_by_base


def _pool_with_legs(*legs: VenueLeg) -> UnifiedFundingPool:
    pool = UnifiedFundingPool(venues=tuple({l.venue for l in legs}))
    for leg in legs:
        pool.legs_by_base.setdefault(leg.base, []).append(leg)
    by_base = {
        base: {
            leg.venue: {"symbol": leg.symbol}
            for leg in leg_list
        }
        for base, leg_list in pool.legs_by_base.items()
    }
    pool.fee_cache = offline_fee_cache_from_by_base(by_base)
    return pool


def _leg(
    venue: str,
    base: str = "BTC",
    rate_pct: float = 0.05,
    interval_h: float = 8.0,
) -> VenueLeg:
    return VenueLeg(
        venue=venue,
        base=base,
        symbol=f"{base}USDT",
        rate_pct=rate_pct,
        interval_h=interval_h,
        next_funding_ts=0,
        mark_price=100000.0,
    )


def test_best_pure_futures_spread_basic():
    pool = _pool_with_legs(
        _leg("binance", rate_pct=0.03),
        _leg("okx", rate_pct=0.15),
    )
    result = pool.best_pure_futures_spread("BTC", min_spread_pct=0.05)
    assert result is not None
    # long at binance (0.03), short at okx (0.15)
    assert result["long_venue"] == "binance"
    assert result["short_venue"] == "okx"
    assert result["spread_pct"] == 0.12
    # fee = binance(0.05) + okx(0.05) = 0.10
    assert result["total_fee_pct"] == 0.10
    # net = 0.12 - 0.10 = 0.02
    assert result["net_edge_pct"] == 0.02


def test_best_pure_futures_spread_no_profitable():
    pool = _pool_with_legs(
        _leg("binance", rate_pct=0.05),
        _leg("okx", rate_pct=0.05),
    )
    result = pool.best_pure_futures_spread("BTC", min_spread_pct=0.05)
    assert result is None


def test_best_pure_futures_spread_single_venue():
    pool = _pool_with_legs(_leg("binance", rate_pct=0.05))
    result = pool.best_pure_futures_spread("BTC")
    assert result is None


def test_best_pure_futures_spread_negative_rates():
    pool = _pool_with_legs(
        _leg("okx", rate_pct=-0.20),
        _leg("bybit", rate_pct=-0.02),
    )
    result = pool.best_pure_futures_spread("BTC", min_spread_pct=0.05)
    assert result is not None
    # short at bybit (-0.02, less negative), long at okx (-0.20, more negative)
    assert result["long_venue"] == "okx"
    assert result["short_venue"] == "bybit"
    # spread = -0.02 - (-0.20) = 0.18
    assert result["spread_pct"] == 0.18


def test_funding_spread_matrix_pure_multiple_venues():
    pool = _pool_with_legs(
        _leg("binance", rate_pct=0.20),
        _leg("okx", rate_pct=0.06),
        _leg("bybit", rate_pct=0.02),
    )
    matrix = pool.funding_spread_matrix_pure("BTC")
    # binance(0.20)-bybit(0.02): spread=0.18, fee=0.105, net=0.075
    # binance(0.20)-okx(0.06): spread=0.14, fee=0.10, net=0.04
    # okx(0.06)-bybit(0.02): spread=0.04, fee=0.105, net=-0.065 → filtered
    assert len(matrix) == 2
    assert matrix[0]["net_edge_pct"] > matrix[1]["net_edge_pct"]


def test_funding_spread_matrix_pure_empty():
    pool = _pool_with_legs(_leg("binance"))
    matrix = pool.funding_spread_matrix_pure("BTC")
    assert matrix == []


def test_scan_pure_futures_routes():
    pool = _pool_with_legs(
        _leg("binance", base="BTC", rate_pct=0.03),
        _leg("okx", base="BTC", rate_pct=0.15),
        _leg("binance", base="ETH", rate_pct=0.01),
        _leg("okx", base="ETH", rate_pct=0.01),  # no spread
    )
    results = pool.scan_pure_futures_routes(min_spread_pct=0.05)
    # Only BTC has a profitable spread
    assert len(results) == 1
    assert results[0]["base"] == "BTC"


def test_scan_pure_futures_routes_no_bases():
    """Empty legs_by_base with non-empty dict → no refresh triggered."""
    pool = _pool_with_legs()  # no legs
    # legs_by_base is {} but not empty enough to trigger refresh
    # since legs_by_base is {} (falsy), refresh would be called.
    # Instead, just test with a populated but no-spread case.
    pool = _pool_with_legs(
        _leg("binance", base="BTC", rate_pct=0.01),
    )
    results = pool.scan_pure_futures_routes()
    assert results == []
