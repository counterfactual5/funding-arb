#!/usr/bin/env python3
"""Scanner API routes — spread scanning and opportunity discovery."""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(tags=["scanner"])

# Ensure the server's main module is importable for the broadcast helper.
_SERVER_DIR = Path(__file__).resolve().parent.parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

# ---------------------------------------------------------------------------
# Try importing scanners; fall back to None if unavailable.
# ---------------------------------------------------------------------------
_scan_pure_fn = None
try:
    from cli.scan_pure_futures_spreads import scan_pure_futures_spreads  # noqa: E402

    _scan_pure_fn = scan_pure_futures_spreads
except Exception:
    pass

_scan_carry_fn = None
try:
    from cli.scan_funding_arbitrage import scan_venue  # noqa: E402

    _scan_carry_fn = scan_venue
except Exception:
    pass

_unified_carry_cls = None
try:
    from backtest.unified_funding_pool import (  # noqa: E402
        DEFAULT_VENUES,
        UnifiedFundingPool,
    )

    _unified_carry_cls = UnifiedFundingPool
except Exception:
    pass

# ---------------------------------------------------------------------------
# In-memory scan state (per strategy)
# ---------------------------------------------------------------------------

_pure_results: dict[str, Any] | None = None
_pure_ts: float = 0.0
_carry_results: list[dict[str, Any]] = []
_carry_ts: float = 0.0
_unified_results: list[dict[str, Any]] = []
_unified_ts: float = 0.0
# Per-strategy locks so a slow carry/unified scan never blocks pure (and vice versa)
_scanning_strategies: set[str] = set()

# ---------------------------------------------------------------------------
# Strategy config helpers (thresholds + venues from Settings)
# ---------------------------------------------------------------------------

# Carry / Unified need spot + borrow → CEX only.
CARRY_VENUES = ["binance", "bitget", "bybit", "okx"]
# Pure futures: any venue with a funding provider. Defaults are CEX-only;
# DEX venues (hyperliquid / aster / lighter / edgex) opt-in via venue selector.
PURE_DEFAULT_VENUES = ["binance", "bitget", "bybit", "okx"]
PURE_ALL_VENUES = PURE_DEFAULT_VENUES + ["hyperliquid", "aster", "lighter", "edgex"]


def _strategy_cfg() -> dict[str, Any]:
    try:
        from server.routes.settings import _strategy_config  # noqa: E402

        return _strategy_config
    except Exception:
        return {}


def _parse_venue_list(override: str | None, fallback: list[str]) -> list[str]:
    if override:
        return [v.strip().lower() for v in override.split(",") if v.strip()]
    cfg = _strategy_cfg()
    saved = cfg.get("scan_venues")
    if isinstance(saved, list) and saved:
        return [str(v).lower() for v in saved]
    return list(fallback)


def _venue_sets_match(requested: list[str], cached: list[str] | None) -> bool:
    if not cached:
        return False
    return set(requested) == set(cached)


def _scan_thresholds() -> tuple[float, float, float]:
    """Return (min_spread, min_edge, max_mark_spread_pct) from saved strategy config."""
    cfg = _strategy_cfg()
    return (
        float(cfg.get("min_spread_annual", 0.03)),
        float(cfg.get("min_edge_annual", 0.01)),
        float(cfg.get("max_mark_spread_pct", 1.0)),
    )


def _fee_policy() -> dict[str, Any]:
    try:
        from core.fee_providers import parse_fee_policy  # noqa: E402

        return parse_fee_policy(_strategy_cfg())
    except Exception:
        return {"mode": "auto", "venue_tiers": {}}


def _min_edge_1h() -> float | None:
    """Optional lower net-edge threshold for pairs where both legs settle hourly.

    1h-group pairs turn capital over faster, so a lower per-cycle edge can still
    be attractive. Returns None when not configured.
    """
    cfg = _strategy_cfg()
    val = cfg.get("min_edge_1h")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _min_edge_mismatch() -> float | None:
    """Optional higher net-edge threshold for cross-interval (settle_mismatch) pairs.

    When two legs settle on different schedules (e.g. EdgeX 4h vs Binance 8h),
    you carry funding-timing risk between settlements, so demand a larger edge.
    Returns None when not configured (no premium).
    """
    cfg = _strategy_cfg()
    val = cfg.get("min_edge_mismatch")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _recalc_pure_fees(result: dict[str, Any]) -> dict[str, Any]:
    """Recompute per-leg fees and net edge from cached spread using current fee policy."""
    from core.fee_providers import resolve_venue_fee  # noqa: E402

    policy = _fee_policy()
    out = dict(result)
    for section in ("forward", "reverse"):
        updated: list[dict[str, Any]] = []
        for row in result.get(section, []):
            base = str(row.get("base", ""))
            sym = f"{base}USDT"
            long_fee = float(
                resolve_venue_fee(
                    str(row["long_venue"]), leg="futures", symbol=sym, policy=policy
                )["taker_pct"]
            )
            short_fee = float(
                resolve_venue_fee(
                    str(row["short_venue"]), leg="futures", symbol=sym, policy=policy
                )["taker_pct"]
            )
            fee_pct = long_fee + short_fee
            spread = float(row.get("spread_pct", 0) or 0)
            r = dict(row)
            r["long_fee_pct"] = round(long_fee, 4)
            r["short_fee_pct"] = round(short_fee, 4)
            r["fee_pct"] = round(fee_pct, 4)
            r["round_trip_fee_pct"] = round(fee_pct * 2, 4)
            net_edge = spread - fee_pct
            r["net_edge_pct"] = round(net_edge, 6)
            r["real_edge_pct"] = round(
                net_edge - float(row.get("mark_spread_pct", 0) or 0), 6
            )
            updated.append(r)
        updated.sort(key=lambda x: -float(x.get("real_edge_pct", 0) or 0))
        out[section] = updated
    out["total_spreads_found"] = len(out.get("forward", [])) + len(out.get("reverse", []))
    return out


def _recalc_carry_fees(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from core.fee_providers import carry_two_leg_fee_pct, resolve_venue_fee  # noqa: E402

    policy = _fee_policy()
    out: list[dict[str, Any]] = []
    for block in results:
        if block.get("error"):
            out.append(block)
            continue
        venue = str(block.get("venue", ""))
        spot = resolve_venue_fee(venue, leg="spot", policy=policy)
        fut = resolve_venue_fee(venue, leg="futures", policy=policy)
        spot_pct = float(spot["taker_pct"])
        fut_pct = float(fut["taker_pct"])
        two_leg = spot_pct + fut_pct
        nb = dict(block)
        nb["spot_fee_pct"] = round(spot_pct, 4)
        nb["futures_fee_pct"] = round(fut_pct, 4)
        nb["two_leg_fee_pct"] = round(two_leg, 4)
        nb["fee_source"] = fut.get("source", "tier")
        nb["fee_tier"] = fut.get("tier")

        for key in ("forward", "reverse"):
            rows: list[dict[str, Any]] = []
            for row in block.get(key, []):
                sym = str(row.get("symbol", ""))
                from core.fee_providers import normalize_symbol  # noqa: E402

                cache_key = (venue.lower(), normalize_symbol(sym))
                _, _, leg_fee = carry_two_leg_fee_pct(
                    venue,
                    sym,
                    futures_cache={cache_key: {"taker_pct": fut_pct}},
                    spot_cache={cache_key: {"taker_pct": spot_pct}},
                )
                r = dict(row)
                r["spot_fee_pct"] = round(spot_pct, 4)
                r["futures_fee_pct"] = round(fut_pct, 4)
                r["fee_pct"] = round(leg_fee, 4)
                rate = float(row.get("rate_pct", 0) or 0)
                if key == "forward":
                    r["net_edge_pct"] = round(rate - leg_fee, 4)
                else:
                    borrow_period = float(row.get("borrow_per_period_pct", 0) or 0)
                    r["net_edge_pct"] = round(abs(rate) - borrow_period - leg_fee, 4)
                rows.append(r)
            nb[key] = rows
        out.append(nb)
    return out


def _recalc_unified_fees(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from core.fee_providers import resolve_venue_fee  # noqa: E402

    policy = _fee_policy()
    out: list[dict[str, Any]] = []
    for row in results:
        base = str(row.get("base", ""))
        sym = f"{base}USDT"
        fv = str(row.get("futures_venue", ""))
        sv = str(row.get("spot_venue", fv))
        spot_pct = float(
            resolve_venue_fee(sv, leg="spot", symbol=sym, policy=policy)["taker_pct"]
        )
        fut_pct = float(
            resolve_venue_fee(fv, leg="futures", symbol=sym, policy=policy)[
                "taker_pct"
            ]
        )
        new_fee = spot_pct + fut_pct
        old_fee = float(row.get("fee_pct", 0) or 0)
        old_net = float(row.get("net_edge_pct", 0) or 0)
        r = dict(row)
        r["spot_fee_pct"] = round(spot_pct, 4)
        r["futures_fee_pct"] = round(fut_pct, 4)
        r["fee_pct"] = round(new_fee, 4)
        r["net_edge_pct"] = round(old_net + old_fee - new_fee, 4)
        out.append(r)
    out.sort(key=lambda x: -(x.get("net_edge_pct") or 0))
    return out


def _row_real_edge(row: dict[str, Any]) -> float:
    """Basis-adjusted edge used for filtering/ranking.

    real_edge = net funding edge − cross-venue mark divergence. A pair whose
    funding edge is offset by a dislocated mark price (high mark_spread) is not
    a clean opportunity, so it is judged on this discounted figure. Falls back
    to net_edge − mark_spread when the field is absent (older cached scans).
    """
    re = row.get("real_edge_pct")
    if re is not None:
        return float(re)
    return float(row.get("net_edge_pct", 0) or 0) - float(
        row.get("mark_spread_pct", 0) or 0
    )


def _row_edge_threshold(
    row: dict[str, Any],
    min_edge: float,
    min_edge_1h: float | None,
    min_edge_mismatch: float | None,
) -> float:
    """Per-row net-edge bar by settlement-interval group."""
    from core.strategy_config import row_edge_threshold  # noqa: E402

    return row_edge_threshold(row, min_edge, min_edge_1h, min_edge_mismatch)


def _apply_group_thresholds(
    result: dict[str, Any],
    min_edge: float,
    min_edge_1h: float | None,
    min_edge_mismatch: float | None = None,
) -> dict[str, Any]:
    """Re-filter scan output on basis-adjusted real edge with per-interval-group
    thresholds.

    The scan runs with the loosest net-edge threshold; this is the primary gate:
    each row must clear its threshold on REAL edge (net edge − mark divergence),
    so funding edges sitting on a dislocated mark price are dropped. 1h-group
    pairs may use the lower min_edge_1h; cross-interval (settle_mismatch) pairs
    must clear the higher min_edge_mismatch risk premium.
    """

    def _keep(row: dict[str, Any]) -> bool:
        return _row_real_edge(row) >= _row_edge_threshold(
            row, min_edge, min_edge_1h, min_edge_mismatch
        )

    out = dict(result)
    out["forward"] = [r for r in result.get("forward", []) if _keep(r)]
    out["reverse"] = [r for r in result.get("reverse", []) if _keep(r)]
    out["total_spreads_found"] = len(out["forward"]) + len(out["reverse"])
    return out


def _empty_pure() -> dict[str, Any]:
    """Empty scan result shape (no placeholder rows)."""
    return {
        "venues": [],
        "total_assets_scanned": 0,
        "total_spreads_found": 0,
        "forward": [],
        "reverse": [],
        "venue_pair_stats": [],
        "timestamp": None,
    }


def _scanner_available(strategy: str) -> bool:
    if strategy == "pure":
        return _scan_pure_fn is not None
    if strategy == "carry":
        return _scan_carry_fn is not None
    if strategy == "unified":
        return _unified_carry_cls is not None
    return False


# ---------------------------------------------------------------------------
# Routes: Pure Futures Spread
# ---------------------------------------------------------------------------


@router.get("/scanner/status")
async def scanner_status(
    strategy: str = Query("pure", enum=["pure", "carry", "unified"]),
):
    """Return current scanner status for the requested strategy."""
    live = False
    scanning = strategy in _scanning_strategies
    has_data = False
    last_ts = 0.0

    if strategy == "pure":
        live = _scan_pure_fn is not None
        has_data = _pure_results is not None
        last_ts = _pure_ts
    elif strategy == "carry":
        live = _scan_carry_fn is not None
        has_data = len(_carry_results) > 0
        last_ts = _carry_ts
    elif strategy == "unified":
        live = _unified_carry_cls is not None
        has_data = len(_unified_results) > 0
        last_ts = _unified_ts

    ts = (
        datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat()
        if last_ts
        else None
    )
    return {
        "success": True,
        "data": {
            "scanning": scanning,
            "last_scan_time": ts,
            "has_data": has_data,
            "live": live,
        },
    }


@router.get("/scanner/opportunities")
async def scanner_opportunities(
    strategy: str = Query("pure", enum=["pure", "carry", "unified"]),
    venues: str | None = Query(
        None, description="Comma-separated venue ids; cache must match to return data"
    ),
):
    """Return latest cached scan results (empty until a real scan completes)."""
    available = _scanner_available(strategy)
    req_venues = (
        [v.strip().lower() for v in venues.split(",") if v.strip()] if venues else None
    )

    if strategy == "pure":
        if _pure_results is not None:
            cached_v = _pure_results.get("venues") or []
            if req_venues is not None and not _venue_sets_match(req_venues, cached_v):
                return {
                    "success": True,
                    "data": _empty_pure(),
                    "live": available,
                    "has_data": False,
                    "venues_mismatch": True,
                }
            return {
                "success": True,
                "data": _pure_results,
                "live": available,
                "has_data": True,
            }
        return {
            "success": True,
            "data": _empty_pure(),
            "live": available,
            "has_data": False,
        }

    if strategy == "carry":
        if _carry_results:
            cached_v = [r.get("venue", "") for r in _carry_results if r.get("venue")]
            if req_venues is not None and not _venue_sets_match(req_venues, cached_v):
                return {
                    "success": True,
                    "data": [],
                    "live": available,
                    "has_data": False,
                    "venues_mismatch": True,
                }
            return {
                "success": True,
                "data": _carry_results,
                "live": available,
                "has_data": True,
            }
        return {"success": True, "data": [], "live": available, "has_data": False}

    if strategy == "unified":
        if _unified_results:
            return {
                "success": True,
                "data": _unified_results,
                "live": available,
                "has_data": True,
            }
        return {"success": True, "data": [], "live": available, "has_data": False}

    return {"success": False, "error": f"Unknown strategy: {strategy}"}


@router.post("/scanner/trigger")
async def scanner_trigger(
    strategy: str = Query("pure", enum=["pure", "carry", "unified"]),
    venues: str | None = Query(
        None, description="Comma-separated venue ids (overrides saved scan_venues)"
    ),
):
    """Trigger a scan for the specified strategy."""
    global \
        _pure_results, \
        _pure_ts, \
        _carry_results, \
        _carry_ts, \
        _unified_results, \
        _unified_ts

    # When called directly from Python (background loop / scan-all) instead of
    # via HTTP, unsupplied params arrive as FastAPI Query objects — normalize.
    if not isinstance(strategy, str):
        strategy = "pure"
    if not isinstance(venues, str):
        venues = None

    if strategy in _scanning_strategies:
        return {"success": False, "error": "Scan already in progress"}

    _scanning_strategies.add(strategy)
    try:
        loop = asyncio.get_event_loop()

        min_spread, min_edge, max_mark = _scan_thresholds()

        if strategy == "pure":
            if _scan_pure_fn is None:
                return {
                    "success": False,
                    "error": "Pure futures scanner unavailable",
                    "live": False,
                }
            venue_list = [
                v
                for v in _parse_venue_list(venues, PURE_DEFAULT_VENUES)
                if v in PURE_ALL_VENUES
            ] or list(PURE_DEFAULT_VENUES)
            edge_1h = _min_edge_1h()
            edge_mismatch = _min_edge_mismatch()
            scan_min_edge = (
                min(min_edge, edge_1h) if edge_1h is not None else min_edge
            )

            def _run_pure() -> dict[str, Any]:
                raw = _scan_pure_fn(
                    venues=venue_list,
                    min_spread=min_spread,
                    min_edge=scan_min_edge,
                    max_mark_spread_pct=max_mark,
                    fee_policy=_fee_policy(),
                )
                return _apply_group_thresholds(raw, min_edge, edge_1h, edge_mismatch)

            result = await loop.run_in_executor(None, _run_pure)
            _pure_results = result
            _pure_ts = time.time()
            await _broadcast("scanner.update", result)
            return {"success": True, "data": result, "live": True}

        elif strategy == "carry":
            if _scan_carry_fn is None:
                return {"success": False, "error": "Cash-and-carry scanner unavailable"}

            # Scan each venue in parallel via thread pool
            # Carry needs spot + borrow — drop perp-only venues (HL/aster/lighter)
            carry_venues = [
                v
                for v in _parse_venue_list(venues, CARRY_VENUES)
                if v in CARRY_VENUES
            ] or list(CARRY_VENUES)

            def _scan_one_carry(v: str) -> dict[str, Any]:
                try:
                    r = _scan_carry_fn(
                        venue=v,
                        entry=min_spread,
                        exit_rate=min_edge,
                        universe_min=min_edge,
                        max_workers=8,
                        fee_policy=_fee_policy(),
                    )
                    # Simplify: keep only forward/reverse candidates + metadata
                    return {
                        "venue": v,
                        "total_pairs": r.get("total_pairs", 0),
                        "forward": r.get("forward_candidates", []),
                        "reverse": r.get("reverse_candidates", []),
                        "spot_fee_pct": r.get("spot_fee_pct", 0),
                        "futures_fee_pct": r.get("futures_fee_pct", 0),
                        "two_leg_fee_pct": r.get("two_leg_fee_pct", 0),
                        "fee_source": r.get("fee_source"),
                        "fee_tier": r.get("fee_tier"),
                    }
                except Exception as e:
                    return {"venue": v, "error": str(e), "forward": [], "reverse": []}

            def _scan_all_carry() -> list[dict[str, Any]]:
                # Venues scan concurrently — each one already parallelizes its own IO
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=len(carry_venues)) as pool:
                    return list(pool.map(_scan_one_carry, carry_venues))

            result = await loop.run_in_executor(None, _scan_all_carry)
            _carry_results = result
            _carry_ts = time.time()
            await _broadcast("scanner.update", {"strategy": "carry", "data": result})
            return {"success": True, "data": result, "live": True}

        elif strategy == "unified":
            if _unified_carry_cls is None:
                return {"success": False, "error": "Unified carry scanner unavailable"}

            unified_venues = [
                v
                for v in _parse_venue_list(venues, list(DEFAULT_VENUES))
                if v in CARRY_VENUES
            ] or list(DEFAULT_VENUES)

            def _scan_unified() -> list[dict[str, Any]]:
                pool = _unified_carry_cls(
                    venues=unified_venues, fee_policy=_fee_policy()
                )
                pool.refresh()
                # scan_routes returns {"forward": [...], "reverse": [...]}, not base→routes
                grouped = pool.scan_routes(
                    entry=min_spread, universe_min=min_edge
                )
                result = []
                for routes in grouped.values():
                    for route in routes:
                        result.append(
                            {
                                "base": route.base,
                                "direction": route.direction,
                                "futures_venue": route.futures_venue,
                                "spot_venue": route.spot_venue
                                if not route.same_venue
                                else route.futures_venue,
                                "same_venue": route.same_venue,
                                "funding_rate_pct": round(route.funding_rate_pct, 4),
                                "annual_pct": round(route.annual_funding_pct, 1),
                                "spot_fee_pct": round(route.spot_fee_pct, 4),
                                "futures_fee_pct": round(route.futures_fee_pct, 4),
                                "fee_pct": round(route.total_fee_pct, 4),
                                "net_edge_pct": round(route.net_edge_pct, 4),
                                "borrow_annual_pct": round(route.borrow_annual_pct, 2),
                            }
                        )

                result.sort(key=lambda x: -(x["net_edge_pct"] or 0))
                return result[:100]

            result = await loop.run_in_executor(None, _scan_unified)
            _unified_results = result
            _unified_ts = time.time()
            await _broadcast("scanner.update", {"strategy": "unified", "data": result})
            return {"success": True, "data": result, "live": True}

    except Exception as e:
        return {"success": False, "error": f"Scan failed: {e}"}
    finally:
        _scanning_strategies.discard(strategy)


@router.post("/scanner/scan-all")
async def scanner_scan_all():
    """Trigger all scanner types and return combined results."""
    results: dict[str, Any] = {}

    # Pure futures (existing)
    try:
        r = await scanner_trigger(strategy="pure")
        results["pure"] = r.get("data") if r.get("success") else None
    except Exception:
        results["pure"] = None

    # Cash and carry
    try:
        r = await scanner_trigger(strategy="carry")
        results["carry"] = r.get("data") if r.get("success") else None
    except Exception:
        results["carry"] = None

    # Unified cross-venue
    try:
        r = await scanner_trigger(strategy="unified")
        results["unified"] = r.get("data") if r.get("success") else None
    except Exception:
        results["unified"] = None

    return {"success": True, "data": results}


@router.post("/scanner/recalc-fees")
async def scanner_recalc_fees():
    """Recompute net edge on cached scan results using current fee policy (no re-scan)."""
    global _pure_results, _carry_results, _unified_results

    min_spread, min_edge, _ = _scan_thresholds()
    edge_1h = _min_edge_1h()
    edge_mismatch = _min_edge_mismatch()
    updated: dict[str, Any] = {}

    if _pure_results is not None:
        recalc = _recalc_pure_fees(_pure_results)
        _pure_results = _apply_group_thresholds(
            recalc, min_edge, edge_1h, edge_mismatch
        )
        updated["pure"] = _pure_results

    if _carry_results:
        _carry_results = _recalc_carry_fees(_carry_results)
        updated["carry"] = _carry_results

    if _unified_results:
        _unified_results = _recalc_unified_fees(_unified_results)
        updated["unified"] = _unified_results

    if not updated:
        return {"success": False, "error": "No cached scan results to recalculate"}

    await _broadcast("scanner.update", {"recalc_fees": True, "data": updated})
    return {"success": True, "data": updated}


async def _broadcast(event: str, data: dict[str, Any]) -> None:
    try:
        from server.main import push_event

        await push_event(event, data)
    except Exception:
        pass
