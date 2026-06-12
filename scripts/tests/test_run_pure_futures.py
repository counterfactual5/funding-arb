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


def test_run_once_shape(tmp_path, monkeypatch):
    cfg_file = tmp_path / "strategy_config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "min_spread_annual": 0.03,
                "min_edge_annual": 0.01,
                "max_mark_spread_pct": 1.0,
                "trade_usd": 500,
                "max_positions": 2,
                "scan_interval_sec": 300,
                "scan_venues": ["binance", "okx"],
                "min_edge_1h": None,
                "min_edge_mismatch": None,
                "fee_mode": "auto",
                "venue_fee_tiers": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("core.strategy_config.STRATEGY_CONFIG_PATH", cfg_file)

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
    ), patch("execution.run_pure_futures_spread._open_positions", lambda: []):
        out = run_once(cfg)
    assert out["strategy"] == "pure_futures_spread"
    assert out["dry_run"] is True
    assert out["scan_total"] == 1
    assert len(out["actions"]) >= 1
