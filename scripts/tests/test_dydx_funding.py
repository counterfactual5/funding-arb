#!/usr/bin/env python3
"""Tests for dYdX v4 funding provider (mocked indexer, no network)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from venues.dydx_funding import (  # noqa: E402
    DydxFundingProvider,
    _symbol_from_ticker,
    _ticker_from_symbol,
)

_BTC_MARKET = {
    "ticker": "BTC-USD",
    "status": "ACTIVE",
    "oraclePrice": "64000.5",
    "nextFundingRate": "0.0000125",
}

_HIST = {
    "historicalFunding": [
        {
            "ticker": "BTC-USD",
            "rate": "0.00000125",
            "effectiveAt": "2026-06-12T10:00:00.312Z",
        },
        {
            "ticker": "BTC-USD",
            "rate": "0.00000225",
            "effectiveAt": "2026-06-12T09:00:00.613Z",
        },
    ]
}


class TestHelpers:
    def test_symbol_mapping(self):
        assert _ticker_from_symbol("BTCUSDT") == "BTC-USD"
        assert _symbol_from_ticker("ETH-USD") == "ETHUSDT"


class TestFetch:
    def test_fetch_all_active(self):
        payload = {"markets": {"BTC-USD": _BTC_MARKET, "OLD-USD": {"status": "FINAL_SETTLEMENT"}}}

        with patch.object(DydxFundingProvider, "_get", return_value=payload):
            rows = DydxFundingProvider().fetch_all()
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BTCUSDT"
        assert rows[0]["rate_pct"] == 0.0000125 * 100
        assert rows[0]["mark_price"] == 64000.5

    def test_fetch_current(self):
        payload = {"markets": {"BTC-USD": _BTC_MARKET}}

        with patch.object(DydxFundingProvider, "_get", return_value=payload):
            cur = DydxFundingProvider().fetch_current("BTCUSDT")
        assert cur["rate_pct"] == 0.0000125 * 100
        assert cur["interval_ms"] == 3_600_000
        assert cur["mark_price"] == 64000.5

    def test_fetch_since(self):
        with patch.object(DydxFundingProvider, "_get", return_value=_HIST):
            rows = DydxFundingProvider().fetch_since("BTCUSDT", start_ms=0, max_pages=1)
        assert len(rows) == 2
        assert rows[0]["rate_pct"] == 0.00000225 * 100
        assert rows[1]["rate_pct"] == 0.00000125 * 100

    def test_registered_provider(self):
        from backtest.funding_providers import get_funding_provider

        p = get_funding_provider("dydx")
        assert p.venue_id == "dydx"
