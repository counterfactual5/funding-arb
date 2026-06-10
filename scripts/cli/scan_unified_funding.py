#!/usr/bin/env python3
"""跨所资金费率套利扫描 — 三所统一池，现货腿与合约腿可拆分。

正向：futures 开空 @ funding 最高所 + spot 买入 @ 成本最低所
反向：futures 开多 @ funding 最低所 + margin 借卖 @ 借率最低所

用法:
  python3 scripts/cli/scan_unified_funding.py
  python3 scripts/cli/scan_unified_funding.py --entry 0.03 --verbose
  python3 scripts/cli/scan_unified_funding.py --compare   # 同所 vs 跨所对比
  python3 scripts/cli/scan_unified_funding.py --json
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

from backtest.unified_funding_pool import (
    DEFAULT_BORROW_FALLBACK_ANNUAL_PCT,
    DEFAULT_IO_WORKERS,
    DEFAULT_REFERENCE_TRADE_USD,
    DEFAULT_VENUES,
    CrossRoute,
    UnifiedFundingPool,
)

TZ = timezone(timedelta(hours=8))
DEFAULT_ENTRY = 0.05
DEFAULT_UNIVERSE_MIN = 0.03


def _mins_to_settle(ts_ms: int) -> str:
    if ts_ms <= 0:
        return "?"
    mins = (ts_ms - time.time() * 1000) / 60000
    if mins < 0:
        return "settling"
    if mins < 60:
        return f"{mins:.0f}m"
    return f"{mins / 60:.1f}h"


def _route_leg_str(route: CrossRoute) -> str:
    if route.same_venue:
        return f"{route.futures_venue} (同所)"
    return f"fut={route.futures_venue}  spot/margin={route.spot_venue}"


def _print_route(route: CrossRoute) -> None:
    edge = route.net_edge_all_in_pct if not route.same_venue else route.net_edge_pct
    flag = "PROFIT" if edge > 0 else "LOSS"
    legs = _route_leg_str(route)
    xfer = ""
    if not route.same_venue and route.transfer_chain:
        xfer = f"  xfer={route.transfer_chain.upper()}({route.transfer_fee_pct:.3f}%)"
    if route.direction == "forward":
        print(
            f"    {route.base:10s} rate={route.funding_rate_pct:+.4f}%  "
            f"net={route.net_edge_pct:+.4f}%  all-in={route.net_edge_all_in_pct:+.4f}% [{flag}]  "
            f"APR~{route.annual_funding_pct:.0f}%  fee={route.total_fee_pct:.2f}%{xfer}  "
            f"{legs}  settle={_mins_to_settle(route.next_funding_ts)}"
        )
    else:
        print(
            f"    {route.base:10s} rate={route.funding_rate_pct:+.4f}%  "
            f"borrow={route.borrow_per_period_pct:.4f}%/period (y={route.borrow_annual_pct:.0f}%)  "
            f"net={route.net_edge_pct:+.4f}%  all-in={route.net_edge_all_in_pct:+.4f}% [{flag}]  "
            f"APR~{route.annual_funding_pct:.0f}%  fee={route.total_fee_pct:.2f}%{xfer}  "
            f"{legs}  settle={_mins_to_settle(route.next_funding_ts)}"
        )


def print_report(
    pool: UnifiedFundingPool,
    routes: dict[str, list[CrossRoute]],
    compare: list[dict[str, Any]] | None,
    entry: float,
    verbose: bool,
) -> None:
    venues = ", ".join(pool.venues)
    fwd = [r for r in routes["forward"] if r.net_edge_pct > 0 or verbose]
    rev = [r for r in routes["reverse"] if r.net_edge_pct > 0 or verbose]

    print(f"\n{'=' * 78}")
    print(f"UNIFIED POOL  venues=[{venues}]  assets={len(pool.legs_by_base)}  entry>={entry}%")
    print(f"{'=' * 78}")

    cross_fwd = [r for r in routes["forward"] if not r.same_venue]
    cross_rev = [r for r in routes["reverse"] if not r.same_venue]

    if fwd:
        n_cross = len([r for r in fwd if not r.same_venue])
        print(f"\n  FORWARD (spot买 + perp空) — {len(fwd)} routes, {n_cross} cross-venue:")
        for r in routes["forward"][:20]:
            if r.net_edge_pct > 0 or verbose:
                _print_route(r)
    else:
        print("\n  FORWARD: 无满足条件的跨所路由")

    if rev:
        n_cross = len([r for r in rev if not r.same_venue])
        print(f"\n  REVERSE (margin借卖 + perp多) — {len(rev)} routes, {n_cross} cross-venue:")
        for r in routes["reverse"][:20]:
            if r.net_edge_pct > 0 or verbose:
                _print_route(r)
    else:
        print("\n  REVERSE: 无满足条件的跨所路由")

    profit_fwd = len([r for r in routes["forward"] if r.net_edge_pct > 0])
    profit_rev = len([r for r in routes["reverse"] if r.net_edge_pct > 0])
    print(
        f"\n  SUMMARY: forward={len(routes['forward'])}({profit_fwd} profitable)  "
        f"reverse={len(routes['reverse'])}({profit_rev} profitable)  "
        f"cross-venue fwd={len(cross_fwd)} rev={len(cross_rev)}"
    )

    if compare:
        improved = [c for c in compare if c["improvement_pct"] > 0.001]
        if improved:
            print(f"\n  CROSS vs SINGLE-VENUE (improvement > 0):")
            for c in improved[:12]:
                same = "同所" if c["cross_same_venue"] else "跨所"
                print(
                    f"    {c['base']:10s} {c['direction']:8s}  single@{c['single_venue']} "
                    f"net={c['single_net_pct']:+.4f}%  →  {same} fut@{c['cross_futures_venue']} "
                    f"spot@{c['cross_spot_venue']} net={c['cross_net_pct']:+.4f}%  "
                    f"(+{c['improvement_pct']:.4f}%)"
                )

    print("\n  EXECUTION MODEL:")
    print("    · 跨所 all-in 净边际已扣链上提现费（按 reference_trade_usd 估算）")
    print("    · 完整编排: scripts/cli/orchestrate_funding.py（scan→transfer→execute）")
    print("    · 反向仅 spot_venue 需支持 margin 借币；futures_venue 只负责收 funding")


def main() -> None:
    parser = argparse.ArgumentParser(description="跨所资金费率套利统一池扫描")
    parser.add_argument(
        "--venues",
        default=",".join(DEFAULT_VENUES),
        help=f"逗号分隔交易所，默认 {','.join(DEFAULT_VENUES)}",
    )
    parser.add_argument("--entry", "-e", type=float, default=DEFAULT_ENTRY)
    parser.add_argument("--universe-min", "-u", type=float, default=DEFAULT_UNIVERSE_MIN)
    parser.add_argument(
        "--borrow-fallback",
        type=float,
        default=DEFAULT_BORROW_FALLBACK_ANNUAL_PCT,
        help="无 live 借率时的年化 fallback (%%)",
    )
    parser.add_argument("--compare", action="store_true", help="输出同所 vs 跨所对比")
    parser.add_argument("--workers", "-w", type=int, default=DEFAULT_IO_WORKERS, help="并行 I/O 线程数")
    parser.add_argument(
        "--reference-trade-usd",
        type=float,
        default=DEFAULT_REFERENCE_TRADE_USD,
        help="跨所链上费估算名义金额 (USD)",
    )
    parser.add_argument("--verbose", "-V", action="store_true", help="含净边际<=0 的路由")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    venues = tuple(v.strip().lower() for v in args.venues.split(",") if v.strip())
    pool = UnifiedFundingPool(
        venues=venues,
        borrow_fallback_annual_pct=args.borrow_fallback,
        max_workers=args.workers,
        reference_trade_usd=args.reference_trade_usd,
    )

    t0 = time.time()
    print(f"Fetching {len(venues)} venues (workers={args.workers})...", file=sys.stderr)
    pool.refresh(universe_min=args.universe_min)
    routes = pool.scan_routes(entry=args.entry, universe_min=args.universe_min)
    compare = pool.compare_single_vs_cross(entry=args.entry) if args.compare else None
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s", file=sys.stderr)

    if args.json:
        out: dict[str, Any] = {
            "venues": list(venues),
            "entry": args.entry,
            "forward": [r.to_dict() for r in routes["forward"]],
            "reverse": [r.to_dict() for r in routes["reverse"]],
        }
        if compare:
            out["compare"] = compare
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    print(f"\nUnified Funding Arbitrage — {now}")
    print(f"Entry>={args.entry}%  universe_min={args.universe_min}%  borrow_fallback={args.borrow_fallback}%/yr")
    print_report(pool, routes, compare, args.entry, args.verbose)


if __name__ == "__main__":
    main()
