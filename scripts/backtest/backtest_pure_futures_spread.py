#!/usr/bin/env python3
"""Pure Futures Spread 回测驱动 — 历史资金费数据回放。

功能:
  - 加载 JSONL 格式的历史资金费快照（由 scan_pure_futures_spreads --watch 采集）
  - 回放每个时刻的价差变化，模拟开仓/平仓/持仓
  - 资金费按各腿真实结算周期 (interval_h) 在 UTC 对齐的结算边界逐腿累计，
    与快照采集频率无关（5 分钟采集和 8 小时采集结果一致）
  - settle_mismatch 候选接入 planner：用 adjusted_net_edge 做开仓门槛
  - 统计总收益、年化、最大回撤、Sharpe、胜率、平均持仓天数

用法:
  # 使用已有的 scanner JSONL 数据
  python3 scripts/backtest/backtest_pure_futures_spread.py \
    --jsonl-file data/pure_futures_spreads.jsonl

  # 指定参数
  python3 scripts/backtest/backtest_pure_futures_spread.py \
    --jsonl-file data/pure_futures_spreads.jsonl \
    --capital 100000 --trade-usd 5000 --min-spread 0.08 --exit-edge 0.02

  # 输出 JSON
  python3 scripts/backtest/backtest_pure_futures_spread.py --jsonl-file data/spreads.jsonl --json
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

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.settle_mismatch_planner import analyze_settle_mismatch  # noqa: E402


@dataclass
class OpenPair:
    """回测中已开仓的配对。"""

    pair_id: str
    base: str
    long_venue: str
    short_venue: str
    direction: str
    amount_usd: float
    open_edge_pct: float
    open_spread_pct: float
    open_ts: datetime
    open_fee_pct: float
    # Per-leg funding state (interval-accurate accrual)
    long_rate_pct: float = 0.0
    short_rate_pct: float = 0.0
    long_interval_h: float = 8.0
    short_interval_h: float = 8.0
    last_accrual_ts: datetime | None = None
    long_settlements: int = 0
    short_settlements: int = 0
    accumulated_funding_pct: float = 0.0


def _settlements_crossed(t0: datetime, t1: datetime, interval_h: float) -> int:
    """(t0, t1] 之间跨过的结算边界数。

    边界按 UTC epoch 对齐（如 8h → 00:00/08:00/16:00），与真实交易所一致。
    """
    if interval_h <= 0 or t1 <= t0:
        return 0
    ih = interval_h * 3600.0
    return int(math.floor(t1.timestamp() / ih) - math.floor(t0.timestamp() / ih))


def _update_pair_rates(pair: OpenPair, row: dict[str, Any]) -> None:
    """用最新快照的费率/周期刷新持仓腿状态。"""
    pair.long_rate_pct = float(row.get("long_rate_pct", pair.long_rate_pct) or 0.0)
    pair.short_rate_pct = float(row.get("short_rate_pct", pair.short_rate_pct) or 0.0)
    pair.long_interval_h = float(
        row.get("long_interval_h", pair.long_interval_h) or 8.0
    )
    pair.short_interval_h = float(
        row.get("short_interval_h", pair.short_interval_h) or 8.0
    )


def _accrue_funding(pair: OpenPair, ts: datetime) -> None:
    """按各腿结算周期累计资金费。

    多头腿每次结算支付 long_rate（费率为负则收取）；
    空头腿每次结算收取 short_rate（费率为负则支付）。
    """
    t0 = pair.last_accrual_ts or pair.open_ts
    n_long = _settlements_crossed(t0, ts, pair.long_interval_h)
    n_short = _settlements_crossed(t0, ts, pair.short_interval_h)
    if n_long or n_short:
        pair.accumulated_funding_pct += (
            n_short * pair.short_rate_pct - n_long * pair.long_rate_pct
        )
        pair.long_settlements += n_long
        pair.short_settlements += n_short
    pair.last_accrual_ts = ts


@dataclass
class ClosedTrade:
    """已平仓的交易记录。"""

    pair_id: str
    base: str
    direction: str
    open_ts: datetime
    close_ts: datetime
    holding_hours: float
    open_edge_pct: float
    close_edge_pct: float
    total_funding_pct: float
    total_fee_pct: float
    net_pnl_pct: float
    amount_usd: float
    close_reason: str
    win: bool
    long_settlements: int = 0
    short_settlements: int = 0


@dataclass
class BacktestResult:
    total_return_pct: float
    annual_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    trade_count: int
    win_count: int
    win_rate_pct: float
    avg_holding_hours: float
    avg_pnl_per_trade_pct: float
    total_funding_collected_pct: float
    total_fees_paid_pct: float
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_return_pct": round(self.total_return_pct, 4),
            "annual_return_pct": round(self.annual_return_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "win_rate_pct": round(self.win_rate_pct, 1),
            "avg_holding_hours": round(self.avg_holding_hours, 1),
            "avg_pnl_per_trade_pct": round(self.avg_pnl_per_trade_pct, 4),
            "total_funding_collected_pct": round(self.total_funding_collected_pct, 4),
            "total_fees_paid_pct": round(self.total_fees_paid_pct, 4),
            "trades": self.trades[:50],
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


def _opp_key(row: dict[str, Any]) -> str:
    return ":".join([
        str(row.get("base", "")).upper(),
        str(row.get("direction", "")),
        str(row.get("long_venue", "")),
        str(row.get("short_venue", "")),
    ])


def _iter_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for direction in ("forward", "reverse"):
        for row in snapshot.get(direction, []) or []:
            item = dict(row)
            item["direction"] = item.get("direction") or direction
            rows.append(item)
    for row in snapshot.get("spreads", []) or []:
        item = dict(row)
        item["direction"] = item.get("direction") or "unknown"
        rows.append(item)
    return rows


def load_snapshots(jsonl_path: Path) -> list[dict[str, Any]]:
    """加载 JSONL 快照。"""
    if not jsonl_path.exists():
        raise FileNotFoundError(f"JSONL not found: {jsonl_path}")

    snapshots: list[dict[str, Any]] = []
    with jsonl_path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"skip line {line_no}: {e}", file=sys.stderr)
                continue
            ts = _parse_ts(snap.get("timestamp"))
            if ts is None:
                continue
            snap["_ts"] = ts
            snapshots.append(snap)
    snapshots.sort(key=lambda x: x["_ts"])
    return snapshots


def run_backtest(
    snapshots: list[dict[str, Any]],
    *,
    initial_capital: float = 100000.0,
    trade_usd: float = 5000.0,
    max_concurrent_pairs: int = 3,
    min_spread_pct: float = 0.05,
    min_edge_pct: float = 0.01,
    exit_edge_pct: float = 0.01,
    max_holding_hours: float = 720.0,  # 30 days
    allow_mismatch: bool = False,
    verbose: bool = False,
) -> BacktestResult:
    """回放历史快照，模拟纯永续资金费差策略。"""
    capital = initial_capital
    equity_curve: list[dict[str, Any]] = []
    open_pairs: dict[str, OpenPair] = {}
    closed_trades: list[ClosedTrade] = []
    peak_equity = capital
    max_drawdown = 0.0

    # Track per-snapshot PnL for Sharpe
    daily_returns: list[float] = []
    prev_date: str | None = None
    day_start_equity = capital

    FEE_RATES = {"bitget": 0.06, "binance": 0.05, "okx": 0.05, "bybit": 0.055}

    for snap_idx, snap in enumerate(snapshots):
        ts: datetime = snap["_ts"]
        current_date = ts.strftime("%Y-%m-%d")
        rows = _iter_rows(snap)

        # Unfiltered lookup（退出判断 / 费率刷新用，不受入场阈值影响）
        all_by_key: dict[str, dict[str, Any]] = {_opp_key(r): r for r in rows}

        # Entry candidates（受入场阈值过滤；mismatch 走 planner 调整后边际）
        current_spreads: dict[str, dict[str, Any]] = {}
        for row in rows:
            edge = float(row.get("net_edge_pct", 0))
            if row.get("settle_mismatch"):
                if not allow_mismatch:
                    continue
                analysis = analyze_settle_mismatch(
                    base=str(row.get("base", "")),
                    long_venue=str(row.get("long_venue", "")),
                    short_venue=str(row.get("short_venue", "")),
                    long_rate_pct=float(row.get("long_rate_pct", 0) or 0),
                    short_rate_pct=float(row.get("short_rate_pct", 0) or 0),
                    long_interval_h=float(row.get("long_interval_h", 8) or 8),
                    short_interval_h=float(row.get("short_interval_h", 8) or 8),
                    total_fee_pct=float(row.get("fee_pct", 0.11) or 0.11),
                )
                if not analysis.viable:
                    continue
                edge = analysis.adjusted_net_edge_pct
                row = dict(row)
                row["adjusted_net_edge_pct"] = edge
            if edge < min_edge_pct:
                continue
            spread = float(row.get("spread_pct", 0))
            if spread < min_spread_pct:
                continue
            current_spreads[_opp_key(row)] = row

        # 1) Refresh rates + accrue funding, then check exits
        to_close: list[str] = []
        for pair_id, pair in list(open_pairs.items()):
            lookup_key = _opp_key({
                "base": pair.base,
                "direction": pair.direction,
                "long_venue": pair.long_venue,
                "short_venue": pair.short_venue,
            })
            current_row = all_by_key.get(lookup_key)

            # 用最新费率刷新，再按各腿结算周期累计已跨过的结算
            if current_row:
                _update_pair_rates(pair, current_row)
            _accrue_funding(pair, ts)

            should_close = False
            close_reason = ""
            current_edge = -999.0

            if current_row is None:
                # 配对从扫描结果中消失（交易对下架/费率不可得）
                should_close = True
                close_reason = "spread_disappeared"
            else:
                current_edge = float(current_row.get("net_edge_pct", 0))
                current_spread_val = float(current_row.get("spread_pct", 0))
                if current_edge <= exit_edge_pct:
                    should_close = True
                    close_reason = f"edge_collapse: {current_edge:.4f}%"
                if current_spread_val < 0:
                    should_close = True
                    close_reason = f"spread_negative: {current_spread_val:.4f}%"

            # Max holding time
            hours_held = (ts - pair.open_ts).total_seconds() / 3600.0
            if hours_held >= max_holding_hours:
                should_close = True
                close_reason = f"max_holding: {hours_held:.0f}h >= {max_holding_hours:.0f}h"

            if should_close:
                to_close.append(pair_id)

                total_fee = pair.open_fee_pct * 2  # open + close
                net_pnl = pair.accumulated_funding_pct - total_fee
                pnl_usd = pair.amount_usd * net_pnl / 100.0
                capital += pnl_usd

                closed_trades.append(ClosedTrade(
                    pair_id=pair_id,
                    base=pair.base,
                    direction=pair.direction,
                    open_ts=pair.open_ts,
                    close_ts=ts,
                    holding_hours=hours_held,
                    open_edge_pct=pair.open_edge_pct,
                    close_edge_pct=current_edge,
                    total_funding_pct=round(pair.accumulated_funding_pct, 6),
                    total_fee_pct=round(total_fee, 4),
                    net_pnl_pct=round(net_pnl, 6),
                    amount_usd=pair.amount_usd,
                    close_reason=close_reason,
                    win=net_pnl > 0,
                    long_settlements=pair.long_settlements,
                    short_settlements=pair.short_settlements,
                ))

        for pid in to_close:
            del open_pairs[pid]

        # 2) Open new pairs
        available_slots = max(0, max_concurrent_pairs - len(open_pairs))
        active_bases = {p.base for p in open_pairs.values()}

        candidates = sorted(
            current_spreads.values(),
            key=lambda x: -float(
                x.get("adjusted_net_edge_pct", x.get("net_edge_pct", 0)) or 0
            ),
        )

        opened = 0
        for row in candidates:
            if opened >= available_slots:
                break
            base = str(row.get("base", "")).upper()
            if base in active_bases:
                continue

            # Capital check
            if capital < trade_usd:
                break

            long_venue = str(row.get("long_venue", ""))
            short_venue = str(row.get("short_venue", ""))
            pair_id = _opp_key(row)

            open_fee = (
                FEE_RATES.get(long_venue, 0.06) +
                FEE_RATES.get(short_venue, 0.06)
            )

            pair = OpenPair(
                pair_id=pair_id,
                base=base,
                long_venue=long_venue,
                short_venue=short_venue,
                direction=str(row.get("direction", "forward")),
                amount_usd=trade_usd,
                open_edge_pct=float(
                    row.get("adjusted_net_edge_pct", row.get("net_edge_pct", 0)) or 0
                ),
                open_spread_pct=float(row.get("spread_pct", 0)),
                open_ts=ts,
                open_fee_pct=open_fee,
                last_accrual_ts=ts,
            )
            _update_pair_rates(pair, row)
            open_pairs[pair_id] = pair

            capital -= trade_usd  # margin locked
            active_bases.add(base)
            opened += 1
            # 资金费在下一个结算边界才累计，开仓瞬间不计（与真实结算一致）

        # 3) Track equity
        # Release margin back + unrealized funding for open pairs
        unrealized_equity = capital
        for pair in open_pairs.values():
            unrealized_equity += pair.amount_usd  # margin back
            unrealized_equity += pair.amount_usd * pair.accumulated_funding_pct / 100.0

        equity_curve.append({
            "ts": ts.isoformat(),
            "equity": round(unrealized_equity, 2),
            "open_pairs": len(open_pairs),
            "capital_free": round(capital, 2),
        })

        peak_equity = max(peak_equity, unrealized_equity)
        drawdown = (peak_equity - unrealized_equity) / peak_equity * 100.0
        max_drawdown = max(max_drawdown, drawdown)

        # Track daily returns for Sharpe
        if current_date != prev_date:
            if prev_date is not None and day_start_equity > 0:
                daily_ret = (unrealized_equity - day_start_equity) / day_start_equity
                daily_returns.append(daily_ret)
            day_start_equity = unrealized_equity
            prev_date = current_date

    # Close remaining open pairs at end
    final_equity = capital
    for pair in open_pairs.values():
        total_fee = pair.open_fee_pct * 2
        net_pnl = pair.accumulated_funding_pct - total_fee
        pnl_usd = pair.amount_usd * net_pnl / 100.0
        final_equity += pair.amount_usd + pnl_usd

        closed_trades.append(ClosedTrade(
            pair_id=pair.pair_id,
            base=pair.base,
            direction=pair.direction,
            open_ts=pair.open_ts,
            close_ts=snapshots[-1]["_ts"] if snapshots else pair.open_ts,
            holding_hours=(snapshots[-1]["_ts"] - pair.open_ts).total_seconds() / 3600.0 if snapshots else 0,
            open_edge_pct=pair.open_edge_pct,
            close_edge_pct=-999,
            total_funding_pct=round(pair.accumulated_funding_pct, 6),
            total_fee_pct=round(total_fee, 4),
            net_pnl_pct=round(net_pnl, 6),
            amount_usd=pair.amount_usd,
            close_reason="backtest_end",
            win=net_pnl > 0,
            long_settlements=pair.long_settlements,
            short_settlements=pair.short_settlements,
        ))

    # Calculate stats
    total_return = (final_equity - initial_capital) / initial_capital * 100.0
    total_hours = 0.0
    if snapshots:
        total_hours = (snapshots[-1]["_ts"] - snapshots[0]["_ts"]).total_seconds() / 3600.0
    annual_return = (total_return / total_hours * 8760.0) if total_hours > 0 else 0.0

    wins = sum(1 for t in closed_trades if t.win)
    avg_holding = (
        sum(t.holding_hours for t in closed_trades) / len(closed_trades)
        if closed_trades
        else 0.0
    )
    avg_pnl = (
        sum(t.net_pnl_pct for t in closed_trades) / len(closed_trades)
        if closed_trades
        else 0.0
    )
    total_funding = sum(t.total_funding_pct for t in closed_trades)
    total_fees = sum(t.total_fee_pct for t in closed_trades)

    # Sharpe ratio (annualized, using daily returns)
    sharpe = 0.0
    if len(daily_returns) > 1:
        mean_ret = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std_dev = math.sqrt(variance) if variance > 0 else 0.0
        if std_dev > 0:
            sharpe = (mean_ret / std_dev) * math.sqrt(365.0)

    return BacktestResult(
        total_return_pct=round(total_return, 4),
        annual_return_pct=round(annual_return, 2),
        max_drawdown_pct=round(max_drawdown, 4),
        sharpe_ratio=round(sharpe, 2),
        trade_count=len(closed_trades),
        win_count=wins,
        win_rate_pct=round(wins / len(closed_trades) * 100.0, 1) if closed_trades else 0.0,
        avg_holding_hours=round(avg_holding, 1),
        avg_pnl_per_trade_pct=round(avg_pnl, 4),
        total_funding_collected_pct=round(total_funding, 4),
        total_fees_paid_pct=round(total_fees, 4),
        trades=[{
            "pair_id": t.pair_id,
            "base": t.base,
            "direction": t.direction,
            "open_ts": t.open_ts.isoformat(),
            "close_ts": t.close_ts.isoformat(),
            "holding_hours": round(t.holding_hours, 1),
            "net_pnl_pct": t.net_pnl_pct,
            "close_reason": t.close_reason,
            "win": t.win,
            "long_settlements": t.long_settlements,
            "short_settlements": t.short_settlements,
        } for t in closed_trades],
        equity_curve=equity_curve,
    )


def print_result(result: BacktestResult) -> None:
    print("\n" + "=" * 60)
    print("PURE FUTURES SPREAD — BACKTEST RESULTS")
    print("=" * 60)
    print(f"  Total Return:          {result.total_return_pct:>10.2f}%")
    print(f"  Annual Return:         {result.annual_return_pct:>10.2f}%")
    print(f"  Max Drawdown:          {result.max_drawdown_pct:>10.2f}%")
    print(f"  Sharpe Ratio:          {result.sharpe_ratio:>10.2f}")
    print(f"  Trades:                {result.trade_count:>10d}")
    print(f"  Win Rate:              {result.win_rate_pct:>9.1f}%")
    print(f"  Avg Holding:           {result.avg_holding_hours:>9.1f}h")
    print(f"  Avg PnL per Trade:     {result.avg_pnl_per_trade_pct:>9.4f}%")
    print(f"  Total Funding:         {result.total_funding_collected_pct:>9.4f}%")
    print(f"  Total Fees:            {result.total_fees_paid_pct:>9.4f}%")
    print("=" * 60)

    if result.trades:
        print(f"\n  Recent trades ({min(10, len(result.trades))}):")
        print(
            f"  {'base':<8s} {'dir':<8s} {'hold_h':>7s} "
            f"{'pnl':>8s} {'reason':<20s} {'win':>4s}"
        )
        print("  " + "-" * 60)
        for t in result.trades[:10]:
            print(
                f"  {t['base']:<8s} {t['direction']:<8s} {t['holding_hours']:7.0f} "
                f"{t['net_pnl_pct']:+7.4f}% {t['close_reason']:<20s} "
                f"{'W' if t['win'] else 'L':>4s}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pure Futures Spread backtest from scanner JSONL"
    )
    parser.add_argument(
        "--jsonl-file",
        default="data/pure_futures_spreads.jsonl",
        help="Scanner JSONL file",
    )
    parser.add_argument("--capital", type=float, default=100000.0, help="Initial capital USD")
    parser.add_argument("--trade-usd", type=float, default=5000.0, help="USD per pair")
    parser.add_argument("--max-pairs", type=int, default=3, help="Max concurrent pairs")
    parser.add_argument("--min-spread", type=float, default=0.05, help="Min spread %%")
    parser.add_argument("--min-edge", type=float, default=0.01, help="Min net edge %%")
    parser.add_argument("--exit-edge", type=float, default=0.01, help="Exit when edge ≤ this %%")
    parser.add_argument("--max-holding-hours", type=float, default=720.0, help="Max holding time")
    parser.add_argument("--allow-mismatch", action="store_true", help="Allow settle mismatch pairs")
    parser.add_argument("--verbose", "-V", action="store_true")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    jsonl_path = Path(args.jsonl_file)
    if not jsonl_path.is_absolute():
        jsonl_path = ROOT / jsonl_path

    print(f"Loading snapshots from {jsonl_path}...", file=sys.stderr)
    try:
        snapshots = load_snapshots(jsonl_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not snapshots:
        print("No snapshots found.", file=sys.stderr)
        return 1

    first_ts = snapshots[0]["_ts"].isoformat()
    last_ts = snapshots[-1]["_ts"].isoformat()
    total_hours = (snapshots[-1]["_ts"] - snapshots[0]["_ts"]).total_seconds() / 3600.0
    print(
        f"Loaded {len(snapshots)} snapshots ({total_hours:.0f}h, "
        f"{total_hours/24:.1f} days): {first_ts} → {last_ts}",
        file=sys.stderr,
    )

    result = run_backtest(
        snapshots,
        initial_capital=args.capital,
        trade_usd=args.trade_usd,
        max_concurrent_pairs=args.max_pairs,
        min_spread_pct=args.min_spread,
        min_edge_pct=args.min_edge,
        exit_edge_pct=args.exit_edge,
        max_holding_hours=args.max_holding_hours,
        allow_mismatch=args.allow_mismatch,
        verbose=args.verbose,
    )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print_result(result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
