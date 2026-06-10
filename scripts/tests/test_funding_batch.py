#!/usr/bin/env python3
"""Unit tests for funding batch helpers (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from market.funding_batch import (
    _snap_from_all_row,
    fetch_funding_history_parallel,
    fetch_funding_snaps_for_assets,
)


class FakeFP:
    venue_id = "fake"

    def __init__(self):
        self.all_rows = [
            {
                "symbol": "BTCUSDT",
                "rate_pct": 0.05,
                "next_funding_ts": 1_700_000_000_000,
                "mark_price": 50000.0,
            },
            {
                "symbol": "ETHUSDT",
                "rate_pct": -0.03,
                "next_funding_ts": 0,  # needs backfill
                "mark_price": 3000.0,
            },
        ]
        self.current_calls: list[str] = []

    def fetch_all(self, quote: str):
        return list(self.all_rows)

    def fetch_interval_map(self, quote: str):
        return {"BTCUSDT": 8.0, "ETHUSDT": 4.0}

    def fetch_current(self, pair: str):
        self.current_calls.append(pair)
        return {
            "rate_pct": -0.03,
            "next_funding_ts": 1_700_000_360_000,
            "last_settle_ts": 1_700_000_000_000,
            "interval_ms": 4 * 3600 * 1000,
            "mark_price": 3000.0,
        }

    def fetch_since(self, pair: str, start_ms: int):
        return [
            {"ts": start_ms + 1000, "rate_pct": 0.01},
            {"ts": start_ms + 2000, "rate_pct": 0.02},
        ]


def test_snap_from_all_row_interval_map():
    row = {"symbol": "BTCUSDT", "rate_pct": 0.1, "next_funding_ts": 8000, "mark_price": 1.0}
    snap = _snap_from_all_row(row, "USDT", {"BTCUSDT": 4.0})
    assert snap["interval_ms"] == 4 * 3600 * 1000
    assert snap["last_settle_ts"] == 8000 - snap["interval_ms"]


def test_fetch_all_bulk_then_backfill():
    fp = FakeFP()
    snaps = fetch_funding_snaps_for_assets(fp, ["BTC", "ETH"], "USDT", workers=2)
    assert "BTC" in snaps
    assert snaps["BTC"]["rate_pct"] == 0.05
    assert "ETH" in snaps
    assert snaps["ETH"]["next_funding_ts"] == 1_700_000_360_000
    assert "ETHUSDT" in fp.current_calls


def test_fetch_history_sorted():
    fp = FakeFP()
    hist = fetch_funding_history_parallel(
        fp, ["BTC"], "USDT", {"BTC": 1000}, workers=1
    )
    ts = [r["ts"] for r in hist["BTC"]]
    assert ts == sorted(ts)


def test_missing_asset_falls_back_to_fetch_current():
    fp = FakeFP()
    snaps = fetch_funding_snaps_for_assets(fp, ["DOGE"], "USDT")
    assert "DOGE" in snaps
    assert "DOGEUSDT" in fp.current_calls


if __name__ == "__main__":
    test_snap_from_all_row_interval_map()
    test_fetch_all_bulk_then_backfill()
    test_fetch_history_sorted()
    test_missing_asset_falls_back_to_fetch_current()
    print("ALL PASSED")
