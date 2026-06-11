#!/usr/bin/env python3
"""Unit tests for per-symbol fee providers (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.fee_providers import (
    _decimal_to_pct,
    build_policy_futures_cache,
    carry_two_leg_fee_pct,
    normalize_symbol,
    offline_fee_cache_from_by_base,
    pair_open_taker_fee_pct,
    parse_fee_policy,
    taker_fee_pct,
    venue_uses_api,
)
from core.vip_fee_tiers import list_venue_tiers, tier_rates


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


def test_carry_two_leg_fee_from_cache():
    futures_cache = {("binance", "BTCUSDT"): {"taker_pct": 0.04, "maker_pct": 0.02}}
    spot_cache = {("binance", "BTCUSDT"): {"taker_pct": 0.08, "maker_pct": 0.08}}
    spot, futures, total = carry_two_leg_fee_pct(
        "binance",
        "BTCUSDT",
        futures_cache=futures_cache,
        spot_cache=spot_cache,
    )
    assert spot == 0.08
    assert futures == 0.04
    assert total == 0.12


def test_tier_rates_binance_vip2():
    rates = tier_rates("binance", "vip2")
    assert rates["spot_taker_pct"] == 0.08
    assert rates["futures_taker_pct"] == 0.035


def test_list_venue_tiers_has_vip0():
    tiers = list_venue_tiers("bybit")
    assert any(t["id"] == "vip0" for t in tiers)


def test_venue_uses_api_vip_tier_mode(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "x")
    policy = parse_fee_policy({"fee_mode": "vip_tier", "venue_fee_tiers": {}})
    assert venue_uses_api("binance", policy) is False


def test_build_policy_futures_cache_uses_tier_without_api(monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_API_KEY", raising=False)
    by_base = {
        "BTC": {
            "binance": {"symbol": "BTCUSDT", "rate_pct": 0.01},
            "bybit": {"symbol": "BTCUSDT", "rate_pct": 0.02},
        },
    }
    policy = parse_fee_policy({"fee_mode": "auto", "venue_fee_tiers": {"bybit": "vip2"}})
    cache = build_policy_futures_cache(by_base, policy)
    assert cache[("binance", "BTCUSDT")]["taker_pct"] == 0.05  # vip0 futures
    assert cache[("bybit", "BTCUSDT")]["taker_pct"] == 0.045  # vip2 futures


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
