#!/usr/bin/env python3
"""Unit tests for per-symbol fee providers (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.fee_providers import (
    _decimal_to_pct,
    normalize_symbol,
    offline_fee_cache_from_by_base,
    pair_open_taker_fee_pct,
    taker_fee_pct,
)


def test_normalize_symbol():
    assert normalize_symbol("btcusdt") == "BTCUSDT"
    assert normalize_symbol("ETH-USDT-SWAP") == "ETHUSDT"


def test_decimal_to_pct():
    assert _decimal_to_pct(0.00055) == 0.055
    assert _decimal_to_pct(0.11) == 0.11


def test_offline_fee_cache_per_symbol():
    by_base = {
        "ESPORTS": {
            "bybit": {"symbol": "ESPORTSUSDT", "rate_pct": 0.1},
            "bitget": {"symbol": "ESPORTSUSDT", "rate_pct": 0.05},
        },
    }
    cache = offline_fee_cache_from_by_base(by_base)
    # venue defaults when offline
    assert cache[("bybit", "ESPORTSUSDT")]["taker_pct"] == 0.055
    assert cache[("bitget", "ESPORTSUSDT")]["taker_pct"] == 0.06


def test_pair_fee_uses_cache_not_venue_default():
    """Bybit ESPORTS taker 0.11% must not be lumped with BTC default 0.055%."""
    cache = {
        ("bybit", "ESPORTSUSDT"): {"taker_pct": 0.11, "maker_pct": 0.02},
        ("bitget", "ESPORTSUSDT"): {"taker_pct": 0.06, "maker_pct": 0.02},
    }
    long_fee, short_fee, total = pair_open_taker_fee_pct(
        "bitget",
        "ESPORTSUSDT",
        "bybit",
        "ESPORTSUSDT",
        fee_cache=cache,
    )
    assert long_fee == 0.06
    assert short_fee == 0.11
    assert abs(total - 0.17) < 1e-9


def test_config_override_beats_cache():
    cache = {("binance", "BTCUSDT"): {"taker_pct": 0.05, "maker_pct": 0.02}}
    assert (
        taker_fee_pct(
            "binance",
            "BTCUSDT",
            fee_cache=cache,
            config_overrides={"binance": 0.04},
        )
        == 0.04
    )
