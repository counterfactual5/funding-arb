#!/usr/bin/env python3
"""Funding-spread persistence enrichment for the Telegram digest.

For each opportunity, pull recent settled funding history for both legs and
measure how persistent the *oriented* spread (short_rate − long_rate, using the
current leg roles) has been — so a transient one-cycle spike can be told apart
from a stable, repeatable carry.

The historical spread is reconstructed on the union of both legs' settlement
timestamps, forward-filling each leg's last settled rate (handles 1h vs 8h
mismatches). We then report, over the window:

* ``hist_cycles``       — grid points compared
* ``hist_held``         — points where the oriented spread stayed positive
* ``hist_held_pct``     — held / cycles × 100
* ``spread_median_hist``— median |spread| over positive points
* ``is_spike``          — current spread > SPIKE_MULT × that median

Best-effort: any fetch/parse failure leaves a row un-annotated rather than
failing the push. Reuses the 6h disk cache in
``backtest.funding_history_source`` so repeated cron runs are cheap.
"""

from __future__ import annotations

import bisect
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.funding_history_source import fetch_leg_history  # noqa: E402

# Current spread this many times above its own recent median ⇒ flag as a spike.
SPIKE_MULT = 3.0
DEFAULT_DAYS = 3
MIN_POINTS = 3


def _rate_forward_filled(ts_list: list[int], rows: list[dict[str, Any]], t: int):
    """Most recent settled rate at or before ``t`` (None before first point)."""
    i = bisect.bisect_right(ts_list, t) - 1
    return rows[i]["rate_pct"] if i >= 0 else None


def _oriented_spread_series(
    long_rows: list[dict[str, Any]], short_rows: list[dict[str, Any]]
) -> list[float]:
    """Reconstruct short_rate − long_rate on the union of settlement times."""
    if not long_rows or not short_rows:
        return []
    long_ts = [int(r["ts"]) for r in long_rows]
    short_ts = [int(r["ts"]) for r in short_rows]
    start = max(long_ts[0], short_ts[0])
    grid = sorted({t for t in (long_ts + short_ts) if t >= start})
    series: list[float] = []
    for t in grid:
        lr = _rate_forward_filled(long_ts, long_rows, t)
        sr = _rate_forward_filled(short_ts, short_rows, t)
        if lr is None or sr is None:
            continue
        series.append(sr - lr)
    return series


def annotate_persistence(
    rows: list[dict[str, Any]],
    days: int = DEFAULT_DAYS,
    workers: int = 8,
) -> list[dict[str, Any]]:
    """Annotate each row in place with persistence metrics. Returns ``rows``."""
    if not rows or days <= 0:
        return rows

    legs: set[tuple[str, str]] = set()
    for r in rows:
        base = str(r.get("base", "")).upper()
        legs.add((str(r.get("long_venue", "")).lower(), base))
        legs.add((str(r.get("short_venue", "")).lower(), base))

    def _one(leg: tuple[str, str]) -> tuple[tuple[str, str], list[dict[str, Any]]]:
        venue, base = leg
        try:
            return leg, fetch_leg_history(venue, base, days)
        except Exception:
            return leg, []

    try:
        from market.parallel_fetch import run_io_parallel

        hist = run_io_parallel(list(legs), _one, max_workers=workers, swallow_errors=True)
    except Exception:
        hist = {leg: _one(leg)[1] for leg in legs}

    for r in rows:
        base = str(r.get("base", "")).upper()
        long_rows = hist.get((str(r.get("long_venue", "")).lower(), base), [])
        short_rows = hist.get((str(r.get("short_venue", "")).lower(), base), [])
        series = _oriented_spread_series(long_rows, short_rows)
        if len(series) < MIN_POINTS:
            continue
        held = sum(1 for s in series if s > 0)
        positives = [abs(s) for s in series if s > 0]
        median = statistics.median(positives) if positives else 0.0
        now = r.get("spread_pct")
        r["hist_cycles"] = len(series)
        r["hist_held"] = held
        r["hist_held_pct"] = round(held / len(series) * 100, 0)
        r["spread_median_hist"] = round(median, 6)
        r["is_spike"] = bool(
            median > 0
            and isinstance(now, (int, float))
            and now > SPIKE_MULT * median
        )
    return rows
