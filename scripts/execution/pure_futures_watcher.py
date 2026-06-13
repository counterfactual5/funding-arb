#!/usr/bin/env python3
"""Pure Futures Watcher — standalone daemon monitoring pure perp pair positions.

Responsibilities:
  1. Spread collapse exit: auto-close when funding spread <= exitThreshold
  2. Rebalance: alert on leg notional value skew; autoRebalance=true trims
     oversized leg on quantity mismatch (real delta exposure from partial liquidation/ADL)
  3. Single-leg liquidation detection: when one leg is liquidated or abnormally closed, immediately close the other

Usage:
  python3 scripts/execution/pure_futures_watcher.py \
    --config templates/config.pure_futures.spread.json

  python3 scripts/execution/pure_futures_watcher.py \
    --config templates/config.pure_futures.spread.json --interval 30 --verbose

Difference from runner:
  - Runner is a periodic scan->decide->execute loop (open + close)
  - Watcher is a pure monitoring process (only exit/hedge/alert), suitable as a systemd/launchd daemon
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
from core.strategy_config import apply_strategy_to_pure_futures_cfg  # noqa: E402
from execution.pure_futures_executor import (  # noqa: E402
    close_pure_futures_leg,
    close_pure_futures_pair,
    load_pure_futures_positions,
    rebalance_pure_futures_pair,
)
from market.parallel_fetch import run_io_parallel  # noqa: E402
from venues import get_venue  # noqa: E402
from venues.base import make_pair  # noqa: E402

TZ = timezone(timedelta(hours=8))
WATCHER_LOG = SCRIPTS_DIR / "data" / "pure-futures" / "watcher.jsonl"

_venue_cache: dict[str, Any] = {}
_mark_price_cache: dict[tuple[str, str, str], tuple[float, float]] = {}
_MARK_PRICE_TTL_SEC = 10.0  # Within a watcher cycle, prices shouldn't drift


def _get_venue_cached(venue_id: str):
    if venue_id not in _venue_cache:
        _venue_cache[venue_id] = get_venue({"venue": {"type": venue_id}})
    return _venue_cache[venue_id]


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
    """Get current spread from scan data (short_rate - long_rate)."""
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
    """Fetch all current perp positions from exchange API.

    Returns None on failure (distinct from empty list): caller must skip leg checks,
    otherwise a query failure would be mistaken for "leg gone" and trigger a wrongful close.
    """
    try:
        v = _get_venue_cached(venue_id)
        return v.fetch_futures_positions(quote)
    except Exception as e:
        print(f"[{_ts_str()}] {venue_id} fetch positions error: {e}", file=sys.stderr)
        return None


def _get_mark_price(venue_id: str, base: str, quote: str = "USDT") -> float:
    """Get single-venue perp mark price. Uses short-lived cache to avoid repeated calls within a loop."""
    key = (str(venue_id).lower(), str(base).upper(), quote.upper())
    now = time.monotonic()
    cached = _mark_price_cache.get(key)
    if cached is not None:
        ts, price = cached
        if now - ts < _MARK_PRICE_TTL_SEC:
            return price
    try:
        v = _get_venue_cached(venue_id)
        pair = make_pair(base, quote)
        if getattr(v, "venue_id", "") == "okx" or venue_id == "okx":
            pair = f"{base.upper()}-{quote.upper()}-SWAP"
        price = float(v.get_ticker(pair) or 0.0)
    except Exception:
        price = 0.0
    _mark_price_cache[key] = (now, price)
    return price


def _prefetch_all_mark_prices(venue_ids: set[str], cache_sec: int = 5) -> None:
    """Bulk-fetch all futures tickers for the given venues in parallel.

    Populates `_mark_price_cache` so subsequent `_get_mark_price` calls
    return instantly without hitting the API.
    """
    if not venue_ids:
        return
    valid: set[str] = set()
    for vid in venue_ids:
        try:
            v = _get_venue_cached(vid)
            if hasattr(v, "get_all_futures_tickers"):
                valid.add(vid)
        except Exception:
            continue
    if not valid:
        return

    def _fetch_one(vid: str) -> tuple[str, dict[str, float]]:
        try:
            v = _get_venue_cached(vid)
            return vid, v.get_all_futures_tickers(cache_sec=cache_sec)
        except Exception:
            return vid, {}

    results = run_io_parallel(
        sorted(valid), _fetch_one, max_workers=max(4, len(valid)), swallow_errors=True
    )

    now = time.monotonic()
    for vid, tickers in results.items():
        for pair, price in tickers.items():
            if "-USDT-SWAP" in pair:
                base = pair.replace("-USDT-SWAP", "")
                key = (vid.lower(), base.upper(), "USDT")
            elif pair.endswith("USDT"):
                base = pair[:-4]
                key = (vid.lower(), base.upper(), "USDT")
            else:
                continue
            _mark_price_cache[key] = (now, float(price))


def check_exit(
    pos: dict[str, Any],
    scan_rates: dict[str, dict[str, dict[str, Any]]],
    exit_edge: float,
) -> tuple[bool, str]:
    """Check whether a position should exit (spread has narrowed).

    Returns (should_exit, reason).
    """
    base = str(pos.get("base", "")).upper()
    long_venue = str(pos.get("long_venue", ""))
    short_venue = str(pos.get("short_venue", ""))

    current_spread = _get_current_spread(base, long_venue, short_venue, scan_rates)
    if current_spread is None:
        # Cannot get current rate → do not actively exit (conservative approach)
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
    """Estimate position spread P&L.

    Price P&L = inter-venue spread at open - inter-venue spread now (scaled by quantity)
    forward: spread narrows -> profit; reverse: spread narrows -> loss
    """
    long_price = float(pos.get("long_price", 0))
    short_price = float(pos.get("short_price", 0))
    qty = float(pos.get("qty", 0))
    trade_usd = float(pos.get("trade_usd", 0))
    direction = str(pos.get("direction", "forward")).lower()

    # Forward: long on cheaper venue, short on expensive → profit when spread narrows
    # Reverse: positions flipped → loss when spread narrows
    sign = 1.0 if direction == "forward" else -1.0
    open_spread = abs(long_price - short_price)
    close_spread = abs(current_long_px - current_short_px)
    spread_pnl = sign * (open_spread - close_spread) * qty
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
    """Extract actual quantity for a leg from pre-fetched venue position snapshot."""
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
    """Check if leg notional values have diverged. Returns (need_rebalance, reason, long_notional, short_notional).

    max_skew_pct: Threshold percentage for leg notional value divergence (default 1%).
    venue_positions: Optional exchange position snapshot; when provided, actual leg quantities
    are used (can detect quantity mismatch from partial liquidation/ADL, which is real delta exposure).
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
    """Check if both legs are still alive (not liquidated/unexpectedly closed).

    venue_positions: {venue_id: [{symbol, side, qty, ...}]}.
    Note: caller must ensure both venues' position snapshots were fetched successfully
    (fetch failure != no position; confusing the two triggers wrongful close).
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
    """Check mark-price-to-liquidation distance per leg; return alerts if too close.

    Cross-venue hedged legs do not share P&L: when price moves unilaterally, one leg accumulates
    floating losses; when distance approaches liquidation, margin must be added or the position
    reduced proactively — waiting for liquidation causes a naked leg incident.
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
                break  # Exchange did not return liquidation price (common for cross-margin low-leverage)
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
    """Single watch cycle: check all open positions, decide exit/alert."""
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
    actions: list[dict[str, Any]] = []
    alerts: list[Any] = []
    # Clear cycle-level mark price cache
    _mark_price_cache.clear()

    open_positions = [
        p for p in load_pure_futures_positions() if p.get("status") == "open"
    ]
    if not open_positions:
        if verbose:
            print(f"[{_ts_str()}] no open positions", file=sys.stderr)
        return cycle_result

    # Bulk-fetch mark prices for all venues that have open positions (parallel, one call per venue)
    wanted_venues: set[str] = set()
    for pos in open_positions:
        for vid in (pos.get("long_venue"), pos.get("short_venue")):
            if vid:
                wanted_venues.add(str(vid))
    _prefetch_all_mark_prices(wanted_venues)

    # Fetch current funding rates for all venues
    try:
        scan_rates = fetch_all_fee_rate_rows_by_base(
            venues, workers, cache_ttl_sec=30.0
        )
    except Exception as e:
        alerts.append(f"rate_fetch_error: {e}")
        return cycle_result

    # Fetch actual positions from venues (for leg/margin check)
    # Only keep successfully fetched venues; failures go to failed set, skip their leg checks
    venue_positions: dict[str, list[dict[str, Any]]] = {}
    failed_venues: set[str] = set()
    if check_legs and not dry_run:
        wanted: set[str] = set()
        for pos in open_positions:
            for vid in (pos.get("long_venue"), pos.get("short_venue")):
                if vid:
                    wanted.add(str(vid))
        if wanted:
            # Parallel fetch — saves V×~200ms vs sequential loop
            def _fetch_one(vid: str) -> tuple[str, list[dict[str, Any]] | None]:
                try:
                    return vid, _fetch_positions_from_venue(vid)
                except Exception as e:
                    return vid, None

            results = run_io_parallel(
                sorted(wanted),
                _fetch_one,
                max_workers=max(4, len(wanted)),
                swallow_errors=True,
            )
            for vid, rows in results.items():
                if rows is None:
                    failed_venues.add(vid)
                    alerts.append({"alert": "positions_fetch_failed", "venue": vid})
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
            actions.append(action)
            continue

        # 1b. PnL-based stop loss
        pos_long_v = str(pos.get("long_venue", ""))
        pos_short_v = str(pos.get("short_venue", ""))
        if not pos_dry_run:
            long_px = _get_mark_price(pos_long_v, base)
            short_px = _get_mark_price(pos_short_v, base)
            if long_px > 0 and short_px > 0:
                pnl_info = estimate_spread_pnl(pos, long_px, short_px)
                spread_loss_pct = -pnl_info.get(
                    "spread_pnl_pct", 0
                )  # Positive value = loss

                # Estimate cumulative funding income
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
                    actions.append(action)
                    continue

        # 2. Check leg alive (only for live positions, and only when
        #    both venues' position snapshots fetched successfully)
        legs_checkable = (
            check_legs
            and not pos_dry_run
            and pos_long_v in venue_positions
            and pos_short_v in venue_positions
        )
        if legs_checkable:
            alive, leg_reason = check_leg_alive(pos, venue_positions)
            if not alive:
                action: dict[str, Any] = {
                    "action": "emergency_close",
                    "position_id": pos_id,
                    "base": base,
                    "reason": leg_reason,
                }
                if leg_reason == "both_legs_gone":
                    # Placing a close order on a vanished leg opens a new position — must not submit.
                    # Only alert for manual review (may be externally closed or symbol match issue).
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
                    # Single leg gone: only close the surviving leg (closing a vanished leg = opening new position)
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
                actions.append(action)
                continue

            # 2b. Per-leg liquidation distance warning (no auto-action, timely notification to add margin/reduce position)
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
                alerts.append(alert)
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
            alerts.append(alert)
            if verbose:
                print(
                    f"[{_ts_str()}] REBALANCE {pos_id} {base}: {rebal_reason} "
                    f"(long=${long_n:.2f} short=${short_n:.2f})",
                    file=sys.stderr,
                )
            if auto_rebalance and not pos_dry_run:
                # Only trim quantity mismatch (real delta from partial liquidation/ADL).
                # Pure mark-price drift (equal quantities) is not tradeable away; executor returns balanced.
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
                actions.append(action)

    cycle_result["actions"] = actions
    cycle_result["alerts"] = alerts
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
    cfg = apply_strategy_to_pure_futures_cfg(
        json.loads(cfg_path.read_text(encoding="utf-8"))
    )

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
