#!/usr/bin/env python3
"""Export scanner results as a static snapshot for the Vercel demo dashboard.

The output JSON is consumed by the frontend in "demo mode" — see
``web/src/composables/useApi.ts`` (``VITE_DEMO_MODE`` branch). It is committed
to the ``gh-pages`` orphan branch by the CI workflow, then served via the
jsDelivr CDN at:

    https://cdn.jsdelivr.net/gh/<USER>/<REPO>@gh-pages/<file>

so the Vercel static site can fetch fresh data every hour **without triggering
a rebuild**.

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

from cli.scan_pure_futures_spreads import scan_pure_futures_spreads  # noqa: E402

DEFAULT_VENUES_CEX = ["binance", "bitget", "bybit", "okx"]
DEFAULT_VENUES_DEX = ["hyperliquid", "aster", "lighter", "edgex", "dydx"]


def _resolve_venues(venues_arg: str | None, include_dex: bool) -> list[str]:
    if venues_arg:
        return [v.strip().lower() for v in venues_arg.split(",") if v.strip()]
    if include_dex:
        return DEFAULT_VENUES_CEX + DEFAULT_VENUES_DEX
    return DEFAULT_VENUES_CEX + ["hyperliquid"]


def build_snapshot(
    scan_result: dict[str, Any],
    *,
    top_n: int = 30,
    pipeline_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap a raw scanner result into the demo-mode payload schema.

    The shape mirrors what the frontend's ``useApi.ts`` expects from
    ``GET /api/scanner/opportunities`` and ``GET /api/scanner/status``,
    so the demo code path is a near-transparent drop-in.
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
    all_rows.sort(key=lambda x: -float(x.get("net_edge_pct", -1e9)))
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
        default=0.03,
        help="Minimum net edge % (default 0.03)",
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
        f"[snapshot] scan done in {elapsed:.1f}s — "
        f"{result.get('total_spreads_found', 0)} candidates",
        file=sys.stderr,
    )

    pipeline_info = {
        "runner": os.environ.get("RUNNER_OS", "local"),
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "git_sha": os.environ.get("GITHUB_SHA", "")[:7],
        "elapsed_sec": round(elapsed, 2),
    }

    snapshot = build_snapshot(result, top_n=args.top, pipeline_info=pipeline_info)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    size_kb = out_path.stat().st_size / 1024
    print(
        f"[snapshot] wrote {out_path} ({size_kb:.1f} KB, "
        f"{len(snapshot['scanner_opportunities']['forward'])}fwd + "
        f"{len(snapshot['scanner_opportunities']['reverse'])}rev)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
