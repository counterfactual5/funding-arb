#!/usr/bin/env python3
"""Tests for shared strategy_config (Dashboard + CLI alignment)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from core.strategy_config import (  # noqa: E402
    apply_strategy_to_pure_futures_cfg,
    row_edge_threshold,
)


def test_apply_strategy_overlays_runner_template(tmp_path, monkeypatch):
    cfg_file = tmp_path / "strategy_config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "min_spread_annual": 0.03,
                "min_edge_annual": 0.015,
                "max_mark_spread_pct": 0.8,
                "trade_usd": 2500,
                "max_positions": 5,
                "scan_interval_sec": 120,
                "scan_venues": ["bybit", "okx"],
                "min_edge_1h": 0.008,
                "min_edge_mismatch": 0.025,
                "fee_mode": "auto",
                "venue_fee_tiers": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "core.strategy_config.STRATEGY_CONFIG_PATH",
        cfg_file,
    )

    tpl = {
        "dry_run": True,
        "pureFuturesArbitrage": {
            "venues": ["binance"],
            "minSpreadPct": 0.05,
            "minNetEdgePct": 0.01,
            "tradeUsdPerPair": 500,
            "maxConcurrentPairs": 2,
            "allowSettleMismatch": False,
            "parallelLegs": True,
        },
    }
    out = apply_strategy_to_pure_futures_cfg(tpl)
    pfa = out["pureFuturesArbitrage"]
    assert pfa["venues"] == ["bybit", "okx"]
    assert pfa["minSpreadPct"] == 0.03
    assert pfa["minNetEdgePct"] == 0.015
    assert pfa["tradeUsdPerPair"] == 2500
    assert pfa["maxConcurrentPairs"] == 5
    assert pfa["scanIntervalMinutes"] == 2.0
    assert pfa["allowSettleMismatch"] is True
    assert pfa["parallelLegs"] is True


def test_row_edge_threshold_groups():
    row_1h = {"long_interval_h": 1.0, "short_interval_h": 1.0}
    assert row_edge_threshold(row_1h, 0.02, 0.01, 0.03) == 0.01

    row_mm = {"long_interval_h": 4.0, "short_interval_h": 8.0, "settle_mismatch": True}
    assert row_edge_threshold(row_mm, 0.02, 0.01, 0.03) == 0.03

    row_base = {"long_interval_h": 8.0, "short_interval_h": 8.0}
    assert row_edge_threshold(row_base, 0.02, 0.01, 0.03) == 0.02
