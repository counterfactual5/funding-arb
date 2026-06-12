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


def test_parse_fee_policy_idempotent():
    """Re-parsing an already-parsed policy must not drop VIP tiers.

    The server layer parses once and passes the result to scanners, which
    parse again; a non-idempotent parse silently degraded everything to vip0.
    """
    raw = {"fee_mode": "auto", "venue_fee_tiers": {"bybit": "vip5", "okx": "vip5"}}
    once = parse_fee_policy(raw)
    twice = parse_fee_policy(once)
    assert twice == once
    assert twice["venue_tiers"] == {"bybit": "vip5", "okx": "vip5"}


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
    assert cache[("bybit", "BTCUSDT")]["taker_pct"] == 0.0375  # vip2 futures


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


# ---------------------------------------------------------------------------
# Perp-DEX public fee fetchers
# ---------------------------------------------------------------------------
import json as _json
from unittest.mock import patch

import core.fee_providers as fp


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        return _json.dumps(self._p).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_hyperliquid_fee_applies_staking_and_referral():
    fp._HL_FEE_CACHE = None
    payload = {
        "userCrossRate": "0.00045",
        "userAddRate": "0.00015",
        "activeStakingDiscount": {"discount": "0.2"},
        "activeReferralDiscount": "0.1",
    }
    with patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
        r = fp._fetch_hyperliquid("BTCUSDT")
    # taker = 0.045% * (1-0.2) * (1-0.1) = 0.0324 ; maker = 0.015% * (1-0.2) = 0.012
    assert round(r["taker_pct"], 5) == 0.0324
    assert round(r["maker_pct"], 5) == 0.012
    fp._HL_FEE_CACHE = None


def test_hyperliquid_base_rate_no_discount():
    fp._HL_FEE_CACHE = None
    payload = {
        "userCrossRate": "0.00045",
        "userAddRate": "0.00015",
        "activeStakingDiscount": {"discount": "0.0"},
        "activeReferralDiscount": "0.0",
    }
    with patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
        r = fp._fetch_hyperliquid("BTCUSDT")
    assert round(r["taker_pct"], 5) == 0.045  # live base, not the stale 0.035
    fp._HL_FEE_CACHE = None


def test_lighter_fee_from_market_meta():
    with patch(
        "venues.lighter_funding.LighterFundingProvider.market_meta_for_base",
        return_value={"taker_fee": 0.0002, "maker_fee": 0.0},
    ):
        r = fp._fetch_lighter("BTCUSDT")
    assert round(r["taker_pct"], 5) == 0.02  # 0.0002 fraction → 0.02%
    assert r["maker_pct"] == 0.0


def test_edgex_fee_from_contract_meta():
    with patch(
        "venues.edgex_funding.EdgexFundingProvider.contract_meta_for_base",
        return_value={"taker_pct": 0.038},
    ):
        r = fp._fetch_edgex("BTCUSDT")
    assert r["taker_pct"] == 0.038


def test_public_fee_venues_use_api_without_credentials(monkeypatch):
    for k in ("HYPERLIQUID_API_KEY", "HYPERLIQUID_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    pol = parse_fee_policy({"fee_mode": "auto"})
    assert venue_uses_api("hyperliquid", pol) is True
    assert venue_uses_api("lighter", pol) is True
    assert venue_uses_api("edgex", pol) is True
    # vip_tier mode still forces the static tier table
    assert venue_uses_api("hyperliquid", parse_fee_policy({"fee_mode": "vip_tier"})) is False
