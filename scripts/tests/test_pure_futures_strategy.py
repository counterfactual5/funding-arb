#!/usr/bin/env python3
"""Hermetic tests for pure_futures_spread strategy module."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies.futures.pure_futures_spread import (
    _annual_pct,
    _base_from_symbol,
    _extract_existing_pairs,
    _fee_pct,
    decide_pure_futures_spread,
)


def test_base_from_symbol():
    assert _base_from_symbol("BTCUSDT") == "BTC"
    assert _base_from_symbol("ETHUSDT") == "ETH"
    assert _base_from_symbol("PENDLEUSDT") == "PENDLE"
    assert _base_from_symbol("USDT") == ""


def test_annual_pct():
    # 0.05% per 8h → (0.05/100) * (365*24/8) * 100 = 54.75%
    apy = _annual_pct(0.05, 8.0)
    assert abs(apy - 54.75) < 0.1


def test_fee_pct():
    assert _fee_pct("binance", {}) == 0.05
    assert _fee_pct("okx", {}) == 0.05
    assert _fee_pct("bitget", {}) == 0.06
    assert _fee_pct("bybit", {}) == 0.055
    assert _fee_pct("unknown", {}) == 0.06
    assert _fee_pct("binance", {"binance": 0.04}) == 0.04


def test_extract_existing_pairs_empty():
    state = {"positions": {}}
    pairs = _extract_existing_pairs(state)
    assert pairs == {}


def test_extract_existing_pairs_with_pair():
    state = {
        "positions": {
            "BTCUSDT": {
                "side": "long",
                "venue": "okx",
                "amount": 0.5,
                "pair_id": "BTC:okx:bybit",
            },
            "BTCUSDT_short": {
                "side": "short",
                "venue": "bybit",
                "amount": 0.5,
                "pair_id": "BTC:okx:bybit",
                "symbol": "BTCUSDT",
            },
        },
    }
    pairs = _extract_existing_pairs(state)
    assert "BTC:okx:bybit" in pairs
    assert pairs["BTC:okx:bybit"]["base"] == "BTC"
    assert pairs["BTC:okx:bybit"]["long_venue"] == "okx"
    assert pairs["BTC:okx:bybit"]["short_venue"] == "bybit"


def test_decide_no_config():
    trades, meta = decide_pure_futures_spread(
        {}, {}, {}, {},
    )
    assert trades == []
    assert "pureFuturesArbitrage config missing" in meta["skipped_reasons"]


def test_decide_opens_pairs():
    cfg = {
        "pureFuturesArbitrage": {
            "maxConcurrentPairs": 2,
            "tradeUsdPerPair": 500,
            "minSpreadPct": 0.05,
            "maxSpreadPct": 0.50,
            "exitThresholdPct": 0.01,
        },
    }
    funding_rates = {
        "binance": {"BTCUSDT": 0.03},
        "okx": {"BTCUSDT": 0.15},
    }
    prices = {"BTC": 100000.0}

    trades, meta = decide_pure_futures_spread(
        {}, prices, cfg, funding_rates,
    )
    # spread = 0.15 - 0.03 = 0.12, fee = 0.05+0.05 = 0.10, net = 0.02 > 0
    assert len(trades) == 2  # open_long + open_short
    assert trades[0]["type"] == "open_long"
    assert trades[0]["venue"] == "binance"
    assert trades[1]["type"] == "open_short"
    assert trades[1]["venue"] == "okx"
    assert meta["strategy"] == "pure_futures_spread"
    assert len(meta["pairs_opened"]) == 1


def test_decide_no_profitable_spread():
    cfg = {
        "pureFuturesArbitrage": {
            "maxConcurrentPairs": 2,
            "tradeUsdPerPair": 500,
            "minSpreadPct": 0.05,
            "maxSpreadPct": 0.50,
            "exitThresholdPct": 0.01,
        },
    }
    # Same rate on both venues → no spread
    funding_rates = {
        "binance": {"BTCUSDT": 0.05},
        "okx": {"BTCUSDT": 0.05},
    }
    prices = {"BTC": 100000.0}

    trades, meta = decide_pure_futures_spread(
        {}, prices, cfg, funding_rates,
    )
    assert len(trades) == 0
    assert len(meta["pairs_opened"]) == 0


def test_decide_exit_existing_pair():
    cfg = {
        "pureFuturesArbitrage": {
            "maxConcurrentPairs": 2,
            "tradeUsdPerPair": 500,
            "minSpreadPct": 0.05,
            "maxSpreadPct": 0.50,
            "exitThresholdPct": 0.01,
        },
    }
    # Existing position with spread collapsed
    futures_state = {
        "positions": {
            "BTCUSDT": {
                "side": "long",
                "venue": "okx",
                "amount": 0.5,
                "pair_id": "BTC:okx:bybit",
            },
            "BTCUSDT_short": {
                "side": "short",
                "venue": "bybit",
                "amount": 0.5,
                "pair_id": "BTC:okx:bybit",
                "symbol": "BTCUSDT",
            },
        },
    }
    # Rates are now almost equal → spread collapsed
    funding_rates = {
        "okx": {"BTCUSDT": 0.05},
        "bybit": {"BTCUSDT": 0.05},
    }
    prices = {"BTC": 100000.0}

    trades, meta = decide_pure_futures_spread(
        futures_state, prices, cfg, funding_rates,
    )
    # Should generate close trades
    close_trades = [t for t in trades if t["type"].startswith("close_")]
    assert len(close_trades) == 2
    assert len(meta["pairs_closed"]) == 1
