#!/usr/bin/env python3
"""Tests for the EdgeX funding provider (mocked V1 REST, no network)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import venues.edgex_funding as ef
from venues.edgex_funding import EdgexFundingProvider, _base_from_contract_name

_META = {
    "code": "SUCCESS",
    "data": {
        "contractList": [
            {
                "contractId": "10000001",
                "contractName": "BTCUSD",
                "enableTrade": True,
                "fundingRateIntervalMin": "240",
                "defaultTakerFeeRate": "0.00038",
                "tickSize": "0.1",
                "stepSize": "0.001",
                "minOrderSize": "0.003",
            },
            {
                "contractId": "10000099",
                "contractName": "DEADUSD",
                "enableTrade": False,  # delisted → filtered out
                "fundingRateIntervalMin": "240",
                "defaultTakerFeeRate": "0.00038",
            },
        ]
    },
}

_TICKER_BTC = {
    "code": "SUCCESS",
    "data": [
        {
            "contractId": "10000001",
            "fundingRate": "0.00005000",
            "markPrice": "63541.93",
            "lastPrice": "63506.1",
            "fundingTime": "1781193600000",
            "nextFundingTime": "1781208000000",
        }
    ],
}


def _fresh() -> EdgexFundingProvider:
    p = EdgexFundingProvider()
    p._meta_cache = None
    return p


class TestSymbolMapping:
    def test_base_from_contract_name(self):
        assert _base_from_contract_name("BTCUSD") == "BTC"
        assert _base_from_contract_name("1000PEPEUSD") == "1000PEPE"


class TestMetadata:
    def test_contracts_filtered_and_cast(self):
        with patch.object(ef, "http_get_json", return_value=_META):
            p = _fresh()
            contracts = p._contracts()
        assert set(contracts) == {"BTC"}  # disabled DEADUSD dropped
        btc = contracts["BTC"]
        assert btc["contract_id"] == "10000001"
        assert btc["interval_h"] == 4.0
        assert btc["taker_pct"] == 0.038
        assert btc["step_size"] == 0.001
        assert btc["min_order_size"] == 0.003

    def test_contract_id_lookup(self):
        with patch.object(ef, "http_get_json", return_value=_META):
            p = _fresh()
            assert p.contract_id_for_base("btc") == "10000001"
            assert p.contract_id_for_base("NOPE") is None


class TestFunding:
    def test_fetch_all(self):
        def fake_get(url, **_):
            return _META if "getMetaData" in url else _TICKER_BTC

        with patch.object(ef, "http_get_json", side_effect=fake_get):
            rows = _fresh().fetch_all()
        assert len(rows) == 1
        r = rows[0]
        assert r["symbol"] == "BTCUSDT"
        assert abs(r["rate_pct"] - 0.005) < 1e-9  # 0.00005 * 100
        assert r["next_funding_ts"] == 1781208000000
        assert r["mark_price"] == 63541.93

    def test_fetch_interval_map(self):
        with patch.object(ef, "http_get_json", return_value=_META):
            imap = _fresh().fetch_interval_map()
        assert imap == {"BTCUSDT": 4.0}

    def test_fetch_current(self):
        def fake_get(url, **_):
            return _META if "getMetaData" in url else _TICKER_BTC

        with patch.object(ef, "http_get_json", side_effect=fake_get):
            cur = _fresh().fetch_current("BTCUSDT")
        assert abs(cur["rate_pct"] - 0.005) < 1e-9
        assert cur["interval_ms"] == 4 * 3600 * 1000
        assert cur["next_funding_ts"] == 1781208000000
        assert cur["last_settle_ts"] == 1781193600000  # from ticker fundingTime

    def test_fetch_current_unknown_symbol(self):
        with patch.object(ef, "http_get_json", return_value=_META):
            cur = _fresh().fetch_current("NOPEUSDT")
        assert cur["rate_pct"] == 0.0
        assert cur["next_funding_ts"] == 0

    def test_fetch_since_empty(self):
        assert _fresh().fetch_since("BTCUSDT", 0) == []


class TestRateLimitSafety:
    """EdgeX has no batch ticker + strict Cloudflare limits — the adapter must
    bound its fan-out to a whitelist and cache snapshots."""

    def test_whitelist_bounds_fanout(self, monkeypatch):
        monkeypatch.setenv("EDGEX_SCAN_BASES", "BTC,ETH")
        calls = {"ticker": 0}

        def fake_get(url, **_):
            if "getMetaData" in url:
                return _META
            calls["ticker"] += 1
            return _TICKER_BTC

        with patch.object(ef, "http_get_json", side_effect=fake_get):
            rows = _fresh().fetch_all()
        # Only BTC is in the (mocked) contract table, so ETH is skipped and we
        # never fan out to the full list.
        assert calls["ticker"] == 1
        assert [r["symbol"] for r in rows] == ["BTCUSDT"]

    def test_snapshot_cache_avoids_refetch(self):
        def fake_get(url, **_):
            return _META if "getMetaData" in url else _TICKER_BTC

        with patch.object(ef, "http_get_json", side_effect=fake_get):
            p = _fresh()
            first = p.fetch_all()
            with patch.object(p, "_ticker_row", side_effect=AssertionError("refetched")):
                second = p.fetch_all()  # served from snapshot cache
        assert first == second


class TestRegistration:
    def test_funding_provider_factory(self):
        from backtest.funding_providers import get_funding_provider

        p = get_funding_provider("edgex")
        assert p.venue_id == "edgex"


class TestDepth:
    def test_depth_branch(self):
        import market.futures_depth as fd

        depth_payload = {
            "code": "SUCCESS",
            "data": {
                "asks": [{"price": "63504.4", "size": "0.113"}],
                "bids": [{"price": "63502.2", "size": "1.760"}],
            },
        }

        def fake_get(url, **_):
            return _META if "getMetaData" in url else depth_payload

        with patch.object(ef, "http_get_json", side_effect=fake_get), patch.object(
            fd, "http_get_json", side_effect=fake_get
        ):
            book = fd.fetch_futures_depth("edgex", "BTC")
        assert book["asks"] == [(63504.4, 0.113)]
        assert book["bids"] == [(63502.2, 1.76)]


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
