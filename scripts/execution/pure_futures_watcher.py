#!/usr/bin/env python3
"""Pure Futures Watcher — 独立常驻进程监控纯永续配对持仓。

职责:
  1. 价差收窄退出: 当 funding spread ≤ exitThreshold 时自动平仓
  2. 重平衡: 两腿名义价值偏斜时告警；autoRebalance=true 时自动
     裁剪数量错配的超重腿（部分强平/ADL 造成的真实 delta 敞口）
  3. 单腿清算/强平检测: 一腿被强平或异常关闭时，立即平掉另一腿

用法:
  python3 scripts/execution/pure_futures_watcher.py \
    --config templates/config.pure_futures.spread.json

  python3 scripts/execution/pure_futures_watcher.py \
    --config templates/config.pure_futures.spread.json --interval 30 --verbose

与 runner 的区别:
  - runner 是周期性 scan→decide→execute 循环（开仓+平仓）
  - watcher 是纯监控进程（只做退出/对冲/告警），适合作为 systemd/launchd 常驻服务
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli.scan_pure_futures_spreads import (  # noqa: E402
    FUTURES_TAKER_FEE_PCT,
    fetch_all_fee_rate_rows_by_base,
)
from core.notify import send_notification  # noqa: E402
from execution.pure_futures_executor import (  # noqa: E402
    close_pure_futures_pair,
    load_pure_futures_positions,
    rebalance_pure_futures_pair,
)
from venues import get_venue  # noqa: E402
from venues.base import make_pair  # noqa: E402

TZ = timezone(timedelta(hours=8))
WATCHER_LOG = SCRIPTS_DIR / "data" / "pure-futures" / "watcher.jsonl"


def _append_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ts_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _get_current_spread(
    base: str,
    long_venue: str,
    short_venue: str,
    venues_rates: dict[str, dict[str, dict[str, Any]]],
) -> float | None:
    """从扫描数据中获取当前 spread (short_rate - long_rate)。"""
    long_info = venues_rates.get(base, {}).get(long_venue)
    short_info = venues_rates.get(base, {}).get(short_venue)
    if not long_info or not short_info:
        return None
    long_rate = float(long_info.get("rate_pct", 0.0))
    short_rate = float(short_info.get("rate_pct", 0.0))
    # scanner convention: spread = short_rate - long_rate (short at higher)
    return short_rate - long_rate


def _fetch_positions_from_venue(
    venue_id: str, base: str, quote: str = "USDT"
) -> list[dict[str, Any]]:
    """从交易所 API 获取当前永续持仓（仅 futures），返回 [{symbol, side, qty, ...}]。"""
    try:
        v = get_venue({"venue": {"type": venue_id}})
        positions = v.fetch_futures_positions(quote)
        return [p for p in positions if p.get("symbol", "").upper().startswith(base.upper())]
    except Exception as e:
        print(f"[{_ts_str()}] {venue_id} fetch positions error: {e}", file=sys.stderr)
        return []


def _get_mark_price(venue_id: str, base: str, quote: str = "USDT") -> float:
    """获取单所永续标记价。"""
    try:
        v = get_venue({"venue": {"type": venue_id}})
        pair = make_pair(base, quote)
        if getattr(v, "venue_id", "") == "okx" or venue_id == "okx":
            pair = f"{base.upper()}-{quote.upper()}-SWAP"
        return float(v.get_ticker(pair) or 0.0)
    except Exception:
        return 0.0


def check_exit(
    pos: dict[str, Any],
    scan_rates: dict[str, dict[str, dict[str, Any]]],
    exit_edge: float,
) -> tuple[bool, str]:
    """检查持仓是否应退出（价差收窄）。

    Returns (should_exit, reason).
    """
    base = str(pos.get("base", "")).upper()
    long_venue = str(pos.get("long_venue", ""))
    short_venue = str(pos.get("short_venue", ""))
    direction = str(pos.get("direction", "forward"))

    current_spread = _get_current_spread(
        base, long_venue, short_venue, scan_rates
    )
    if current_spread is None:
        # 无法获取当前 rate → 不主动退出（保守策略）
        return False, "rate_unavailable"

    # For forward: spread = short_rate - long_rate > 0 profitable
    # Exit when spread collapses below exit_edge
    # For reverse: spread is same formula but both rates negative
    if current_spread <= exit_edge:
        return True, f"spread_collapse: {current_spread:.4f}% ≤ {exit_edge}%"

    return False, ""


def _leg_qty_from_snapshot(
    venue_positions: dict[str, list[dict[str, Any]]],
    venue_id: str,
    base: str,
    side: str,
) -> float | None:
    """从已抓取的 venue positions 快照中提取某腿实际数量。"""
    rows = venue_positions.get(venue_id)
    if rows is None:
        return None
    base_u = base.upper()
    for p in rows:
        sym = str(p.get("symbol", "")).upper()
        if sym.startswith(base_u) and str(p.get("side", "")).lower() == side:
            return abs(float(p.get("qty", 0) or p.get("amount", 0)))
    return 0.0


def check_rebalance(
    pos: dict[str, Any],
    max_skew_pct: float = 1.0,
    venue_positions: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[bool, str, float, float]:
    """检查两腿名义价值是否偏离，返回 (need_rebalance, reason, long_notional, short_notional)。

    max_skew_pct: 两腿名义价值偏离阈值百分比（默认 1%）。
    venue_positions: 可选的交易所持仓快照；提供时用实际腿数量
    （能发现部分强平/ADL 造成的数量错配，这才是真正的 delta 敞口）。
    """
    base = str(pos.get("base", ""))
    long_venue = str(pos.get("long_venue", ""))
    short_venue = str(pos.get("short_venue", ""))
    qty = float(pos.get("qty", 0))

    long_qty = short_qty = qty
    if venue_positions:
        actual_lq = _leg_qty_from_snapshot(venue_positions, long_venue, base, "long")
        actual_sq = _leg_qty_from_snapshot(venue_positions, short_venue, base, "short")
        if actual_lq:
            long_qty = actual_lq
        if actual_sq:
            short_qty = actual_sq

    long_px = _get_mark_price(long_venue, base)
    short_px = _get_mark_price(short_venue, base)
    if long_px <= 0 or short_px <= 0:
        return False, "price_unavailable", 0.0, 0.0

    long_notional = long_qty * long_px
    short_notional = short_qty * short_px
    skew = abs(long_notional - short_notional) / max(long_notional, short_notional) * 100.0

    if skew > max_skew_pct:
        qty_part = (
            f" qty long={long_qty} short={short_qty}"
            if abs(long_qty - short_qty) > 1e-12
            else ""
        )
        return (
            True,
            f"skew={skew:.2f}%>{max_skew_pct}%{qty_part}",
            long_notional,
            short_notional,
        )

    return False, "", long_notional, short_notional


def check_leg_alive(
    pos: dict[str, Any],
    venue_positions: dict[str, list[dict[str, Any]]],
) -> tuple[bool, str]:
    """检查两腿是否仍然存活（没被强平/意外关闭）。

    venue_positions: {venue_id: [{symbol, side, qty, ...}]}
    """
    base = str(pos.get("base", "")).upper()
    qty = float(pos.get("qty", 0))
    long_venue = str(pos.get("long_venue", ""))
    short_venue = str(pos.get("short_venue", ""))

    long_positions = venue_positions.get(long_venue, [])
    short_positions = venue_positions.get(short_venue, [])

    # Check long leg
    long_alive = False
    for p in long_positions:
        sym = str(p.get("symbol", "")).upper()
        side = str(p.get("side", "")).lower()
        p_qty = float(p.get("qty", 0) or p.get("amount", 0))
        if sym.startswith(base) and side == "long" and p_qty >= qty * 0.95:
            long_alive = True
            break

    short_alive = False
    for p in short_positions:
        sym = str(p.get("symbol", "")).upper()
        side = str(p.get("side", "")).lower()
        p_qty = float(p.get("qty", 0) or p.get("amount", 0))
        if sym.startswith(base) and side == "short" and abs(p_qty) >= qty * 0.95:
            short_alive = True
            break

    if not long_alive and not short_alive:
        return False, "both_legs_gone"
    if not long_alive:
        return False, f"long_leg_gone@{long_venue}"
    if not short_alive:
        return False, f"short_leg_gone@{short_venue}"

    return True, ""


def watch_cycle(
    cfg: dict[str, Any],
    *,
    dry_run: bool = True,
    verbose: bool = False,
    log_path: Path = WATCHER_LOG,
) -> dict[str, Any]:
    """单次 watch 循环：检查所有 open 持仓，决定退出/告警。"""
    pfa = cfg.get("pureFuturesArbitrage") or {}
    venues = [str(v).lower() for v in pfa.get("venues", ["binance", "bitget", "bybit", "okx"])]
    exit_edge = float(pfa.get("exitThresholdPct", 0.01))
    max_skew_pct = float(pfa.get("rebalanceSkewPct", 1.0))
    check_legs = bool(pfa.get("watcherCheckLegs", True))
    auto_rebalance = bool(pfa.get("autoRebalance", False))
    workers = int(pfa.get("workers", 4))

    cycle_result: dict[str, Any] = {
        "ts": _ts_str(),
        "ts_ms": _now_ms(),
        "actions": [],
        "alerts": [],
        "checked": 0,
    }

    open_positions = [p for p in load_pure_futures_positions() if p.get("status") == "open"]
    if not open_positions:
        if verbose:
            print(f"[{_ts_str()}] no open positions", file=sys.stderr)
        return cycle_result

    # Fetch current funding rates for all venues
    try:
        scan_rates = fetch_all_fee_rate_rows_by_base(venues, workers)
    except Exception as e:
        cycle_result["alerts"].append(f"rate_fetch_error: {e}")
        return cycle_result

    # Fetch actual positions from venues (for leg check)
    venue_positions: dict[str, list[dict[str, Any]]] = {}
    if check_legs and not dry_run:
        for pos in open_positions:
            for vid in (pos.get("long_venue"), pos.get("short_venue")):
                if vid and vid not in venue_positions:
                    venue_positions[vid] = _fetch_positions_from_venue(
                        vid, str(pos.get("base", ""))
                    )

    for pos in open_positions:
        pos_id = str(pos.get("id", ""))
        base = str(pos.get("base", ""))
        pos_dry_run = dry_run or bool(pos.get("dry_run", True))
        cycle_result["checked"] += 1

        # 1. Check exit condition
        should_exit, exit_reason = check_exit(pos, scan_rates, exit_edge)
        if should_exit:
            action = {
                "action": "close",
                "position_id": pos_id,
                "base": base,
                "reason": exit_reason,
                "dry_run": pos_dry_run,
            }
            if verbose:
                print(
                    f"[{_ts_str()}] EXIT {pos_id} {base}: {exit_reason}",
                    file=sys.stderr,
                )
            if not pos_dry_run:
                res = close_pure_futures_pair(pos_id, dry_run=False, config=cfg)
                action["result"] = res.to_dict()
                if not res.ok:
                    action["error"] = f"close failed: state={res.state}"
                    send_notification(
                        "Watcher Close Failed",
                        f"Position {pos_id} {base}: exit triggered ({exit_reason}) but close failed: {res.state}",
                        cfg,
                    )
            else:
                action["note"] = "dry_run position, skipping live close"
            cycle_result["actions"].append(action)
            continue

        # 2. Check leg alive (only for live positions)
        if check_legs and not pos_dry_run and venue_positions:
            alive, leg_reason = check_leg_alive(pos, venue_positions)
            if not alive:
                action = {
                    "action": "emergency_close",
                    "position_id": pos_id,
                    "base": base,
                    "reason": leg_reason,
                }
                send_notification(
                    "NAKED LEG DETECTED",
                    f"Position {pos_id} {base}: {leg_reason}. Attempting emergency close.",
                    cfg,
                )
                res = close_pure_futures_pair(pos_id, dry_run=False, config=cfg)
                action["result"] = res.to_dict()
                cycle_result["actions"].append(action)
                continue

        # 3. Check rebalance
        need_rebalance, rebal_reason, long_n, short_n = check_rebalance(
            pos, max_skew_pct, venue_positions or None
        )
        if need_rebalance:
            alert = {
                "position_id": pos_id,
                "base": base,
                "alert": "rebalance_needed",
                "reason": rebal_reason,
                "long_notional": round(long_n, 2),
                "short_notional": round(short_n, 2),
            }
            cycle_result["alerts"].append(alert)
            if verbose:
                print(
                    f"[{_ts_str()}] REBALANCE {pos_id} {base}: {rebal_reason} "
                    f"(long=${long_n:.2f} short=${short_n:.2f})",
                    file=sys.stderr,
                )
            if auto_rebalance and not pos_dry_run:
                # 仅裁剪数量错配（部分强平/ADL 造成的真实 delta）。
                # 纯标记价漂移（数量一致）的偏斜不可交易消除，executor 会返回 balanced。
                res = rebalance_pure_futures_pair(pos_id, dry_run=False, config=cfg)
                action = {
                    "action": "rebalance",
                    "position_id": pos_id,
                    "base": base,
                    "reason": rebal_reason,
                    "result": res.to_dict(),
                }
                if res.state == "balanced":
                    action["note"] = "qty equal; skew is mark-price drift only"
                elif not res.ok:
                    action["error"] = f"rebalance failed: state={res.state}"
                elif verbose:
                    print(
                        f"[{_ts_str()}] REBALANCED {pos_id} {base}: {res.logs[-1] if res.logs else ''}",
                        file=sys.stderr,
                    )
                cycle_result["actions"].append(action)

    _append_log(log_path, cycle_result)
    return cycle_result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pure Futures Watcher — standalone monitor for spread positions"
    )
    parser.add_argument("--config", required=True, help="Config JSON path")
    parser.add_argument(
        "--interval",
        type=float,
        default=60.0,
        help="Check interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one check cycle and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Never submit live orders (close/rollback)",
    )
    parser.add_argument("--verbose", "-V", action="store_true")
    parser.add_argument("--json", action="store_true", help="JSON output each cycle")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    # Determine dry-run: explicit flag > env > config
    if args.dry_run:
        dry_run = True
    elif os.environ.get("DCA_LIVE") == "1":
        dry_run = False
    elif os.environ.get("DCA_DRY_RUN") == "1":
        dry_run = True
    else:
        dry_run = bool(cfg.get("dry_run", True))

    if args.once:
        result = watch_cycle(cfg, dry_run=dry_run, verbose=args.verbose)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(
        f"[{_ts_str()}] Pure Futures Watcher started "
        f"(interval={args.interval}s, dry_run={dry_run})",
        file=sys.stderr,
    )

    while True:
        try:
            t0 = time.time()
            result = watch_cycle(cfg, dry_run=dry_run, verbose=args.verbose)
            elapsed = time.time() - t0

            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            elif args.verbose:
                print(
                    f"[{_ts_str()}] checked={result['checked']} "
                    f"actions={len(result['actions'])} "
                    f"alerts={len(result['alerts'])} "
                    f"in {elapsed:.1f}s",
                    file=sys.stderr,
                )

            sleep_time = max(0.0, args.interval - elapsed)
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            print(f"\n[{_ts_str()}] Watcher stopped.", file=sys.stderr)
            break
        except Exception as e:
            print(f"[{_ts_str()}] Watcher error: {e}", file=sys.stderr)
            time.sleep(60)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
