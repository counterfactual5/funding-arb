#!/usr/bin/env python3
"""Position management API routes."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["positions"])

StrategyKind = Literal["pure_futures", "carry", "unified"]

# ---------------------------------------------------------------------------
# Try importing real position loader / executor
# ---------------------------------------------------------------------------
_load_positions_fn = None
_close_fn = None
_load_cross_positions_fn = None
_open_cross_fn = None
_close_cross_fn = None
try:
    from execution.pure_futures_executor import (  # noqa: E402
        close_pure_futures_pair,
        load_pure_futures_positions,
    )

    _load_positions_fn = load_pure_futures_positions
    _close_fn = close_pure_futures_pair
except Exception:
    pass

try:
    from execution.cross_venue_executor import (  # noqa: E402
        close_cross_venue_position,
        load_positions as load_cross_venue_positions,
        open_cross_venue_position,
    )

    _load_cross_positions_fn = load_cross_venue_positions
    _open_cross_fn = open_cross_venue_position
    _close_cross_fn = close_cross_venue_position
except Exception:
    pass

# ---------------------------------------------------------------------------
# Position file paths
# ---------------------------------------------------------------------------
_POSITIONS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "data"
    / "pure-futures"
    / "positions.json"
)
_PURE_FUTURES_TEMPLATE = (
    Path(__file__).resolve().parent.parent.parent
    / "templates"
    / "config.pure_futures.spread.json"
)


def _read_pure_positions() -> list[dict[str, Any]]:
    """Read pure-futures positions from file, using the real loader if available."""
    if _load_positions_fn is not None:
        try:
            rows = _load_positions_fn()
            for p in rows:
                p.setdefault("strategy", "pure_futures")
            return rows
        except Exception:
            pass
    if not _POSITIONS_PATH.exists():
        return []
    try:
        data = json.loads(_POSITIONS_PATH.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else []
        for p in rows:
            p.setdefault("strategy", "pure_futures")
        return rows
    except Exception:
        return []


def _read_cross_positions() -> list[dict[str, Any]]:
    if _load_cross_positions_fn is None:
        return []
    try:
        rows = _load_cross_positions_fn()
        for p in rows:
            p.setdefault("strategy", "carry" if p.get("futures_venue") == p.get("spot_venue") else "unified")
        return rows
    except Exception:
        return []


def _read_positions() -> list[dict[str, Any]]:
    return _read_pure_positions() + _read_cross_positions()


# ---------------------------------------------------------------------------
# Funding income estimate
# ---------------------------------------------------------------------------
# /positions is polled frequently, so cache funding rates per venue to avoid
# hammering upstream APIs. Value: (timestamp, {symbol: rate dict}).
_FUNDING_CACHE: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
_FUNDING_CACHE_TTL = 60.0


def _get_cached_funding(venue: str, symbol: str) -> dict[str, Any]:
    """Return the cached funding-rate dict for (venue, symbol), refreshing at
    most every _FUNDING_CACHE_TTL seconds. Falls back to a stale value when a
    fresh fetch fails, so PnL enrichment degrades gracefully."""
    now = time.time()
    entry = _FUNDING_CACHE.get(venue)
    if entry is not None:
        ts, rates = entry
        if (now - ts) < _FUNDING_CACHE_TTL and symbol in rates:
            return rates[symbol]
    try:
        from backtest.funding_providers import get_funding_provider  # noqa: E402

        fp = get_funding_provider(venue)
        fresh = fp.fetch_current(symbol) if fp else {}
    except Exception:
        # Fetch failed: serve stale value if we have one, else empty.
        if entry is not None and symbol in entry[1]:
            return entry[1][symbol]
        return {}
    rates = entry[1] if entry is not None else {}
    rates[symbol] = fresh
    _FUNDING_CACHE[venue] = (now, rates)
    return fresh


def _estimate_funding_income(
    pos: dict[str, Any], qty: float, long_mark: float, short_mark: float
) -> tuple[float, float]:
    """Estimate cumulative funding income (USD) for an open pure-futures pair.

    Uses current funding rates as an approximation of realized income; real
    settled income would require querying each venue's funding ledger.

    Returns (estimated_funding_usd, current_spread_annualized_pct).
    """
    base = str(pos.get("base", "")).upper()
    long_id = str(pos.get("long_venue", ""))
    short_id = str(pos.get("short_venue", ""))
    opened_at = int(pos.get("opened_at", 0) or 0)
    if not base or not long_id or not short_id or opened_at <= 0:
        return 0.0, 0.0

    symbol = f"{base}USDT"
    long_data = _get_cached_funding(long_id, symbol)
    short_data = _get_cached_funding(short_id, symbol)
    long_rate_pct = float(long_data.get("rate_pct", 0) or 0)
    short_rate_pct = float(short_data.get("rate_pct", 0) or 0)

    # Funding convention: positive rate => longs pay shorts.
    # forward (long @ long_venue, short @ short_venue):
    #   pay long_rate, receive short_rate  => net = short_rate - long_rate
    # reverse (long @ short_venue, short @ long_venue):
    #   pay short_rate, receive long_rate  => net = long_rate - short_rate
    direction = str(pos.get("direction", "forward")).lower()
    if direction == "reverse":
        net_rate_pct = long_rate_pct - short_rate_pct
    else:
        net_rate_pct = short_rate_pct - long_rate_pct

    # Reference interval: prefer long leg, fall back to short, then default 8h.
    long_interval_ms = int(long_data.get("interval_ms", 0) or 0)
    short_interval_ms = int(short_data.get("interval_ms", 0) or 0)
    interval_ms = long_interval_ms or short_interval_ms or (8 * 60 * 60 * 1000)
    interval_h = interval_ms / (60 * 60 * 1000.0)

    now_ms = int(time.time() * 1000)
    held_hours = max(0.0, (now_ms - opened_at) / 3600000.0)
    periods = held_hours / interval_h if interval_h > 0 else 0.0

    periods_per_year = (365.0 * 24.0) / interval_h if interval_h > 0 else 0.0
    spread_annual = net_rate_pct * periods_per_year

    # Notional ~= avg mark price * qty (use whichever leg has a price).
    if long_mark > 0 and short_mark > 0:
        avg_mark = (long_mark + short_mark) / 2.0
    else:
        avg_mark = long_mark or short_mark or 0.0
    notional_usd = avg_mark * qty

    funding_income_usd = (net_rate_pct / 100.0) * notional_usd * periods
    return round(funding_income_usd, 2), round(spread_annual, 2)


def _enrich_positions_with_pnl(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach unrealized PnL from live mark prices for open pure-futures positions."""
    open_pos = [
        p
        for p in positions
        if p.get("status") == "open"
        and p.get("qty")
        and p.get("strategy", "pure_futures") == "pure_futures"
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
        if p.get("status") != "open" or p.get("strategy", "pure_futures") != "pure_futures":
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
        funding_income, spread_annual = _estimate_funding_income(p, qty, long_mark, short_mark)
        p["funding_pnl_est_usd"] = funding_income
        p["funding_rate_spread_pct"] = spread_annual
        p["total_pnl_usd"] = round(unrealized + funding_income, 2)
        enriched.append(p)
    return enriched


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class OpenPositionRequest(BaseModel):
    strategy: StrategyKind = Field(
        "pure_futures",
        description="pure_futures | carry | unified",
    )
    base: str = Field(..., description="Base asset of the trading pair, e.g. BTC")
    amount_usd: float = Field(..., gt=0, description="Position size (USD)")
    direction: str = Field("forward", description="forward | reverse")
    dry_run: bool = Field(True, description="Whether to simulate opening a position")
    long_venue: str | None = Field(None, description="Pure futures long venue")
    short_venue: str | None = Field(None, description="Pure futures short venue")
    futures_venue: str | None = Field(None, description="Carry/unified perp venue")
    spot_venue: str | None = Field(None, description="Carry/unified spot venue")


class ClosePositionRequest(BaseModel):
    reason: str = Field("", description="Reason for closing the position")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_venues_tradeable(venue_ids: list[str], *, live: bool) -> str | None:
    try:
        from server.routes.settings import venue_live_ready, venue_trade_capability  # noqa: E402
    except ImportError:
        return None

    for vid in venue_ids:
        capable, reason = venue_trade_capability(vid)
        if not capable:
            return f"venue {vid!r} is scan-only, cannot trade: {reason}"
        if live:
            ready, live_reason = venue_live_ready(vid)
            if not ready:
                return f"venue {vid!r} not ready for live trading: {live_reason}"
    return None


def _executor_config() -> tuple[dict[str, Any], float]:
    """Build pure-futures executor config from template + Dashboard strategy settings."""
    cfg: dict[str, Any] = {}
    if _PURE_FUTURES_TEMPLATE.exists():
        try:
            cfg = json.loads(_PURE_FUTURES_TEMPLATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        from core.strategy_config import apply_strategy_to_pure_futures_cfg  # noqa: E402

        cfg = apply_strategy_to_pure_futures_cfg(cfg)
    except Exception:
        pass
    pfa = cfg.get("pureFuturesArbitrage") or {}
    max_mark = float(pfa.get("maxMarkSpreadPct") or 1.0)
    return cfg, max_mark


def _position_kind(position_id: str, positions: list[dict[str, Any]]) -> str:
    for pos in positions:
        if pos.get("id") == position_id:
            return str(pos.get("strategy") or "pure_futures")
    if position_id.startswith("xv-"):
        return "carry"
    return "pure_futures"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/positions")
async def list_positions():
    """List all positions (open + closed)."""
    positions = _read_positions()
    live = _load_positions_fn is not None or _load_cross_positions_fn is not None
    if live and positions:
        positions = _enrich_positions_with_pnl(positions)
    return {"success": True, "data": positions, "live": live}


@router.get("/positions/{position_id}")
async def get_position(position_id: str):
    """Get a single position by ID."""
    positions = _read_positions()
    live = _load_positions_fn is not None or _load_cross_positions_fn is not None

    for pos in positions:
        if pos.get("id") == position_id:
            return {"success": True, "data": pos, "live": live}

    raise HTTPException(status_code=404, detail=f"Position {position_id} not found")


@router.post("/positions/open")
async def open_position(req: OpenPositionRequest):
    """Open a new hedge position (pure futures, cash-and-carry, or unified C&C)."""
    strategy = req.strategy
    direction = str(req.direction or "forward").lower()
    if direction not in ("forward", "reverse"):
        return {"success": False, "error": f"invalid direction {req.direction!r}"}

    if strategy == "pure_futures":
        if not req.long_venue or not req.short_venue:
            return {
                "success": False,
                "error": "long_venue and short_venue required for pure_futures",
            }
        venue_err = _check_venues_tradeable(
            [req.long_venue, req.short_venue], live=not req.dry_run
        )
        if venue_err:
            return {"success": False, "error": venue_err}
        if _load_positions_fn is None:
            return {"success": False, "error": "Executor module unavailable", "live": False}
        try:
            import asyncio

            from execution.pure_futures_executor import open_pure_futures_pair  # noqa: E402

            exec_config, max_mark = _executor_config()
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: open_pure_futures_pair(
                    req.base,
                    req.long_venue,
                    req.short_venue,
                    req.amount_usd,
                    direction=direction,
                    dry_run=req.dry_run,
                    max_mark_spread_pct=max_mark,
                    config=exec_config,
                ),
            )
            return _format_open_result(result)
        except Exception as e:
            return {"success": False, "error": f"Failed to open position: {e}"}

    if strategy in ("carry", "unified"):
        futures_v = (req.futures_venue or "").strip().lower()
        spot_v = (req.spot_venue or "").strip().lower()
        if not futures_v or not spot_v:
            return {
                "success": False,
                "error": "futures_venue and spot_venue required for carry/unified",
            }
        venue_err = _check_venues_tradeable(
            [futures_v, spot_v], live=not req.dry_run
        )
        if venue_err:
            return {"success": False, "error": venue_err}
        if _open_cross_fn is None:
            return {
                "success": False,
                "error": "Cross-venue executor unavailable",
                "live": False,
            }
        try:
            import asyncio

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: _open_cross_fn(
                    req.base,
                    direction,  # type: ignore[arg-type]
                    futures_v,
                    spot_v,
                    req.amount_usd,
                    dry_run=req.dry_run,
                ),
            )
            return _format_open_result(result)
        except Exception as e:
            return {"success": False, "error": f"Failed to open position: {e}"}

    return {"success": False, "error": f"unknown strategy {strategy!r}"}


def _format_open_result(result: Any) -> dict[str, Any]:
    data = result.to_dict() if hasattr(result, "to_dict") else result
    ok = bool(getattr(result, "ok", True))
    resp: dict[str, Any] = {"success": ok, "data": data, "live": True}
    if not ok:
        logs = getattr(result, "logs", None) or []
        resp["error"] = "; ".join(str(x) for x in logs[-3:]) or "open aborted"
    return resp


@router.post("/positions/{position_id}/close")
async def close_position(position_id: str, req: ClosePositionRequest | None = None):
    """Close a position by ID."""
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

    kind = _position_kind(position_id, positions)
    # Close in the same mode the position was opened in. Never escalate a
    # dry-run (paper) position to a live close based on a global env flag —
    # there is no real exchange position behind it, so a live order would be wrong.
    dry_run = bool(target.get("dry_run", True))

    try:
        import asyncio

        loop = asyncio.get_running_loop()

        if kind in ("carry", "unified") and _close_cross_fn is not None:
            result = await loop.run_in_executor(
                None,
                lambda: _close_cross_fn(position_id, dry_run=dry_run),
            )
        elif _close_fn is not None:
            result = await loop.run_in_executor(
                None,
                lambda: _close_fn(position_id),
            )
        else:
            return {
                "success": False,
                "error": "Executor module unavailable, cannot close position",
                "live": False,
            }

        data = result.to_dict() if hasattr(result, "to_dict") else result
        ok = bool(getattr(result, "ok", True))
        resp: dict[str, Any] = {"success": ok, "data": data, "live": True}
        if not ok:
            logs = getattr(result, "logs", None) or []
            resp["error"] = "; ".join(str(x) for x in logs[-3:]) or "close aborted"
        return resp
    except Exception as e:
        return {"success": False, "error": f"Failed to close position: {e}"}
