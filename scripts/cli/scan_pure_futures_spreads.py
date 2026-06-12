#!/usr/bin/env python3
"""Pure Perpetual Futures Spread Arbitrage Scanner — Cross-exchange perpetual contract funding rate spread scanner。

No spot/borrow needed; only compares funding rate differences across exchanges for USDT perpetuals：
  - Forward: long at the venue with the highest rate, short at the venue with the lowest rate
  - Reverse: long at the venue with the lowest (most negative) rate, short at the venue with the highest (or less negative) rate

Key difference from cash-and-carry scanner：both legs are perp（lower taker fees, no spot slippage），
Legs can be split across different exchanges; no need for one exchange to support both spot and perp.

Usage:
  python3 scripts/cli/scan_pure_futures_spreads.py
  python3 scripts/cli/scan_pure_futures_spreads.py --venues binance,bybit,okx,bitget
  python3 scripts/cli/scan_pure_futures_spreads.py --min-spread 0.1 --verbose
  python3 scripts/cli/scan_pure_futures_spreads.py --json
  python3 scripts/cli/scan_pure_futures_spreads.py --watch 5  # scan every 5 minutes and append to JSONL

Architecture note:
  This is Phase 1 — data-driven scan only. No order execution. The output answers:
  - How many pairs have spreads > 0.1% net?
  - What is the average duration of a spread above threshold?
  - Which venue pairs dominate?
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.funding_providers import get_funding_provider
from market.parallel_fetch import run_io_parallel

TZ = timezone(timedelta(hours=8))
TZ_UTC = timezone.utc

# Default thresholds
DEFAULT_MIN_SPREAD = 0.03  # 0.03% minimum rate spread
DEFAULT_MIN_EDGE = 0.01  # 0.01% minimum net edge after fees
DEFAULT_IO_WORKERS = 16  # I/O-bound; threads release GIL during urllib calls
DEFAULT_WATCH_INTERVAL = 5  # minutes
DEFAULT_JSONL_FILE = "data/pure_futures_spreads.jsonl"
HOURS_PER_YEAR = 365.0 * 24.0

from core.cross_interval_funding import (
    blended_hourly_rate,
    infer_last_settle_ts,
    spread_source_for_pair,
)
from core.fee_providers import (
    build_policy_futures_cache,
    pair_open_taker_fee_pct,
    parse_fee_policy,
)

# USDT-stablecoin / wrapped-asset blacklist (no point arbing against CEX's internal USD)
SYMBOL_BLACKLIST = {"USDC", "FDUSD", "TUSD", "BTCDOM", "BUSD", "USDP", "DAI"}


def _base_from_symbol(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith("USDT"):
        return s[:-4]
    return s


def _ts_to_ymd(ts_ms: int) -> str:
    if ts_ms <= 0:
        return "N/A"
    return datetime.fromtimestamp(ts_ms / 1000, TZ).strftime("%m-%d %H:%M")


def _mins_to_settle(ts_ms: int) -> str:
    if ts_ms <= 0:
        return "?"
    mins = (ts_ms - time.time() * 1000) / 60000
    if mins < 0:
        return "now"
    if mins < 60:
        return f"{mins:.0f}m"
    return f"{mins / 60:.1f}h"


def _annual_from_rate(rate_pct: float, interval_h: float) -> float:
    if interval_h <= 0:
        interval_h = 8.0
    return (rate_pct / 100.0) * (24.0 / interval_h) * 365.0 * 100.0


def fetch_all_fee_rate_rows_by_base(
    venues: list[str],
    workers: int,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Fetch funding info from each venue, return {base: {venue: {rate, next_ts, interval_h, mark}}}."""

    def _fetch_one(venue: str) -> tuple[str, dict[str, dict[str, Any]]]:
        fp = get_funding_provider(venue)
        rows = fp.fetch_all("USDT")
        imap = fp.fetch_interval_map("USDT")
        by_base: dict[str, dict[str, Any]] = {}
        for r in rows:
            sym = str(r.get("symbol", "")).upper()
            if not sym.endswith("USDT"):
                continue
            base = _base_from_symbol(sym)
            if not base or base in SYMBOL_BLACKLIST:
                continue
            interval_h = float(imap.get(sym, 8.0) or 8.0)
            next_ts = int(r.get("next_funding_ts", 0) or 0)
            by_base[base] = {
                "symbol": sym,
                "rate_pct": float(r.get("rate_pct", 0.0)),
                "interval_h": interval_h,
                "next_funding_ts": next_ts,
                "last_settle_ts": infer_last_settle_ts(next_ts, interval_h),
                "mark_price": float(r.get("mark_price", 0.0) or 0.0),
                "index_price": float(r.get("index_price", 0.0) or 0.0),
            }
        return venue, by_base

    out: dict[str, dict[str, dict[str, Any]]] = {}
    for venue, by_base in run_io_parallel(
        venues, _fetch_one, max_workers=workers, swallow_errors=True
    ).items():
        for base, info in by_base.items():
            if base not in out:
                out[base] = {}
            out[base][venue] = info
    return out


def _backfill_missing_settle_times(
    by_base: dict[str, dict[str, dict[str, Any]]],
    venues: list[str],
    workers: int,
) -> None:
    """For entries with next_funding_ts==0 (Bitget bulk), backfill via per-symbol fetch_current."""
    missing: list[tuple[str, str, str]] = []  # (venue, symbol, base)
    for base, venue_map in by_base.items():
        for venue in venues:
            info = venue_map.get(venue)
            if info and not info.get("next_funding_ts"):
                missing.append((venue, info["symbol"], base))
    if not missing:
        return

    def _fetch_one(
        args: tuple[str, str, str],
    ) -> tuple[tuple[str, str, str], dict[str, Any]]:
        venue, symbol, _base = args
        fp = get_funding_provider(venue)
        snap = fp.fetch_current(symbol)
        return args, snap

    snap_map = run_io_parallel(
        missing, _fetch_one, max_workers=workers, swallow_errors=True
    )
    for venue, symbol, base in missing:
        snap = snap_map.get((venue, symbol, base))
        if not isinstance(snap, dict):
            continue
        info = by_base[base][venue]
        next_ts = int(snap.get("next_funding_ts", 0) or 0)
        if next_ts > 0:
            info["next_funding_ts"] = next_ts
        last_ts = int(snap.get("last_settle_ts", 0) or 0)
        if last_ts > 0:
            info["last_settle_ts"] = last_ts
        elif next_ts > 0:
            info["last_settle_ts"] = infer_last_settle_ts(
                next_ts, float(info.get("interval_h", 8.0) or 8.0)
            )
        idx = float(snap.get("index_price", 0) or 0)
        if idx > 0:
            info["index_price"] = idx
        mark = float(snap.get("mark_price", 0) or 0)
        if mark > 0 and not info.get("mark_price"):
            info["mark_price"] = mark


def _scan_spreads(
    by_base: dict[str, dict[str, dict[str, Any]]],
    min_spread: float,
    min_edge: float,
    fee_cache: dict[tuple[str, str], dict[str, float]] | None = None,
    max_mark_spread_pct: float = 1.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compute pairwise perp-perp spreads across venues, return (forward, reverse)."""
    forward: list[dict[str, Any]] = []
    reverse: list[dict[str, Any]] = []

    for base, venue_map in by_base.items():
        vlist = sorted(venue_map.keys())
        for i, va in enumerate(vlist):
            for vb in vlist[i + 1 :]:
                ra = venue_map[va]
                rb = venue_map[vb]
                rate_a = float(ra["rate_pct"])
                rate_b = float(rb["rate_pct"])
                interval_a = float(ra.get("interval_h", 8.0) or 8.0)
                interval_b = float(rb.get("interval_h", 8.0) or 8.0)

                # Perp-perp: short at higher rate (receives funding), long at lower (pays less).
                # Spread = absolute difference regardless of sign.
                if rate_a >= rate_b:
                    short_venue, short_rate, short_info, short_interval = (
                        va,
                        rate_a,
                        ra,
                        interval_a,
                    )
                    long_venue, long_rate, long_info, long_interval = (
                        vb,
                        rate_b,
                        rb,
                        interval_b,
                    )
                else:
                    short_venue, short_rate, short_info, short_interval = (
                        vb,
                        rate_b,
                        rb,
                        interval_b,
                    )
                    long_venue, long_rate, long_info, long_interval = (
                        va,
                        rate_a,
                        ra,
                        interval_a,
                    )

                # Normalize rates to per-hour (HL 1h vs CEX 8h). Cross-interval pairs
                # blend observed rate with basis-implied hourly funding; see
                # core.cross_interval_funding for the model.
                is_mismatch = abs(long_interval - short_interval) > 0.5
                now_ms = int(time.time() * 1000)

                long_rate_hourly, long_blend = blended_hourly_rate(
                    long_rate,
                    long_interval,
                    long_info,
                    now_ms=now_ms,
                    venue=long_venue,
                    use_basis_blend=is_mismatch,
                )
                short_rate_hourly, short_blend = blended_hourly_rate(
                    short_rate,
                    short_interval,
                    short_info,
                    now_ms=now_ms,
                    venue=short_venue,
                    use_basis_blend=is_mismatch,
                )

                # Effective holding period: the shorter interval determines the
                # minimum cycle (you can exit the shorter side sooner).
                # Scale hourly spread back to the effective cycle length.
                eff_interval = min(long_interval, short_interval)
                spread = (short_rate_hourly - long_rate_hourly) * eff_interval
                if spread < min_spread:
                    continue

                long_sym = str(long_info.get("symbol") or f"{base}USDT")
                short_sym = str(short_info.get("symbol") or f"{base}USDT")
                long_fee, short_fee, fee_pct = pair_open_taker_fee_pct(
                    long_venue,
                    long_sym,
                    short_venue,
                    short_sym,
                    fee_cache=fee_cache,
                )

                net_edge = spread - fee_pct
                if net_edge < min_edge:
                    continue

                # Categorize: forward (both positive or mixed), reverse (both negative)
                if long_rate < 0 and short_rate < 0:
                    direction = "reverse"
                else:
                    direction = "forward"

                annual = _annual_from_rate(spread, eff_interval)

                # Calculate mark price spread percentage
                long_mark = float(long_info.get("mark_price", 0.0) or 0.0)
                short_mark = float(short_info.get("mark_price", 0.0) or 0.0)
                mark_spread_pct = (
                    abs(long_mark - short_mark) / max(long_mark, short_mark) * 100.0
                    if max(long_mark, short_mark) > 0
                    else 0.0
                )

                # Skip if mark price spread is too large（conservative pass when mark is 0）
                if mark_spread_pct > 0 and mark_spread_pct > max_mark_spread_pct:
                    continue

                entry: dict[str, Any] = {
                    "base": base,
                    "direction": direction,
                    "long_venue": long_venue,
                    "short_venue": short_venue,
                    "long_rate_pct": round(long_rate, 6),
                    "short_rate_pct": round(short_rate, 6),
                    "spread_pct": round(spread, 6),
                    "long_fee_pct": round(long_fee, 4),
                    "short_fee_pct": round(short_fee, 4),
                    "fee_pct": round(fee_pct, 4),
                    "round_trip_fee_pct": round(fee_pct * 2, 4),
                    "net_edge_pct": round(net_edge, 6),
                    "annual_apy_pct": round(annual, 1),
                    "long_settle_ms": long_info.get("next_funding_ts", 0),
                    "short_settle_ms": short_info.get("next_funding_ts", 0),
                    "long_interval_h": float(long_info.get("interval_h", 8.0) or 8.0),
                    "short_interval_h": float(short_info.get("interval_h", 8.0) or 8.0),
                    "settle_mismatch": is_mismatch,
                    "same_interval": not is_mismatch,
                    "long_mark": long_mark,
                    "short_mark": short_mark,
                    "mark_spread_pct": round(mark_spread_pct, 6),
                    "long_basis_pct": (
                        round(long_blend["basis_pct"], 6)
                        if long_blend.get("basis_pct") is not None
                        else None
                    ),
                    "short_basis_pct": (
                        round(short_blend["basis_pct"], 6)
                        if short_blend.get("basis_pct") is not None
                        else None
                    ),
                    "long_settle_progress": long_blend.get("settle_progress"),
                    "short_settle_progress": short_blend.get("settle_progress"),
                    "spread_source": spread_source_for_pair(
                        is_mismatch, long_blend, short_blend
                    ),
                }

                if direction == "forward":
                    forward.append(entry)
                else:
                    reverse.append(entry)

    forward.sort(key=lambda x: -x["net_edge_pct"])
    reverse.sort(key=lambda x: -x["net_edge_pct"])
    return forward, reverse


def scan_pure_futures_spreads(
    venues: list[str] | None = None,
    min_spread: float = DEFAULT_MIN_SPREAD,
    min_edge: float = DEFAULT_MIN_EDGE,
    max_mark_spread_pct: float = 1.0,
    workers: int = DEFAULT_IO_WORKERS,
    fee_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Main entry point — scan and return structured results."""
    if venues is None:
        venues = ["binance", "bitget", "bybit", "okx", "hyperliquid"]

    by_base = fetch_all_fee_rate_rows_by_base(venues, workers)
    _backfill_missing_settle_times(by_base, venues, workers)
    policy = parse_fee_policy(fee_policy)
    fee_cache = build_policy_futures_cache(by_base, policy, workers=workers)

    forward, reverse = _scan_spreads(
        by_base, min_spread, min_edge, fee_cache, max_mark_spread_pct
    )

    # Compute venue-level stats
    venue_pairs: dict[str, int] = {}
    for entry in forward + reverse:
        key = f"{entry['long_venue']}↔{entry['short_venue']}"
        venue_pairs[key] = venue_pairs.get(key, 0) + 1

    return {
        "venues": venues,
        "total_assets_scanned": len(by_base),
        "total_spreads_found": len(forward) + len(reverse),
        "forward": forward,
        "reverse": reverse,
        "venue_pair_stats": sorted(
            [{"pair": k, "count": v} for k, v in venue_pairs.items()],
            key=lambda x: -x["count"],
        ),
        "timestamp": datetime.now(TZ_UTC).isoformat(),
    }


def _print_human(result: dict[str, Any], verbose: bool = False) -> None:
    venues = result["venues"]
    fwd = result["forward"]
    rev = result["reverse"]
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")

    print(f"\n{'=' * 100}")
    print(
        f"PURE-FUTURES SPREAD  —  {now}  "
        f"venues=[{','.join(venues)}]  assets={result['total_assets_scanned']}"
    )
    print(f"{'=' * 100}")

    if fwd:
        print(
            f"\n  FORWARD (long@higher-rate  short@lower-rate) — {len(fwd)} candidates:"
        )
        print(
            f"  {'asset':<10s} {'long@':>8s} {'short@':>8s} "
            f"{'long_rate':>10s} {'short_rate':>10s} {'spread':>8s} "
            f"{'fee':>6s} {'net_edge':>9s} {'APY':>7s} {'mark_diff':>10s} {'settle':>18s}"
        )
        print(
            f"  {'-' * 10} {'-' * 8} {'-' * 8} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 6} {'-' * 9} {'-' * 7} {'-' * 10} {'-' * 18}"
        )
        for x in fwd[: verbose and 50 or 20]:
            settle = (
                f"L={_mins_to_settle(x['long_settle_ms'])} "
                f"S={_mins_to_settle(x['short_settle_ms'])}"
            )
            if x.get("settle_mismatch"):
                settle += " ⚠️"
            mark_spread = x.get("mark_spread_pct", 0.0)
            mark_str = f"{mark_spread:9.4f}%" if mark_spread > 0 else "      N/A"
            print(
                f"  {x['base']:<10s} {x['long_venue']:>8s} {x['short_venue']:>8s} "
                f"{x['long_rate_pct']:+9.4f}% {x['short_rate_pct']:+9.4f}% "
                f"{x['spread_pct']:7.4f}% {x['fee_pct']:5.3f}% "
                f"{x['net_edge_pct']:+8.4f}% {x['annual_apy_pct']:6.0f}% "
                f"{mark_str} {settle}"
            )
        if len(fwd) > 20 and not verbose:
            print(f"  ... ({len(fwd) - 20} more, use --verbose to see all)")

    if rev:
        print(
            f"\n  REVERSE (long@more-negative  short@less-negative) — {len(rev)} candidates:"
        )
        print(
            f"  {'asset':<10s} {'long@':>8s} {'short@':>8s} "
            f"{'long_rate':>10s} {'short_rate':>10s} {'spread':>8s} "
            f"{'fee':>6s} {'net_edge':>9s} {'APY':>7s} {'mark_diff':>10s} {'settle':>18s}"
        )
        print(
            f"  {'-' * 10} {'-' * 8} {'-' * 8} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 6} {'-' * 9} {'-' * 7} {'-' * 10} {'-' * 18}"
        )
        for x in rev[: verbose and 50 or 20]:
            settle = (
                f"L={_mins_to_settle(x['long_settle_ms'])} "
                f"S={_mins_to_settle(x['short_settle_ms'])}"
            )
            if x.get("settle_mismatch"):
                settle += " ⚠️"
            mark_spread = x.get("mark_spread_pct", 0.0)
            mark_str = f"{mark_spread:9.4f}%" if mark_spread > 0 else "      N/A"
            print(
                f"  {x['base']:<10s} {x['long_venue']:>8s} {x['short_venue']:>8s} "
                f"{x['long_rate_pct']:+9.4f}% {x['short_rate_pct']:+9.4f}% "
                f"{x['spread_pct']:7.4f}% {x['fee_pct']:5.3f}% "
                f"{x['net_edge_pct']:+8.4f}% {x['annual_apy_pct']:6.0f}% "
                f"{mark_str} {settle}"
            )
        if len(rev) > 20 and not verbose:
            print(f"  ... ({len(rev) - 20} more, use --verbose to see all)")

    if not fwd and not rev:
        print(
            f"\n  No spreads above min_spread={result.get('min_spread', 'N/A')}%. Try lowering --min-spread."
        )
    else:
        pairs = result.get("venue_pair_stats", [])
        if pairs and verbose:
            print("\n  VENUE PAIR FREQUENCY (top 10):")
            for p in pairs[:10]:
                print(f"    {p['pair']:<16s} {p['count']} pairs")

    fwd_profitable = len([x for x in fwd if x["net_edge_pct"] > 0])
    rev_profitable = len([x for x in rev if x["net_edge_pct"] > 0])
    fmt_mismatch = len([x for x in fwd + rev if x.get("settle_mismatch")])
    print(
        f"\n  SUMMARY: forward={len(fwd)}({fwd_profitable} profitable)  "
        f"reverse={len(rev)}({rev_profitable} profitable)  "
        f"settle_mismatch={fmt_mismatch}"
    )


def _append_jsonl(jsonl_path: str, result: dict[str, Any]) -> None:
    p = Path(jsonl_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pure perpetual futures funding spread scanner"
    )
    parser.add_argument(
        "--venues",
        default="binance,bitget,bybit,okx,hyperliquid",
        help=(
            "Comma-separated venues (default: binance,bitget,bybit,okx,hyperliquid; "
            "also available: aster, lighter)"
        ),
    )
    parser.add_argument(
        "--min-spread",
        type=float,
        default=DEFAULT_MIN_SPREAD,
        help=f"Minimum rate spread %% (default {DEFAULT_MIN_SPREAD}%%)",
    )
    parser.add_argument(
        "--min-edge",
        type=float,
        default=DEFAULT_MIN_EDGE,
        help=f"Minimum net edge %% after fees (default {DEFAULT_MIN_EDGE}%%)",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=DEFAULT_IO_WORKERS,
        help="Parallel I/O workers",
    )
    parser.add_argument(
        "--max-mark-spread",
        type=float,
        default=1.0,
        help="Max mark price spread %% between venues (default 1.0%%)",
    )
    parser.add_argument("--verbose", "-V", action="store_true", help="Show all results")
    parser.add_argument("--json", action="store_true", help="JSON output (single scan)")
    parser.add_argument(
        "--watch",
        type=float,
        const=DEFAULT_WATCH_INTERVAL,
        nargs="?",
        metavar="MINUTES",
        help=f"Run continuously every N minutes (default {DEFAULT_WATCH_INTERVAL}m), append JSONL",
    )
    parser.add_argument(
        "--jsonl-file",
        default=DEFAULT_JSONL_FILE,
        help=f"JSONL output file (default {DEFAULT_JSONL_FILE})",
    )
    args = parser.parse_args()

    venues = [v.strip().lower() for v in args.venues.split(",") if v.strip()]

    if args.watch:
        interval_min = float(args.watch)
        print(
            f"Watch mode: scanning every {interval_min:.1f}m → {args.jsonl_file}",
            file=sys.stderr,
        )
        print("Press Ctrl+C to stop.", file=sys.stderr)
        while True:
            try:
                t0 = time.time()
                result = scan_pure_futures_spreads(
                    venues=venues,
                    min_spread=args.min_spread,
                    min_edge=args.min_edge,
                    max_mark_spread_pct=args.max_mark_spread,
                    workers=args.workers,
                )
                result["min_spread"] = args.min_spread
                result["min_edge"] = args.min_edge
                elapsed = time.time() - t0
                result["elapsed_sec"] = round(elapsed, 2)
                _append_jsonl(args.jsonl_file, result)
                print(
                    f"[{datetime.now(TZ).strftime('%H:%M:%S')}] "
                    f"{result['total_spreads_found']} spreads "
                    f"({len(result['forward'])}fwd {len(result['reverse'])}rev) "
                    f"in {elapsed:.1f}s",
                    file=sys.stderr,
                )
                _print_human(result, verbose=args.verbose)
                time.sleep(max(0, interval_min * 60 - elapsed))
            except KeyboardInterrupt:
                print("\nStopped.", file=sys.stderr)
                break
            except Exception as e:
                print(f"Scan error: {e}", file=sys.stderr)
                time.sleep(60)
        return

    # Single scan
    t0 = time.time()
    print(f"Fetching {len(venues)} venues...", file=sys.stderr)
    result = scan_pure_futures_spreads(
        venues=venues,
        min_spread=args.min_spread,
        min_edge=args.min_edge,
        max_mark_spread_pct=args.max_mark_spread,
        workers=args.workers,
    )
    elapsed = time.time() - t0
    result["min_spread"] = args.min_spread
    result["min_edge"] = args.min_edge
    result["elapsed_sec"] = round(elapsed, 2)
    print(f"Done in {elapsed:.1f}s", file=sys.stderr)

    if args.json:
        out_rows: list[dict[str, Any]] = []
        for x in result["forward"]:
            x["direction"] = "forward"
            out_rows.append(x)
        for x in result["reverse"]:
            x["direction"] = "reverse"
            out_rows.append(x)
        out_rows.sort(key=lambda x: -x["net_edge_pct"])
        print(
            json.dumps(
                {
                    "timestamp": result["timestamp"],
                    "venues": result["venues"],
                    "total_assets": result["total_assets_scanned"],
                    "min_spread": args.min_spread,
                    "min_edge": args.min_edge,
                    "elapsed_sec": elapsed,
                    "spreads": out_rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print_human(result, verbose=args.verbose)


if __name__ == "__main__":
    main()
