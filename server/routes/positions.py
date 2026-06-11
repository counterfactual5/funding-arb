#!/usr/bin/env python3
"""Position management API routes."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["positions"])

# ---------------------------------------------------------------------------
# Try importing real position loader / executor
# ---------------------------------------------------------------------------
_load_positions_fn = None
_close_fn = None
try:
    from execution.pure_futures_executor import (  # noqa: E402
        close_pure_futures_pair,
        load_pure_futures_positions,
    )

    _load_positions_fn = load_pure_futures_positions
    _close_fn = close_pure_futures_pair
except Exception:
    pass

# ---------------------------------------------------------------------------
# Position file path (matches execution/pure_futures_executor.py)
# ---------------------------------------------------------------------------
_POSITIONS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "data"
    / "pure-futures"
    / "positions.json"
)


def _read_positions() -> list[dict[str, Any]]:
    """Read positions from file, using the real loader if available."""
    if _load_positions_fn is not None:
        try:
            return _load_positions_fn()
        except Exception:
            pass
    # Fallback: read directly
    if not _POSITIONS_PATH.exists():
        return []
    try:
        data = json.loads(_POSITIONS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _enrich_positions_with_pnl(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach unrealized PnL from live mark prices for open positions."""
    open_pos = [
        p for p in positions if p.get("status") == "open" and p.get("qty")
    ]
    if not open_pos:
        return positions

    get_mark = None
    try:
        from execution.pure_futures_watcher import _get_mark_price  # noqa: E402

        get_mark = _get_mark_price
    except Exception:
        return positions

    venue_ids = {str(p["long_venue"]) for p in open_pos} | {
        str(p["short_venue"]) for p in open_pos
    }
    try:
        from execution.pure_futures_watcher import _prefetch_all_mark_prices  # noqa: E402

        _prefetch_all_mark_prices(venue_ids)
    except Exception:
        pass

    enriched: list[dict[str, Any]] = []
    for pos in positions:
        p = dict(pos)
        if p.get("status") != "open":
            enriched.append(p)
            continue
        qty = float(p.get("qty") or 0)
        long_open = float(p.get("long_price") or 0)
        short_open = float(p.get("short_price") or 0)
        if qty <= 0 or (long_open <= 0 and short_open <= 0):
            enriched.append(p)
            continue
        base = str(p.get("base", ""))
        long_mark = get_mark(str(p["long_venue"]), base) if get_mark else 0.0
        short_mark = get_mark(str(p["short_venue"]), base) if get_mark else 0.0
        long_pnl = (long_mark - long_open) * qty if long_open > 0 else 0.0
        short_pnl = (short_open - short_mark) * qty if short_open > 0 else 0.0
        unrealized = round(long_pnl + short_pnl, 2)
        p["unrealized_pnl_usd"] = unrealized
        p["pnl_usd"] = unrealized
        if long_mark > 0 and short_mark > 0:
            p["mark_spread_pct"] = round(
                abs(long_mark - short_mark) / max(long_mark, short_mark) * 100.0, 4
            )
        enriched.append(p)
    return enriched


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class OpenPositionRequest(BaseModel):
    base: str = Field(..., description="Base asset of the trading pair, e.g. BTC")
    long_venue: str = Field(..., description="Long venue")
    short_venue: str = Field(..., description="Short venue")
    amount_usd: float = Field(..., gt=0, description="Position size (USD)")
    direction: str = Field("forward", description="forward | reverse")
    dry_run: bool = Field(True, description="Whether to simulate opening a position")


class ClosePositionRequest(BaseModel):
    reason: str = Field("", description="Reason for closing the position")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/positions")
async def list_positions():
    """List all positions (open + closed)."""
    positions = _read_positions()
    live = _load_positions_fn is not None
    if live and positions:
        positions = _enrich_positions_with_pnl(positions)
    return {"success": True, "data": positions, "live": live}


@router.get("/positions/{position_id}")
async def get_position(position_id: str):
    """Get a single position by ID."""
    positions = _read_positions()
    live = _load_positions_fn is not None

    for pos in positions:
        if pos.get("id") == position_id:
            return {"success": True, "data": pos, "live": live}

    raise HTTPException(status_code=404, detail=f"Position {position_id} not found")


def _executor_config() -> tuple[dict[str, Any], float]:
    """Build executor config (depth check etc.) from persisted strategy params.

    Returns (config, max_mark_spread_pct).
    """
    strategy: dict[str, Any] = {}
    try:
        from server.routes.settings import _load_strategy_config  # noqa: E402

        strategy = _load_strategy_config()
    except Exception:
        pass
    max_mark = float(strategy.get("max_mark_spread_pct") or 1.0)
    config = {
        "pureFuturesArbitrage": {
            "depthCheckEnabled": True,
            "depthMaxDevPct": 0.3,
            "depthMinMultiple": 3.0,
            # DEX orderbooks can be thin/flaky: a failed depth fetch blocks the open
            "depthCheckFailOpen": False,
        }
    }
    return config, max_mark


@router.post("/positions/open")
async def open_position(req: OpenPositionRequest):
    """Open a new spread position."""
    # Reject venues the executor cannot route orders to (scan-only venues).
    try:
        from server.routes.settings import venue_live_ready, venue_trade_capability  # noqa: E402

        for vid in (req.long_venue, req.short_venue):
            capable, reason = venue_trade_capability(vid)
            if not capable:
                return {
                    "success": False,
                    "error": f"venue {vid!r} is scan-only, cannot trade: {reason}",
                }
            if not req.dry_run:
                ready, live_reason = venue_live_ready(vid)
                if not ready:
                    return {
                        "success": False,
                        "error": f"venue {vid!r} not ready for live trading: {live_reason}",
                    }
    except ImportError:
        pass

    if _load_positions_fn is None:
        return {
            "success": False,
            "error": "Executor module unavailable",
            "live": False,
        }

    try:
        import asyncio

        from execution.pure_futures_executor import open_pure_futures_pair  # noqa: E402

        exec_config, max_mark = _executor_config()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: open_pure_futures_pair(
                req.base,
                req.long_venue,
                req.short_venue,
                req.amount_usd,
                direction=req.direction,
                dry_run=req.dry_run,
                max_mark_spread_pct=max_mark,
                config=exec_config,
            ),
        )
        data = result.to_dict() if hasattr(result, "to_dict") else result
        ok = bool(getattr(result, "ok", True))
        resp: dict[str, Any] = {"success": ok, "data": data, "live": True}
        if not ok:
            logs = getattr(result, "logs", None) or []
            resp["error"] = "; ".join(str(x) for x in logs[-3:]) or "open aborted"
        return resp
    except Exception as e:
        return {"success": False, "error": f"Failed to open position: {e}"}


@router.post("/positions/{position_id}/close")
async def close_position(position_id: str, req: ClosePositionRequest | None = None):
    """Close a position by ID."""
    if _close_fn is None:
        return {
            "success": False,
            "error": "Executor module unavailable, cannot close position",
            "live": False,
        }

    positions = _read_positions()
    target = None
    for pos in positions:
        if pos.get("id") == position_id:
            target = pos
            break

    if target is None:
        raise HTTPException(status_code=404, detail=f"Position {position_id} not found")

    if target.get("status") == "closed":
        return {"success": False, "error": "Position already closed"}

    try:
        import asyncio

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _close_fn(position_id),
        )
        data = result.to_dict() if hasattr(result, "to_dict") else result
        ok = bool(getattr(result, "ok", True))
        resp: dict[str, Any] = {"success": ok, "data": data, "live": True}
        if not ok:
            logs = getattr(result, "logs", None) or []
            resp["error"] = "; ".join(str(x) for x in logs[-3:]) or "close aborted"
        return resp
    except Exception as e:
        return {"success": False, "error": f"Failed to close position: {e}"}
