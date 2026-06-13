#!/usr/bin/env python3
"""Historical funding API → backtest snapshot synthesizer.

Does not depend on `scan --watch` collection: directly fetches settled historical funding rates
from exchanges (all 4 venues via funding_providers.fetch_since public endpoints), and at each
real settlement timestamp synthesizes snapshots with the same structure as scanner JSONL,
feeding them into run_backtest.

Conventions:
  - The "visible rate" at time t = the rate to be settled at the next settlement ts >= t.
    Most exchanges expose the current period's rate in real time / predictably, and run_backtest
    applies the most recent snapshot rate when crossing settlement boundaries — this combination
    yields funding accumulation perfectly consistent with the actual settled sequence.
  - Snapshot grid = union of all legs' settlement times (rounded to minute for dedup).
  - Pre-listing window / data holes (adjacent settlement gap > 8h×1.5): the leg is treated as
    non-tradable, avoiding bridging the first settlement rate backward to create phantom funding.
  - In addition to pure perp pairs (forward/reverse), also synthesizes single-venue cash-and-carry
    rows (snapshot["cc"]): cc_forward = spot long + perp short collecting positive rate;
    cc_reverse = borrow-sell + perp long collecting negative rate (minus borrow interest).
    Historical borrow rates have no public data; a constant --cc-borrow-apr assumption is converted
    to per-period cost.
  - Known limitation: when the spread flips sign, the long/short leg ordering reverses, the
    original pair key disappears, and backtest closes via spread_disappeared (equivalent to
    edge collapse exit); cc rows assume the coin is spot-buyable / borrowable at that venue
    (historical listing and borrowability cannot be verified).
"""

from __future__ import annotations

import bisect
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.funding_providers import get_funding_provider  # noqa: E402
from cli.scan_pure_futures_spreads import _scan_spreads  # noqa: E402
from core.fee_providers import (  # noqa: E402
    offline_fee_cache_from_by_base,
    prefetch_futures_fee_rates,
    taker_fee_pct,
)

CACHE_DIR = ROOT / "data" / "cache" / "funding-history"
CACHE_TTL_SEC = 6 * 3600

# Venues with no public settled-funding history endpoint (anonymous access).
# EdgeX's getFundingRatePage returns an empty list without an account context,
# so these legs contribute no history and are skipped (current-snapshot scanning
# still works). Revisit if the V2 SDK exposes a read-only history path.
_NO_PUBLIC_HISTORY = frozenset({"edgex"})
SPOT_TAKER_FEE_PCT = 0.10  # Spot taker fee is typically 0.1% across all four venues
DEFAULT_BORROW_APR_PCT = (
    15.0  # cc_reverse borrow APR assumption (may be higher for small caps)
)
HOURS_PER_YEAR = 365.0 * 24.0


def fetch_leg_history(
    venue: str,
    base: str,
    days: int,
    *,
    refresh: bool = False,
    cache_dir: Path = CACHE_DIR,
) -> list[dict[str, Any]]:
    """Fetch N days of settled funding rates for a venue × base, with 6h disk cache.

    Returns [{"ts": ms, "rate_pct": float}, ...] ascending; returns [] on failure/no data.
    """
    if venue.lower() in _NO_PUBLIC_HISTORY:
        print(
            f"[history] {venue} has no public funding history "
            f"(current-snapshot only) — leg excluded from backtest",
            file=sys.stderr,
        )
        return []

    sym = f"{base.upper()}USDT"
    start_ms = int((time.time() - days * 86400) * 1000)
    cache_path = cache_dir / f"{venue.lower()}_{sym}.json"

    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            fresh = time.time() - float(cached.get("fetched_at", 0)) < CACHE_TTL_SEC
            covers = int(cached.get("start_ms", 1 << 62)) <= start_ms
            if fresh and covers:
                return [r for r in cached.get("rows", []) if r["ts"] >= start_ms]
        except Exception:
            pass

    # Venues with 100 rows/page (bitget/okx/bybit): estimate pages based on worst-case 2h interval
    max_pages = max(10, int(days * 24 / 2 / 100) + 3)
    try:
        rows = get_funding_provider(venue).fetch_since(
            sym, start_ms, max_pages=max_pages
        )
    except Exception as e:
        print(f"[history] {venue} {sym} fetch failed: {e}", file=sys.stderr)
        return []
    rows.sort(key=lambda r: r["ts"])

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {"fetched_at": time.time(), "start_ms": start_ms, "rows": rows},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return rows


# Maximum settlement interval for major venues is 8h; exceeding this (with grace) is treated
# as pre-listing window or data hole
MAX_SETTLE_INTERVAL_H = 8.0
GAP_GRACE = 1.5


def _snap_interval(gap_h: float) -> float:
    """Snap settlement interval to common tiers (1/2/4/8h), absorbing millisecond jitter."""
    for cand in (1.0, 2.0, 4.0, 8.0):
        if abs(gap_h - cand) < 0.25 * cand:
            return cand
    return max(1.0, float(round(gap_h)))


def infer_interval_h(rows: list[dict[str, Any]]) -> float:
    """Infer settlement period from adjacent settlement time gaps (global median, used as first-point fallback only).

    Note: coins may switch intervals mid-history (e.g. ID 8h→4h→1h); the global median becomes
    inaccurate. build_snapshots uses per-point local intervals from adjacent settlements,
    falling back to this only for the very first point which has no predecessor.
    """
    if len(rows) < 3:
        return 8.0
    diffs = sorted(
        (rows[i + 1]["ts"] - rows[i]["ts"]) / 3600000.0 for i in range(len(rows) - 1)
    )
    return _snap_interval(diffs[len(diffs) // 2])


def fetch_cc_capability(
    venues: list[str],
    bases: list[str],
    *,
    workers: int = 8,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Probe real-time spot-buyability / borrowability per venue × base (historical state cannot be verified; uses current state as proxy).

    Returns {(venue, base): {"has_spot", "borrowable", "borrow_apr_pct"}}.
    Probe failures are treated conservatively (not buyable / not borrowable).
    """
    from backtest.borrow_providers import get_borrow_provider
    from backtest.unified_funding_pool import _get_venue
    from market.parallel_fetch import run_io_parallel

    out: dict[tuple[str, str], dict[str, Any]] = {
        (v.lower(), b.upper()): {
            "has_spot": False,
            "borrowable": False,
            "borrow_apr_pct": 0.0,
        }
        for v in venues
        for b in bases
    }

    # Bulk spot probe: fetch all tickers per venue in ONE call (when supported),
    # instead of one HTTP call per (venue, base) pair. Reduces V×B calls to V.
    def _spot_bulk(venue: str) -> tuple[str, dict[str, float]]:
        v = _get_venue(venue)
        tickers: dict[str, float] = {}
        if hasattr(v, "get_all_spot_tickers"):
            try:
                tickers = {
                    k.upper(): float(val or 0)
                    for k, val in v.get_all_spot_tickers().items()
                }
            except Exception:
                pass
        return venue.lower(), tickers

    bulk_spot = run_io_parallel(
        [v.lower() for v in venues],
        _spot_bulk,
        max_workers=min(workers, len(venues)),
        swallow_errors=True,
    )
    for pair_key in out:
        venue_lower, base_upper = pair_key
        tickers = bulk_spot.get(venue_lower, {})
        sym = f"{base_upper}USDT"
        out[pair_key]["has_spot"] = tickers.get(sym, 0) > 0

    def _borrow_one(venue: str) -> tuple[str, dict[str, Any]]:
        try:
            return venue, get_borrow_provider(venue).fetch_borrow_info(
                [b.upper() for b in bases]
            )
        except Exception:
            return venue, {}

    borrow_map = run_io_parallel(
        [v.lower() for v in venues],
        _borrow_one,
        max_workers=len(venues),
        swallow_errors=True,
    )
    for venue in (v.lower() for v in venues):
        info = borrow_map.get(venue) or {}
        if not any(i.get("borrowable") for i in info.values()):
            # All False may indicate missing API credentials (bitget/bybit/binance borrow queries require auth)
            print(
                f"[history] {venue}: 0 borrowable (if this venue requires API keys, "
                f"the probe may have failed rather than truly not borrowable)",
                file=sys.stderr,
            )
    for venue, info_by_coin in borrow_map.items():
        for base, info in (info_by_coin or {}).items():
            key = (venue, base.upper())
            if key not in out:
                continue
            out[key]["borrowable"] = bool(info.get("borrowable"))
            apr = float(info.get("annual_rate_pct", 0) or 0)
            if apr <= 0 and float(info.get("daily_rate_pct", 0) or 0) > 0:
                apr = float(info["daily_rate_pct"]) * 365.0
            out[key]["borrow_apr_pct"] = apr
    return out


def _cc_rows(
    by_base: dict[str, dict[str, dict[str, Any]]],
    borrow_apr_pct: float,
    cc_capability: dict[tuple[str, str], dict[str, Any]] | None = None,
    fee_cache: dict[tuple[str, str], dict[str, float]] | None = None,
) -> list[dict[str, Any]]:
    """Synthesize cash-and-carry rows from single-venue rates (same structure as pure rows, consumed by the backtest engine).

    cc_forward: spot long (rate 0) + perp short (collects positive rate), short leg carries the rate.
    cc_reverse: perp long (negative rate → receives money) + borrow-sell, long leg carries the rate,
                borrow interest is converted from APR to per-settlement period, stored in borrow_per_settle_pct.

    When cc_capability is non-empty: cc_forward requires spot buyability at that venue,
    cc_reverse requires borrowability, and when borrowable the real borrow APR replaces the constant assumption.
    """
    rows: list[dict[str, Any]] = []
    for base, venue_map in by_base.items():
        for venue, info in venue_map.items():
            rate = float(info["rate_pct"])
            if rate == 0:
                continue
            cap = (
                cc_capability.get((venue, base)) if cc_capability is not None else None
            )
            if cc_capability is not None and cap is None:
                continue
            if cap is not None:
                if rate > 0 and not cap["has_spot"]:
                    continue  # No spot (e.g. pre-market contract) → forward not executable
                if rate < 0 and not cap["borrowable"]:
                    continue  # Not borrowable → reverse not executable
            effective_apr = borrow_apr_pct
            if cap is not None and cap["borrow_apr_pct"] > 0:
                effective_apr = cap["borrow_apr_pct"]
            interval_h = float(info["interval_h"])
            sym = str(info.get("symbol") or f"{base}USDT")
            perp_fee = taker_fee_pct(venue, sym, fee_cache=fee_cache)
            fee = SPOT_TAKER_FEE_PCT + perp_fee
            common = {
                "base": base,
                "fee_pct": round(fee, 4),
                "long_interval_h": interval_h,
                "short_interval_h": interval_h,
                "settle_mismatch": False,
                "long_settle_ms": info.get("next_funding_ts", 0),
                "short_settle_ms": info.get("next_funding_ts", 0),
            }
            if rate > 0:
                rows.append(
                    {
                        **common,
                        "direction": "cc_forward",
                        "long_venue": f"{venue}:spot",
                        "short_venue": venue,
                        "long_rate_pct": 0.0,
                        "short_rate_pct": rate,
                        "spread_pct": round(rate, 6),
                        "net_edge_pct": round(rate - fee, 6),
                        "borrow_per_settle_pct": 0.0,
                    }
                )
            else:
                borrow_per_settle = effective_apr / HOURS_PER_YEAR * interval_h
                rows.append(
                    {
                        **common,
                        "direction": "cc_reverse",
                        "long_venue": venue,
                        "short_venue": f"{venue}:margin",
                        "long_rate_pct": rate,
                        "short_rate_pct": 0.0,
                        "spread_pct": round(abs(rate), 6),
                        "net_edge_pct": round(abs(rate) - borrow_per_settle - fee, 6),
                        "borrow_per_settle_pct": round(borrow_per_settle, 6),
                    }
                )
    rows.sort(key=lambda x: -x["net_edge_pct"])
    return rows


def build_snapshots(
    histories: dict[tuple[str, str], list[dict[str, Any]]],
    *,
    include_cc: bool = True,
    borrow_apr_pct: float = DEFAULT_BORROW_APR_PCT,
    cc_capability: dict[tuple[str, str], dict[str, Any]] | None = None,
    fee_cache: dict[tuple[str, str], dict[str, float]] | None = None,
    prefetch_live_fees: bool = False,
) -> list[dict[str, Any]]:
    """Synthesize a snapshot sequence directly consumable by run_backtest (including _ts).

    histories: {(venue, base): [{ts, rate_pct} ascending]}
    """
    if fee_cache is None:
        pairs = [(v.lower(), f"{b.upper()}USDT") for v, b in histories.keys()]
        if prefetch_live_fees and pairs:
            fee_cache = prefetch_futures_fee_rates(pairs)
        else:
            by_base_seed: dict[str, dict[str, dict[str, Any]]] = {}
            for venue, base in histories.keys():
                by_base_seed.setdefault(base.upper(), {})[venue.lower()] = {
                    "symbol": f"{base.upper()}USDT",
                }
            fee_cache = offline_fee_cache_from_by_base(by_base_seed)

    legs: dict[tuple[str, str], tuple[list[int], list[float], float]] = {}
    grid: set[int] = set()
    for (venue, base), rows in histories.items():
        if len(rows) < 2:
            continue
        ts_list = [int(r["ts"]) for r in rows]
        rates = [float(r["rate_pct"]) for r in rows]
        legs[(venue, base)] = (ts_list, rates, infer_interval_h(rows))
        # Round to minute to absorb millisecond jitter
        grid.update(t // 60000 * 60000 for t in ts_list)

    snapshots: list[dict[str, Any]] = []
    for t in sorted(grid):
        by_base: dict[str, dict[str, dict[str, Any]]] = {}
        for (venue, base), (ts_list, rates, fallback_interval) in legs.items():
            idx = bisect.bisect_left(ts_list, t)
            if idx >= len(ts_list):
                continue  # This leg's history has ended
            if idx > 0:
                gap_h = (ts_list[idx] - ts_list[idx - 1]) / 3600000.0
                if gap_h > MAX_SETTLE_INTERVAL_H * GAP_GRACE:
                    continue  # Abnormally large adjacent settlement gap → data hole, skip to avoid phantom accumulation
                # Interval may switch mid-history (8h→4h→1h); infer per-point from local adjacent gap
                interval_h = _snap_interval(gap_h)
            else:
                # t is before the first settlement: only treat as listed if within the first settlement period
                if (ts_list[0] - t) / 3600000.0 > fallback_interval * GAP_GRACE:
                    continue
                interval_h = fallback_interval
            by_base.setdefault(base, {})[venue.lower()] = {
                "symbol": f"{base}USDT",
                "rate_pct": rates[idx],
                "interval_h": interval_h,
                "next_funding_ts": ts_list[idx],
                "mark_price": 0.0,
            }
        # Thresholds relaxed: market data is continuously visible; entry thresholds are handled by run_backtest
        forward, reverse = _scan_spreads(
            by_base, min_spread=0.0, min_edge=-999.0, fee_cache=fee_cache
        )
        dt = datetime.fromtimestamp(t / 1000, timezone.utc)
        snap: dict[str, Any] = {
            "timestamp": dt.isoformat(),
            "forward": forward,
            "reverse": reverse,
            "_ts": dt,
        }
        if include_cc:
            snap["cc"] = _cc_rows(by_base, borrow_apr_pct, cc_capability, fee_cache)
        snapshots.append(snap)
    return snapshots


def fetch_history_snapshots(
    venues: list[str],
    bases: list[str],
    days: int,
    *,
    refresh: bool = False,
    workers: int = 8,
    borrow_apr_pct: float = DEFAULT_BORROW_APR_PCT,
    check_cc_capability: bool = False,
    prefetch_live_fees: bool = False,
) -> list[dict[str, Any]]:
    """Fetch historical rates for all venue × base combinations in parallel and synthesize snapshots.

    When check_cc_capability=True, probes real-time spot/borrow capability to filter cc rows
    (uses current state as proxy for historical; new listings' historical borrowability cannot be verified).
    """
    from market.parallel_fetch import run_io_parallel

    pairs = [(v.lower(), b.upper()) for v in venues for b in bases]

    def _one(pair: tuple[str, str]) -> tuple[tuple[str, str], list[dict[str, Any]]]:
        venue, base = pair
        rows = fetch_leg_history(venue, base, days, refresh=refresh)
        print(f"[history] {venue} {base}: {len(rows)} settlements", file=sys.stderr)
        return pair, rows

    histories = run_io_parallel(
        pairs, _one, max_workers=min(workers, len(pairs)), swallow_errors=True
    )
    cc_capability = None
    if check_cc_capability:
        print("[history] probing spot/borrow capability...", file=sys.stderr)
        cc_capability = fetch_cc_capability(venues, bases, workers=workers)
        n_spot = sum(1 for c in cc_capability.values() if c["has_spot"])
        n_borrow = sum(1 for c in cc_capability.values() if c["borrowable"])
        print(
            f"[history] capability: {n_spot}/{len(cc_capability)} legs have spot, "
            f"{n_borrow} borrowable",
            file=sys.stderr,
        )
    return build_snapshots(
        histories,
        borrow_apr_pct=borrow_apr_pct,
        cc_capability=cc_capability,
        prefetch_live_fees=prefetch_live_fees,
    )
