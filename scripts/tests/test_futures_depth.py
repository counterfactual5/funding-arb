#!/usr/bin/env python3
"""Hermetic tests for futures depth pre-check (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from market.futures_depth import check_pair_depth, depth_usd_within


def _book(mid: float = 100.0, qty: float = 10.0, levels: int = 5, step: float = 0.1):
    """Symmetric order book: bids from mid−step/2 downward, asks from mid+step/2 upward."""
    bids = [(mid - step / 2 - i * step, qty) for i in range(levels)]
    asks = [(mid + step / 2 + i * step, qty) for i in range(levels)]
    return {"bids": bids, "asks": asks}


def test_depth_usd_within_window():
    book = _book(mid=100.0, qty=10.0, levels=5, step=0.1)
    # Window 0.3%: mid=100, asks at 100.05/100.15/100.25 three levels ≤ 100.3
    usd = depth_usd_within(book, "asks", 0.3)
    assert abs(usd - (100.05 + 100.15 + 100.25) * 10) < 1e-6
    # bids side is symmetric
    usd_b = depth_usd_within(book, "bids", 0.3)
    assert abs(usd_b - (99.95 + 99.85 + 99.75) * 10) < 1e-6
    # Empty order book → 0
    assert depth_usd_within({"bids": [], "asks": []}, "asks", 0.3) == 0.0


def test_check_pair_depth_pass_and_fail():
    deep = _book(qty=100.0)  # ~30k USD within window
    thin = _book(qty=0.1)  # ~30 USD within window

    def fetcher_deep(venue, base, quote):
        return deep

    ok, detail = check_pair_depth(
        "binance",
        "okx",
        "BTC",
        500,
        max_dev_pct=0.3,
        min_multiple=3.0,
        depth_fetcher=fetcher_deep,
    )
    assert ok, detail

    def fetcher_thin_short(venue, base, quote):
        return deep if venue == "binance" else thin

    ok, detail = check_pair_depth(
        "binance",
        "okx",
        "BTC",
        500,
        max_dev_pct=0.3,
        min_multiple=3.0,
        depth_fetcher=fetcher_thin_short,
    )
    assert not ok
    assert "short@okx" in detail


def test_check_pair_depth_fetch_failure_skips():
    """Order book fetch failure should not block trading (same philosophy as margin check)."""

    def fetcher_err(venue, base, quote):
        raise RuntimeError("api down")

    ok, detail = check_pair_depth(
        "binance",
        "okx",
        "BTC",
        500,
        depth_fetcher=fetcher_err,
    )
    assert ok
    assert "depth_fetch_failed" in detail
