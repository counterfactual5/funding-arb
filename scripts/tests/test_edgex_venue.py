#!/usr/bin/env python3
"""Tests for the EdgeX venue adapter (mocked REST + stubbed V2 SDK, no network)."""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import venues.edgex as edgex_mod
import venues.edgex_funding as ef
from venues.edgex import (
    EdgexVenue,
    _base_from_pair,
    _decimals_from_step,
    _pair_from_base,
)

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
            }
        ]
    },
}


def _stub_sdk() -> None:
    """Inject a fake `edgex_sdk` so live-order code paths can be exercised."""

    class OrderSide(Enum):
        BUY = "BUY"
        SELL = "SELL"

    class Client:  # pragma: no cover - constructed only in stubbed live tests
        def __init__(self, **kw):
            self.kw = kw

    sys.modules["edgex_sdk"] = SimpleNamespace(Client=Client, OrderSide=OrderSide)


def _fresh() -> EdgexVenue:
    v = EdgexVenue()
    v._funding._meta_cache = None
    v._funding._snapshot_cache = {}
    return v


class TestHelpers:
    def test_symbol_mapping(self):
        assert _base_from_pair("BTCUSDT") == "BTC"
        assert _base_from_pair("1000PEPEUSDT") == "1000PEPE"
        assert _pair_from_base("btc") == "BTCUSDT"

    def test_decimals_from_step(self):
        assert _decimals_from_step(0.001) == 3
        assert _decimals_from_step(0.1) == 1
        assert _decimals_from_step(1.0) == 0


class TestMarketData:
    def test_symbol_rules(self):
        with patch.object(ef, "http_get_json", return_value=_META):
            rules = _fresh().fetch_futures_symbol_rules("BTCUSDT")
        assert rules == {
            "symbol": "BTCUSDT",
            "quantity_precision": 3,
            "quote_precision": 1,
            "min_trade_base": 0.003,
            "min_trade_usdt": 0.0,
        }

    def test_ticker(self):
        ticker = {
            "code": "SUCCESS",
            "data": [
                {
                    "contractId": "10000001",
                    "fundingRate": "0.00005",
                    "markPrice": "63541.93",
                    "nextFundingTime": "1781208000000",
                    "fundingTime": "1781193600000",
                }
            ],
        }

        def fake_get(url, **_):
            return _META if "getMetaData" in url else ticker

        with patch.object(ef, "http_get_json", side_effect=fake_get):
            assert _fresh().get_futures_ticker("BTCUSDT") == 63541.93


class TestPositions:
    def test_positions_parsing(self):
        resp = {
            "data": {
                "positionList": [
                    {
                        "contractId": "10000001",
                        "openSize": "0.5",
                        "openValue": "30000",
                        "liquidatePrice": "50000",
                        "leverage": "10",
                        "unrealizePnl": "12.5",
                    },
                    {
                        "contractId": "10000001",
                        "openSize": "-1",
                        "openValue": "60000",
                        "liquidatePrice": "90000",
                        "leverage": "5",
                        "unrealizePnl": "-3",
                    },
                    {"contractId": "10000001", "openSize": "0"},  # flat → dropped
                ]
            }
        }
        with (
            patch.object(ef, "http_get_json", return_value=_META),
            patch.object(edgex_mod, "_run_async", return_value=resp),
            patch.object(
                EdgexVenue,
                "_get_positions",
                # MagicMock (not the auto-detected AsyncMock): plain callable so
                # _get_positions() returns None directly without creating a coroutine
                # that _run_async (also mocked) would never await.
                new_callable=MagicMock,
                return_value=None,
            ),
        ):
            positions = _fresh().fetch_futures_positions()
        assert len(positions) == 2
        long = next(p for p in positions if p["side"] == "long")
        assert long["symbol"] == "BTCUSDT" and long["qty"] == 0.5
        assert long["entry_price"] == 60000.0  # openValue / size
        short = next(p for p in positions if p["side"] == "short")
        assert short["qty"] == 1.0 and short["liq_price"] == 90000.0

    def test_balance_parsing(self):
        resp = {"data": {"collateralList": [{"availableAmount": "2500.75"}]}}
        with (
            patch.object(edgex_mod, "_run_async", return_value=resp),
            patch.object(
                EdgexVenue,
                "_get_asset",
                new_callable=MagicMock,  # plain callable, no coroutine created
                return_value=None,
            ),
        ):
            assert _fresh().fetch_usdt_account_balances() == {
                "spot": 0.0,
                "futures": 2500.75,
            }

    def test_positions_failure_returns_empty(self):
        with (
            patch.object(edgex_mod, "_run_async", side_effect=RuntimeError("no creds")),
            patch.object(
                EdgexVenue, "_get_positions", new_callable=MagicMock, return_value=None
            ),
            patch.object(
                EdgexVenue, "_get_asset", new_callable=MagicMock, return_value=None
            ),
        ):
            v = _fresh()
            assert v.fetch_futures_positions() == []
            assert v.fetch_usdt_account_balances() == {"spot": 0.0, "futures": 0.0}


class TestExecution:
    def test_dry_run_simulated(self):
        v = EdgexVenue()
        trades = [{"symbol": "BTCUSDT", "type": "open_long", "amount_base": 0.01}]
        market = {"BTCUSDT": {"price": 63500.0}}
        results = v.execute_trades(trades, market, dry_run=True)
        assert results[0]["status"] == "simulated"
        assert results[0]["venue"] == "edgex"
        assert results[0]["exec_price"] == 63500.0

    def test_unknown_type_fails(self):
        v = EdgexVenue()
        results = v.execute_trades(
            [{"symbol": "BTCUSDT", "type": "rebalance", "amount_base": 1}],
            {"BTCUSDT": {"price": 1.0}},
            dry_run=False,
        )
        assert results[0]["status"] == "failed"
        assert "Unknown trade type" in results[0]["error"]

    def test_live_side_and_formatting(self):
        """open_long → BUY @ +2%; open_short → SELL @ −2%; size/price string-formatted."""
        _stub_sdk()
        expected = {
            "open_long": ("BUY", 63500.0 * 1.02),
            "open_short": ("SELL", 63500.0 * 0.98),
            "close_long": ("SELL", 63500.0 * 0.98),
            "close_short": ("BUY", 63500.0 * 1.02),
        }
        for typ, (side_name, bound) in expected.items():
            with patch.object(ef, "http_get_json", return_value=_META):
                v = _fresh()
                captured: dict = {}

                async def fake_submit(contract_id, size, price, side):
                    captured.update(
                        contract_id=contract_id, size=size, price=price, side=side
                    )
                    return {"data": {"orderId": "ord-123"}}

                v._submit_limit_order = fake_submit  # type: ignore[method-assign]
                results = v.execute_trades(
                    [{"symbol": "BTCUSDT", "type": typ, "amount_base": 0.01}],
                    {"BTCUSDT": {"price": 63500.0}},
                    dry_run=False,
                )
            assert results[0]["status"] == "filled", (typ, results[0].get("error"))
            assert results[0]["order_id"] == "ord-123"
            assert captured["contract_id"] == "10000001"
            assert captured["side"].value == side_name, typ
            assert captured["size"] == "0.010"  # step 0.001 → 3dp
            assert captured["price"] == f"{bound:.1f}"  # tick 0.1 → 1dp

    def test_live_sdk_error_marks_failed(self):
        _stub_sdk()
        with patch.object(ef, "http_get_json", return_value=_META):
            v = _fresh()

            async def fake_submit(*a, **k):
                raise RuntimeError("signature rejected")

            v._submit_limit_order = fake_submit  # type: ignore[method-assign]
            results = v.execute_trades(
                [{"symbol": "BTCUSDT", "type": "open_long", "amount_base": 0.01}],
                {"BTCUSDT": {"price": 63500.0}},
                dry_run=False,
            )
        assert results[0]["status"] == "failed"
        assert "signature rejected" in results[0]["error"]


class TestRegistration:
    def test_get_venue(self):
        from venues import get_venue, supported_venues

        assert "edgex" in supported_venues()
        v = get_venue({"venue": {"type": "edgex"}})
        assert v.venue_id == "edgex"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
