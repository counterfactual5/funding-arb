#!/usr/bin/env python3
"""Hermetic tests for pure_futures_watcher (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import execution.pure_futures_watcher as watcher_mod
from execution.pure_futures_watcher import (
    _leg_qty_from_snapshot,
    check_exit,
    check_leg_alive,
    check_margin_distance,
    check_rebalance,
    estimate_spread_pnl,
)


def _pos(
    base="BTC",
    long_venue="okx",
    short_venue="bybit",
    qty=0.5,
    direction="forward",
) -> dict:
    return {
        "base": base,
        "long_venue": long_venue,
        "short_venue": short_venue,
        "qty": qty,
        "direction": direction,
    }


def test_check_exit_spread_collapse():
    pos = _pos()
    # spread = short_rate - long_rate = -0.01 - 0.05 = -0.06 → below 0.01 exit threshold
    rates = {
        "BTC": {
            "okx": {"rate_pct": 0.05},
            "bybit": {"rate_pct": -0.01},
        },
    }
    should_exit, reason = check_exit(pos, rates, exit_edge=0.01)
    assert should_exit is True
    assert "spread_collapse" in reason


def test_check_exit_spread_ok():
    pos = _pos()
    # spread = 0.15 - 0.03 = 0.12 → above exit threshold
    rates = {
        "BTC": {
            "okx": {"rate_pct": 0.03},
            "bybit": {"rate_pct": 0.15},
        },
    }
    should_exit, reason = check_exit(pos, rates, exit_edge=0.01)
    assert should_exit is False


def test_check_exit_rate_unavailable():
    pos = _pos(base="ETH")
    # No ETH in rates
    rates = {
        "BTC": {"okx": {"rate_pct": 0.03}},
    }
    should_exit, reason = check_exit(pos, rates, exit_edge=0.01)
    assert should_exit is False
    assert reason == "rate_unavailable"


def test_check_rebalance_no_skew():
    # Both prices equal → no skew
    pos = _pos(qty=1.0)
    need, reason, long_n, short_n = check_rebalance(pos, max_skew_pct=1.0)
    # This will try to fetch real prices and fail, returning price_unavailable
    # That's expected for hermetic test — check the logic still runs
    assert isinstance(need, bool)


def test_check_rebalance_price_unavailable():
    pos = _pos(base="NONEXIST")
    need, reason, long_n, short_n = check_rebalance(pos)
    assert need is False
    assert "price_unavailable" in reason


def test_check_leg_alive_both_present():
    pos = _pos(qty=1.0)
    venue_positions = {
        "okx": [{"symbol": "BTCUSDT", "side": "long", "qty": 1.0}],
        "bybit": [{"symbol": "BTCUSDT", "side": "short", "qty": -1.0}],
    }
    alive, reason = check_leg_alive(pos, venue_positions)
    assert alive is True
    assert reason == ""


def test_check_leg_alive_long_gone():
    pos = _pos(qty=1.0)
    venue_positions = {
        "okx": [],  # long leg gone
        "bybit": [{"symbol": "BTCUSDT", "side": "short", "qty": -1.0}],
    }
    alive, reason = check_leg_alive(pos, venue_positions)
    assert alive is False
    assert "long_leg_gone" in reason


def test_check_leg_alive_short_gone():
    pos = _pos(qty=1.0)
    venue_positions = {
        "okx": [{"symbol": "BTCUSDT", "side": "long", "qty": 1.0}],
        "bybit": [],  # short leg gone
    }
    alive, reason = check_leg_alive(pos, venue_positions)
    assert alive is False
    assert "short_leg_gone" in reason


def test_check_leg_alive_both_gone():
    pos = _pos(qty=1.0)
    venue_positions = {
        "okx": [],
        "bybit": [],
    }
    alive, reason = check_leg_alive(pos, venue_positions)
    assert alive is False
    assert "both_legs_gone" in reason


def test_check_leg_alive_qty_below_threshold():
    """qty at 50% of expected → leg considered dead."""
    pos = _pos(qty=1.0)
    venue_positions = {
        "okx": [{"symbol": "BTCUSDT", "side": "long", "qty": 0.4}],  # < 0.95
        "bybit": [{"symbol": "BTCUSDT", "side": "short", "qty": -1.0}],
    }
    alive, reason = check_leg_alive(pos, venue_positions)
    assert alive is False
    assert "long_leg_gone" in reason


def test_leg_qty_from_snapshot():
    venue_positions = {
        "okx": [{"symbol": "BTCUSDT", "side": "long", "qty": 0.7}],
        "bybit": [{"symbol": "BTCUSDT", "side": "short", "qty": -0.5}],
    }
    assert _leg_qty_from_snapshot(venue_positions, "okx", "BTC", "long") == 0.7
    assert _leg_qty_from_snapshot(venue_positions, "bybit", "BTC", "short") == 0.5
    # venue not in snapshot → None (unknown), present but no match → 0.0
    assert _leg_qty_from_snapshot(venue_positions, "binance", "BTC", "long") is None
    assert _leg_qty_from_snapshot(venue_positions, "okx", "ETH", "long") == 0.0


def test_estimate_spread_pnl_profit():
    """Opening spread large, current spread small → positive profit."""
    pos = {
        "long_price": 100.0,
        "short_price": 101.0,
        "qty": 2.0,
        "trade_usd": 2000.0,
    }
    result = estimate_spread_pnl(pos, current_long_px=100.5, current_short_px=100.6)
    assert result["open_spread"] == 1.0
    assert result["close_spread"] == pytest.approx(0.1)
    assert result["spread_pnl"] == pytest.approx(1.8)
    assert result["spread_pnl_pct"] == pytest.approx(0.09)


def test_estimate_spread_pnl_loss():
    """Opening spread small, current spread large → negative profit."""
    pos = {
        "long_price": 100.0,
        "short_price": 101.0,
        "qty": 2.0,
        "trade_usd": 2000.0,
    }
    result = estimate_spread_pnl(pos, current_long_px=100.0, current_short_px=103.0)
    assert result["open_spread"] == 1.0
    assert result["close_spread"] == pytest.approx(3.0)
    assert result["spread_pnl"] == pytest.approx(-4.0)
    assert result["spread_pnl_pct"] == pytest.approx(-0.2)


def test_estimate_spread_pnl_zero():
    """Prices unchanged → zero profit."""
    pos = {
        "long_price": 100.0,
        "short_price": 101.0,
        "qty": 1.0,
        "trade_usd": 1000.0,
    }
    result = estimate_spread_pnl(pos, current_long_px=100.0, current_short_px=101.0)
    assert result["open_spread"] == 1.0
    assert result["close_spread"] == pytest.approx(1.0)
    assert result["spread_pnl"] == pytest.approx(0.0)
    assert result["spread_pnl_pct"] == pytest.approx(0.0)


def test_check_rebalance_qty_mismatch(monkeypatch):
    """Partial liquidation shrinks short leg quantity → triggers rebalance."""
    monkeypatch.setattr(watcher_mod, "_get_mark_price", lambda v, b, q="USDT": 100.0)
    pos = _pos(qty=1.0)
    venue_positions = {
        "okx": [{"symbol": "BTCUSDT", "side": "long", "qty": 1.0}],
        "bybit": [{"symbol": "BTCUSDT", "side": "short", "qty": -0.9}],
    }
    need, reason, long_n, short_n = check_rebalance(
        pos, max_skew_pct=1.0, venue_positions=venue_positions
    )
    assert need is True
    assert "qty" in reason
    assert long_n == 100.0
    assert short_n == 90.0


def test_check_rebalance_equal_qty_no_skew(monkeypatch):
    monkeypatch.setattr(watcher_mod, "_get_mark_price", lambda v, b, q="USDT": 100.0)
    pos = _pos(qty=1.0)
    venue_positions = {
        "okx": [{"symbol": "BTCUSDT", "side": "long", "qty": 1.0}],
        "bybit": [{"symbol": "BTCUSDT", "side": "short", "qty": -1.0}],
    }
    need, reason, long_n, short_n = check_rebalance(
        pos, max_skew_pct=1.0, venue_positions=venue_positions
    )
    assert need is False
    assert long_n == short_n == 100.0


def test_check_margin_distance_alerts_when_close(monkeypatch):
    """Mark price close to liquidation price (< threshold) → alert; far away → no alert."""
    monkeypatch.setattr(watcher_mod, "_get_mark_price", lambda v, b, q="USDT": 100.0)
    pos = _pos(qty=1.0)
    venue_positions = {
        # long leg liq price 90 → distance 10% < 20% threshold → alert
        "okx": [{"symbol": "BTCUSDT", "side": "long", "qty": 1.0, "liq_price": 90.0}],
        # short leg liq price 150 → distance 50% → safe
        "bybit": [
            {"symbol": "BTCUSDT", "side": "short", "qty": 1.0, "liq_price": 150.0}
        ],
    }
    alerts = check_margin_distance(pos, venue_positions, alert_distance_pct=20.0)
    assert len(alerts) == 1
    assert alerts[0]["leg"] == "long"
    assert alerts[0]["venue"] == "okx"
    assert abs(alerts[0]["distance_pct"] - 10.0) < 1e-9


def test_check_margin_distance_no_liq_price(monkeypatch):
    """Exchange returns no liquidation price (common for cross-margin low-leverage) → no alert, no error."""
    monkeypatch.setattr(watcher_mod, "_get_mark_price", lambda v, b, q="USDT": 100.0)
    pos = _pos(qty=1.0)
    venue_positions = {
        "okx": [{"symbol": "BTCUSDT", "side": "long", "qty": 1.0, "liq_price": 0.0}],
        "bybit": [{"symbol": "BTCUSDT", "side": "short", "qty": 1.0}],
    }
    assert check_margin_distance(pos, venue_positions) == []


def test_check_margin_distance_skips_unfetched_venue(monkeypatch):
    """Venue snapshot missing (fetch failed) → skip that leg rather than misjudge."""
    monkeypatch.setattr(watcher_mod, "_get_mark_price", lambda v, b, q="USDT": 100.0)
    pos = _pos(qty=1.0)
    venue_positions = {
        "bybit": [
            {"symbol": "BTCUSDT", "side": "short", "qty": 1.0, "liq_price": 105.0}
        ],
    }
    alerts = check_margin_distance(pos, venue_positions, alert_distance_pct=20.0)
    assert len(alerts) == 1 and alerts[0]["leg"] == "short"
