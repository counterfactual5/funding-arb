#!/usr/bin/env python3
"""Pure futures cross-venue executor — 永续 + 永续资金费差套利执行器。

MVP 范围：
  - open: long_venue open_long + short_venue open_short
  - close: short leg close_short + long leg close_long
  - dry-run / live 共用独立 ledger: scripts/data/pure-futures/positions.json
  - 一腿失败时 best-effort 回滚另一腿；回滚失败标记 naked，需人工处理。

注意：跨交易所没有真正原子性。本模块只做工程上的两腿回滚和记录，不解决
跨所保证金迁移、强平监控、资金费周期错配等 Phase 2 后半风控问题。
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core.notify import send_notification  # noqa: E402
from execution.cross_venue_executor import (  # noqa: E402
    CrossVenueResult,
    _exec_qty,
    _filled,
    _floor_qty,
    _leg_market,
)
from venues import get_venue  # noqa: E402

POSITIONS_PATH = SCRIPTS_DIR / "data" / "pure-futures" / "positions.json"
MAX_MARK_PRICE_SPREAD_PCT = 1.0
MARGIN_BUFFER = 1.05


def load_pure_futures_positions(path: Path = POSITIONS_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_positions(
    positions: list[dict[str, Any]], path: Path = POSITIONS_PATH
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(positions, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _record_position(record: dict[str, Any], path: Path = POSITIONS_PATH) -> None:
    positions = load_pure_futures_positions(path)
    positions.append(record)
    _save_positions(positions, path)


def _mark_closed(
    position_id: str, close_info: dict[str, Any], path: Path = POSITIONS_PATH
) -> bool:
    positions = load_pure_futures_positions(path)
    for p in positions:
        if p.get("id") == position_id and p.get("status") == "open":
            p["status"] = "closed"
            p["closed_at"] = int(time.time() * 1000)
            p["close_info"] = close_info
            _save_positions(positions, path)
            return True
    return False


def _get_open_position(
    position_id: str, path: Path = POSITIONS_PATH
) -> dict[str, Any] | None:
    for p in load_pure_futures_positions(path):
        if p.get("id") == position_id and p.get("status") == "open":
            return p
    return None


def _update_position(
    position_id: str, updates: dict[str, Any], path: Path = POSITIONS_PATH
) -> bool:
    positions = load_pure_futures_positions(path)
    for p in positions:
        if p.get("id") == position_id and p.get("status") == "open":
            p.update(updates)
            _save_positions(positions, path)
            return True
    return False


def _venue(venue_id: str, injected: Any = None):
    return injected or get_venue({"venue": {"type": venue_id}})


def _check_futures_margin(
    venue: Any, venue_id: str, quote: str, required_usd: float, logs: list[str]
) -> bool:
    """校验 futures USDT ≥ required；不足时尝试 earn→spot→futures 链路。

    链路: futures 不足 → spot 划转 → spot 不足 → earn 赎回到 spot → 再划转。
    返回 False 表示保证金确认不足（应放弃开仓）。
    余额 API 异常时跳过校验放行（不让偶发接口故障阻塞交易）。
    """
    try:
        balances = venue.fetch_usdt_account_balances()
    except Exception as e:
        logs.append(f"{venue_id}: 保证金查询失败，跳过校验 ({e})")
        return True
    futures_avail = float(balances.get("futures", 0) or 0)
    if futures_avail >= required_usd:
        return True
    shortfall = required_usd - futures_avail
    spot_avail = float(balances.get("spot", 0) or 0)

    # Step 1: earn → spot（仅 Bitget，仅 spot 不足时）
    if spot_avail < shortfall and venue_id == "bitget":
        earn_shortfall = shortfall - spot_avail
        earn_redeemed = _redeem_bitget_earn(quote, earn_shortfall, logs)
        if earn_redeemed > 0:
            import time as _t
            _t.sleep(1.0)
            spot_avail += earn_redeemed

    # Step 2: spot → futures
    if spot_avail >= shortfall:
        try:
            if venue.transfer_asset(quote, shortfall, "spot", "futures"):
                logs.append(
                    f"{venue_id}: spot→futures 划转 {shortfall:.2f} {quote}"
                )
                return True
        except Exception as e:
            logs.append(f"{venue_id}: 划转失败 ({e})")
    logs.append(
        f"{venue_id}: 保证金不足 futures={futures_avail:.2f} "
        f"spot={spot_avail:.2f} 需 {required_usd:.2f}"
    )
    return False


_EARN_PRODUCTS = {"USDT": "964334561256718336"}
_MIN_REDEEM_USDT = 1.0


def _redeem_bitget_earn(
    coin: str, amount: float, logs: list[str]
) -> float:
    """从 Bitget 活期理财赎回。成功返回赎回金额，失败返回 0。"""
    pid = _EARN_PRODUCTS.get(coin)
    if not pid:
        return 0.0
    if amount < _MIN_REDEEM_USDT:
        return 0.0
    try:
        from venues.bitget import _api_call as _bitget_api
        # 查询理财余额
        data = _bitget_api("GET", "/api/v2/earn/account/assets")
        earn_bal = 0.0
        for a in data.get("data", []):
            if a.get("coin") == coin:
                earn_bal = float(a.get("amount", "0"))
                break
        if earn_bal < _MIN_REDEEM_USDT:
            logs.append(f"bitget earn: {coin} 理财余额 {earn_bal:.2f}，无需赎回")
            return 0.0
        redeem_amt = min(amount, earn_bal)
        # 赎回
        result = _bitget_api("POST", "/api/v2/earn/savings/redeem", body={
            "productId": pid,
            "periodType": "flexible",
            "amount": f"{redeem_amt:.2f}",
        })
        if result.get("code") == "00000":
            logs.append(f"bitget earn: 赎回 {redeem_amt:.2f} {coin} 到 spot")
            return redeem_amt
        else:
            logs.append(f"bitget earn: 赎回失败 {result.get('msg', '')}")
            return 0.0
    except Exception as e:
        logs.append(f"bitget earn: 赎回异常 ({e})")
        return 0.0


def _make_futures_trade(
    base: str, typ: str, qty: float, px: float, qprec: int, reason: str
) -> dict[str, Any]:
    return {
        "symbol": base,
        "type": typ,
        "amount_base": qty,
        "amount_usdt": round(qty * px, 4),
        "quantity_precision": qprec,
        "reason": reason,
    }


def open_pure_futures_pair(
    base: str,
    long_venue_id: str,
    short_venue_id: str,
    trade_usd: float,
    *,
    dry_run: bool = True,
    quote: str = "USDT",
    direction: str = "forward",
    max_mark_spread_pct: float = MAX_MARK_PRICE_SPREAD_PCT,
    config: dict[str, Any] | None = None,
    long_venue: Any = None,
    short_venue: Any = None,
    positions_path: Path = POSITIONS_PATH,
    capital_buffer_pct: float = 0.0,
) -> CrossVenueResult:
    """Open a pure futures funding-spread pair: long perp on one venue, short perp on another.

    capital_buffer_pct: settle-mismatch planner 建议的额外保证金预留
    （名义价值百分比），计入开仓前的余额校验。
    """
    logs: list[str] = []
    executed: list[dict[str, Any]] = []
    lv = _venue(long_venue_id, long_venue)
    sv = _venue(short_venue_id, short_venue)

    long_mkt = _leg_market(lv, base, quote, futures=True)
    short_mkt = _leg_market(sv, base, quote, futures=True)
    long_px = float(long_mkt.get("price") or 0.0)
    short_px = float(short_mkt.get("price") or 0.0)
    if long_px <= 0 or short_px <= 0:
        return CrossVenueResult(
            False, "aborted", logs=[f"永续价格不可用 long={long_px} short={short_px}"]
        )

    mark_spread_pct = abs(long_px - short_px) / max(long_px, short_px) * 100.0
    if mark_spread_pct > max_mark_spread_pct:
        return CrossVenueResult(
            False,
            "aborted",
            logs=[
                f"两所永续标记价差 {mark_spread_pct:.2f}% > {max_mark_spread_pct}%，拒绝开仓"
            ],
        )

    qty_prec = min(
        int(long_mkt["quantity_precision"]), int(short_mkt["quantity_precision"])
    )
    ref_px = max(long_px, short_px)
    base_amount = _floor_qty(trade_usd / ref_px, qty_prec)
    if base_amount <= 0:
        return CrossVenueResult(
            False, "aborted", logs=["数量取整后为 0，trade_usd 太小"]
        )
    for leg_name, mkt in (("long", long_mkt), ("short", short_mkt)):
        if trade_usd < mkt["min_trade_usdt"] or base_amount < mkt["min_trade_base"]:
            return CrossVenueResult(
                False,
                "aborted",
                logs=[
                    f"{leg_name} 腿低于最小限额: trade_usd={trade_usd} "
                    f"(min {mkt['min_trade_usdt']}), base={base_amount} (min {mkt['min_trade_base']})"
                ],
            )

    position_id = f"pf-{base.upper()}-{long_venue_id}-{short_venue_id}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    reason = (
        f"Pure-futures spread open {base}: long@{long_venue_id} short@{short_venue_id}"
    )
    long_trade = _make_futures_trade(
        base, "open_long", base_amount, long_px, qty_prec, reason
    )
    short_trade = _make_futures_trade(
        base, "open_short", base_amount, short_px, qty_prec, reason
    )
    long_market = {base: long_mkt}
    short_market = {base: short_mkt}

    if dry_run:
        executed.extend(lv.execute_trades([long_trade], long_market, dry_run=True))
        executed.extend(sv.execute_trades([short_trade], short_market, dry_run=True))
        _record_position(
            {
                "id": position_id,
                "status": "open",
                "strategy": "pure_futures_spread",
                "dry_run": True,
                "base": base,
                "direction": direction,
                "quote": quote,
                "long_venue": long_venue_id,
                "short_venue": short_venue_id,
                "qty": base_amount,
                "long_price": long_px,
                "short_price": short_px,
                "trade_usd": trade_usd,
                "mark_spread_pct": round(mark_spread_pct, 6),
                "opened_at": int(time.time() * 1000),
            },
            positions_path,
        )
        logs.append(
            f"[DRY-RUN] open pure-futures {base} qty={base_amount} long@{long_venue_id} short@{short_venue_id}"
        )
        return CrossVenueResult(True, "simulated", position_id, executed, logs)

    # 盘口深度预检：偏离窗口内深度不足时放弃（小币滑点会吃掉数期费差）。
    # 仅在配置含 pureFuturesArbitrage 时启用（注入 venue 的单测不走网络）。
    pfa_cfg = (config or {}).get("pureFuturesArbitrage") or {}
    if pfa_cfg and bool(pfa_cfg.get("depthCheckEnabled", True)):
        from market.futures_depth import check_pair_depth

        depth_ok, depth_detail = check_pair_depth(
            long_venue_id,
            short_venue_id,
            base,
            trade_usd,
            quote=quote,
            max_dev_pct=float(pfa_cfg.get("depthMaxDevPct", 0.3)),
            min_multiple=float(pfa_cfg.get("depthMinMultiple", 3.0)),
        )
        logs.append(f"depth check: {depth_detail}")
        if not depth_ok:
            return CrossVenueResult(False, "aborted", logs=logs)

    margin_usd = trade_usd * MARGIN_BUFFER + trade_usd * max(capital_buffer_pct, 0.0) / 100.0
    ok_long = _check_futures_margin(lv, long_venue_id, quote, margin_usd, logs)
    ok_short = _check_futures_margin(sv, short_venue_id, quote, margin_usd, logs)
    if not (ok_long and ok_short):
        # 在下首单前放弃，避免单腿成交后再回滚
        return CrossVenueResult(False, "aborted", "", executed, logs)
    for venue, mkt in ((lv, long_mkt), (sv, short_mkt)):
        try:
            venue.initialize_futures_symbol(mkt["pair"])
        except Exception:
            pass

    res_long = lv.execute_trades([long_trade], long_market, dry_run=False)
    executed.extend(res_long)
    if not _filled(res_long):
        logs.append(
            f"多头腿失败: {res_long[0].get('error') if res_long else 'no result'}"
        )
        return CrossVenueResult(False, "aborted", "", executed, logs)
    exec_qty = _floor_qty(_exec_qty(res_long, base_amount), qty_prec)
    logs.append(f"多头腿成交 {long_venue_id} open_long {exec_qty} {base}")

    short_trade["amount_base"] = exec_qty
    short_trade["amount_usdt"] = round(exec_qty * short_px, 4)
    res_short = sv.execute_trades([short_trade], short_market, dry_run=False)
    executed.extend(res_short)
    if _filled(res_short):
        short_qty = _exec_qty(res_short, exec_qty)
        logs.append(f"空头腿成交 {short_venue_id} open_short {short_qty} {base}")
        _record_position(
            {
                "id": position_id,
                "status": "open",
                "strategy": "pure_futures_spread",
                "dry_run": False,
                "base": base,
                "direction": direction,
                "quote": quote,
                "long_venue": long_venue_id,
                "short_venue": short_venue_id,
                "qty": min(exec_qty, short_qty),
                "long_qty": exec_qty,
                "short_qty": short_qty,
                "long_price": res_long[0].get("exec_price", long_px),
                "short_price": res_short[0].get("exec_price", short_px),
                "trade_usd": trade_usd,
                "mark_spread_pct": round(mark_spread_pct, 6),
                "opened_at": int(time.time() * 1000),
            },
            positions_path,
        )
        return CrossVenueResult(True, "filled", position_id, executed, logs)

    # Short leg failed → close long leg.
    logs.append(
        f"空头腿失败: {res_short[0].get('error') if res_short else 'no result'}，回滚多头腿"
    )
    rollback = _make_futures_trade(
        base,
        "close_long",
        exec_qty,
        long_px,
        qty_prec,
        "ROLLBACK: pure-futures short leg failed",
    )
    send_notification(
        "Pure Futures Leg Failure",
        f"{short_venue_id} open_short {base} failed; rolling back {long_venue_id} close_long {exec_qty}",
        config,
    )
    res_rb = lv.execute_trades([rollback], long_market, dry_run=False)
    executed.extend(res_rb)
    if _filled(res_rb):
        logs.append("回滚成功，无裸露持仓")
        return CrossVenueResult(False, "rolled_back", "", executed, logs)

    logs.append("回滚失败！多头腿裸露，需人工处理")
    send_notification(
        "NAKED PURE FUTURES POSITION",
        f"Pure-futures rollback failed: {long_venue_id} long {exec_qty} {base} unhedged",
        config,
    )
    return CrossVenueResult(False, "naked", "", executed, logs)


def close_pure_futures_pair(
    position_id: str,
    *,
    dry_run: bool | None = None,
    quote: str = "USDT",
    config: dict[str, Any] | None = None,
    long_venue: Any = None,
    short_venue: Any = None,
    positions_path: Path = POSITIONS_PATH,
) -> CrossVenueResult:
    """Close an open pure futures pair. Short leg first; rollback by reopening short if long close fails."""
    pos = _get_open_position(position_id, positions_path)
    if pos is None:
        return CrossVenueResult(
            False, "aborted", logs=[f"未找到 open 持仓 {position_id}"]
        )
    base = str(pos["base"])
    long_id = str(pos["long_venue"])
    short_id = str(pos["short_venue"])
    if dry_run is None:
        dry_run = bool(pos.get("dry_run", True))

    lv = _venue(long_id, long_venue)
    sv = _venue(short_id, short_venue)
    long_mkt = _leg_market(lv, base, quote, futures=True)
    short_mkt = _leg_market(sv, base, quote, futures=True)
    long_px = float(long_mkt.get("price") or pos.get("long_price") or 0.0)
    short_px = float(short_mkt.get("price") or pos.get("short_price") or 0.0)
    qty = float(
        pos.get("qty")
        or min(float(pos.get("long_qty", 0)), float(pos.get("short_qty", 0)))
    )
    qty_prec = min(
        int(long_mkt["quantity_precision"]), int(short_mkt["quantity_precision"])
    )
    qty = _floor_qty(qty, qty_prec)
    if qty <= 0:
        return CrossVenueResult(False, "aborted", position_id, logs=["持仓数量无效"])

    reason = f"Pure-futures spread close {position_id}"
    short_close = _make_futures_trade(
        base, "close_short", qty, short_px, qty_prec, reason
    )
    long_close = _make_futures_trade(base, "close_long", qty, long_px, qty_prec, reason)
    short_market = {base: short_mkt}
    long_market = {base: long_mkt}
    logs: list[str] = []
    executed: list[dict[str, Any]] = []

    if dry_run:
        executed.extend(sv.execute_trades([short_close], short_market, dry_run=True))
        executed.extend(lv.execute_trades([long_close], long_market, dry_run=True))
        _mark_closed(position_id, {"dry_run": True}, positions_path)
        logs.append(f"[DRY-RUN] close pure-futures {base} qty={qty}")
        return CrossVenueResult(True, "simulated", position_id, executed, logs)

    res_short = sv.execute_trades([short_close], short_market, dry_run=False)
    executed.extend(res_short)
    if not _filled(res_short):
        logs.append(
            f"空头平仓失败: {res_short[0].get('error') if res_short else 'no result'}"
        )
        return CrossVenueResult(False, "aborted", position_id, executed, logs)
    closed_short_qty = _exec_qty(res_short, qty)
    logs.append(f"空头腿已平 {short_id} close_short {closed_short_qty} {base}")

    long_close["amount_base"] = _floor_qty(closed_short_qty, qty_prec)
    res_long = lv.execute_trades([long_close], long_market, dry_run=False)
    executed.extend(res_long)
    if _filled(res_long):
        logs.append(
            f"多头腿已平 {long_id} close_long {long_close['amount_base']} {base}"
        )
        _mark_closed(
            position_id,
            {
                "short_price": res_short[0].get("exec_price"),
                "long_price": res_long[0].get("exec_price"),
            },
            positions_path,
        )
        return CrossVenueResult(True, "filled", position_id, executed, logs)

    # Long close failed → re-open short to restore hedge.
    logs.append("多头平仓失败，重新开空恢复对冲")
    reopen = _make_futures_trade(
        base,
        "open_short",
        closed_short_qty,
        short_px,
        qty_prec,
        "ROLLBACK: pure-futures long close failed",
    )
    send_notification(
        "Pure Futures Close Rollback",
        f"{long_id} close_long {base} failed; re-opening {short_id} open_short {closed_short_qty}",
        config,
    )
    res_rb = sv.execute_trades([reopen], short_market, dry_run=False)
    executed.extend(res_rb)
    if _filled(res_rb):
        logs.append("已重新对冲，持仓保持 open")
        return CrossVenueResult(False, "rolled_back", position_id, executed, logs)

    logs.append("重新对冲失败！多头腿裸露，需人工处理")
    send_notification(
        "NAKED PURE FUTURES POSITION",
        f"Pure-futures close rollback failed: {long_id} long {base} exposure unhedged",
        config,
    )
    return CrossVenueResult(False, "naked", position_id, executed, logs)


def close_pure_futures_leg(
    position_id: str,
    leg: str,
    *,
    quote: str = "USDT",
    config: dict[str, Any] | None = None,
    long_venue: Any = None,
    short_venue: Any = None,
    positions_path: Path = POSITIONS_PATH,
    close_reason: str = "single_leg_close",
) -> CrossVenueResult:
    """只平指定的一条腿（另一腿已被强平/消失时的应急处理）。

    对已消失的腿下平仓单会反向开出新仓位，所以双腿状态异常时
    必须只对仍存活的腿下单。leg ∈ {"long", "short"}。
    """
    if leg not in ("long", "short"):
        return CrossVenueResult(False, "aborted", logs=[f"无效 leg={leg!r}"])
    pos = _get_open_position(position_id, positions_path)
    if pos is None:
        return CrossVenueResult(
            False, "aborted", logs=[f"未找到 open 持仓 {position_id}"]
        )
    base = str(pos["base"])
    venue_id = str(pos[f"{leg}_venue"])
    v = _venue(venue_id, long_venue if leg == "long" else short_venue)
    mkt = _leg_market(v, base, quote, futures=True)
    px = float(mkt.get("price") or pos.get(f"{leg}_price") or 0.0)
    qty = float(pos.get(f"{leg}_qty", 0) or pos.get("qty", 0))
    qprec = int(mkt["quantity_precision"])
    qty = _floor_qty(qty, qprec)
    if qty <= 0:
        return CrossVenueResult(False, "aborted", position_id, logs=["腿数量无效"])

    trade = _make_futures_trade(
        base, f"close_{leg}", qty, px, qprec, f"{close_reason} {position_id}"
    )
    res = v.execute_trades([trade], {base: mkt}, dry_run=False)
    logs: list[str] = []
    if not _filled(res):
        logs.append(
            f"{venue_id} close_{leg} 失败: "
            f"{res[0].get('error') if res else 'no result'}"
        )
        send_notification(
            "Single Leg Close Failed",
            f"Position {position_id} {base}: close_{leg}@{venue_id} failed, "
            f"exposure remains unhedged",
            config,
        )
        return CrossVenueResult(False, "naked", position_id, res, logs)
    logs.append(f"{venue_id} close_{leg} {qty} {base} 已平（另一腿已消失）")
    _mark_closed(
        position_id,
        {"single_leg": leg, "reason": close_reason,
         f"{leg}_price": res[0].get("exec_price")},
        positions_path,
    )
    return CrossVenueResult(True, "filled", position_id, res, logs)


def _leg_qty_from_venue(
    venue: Any, base: str, side: str, quote: str = "USDT"
) -> float | None:
    """从交易所 API 读取某条腿的实际持仓数量；失败返回 None。"""
    try:
        positions = venue.fetch_futures_positions(quote)
    except Exception:
        return None
    base_u = base.upper()
    for p in positions:
        sym = str(p.get("symbol", "")).upper()
        p_side = str(p.get("side", "")).lower()
        if sym.startswith(base_u) and p_side == side:
            return abs(float(p.get("qty", 0) or p.get("amount", 0)))
    return 0.0


def rebalance_pure_futures_pair(
    position_id: str,
    *,
    dry_run: bool | None = None,
    quote: str = "USDT",
    config: dict[str, Any] | None = None,
    long_venue: Any = None,
    short_venue: Any = None,
    positions_path: Path = POSITIONS_PATH,
    long_qty: float | None = None,
    short_qty: float | None = None,
) -> CrossVenueResult:
    """两腿数量错配时（部分强平/ADL），减仓较大的一腿恢复 delta 中性。

    只做「裁大腿」：把数量多的一腿部分平仓到与另一腿一致。
    不加仓轻腿（避免追加保证金和滑点放大风险敞口）。

    long_qty / short_qty 可显式注入（测试或上层已查询过时）；
    否则 live 模式从交易所 API 读取，dry-run 用持仓记录。
    """
    pos = _get_open_position(position_id, positions_path)
    if pos is None:
        return CrossVenueResult(
            False, "aborted", logs=[f"未找到 open 持仓 {position_id}"]
        )
    base = str(pos["base"])
    long_id = str(pos["long_venue"])
    short_id = str(pos["short_venue"])
    if dry_run is None:
        dry_run = bool(pos.get("dry_run", True))

    lv = _venue(long_id, long_venue)
    sv = _venue(short_id, short_venue)
    long_mkt = _leg_market(lv, base, quote, futures=True)
    short_mkt = _leg_market(sv, base, quote, futures=True)
    qty_prec = min(
        int(long_mkt["quantity_precision"]), int(short_mkt["quantity_precision"])
    )

    rec_lq = float(pos.get("long_qty", 0) or pos.get("qty", 0))
    rec_sq = float(pos.get("short_qty", 0) or pos.get("qty", 0))
    lq = long_qty if long_qty is not None else rec_lq
    sq = short_qty if short_qty is not None else rec_sq
    if not dry_run:
        if long_qty is None:
            api_lq = _leg_qty_from_venue(lv, base, "long", quote)
            if api_lq is not None:
                lq = api_lq
        if short_qty is None:
            api_sq = _leg_qty_from_venue(sv, base, "short", quote)
            if api_sq is not None:
                sq = api_sq

    logs: list[str] = [f"leg qty: long={lq} short={sq}"]
    executed: list[dict[str, Any]] = []

    if lq <= 0 or sq <= 0:
        # 一腿已完全消失，重平衡无意义，应走 emergency close
        return CrossVenueResult(
            False,
            "aborted",
            position_id,
            executed,
            logs + ["一腿数量为 0，请用 close/emergency close 处理"],
        )

    trim_qty = _floor_qty(abs(lq - sq), qty_prec)
    if trim_qty <= 0:
        return CrossVenueResult(
            True, "balanced", position_id, executed, logs + ["两腿数量一致，无需重平衡"]
        )

    if lq > sq:
        trim_venue, trim_id, trim_mkt = lv, long_id, long_mkt
        trade_type = "close_long"
    else:
        trim_venue, trim_id, trim_mkt = sv, short_id, short_mkt
        trade_type = "close_short"
    px = float(trim_mkt.get("price") or 0.0)
    trade = _make_futures_trade(
        base,
        trade_type,
        trim_qty,
        px,
        qty_prec,
        f"REBALANCE: trim oversized leg {position_id}",
    )
    market = {base: trim_mkt}

    if dry_run:
        executed.extend(trim_venue.execute_trades([trade], market, dry_run=True))
        logs.append(f"[DRY-RUN] rebalance {trim_id} {trade_type} {trim_qty} {base}")
        return CrossVenueResult(True, "simulated", position_id, executed, logs)

    res = trim_venue.execute_trades([trade], market, dry_run=False)
    executed.extend(res)
    if not _filled(res):
        logs.append(
            f"重平衡失败: {res[0].get('error') if res else 'no result'}"
        )
        send_notification(
            "Pure Futures Rebalance Failed",
            f"Position {position_id} {base}: {trim_id} {trade_type} {trim_qty} failed; "
            f"legs remain skewed (long={lq} short={sq})",
            config,
        )
        return CrossVenueResult(False, "aborted", position_id, executed, logs)

    trimmed = _exec_qty(res, trim_qty)
    new_qty = _floor_qty(min(lq, sq), qty_prec)
    logs.append(f"重平衡成交 {trim_id} {trade_type} {trimmed} {base} → qty={new_qty}")
    _update_position(
        position_id,
        {
            "qty": new_qty,
            "long_qty": new_qty,
            "short_qty": new_qty,
            "last_rebalance": {
                "ts": int(time.time() * 1000),
                "venue": trim_id,
                "type": trade_type,
                "trim_qty": trimmed,
                "before": {"long_qty": lq, "short_qty": sq},
            },
        },
        positions_path,
    )
    return CrossVenueResult(True, "filled", position_id, executed, logs)
