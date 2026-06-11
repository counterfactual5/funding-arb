#!/usr/bin/env python3
"""Scan multi-exchange funding rate arbitrage opportunities.

Forward arbitrage (positive funding rate): buy spot + open short perp -> requires spot market
Reverse arbitrage (negative funding rate): borrow & sell + open long perp -> requires borrowable coins, not just spot

Usage:
  python3 scripts/cli/scan_funding_arbitrage.py                 # All exchanges
  python3 scripts/cli/scan_funding_arbitrage.py --venue bitget  # Specify exchange
  python3 scripts/cli/scan_funding_arbitrage.py --entry 0.03    # Custom entry threshold
  python3 scripts/cli/scan_funding_arbitrage.py --json           # JSON Output
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

from backtest.borrow_providers import borrow_cost_per_period, get_borrow_provider
from backtest.funding_providers import get_funding_provider
from core.fee_providers import (
    build_policy_carry_caches,
    carry_two_leg_fee_pct,
    parse_fee_policy,
    resolve_venue_fee,
)
from market.parallel_fetch import run_io_parallel

TZ = timezone(timedelta(hours=8))

# Default thresholds
DEFAULT_ENTRY = 0.05
DEFAULT_EXIT = 0.01
DEFAULT_UNIVERSE_MIN = 0.03
DEFAULT_BORROW_ANNUAL_PCT = 8.0
DEFAULT_IO_WORKERS = 8
HOURS_PER_YEAR = 365.0 * 24.0
BLACKLIST = {"USDC", "FDUSD", "TUSD", "BTCDOM", "BUSD"}

VENUE_CLASSES = {
    "bitget": "venues.bitget.BitgetSpotVenue",
    "bybit": "venues.bybit.BybitSpotVenue",
    "okx": "venues.okx.OkxSpotVenue",
    "binance": "venues.binance.BinanceSpotVenue",
}


def _get_venue(venue: str):
    """Dynamically import and instantiate venue."""
    parts = VENUE_CLASSES[venue].rsplit(".", 1)
    mod = __import__(parts[0], fromlist=[parts[1]])
    return getattr(mod, parts[1])()


def _ts_to_ymd(ts_ms: int) -> str:
    if ts_ms <= 0:
        return "N/A"
    return datetime.fromtimestamp(ts_ms / 1000, TZ).strftime("%m-%d %H:%M")


def _mins_to_settle(ts_ms: int) -> str:
    if ts_ms <= 0:
        return "?"
    mins = (ts_ms - time.time() * 1000) / 60000
    if mins < 0:
        return "settling"
    if mins < 60:
        return f"{mins:.0f}m"
    return f"{mins / 60:.1f}h"


def _borrow_pct_per_interval(annual_pct: float, interval_h: float) -> float:
    """Normalize annual borrow rate to the same settlement cycle as funding rate (percentage per interval)."""
    if annual_pct <= 0 or interval_h <= 0:
        return 0.0
    return (annual_pct / HOURS_PER_YEAR) * interval_h


def scan_venue(
    venue: str,
    entry: float,
    exit_rate: float,
    universe_min: float,
    borrow_fallback_annual_pct: float = DEFAULT_BORROW_ANNUAL_PCT,
    max_workers: int = DEFAULT_IO_WORKERS,
    fee_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan a single exchange, return structured results."""
    fp = get_funding_provider(venue)
    rows = fp.fetch_all("USDT")
    imap = fp.fetch_interval_map("USDT")

    policy = parse_fee_policy(fee_policy)
    symbols = [
        r["symbol"].upper()
        for r in rows
        if r["symbol"].upper().endswith("USDT")
        and r["symbol"].upper()[:-4] not in BLACKLIST
    ]
    futures_cache, spot_cache = build_policy_carry_caches(
        venue, symbols, policy, workers=max_workers
    )
    resolved = resolve_venue_fee(venue, leg="spot", policy=policy)
    resolved_fut = resolve_venue_fee(venue, leg="futures", policy=policy)
    default_spot_pct = float(resolved["taker_pct"])
    default_futures_pct = float(resolved_fut["taker_pct"])
    fee_source = resolved_fut.get("source", "tier")

    positive: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []

    for r in rows:
        sym = r["symbol"].upper()
        if not sym.endswith("USDT"):
            continue
        base = sym[:-4]
        if not base or base in BLACKLIST:
            continue

        rate = r["rate_pct"]
        next_ts = r.get("next_funding_ts", 0) or 0
        interval_h = imap.get(sym, 8.0)
        annual = abs(rate) * (24 / interval_h) * 365
        mark = r.get("mark_price", 0) or 0
        spot_pct, futures_pct, two_leg_fee = carry_two_leg_fee_pct(
            venue,
            sym,
            futures_cache=futures_cache,
            spot_cache=spot_cache,
        )

        entry_obj: dict[str, Any] = {
            "base": base,
            "symbol": sym,
            "rate_pct": rate,
            "next_ts": next_ts,
            "interval_h": interval_h,
            "annual_pct": round(annual, 1),
            "mark_price": mark,
            "spot_fee_pct": round(spot_pct, 4),
            "futures_fee_pct": round(futures_pct, 4),
            "fee_pct": round(two_leg_fee, 4),
        }

        if rate > 0:
            entry_obj["has_spot"] = False
            entry_obj["spot_price"] = 0.0
            entry_obj["net_edge_pct"] = round(rate - two_leg_fee, 4)
            positive.append(entry_obj)
        else:
            entry_obj["borrowable"] = False
            entry_obj["borrow_daily_pct"] = 0.0
            entry_obj["borrow_annual_pct"] = 0.0
            entry_obj["borrow_per_period_pct"] = 0.0
            entry_obj["net_edge_pct"] = round(abs(rate) - two_leg_fee, 4)
            negative.append(entry_obj)

    positive.sort(key=lambda x: -x["rate_pct"])
    negative.sort(key=lambda x: x["rate_pct"])

    # Forward: check spot (above threshold only; prefer bulk tickers)
    pos_bases = [x["base"] for x in positive if x["rate_pct"] >= universe_min]
    bulk_tickers: dict[str, float] = {}
    if pos_bases:
        try:
            v = _get_venue(venue)
            if hasattr(v, "get_all_spot_tickers"):
                try:
                    bulk_tickers = v.get_all_spot_tickers()
                except Exception:
                    pass
            for x in positive:
                if x["base"] not in pos_bases:
                    continue
                if bulk_tickers:
                    px = float(bulk_tickers.get(x["symbol"], 0) or 0)
                    x["spot_price"] = px
                    x["has_spot"] = px > 0
                else:
                    try:
                        px = v.get_ticker(x["symbol"])
                        x["spot_price"] = px
                        x["has_spot"] = px > 0
                    except Exception:
                        pass
        except Exception:
            pass

    # Reverse: check borrowability + borrow rates (parallel per-coin)
    neg_bases = [x["base"] for x in negative if x["rate_pct"] <= -universe_min]
    borrow_map: dict[str, dict[str, Any]] = {}
    if neg_bases:
        try:
            bp = get_borrow_provider(venue)
            borrow_map = bp.fetch_borrow_info(neg_bases, max_workers=max_workers)
        except Exception as e:
            print(
                f"[{venue}] borrow info fetch failed ({e}); "
                f"reverse candidates will all show not-borrowable",
                file=sys.stderr,
            )
        for x in negative:
            if x["base"] not in neg_bases:
                continue
            info = borrow_map.get(x["base"], {})
            borrowable = bool(info.get("borrowable"))
            daily = float(info.get("daily_rate_pct", 0) or 0)
            annual_borrow = float(info.get("annual_rate_pct", 0) or 0)
            if borrowable and annual_borrow <= 0 and daily <= 0:
                annual_borrow = borrow_fallback_annual_pct
                daily = annual_borrow / 365.0
            elif borrowable and annual_borrow <= 0 and daily > 0:
                annual_borrow = daily * 365.0
            borrow_period = (
                borrow_cost_per_period(daily, x["interval_h"])
                if daily > 0
                else _borrow_pct_per_interval(annual_borrow, x["interval_h"])
            )
            _, _, leg_fee = carry_two_leg_fee_pct(
                venue,
                x["symbol"],
                futures_cache=futures_cache,
                spot_cache=spot_cache,
            )
            net = abs(x["rate_pct"]) - borrow_period - leg_fee
            x["borrowable"] = borrowable
            x["borrow_daily_pct"] = round(daily, 4)
            x["borrow_annual_pct"] = round(annual_borrow, 2)
            x["borrow_per_period_pct"] = round(borrow_period, 4)
            x["net_edge_pct"] = round(net, 4)
            if info.get("max_borrow"):
                x["max_borrow"] = info.get("max_borrow")

    # Backfill missing next settlement times (e.g. Bitget bulk API missing), only for candidates above threshold
    missing_ts = [
        x for x in (positive + negative)
        if not x["next_ts"] and abs(x["rate_pct"]) >= universe_min
    ][:30]
    if missing_ts:
        def _fill_ts(x: dict[str, Any]) -> tuple[str, int]:
            snap = fp.fetch_current(x["symbol"])
            return x["symbol"], int(snap.get("next_funding_ts", 0) or 0)

        ts_map = run_io_parallel(
            missing_ts, lambda x: _fill_ts(x), max_workers=max_workers, swallow_errors=True
        )
        for x in missing_ts:
            x["next_ts"] = ts_map.get(x["symbol"], 0) or 0

    return {
        "venue": venue,
        "total_pairs": len(positive) + len(negative),
        "spot_fee_pct": default_spot_pct,
        "futures_fee_pct": default_futures_pct,
        "two_leg_fee_pct": round(default_spot_pct + default_futures_pct, 4),
        "fee_source": fee_source,
        "fee_tier": resolved_fut.get("tier"),
        "borrow_fallback_annual_pct": borrow_fallback_annual_pct,
        "entry_threshold": entry,
        "forward_candidates": [x for x in positive if x["rate_pct"] >= entry and x.get("has_spot")],
        "forward_no_spot": [x for x in positive if x["rate_pct"] >= entry and not x.get("has_spot")],
        "reverse_candidates": [x for x in negative if x["rate_pct"] <= -entry and x.get("borrowable")],
        "reverse_not_borrowable": [
            x for x in negative if x["rate_pct"] <= -entry and not x.get("borrowable")
        ],
        "near_forward": [
            x for x in positive
            if entry > x["rate_pct"] >= universe_min and x.get("has_spot")
        ],
        "near_reverse": [
            x for x in negative
            if -entry < x["rate_pct"] <= -universe_min and x.get("borrowable")
        ],
        "positive_all": positive,
        "negative_all": negative,
    }


def print_report(result: dict[str, Any], verbose: bool = False):
    """Print human-readable report."""
    v = result["venue"].upper()
    fee = result["two_leg_fee_pct"]
    fwd = result["forward_candidates"]
    fwd_ns = result["forward_no_spot"]
    rev = result["reverse_candidates"]
    rev_nb = result["reverse_not_borrowable"]
    near_fwd = result["near_forward"]
    near_rev = result["near_reverse"]

    print(f"\n{'='*70}")
    print(
        f"{v}  pairs={result['total_pairs']}  "
        f"spot_fee={result['spot_fee_pct']}%  futures_fee={result['futures_fee_pct']}%  two_leg={fee}%"
    )
    print(f"{'='*70}")

    if fwd:
        print(f"\n  FORWARD (buy spot + open short) — {len(fwd)} tradeable:")
        for x in fwd:
            net = x["net_edge_pct"]
            flag = "PROFIT" if net > 0 else "LOSS-after-fee"
            print(
                f"    {x['base']:10s} rate={x['rate_pct']:+.4f}%  APR~{x['annual_pct']:>6.0f}%  "
                f"net={net:+.4f}% [{flag}]  px={x['spot_price']:.6f}  settle={_mins_to_settle(x['next_ts'])}"
            )
    if fwd_ns and verbose:
        print(f"\n  FORWARD no-spot ({len(fwd_ns)}):")
        for x in fwd_ns[:8]:
            print(f"    {x['base']:10s} rate={x['rate_pct']:+.4f}%  APR~{x['annual_pct']:>6.0f}%")

    if rev:
        print(f"\n  REVERSE (borrow sell + open long) — {len(rev)} borrowable:")
        for x in rev:
            net = x["net_edge_pct"]
            flag = "PROFIT" if net > 0 else "LOSS-after-cost"
            borrow = x.get("borrow_per_period_pct", 0)
            print(
                f"    {x['base']:10s} rate={x['rate_pct']:+.4f}%  borrow={borrow:.4f}%/period  "
                f"APR~{x['annual_pct']:>6.0f}%  net={net:+.4f}% [{flag}]  "
                f"borrow_y={x.get('borrow_annual_pct', 0):.0f}%  settle={_mins_to_settle(x['next_ts'])}"
            )
    if rev_nb and verbose:
        print(f"\n  REVERSE not-borrowable ({len(rev_nb)}) — Negative rate but cannot borrow to short:")
        for x in rev_nb[:8]:
            print(
                f"    {x['base']:10s} rate={x['rate_pct']:+.4f}%  APR~{x['annual_pct']:>6.0f}%  "
                f"(spot may exist but margin borrow unavailable)"
            )

    if near_fwd:
        print(f"\n  NEAR FORWARD (fee-adj break-even):")
        for x in near_fwd[:5]:
            print(
                f"    {x['base']:10s} rate={x['rate_pct']:+.4f}%  net={x['net_edge_pct']:+.4f}%  "
                f"px={x['spot_price']:.6f}"
            )
    if near_rev:
        print(f"\n  NEAR REVERSE (borrowable, below entry):")
        for x in near_rev[:5]:
            print(
                f"    {x['base']:10s} rate={x['rate_pct']:+.4f}%  borrow={x.get('borrow_per_period_pct', 0):.4f}%  "
                f"net={x['net_edge_pct']:+.4f}%"
            )

    total_fwd = len(fwd)
    total_rev = len(rev)
    profit_fwd = len([x for x in fwd if x["net_edge_pct"] > 0])
    profit_rev = len([x for x in rev if x["net_edge_pct"] > 0])
    print(
        f"\n  SUMMARY: forward={total_fwd}({profit_fwd} profitable)  "
        f"reverse={total_rev}({profit_rev} profitable after borrow cost)"
    )
    if not fwd and not rev:
        print(f"  No actionable opportunities at entry={result['entry_threshold']}%.")


def main():
    parser = argparse.ArgumentParser(description="Scan multi-exchange funding rate arbitrage opportunities")
    parser.add_argument("--venue", "-v", help="Specify exchange (bitget/bybit/okx/binance), default all")
    parser.add_argument("--entry", "-e", type=float, default=DEFAULT_ENTRY, help=f"Entry rate threshold (default {DEFAULT_ENTRY}%%)")
    parser.add_argument("--exit", "-x", type=float, default=DEFAULT_EXIT, help="Exit rate threshold")
    parser.add_argument("--universe-min", "-u", type=float, default=DEFAULT_UNIVERSE_MIN, help="Minimum rate to enter universe")
    parser.add_argument(
        "--borrow-fallback",
        type=float,
        default=DEFAULT_BORROW_ANNUAL_PCT,
        help=f"Reverse borrow annual rate fallback (%%/yr, used when live borrow rates are unavailable; default {DEFAULT_BORROW_ANNUAL_PCT})",
    )
    parser.add_argument("--verbose", "-V", action="store_true", help="Show untradeable candidates (no spot / not borrowable)")
    parser.add_argument("--workers", "-w", type=int, default=DEFAULT_IO_WORKERS, help="Parallel I/O thread count")
    parser.add_argument("--json", action="store_true", help="JSON Output")
    args = parser.parse_args()

    venues = [args.venue] if args.venue else ["binance", "bitget", "bybit", "okx"]

    def _scan_one(v: str) -> tuple[str, dict[str, Any]]:
        return v, scan_venue(
            v, args.entry, args.exit, args.universe_min, args.borrow_fallback, args.workers
        )

    scanned = run_io_parallel(
        venues, _scan_one, max_workers=len(venues), swallow_errors=True
    )
    results = [scanned[v] for v in venues if v in scanned]
    for v in venues:
        if v not in scanned:
            print(f"{v.upper()} error: scan failed", file=sys.stderr)

    if args.json:
        out = []
        for r in results:
            out.append({
                "venue": r["venue"],
                "two_leg_fee_pct": r["two_leg_fee_pct"],
                "forward": r["forward_candidates"],
                "reverse": r["reverse_candidates"],
                "reverse_not_borrowable": r["reverse_not_borrowable"],
            })
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
        print(f"\nFunding Rate Arbitrage Scanner — {now}")
        print(f"Entry: >= {args.entry}% | Exit: <= {args.exit}% | Universe min: {args.universe_min}%")
        print(f"Reverse borrow fallback: {args.borrow_fallback}%/yr (live rate takes priority)")
        print("Forward = spot required | Reverse = margin borrow required (not just spot listing)")
        for r in results:
            print_report(r, verbose=args.verbose)


if __name__ == "__main__":
    main()
