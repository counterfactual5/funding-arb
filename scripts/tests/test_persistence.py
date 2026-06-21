"""Tests for scripts/notify/persistence.py — spread reconstruction & labels.

Network is mocked: ``fetch_leg_history`` is patched so we exercise the
alignment / metric logic deterministically without hitting any exchange.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notify.persistence import (  # noqa: E402
    _oriented_spread_series,
    annotate_persistence,
)

H8 = 8 * 3600 * 1000


def _rows(*rates, step=H8, start=0):
    return [{"ts": start + i * step, "rate_pct": r} for i, r in enumerate(rates)]


class TestOrientedSpreadSeries:
    def test_same_grid(self):
        long_rows = _rows(0.01, 0.01, 0.01)
        short_rows = _rows(0.05, 0.04, 0.02)
        series = _oriented_spread_series(long_rows, short_rows)
        assert series == pytest.approx([0.04, 0.03, 0.01])

    def test_empty_when_a_leg_missing(self):
        assert _oriented_spread_series([], _rows(0.05)) == []
        assert _oriented_spread_series(_rows(0.01), []) == []

    def test_forward_fill_across_mismatched_intervals(self):
        # long settles every 8h, short every 1h within the same window
        long_rows = [{"ts": 0, "rate_pct": 0.01}, {"ts": H8, "rate_pct": 0.02}]
        short_rows = [
            {"ts": 0, "rate_pct": 0.05},
            {"ts": 3600 * 1000, "rate_pct": 0.05},
            {"ts": H8, "rate_pct": 0.03},
        ]
        series = _oriented_spread_series(long_rows, short_rows)
        # grid = {0, 1h, 8h}; long forward-filled to 0.01 until 8h
        assert series == pytest.approx([0.04, 0.04, 0.01])


class TestAnnotatePersistence:
    def _fake_fetch(self, mapping):
        def _fetch(venue, base, days, **kw):
            return mapping.get((venue.lower(), base.upper()), [])

        return _fetch

    def test_stable_spread_high_held_pct(self):
        mapping = {
            ("okx", "BTC"): _rows(0.01, 0.01, 0.01, 0.01),
            ("bybit", "BTC"): _rows(0.05, 0.05, 0.05, 0.05),
        }
        rows = [
            {
                "base": "BTC",
                "long_venue": "okx",
                "short_venue": "bybit",
                "spread_pct": 0.04,
            }
        ]
        with patch("notify.persistence.fetch_leg_history", self._fake_fetch(mapping)):
            annotate_persistence(rows, days=3, workers=2)
        assert rows[0]["hist_cycles"] == 4
        assert rows[0]["hist_held"] == 4
        assert rows[0]["hist_held_pct"] == 100
        assert rows[0]["is_spike"] is False

    def test_transient_spike_flagged(self):
        # history median spread ~0.01, but current spread 0.10 → >3× → spike
        mapping = {
            ("okx", "HOME"): _rows(0.00, 0.00, 0.00, 0.00),
            ("bybit", "HOME"): _rows(0.01, 0.01, 0.01, 0.01),
        }
        rows = [
            {
                "base": "HOME",
                "long_venue": "okx",
                "short_venue": "bybit",
                "spread_pct": 0.10,
            }
        ]
        with patch("notify.persistence.fetch_leg_history", self._fake_fetch(mapping)):
            annotate_persistence(rows, days=3, workers=2)
        assert rows[0]["is_spike"] is True

    def test_insufficient_history_leaves_row_unannotated(self):
        mapping = {("okx", "BTC"): _rows(0.01), ("bybit", "BTC"): _rows(0.05)}
        rows = [
            {
                "base": "BTC",
                "long_venue": "okx",
                "short_venue": "bybit",
                "spread_pct": 0.04,
            }
        ]
        with patch("notify.persistence.fetch_leg_history", self._fake_fetch(mapping)):
            annotate_persistence(rows, days=3, workers=2)
        assert "hist_cycles" not in rows[0]

    def test_days_zero_is_noop(self):
        rows = [{"base": "BTC", "long_venue": "okx", "short_venue": "bybit"}]
        annotate_persistence(rows, days=0)
        assert "hist_cycles" not in rows[0]
