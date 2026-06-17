#!/usr/bin/env python3
"""Backtest API routes."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["backtest"])

# ---------------------------------------------------------------------------
# Try importing real backtest module
# ---------------------------------------------------------------------------
_backtest_fn = None
try:
    from backtest.backtest_pure_futures_spread import run_backtest  # noqa: E402

    _backtest_fn = run_backtest
except Exception:
    pass

# ---------------------------------------------------------------------------
# Backtest results storage
# ---------------------------------------------------------------------------
_RESULTS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "data"
    / "backtest-results"
)


def _ensure_results_dir() -> Path:
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return _RESULTS_DIR


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class BacktestRequest(BaseModel):
    jsonl_file: str | None = Field(None, description="Historical data JSONL file path")
    history_bases: str | None = Field(
        None, description="Backtest asset list, comma-separated, e.g. BTC,ETH"
    )
    history_venues: str | None = Field(
        None,
        description=(
            "Venues for history fetch, comma-separated "
            "(default binance,bitget,bybit,okx; also: hyperliquid, aster, lighter). "
            "edgex has no public funding history (current-snapshot only) and is "
            "skipped if requested."
        ),
    )
    history_days: int = Field(90, description="Backtest period in days")
    capital: float = Field(100000, gt=0, description="Initial capital (USD)")
    trade_usd: float = Field(5000, gt=0, description="Trade size per transaction (USD)")
    min_spread: float = Field(0.08, description="Minimum annualized spread (%)")
    exit_edge: float = Field(0.02, description="Exit annualized threshold (%)")
    max_positions: int = Field(3, ge=1, description="Maximum concurrent positions")
    min_edge_pct: float = Field(
        0.01, description="Minimum net edge percentage to enter"
    )
    max_holding_hours: int = Field(720, description="Maximum holding time in hours")
    allow_mismatch: bool = Field(
        False, description="Allow cross-interval pairs (e.g. 1h vs 8h)"
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


_DEFAULT_JSONL = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "pure_futures_spreads.jsonl"
)


def _load_snapshots_for_request(req: BacktestRequest) -> list[dict[str, Any]]:
    """Load snapshots either from exchange funding history or a scanner JSONL file."""
    if req.history_bases:
        from backtest.funding_history_source import (
            fetch_history_snapshots,  # noqa: E402
        )

        bases = [b.strip().upper() for b in req.history_bases.split(",") if b.strip()]
        if req.history_venues:
            venues = [
                v.strip().lower() for v in req.history_venues.split(",") if v.strip()
            ]
        else:
            venues = ["binance", "bitget", "bybit", "okx"]
        return fetch_history_snapshots(venues, bases, req.history_days)

    from backtest.backtest_pure_futures_spread import load_snapshots  # noqa: E402

    jsonl_path = Path(req.jsonl_file) if req.jsonl_file else _DEFAULT_JSONL
    if not jsonl_path.is_absolute():
        jsonl_path = Path(__file__).resolve().parent.parent.parent / jsonl_path
    return load_snapshots(jsonl_path)


def _venues_from_pair_id(pair_id: str) -> tuple[str, str]:
    # pair_id format: BASE:direction:long_venue:short_venue (see _opp_key)
    parts = str(pair_id).split(":")
    if len(parts) >= 4:
        return parts[2], parts[3]
    return "", ""


def _adapt_result(raw: dict[str, Any], req: BacktestRequest) -> dict[str, Any]:
    """Convert BacktestResult.to_dict() output into the UI summary/trades shape."""
    trades = []
    for t in raw.get("trades", []):
        long_v, short_v = _venues_from_pair_id(t.get("pair_id", ""))
        trades.append(
            {
                "base": t.get("base", ""),
                "direction": t.get("direction", ""),
                "long_venue": long_v,
                "short_venue": short_v,
                "open_time": t.get("open_ts", ""),
                "close_time": t.get("close_ts", ""),
                "hold_days": round(float(t.get("holding_hours", 0)) / 24.0, 2),
                "pnl_usd": round(
                    float(t.get("net_pnl_pct", 0)) / 100.0 * req.trade_usd, 2
                ),
                "close_reason": t.get("close_reason", ""),
            }
        )

    equity_curve = raw.get("equity_curve") or []

    return {
        "id": f"bt-{int(time.time())}",
        "params": req.model_dump(),
        "summary": {
            "total_pnl_usd": round(
                float(raw.get("total_return_pct", 0)) / 100.0 * req.capital, 2
            ),
            "total_pnl_pct": raw.get("total_return_pct", 0),
            "annualized_pct": raw.get("annual_return_pct", 0),
            "max_drawdown_pct": raw.get("max_drawdown_pct", 0),
            "sharpe": raw.get("sharpe_ratio", 0),
            "win_rate": float(raw.get("win_rate_pct", 0)) / 100.0,
            "total_trades": raw.get("trade_count", 0),
            "avg_hold_days": round(float(raw.get("avg_holding_hours", 0)) / 24.0, 1),
        },
        "trades": trades,
        "equity_curve": equity_curve,
        "run_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "live": True,
    }


@router.post("/backtest/run")
async def run_backtest(req: BacktestRequest):
    """Run a backtest with given parameters."""
    if _backtest_fn is None:
        return {
            "success": False,
            "error": "Backtest module unavailable",
            "live": False,
        }

    try:
        import asyncio

        loop = asyncio.get_running_loop()

        def _run() -> dict[str, Any]:
            snapshots = _load_snapshots_for_request(req)
            if not snapshots:
                raise RuntimeError(
                    "No snapshots available — provide history_bases (e.g. BTC,ETH) "
                    "or run the scanner with --watch to produce a JSONL file"
                )
            bt = _backtest_fn(
                snapshots,
                initial_capital=req.capital,
                trade_usd=req.trade_usd,
                max_concurrent_pairs=req.max_positions,
                min_spread_pct=req.min_spread,
                exit_edge_pct=req.exit_edge,
                min_edge_pct=req.min_edge_pct,
                max_holding_hours=req.max_holding_hours,
                allow_mismatch=req.allow_mismatch,
                basis_cost_pct=0.05,  # estimated 0.05% mark divergence cost per round-trip
            )
            return _adapt_result(bt.to_dict(), req)

        result = await loop.run_in_executor(None, _run)
        _save_backtest_result(result)
        return {"success": True, "data": result, "live": True}
    except Exception as e:
        return {"success": False, "error": f"Backtest failed: {e}"}


@router.get("/backtest/history")
async def backtest_history():
    """List past backtest results."""
    results_dir = _ensure_results_dir()
    results: list[dict[str, Any]] = []

    for f in sorted(results_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            results.append(data)
        except Exception:
            continue

    if not results:
        return {
            "success": True,
            "data": [],
            "live": _backtest_fn is not None,
        }

    return {"success": True, "data": results, "live": _backtest_fn is not None}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _save_backtest_result(result: dict[str, Any]) -> None:
    results_dir = _ensure_results_dir()
    bt_id = result.get("id", f"bt-{int(time.time())}")
    path = results_dir / f"{bt_id}.json"
    try:
        path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass
