#!/usr/bin/env python3
"""Export scanner results as a static snapshot for the Vercel demo dashboard.

The output JSON is consumed by the frontend in "demo mode" — see
``web/src/composables/useApi.ts`` (``VITE_DEMO_MODE`` branch). It is committed
to the ``gh-pages`` orphan branch by the CI workflow, then served directly
from ``raw.githubusercontent.com`` at:

    https://raw.githubusercontent.com/<USER>/<REPO>/gh-pages/<file>

so the Vercel static site can fetch fresh data every hour **without triggering
a rebuild**. (We intentionally do NOT use jsDelivr here — its edge cache holds
``gh`` content for up to 12h and ignores cache-buster query strings, which
made the demo display stale snapshots long after the hourly refresh.)

The payload schema is the union of what the demo UI needs:

* ``scanner/opportunities``  → Scanner table rows
* ``scanner/status``         → "Scanned N assets at HH:MM UTC"
* ``meta``                   → pipeline provenance for the demo banner

Usage
-----
::

    python3 scripts/notify/snapshot_to_pages.py \\
        --out /tmp/scanner-latest.json \\
        --top 30 --min-edge 0.03

Designed to be invoked from a GitHub Actions workflow step; the workflow is
responsible for the actual ``git push`` to ``gh-pages``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli.scan_funding_arbitrage import scan_venue as scan_carry_venue  # noqa: E402
from cli.scan_pure_futures_spreads import scan_pure_futures_spreads  # noqa: E402
from cli.scan_unified_funding import (
    DEFAULT_VENUES as UNIFIED_DEFAULT_VENUES,  # noqa: E402
)

DEFAULT_VENUES_CEX = ["binance", "bitget", "bybit", "okx"]
DEFAULT_VENUES_DEX = ["hyperliquid", "aster", "lighter", "edgex", "dydx"]

# Carry / unified scan thresholds — kept conservative so the snapshot surfaces
# real candidates without flooding the dashboard. Thresholds are lower than
# the CLI defaults because the demo is illustrative: we want to show the
# *shape* of carry / unified opportunities even in calm markets, while the
# net_edge_pct column still tells the user which ones are actually profitable
# after fees.
CARRY_ENTRY_THRESHOLD = (
    0.01  # % funding rate to qualify as a candidate (low → show more in demo)
)
CARRY_UNIVERSE_MIN = 0.005  # % funding rate to enter the scan universe
CARRY_EXIT_THRESHOLD = 0.005  # % funding rate to exit
CARRY_BORROW_FALLBACK_ANNUAL = 8.0  # %/yr, used when live borrow rate unavailable
UNIFIED_ENTRY_THRESHOLD = 0.01  # % net edge to qualify as a route


def _resolve_venues(venues_arg: str | None, include_dex: bool) -> list[str]:
    if venues_arg:
        return [v.strip().lower() for v in venues_arg.split(",") if v.strip()]
    if include_dex:
        return DEFAULT_VENUES_CEX + DEFAULT_VENUES_DEX
    return DEFAULT_VENUES_CEX + ["hyperliquid"]


def _scan_carry_for_snapshot(
    venues: list[str],
    *,
    top_n_per_venue: int = 20,
    workers: int = 8,
) -> list[dict[str, Any]]:
    """Scan Cash-and-Carry candidates per venue for the demo snapshot.

    Returns a list of ``{venue, forward, reverse}`` dicts (mirrors the JSON
    output of ``scan_funding_arbitrage.py --json``), with each venue's
    forward / reverse lists truncated to ``top_n_per_venue`` rows ranked by
    net_edge_pct. Carry needs spot + borrow data, so only CEX venues are
    scanned (DEX venues are perp-only).
    """
    from market.parallel_fetch import run_io_parallel

    carry_venues = [v for v in venues if v in DEFAULT_VENUES_CEX]
    if not carry_venues:
        return []

    def _one(v: str) -> tuple[str, dict[str, Any]]:
        r = scan_carry_venue(
            v,
            entry=CARRY_ENTRY_THRESHOLD,
            exit_rate=CARRY_EXIT_THRESHOLD,
            universe_min=CARRY_UNIVERSE_MIN,
            borrow_fallback_annual_pct=CARRY_BORROW_FALLBACK_ANNUAL,
            max_workers=workers,
        )

        # Rank + truncate each bucket by net_edge to keep payload bounded.
        # NOTE: we intentionally keep the top rows *regardless of sign* so the
        # demo dashboard always shows the shape of the carry universe. In calm
        # markets almost every candidate has net_edge < 0 (fees > funding), and
        # showing an empty table would make the demo look broken. The
        # net_edge_pct column makes it obvious which rows are actually
        # profitable, so this is honest rather than misleading.
        def _top(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            ranked = sorted(rows, key=lambda x: -float(x.get("net_edge_pct", -1e9)))
            return ranked[:top_n_per_venue]

        return v, {
            "venue": v,
            "two_leg_fee_pct": r["two_leg_fee_pct"],
            "forward": _top(r["forward_candidates"]),
            "reverse": _top(r["reverse_candidates"]),
            "reverse_not_borrowable": _top(r["reverse_not_borrowable"]),
            "total_pairs": r["total_pairs"],
        }

    scanned = run_io_parallel(
        carry_venues, _one, max_workers=len(carry_venues), swallow_errors=True
    )
    return [scanned[v] for v in carry_venues if v in scanned]


def _scan_unified_for_snapshot(
    venues: list[str],
    *,
    top_n: int = 30,
    workers: int = 8,
) -> dict[str, Any]:
    """Scan unified cross-venue carry routes for the demo snapshot.

    Returns ``{venues, forward, reverse}`` with each direction truncated to
    ``top_n`` routes ranked by net_edge_pct. Unified needs spot on one venue
    + perp on another, so only CEX venues participate (per the scanner's
    own DEFAULT_VENUES).
    """
    from backtest.unified_funding_pool import UnifiedFundingPool

    # Unified only supports CEX venues (spot leg required).
    unified_venues = [v for v in venues if v in DEFAULT_VENUES_CEX]
    if len(unified_venues) < 2:
        return {"venues": unified_venues, "forward": [], "reverse": []}

    pool = UnifiedFundingPool(
        venues=tuple(unified_venues),
        borrow_fallback_annual_pct=CARRY_BORROW_FALLBACK_ANNUAL,
        max_workers=workers,
    )
    pool.refresh(universe_min=CARRY_UNIVERSE_MIN)
    routes = pool.scan_routes(
        entry=UNIFIED_ENTRY_THRESHOLD, universe_min=CARRY_UNIVERSE_MIN
    )

    def _top(rows: list[Any]) -> list[dict[str, Any]]:
        # Same rank-only policy as carry — demo shows the shape of the route
        # universe even when no route is profitable after fees. The
        # net_edge_pct column signals profitability to the viewer.
        ranked = sorted(rows, key=lambda x: -float(getattr(x, "net_edge_pct", -1e9)))
        return [r.to_dict() for r in ranked[:top_n]]

    return {
        "venues": unified_venues,
        "forward": _top(routes["forward"]),
        "reverse": _top(routes["reverse"]),
    }


def build_snapshot(
    scan_result: dict[str, Any],
    *,
    top_n: int = 30,
    pipeline_info: dict[str, Any] | None = None,
    carry_venues: list[dict[str, Any]] | None = None,
    unified_routes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap raw scanner results into the demo-mode payload schema.

    The shape mirrors what the frontend's ``useApi.ts`` expects from
    ``GET /api/scanner/opportunities`` (pure), the carry tab's per-venue
    structure, and the unified tab's route list — so the demo code path is
    a near-transparent drop-in for all three strategies.
    """
    # Shallow-copy each row so setdefault() below (and the direction split)
    # never mutates the caller's data. The scanner result may be reused
    # downstream (e.g. written to JSONL for backtests).
    forward = [dict(x) for x in scan_result.get("forward", [])]
    reverse = [dict(x) for x in scan_result.get("reverse", [])]
    for x in forward:
        x.setdefault("direction", "forward")
    for x in reverse:
        x.setdefault("direction", "reverse")

    all_rows = forward + reverse
    # Rank by basis-adjusted real edge (scanner's primary metric), falling back
    # to net edge so older snapshots without the field still sort sanely.
    all_rows.sort(
        key=lambda x: (
            -float(
                x["real_edge_pct"]
                if x.get("real_edge_pct") is not None
                else x.get("net_edge_pct", -1e9)
            )
        )
    )
    top_rows = all_rows[:top_n]

    ts = scan_result.get("timestamp") or datetime.now(timezone.utc).isoformat()

    return {
        "meta": {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scan_timestamp": ts,
            "pipeline": pipeline_info or {},
        },
        "scanner_status": {
            "scanning": False,
            "last_scan_time": ts,
            "has_data": bool(top_rows),
            "live": False,
            "is_demo_snapshot": True,
        },
        "scanner_opportunities": {
            "venues": scan_result.get("venues", []),
            "total_assets_scanned": scan_result.get("total_assets_scanned", 0),
            "total_spreads_found": scan_result.get("total_spreads_found", 0),
            # Keep full forward/reverse split (frontend shows two tabs) but
            # truncate each list to top_n to keep payload < 100 KB.
            "forward": [r for r in top_rows if r.get("direction") == "forward"],
            "reverse": [r for r in top_rows if r.get("direction") == "reverse"],
            "venue_pair_stats": scan_result.get("venue_pair_stats", []),
            "timestamp": ts,
        },
        # Carry & Unified: optional slices — included only when the caller
        # passes them in. The frontend's demo route table falls back to an
        # empty state when these keys are absent (older snapshots).
        "scanner_carry_venues": carry_venues or [],
        "scanner_unified_routes": unified_routes
        or {"venues": [], "forward": [], "reverse": []},
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export scanner snapshot for the Vercel demo dashboard."
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output JSON path (e.g. /tmp/scanner-latest.json)",
    )
    parser.add_argument(
        "--venues",
        default=None,
        help="Comma-separated venues (default: CEX + hyperliquid)",
    )
    parser.add_argument(
        "--include-dex",
        action="store_true",
        help="Include all perp DEX venues",
    )
    parser.add_argument(
        "--min-edge",
        type=float,
        default=0.0,
        help="Minimum net edge %% after fees (default 0.0 — any positive edge)",
    )
    parser.add_argument(
        "--min-spread",
        type=float,
        default=0.03,
        help="Minimum raw spread % (default 0.03)",
    )
    parser.add_argument(
        "--max-mark-spread",
        type=float,
        default=1.0,
        help="Max mark price spread % between venues (default 1.0)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="Keep top-N rows per direction in the snapshot (default 30)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Parallel I/O workers",
    )
    parser.add_argument(
        "--include-carry",
        dest="include_carry",
        action="store_true",
        default=True,
        help="Also scan Cash-and-Carry + Unified routes for the snapshot (default on).",
    )
    parser.add_argument(
        "--no-carry",
        dest="include_carry",
        action="store_false",
        help="Skip carry / unified scan (pure-futures only, faster).",
    )
    args = parser.parse_args()

    venues = _resolve_venues(args.venues, args.include_dex)
    print(
        f"[snapshot] scanning venues={venues} min_edge={args.min_edge}", file=sys.stderr
    )

    t0 = time.time()
    try:
        result = scan_pure_futures_spreads(
            venues=venues,
            min_spread=args.min_spread,
            min_edge=args.min_edge,
            max_mark_spread_pct=args.max_mark_spread,
            workers=args.workers,
        )
    except Exception as e:
        print(f"[snapshot] scanner raised: {e}", file=sys.stderr)
        return 2
    elapsed = time.time() - t0
    print(
        f"[snapshot] pure-futures scan done in {elapsed:.1f}s — "
        f"{result.get('total_spreads_found', 0)} candidates",
        file=sys.stderr,
    )

    # Cash-and-Carry + Unified scans (optional, enabled by default). These
    # power the Carry / Unified tabs in the demo dashboard. Skipped via
    # --no-carry when latency matters more than coverage.
    carry_venues: list[dict[str, Any]] = []
    unified_routes: dict[str, Any] = {"venues": [], "forward": [], "reverse": []}
    if args.include_carry:
        t1 = time.time()
        try:
            carry_venues = _scan_carry_for_snapshot(venues, workers=args.workers)
            print(
                f"[snapshot] carry scan done in {time.time() - t1:.1f}s — "
                f"{len(carry_venues)} venues",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"[snapshot] carry scan failed (skipped): {e}", file=sys.stderr)

        t2 = time.time()
        try:
            unified_routes = _scan_unified_for_snapshot(
                venues, top_n=args.top, workers=args.workers
            )
            print(
                f"[snapshot] unified scan done in {time.time() - t2:.1f}s — "
                f"{len(unified_routes.get('forward', []))}fwd + "
                f"{len(unified_routes.get('reverse', []))}rev",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"[snapshot] unified scan failed (skipped): {e}", file=sys.stderr)

    pipeline_info = {
        "runner": os.environ.get("RUNNER_OS", "local"),
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "git_sha": os.environ.get("GITHUB_SHA", "")[:7],
        "elapsed_sec": round(elapsed, 2),
    }

    snapshot = build_snapshot(
        result,
        top_n=args.top,
        pipeline_info=pipeline_info,
        carry_venues=carry_venues,
        unified_routes=unified_routes,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    size_kb = out_path.stat().st_size / 1024
    carry_fwd = sum(len(v.get("forward", [])) for v in carry_venues)
    carry_rev = sum(len(v.get("reverse", [])) for v in carry_venues)
    print(
        f"[snapshot] wrote {out_path} ({size_kb:.1f} KB)",
        file=sys.stderr,
    )
    print(
        f"[snapshot]   pure: {len(snapshot['scanner_opportunities']['forward'])}fwd + "
        f"{len(snapshot['scanner_opportunities']['reverse'])}rev  "
        f"carry: {carry_fwd}fwd + {carry_rev}rev across {len(carry_venues)} venues  "
        f"unified: {len(unified_routes.get('forward', []))}fwd + "
        f"{len(unified_routes.get('reverse', []))}rev",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
