#!/usr/bin/env python3
"""Tests for the Lighter venue adapter (mocked REST + SDK, no network/signing)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import venues.lighter as lighter_mod
from venues.lighter import LighterVenue, _base_from_pair, _pair_from_base

_ORDER_BOOK_DETAILS = {
    "order_book_details": [
        {
            "symbol": "BTC",
            "market_type": "perp",
            "status": "active",
            "market_id": 1,
            "last_trade_price": "60000.5",
            "supported_size_decimals": 5,
            "supported_price_decimals": 1,
            "min_base_amount": "0.0002",
            "min_quote_amount": "10",
        },
        {
            "symbol": "1000PEPE",
            "market_type": "perp",
            "status": "active",
            "market_id": 42,
            "last_trade_price": "0.012",
            "supported_size_decimals": 0,
            "supported_price_decimals": 6,
            "min_base_amount": "100",
            "min_quote_amount": "10",
        },
    ]
}


def _fresh_venue() -> LighterVenue:
    lighter_mod._price_cache = None
    v = LighterVenue()
    v._funding._meta_cache = None
    return v


class TestSymbolMapping:
    def test_pair_base_roundtrip(self):
        assert _base_from_pair("BTCUSDT") == "BTC"
        assert _base_from_pair("1000PEPEUSDT") == "1000PEPE"
        assert _pair_from_base("btc") == "BTCUSDT"


class TestMarketData:
    def test_ticker(self):
        with patch.object(
            lighter_mod, "http_get_json", return_value=_ORDER_BOOK_DETAILS
        ):
            v = _fresh_venue()
            assert v.get_futures_ticker("BTCUSDT") == 60000.5
            assert v.get_ticker("1000PEPEUSDT") == 0.012
            assert v.get_futures_ticker("NOPEUSDT") == 0.0

    def test_symbol_rules(self):
        import venues.lighter_funding as lf

        with patch.object(lf, "http_get_json", return_value=_ORDER_BOOK_DETAILS):
            v = _fresh_venue()
            rules = v.fetch_futures_symbol_rules("BTCUSDT")
        assert rules == {
            "symbol": "BTCUSDT",
            "quantity_precision": 5,
            "quote_precision": 1,
            "min_trade_base": 0.0002,
            "min_trade_usdt": 10.0,
        }

    def test_market_id_mapping(self):
        import venues.lighter_funding as lf

        with patch.object(lf, "http_get_json", return_value=_ORDER_BOOK_DETAILS):
            v = _fresh_venue()
            assert v._funding.market_id_for_base("1000PEPE") == 42
            assert v._funding.market_id_for_base("NOPE") is None


class TestPositions:
    def test_positions_format(self):
        account = SimpleNamespace(
            available_balance="2500.75",
            positions=[
                SimpleNamespace(
                    symbol="BTC",
                    sign=1,
                    position="0.5",
                    avg_entry_price="60000",
                    unrealized_pnl="12.5",
                    liquidation_price="0",
                ),
                SimpleNamespace(
                    symbol="SOL",
                    sign=-1,
                    position="20",
                    avg_entry_price="150",
                    unrealized_pnl="-3",
                    liquidation_price="290",
                ),
                SimpleNamespace(symbol="ETH", sign=1, position="0"),
            ],
        )
        with (
            patch.object(lighter_mod, "_run_async", return_value=account),
            patch.object(
                LighterVenue,
                "_fetch_account",
                return_value=None,  # avoid creating un-awaited coroutine
            ),
        ):
            v = LighterVenue()
            positions = v.fetch_futures_positions()
            bal = v.fetch_usdt_account_balances()
        assert len(positions) == 2
        btc = next(p for p in positions if p["symbol"] == "BTCUSDT")
        assert btc["side"] == "long" and btc["qty"] == 0.5
        sol = next(p for p in positions if p["symbol"] == "SOLUSDT")
        assert sol["side"] == "short" and sol["liq_price"] == 290.0
        assert bal == {"spot": 0.0, "futures": 2500.75}

    def test_positions_failure_returns_empty(self):
        with (
            patch.object(
                lighter_mod, "_run_async", side_effect=RuntimeError("no creds")
            ),
            patch.object(
                LighterVenue,
                "_fetch_account",
                return_value=None,  # avoid creating un-awaited coroutine
            ),
        ):
            v = LighterVenue()
            assert v.fetch_futures_positions() == []
            assert v.fetch_usdt_account_balances() == {"spot": 0.0, "futures": 0.0}


class TestExecution:
    def test_dry_run_simulated(self):
        v = LighterVenue()
        trades = [{"symbol": "BTC", "type": "open_long", "amount_base": 0.01}]
        market = {"BTC": {"price": 60000.0}}
        results = v.execute_trades(trades, market, dry_run=True)
        assert results[0]["status"] == "simulated"
        assert results[0]["venue"] == "lighter"
        assert results[0]["exec_price"] == 60000.0

    def test_direction_mapping_and_scaling(self):
        """open_long/close_short → bid; open_short/close_long → ask; ints scaled by decimals."""
        import venues.lighter_funding as lf

        expected = {
            "open_long": (False, False),
            "close_short": (False, True),
            "open_short": (True, False),
            "close_long": (True, True),
        }
        with patch.object(lf, "http_get_json", return_value=_ORDER_BOOK_DETAILS):
            for typ, (is_ask, reduce_only) in expected.items():
                v = _fresh_venue()
                captured: dict = {}

                async def fake_submit(market_id, base_scaled, price_scaled, ask, ro):
                    captured.update(
                        market_id=market_id,
                        base_scaled=base_scaled,
                        price_scaled=price_scaled,
                        is_ask=ask,
                        reduce_only=ro,
                    )
                    return None, SimpleNamespace(tx_hash="0xabc"), None

                v._submit_market_order = fake_submit  # type: ignore[method-assign]
                trades = [{"symbol": "BTC", "type": typ, "amount_base": 0.01}]
                market = {"BTC": {"price": 60000.0}}
                results = v.execute_trades(trades, market, dry_run=False)
                assert results[0]["status"] == "filled", (typ, results[0].get("error"))
                assert results[0]["order_id"] == "0xabc"
                assert captured["market_id"] == 1
                assert captured["is_ask"] is is_ask, typ
                assert captured["reduce_only"] is reduce_only, typ
                # 0.01 BTC at size_decimals=5 → 1000
                assert captured["base_scaled"] == 1000
                # price bound: ±2% of ref at price_decimals=1
                bound = 60000.0 * (0.98 if is_ask else 1.02)
                assert captured["price_scaled"] == int(round(bound * 10))

    def test_sdk_error_marks_failed(self):
        import venues.lighter_funding as lf

        with patch.object(lf, "http_get_json", return_value=_ORDER_BOOK_DETAILS):
            v = _fresh_venue()

            async def fake_submit(*args):
                return None, None, "nonce error"

            v._submit_market_order = fake_submit  # type: ignore[method-assign]
            results = v.execute_trades(
                [{"symbol": "BTC", "type": "open_long", "amount_base": 0.01}],
                {"BTC": {"price": 60000.0}},
                dry_run=False,
            )
        assert results[0]["status"] == "failed"
        assert "nonce error" in results[0]["error"]

    def test_unknown_market_fails(self):
        import venues.lighter_funding as lf

        with patch.object(lf, "http_get_json", return_value=_ORDER_BOOK_DETAILS):
            v = _fresh_venue()
            results = v.execute_trades(
                [{"symbol": "NOPE", "type": "open_long", "amount_base": 1}],
                {"NOPE": {"price": 1.0}},
                dry_run=False,
            )
        assert results[0]["status"] == "failed"
        assert "market not found" in results[0]["error"]


class TestRegistration:
    def test_get_venue(self):
        from venues import get_venue, supported_venues

        assert "lighter" in supported_venues()
        v = get_venue({"venue": {"type": "lighter"}})
        assert v.venue_id == "lighter"


class TestDepth:
    def test_depth_branch(self):
        import market.futures_depth as fd
        import venues.lighter_funding as lf

        book_payload = {
            "bids": [
                {"price": "59999.0", "remaining_base_amount": "0.5"},
            ],
            "asks": [
                {"price": "60001.0", "remaining_base_amount": "0.7"},
            ],
        }

        with (
            patch.object(lf, "http_get_json", return_value=_ORDER_BOOK_DETAILS),
            patch.object(fd, "http_get_json", return_value=book_payload),
        ):
            book = fd.fetch_futures_depth("lighter", "BTC")
        assert book["bids"] == [(59999.0, 0.5)]
        assert book["asks"] == [(60001.0, 0.7)]


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
