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
_scanning = False

# ---------------------------------------------------------------------------
# Strategy config helpers (thresholds + venues from Settings)
# ---------------------------------------------------------------------------

# Carry / Unified need spot + borrow → CEX only.
CARRY_VENUES = ["binance", "bitget", "bybit", "okx"]
# Pure futures: any venue with a funding provider. HL is a first-class citizen;
# aster / lighter are available but off by default (enable via venue selector).
PURE_DEFAULT_VENUES = ["binance", "bitget", "bybit", "okx", "hyperliquid"]
PURE_ALL_VENUES = PURE_DEFAULT_VENUES + ["aster", "lighter"]


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


def _scan_thresholds() -> tuple[float, float, float]:
    """Return (min_spread, min_edge, max_mark_spread_pct) from saved strategy config."""
    cfg = _strategy_cfg()
    return (
        float(cfg.get("min_spread_annual", 0.03)),
        float(cfg.get("min_edge_annual", 0.01)),
        float(cfg.get("max_mark_spread_pct", 1.0)),
    )


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


def _apply_group_thresholds(
    result: dict[str, Any], min_edge: float, min_edge_1h: float | None
) -> dict[str, Any]:
    """Re-filter scan output so 1h-group pairs can use a lower edge threshold.

    The scan itself runs with the loosest threshold; this keeps a row if it
    passes the regular min_edge, or if both legs are 1h and it passes min_edge_1h.
    """
    if min_edge_1h is None or min_edge_1h >= min_edge:
        return result

    def _keep(row: dict[str, Any]) -> bool:
        edge = float(row.get("net_edge_pct", 0) or 0)
        is_1h = (
            float(row.get("long_interval_h", 8) or 8) <= 1.0
            and float(row.get("short_interval_h", 8) or 8) <= 1.0
        )
        return edge >= (min_edge_1h if is_1h else min_edge)

    out = dict(result)
    out["forward"] = [r for r in result.get("forward", []) if _keep(r)]
    out["reverse"] = [r for r in result.get("reverse", []) if _keep(r)]
    out["total_spreads_found"] = len(out["forward"]) + len(out["reverse"])
    return out


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------


def _mock_pure() -> dict[str, Any]:
    # Field names mirror cli.scan_pure_futures_spreads._scan_spreads output.
    return {
        "venues": CARRY_VENUES,
        "total_assets_scanned": 45,
        "total_spreads_found": 2,
        "forward": [
            {
                "base": "BTC",
                "direction": "forward",
                "long_venue": "binance",
                "short_venue": "bybit",
                "long_rate_pct": 0.0185,
                "short_rate_pct": 0.0623,
                "spread_pct": 0.0438,
                "fee_pct": 0.0088,
                "round_trip_fee_pct": 0.0176,
                "net_edge_pct": 0.0350,
                "annual_apy_pct": 47.9,
                "long_mark": 95010.0,
                "short_mark": 95025.0,
                "mark_spread_pct": 0.0158,
                "long_interval_h": 8.0,
                "short_interval_h": 8.0,
                "settle_mismatch": False,
                "same_interval": True,
            },
        ],
        "reverse": [
            {
                "base": "SOL",
                "direction": "reverse",
                "long_venue": "bybit",
                "short_venue": "binance",
                "long_rate_pct": -0.0680,
                "short_rate_pct": -0.0210,
                "spread_pct": 0.0470,
                "fee_pct": 0.0094,
                "round_trip_fee_pct": 0.0188,
                "net_edge_pct": 0.0376,
                "annual_apy_pct": 51.4,
                "long_mark": 185.21,
                "short_mark": 185.16,
                "mark_spread_pct": 0.0270,
                "long_interval_h": 1.0,
                "short_interval_h": 8.0,
                "settle_mismatch": True,
                "same_interval": False,
            },
        ],
        "venue_pair_stats": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _mock_carry() -> list[dict[str, Any]]:
    return [
        {
            "venue": "binance",
            "total_pairs": 120,
            "forward": [
                {
                    "base": "BTC",
                    "symbol": "BTCUSDT",
                    "rate_pct": 0.0623,
                    "annual_pct": 68.2,
                    "has_spot": True,
                    "net_edge_pct": 0.04,
                    "spot_price": 95000.0,
                    "next_ts": int(time.time() * 1000 + 8 * 3600 * 1000),
                    "interval_h": 8.0,
                },
                {
                    "base": "ETH",
                    "symbol": "ETHUSDT",
                    "rate_pct": 0.0510,
                    "annual_pct": 55.8,
                    "has_spot": True,
                    "net_edge_pct": 0.03,
                    "spot_price": 4200.0,
                    "next_ts": int(time.time() * 1000 + 8 * 3600 * 1000),
                    "interval_h": 8.0,
                },
            ],
            "reverse": [
                {
                    "base": "ARB",
                    "symbol": "ARBUSDT",
                    "rate_pct": -0.0245,
                    "annual_pct": 26.8,
                    "borrowable": True,
                    "net_edge_pct": 0.01,
                    "next_ts": int(time.time() * 1000 + 4 * 3600 * 1000),
                    "interval_h": 4.0,
                },
            ],
        },
        {
            "venue": "bybit",
            "total_pairs": 118,
            "forward": [
                {
                    "base": "SOL",
                    "symbol": "SOLUSDT",
                    "rate_pct": 0.0382,
                    "annual_pct": 41.8,
                    "has_spot": True,
                    "net_edge_pct": 0.02,
                    "spot_price": 185.0,
                    "next_ts": int(time.time() * 1000 + 6 * 3600 * 1000),
                    "interval_h": 8.0,
                },
            ],
            "reverse": [],
        },
    ]


# ---------------------------------------------------------------------------
# Routes: Pure Futures Spread
# ---------------------------------------------------------------------------


@router.get("/scanner/status")
async def scanner_status(
    strategy: str = Query("pure", enum=["pure", "carry", "unified"]),
):
    """Return current scanner status for the requested strategy."""
    live = False
    scanning = _scanning
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
):
    """Return latest opportunities for the requested strategy."""
    if strategy == "pure":
        if _pure_results is not None:
            return {"success": True, "data": _pure_results, "live": True}
        return {"success": True, "data": _mock_pure(), "live": False}

    if strategy == "carry":
        if _carry_results:
            return {
                "success": True,
                "data": _carry_results,
                "live": _scan_carry_fn is not None,
            }
        return {"success": True, "data": _mock_carry(), "live": False}

    if strategy == "unified":
        if _unified_results:
            return {
                "success": True,
                "data": _unified_results,
                "live": _unified_carry_cls is not None,
            }
        return {"success": True, "data": [], "live": False}

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
        _unified_ts, \
        _scanning

    if _scanning:
        return {"success": False, "error": "Scan already in progress"}

    _scanning = True
    try:
        loop = asyncio.get_event_loop()

        min_spread, min_edge, max_mark = _scan_thresholds()

        if strategy == "pure":
            if _scan_pure_fn is None:
                mock = _mock_pure()
                _pure_results = mock
                _pure_ts = time.time()
                return {
                    "success": True,
                    "data": mock,
                    "live": False,
                    "message": "Scanner unavailable, returning mock",
                }
            venue_list = [
                v
                for v in _parse_venue_list(venues, PURE_DEFAULT_VENUES)
                if v in PURE_ALL_VENUES
            ] or list(PURE_DEFAULT_VENUES)
            edge_1h = _min_edge_1h()
            scan_min_edge = (
                min(min_edge, edge_1h) if edge_1h is not None else min_edge
            )

            def _run_pure() -> dict[str, Any]:
                raw = _scan_pure_fn(
                    venues=venue_list,
                    min_spread=min_spread,
                    min_edge=scan_min_edge,
                    max_mark_spread_pct=max_mark,
                )
                return _apply_group_thresholds(raw, min_edge, edge_1h)

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

            def _scan_all_carry() -> list[dict[str, Any]]:
                results = []
                for v in carry_venues:
                    try:
                        r = _scan_carry_fn(
                            venue=v,
                            entry=min_spread,
                            exit_rate=min_edge,
                            universe_min=min_edge,
                            max_workers=8,
                        )
                        # Simplify: keep only forward/reverse candidates + metadata
                        results.append(
                            {
                                "venue": v,
                                "total_pairs": r.get("total_pairs", 0),
                                "forward": r.get("forward_candidates", []),
                                "reverse": r.get("reverse_candidates", []),
                                "spot_fee_pct": r.get("spot_fee_pct", 0),
                                "futures_fee_pct": r.get("futures_fee_pct", 0),
                                "two_leg_fee_pct": r.get("two_leg_fee_pct", 0),
                            }
                        )
                    except Exception as e:
                        results.append(
                            {"venue": v, "error": str(e), "forward": [], "reverse": []}
                        )
                return results

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
                pool = _unified_carry_cls(venues=unified_venues)
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
        _scanning = False


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


async def _broadcast(event: str, data: dict[str, Any]) -> None:
    try:
        from server.main import push_event

        await push_event(event, data)
    except Exception:
        pass
