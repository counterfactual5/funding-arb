#!/usr/bin/env python3
"""资金费率套利编排 — 扫描 → 余额检查 → 跨所划转 → 执行（dry-run 默认）。

流程:
  1. UnifiedFundingPool 扫描最优路由（含链上提现费估算）
  2. 检查各所 USDT 余额，不足则规划最低费链划转
  3. 同所路由：调用 run_cash_and_carry（paper/live）
  4. 跨所路由：--run-executor 调 cross_venue_executor 自动双腿（--live-trades 实盘）

用法:
  python3 scripts/cli/orchestrate_funding.py --venues bitget,bybit
  python3 scripts/cli/orchestrate_funding.py --base BTC --trade-usd 500 --direction forward
  python3 scripts/cli/orchestrate_funding.py --execute-transfer --poll-deposit   # 真实提现+轮询
  python3 scripts/cli/orchestrate_funding.py --run-executor --config templates/config.cash_and_carry.bitget.json
"""
from __future__ import annotations

import argparse
import json
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
from transfer.transfer_providers import get_transfer_provider, poll_deposit_until  # noqa: E402

TZ = timezone(timedelta(hours=8))
Direction = Literal["forward", "reverse", "auto"]
VENUE_TEMPLATES = {
    "bitget": "templates/config.cash_and_carry.bitget.json",
    "bybit": "templates/config.cash_and_carry.bybit.json",
    "okx": "templates/config.cash_and_carry.okx.json",
    "binance": "templates/config.cash_and_carry.binance.reverse.json",
}


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
    """估算各所 USDT 需求（保守：每腿 trade_usd + 10% buffer）。"""
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
    """从余额充裕所向不足所规划 USDT 划转。"""
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
            f"同所 {route.futures_venue}: 运行 run_cash_and_carry "
            f"(template={tpl or 'n/a'}) base={route.base} trade_usd≈{trade_usd}"
        )
    else:
        if route.direction == "forward":
            notes.append(
                f"跨所 FORWARD: {route.futures_venue} 开空 {route.base} perp "
                f"+ {route.spot_venue} 现货买入 {route.base} (~{trade_usd} USD)"
            )
        else:
            notes.append(
                f"跨所 REVERSE: {route.futures_venue} 开多 {route.base} perp "
                f"+ {route.spot_venue} margin 借卖 {route.base}"
            )
        if route.transfer_chain:
            notes.append(
                f"资金路由: 优先 {route.transfer_chain.upper()} "
                f"(est fee {route.transfer_fee_usdt:.4f} USDT / {route.transfer_fee_pct:.3f}%)"
            )
        notes.append(
            "跨所自动下单: --run-executor 模拟双腿，--run-executor --live-trades 实盘开仓"
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
    same = "同所" if r.same_venue else "跨所"
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
        print("\n  [DRY-RUN] 未发起划转或下单")


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


def main() -> None:
    parser = argparse.ArgumentParser(description="资金费率套利编排 scan→transfer→execute")
    parser.add_argument("--venues", default=",".join(DEFAULT_VENUES))
    parser.add_argument("--entry", "-e", type=float, default=0.05)
    parser.add_argument("--universe-min", "-u", type=float, default=0.03)
    parser.add_argument("--trade-usd", type=float, default=DEFAULT_REFERENCE_TRADE_USD)
    parser.add_argument("--min-all-in", type=float, default=0.0, help="最低 all-in 净边际 (%%)")
    parser.add_argument("--base", default="", help="指定标的，默认自动选最优")
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
        help="真实发起链上提现（默认仅展示计划）",
    )
    parser.add_argument("--poll-deposit", action="store_true", help="提现后轮询充值到账")
    parser.add_argument("--poll-timeout", type=int, default=600)
    parser.add_argument(
        "--run-executor",
        action="store_true",
        help="同所路由运行 run_cash_and_carry；跨所路由运行 cross_venue_executor",
    )
    parser.add_argument(
        "--live-trades",
        action="store_true",
        help="跨所执行真实下单（默认双腿 dry-run 模拟）",
    )
    parser.add_argument("--config", default="", help="run_cash_and_carry 配置路径")
    parser.add_argument("--verbose", "-V", action="store_true")
    args = parser.parse_args()

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
        print("无满足条件的路由", file=sys.stderr)
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
            print("未找到 venue 模板，请 --config 指定", file=sys.stderr)
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
                print(f"余额不足，拒绝实盘开仓: {names}", file=sys.stderr)
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
            print(f"    position_id={result.position_id}  (平仓: cross_venue_trade.py close {result.position_id})")
        sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
