#!/usr/bin/env python3
"""Tests for the Aster venue adapter (mocked HTTP, no network)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import venues.aster as aster_mod
from venues.aster import AsterVenue

_EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "status": "TRADING",
            "quantityPrecision": 3,
            "pricePrecision": 1,
            "filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.001"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        }
    ]
}


def _fresh_venue() -> AsterVenue:
    aster_mod._rules_cache = {}
    aster_mod._rules_loaded_at = 0.0
    return AsterVenue()


class TestMarketData:
    def test_ticker(self):
        with patch.object(
            aster_mod, "http_get_json", return_value={"price": "60123.4"}
        ):
            v = _fresh_venue()
            assert v.get_futures_ticker("BTCUSDT") == 60123.4
            assert v.get_ticker("BTCUSDT") == 60123.4

    def test_ticker_failure_returns_zero(self):
        with patch.object(
            aster_mod, "http_get_json", side_effect=RuntimeError("down")
        ):
            v = _fresh_venue()
            assert v.get_futures_ticker("BTCUSDT") == 0.0

    def test_symbol_rules(self):
        with patch.object(aster_mod, "http_get_json", return_value=_EXCHANGE_INFO):
            v = _fresh_venue()
            rules = v.fetch_futures_symbol_rules("BTCUSDT")
        assert rules is not None
        assert rules["quantity_precision"] == 3
        assert rules["quote_precision"] == 1
        assert rules["min_trade_base"] == 0.001
        assert rules["min_trade_usdt"] == 5.0

    def test_symbol_rules_unknown_pair(self):
        with patch.object(aster_mod, "http_get_json", return_value=_EXCHANGE_INFO):
            v = _fresh_venue()
            assert v.fetch_futures_symbol_rules("NOPEUSDT") is None


class TestAccount:
    def test_balances(self):
        with patch.object(
            aster_mod,
            "_api_call",
            return_value=[
                {"asset": "USDT", "availableBalance": "1234.5"},
                {"asset": "BTC", "availableBalance": "0.5"},
            ],
        ):
            bal = AsterVenue().fetch_usdt_account_balances()
        assert bal == {"spot": 0.0, "futures": 1234.5}

    def test_positions(self):
        with patch.object(
            aster_mod,
            "_api_call",
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.5",
                    "entryPrice": "60000",
                    "liquidationPrice": "0",
                    "leverage": "1",
                    "unRealizedProfit": "10.0",
                },
                {"symbol": "ETHUSDT", "positionAmt": "0"},
                {
                    "symbol": "SOLUSDT",
                    "positionAmt": "-20",
                    "entryPrice": "150",
                    "liquidationPrice": "300",
                    "leverage": "1",
                    "unRealizedProfit": "-5.0",
                },
            ],
        ):
            positions = AsterVenue().fetch_futures_positions()
        assert len(positions) == 2
        btc = next(p for p in positions if p["symbol"] == "BTCUSDT")
        assert btc["side"] == "long" and btc["qty"] == 0.5
        sol = next(p for p in positions if p["symbol"] == "SOLUSDT")
        assert sol["side"] == "short" and sol["qty"] == 20.0


class TestExecution:
    def test_dry_run_simulated(self):
        v = AsterVenue()
        trades = [{"symbol": "BTC", "type": "open_long", "amount_base": 0.01}]
        market = {"BTC": {"price": 60000.0, "pair": "BTCUSDT"}}
        results = v.execute_trades(trades, market, dry_run=True)
        assert results[0]["status"] == "simulated"
        assert results[0]["venue"] == "aster"
        assert results[0]["exec_price"] == 60000.0

    def test_direction_mapping(self):
        """open_long/close_short → BUY; open_short/close_long → SELL; reduce_only on closes."""
        expected = {
            "open_long": ("BUY", False),
            "close_short": ("BUY", True),
            "open_short": ("SELL", False),
            "close_long": ("SELL", True),
        }
        with patch.object(aster_mod, "http_get_json", return_value=_EXCHANGE_INFO):
            for typ, (side, reduce_only) in expected.items():
                v = _fresh_venue()
                calls: list[tuple] = []

                def fake_order(pair, s, qty, prec, ref_price=0.0, reduce_only=False):
                    calls.append((pair, s, reduce_only))
                    return True, {"order_id": "1", "exec_price": 60000.0, "exec_qty": qty}

                v.place_futures_order = fake_order  # type: ignore[method-assign]
                trades = [{"symbol": "BTC", "type": typ, "amount_base": 0.01}]
                market = {"BTC": {"price": 60000.0, "pair": "BTCUSDT"}}
                results = v.execute_trades(trades, market, dry_run=False)
                assert results[0]["status"] == "filled", typ
                assert calls == [("BTCUSDT", side, reduce_only)], typ

    def test_unknown_trade_type(self):
        v = AsterVenue()
        results = v.execute_trades(
            [{"symbol": "BTC", "type": "hodl", "amount_base": 1}],
            {"BTC": {"price": 1.0}},
            dry_run=False,
        )
        assert results[0]["status"] == "failed"
        assert "Unknown trade type" in results[0]["error"]

    def test_live_requires_credentials(self):
        """Signed calls must fail loudly when ASTER keys are absent."""
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("ASTER_API_KEY", None)
            os.environ.pop("ASTER_API_SECRET", None)
            try:
                aster_mod._api_call("POST", "/fapi/v1/order", {}, signed=True)
                raise AssertionError("expected RuntimeError")
            except RuntimeError as e:
                assert "credentials missing" in str(e)


class TestRegistration:
    def test_get_venue(self):
        from venues import get_venue, supported_venues

        assert "aster" in supported_venues()
        v = get_venue({"venue": {"type": "aster"}})
        assert v.venue_id == "aster"


class TestDepth:
    def test_depth_branch(self):
        import market.futures_depth as fd

        with patch.object(
            fd,
            "http_get_json",
            return_value={
                "bids": [["60000", "1.5"]],
                "asks": [["60001", "2.0"]],
            },
        ):
            book = fd.fetch_futures_depth("aster", "BTC")
        assert book["bids"] == [(60000.0, 1.5)]
        assert book["asks"] == [(60001.0, 2.0)]


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
