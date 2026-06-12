#!/usr/bin/env python3
"""Unit tests for cross-interval funding estimation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.cross_interval_funding import (
    basis_pct,
    blended_hourly_rate,
    infer_last_settle_ts,
    settle_progress,
    spread_source_for_pair,
)


def test_infer_last_settle_ts():
    next_ts = 8 * 3600 * 1000
    assert infer_last_settle_ts(next_ts, 8.0) == 0
    assert infer_last_settle_ts(0, 8.0) == 0


def test_settle_progress_from_last_settle():
    interval_ms = 8 * 3600 * 1000
    last = 1_000_000
    next_ts = last + interval_ms
    now = last + interval_ms // 2
    p = settle_progress(
        now, next_funding_ts=next_ts, last_settle_ts=last, interval_h=8.0
    )
    assert 0.49 < p < 0.51


def test_settle_progress_from_next_only():
    interval_ms = 3600 * 1000
    now = 1_000_000
    next_ts = now + interval_ms // 4  # 25% remaining → 75% progress
    p = settle_progress(now, next_funding_ts=next_ts, interval_h=1.0)
    assert 0.74 < p < 0.76


def test_basis_pct_caps_extreme():
    assert basis_pct(101_000, 100_000) == 1.0
    assert basis_pct(99_000, 100_000) == -1.0


def test_blended_hourly_early_period_trusts_rate():
    interval_h = 8.0
    now_ms = 1_000_000
    last = now_ms - int(0.1 * interval_h * 3600 * 1000)  # 10% into period
    info = {
        "mark_price": 100_500.0,
        "index_price": 100_000.0,
        "next_funding_ts": last + int(interval_h * 3600 * 1000),
        "last_settle_ts": last,
    }
    hourly, meta = blended_hourly_rate(0.08, interval_h, info, now_ms=now_ms)
    rate_hourly = 0.08 / 8.0
    basis_hourly = 0.5 / 8.0  # 0.5% basis over 8h
    assert meta["used_basis"] is True
    assert hourly < basis_hourly  # early → closer to rate than basis
    assert hourly > rate_hourly * 0.5


def test_blended_hourly_late_period_trusts_basis():
    interval_h = 8.0
    now_ms = 1_000_000
    last = now_ms - int(0.9 * interval_h * 3600 * 1000)
    info = {
        "mark_price": 100_500.0,
        "index_price": 100_000.0,
        "next_funding_ts": last + int(interval_h * 3600 * 1000),
        "last_settle_ts": last,
    }
    hourly, meta = blended_hourly_rate(0.08, interval_h, info, now_ms=now_ms)
    rate_hourly = 0.08 / 8.0
    basis_hourly = 0.5 / 8.0
    assert meta["settle_progress"] > 0.85
    assert hourly > rate_hourly
    assert abs(hourly - basis_hourly) < abs(hourly - rate_hourly)


def test_blended_hourly_no_index_falls_back():
    info = {"mark_price": 100.0, "index_price": 0.0, "next_funding_ts": 0}
    hourly, meta = blended_hourly_rate(0.16, 2.0, info, now_ms=0)
    assert meta["used_basis"] is False
    assert hourly == 0.16 / 2.0


def test_spread_source_labels():
    assert spread_source_for_pair(False, {}, {}) == "rate"
    assert (
        spread_source_for_pair(True, {"used_basis": True}, {"used_basis": False})
        == "basis_blend"
    )
    assert spread_source_for_pair(True, {}, {}) == "rate_linear"
