#!/usr/bin/env python3
"""Hermetic tests for pure futures spread observation report."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cli.report_pure_futures_spreads import build_report, load_snapshots  # noqa: E402


def _snap(ts: datetime, rows: list[dict]) -> dict:
    return {
        "timestamp": ts.isoformat(),
        "venues": ["binance", "bitget", "bybit", "okx"],
        "total_assets_scanned": 2,
        "forward": rows,
        "reverse": [],
    }


def _row(base="BTC", edge=0.02, spread=0.13, mismatch=False) -> dict:
    return {
        "base": base,
        "direction": "forward",
        "long_venue": "binance",
        "short_venue": "bitget",
        "long_rate_pct": 0.02,
        "short_rate_pct": 0.15,
        "spread_pct": spread,
        "fee_pct": 0.11,
        "net_edge_pct": edge,
        "annual_apy_pct": 142.3,
        "settle_mismatch": mismatch,
    }


def test_load_snapshots_and_build_report(tmp_path):
    path = tmp_path / "spreads.jsonl"
    t0 = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
    snaps = [
        _snap(t0, [_row(edge=0.02)]),
        _snap(t0 + timedelta(minutes=5), [_row(edge=0.03)]),
        _snap(t0 + timedelta(minutes=10), [_row(edge=0.04, mismatch=True)]),
    ]
    path.write_text("\n".join(json.dumps(s) for s in snaps) + "\n")

    loaded = load_snapshots(path)
    report = build_report(loaded, max_gap_min=6)

    assert report["snapshot_count"] == 3
    assert report["total_opportunity_rows"] == 3
    assert report["unique_opportunities"] == 1
    assert report["settle_mismatch_ratio_pct"] == 33.33
    assert report["top_assets"][0] == {"key": "BTC", "count": 3}
    opp = report["opportunities"][0]
    assert opp["base"] == "BTC"
    assert opp["samples"] == 3
    assert opp["seen_ratio_pct"] == 100.0
    assert opp["longest_duration_min"] == 10.0
    assert opp["avg_edge_pct"] == 0.03
    assert opp["max_edge_pct"] == 0.04


def test_min_edge_and_min_samples_filter(tmp_path):
    path = tmp_path / "spreads.jsonl"
    t0 = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
    snaps = [
        _snap(t0, [_row(base="BTC", edge=0.005), _row(base="ETH", edge=0.03)]),
        _snap(t0 + timedelta(minutes=5), [_row(base="ETH", edge=0.04)]),
    ]
    path.write_text("\n".join(json.dumps(s) for s in snaps) + "\n")

    report = build_report(load_snapshots(path), min_edge=0.01, min_samples=2)

    assert report["total_opportunity_rows"] == 2
    assert len(report["opportunities"]) == 1
    assert report["opportunities"][0]["base"] == "ETH"
    assert report["opportunities"][0]["samples"] == 2


def test_gap_breaks_duration_streak(tmp_path):
    path = tmp_path / "spreads.jsonl"
    t0 = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
    snaps = [
        _snap(t0, [_row(edge=0.02)]),
        _snap(t0 + timedelta(minutes=5), [_row(edge=0.03)]),
        _snap(t0 + timedelta(minutes=40), [_row(edge=0.04)]),
    ]
    path.write_text("\n".join(json.dumps(s) for s in snaps) + "\n")

    report = build_report(load_snapshots(path), max_gap_min=10)
    opp = report["opportunities"][0]

    assert opp["samples"] == 3
    # First streak 0→5 min, second streak is a single point. Longest duration is 5 min.
    assert opp["longest_duration_min"] == 5.0
    assert opp["max_streak_samples"] == 2
