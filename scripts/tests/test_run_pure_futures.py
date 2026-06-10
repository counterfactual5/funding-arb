#!/usr/bin/env python3
"""Quick test that runner loads config and produces expected output shape (no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from execution.run_pure_futures_spread import run_once  # noqa: E402


def _scan_stub(*args, **kwargs):
    return {
        "forward": [
            {
                "base": "BTC",
                "direction": "forward",
                "long_venue": "okx",
                "short_venue": "bybit",
                "spread_pct": 0.12,
                "net_edge_pct": 0.02,
                "settle_mismatch": False,
            }
        ],
        "reverse": [],
    }


def test_run_once_shape():
    cfg = json.loads(
        json.dumps(
            {
                "dry_run": True,
                "pureFuturesArbitrage": {
                    "venues": ["binance", "okx"],
                    "maxConcurrentPairs": 2,
                    "tradeUsdPerPair": 500,
                    "minSpreadPct": 0.05,
                    "minNetEdgePct": 0.01,
                    "exitThresholdPct": 0.01,
                },
            }
        )
    )
    # monkeypatch the scanner so we don't hit real APIs
    with patch(
        "execution.run_pure_futures_spread.scan_pure_futures_spreads", _scan_stub
    ):
        out = run_once(cfg)
    assert out["strategy"] == "pure_futures_spread"
    assert out["dry_run"] is True
    assert out["scan_total"] == 1
    assert len(out["actions"]) >= 1
