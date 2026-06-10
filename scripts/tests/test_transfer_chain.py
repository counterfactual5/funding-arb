#!/usr/bin/env python3
"""Unit tests for transfer chain aliases and fee estimation (no network)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.unified_funding_pool import CrossRoute, UnifiedFundingPool  # noqa: E402
from transfer.chain_aliases import common_canonicals, native_chain, to_canonical  # noqa: E402
from transfer.cross_venue_router import CrossTransferRoute, estimate_transfer_fee  # noqa: E402


def test_to_canonical_bsc_aliases():
    assert to_canonical("BEP20") == "bsc"
    assert to_canonical("BSC") == "bsc"
    assert to_canonical("USDT-BSC") == "bsc"


def test_native_chain_roundtrip():
    assert native_chain("bsc", "bitget") == "BEP20"
    assert native_chain("bsc", "bybit") == "BSC"
    assert native_chain("plasma", "bitget") == "Plasma"
    assert native_chain("plasma", "bybit") == "PLASMA"


def test_common_canonicals_bitget_bybit():
    shared = common_canonicals("bitget", "bybit")
    assert "bsc" in shared
    assert "plasma" in shared
    assert "aptos" in shared


def test_estimate_transfer_fee_same_venue():
    fee, pct, chain = estimate_transfer_fee("bitget", "bitget", "USDT", 500)
    assert fee == 0.0
    assert pct == 0.0
    assert chain == ""


@patch("transfer.cross_venue_router.best_route")
def test_estimate_transfer_fee_cross_venue(mock_best):
    mock_best.return_value = CrossTransferRoute(
        canonical="plasma",
        from_venue="bybit",
        to_venue="bitget",
        coin="USDT",
        amount=500,
        from_chain="PLASMA",
        to_chain="Plasma",
        withdraw_fee=0.0,
        fee_pct=0.0,
        total_fee=0.0,
        net_est=500.0,
        min_withdraw=1.0,
        min_deposit=0.0,
        from_label="PLASMA",
        to_label="Plasma",
        viable=True,
    )
    fee, pct, chain = estimate_transfer_fee("bybit", "bitget", "USDT", 500)
    assert fee == 0.0
    assert pct == 0.0
    assert chain == "plasma"


@patch("transfer.cross_venue_router.estimate_transfer_fee")
def test_unified_pool_apply_transfer_cost(mock_est):
    mock_est.return_value = (0.5, 0.1, "bsc")
    pool = UnifiedFundingPool(venues=("bitget", "bybit"), reference_trade_usd=500)
    route = CrossRoute(
        base="BTC",
        direction="forward",
        futures_venue="bitget",
        spot_venue="bybit",
        funding_rate_pct=0.08,
        interval_h=8.0,
        next_funding_ts=0,
        borrow_per_period_pct=0.0,
        futures_fee_pct=0.06,
        spot_fee_pct=0.1,
        total_fee_pct=0.16,
        net_edge_pct=0.05,
        annual_funding_pct=87.6,
        same_venue=False,
    )
    out = pool._apply_transfer_cost(route)
    assert out.transfer_fee_usdt == 0.5
    assert out.transfer_fee_pct == 0.1
    assert out.transfer_chain == "bsc"
    assert out.net_edge_all_in_pct == 0.05 - 0.1


def test_unified_pool_same_venue_skips_transfer():
    pool = UnifiedFundingPool(venues=("bitget",), reference_trade_usd=500)
    route = CrossRoute(
        base="BTC",
        direction="forward",
        futures_venue="bitget",
        spot_venue="bitget",
        funding_rate_pct=0.08,
        interval_h=8.0,
        next_funding_ts=0,
        borrow_per_period_pct=0.0,
        futures_fee_pct=0.06,
        spot_fee_pct=0.1,
        total_fee_pct=0.16,
        net_edge_pct=0.05,
        annual_funding_pct=87.6,
        same_venue=True,
    )
    out = pool._apply_transfer_cost(route)
    assert out.transfer_fee_usdt == 0.0
    assert out.net_edge_all_in_pct == 0.05
