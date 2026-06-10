#!/usr/bin/env python3
"""Pure Futures Spread observation report — 读取 scanner JSONL，统计机会质量。

配套 scanner:
  python3 scripts/cli/scan_pure_futures_spreads.py --watch 5 \
    --jsonl-file data/pure_futures_spreads.jsonl

报告:
  python3 scripts/cli/report_pure_futures_spreads.py
  python3 scripts/cli/report_pure_futures_spreads.py --since-hours 24 --top 20
  python3 scripts/cli/report_pure_futures_spreads.py --json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_JSONL_FILE = "data/pure_futures_spreads.jsonl"
TZ = timezone(timedelta(hours=8))


@dataclass
class OpportunityStats:
    key: str
    base: str
    direction: str
    long_venue: str
    short_venue: str
    samples: int = 0
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    spread_values: list[float] = field(default_factory=list)
    edge_values: list[float] = field(default_factory=list)
    apy_values: list[float] = field(default_factory=list)
    settle_mismatch_samples: int = 0
    active_streak: int = 0
    max_streak: int = 0
    longest_duration_min: float = 0.0
    current_start_ts: datetime | None = None
    prev_seen_ts: datetime | None = None

    def observe(self, ts: datetime, row: dict[str, Any], max_gap_min: float) -> None:
        self.samples += 1
        self.first_ts = ts if self.first_ts is None else min(self.first_ts, ts)
        self.last_ts = ts if self.last_ts is None else max(self.last_ts, ts)
        self.spread_values.append(float(row.get("spread_pct", 0.0) or 0.0))
        self.edge_values.append(float(row.get("net_edge_pct", 0.0) or 0.0))
        self.apy_values.append(float(row.get("annual_apy_pct", 0.0) or 0.0))
        if row.get("settle_mismatch"):
            self.settle_mismatch_samples += 1

        if (
            self.prev_seen_ts is None
            or (ts - self.prev_seen_ts).total_seconds() / 60.0 > max_gap_min
        ):
            self.active_streak = 1
            self.current_start_ts = ts
        else:
            self.active_streak += 1
        self.prev_seen_ts = ts
        self.max_streak = max(self.max_streak, self.active_streak)
        if self.current_start_ts is not None:
            self.longest_duration_min = max(
                self.longest_duration_min,
                (ts - self.current_start_ts).total_seconds() / 60.0,
            )

    def to_dict(self, total_snapshots: int) -> dict[str, Any]:
        seen_ratio = self.samples / total_snapshots if total_snapshots > 0 else 0.0
        return {
            "key": self.key,
            "base": self.base,
            "direction": self.direction,
            "long_venue": self.long_venue,
            "short_venue": self.short_venue,
            "samples": self.samples,
            "seen_ratio_pct": round(seen_ratio * 100.0, 2),
            "first_ts": self.first_ts.isoformat() if self.first_ts else None,
            "last_ts": self.last_ts.isoformat() if self.last_ts else None,
            "avg_spread_pct": round(_avg(self.spread_values), 6),
            "max_spread_pct": round(
                max(self.spread_values) if self.spread_values else 0.0, 6
            ),
            "avg_edge_pct": round(_avg(self.edge_values), 6),
            "p95_edge_pct": round(_percentile(self.edge_values, 0.95), 6),
            "max_edge_pct": round(
                max(self.edge_values) if self.edge_values else 0.0, 6
            ),
            "max_apy_pct": round(max(self.apy_values) if self.apy_values else 0.0, 2),
            "settle_mismatch_ratio_pct": round(
                self.settle_mismatch_samples / self.samples * 100.0
                if self.samples
                else 0.0,
                2,
            ),
            "max_streak_samples": self.max_streak,
            "longest_duration_min": round(self.longest_duration_min, 1),
        }


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def _iter_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for direction in ("forward", "reverse"):
        for row in snapshot.get(direction, []) or []:
            item = dict(row)
            item["direction"] = item.get("direction") or direction
            rows.append(item)
    # Also tolerate --json single-scan shape if someone saved it as JSONL.
    for row in snapshot.get("spreads", []) or []:
        item = dict(row)
        item["direction"] = item.get("direction") or "unknown"
        rows.append(item)
    return rows


def _opp_key(row: dict[str, Any]) -> str:
    return ":".join(
        [
            str(row.get("base", "")).upper(),
            str(row.get("direction", "")),
            str(row.get("long_venue", "")),
            str(row.get("short_venue", "")),
        ]
    )


def load_snapshots(
    jsonl_path: Path, since_hours: float | None = None
) -> list[dict[str, Any]]:
    if not jsonl_path.exists():
        raise FileNotFoundError(f"JSONL not found: {jsonl_path}")
    cutoff: datetime | None = None
    if since_hours and since_hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    snapshots: list[dict[str, Any]] = []
    with jsonl_path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"skip malformed line {line_no}: {e}", file=sys.stderr)
                continue
            ts = _parse_ts(snap.get("timestamp"))
            if ts is None:
                print(f"skip line {line_no}: missing timestamp", file=sys.stderr)
                continue
            if cutoff and ts < cutoff:
                continue
            snap["_ts"] = ts
            snapshots.append(snap)
    snapshots.sort(key=lambda x: x["_ts"])
    return snapshots


def build_report(
    snapshots: list[dict[str, Any]],
    min_edge: float | None = None,
    min_samples: int = 1,
    max_gap_min: float = 15.0,
) -> dict[str, Any]:
    stats: dict[str, OpportunityStats] = {}
    venue_pairs: dict[str, int] = {}
    assets: dict[str, int] = {}
    total_rows = 0
    mismatch_rows = 0

    for snap in snapshots:
        ts: datetime = snap["_ts"]
        seen_keys: set[str] = set()
        for row in _iter_rows(snap):
            edge = float(row.get("net_edge_pct", 0.0) or 0.0)
            if min_edge is not None and edge < min_edge:
                continue
            key = _opp_key(row)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            total_rows += 1
            if row.get("settle_mismatch"):
                mismatch_rows += 1
            if key not in stats:
                stats[key] = OpportunityStats(
                    key=key,
                    base=str(row.get("base", "")).upper(),
                    direction=str(row.get("direction", "")),
                    long_venue=str(row.get("long_venue", "")),
                    short_venue=str(row.get("short_venue", "")),
                )
            stats[key].observe(ts, row, max_gap_min=max_gap_min)
            pair = f"{row.get('long_venue')}↔{row.get('short_venue')}"
            venue_pairs[pair] = venue_pairs.get(pair, 0) + 1
            base = str(row.get("base", "")).upper()
            assets[base] = assets.get(base, 0) + 1

    total_snapshots = len(snapshots)
    opportunities = [
        s.to_dict(total_snapshots) for s in stats.values() if s.samples >= min_samples
    ]
    opportunities.sort(
        key=lambda x: (
            -x["longest_duration_min"],
            -x["samples"],
            -x["avg_edge_pct"],
        )
    )
    return {
        "snapshot_count": total_snapshots,
        "first_snapshot": snapshots[0]["_ts"].isoformat() if snapshots else None,
        "last_snapshot": snapshots[-1]["_ts"].isoformat() if snapshots else None,
        "total_opportunity_rows": total_rows,
        "unique_opportunities": len(stats),
        "settle_mismatch_rows": mismatch_rows,
        "settle_mismatch_ratio_pct": round(
            mismatch_rows / total_rows * 100.0 if total_rows else 0.0, 2
        ),
        "top_assets": _top_counts(assets),
        "top_venue_pairs": _top_counts(venue_pairs),
        "opportunities": opportunities,
    }


def _top_counts(counts: dict[str, int], limit: int = 20) -> list[dict[str, Any]]:
    return [
        {"key": k, "count": v}
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    ]


def print_report(report: dict[str, Any], top: int = 20) -> None:
    print("\n" + "=" * 96)
    print("PURE-FUTURES SPREAD OBSERVATION REPORT")
    print("=" * 96)
    print(
        f"snapshots={report['snapshot_count']}  rows={report['total_opportunity_rows']}  "
        f"unique={report['unique_opportunities']}  mismatch={report['settle_mismatch_ratio_pct']}%"
    )
    if report.get("first_snapshot"):
        first = (
            _parse_ts(report["first_snapshot"])
            .astimezone(TZ)
            .strftime("%Y-%m-%d %H:%M")
        )
        last = (
            _parse_ts(report["last_snapshot"]).astimezone(TZ).strftime("%Y-%m-%d %H:%M")
        )
        print(f"window={first} → {last} (Asia/Shanghai)")

    print("\nTop assets:")
    for x in report["top_assets"][:10]:
        print(f"  {x['key']:<12s} {x['count']:>5d}")

    print("\nTop venue pairs:")
    for x in report["top_venue_pairs"][:10]:
        print(f"  {x['key']:<20s} {x['count']:>5d}")

    print("\nTop persistent opportunities:")
    print(
        f"  {'asset':<10s} {'dir':<8s} {'long@':<8s} {'short@':<8s} "
        f"{'samples':>7s} {'seen%':>7s} {'dur':>8s} {'avgEdge':>9s} {'p95Edge':>9s} {'maxEdge':>9s} {'mismatch':>9s}"
    )
    print("  " + "-" * 108)
    for x in report["opportunities"][:top]:
        print(
            f"  {x['base']:<10s} {x['direction']:<8s} {x['long_venue']:<8s} {x['short_venue']:<8s} "
            f"{x['samples']:7d} {x['seen_ratio_pct']:6.1f}% {x['longest_duration_min']:7.0f}m "
            f"{x['avg_edge_pct']:+8.4f}% {x['p95_edge_pct']:+8.4f}% {x['max_edge_pct']:+8.4f}% "
            f"{x['settle_mismatch_ratio_pct']:8.1f}%"
        )
    if not report["opportunities"]:
        print("  No opportunities after filters.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report pure futures spread opportunities from scanner JSONL"
    )
    parser.add_argument(
        "--jsonl-file",
        default=DEFAULT_JSONL_FILE,
        help=f"Scanner JSONL file (default {DEFAULT_JSONL_FILE})",
    )
    parser.add_argument(
        "--since-hours",
        type=float,
        default=0.0,
        help="Only include snapshots from the last N hours (0 = all)",
    )
    parser.add_argument(
        "--min-edge", type=float, help="Filter rows below this net edge %%"
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=1,
        help="Only show opportunities seen at least N times",
    )
    parser.add_argument(
        "--max-gap-min",
        type=float,
        default=15.0,
        help="Max gap between sightings to count as same duration streak",
    )
    parser.add_argument("--top", type=int, default=20, help="Number of rows to print")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    args = parser.parse_args()

    snapshots = load_snapshots(
        Path(args.jsonl_file), since_hours=args.since_hours or None
    )
    report = build_report(
        snapshots,
        min_edge=args.min_edge,
        min_samples=args.min_samples,
        max_gap_min=args.max_gap_min,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report, top=args.top)


if __name__ == "__main__":
    main()
