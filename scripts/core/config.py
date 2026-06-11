#!/usr/bin/env python3
"""Core configuration and path resolution."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent


# ── Path helpers ────────────────────────────────────────────────────────────


def _load_dotenv() -> None:
    env_file = SKILL_ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        os.environ.setdefault(key, val)


_load_dotenv()


def farb_home() -> Path:
    """Runtime data root. Override with env FARB_HOME (primary) or DCA_HOME (legacy fallback).
    Default: <project>/data."""
    raw = os.environ.get("FARB_HOME", "") or os.environ.get("DCA_HOME", "")
    raw = raw.strip()
    if raw:
        return Path(raw).expanduser().resolve()
    local = SKILL_ROOT / "data"
    local.mkdir(parents=True, exist_ok=True)
    return local.resolve()


def dca_home() -> Path:  # deprecated alias, use farb_home()
    return farb_home()


def runs_namespace() -> str:
    ns = os.environ.get("FARB_RUNS_NAMESPACE", "") or os.environ.get(
        "DCA_RUNS_NAMESPACE", "funding-arb"
    )
    return ns.strip() or "funding-arb"


def runs_base() -> Path:
    return farb_home() / runs_namespace()


def strategy_dir(strategy_id: str) -> Path:
    return runs_base() / strategy_id


def resolve_config_path(config_path: Path) -> Path:
    if config_path.is_absolute():
        return config_path
    if config_path.exists():
        return config_path.resolve()
    tpl = SKILL_ROOT / "templates" / config_path.name
    if tpl.exists():
        return tpl
    deployed = runs_base() / config_path
    if deployed.exists():
        return deployed.resolve()
    return (Path.cwd() / config_path).resolve()


# ── DCA mode resolution ─────────────────────────────────────────────────────

MODE_DEFAULTS: dict[str, dict[str, Any]] = {
    "accumulate": {
        "takeProfitEnabled": False,
        "recycleProfitToBudget": False,
    },
    "recycle": {
        "takeProfitEnabled": True,
        "takeProfitMode": "lots_lifo",
        "takeProfitSellRatio": 1.0,
        "recycleProfitToBudget": True,
        "takeProfitVolatilityScaled": True,
        "takeProfitAtrMult": 0.3,
        "takeProfitPctMin": 0.015,
        "takeProfitPctMax": 0.05,
    },
}

MODE_LABELS: dict[str, str] = {
    "accumulate": "Accumulate",
    "recycle": "Recycle",
}

MODE_HINTS: dict[str, str] = {
    "recycle": (
        "Recycle mode: suitable for sustained downtrends with occasional small bounces; manually stop or switch to Accumulate near the bounce peak, "
        "otherwise you may sell all holdings and miss a full recovery."
    ),
}


def infer_dca_mode(cfg: dict[str, Any]) -> str:
    raw = str(cfg.get("dcaMode", "")).strip().lower()
    if raw in MODE_DEFAULTS:
        return raw
    if cfg.get("takeProfitEnabled"):
        return "recycle"
    return "accumulate"


def apply_dca_mode(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``dcaMode`` into take-profit fields. Legacy ``takeProfitEnabled`` still works."""
    mode = infer_dca_mode(cfg)
    cfg = dict(cfg)
    cfg["dcaMode"] = mode
    cfg["takeProfitEnabled"] = MODE_DEFAULTS[mode]["takeProfitEnabled"]
    cfg["recycleProfitToBudget"] = MODE_DEFAULTS[mode]["recycleProfitToBudget"]
    for key, value in MODE_DEFAULTS[mode].items():
        cfg.setdefault(key, value)
    return cfg


def mode_label(cfg: dict[str, Any]) -> str:
    return MODE_LABELS.get(
        str(cfg.get("dcaMode", "accumulate")), cfg.get("dcaMode", "")
    )


def mode_hint(cfg: dict[str, Any]) -> str | None:
    return MODE_HINTS.get(str(cfg.get("dcaMode", "")))


# ── Strategy Timeframes & Periods ──────────────────────────────────────────


def resolve_timeframes(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    default_tf = {
        "slow": {"interval": "1day", "limit": 200},
        "mid": {"interval": "4h", "limit": 50},
        "macro": {"interval": "1week", "limit": 60},
    }
    raw = cfg.get("timeframes", default_tf)
    result = {}
    for k in ("mid", "slow", "macro"):
        val = raw.get(k, default_tf[k])
        if isinstance(val, str):
            limit = default_tf[k]["limit"]
            result[k] = {"interval": val, "limit": limit}
        elif isinstance(val, dict):
            result[k] = {
                "interval": val.get("interval", default_tf[k]["interval"]),
                "limit": val.get("limit", default_tf[k]["limit"]),
            }
        else:
            result[k] = default_tf[k]
    return result


def resolve_indicator_periods(cfg: dict[str, Any]) -> dict[str, int]:
    default_periods = {
        "rsi": 14,
        "emaFast": 20,
        "emaSlow": 50,
        "atr": 14,
        "rangeLookbackBars": 10,
        "drawdownBars": 200,
    }
    raw = cfg.get("indicatorPeriods", default_periods)
    res = dict(raw)
    if "emaFast" in res and "ema_fast" not in res:
        res["ema_fast"] = res["emaFast"]
    if "emaSlow" in res and "ema_slow" not in res:
        res["ema_slow"] = res["emaSlow"]
    return res


def interval_to_ms(interval: str) -> int:
    interval = interval.lower()
    if interval.endswith("m"):
        return int(interval[:-1]) * 60 * 1000
    if interval.endswith("h"):
        return int(interval[:-1]) * 60 * 60 * 1000
    if interval in ("1d", "1day"):
        return 24 * 60 * 60 * 1000
    if interval in ("1w", "1week"):
        return 7 * 24 * 60 * 60 * 1000
    raise ValueError(f"Unknown interval: {interval}")


def interval_to_bars_per_day(interval: str) -> float:
    ms = interval_to_ms(interval)
    day_ms = 24 * 60 * 60 * 1000
    return day_ms / ms
