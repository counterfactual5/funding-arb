#!/usr/bin/env python3
"""Tests for Hyperliquid integration: funding provider, venue adapter, executor."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.gettempdir()) / "funding-arb-test-hyperliquid"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _path(name: str) -> Path:
    TMP.mkdir(parents=True, exist_ok=True)
    p = TMP / f"{name}.json"
    if p.exists():
        p.unlink()
    return p


# ---------------------------------------------------------------------------
# FakeHyperliquidVenue — mirrors FakeFuturesVenue pattern
# ---------------------------------------------------------------------------

class FakeHyperliquidVenue:
    """Test double for HyperliquidVenue."""

    def __init__(
        self,
        venue_id: str = "hyperliquid",
        price: float = 100.0,
        fail_types: set[str] | None = None,
        balances: dict[str, float] | None = None,
    ):
        self.venue_id = venue_id
        self.price = price
        self.fail_types = fail_types or set()
        self.trades: list[dict] = []
        self.initialized: list[str] = []

    def fetch_futures_symbol_rules(self, pair: str, cache_sec: int = 3600):
        return {
            "symbol": pair,
            "quantity_precision": 5,
            "quote_precision": 2,
            "min_trade_usdt": 10.0,
            "min_trade_base": 0.0,
        }

    def fetch_symbol_rules(self, pair: str, cache_sec: int = 3600):
        return self.fetch_futures_symbol_rules(pair, cache_sec)

    def get_ticker(self, pair: str):
        return self.price

    def get_futures_ticker(self, pair: str):
        return self.price

    def initialize_futures_symbol(self, pair: str):
        self.initialized.append(pair)

    def fetch_usdt_account_balances(self):
        return {"spot": 0.0, "futures": 100000.0}

    def fetch_futures_positions(self, quote: str = "USDT"):
        return []

    def execute_trades(self, trades, market, dry_run=True):
        out = []
        for t in trades:
            self.trades.append(dict(t, dry_run=dry_run))
            typ = t["type"]
            if typ in self.fail_types and not dry_run:
                out.append({
                    "symbol": t["symbol"],
                    "type": typ,
                    "status": "failed",
                    "error": f"fail {typ}",
                })
            else:
                out.append({
                    "symbol": t["symbol"],
                    "type": typ,
                    "status": "simulated" if dry_run else "filled",
                    "exec_qty": t["amount_base"],
                    "exec_price": self.price,
                })
        return out


class FakeCexVenue:
    """Standard CEX fake venue for cross-venue tests."""

    def __init__(
        self,
        venue_id: str = "binance",
        price: float = 100.0,
        fail_types: set[str] | None = None,
    ):
        self.venue_id = venue_id
        self.price = price
        self.fail_types = fail_types or set()
        self.trades: list[dict] = []
        self.initialized: list[str] = []

    def fetch_futures_symbol_rules(self, pair: str, cache_sec: int = 3600):
        return {
            "symbol": pair,
            "quantity_precision": 4,
            "quote_precision": 2,
            "min_trade_usdt": 5.0,
            "min_trade_base": 0.0,
        }

    def fetch_symbol_rules(self, pair: str, cache_sec: int = 3600):
        return self.fetch_futures_symbol_rules(pair, cache_sec)

    def get_ticker(self, pair: str):
        return self.price

    def initialize_futures_symbol(self, pair: str):
        self.initialized.append(pair)

    def fetch_usdt_account_balances(self):
        return {"spot": 100000.0, "futures": 100000.0}

    def fetch_futures_positions(self, quote: str = "USDT"):
        return []

    def execute_trades(self, trades, market, dry_run=True):
        out = []
        for t in trades:
            self.trades.append(dict(t, dry_run=dry_run))
            typ = t["type"]
            if typ in self.fail_types and not dry_run:
                out.append({
                    "symbol": t["symbol"],
                    "type": typ,
                    "status": "failed",
                    "error": f"fail {typ}",
                })
            else:
                out.append({
                    "symbol": t["symbol"],
                    "type": typ,
                    "status": "simulated" if dry_run else "filled",
                    "exec_qty": t["amount_base"],
                    "exec_price": self.price,
                })
        return out


# ===========================================================================
# FundingProvider tests
# ===========================================================================

class TestHyperliquidFundingProvider:
    """Test HyperliquidFundingProvider with mocked HTTP."""

    def _mock_meta_response(self):
        return [
            {
                "universe": [
                    {"name": "BTC", "szDecimals": 5},
                    {"name": "ETH", "szDecimals": 4},
                    {"name": "SOL", "szDecimals": 2},
                ],
            },
            [
                {"funding": "0.0001", "markPx": "60000.0"},
                {"funding": "-0.00005", "markPx": "3000.0"},
                {"funding": "0.0002", "markPx": "150.0"},
            ],
        ]

    @patch("venues.hyperliquid_funding._post")
    def test_fetch_all_returns_usdt_symbols(self, mock_post):
        mock_post.return_value = self._mock_meta_response()
        from venues.hyperliquid_funding import HyperliquidFundingProvider

        fp = HyperliquidFundingProvider()
        result = fp.fetch_all()

        assert len(result) == 3
        symbols = {r["symbol"] for r in result}
        assert symbols == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}

    @patch("venues.hyperliquid_funding._post")
    def test_fetch_all_rate_conversion(self, mock_post):
        mock_post.return_value = self._mock_meta_response()
        from venues.hyperliquid_funding import HyperliquidFundingProvider

        fp = HyperliquidFundingProvider()
        result = fp.fetch_all()

        btc = next(r for r in result if r["symbol"] == "BTCUSDT")
        assert abs(btc["rate_pct"] - 0.01) < 0.001  # 0.0001 * 100 = 0.01%

        eth = next(r for r in result if r["symbol"] == "ETHUSDT")
        assert eth["rate_pct"] < 0  # negative funding

    @patch("venues.hyperliquid_funding._post")
    def test_fetch_all_has_next_funding_ts(self, mock_post):
        mock_post.return_value = self._mock_meta_response()
        from venues.hyperliquid_funding import HyperliquidFundingProvider

        fp = HyperliquidFundingProvider()
        result = fp.fetch_all()

        for r in result:
            assert r["next_funding_ts"] > 0
            assert r["mark_price"] > 0

    @patch("venues.hyperliquid_funding._post")
    def test_fetch_current_single_coin(self, mock_post):
        mock_post.return_value = self._mock_meta_response()
        from venues.hyperliquid_funding import HyperliquidFundingProvider

        fp = HyperliquidFundingProvider()
        result = fp.fetch_current("BTCUSDT")

        assert result["rate_pct"] > 0
        assert result["interval_ms"] == 3600000  # 1 hour
        assert result["next_funding_ts"] > 0
        assert result["last_settle_ts"] > 0

    @patch("venues.hyperliquid_funding._post")
    def test_fetch_interval_map_1h(self, mock_post):
        mock_post.return_value = self._mock_meta_response()
        from venues.hyperliquid_funding import HyperliquidFundingProvider

        fp = HyperliquidFundingProvider()
        imap = fp.fetch_interval_map()

        assert imap["BTCUSDT"] == 1.0
        assert imap["ETHUSDT"] == 1.0
        assert len(imap) == 3

    @patch("venues.hyperliquid_funding._post")
    def test_fetch_since_history(self, mock_post):
        mock_post.return_value = [
            {"coin": "BTC", "fundingRate": "0.0001", "time": 1749600000023},
            {"coin": "BTC", "fundingRate": "0.0002", "time": 1749603600031},
            {"coin": "BTC", "fundingRate": "0.0003", "time": 1749607200000},
        ]
        from venues.hyperliquid_funding import HyperliquidFundingProvider

        fp = HyperliquidFundingProvider()
        result = fp.fetch_since("BTCUSDT", 1749600000000)

        # All 3 have ts > start_ms (1749600000023 > 1749600000000)
        assert len(result) == 3
        assert result[0]["ts"] == 1749600000023
        assert abs(result[0]["rate_pct"] - 0.01) < 0.001


# ===========================================================================
# Venue adapter tests
# ===========================================================================

class TestHyperliquidVenue:
    """Test HyperliquidVenue with mocked HTTP and skill functions."""

    @patch("venues.hyperliquid._info_post")
    def test_get_futures_ticker(self, mock_post):
        mock_post.return_value = {"BTC": "60000.5", "ETH": "3000.0"}
        from venues.hyperliquid import HyperliquidVenue

        v = HyperliquidVenue()
        assert v.get_futures_ticker("BTCUSDT") == 60000.5
        assert v.get_futures_ticker("ETHUSDT") == 3000.0

    @patch("venues.hyperliquid._info_post")
    def test_get_ticker_same_as_futures(self, mock_post):
        mock_post.return_value = {"BTC": "60000.0"}
        from venues.hyperliquid import HyperliquidVenue

        v = HyperliquidVenue()
        assert v.get_ticker("BTCUSDT") == 60000.0

    @patch("venues.hyperliquid._info_post")
    def test_fetch_futures_symbol_rules(self, mock_post):
        mock_post.return_value = [
            {"universe": [
                {"name": "BTC", "szDecimals": 5},
                {"name": "ETH", "szDecimals": 4},
            ]},
            [],
        ]
        from venues.hyperliquid import HyperliquidVenue

        v = HyperliquidVenue()
        rules = v.fetch_futures_symbol_rules("BTCUSDT")
        assert rules["quantity_precision"] == 5
        assert rules["symbol"] == "BTCUSDT"
        assert rules["min_trade_usdt"] == 10.0

        # Cached
        rules2 = v.fetch_futures_symbol_rules("BTCUSDT")
        assert rules2 is rules  # same object

    def test_execute_trades_dry_run(self):
        from venues.hyperliquid import HyperliquidVenue

        v = HyperliquidVenue()
        trades = [{"symbol": "BTC", "type": "open_long", "amount_base": 0.001}]
        market = {"BTC": {"price": 60000.0}}
        results = v.execute_trades(trades, market, dry_run=True)

        assert len(results) == 1
        assert results[0]["status"] == "simulated"
        assert results[0]["exec_qty"] == 0.001
        assert results[0]["exec_price"] == 60000.0
        assert results[0]["venue"] == "hyperliquid"

    def test_execute_trades_direction_mapping(self):
        """Verify all 4 trade types produce correct simulated results."""
        from venues.hyperliquid import HyperliquidVenue

        v = HyperliquidVenue()
        for typ in ("open_long", "open_short", "close_long", "close_short"):
            trades = [{"symbol": "BTC", "type": typ, "amount_base": 0.01}]
            market = {"BTC": {"price": 60000.0}}
            results = v.execute_trades(trades, market, dry_run=True)
            assert results[0]["status"] == "simulated", f"{typ} should simulate"

    @patch("venues.hyperliquid._make_info_client")
    def test_fetch_futures_positions_format(self, mock_info):
        # Mock the SDK Info client's user_state response
        mock_client = MagicMock()
        mock_client.user_state.return_value = {
            "assetPositions": [
                {
                    "position": {
                        "coin": "BTC",
                        "szi": "0.5",
                        "entryPx": "60000.0",
                        "unrealizedPnl": "100.0",
                        "liquidationPx": "0.0",
                    },
                    "leverage": {"value": "1"},
                },
                {
                    "position": {
                        "coin": "ETH",
                        "szi": "-2.0",
                        "entryPx": "3000.0",
                        "unrealizedPnl": "-50.0",
                        "liquidationPx": "5000.0",
                    },
                    "leverage": {"value": "1"},
                },
            ]
        }
        mock_info.return_value = mock_client
        # Mock wallet address
        with patch("venues.hyperliquid._get_wallet_address", return_value="0xabc"):
            from venues.hyperliquid import HyperliquidVenue

            v = HyperliquidVenue()
            positions = v.fetch_futures_positions()

        assert len(positions) == 2
        btc = next(p for p in positions if p["symbol"] == "BTCUSDT")
        assert btc["side"] == "long"
        assert btc["qty"] == 0.5
        assert btc["entry_price"] == 60000.0

        eth = next(p for p in positions if p["symbol"] == "ETHUSDT")
        assert eth["side"] == "short"
        assert eth["qty"] == 2.0
        assert eth["liq_price"] == 5000.0

    @patch("venues.hyperliquid._make_info_client")
    def test_fetch_usdt_account_balances(self, mock_info):
        mock_client = MagicMock()
        mock_client.user_state.return_value = {
            "marginSummary": {
                "accountValue": "5000.0",
                "totalMarginUsed": "1000.0",
            }
        }
        mock_info.return_value = mock_client
        with patch("venues.hyperliquid._get_wallet_address", return_value="0xabc"):
            from venues.hyperliquid import HyperliquidVenue

            v = HyperliquidVenue()
            bal = v.fetch_usdt_account_balances()
        assert bal["spot"] == 0.0
        assert bal["futures"] == 5000.0

    def test_venue_id(self):
        from venues.hyperliquid import HyperliquidVenue

        v = HyperliquidVenue()
        assert v.venue_id == "hyperliquid"


# ===========================================================================
# Executor integration tests
# ===========================================================================

class TestExecutorWithHyperliquid:
    """Integration: open/close pure futures pair with HL as one leg."""

    def test_dry_run_open_binance_vs_hyperliquid(self):
        from execution.pure_futures_executor import open_pure_futures_pair

        path = _path("hl_open_dry")
        lv = FakeCexVenue("binance", price=60000.0)
        sv = FakeHyperliquidVenue("hyperliquid", price=60100.0)

        result = open_pure_futures_pair(
            "BTC",
            "binance",
            "hyperliquid",
            500.0,
            dry_run=True,
            long_venue=lv,
            short_venue=sv,
            positions_path=path,
        )

        assert result.ok
        assert result.state == "simulated"
        assert "binance" in result.position_id
        assert "hyperliquid" in result.position_id
        assert len(result.executed) == 2

    def test_dry_run_open_hyperliquid_vs_binance(self):
        """HL as long leg, CEX as short leg."""
        from execution.pure_futures_executor import open_pure_futures_pair

        path = _path("hl_reverse_dry")
        lv = FakeHyperliquidVenue("hyperliquid", price=60000.0)
        sv = FakeCexVenue("binance", price=60100.0)

        result = open_pure_futures_pair(
            "BTC",
            "hyperliquid",
            "binance",
            500.0,
            dry_run=True,
            long_venue=lv,
            short_venue=sv,
            positions_path=path,
        )

        assert result.ok
        assert result.state == "simulated"
        assert len(result.executed) == 2

    def test_dry_run_open_and_close_cycle(self):
        from execution.pure_futures_executor import (
            close_pure_futures_pair,
            open_pure_futures_pair,
        )

        path = _path("hl_cycle_dry")
        lv = FakeCexVenue("binance", price=60000.0)
        sv = FakeHyperliquidVenue("hyperliquid", price=60100.0)

        open_result = open_pure_futures_pair(
            "BTC",
            "binance",
            "hyperliquid",
            500.0,
            dry_run=True,
            long_venue=lv,
            short_venue=sv,
            positions_path=path,
        )
        assert open_result.ok
        pid = open_result.position_id

        close_result = close_pure_futures_pair(
            pid,
            long_venue=lv,
            short_venue=sv,
            positions_path=path,
        )
        assert close_result.ok
        assert close_result.state == "simulated"

    def test_live_open_short_leg_failure_rollback(self):
        """If short leg (HL) fails, long leg (CEX) should be rolled back."""
        from execution.pure_futures_executor import open_pure_futures_pair

        path = _path("hl_rollback")
        lv = FakeCexVenue("binance", price=60000.0)
        sv = FakeHyperliquidVenue("hyperliquid", price=60100.0, fail_types={"open_short"})

        result = open_pure_futures_pair(
            "BTC",
            "binance",
            "hyperliquid",
            500.0,
            dry_run=False,
            long_venue=lv,
            short_venue=sv,
            positions_path=path,
        )

        assert not result.ok
        assert result.state == "rolled_back"

    def test_live_open_long_leg_failure_aborts(self):
        """If long leg (HL) fails, no short leg should be attempted."""
        from execution.pure_futures_executor import open_pure_futures_pair

        path = _path("hl_long_fail")
        lv = FakeHyperliquidVenue("hyperliquid", price=60000.0, fail_types={"open_long"})
        sv = FakeCexVenue("binance", price=60100.0)

        result = open_pure_futures_pair(
            "BTC",
            "hyperliquid",
            "binance",
            500.0,
            dry_run=False,
            long_venue=lv,
            short_venue=sv,
            positions_path=path,
            config={"parallelLegs": False},
        )

        assert not result.ok
        assert result.state == "aborted"
        # Short venue should not have received any trades (sequential mode)
        assert len(sv.trades) == 0


# ===========================================================================
# Registration tests
# ===========================================================================

class TestRegistration:
    """Verify Hyperliquid is registered in all the right places."""

    def test_venue_registry(self):
        from venues import _REGISTRY, get_venue

        v = get_venue({"venue": {"type": "hyperliquid"}})
        assert v.venue_id == "hyperliquid"
        assert "hyperliquid" in _REGISTRY

    def test_fee_defaults(self):
        from core.fee_providers import DEFAULT_TAKER_PCT, DEFAULT_MAKER_PCT

        assert "hyperliquid" in DEFAULT_TAKER_PCT
        assert "hyperliquid" in DEFAULT_MAKER_PCT
        # Live base cross rate (userFees userCrossRate=0.00045); fetched live
        # when available, this is the offline fallback.
        assert DEFAULT_TAKER_PCT["hyperliquid"] == 0.045
        assert DEFAULT_MAKER_PCT["hyperliquid"] == 0.01

    def test_funding_provider_registry(self):
        from backtest.funding_providers import _PROVIDERS

        assert "hyperliquid" in _PROVIDERS

    def test_funding_provider_get(self):
        from backtest.funding_providers import get_funding_provider

        fp = get_funding_provider("hyperliquid")
        assert fp.venue_id == "hyperliquid"

    def test_taker_fee_default_value(self):
        from core.fee_providers import default_taker_pct

        assert default_taker_pct("hyperliquid") == 0.045


# ===========================================================================
# Run
# ===========================================================================

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
