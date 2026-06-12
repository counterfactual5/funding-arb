#!/usr/bin/env python3
"""Shared strategy settings from scripts/data/strategy_config.json.

Dashboard Settings and CLI runners (pure-futures spread, orchestrate) read the
same file so thresholds, venues, and fee policy stay aligned.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from core.config import SKILL_ROOT

STRATEGY_CONFIG_PATH = SKILL_ROOT / "data" / "strategy_config.json"

DEFAULT_STRATEGY: dict[str, Any] = {
    "min_spread_annual": 0.04,
    "min_edge_annual": 0.02,
    "max_mark_spread_pct": 1.0,
    "trade_usd": 5000.0,
    "max_positions": 3,
    "scan_interval_sec": 300,
    "scan_venues": ["binance", "bitget", "bybit", "okx"],
    "min_edge_1h": 0.01,
    "min_edge_mismatch": None,
    "fee_mode": "auto",
    "venue_fee_tiers": {},
}


def load_strategy_config() -> dict[str, Any]:
    """Load persisted strategy config merged over defaults."""
    cfg = dict(DEFAULT_STRATEGY)
    try:
        if STRATEGY_CONFIG_PATH.exists():
            saved = json.loads(STRATEGY_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update({k: v for k, v in saved.items() if k in DEFAULT_STRATEGY})
    except Exception:
        pass
    return cfg


def save_strategy_config(cfg: dict[str, Any]) -> None:
    """Persist strategy config (used by Dashboard API)."""
    merged = dict(DEFAULT_STRATEGY)
    merged.update({k: v for k, v in cfg.items() if k in DEFAULT_STRATEGY})
    try:
        STRATEGY_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        STRATEGY_CONFIG_PATH.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def strategy_fee_policy(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    from core.fee_providers import parse_fee_policy

    return parse_fee_policy(cfg or load_strategy_config())


def row_edge_threshold(
    row: dict[str, Any],
    min_edge: float,
    min_edge_1h: float | None,
    min_edge_mismatch: float | None,
) -> float:
    """Per-row net-edge bar by settlement-interval group (matches Dashboard scanner)."""
    long_h = float(row.get("long_interval_h", 8) or 8)
    short_h = float(row.get("short_interval_h", 8) or 8)
    if min_edge_1h is not None and long_h <= 1.0 and short_h <= 1.0:
        return min_edge_1h
    if min_edge_mismatch is not None and (
        row.get("settle_mismatch") or abs(long_h - short_h) > 0.5
    ):
        return min_edge_mismatch
    return min_edge


def _optional_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def strategy_edge_thresholds(
    strat: dict[str, Any] | None = None,
) -> tuple[float, float | None, float | None]:
    """Return (min_edge_annual, min_edge_1h, min_edge_mismatch)."""
    cfg = strat or load_strategy_config()
    return (
        float(cfg.get("min_edge_annual", DEFAULT_STRATEGY["min_edge_annual"])),
        _optional_float(cfg.get("min_edge_1h")),
        _optional_float(cfg.get("min_edge_mismatch")),
    )


def allow_settle_mismatch(strat: dict[str, Any], pfa: dict[str, Any]) -> bool:
    """Whether cross-interval pairs may be opened."""
    if bool(pfa.get("allowSettleMismatch", False)):
        return True
    return strat.get("min_edge_mismatch") is not None


def apply_strategy_to_pure_futures_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """Overlay Dashboard strategy_config onto a runner template config."""
    strat = load_strategy_config()
    out = dict(cfg)
    pfa = dict(out.get("pureFuturesArbitrage") or {})

    venues = strat.get("scan_venues")
    if isinstance(venues, list) and venues:
        pfa["venues"] = [str(v).lower() for v in venues]

    pfa["minSpreadPct"] = float(strat.get("min_spread_annual", pfa.get("minSpreadPct", 0.04)))
    pfa["minNetEdgePct"] = float(strat.get("min_edge_annual", pfa.get("minNetEdgePct", 0.02)))
    pfa["maxMarkSpreadPct"] = float(
        strat.get("max_mark_spread_pct", pfa.get("maxMarkSpreadPct", 1.0))
    )
    pfa["tradeUsdPerPair"] = float(strat.get("trade_usd", pfa.get("tradeUsdPerPair", 5000.0)))
    pfa["maxConcurrentPairs"] = int(
        strat.get("max_positions", pfa.get("maxConcurrentPairs", 3))
    )
    if "scan_interval_sec" in strat:
        pfa["scanIntervalMinutes"] = float(strat["scan_interval_sec"]) / 60.0

    if allow_settle_mismatch(strat, pfa):
        pfa["allowSettleMismatch"] = True

    out["pureFuturesArbitrage"] = pfa
    return out


def min_edge_for_row_factory(
    min_edge: float,
    min_edge_1h: float | None,
    min_edge_mismatch: float | None,
) -> Callable[[dict[str, Any]], float]:
    def _fn(row: dict[str, Any]) -> float:
        return row_edge_threshold(row, min_edge, min_edge_1h, min_edge_mismatch)

    return _fn
