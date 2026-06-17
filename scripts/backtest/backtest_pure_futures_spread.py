#!/usr/bin/env python3
"""Pure Futures Spread backtest driver -- historical funding rate data replay.

Features:
  - Loads JSONL-format historical funding rate snapshots (collected by scan_pure_futures_spreads --watch)
  - Replays spread changes at each timestamp, simulating open/close/hold
  - Funding accrues per-leg at real settlement intervals (interval_h) on UTC-aligned settlement boundaries,
    independent of snapshot collection frequency (5-minute and 8-hour collection yield identical results)
  - settle_mismatch candidates integrate with planner: uses adjusted_net_edge as entry threshold
  - Statistics: total return, annualized, max drawdown, Sharpe, win rate, average holding time

Usage:
  # Use existing scanner JSONL data
  python3 scripts/backtest/backtest_pure_futures_spread.py \
    --jsonl-file data/pure_futures_spreads.jsonl

  # Fetch exchange historical funding API directly (no collection needed, 6h cache)
  python3 scripts/backtest/backtest_pure_futures_spread.py \
    --history-bases BTC,ETH,SOL --history-days 90

  # Specify parameters
  python3 scripts/backtest/backtest_pure_futures_spread.py \
    --jsonl-file data/pure_futures_spreads.jsonl \
    --capital 100000 --trade-usd 5000 --min-spread 0.08 --exit-edge 0.02

  # JSON output
  python3 scripts/backtest/backtest_pure_futures_spread.py --jsonl-file data/spreads.jsonl --json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.settle_mismatch_planner import (  # noqa: E402
    _leg_info_from_row,
    analyze_settle_mismatch,
)


@dataclass
class OpenPair:
    """An opened pair in the backtest."""

    pair_id: str
    base: str
    long_venue: str
    short_venue: str
    direction: str
    amount_usd: float
    open_edge_pct: float
    open_spread_pct: float
    open_mark_spread_pct: float  # cross-venue mark divergence at entry (for basis risk)
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
    # cash-and-carry reverse leg borrow cost (converted to per-settlement period, accrued with settlements)
    borrow_per_settle_pct: float = 0.0
    borrow_paid_pct: float = 0.0


def _settlements_crossed(t0: datetime, t1: datetime, interval_h: float) -> int:
    """Number of settlement boundaries crossed between (t0, t1].

    Boundaries are UTC epoch-aligned (e.g. 8h -> 00:00/08:00/16:00), consistent with real exchanges.
    """
    if interval_h <= 0 or t1 <= t0:
        return 0
    ih = interval_h * 3600.0
    return int(math.floor(t1.timestamp() / ih) - math.floor(t0.timestamp() / ih))


def _update_pair_rates(pair: OpenPair, row: dict[str, Any]) -> None:
    """Refresh position leg state with the latest snapshot's rate/interval."""
    pair.long_rate_pct = float(row.get("long_rate_pct", pair.long_rate_pct) or 0.0)
    pair.short_rate_pct = float(row.get("short_rate_pct", pair.short_rate_pct) or 0.0)
    pair.long_interval_h = float(
        row.get("long_interval_h", pair.long_interval_h) or 8.0
    )
    pair.short_interval_h = float(
        row.get("short_interval_h", pair.short_interval_h) or 8.0
    )
    pair.borrow_per_settle_pct = float(
        row.get("borrow_per_settle_pct", pair.borrow_per_settle_pct) or 0.0
    )


def _accrue_funding(pair: OpenPair, ts: datetime) -> None:
    """Accrue funding per leg's settlement period.

    Long leg pays long_rate at each settlement (negative rate means receiving);
    Short leg receives short_rate at each settlement (negative rate means paying).
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
        # Borrow interest accrues with the perp leg's settlement period (cc_reverse's long leg)
        if pair.borrow_per_settle_pct > 0:
            pair.borrow_paid_pct += n_long * pair.borrow_per_settle_pct
    pair.last_accrual_ts = ts


@dataclass
class ClosedTrade:
    """A closed trade record."""

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
    borrow_paid_pct: float = 0.0


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
    return ":".join(
        [
            str(row.get("base", "")).upper(),
            str(row.get("direction", "")),
            str(row.get("long_venue", "")),
            str(row.get("short_venue", "")),
        ]
    )


_STRATEGY_OF_DIRECTION = {
    "forward": "pure",
    "reverse": "pure",
    "cc_forward": "cc",
    "cc_reverse": "cc",
}


def _iter_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for direction in ("forward", "reverse"):
        for row in snapshot.get(direction, []) or []:
            item = dict(row)
            item["direction"] = item.get("direction") or direction
            rows.append(item)
    for row in snapshot.get("cc", []) or []:
        item = dict(row)
        item["direction"] = item.get("direction") or "cc_forward"
        rows.append(item)
    for row in snapshot.get("spreads", []) or []:
        item = dict(row)
        item["direction"] = item.get("direction") or "unknown"
        rows.append(item)
    return rows


def load_snapshots(jsonl_path: Path) -> list[dict[str, Any]]:
    """Load JSONL snapshots."""
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
    # Basis risk: estimated one-time mark divergence cost at exit (%).
    # When mark prices are available in snapshots, the actual spread is used.
    # When mark_price=0 (historical mode), this value is used as a fixed cost.
    # Default 0.0 = disabled (backward compatible); set via API to enable.
    basis_cost_pct: float = 0.0,
    strategies: set[str] | None = None,
    verbose: bool = False,
) -> BacktestResult:
    """Replay historical snapshots, simulating funding rate strategy combinations.

    strategies: allowed strategy set for opening, default {"pure"}.
      - "pure": cross-venue pure perp spread (forward/reverse rows)
      - "cc":   single-venue cash-and-carry (cc_forward/cc_reverse rows, synthesized in history mode)
    """
    if strategies is None:
        strategies = {"pure"}
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

    FEE_RATES = {
        "bitget": 0.06,
        "binance": 0.05,
        "okx": 0.05,
        "bybit": 0.055,
        "hyperliquid": 0.035,
        "dydx": 0.05,
        "lighter": 0.035,
        "aster": 0.05,
        "edgex": 0.05,
    }

    for snap_idx, snap in enumerate(snapshots):
        ts: datetime = snap["_ts"]
        current_date = ts.strftime("%Y-%m-%d")
        rows = _iter_rows(snap)

        # Unfiltered lookup (for exit decisions / rate refresh, not affected by entry thresholds)
        all_by_key: dict[str, dict[str, Any]] = {_opp_key(r): r for r in rows}

        # Entry candidates (filtered by entry thresholds; mismatch goes through planner's adjusted edge)
        current_spreads: dict[str, dict[str, Any]] = {}
        for row in rows:
            strat = _STRATEGY_OF_DIRECTION.get(str(row.get("direction", "")), "pure")
            if strat not in strategies:
                continue
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
                    net_edge_pct=float(row.get("net_edge_pct"))
                    if row.get("net_edge_pct") is not None
                    else None,
                    long_leg_info=_leg_info_from_row(row, "long"),
                    short_leg_info=_leg_info_from_row(row, "short"),
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
            lookup_key = _opp_key(
                {
                    "base": pair.base,
                    "direction": pair.direction,
                    "long_venue": pair.long_venue,
                    "short_venue": pair.short_venue,
                }
            )
            current_row = all_by_key.get(lookup_key)

            # Refresh with latest rates, then accrue settlements crossed per leg's settlement period
            if current_row:
                _update_pair_rates(pair, current_row)
            _accrue_funding(pair, ts)

            should_close = False
            close_reason = ""
            current_edge = -999.0

            if current_row is None:
                # Pair disappeared from scan results (pair delisted / rate unavailable)
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
                close_reason = (
                    f"max_holding: {hours_held:.0f}h >= {max_holding_hours:.0f}h"
                )

            if should_close:
                to_close.append(pair_id)

                # Basis/mark risk: model the cross-venue mark divergence cost at exit.
                # When snapshots have real mark_spread_pct (> 0), use the actual change.
                # When mark data is absent (historical mode, mark_spread=0), use the
                # configured basis_cost_pct as a fixed estimated cost.
                exit_mark_spread = (
                    float(current_row.get("mark_spread_pct", 0) or 0)
                    if current_row
                    else 0.0
                )
                if exit_mark_spread > 0 and pair.open_mark_spread_pct > 0:
                    # Both have real mark data — cost is the change in spread
                    basis_cost = abs(exit_mark_spread - pair.open_mark_spread_pct)
                else:
                    # No mark data available — use estimated basis cost
                    basis_cost = basis_cost_pct

                total_fee = pair.open_fee_pct * 2  # open + close
                net_pnl = (
                    pair.accumulated_funding_pct
                    - pair.borrow_paid_pct
                    - total_fee
                    - basis_cost
                )
                pnl_usd = pair.amount_usd * net_pnl / 100.0
                # Return margin locked at open + settlement PnL
                capital += pair.amount_usd + pnl_usd

                closed_trades.append(
                    ClosedTrade(
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
                        borrow_paid_pct=round(pair.borrow_paid_pct, 6),
                    )
                )

        for pid in to_close:
            del open_pairs[pid]

        # 2) Open new pairs
        available_slots = max(0, max_concurrent_pairs - len(open_pairs))
        active_bases = {p.base for p in open_pairs.values()}

        candidates = sorted(
            current_spreads.values(),
            key=lambda x: (
                -float(x.get("adjusted_net_edge_pct", x.get("net_edge_pct", 0)) or 0)
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

            # Row carries fee_pct (single-side two-leg total) with priority -- cc rows include spot fee,
            # perp fee tables can't look up leg names like "venue:spot"
            open_fee = float(row.get("fee_pct", 0) or 0)
            if open_fee <= 0:
                open_fee = FEE_RATES.get(long_venue, 0.06) + FEE_RATES.get(
                    short_venue, 0.06
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
                open_mark_spread_pct=float(row.get("mark_spread_pct", 0) or 0),
                open_ts=ts,
                open_fee_pct=open_fee,
                last_accrual_ts=ts,
            )
            _update_pair_rates(pair, row)
            open_pairs[pair_id] = pair

            capital -= trade_usd  # margin locked
            active_bases.add(base)
            opened += 1
            # Funding accrues at the next settlement boundary, not at open time (consistent with real settlement)

        # 3) Track equity
        # Release margin back + unrealized funding for open pairs
        unrealized_equity = capital
        for pair in open_pairs.values():
            unrealized_equity += pair.amount_usd  # margin back
            unrealized_equity += pair.amount_usd * pair.accumulated_funding_pct / 100.0

        equity_curve.append(
            {
                "ts": ts.isoformat(),
                "equity": round(unrealized_equity, 2),
                "open_pairs": len(open_pairs),
                "capital_free": round(capital, 2),
            }
        )

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
        # Apply basis cost at end-of-backtest forced close too
        basis_cost = (
            basis_cost_pct
            if pair.open_mark_spread_pct == 0
            else pair.open_mark_spread_pct
        )
        net_pnl = (
            pair.accumulated_funding_pct - pair.borrow_paid_pct - total_fee - basis_cost
        )
        pnl_usd = pair.amount_usd * net_pnl / 100.0
        final_equity += pair.amount_usd + pnl_usd

        closed_trades.append(
            ClosedTrade(
                pair_id=pair.pair_id,
                base=pair.base,
                direction=pair.direction,
                open_ts=pair.open_ts,
                close_ts=snapshots[-1]["_ts"] if snapshots else pair.open_ts,
                holding_hours=(snapshots[-1]["_ts"] - pair.open_ts).total_seconds()
                / 3600.0
                if snapshots
                else 0,
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
                borrow_paid_pct=round(pair.borrow_paid_pct, 6),
            )
        )

    # Calculate stats
    total_return = (final_equity - initial_capital) / initial_capital * 100.0
    total_hours = 0.0
    if snapshots:
        total_hours = (
            snapshots[-1]["_ts"] - snapshots[0]["_ts"]
        ).total_seconds() / 3600.0
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
        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (
            len(daily_returns) - 1
        )
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
        win_rate_pct=round(wins / len(closed_trades) * 100.0, 1)
        if closed_trades
        else 0.0,
        avg_holding_hours=round(avg_holding, 1),
        avg_pnl_per_trade_pct=round(avg_pnl, 4),
        total_funding_collected_pct=round(total_funding, 4),
        total_fees_paid_pct=round(total_fees, 4),
        trades=[
            {
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
                "borrow_paid_pct": t.borrow_paid_pct,
            }
            for t in closed_trades
        ],
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
    parser.add_argument(
        "--history-bases",
        default="",
        help="Comma-separated base list (e.g. BTC,ETH,SOL); when non-empty, fetches exchange historical funding API directly, ignoring --jsonl-file",
    )
    parser.add_argument(
        "--history-days",
        type=int,
        default=30,
        help="Historical lookback days (default 30)",
    )
    parser.add_argument(
        "--venues",
        default="binance,bitget,bybit,okx",
        help="Exchange list for history mode",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore 6h disk cache and force re-fetch",
    )
    parser.add_argument(
        "--capital", type=float, default=100000.0, help="Initial capital USD"
    )
    parser.add_argument("--trade-usd", type=float, default=5000.0, help="USD per pair")
    parser.add_argument("--max-pairs", type=int, default=3, help="Max concurrent pairs")
    parser.add_argument("--min-spread", type=float, default=0.05, help="Min spread %%")
    parser.add_argument("--min-edge", type=float, default=0.01, help="Min net edge %%")
    parser.add_argument(
        "--exit-edge", type=float, default=0.01, help="Exit when edge ≤ this %%"
    )
    parser.add_argument(
        "--max-holding-hours", type=float, default=720.0, help="Max holding time"
    )
    parser.add_argument(
        "--allow-mismatch", action="store_true", help="Allow settle mismatch pairs"
    )
    parser.add_argument(
        "--strategies",
        default="pure",
        help="Comma-separated strategy set: pure (cross-venue pure perps) / cc (single-venue spot+perp); e.g. pure,cc",
    )
    parser.add_argument(
        "--cc-borrow-apr",
        type=float,
        default=15.0,
        help="cc_reverse borrow APR assumption %% (no public historical borrow data, default 15)",
    )
    parser.add_argument(
        "--cc-check-caps",
        action="store_true",
        help="Probe real-time spot/borrow status to filter cc rows (uses current state as proxy for historical, closer to executable returns)",
    )
    parser.add_argument("--verbose", "-V", action="store_true")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.history_bases.strip():
        from backtest.funding_history_source import fetch_history_snapshots

        bases = [b.strip().upper() for b in args.history_bases.split(",") if b.strip()]
        venues = [v.strip().lower() for v in args.venues.split(",") if v.strip()]
        print(
            f"Fetching {args.history_days}d funding history: "
            f"{len(venues)} venues × {len(bases)} bases...",
            file=sys.stderr,
        )
        snapshots = fetch_history_snapshots(
            venues,
            bases,
            args.history_days,
            refresh=args.refresh_cache,
            borrow_apr_pct=args.cc_borrow_apr,
            check_cc_capability=args.cc_check_caps,
        )
    else:
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
        f"{total_hours / 24:.1f} days): {first_ts} → {last_ts}",
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
        strategies={s.strip().lower() for s in args.strategies.split(",") if s.strip()},
        verbose=args.verbose,
    )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print_result(result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
