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

from cli.scan_pure_futures_spreads import fetch_all_fee_rate_rows_by_base  # noqa: E402
from core.notify import send_notification  # noqa: E402
from execution.pure_futures_executor import (  # noqa: E402
    close_pure_futures_leg,
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
    venue_id: str, quote: str = "USDT"
) -> list[dict[str, Any]] | None:
    """从交易所 API 获取当前全部永续持仓。

    失败返回 None（区别于空列表）：调用方必须跳过腿校验，
    否则查询故障会被误判为「腿消失」触发误平仓。
    """
    try:
        v = get_venue({"venue": {"type": venue_id}})
        return v.fetch_futures_positions(quote)
    except Exception as e:
        print(f"[{_ts_str()}] {venue_id} fetch positions error: {e}", file=sys.stderr)
        return None


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

    current_spread = _get_current_spread(base, long_venue, short_venue, scan_rates)
    if current_spread is None:
        # 无法获取当前 rate → 不主动退出（保守策略）
        return False, "rate_unavailable"

    # For forward: spread = short_rate - long_rate > 0 profitable
    # Exit when spread collapses below exit_edge
    # For reverse: spread is same formula but both rates negative
    if current_spread <= exit_edge:
        return True, f"spread_collapse: {current_spread:.4f}% ≤ {exit_edge}%"

    return False, ""


def estimate_spread_pnl(
    pos: dict[str, Any],
    current_long_px: float,
    current_short_px: float,
) -> dict[str, Any]:
    """估算持仓的价差损益。

    价格盈亏 = 开仓时两所价差 - 当前两所价差（按数量折算）
    """
    long_price = float(pos.get("long_price", 0))
    short_price = float(pos.get("short_price", 0))
    qty = float(pos.get("qty", 0))
    trade_usd = float(pos.get("trade_usd", 0))

    open_spread = abs(long_price - short_price)
    close_spread = abs(current_long_px - current_short_px)
    spread_pnl = (open_spread - close_spread) * qty
    spread_pnl_pct = (spread_pnl / trade_usd * 100) if trade_usd > 0 else 0.0

    return {
        "open_spread": open_spread,
        "close_spread": close_spread,
        "spread_pnl": spread_pnl,
        "spread_pnl_pct": spread_pnl_pct,
    }


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
    skew = (
        abs(long_notional - short_notional) / max(long_notional, short_notional) * 100.0
    )

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

    venue_positions: {venue_id: [{symbol, side, qty, ...}]}。
    注意：调用方必须保证两个 venue 的持仓快照都拉取成功
    （拉取失败 ≠ 无持仓，混淆会触发误平仓）。
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


def check_margin_distance(
    pos: dict[str, Any],
    venue_positions: dict[str, list[dict[str, Any]]],
    alert_distance_pct: float = 20.0,
) -> list[dict[str, Any]]:
    """逐腿检查标记价距强平价的百分比距离，过近则返回告警。

    跨所对冲两腿盈亏不互通：行情单边走时一腿浮亏积累，
    距离逼近时必须补保证金或主动减仓，等强平就是裸腿事故。
    """
    alerts: list[dict[str, Any]] = []
    base = str(pos.get("base", "")).upper()
    for leg, side in (("long", "long"), ("short", "short")):
        venue_id = str(pos.get(f"{leg}_venue", ""))
        rows = venue_positions.get(venue_id)
        if rows is None:
            continue
        for p in rows:
            sym = str(p.get("symbol", "")).upper()
            if not sym.startswith(base) or str(p.get("side", "")).lower() != side:
                continue
            liq_px = float(p.get("liq_price", 0) or 0)
            if liq_px <= 0:
                break  # 交易所未返回强平价（全仓低杠杆时常见）
            mark = _get_mark_price(venue_id, base)
            if mark <= 0:
                break
            distance_pct = abs(mark - liq_px) / mark * 100.0
            if distance_pct < alert_distance_pct:
                alerts.append(
                    {
                        "leg": leg,
                        "venue": venue_id,
                        "mark_price": mark,
                        "liq_price": liq_px,
                        "distance_pct": round(distance_pct, 2),
                    }
                )
            break
    return alerts


def watch_cycle(
    cfg: dict[str, Any],
    *,
    dry_run: bool = True,
    verbose: bool = False,
    log_path: Path = WATCHER_LOG,
) -> dict[str, Any]:
    """单次 watch 循环：检查所有 open 持仓，决定退出/告警。"""
    pfa = cfg.get("pureFuturesArbitrage") or {}
    venues = [
        str(v).lower() for v in pfa.get("venues", ["binance", "bitget", "bybit", "okx"])
    ]
    exit_edge = float(pfa.get("exitThresholdPct", 0.01))
    max_skew_pct = float(pfa.get("rebalanceSkewPct", 1.0))
    check_legs = bool(pfa.get("watcherCheckLegs", True))
    auto_rebalance = bool(pfa.get("autoRebalance", False))
    margin_alert_pct = float(pfa.get("marginAlertDistancePct", 20.0))
    workers = int(pfa.get("workers", 4))

    cycle_result: dict[str, Any] = {
        "ts": _ts_str(),
        "ts_ms": _now_ms(),
        "actions": [],
        "alerts": [],
        "checked": 0,
    }

    open_positions = [
        p for p in load_pure_futures_positions() if p.get("status") == "open"
    ]
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

    # Fetch actual positions from venues (for leg/margin check)
    # 只保留拉取成功的 venue；失败的记入 failed 集合，跳过其腿校验
    venue_positions: dict[str, list[dict[str, Any]]] = {}
    failed_venues: set[str] = set()
    if check_legs and not dry_run:
        wanted: set[str] = set()
        for pos in open_positions:
            for vid in (pos.get("long_venue"), pos.get("short_venue")):
                if vid:
                    wanted.add(str(vid))
        for vid in sorted(wanted):
            rows = _fetch_positions_from_venue(vid)
            if rows is None:
                failed_venues.add(vid)
                cycle_result["alerts"].append(
                    {"alert": "positions_fetch_failed", "venue": vid}
                )
            else:
                venue_positions[vid] = rows

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

        # 2. Check leg alive (only for live positions, and only when
        #    both venues' position snapshots fetched successfully)
        pos_long_v = str(pos.get("long_venue", ""))
        pos_short_v = str(pos.get("short_venue", ""))

        # 1b. PnL-based stop loss
        if not pos_dry_run:
            long_px = _get_mark_price(pos_long_v, base)
            short_px = _get_mark_price(pos_short_v, base)
            if long_px > 0 and short_px > 0:
                pnl_info = estimate_spread_pnl(pos, long_px, short_px)
                spread_loss_pct = -pnl_info.get("spread_pnl_pct", 0)  # 正值=亏损

                # 估算累计资金费收益
                opened_at = int(pos.get("opened_at", 0) or 0)
                held_hours = (_now_ms() - opened_at) / 3600000.0 if opened_at > 0 else 0
                interval_h = 8.0
                periods = max(0, held_hours / interval_h)
                current_spread = _get_current_spread(
                    base, pos_long_v, pos_short_v, scan_rates
                )
                est_funding_pct = (
                    (current_spread or 0) * periods if current_spread else 0
                )

                max_loss_mult = float(pfa.get("maxLossVsFundingMult", 3.0))
                if (
                    spread_loss_pct > 0
                    and est_funding_pct > 0
                    and spread_loss_pct > est_funding_pct * max_loss_mult
                ):
                    action = {
                        "action": "close",
                        "position_id": pos_id,
                        "base": base,
                        "reason": f"pnl_stop_loss: spread_loss={spread_loss_pct:.4f}% > {max_loss_mult}x est_funding={est_funding_pct:.4f}%",
                        "pnl_info": pnl_info,
                    }
                    send_notification(
                        "PNL STOP LOSS",
                        f"Position {pos_id} {base}: "
                        f"spread_loss={spread_loss_pct:.4f}% > {max_loss_mult}x est_funding={est_funding_pct:.4f}%",
                        cfg,
                    )
                    res = close_pure_futures_pair(pos_id, dry_run=False, config=cfg)
                    action["result"] = res.to_dict()
                    cycle_result["actions"].append(action)
                    continue

        # 2. Check leg alive (only for live positions, and only when
        #    both venues' position snapshots fetched successfully)
        pos_long_v = str(pos.get("long_venue", ""))
        pos_short_v = str(pos.get("short_venue", ""))
        legs_checkable = (
            check_legs
            and not pos_dry_run
            and pos_long_v in venue_positions
            and pos_short_v in venue_positions
        )
        if legs_checkable:
            alive, leg_reason = check_leg_alive(pos, venue_positions)
            if not alive:
                action = {
                    "action": "emergency_close",
                    "position_id": pos_id,
                    "base": base,
                    "reason": leg_reason,
                }
                if leg_reason == "both_legs_gone":
                    # 对不存在的腿下平仓单会反向开出新仓——绝不能下单。
                    # 只告警留待人工核对（可能是外部手动平仓或符号匹配问题）。
                    action["action"] = "manual_review"
                    action["note"] = "both legs gone; no orders placed"
                    send_notification(
                        "BOTH LEGS GONE",
                        f"Position {pos_id} {base}: both legs missing on "
                        f"{pos_long_v}/{pos_short_v}. No orders placed — "
                        f"verify manually and mark position closed.",
                        cfg,
                    )
                else:
                    # 单腿消失：只平仍存活的那条腿（对消失腿下单 = 开新仓）
                    alive_leg = "short" if leg_reason.startswith("long_leg") else "long"
                    send_notification(
                        "NAKED LEG DETECTED",
                        f"Position {pos_id} {base}: {leg_reason}. "
                        f"Closing surviving {alive_leg} leg only.",
                        cfg,
                    )
                    res = close_pure_futures_leg(
                        pos_id,
                        alive_leg,
                        config=cfg,
                        close_reason=f"emergency: {leg_reason}",
                    )
                    action["result"] = res.to_dict()
                cycle_result["actions"].append(action)
                continue

            # 2b. 逐腿强平距离预警（不自动操作，及时通知补保证金/减仓）
            margin_alerts = check_margin_distance(
                pos, venue_positions, margin_alert_pct
            )
            for ma in margin_alerts:
                alert = {
                    "position_id": pos_id,
                    "base": base,
                    "alert": "margin_distance_low",
                    **ma,
                }
                cycle_result["alerts"].append(alert)
                send_notification(
                    "MARGIN DISTANCE LOW",
                    f"Position {pos_id} {base} {ma['leg']}@{ma['venue']}: "
                    f"mark {ma['mark_price']} vs liq {ma['liq_price']} "
                    f"({ma['distance_pct']}% away, threshold {margin_alert_pct}%). "
                    f"Add margin or reduce position.",
                    cfg,
                )

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
