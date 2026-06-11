#!/usr/bin/env python3
"""Funding rate arbitrage orchestration — scan → balance check → cross-venue transfer → execute (dry-run by default).

Flow:
  1. UnifiedFundingPool scans for optimal routes (including on-chain withdrawal fee estimation)
  2. Check USDT balance at each venue; if insufficient, plan minimum-fee chain transfers
  3. Same-venue routes: run run_cash_and_carry (paper/live)
  4. Cross-venue routes: --run-executor invokes cross_venue_executor for automatic two-leg execution (--live-trades for live)
  5. --pure-futures: pure perp funding rate spread scan + execution (no spot/borrow/transfer)

Usage:
  python3 scripts/cli/orchestrate_funding.py --venues bitget,bybit
  python3 scripts/cli/orchestrate_funding.py --base BTC --trade-usd 500 --direction forward
  python3 scripts/cli/orchestrate_funding.py --execute-transfer --poll-deposit   # live withdrawal + polling
  python3 scripts/cli/orchestrate_funding.py --run-executor --config templates/config.cash_and_carry.bitget.json
  python3 scripts/cli/orchestrate_funding.py --pure-futures                     # pure perp scan only
  python3 scripts/cli/orchestrate_funding.py --pure-futures --run-executor      # + auto open
  python3 scripts/cli/orchestrate_funding.py --pure-futures --run-executor --live-trades  # live
  python3 scripts/cli/orchestrate_funding.py --pure-futures --auto-spread-watch # + background watcher
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.unified_funding_pool import (  # noqa: E402
    DEFAULT_IO_WORKERS,
    DEFAULT_REFERENCE_TRADE_USD,
    DEFAULT_VENUES,
    CrossRoute,
    UnifiedFundingPool,
)
from execution.cross_venue_executor import open_cross_venue_position  # noqa: E402
from transfer.cross_venue_router import build_plan, execute_plan  # noqa: E402
from transfer.transfer_providers import (  # noqa: E402
    get_transfer_provider,
    poll_deposit_until,
)

TZ = timezone(timedelta(hours=8))
Direction = Literal["forward", "reverse", "auto"]
VENUE_TEMPLATES = {
    "bitget": "templates/config.cash_and_carry.bitget.json",
    "bybit": "templates/config.cash_and_carry.bybit.json",
    "okx": "templates/config.cash_and_carry.okx.json",
    "binance": "templates/config.cash_and_carry.binance.reverse.json",
}
PURE_FUTURES_CONFIG = "templates/config.pure_futures.spread.json"


@dataclass
class BalanceNeed:
    venue: str
    role: str
    required_usd: float
    available_usd: float
    deficit_usd: float = 0.0

    @property
    def sufficient(self) -> bool:
        return self.deficit_usd <= 0.01


@dataclass
class OrchestrationPlan:
    route: CrossRoute
    trade_usd: float
    balances: list[BalanceNeed] = field(default_factory=list)
    transfers: list[dict[str, Any]] = field(default_factory=list)
    execution_notes: list[str] = field(default_factory=list)
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route.to_dict(),
            "trade_usd": self.trade_usd,
            "balances": [b.__dict__ for b in self.balances],
            "transfers": self.transfers,
            "execution_notes": self.execution_notes,
            "dry_run": self.dry_run,
        }


def _venue_usdt_balance(venue: str) -> float:
    return get_transfer_provider(venue).get_withdrawable_balance("USDT")


def _balance_needs(route: CrossRoute, trade_usd: float) -> list[BalanceNeed]:
    """Estimate USDT requirements per venue (conservative: each leg needs trade_usd + 10% buffer)."""
    buffer = trade_usd * 0.1
    needs: list[BalanceNeed] = []
    if route.direction == "forward":
        fut_need = trade_usd + buffer
        spot_need = trade_usd + buffer
        needs.append(
            BalanceNeed(
                venue=route.futures_venue,
                role="futures_margin",
                required_usd=fut_need,
                available_usd=_venue_usdt_balance(route.futures_venue),
            )
        )
        if route.futures_venue != route.spot_venue:
            needs.append(
                BalanceNeed(
                    venue=route.spot_venue,
                    role="spot_buy",
                    required_usd=spot_need,
                    available_usd=_venue_usdt_balance(route.spot_venue),
                )
            )
        elif route.same_venue:
            combined = trade_usd * 2.1 + buffer
            needs[0].required_usd = combined
            needs[0].role = "futures+spot"
    else:
        fut_need = trade_usd + buffer
        margin_need = trade_usd * 0.5 + buffer
        needs.append(
            BalanceNeed(
                venue=route.futures_venue,
                role="futures_margin",
                required_usd=fut_need,
                available_usd=_venue_usdt_balance(route.futures_venue),
            )
        )
        if route.futures_venue != route.spot_venue:
            needs.append(
                BalanceNeed(
                    venue=route.spot_venue,
                    role="margin_collateral",
                    required_usd=margin_need,
                    available_usd=_venue_usdt_balance(route.spot_venue),
                )
            )
    for n in needs:
        n.deficit_usd = max(0.0, n.required_usd - n.available_usd)
    return needs


def _plan_transfers(
    balances: list[BalanceNeed],
    venues: tuple[str, ...],
    trade_usd: float,
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Plan USDT transfers from venues with surplus to those with deficit."""
    plans: list[dict[str, Any]] = []
    donors = sorted(
        [b for b in balances if b.available_usd > b.required_usd + 5],
        key=lambda x: -(x.available_usd - x.required_usd),
    )
    for need in balances:
        if need.sufficient:
            continue
        amount = need.deficit_usd
        donor_venue = None
        for d in donors:
            if d.venue == need.venue:
                continue
            surplus = d.available_usd - d.required_usd
            if surplus >= amount:
                donor_venue = d.venue
                break
        if donor_venue is None:
            for v in venues:
                if v == need.venue:
                    continue
                bal = _venue_usdt_balance(v)
                if bal >= amount + 5:
                    donor_venue = v
                    break
        if donor_venue is None:
            plans.append(
                {
                    "from": None,
                    "to": need.venue,
                    "amount": round(amount, 2),
                    "viable": False,
                    "note": f"no donor with >= {amount:.2f} USDT",
                }
            )
            continue
        xfer_plan = build_plan(donor_venue, need.venue, "USDT", amount, dry_run=dry_run)
        if xfer_plan is None:
            plans.append(
                {
                    "from": donor_venue,
                    "to": need.venue,
                    "amount": round(amount, 2),
                    "viable": False,
                    "note": "no common chain route",
                }
            )
            continue
        plans.append({**xfer_plan.to_dict(), "viable": xfer_plan.route.viable})
    return plans


def _pick_route(
    routes: dict[str, list[CrossRoute]],
    *,
    base: str | None,
    direction: Direction,
    min_all_in: float,
) -> CrossRoute | None:
    pool: list[CrossRoute] = []
    if direction in ("forward", "auto"):
        pool.extend(routes.get("forward") or [])
    if direction in ("reverse", "auto"):
        pool.extend(routes.get("reverse") or [])
    if base:
        pool = [r for r in pool if r.base.upper() == base.upper()]
    pool = [r for r in pool if r.net_edge_all_in_pct >= min_all_in]
    pool.sort(key=lambda r: -r.net_edge_all_in_pct)
    return pool[0] if pool else None


def _execution_notes(route: CrossRoute, trade_usd: float) -> list[str]:
    notes: list[str] = []
    if route.same_venue:
        tpl = VENUE_TEMPLATES.get(route.futures_venue)
        notes.append(
            f"Same-venue {route.futures_venue}: run run_cash_and_carry "
            f"(template={tpl or 'n/a'}) base={route.base} trade_usd≈{trade_usd}"
        )
    else:
        if route.direction == "forward":
            notes.append(
                f"Cross-venue FORWARD: {route.futures_venue} open short {route.base} perp "
                f"+ {route.spot_venue} spot buy {route.base} (~{trade_usd} USD)"
            )
        else:
            notes.append(
                f"Cross-venue REVERSE: {route.futures_venue} open long {route.base} perp "
                f"+ {route.spot_venue} margin borrow-sell {route.base}"
            )
        if route.transfer_chain:
            notes.append(
                f"Funding route: prefer {route.transfer_chain.upper()} "
                f"(est fee {route.transfer_fee_usdt:.4f} USDT / {route.transfer_fee_pct:.3f}%)"
            )
        notes.append(
            "Cross-venue auto-trade: --run-executor simulates both legs, --run-executor --live-trades opens live"
        )
    return notes


def build_orchestration_plan(
    pool: UnifiedFundingPool,
    routes: dict[str, list[CrossRoute]],
    *,
    base: str | None,
    direction: Direction,
    trade_usd: float,
    min_all_in: float,
    dry_run: bool,
) -> OrchestrationPlan | None:
    route = _pick_route(routes, base=base, direction=direction, min_all_in=min_all_in)
    if route is None:
        return None
    balances = _balance_needs(route, trade_usd)
    transfers = _plan_transfers(balances, pool.venues, trade_usd, dry_run=dry_run)
    notes = _execution_notes(route, trade_usd)
    return OrchestrationPlan(
        route=route,
        trade_usd=trade_usd,
        balances=balances,
        transfers=transfers,
        execution_notes=notes,
        dry_run=dry_run,
    )


def _print_plan(plan: OrchestrationPlan) -> None:
    r = plan.route
    print(f"\n{'=' * 72}")
    print(f"ORCHESTRATION  {r.direction.upper()}  {r.base}  trade_usd={plan.trade_usd}")
    print(f"{'=' * 72}")
    legs = f"fut={r.futures_venue}  spot/margin={r.spot_venue}"
    same = "same-venue" if r.same_venue else "cross-venue"
    print(
        f"  route: {same}  {legs}\n"
        f"  funding={r.funding_rate_pct:+.4f}%  net={r.net_edge_pct:+.4f}%  "
        f"all-in={r.net_edge_all_in_pct:+.4f}%"
    )
    if not r.same_venue and r.transfer_chain:
        print(
            f"  transfer: {r.transfer_chain.upper()}  "
            f"fee≈{r.transfer_fee_usdt:.4f} USDT ({r.transfer_fee_pct:.3f}%)"
        )
    print("\n  BALANCES:")
    for b in plan.balances:
        flag = "OK" if b.sufficient else f"NEED +{b.deficit_usd:.2f}"
        print(
            f"    {b.venue:8s} [{b.role:16s}]  "
            f"avail={b.available_usd:.2f}  req={b.required_usd:.2f}  {flag}"
        )
    if plan.transfers:
        print("\n  TRANSFERS:")
        for i, t in enumerate(plan.transfers, 1):
            if not t.get("viable", True):
                print(f"    [{i}] SKIP  {t.get('note', 'not viable')}")
                continue
            print(
                f"    [{i}] {t.get('from_venue')}→{t.get('to_venue')}  "
                f"{t.get('amount')} USDT via {str(t.get('canonical', '')).upper()}  "
                f"fee={t.get('total_fee', 0):.4f}"
            )
    print("\n  EXECUTION:")
    for line in plan.execution_notes:
        print(f"    · {line}")
    if plan.dry_run:
        print("\n  [DRY-RUN] No transfers or orders submitted")


def _print_pure_futures_plan(
    scan_result: dict[str, Any],
    top: int = 10,
) -> None:
    """Print pure futures scan results."""
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    all_rows = list(scan_result.get("forward", [])) + list(
        scan_result.get("reverse", [])
    )
    all_rows.sort(key=lambda x: -float(x.get("net_edge_pct", 0)))

    print(f"\n{'=' * 96}")
    print(f"PURE-FUTURES ORCHESTRATION  —  {now}")
    print(f"{'=' * 96}")
    print(
        f"  venues={scan_result.get('venues')}  assets={scan_result.get('total_assets_scanned')}  "
        f"spreads={scan_result.get('total_spreads_found')}"
    )

    if not all_rows:
        print("\n  No profitable spreads found.")
        return

    print(f"\n  Top {top} candidates:")
    print(
        f"  {'#':>3s}  {'asset':<8s} {'dir':<8s} {'long@':>8s} {'short@':>8s} "
        f"{'spread':>8s} {'fee':>6s} {'net_edge':>9s} {'APY':>7s} {'mismatch':>8s}"
    )
    print("  " + "-" * 90)
    for i, x in enumerate(all_rows[:top], 1):
        mismatch_str = "YES" if x.get("settle_mismatch") else ""
        print(
            f"  {i:>3d}  {x['base']:<8s} {x.get('direction', ''):<8s} "
            f"{x.get('long_venue', ''):>8s} {x.get('short_venue', ''):>8s} "
            f"{x.get('spread_pct', 0):7.4f}% {x.get('fee_pct', 0):5.3f}% "
            f"{x.get('net_edge_pct', 0):+8.4f}% {x.get('annual_apy_pct', 0):6.0f}% "
            f"{mismatch_str:>8s}"
        )


def _run_transfers(
    plan: OrchestrationPlan,
    *,
    execute: bool,
    poll_deposit: bool,
    poll_timeout: int,
) -> list[str]:
    logs: list[str] = []
    if not execute:
        return logs
    for t in plan.transfers:
        if not t.get("viable"):
            logs.append(f"skip transfer: {t.get('note')}")
            continue
        from_v = t.get("from_venue")
        to_v = t.get("to_venue")
        amount = float(t.get("amount", 0))
        if not from_v or not to_v or amount <= 0:
            continue
        xfer = build_plan(from_v, to_v, "USDT", amount, dry_run=False)
        if xfer is None or not xfer.route.viable:
            logs.append(f"transfer {from_v}→{to_v} aborted: not viable")
            continue
        since_ms = int(time.time() * 1000)
        step_logs, result = execute_plan(xfer)
        logs.extend(step_logs)
        if result and result.ok and poll_deposit:
            ok, recs = poll_deposit_until(
                to_v,
                "USDT",
                xfer.route.net_est,
                since_ms,
                timeout_s=poll_timeout,
            )
            logs.append(
                f"poll deposit {to_v}: {'OK' if ok else 'TIMEOUT'} ({len(recs)} records)"
            )
    return logs


def _run_executor(config_path: Path, verbose: bool) -> int:
    script = ROOT / "execution" / "run_cash_and_carry.py"
    cmd = [sys.executable, str(script), "--config", str(config_path)]
    if verbose:
        cmd.append("--verbose")
    print(f"Running: {' '.join(cmd)}")
    return subprocess.call(cmd)


def _run_pure_futures_mode(args: argparse.Namespace) -> None:
    """--pure-futures mode: scan perp spreads → optional open → optional watcher start."""
    from cli.scan_pure_futures_spreads import scan_pure_futures_spreads  # noqa: E402
    from execution.pure_futures_executor import open_pure_futures_pair  # noqa: E402
    from execution.settle_mismatch_planner import (  # noqa: E402
        effective_trade_usd,
        filter_candidates_with_mismatch,
    )

    venues = [v.strip().lower() for v in args.venues.split(",") if v.strip()]
    min_spread = float(getattr(args, "pf_min_spread", 0.05))
    min_edge = float(getattr(args, "pf_min_edge", 0.01))

    # Load config for pure-futures if available
    cfg: dict[str, Any] = {}
    cfg_path = Path(args.config) if args.config else None
    if cfg_path and cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    else:
        default_cfg = ROOT.parent / PURE_FUTURES_CONFIG
        if default_cfg.exists():
            cfg = json.loads(default_cfg.read_text(encoding="utf-8"))

    pfa = cfg.get("pureFuturesArbitrage") or {}
    if not args.config:
        # Allow CLI flags to override config
        pfa.setdefault("venues", venues)
        pfa.setdefault("tradeUsdPerPair", args.trade_usd)

    trade_usd = float(pfa.get("tradeUsdPerPair", args.trade_usd))
    max_pairs = int(pfa.get("maxConcurrentPairs", 3))
    exit_edge = float(pfa.get("exitThresholdPct", 0.01))
    max_mark_spread = float(pfa.get("maxMarkSpreadPct", 1.0))
    allow_mismatch = bool(pfa.get("allowSettleMismatch", False))

    t0 = time.time()
    print(f"Scanning {len(venues)} venues for pure-futures spreads...", file=sys.stderr)
    scan = scan_pure_futures_spreads(
        venues=venues,
        min_spread=min_spread,
        min_edge=min_edge,
        max_mark_spread_pct=max_mark_spread,
        workers=args.workers,
    )
    elapsed = time.time() - t0
    print(f"Scan done in {elapsed:.1f}s", file=sys.stderr)

    if args.json:
        # JSON output
        print(json.dumps(scan, ensure_ascii=False, indent=2))
    else:
        _print_pure_futures_plan(scan)

    # Run executor: open top-N pairs
    if args.run_executor:
        all_rows = list(scan.get("forward", [])) + list(scan.get("reverse", []))
        candidates = filter_candidates_with_mismatch(
            all_rows,
            allow_mismatch=allow_mismatch,
            max_cumulative_outflow_pct=0.5,
            min_adjusted_edge_pct=min_edge,
        )
        candidates.sort(
            key=lambda x: (
                -float(x.get("adjusted_net_edge_pct", 0) or x.get("net_edge_pct", 0))
            )
        )

        opened = 0
        for row in candidates[:max_pairs]:
            dry_run = not args.live_trades
            row_trade_usd = effective_trade_usd(trade_usd, row)
            res = open_pure_futures_pair(
                str(row["base"]),
                str(row["long_venue"]),
                str(row["short_venue"]),
                row_trade_usd,
                dry_run=dry_run,
                direction=str(row.get("direction", "forward")),
                max_mark_spread_pct=max_mark_spread,
                config=cfg,
                capital_buffer_pct=float(row.get("capital_buffer_pct", 0) or 0),
            )
            status = "OK" if res.ok else f"FAIL({res.state})"
            edge = float(
                row.get("adjusted_net_edge_pct", 0) or row.get("net_edge_pct", 0)
            )
            print(
                f"  OPEN {row['base']} long@{row['long_venue']} short@{row['short_venue']} "
                f"edge={edge:+.4f}% usd={row_trade_usd:.0f} → {status} {res.position_id}"
            )
            if res.ok:
                opened += 1
            for log in res.logs:
                print(f"    · {log}")

        if not args.live_trades:
            print(f"\n  [DRY-RUN] {opened} pairs simulated")

    # Start watcher if requested
    if getattr(args, "auto_spread_watch", False):
        from execution.pure_futures_watcher import main as watcher_main  # noqa: E402

        # Rebuild argv for watcher
        watcher_cfg = args.config or str(ROOT.parent / PURE_FUTURES_CONFIG)
        watcher_argv = [
            "--config",
            watcher_cfg,
            "--interval",
            str(getattr(args, "watch_interval", 60)),
        ]
        if not args.live_trades:
            watcher_argv.append("--dry-run")
        if args.verbose:
            watcher_argv.append("--verbose")

        print(f"\n  Starting watcher (config={watcher_cfg})...", file=sys.stderr)
        sys.argv = ["pure_futures_watcher"] + watcher_argv
        raise SystemExit(watcher_main())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Funding rate arbitrage orchestration scan→transfer→execute"
    )
    parser.add_argument("--venues", default=",".join(DEFAULT_VENUES))
    parser.add_argument("--entry", "-e", type=float, default=0.05)
    parser.add_argument("--universe-min", "-u", type=float, default=0.03)
    parser.add_argument("--trade-usd", type=float, default=DEFAULT_REFERENCE_TRADE_USD)
    parser.add_argument(
        "--min-all-in", type=float, default=0.0, help="Minimum all-in net edge (%%)"
    )
    parser.add_argument(
        "--base", default="", help="Specify asset; defaults to auto-select best"
    )
    parser.add_argument(
        "--direction",
        choices=("auto", "forward", "reverse"),
        default="auto",
    )
    parser.add_argument("--workers", "-w", type=int, default=DEFAULT_IO_WORKERS)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--execute-transfer",
        action="store_true",
        help="Initiate live on-chain withdrawal (default: show plan only)",
    )
    parser.add_argument(
        "--poll-deposit",
        action="store_true",
        help="Poll for deposit arrival after withdrawal",
    )
    parser.add_argument("--poll-timeout", type=int, default=600)
    parser.add_argument(
        "--run-executor",
        action="store_true",
        help="Same-venue routes run run_cash_and_carry; cross-venue routes run cross_venue_executor; "
        "with --pure-futures, auto-opens top-N pairs",
    )
    parser.add_argument(
        "--live-trades",
        action="store_true",
        help="Cross-venue live order execution (default: both legs dry-run simulated)",
    )
    parser.add_argument("--config", default="", help="Config file path")
    parser.add_argument("--verbose", "-V", action="store_true")

    # ── Pure Futures options ────────────────────────────────────────────────
    pf = parser.add_argument_group(
        "pure-futures", "Pure perp funding rate spread arbitrage options"
    )
    pf.add_argument(
        "--pure-futures",
        action="store_true",
        help="Scan pure perp funding rate spread opportunities only (no spot/borrow/transfer), use with --run-executor to open",
    )
    pf.add_argument(
        "--pf-min-spread",
        type=float,
        default=0.05,
        help="Min spread %% (default 0.05)",
    )
    pf.add_argument(
        "--pf-min-edge",
        type=float,
        default=0.01,
        help="Min net edge %% (default 0.01)",
    )
    pf.add_argument(
        "--auto-spread-watch",
        action="store_true",
        help="--pure-futures mode starts a background watcher process",
    )
    pf.add_argument(
        "--watch-interval",
        type=float,
        default=60,
        help="Watcher check interval seconds (default 60)",
    )

    args = parser.parse_args()

    # ── Pure Futures fast path ────────────────────────────────────────────
    if args.pure_futures:
        _run_pure_futures_mode(args)
        return

    # ── Original cash-and-carry logic below ──────────────────────────────────
    venues = tuple(v.strip().lower() for v in args.venues.split(",") if v.strip())
    dry_run = not args.execute_transfer
    base = args.base.strip().upper() or None

    pool = UnifiedFundingPool(
        venues=venues,
        max_workers=args.workers,
        reference_trade_usd=args.trade_usd,
    )
    t0 = time.time()
    print(f"Scanning {len(venues)} venues...", file=sys.stderr)
    pool.refresh(universe_min=args.universe_min)
    routes = pool.scan_routes(entry=args.entry, universe_min=args.universe_min)
    print(f"Scan done in {time.time() - t0:.1f}s", file=sys.stderr)

    plan = build_orchestration_plan(
        pool,
        routes,
        base=base,
        direction=args.direction,  # type: ignore[arg-type]
        trade_usd=args.trade_usd,
        min_all_in=args.min_all_in,
        dry_run=dry_run,
    )
    if plan is None:
        print("No qualifying routes found", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    else:
        now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
        print(f"\nFunding Orchestrator — {now}")
        _print_plan(plan)

    xfer_logs = _run_transfers(
        plan,
        execute=args.execute_transfer,
        poll_deposit=args.poll_deposit,
        poll_timeout=args.poll_timeout,
    )
    for line in xfer_logs:
        print(line)

    if args.run_executor and plan.route.same_venue:
        cfg = args.config
        if not cfg:
            cfg = VENUE_TEMPLATES.get(plan.route.futures_venue, "")
        if not cfg:
            print("Venue template not found, please specify --config", file=sys.stderr)
        else:
            cfg_path = Path(cfg)
            if not cfg_path.is_absolute():
                for base in (SKILL_ROOT, ROOT):
                    candidate = base / cfg
                    if candidate.exists():
                        cfg_path = candidate
                        break
            rc = _run_executor(cfg_path.resolve(), args.verbose)
            sys.exit(rc)

    if not plan.route.same_venue and args.run_executor:
        r = plan.route
        if args.live_trades:
            blockers = [b for b in plan.balances if not b.sufficient]
            if blockers:
                names = ", ".join(f"{b.venue}(-{b.deficit_usd:.2f})" for b in blockers)
                print(
                    f"Insufficient balance, rejecting live open: {names}",
                    file=sys.stderr,
                )
                sys.exit(2)
        result = open_cross_venue_position(
            r.base,
            r.direction,
            r.futures_venue,
            r.spot_venue,
            args.trade_usd,
            dry_run=not args.live_trades,
        )
        print(f"\n  CROSS-VENUE EXECUTION  state={result.state}  ok={result.ok}")
        for line in result.logs:
            print(f"    · {line}")
        if result.position_id:
            print(
                f"    position_id={result.position_id}  (close: cross_venue_trade.py close {result.position_id})"
            )
        sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
