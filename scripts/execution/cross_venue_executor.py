#!/usr/bin/env python3
"""跨所 delta-neutral 执行器 — 合约腿与现货腿在不同交易所原子执行。

与 delta_neutral_executor（单所）语义对齐：
  开仓 forward:  spot_venue 现货买入 → futures_venue 开空；合约腿失败则卖回现货回滚
  开仓 reverse:  spot_venue margin 借卖 → futures_venue 开多；合约腿失败则买回还币回滚
  平仓 forward:  futures_venue 平空 → spot_venue 卖出现货；现货腿失败则重新开空回滚
  平仓 reverse:  futures_venue 平多 → spot_venue 买回还币；现货腿失败则重新开多回滚

跨所没有所间原子性：回滚失败时持仓裸露（state=naked），会推送告警，需人工处理。
持仓记录在 scripts/data/cross-venue/positions.json，供平仓与对账使用。
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core.notify import send_notification  # noqa: E402
from venues import get_venue  # noqa: E402
from venues.base import make_pair  # noqa: E402

Direction = Literal["forward", "reverse"]

POSITIONS_PATH = SCRIPTS_DIR / "data" / "cross-venue" / "positions.json"
# 两所现货价差超过此值视为数据异常 / 不可套，拒绝开仓
MAX_VENUE_PRICE_SPREAD_PCT = 1.0
MARGIN_BUFFER = 1.05


@dataclass
class CrossVenueResult:
    ok: bool
    state: str  # simulated | filled | rolled_back | naked | aborted
    position_id: str = ""
    executed: list[dict[str, Any]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "state": self.state,
            "position_id": self.position_id,
            "executed": self.executed,
            "logs": self.logs,
        }


# ── 持仓记录 ─────────────────────────────────────────────────────────────────

def load_positions(path: Path = POSITIONS_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_positions(positions: list[dict[str, Any]], path: Path = POSITIONS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(positions, ensure_ascii=False, indent=2), encoding="utf-8")


def _record_position(record: dict[str, Any], path: Path = POSITIONS_PATH) -> None:
    positions = load_positions(path)
    positions.append(record)
    _save_positions(positions, path)


def _mark_closed(position_id: str, close_info: dict[str, Any], path: Path = POSITIONS_PATH) -> bool:
    positions = load_positions(path)
    for p in positions:
        if p.get("id") == position_id and p.get("status") == "open":
            p["status"] = "closed"
            p["closed_at"] = int(time.time() * 1000)
            p["close_info"] = close_info
            _save_positions(positions, path)
            return True
    return False


# ── 行情/规则快照 ────────────────────────────────────────────────────────────

def _leg_market(venue: Any, base: str, quote: str, *, futures: bool) -> dict[str, Any]:
    """单腿最小行情快照：价格 + 精度/最小量规则。"""
    default_rules = {
        "quantity_precision": 6,
        "quote_precision": 2,
        "min_trade_usdt": 5.0,
        "min_trade_base": 0.0,
    }
    if futures:
        pair = make_pair(base, quote)
        rules = venue.fetch_futures_symbol_rules(pair) or venue.fetch_symbol_rules(pair) or default_rules
        price = 0.0
        try:
            # OKX 永续 ticker 需 SWAP instId（如 ETH-USDT-SWAP），不能用 ETHUSDT
            if getattr(venue, "venue_id", "") == "okx":
                swap = f"{base.upper()}-{quote.upper()}-SWAP"
                price = float(venue.get_ticker(swap) or 0.0)
            else:
                price = float(venue.get_ticker(pair) or 0.0)
        except Exception:
            price = 0.0
    else:
        # 现货腿用 fetch_asset_market，自动处理 OKX 的 ETH-USDT 等格式差异
        am = venue.fetch_asset_market(base, quote)
        pair = str(am.get("pair") or make_pair(base, quote))
        rules = am.get("symbol_rules") or venue.fetch_symbol_rules(pair) or default_rules
        price = float(am.get("price") or 0.0)
    return {
        "pair": pair,
        "price": price,
        "quantity_precision": int(rules.get("quantity_precision", 6)),
        "quote_precision": int(rules.get("quote_precision", 2)),
        "min_trade_usdt": float(rules.get("min_trade_usdt", 0) or 0),
        "min_trade_base": float(rules.get("min_trade_base", 0) or 0),
    }


def _floor_qty(qty: float, precision: int) -> float:
    scale = 10**precision
    return int(qty * scale) / scale


def _filled(results: list[dict[str, Any]]) -> bool:
    return bool(results) and results[0].get("status") in ("filled", "simulated")


def _exec_qty(results: list[dict[str, Any]], fallback: float) -> float:
    if results and results[0].get("exec_qty"):
        return float(results[0]["exec_qty"])
    return fallback


# ── 开仓 ─────────────────────────────────────────────────────────────────────

def open_cross_venue_position(
    base: str,
    direction: Direction,
    futures_venue_id: str,
    spot_venue_id: str,
    trade_usd: float,
    *,
    dry_run: bool = True,
    quote: str = "USDT",
    config: dict[str, Any] | None = None,
    futures_venue: Any = None,
    spot_venue: Any = None,
    positions_path: Path = POSITIONS_PATH,
) -> CrossVenueResult:
    """跨所开仓。forward: spot买+perp空；reverse: margin借卖+perp多。"""
    logs: list[str] = []
    executed: list[dict[str, Any]] = []
    fv = futures_venue or get_venue({"venue": {"type": futures_venue_id}})
    sv = spot_venue or get_venue({"venue": {"type": spot_venue_id}})

    spot_mkt = _leg_market(sv, base, quote, futures=False)
    fut_mkt = _leg_market(fv, base, quote, futures=True)
    spot_px = spot_mkt["price"]
    fut_px = fut_mkt["price"] or spot_px
    if spot_px <= 0:
        return CrossVenueResult(False, "aborted", logs=[f"{spot_venue_id} 现货价不可用"])

    # 价差闸：两所价格偏离过大说明数据异常或不可套
    if fut_px > 0:
        spread_pct = abs(fut_px - spot_px) / spot_px * 100.0
        if spread_pct > MAX_VENUE_PRICE_SPREAD_PCT:
            return CrossVenueResult(
                False,
                "aborted",
                logs=[f"两所价差 {spread_pct:.2f}% > {MAX_VENUE_PRICE_SPREAD_PCT}%，拒绝开仓"],
            )

    if direction == "reverse" and not sv.supports_reverse_arbitrage():
        return CrossVenueResult(
            False, "aborted", logs=[f"{spot_venue_id} 不支持 margin 借卖，无法做 reverse 现货腿"]
        )

    # 双腿统一数量：取两腿精度的较粗者，保证两边都能下单
    qty_prec = min(spot_mkt["quantity_precision"], fut_mkt["quantity_precision"])
    base_amount = _floor_qty(trade_usd / spot_px, qty_prec)
    if base_amount <= 0:
        return CrossVenueResult(False, "aborted", logs=["数量取整后为 0，trade_usd 太小"])
    for leg_name, mkt in (("spot", spot_mkt), ("futures", fut_mkt)):
        if trade_usd < mkt["min_trade_usdt"] or base_amount < mkt["min_trade_base"]:
            return CrossVenueResult(
                False,
                "aborted",
                logs=[
                    f"{leg_name} 腿低于最小限额: trade_usd={trade_usd} "
                    f"(min {mkt['min_trade_usdt']}), base={base_amount} (min {mkt['min_trade_base']})"
                ],
            )

    spot_trade: dict[str, Any] = {
        "symbol": base,
        "type": "buy" if direction == "forward" else "sell",
        "amount_base": base_amount,
        "amount_usdt": round(base_amount * spot_px, 4),
        "reason": f"Cross-venue {direction} open: fut@{futures_venue_id} spot@{spot_venue_id}",
    }
    if direction == "reverse":
        spot_trade["account"] = "margin"
        spot_trade["side_effect"] = "auto_borrow"
    fut_trade: dict[str, Any] = {
        "symbol": base,
        "type": "open_short" if direction == "forward" else "open_long",
        "amount_base": base_amount,
        "amount_usdt": round(base_amount * fut_px, 4),
        "quantity_precision": fut_mkt["quantity_precision"],
        "reason": spot_trade["reason"],
    }
    spot_market = {base: spot_mkt}
    fut_market = {base: fut_mkt}
    position_id = f"xv-{int(time.time())}-{uuid.uuid4().hex[:6]}"

    if dry_run:
        executed.extend(sv.execute_trades([spot_trade], spot_market, dry_run=True))
        executed.extend(fv.execute_trades([fut_trade], fut_market, dry_run=True))
        logs.append(
            f"[DRY-RUN] {direction} {base} qty={base_amount} "
            f"spot@{spot_venue_id}({spot_px:.6g}) fut@{futures_venue_id}({fut_px:.6g})"
        )
        _record_position(
            {
                "id": position_id,
                "status": "open",
                "dry_run": True,
                "base": base,
                "direction": direction,
                "futures_venue": futures_venue_id,
                "spot_venue": spot_venue_id,
                "qty": base_amount,
                "spot_price": spot_px,
                "futures_price": fut_px,
                "trade_usd": trade_usd,
                "opened_at": int(time.time() * 1000),
            },
            positions_path,
        )
        return CrossVenueResult(True, "simulated", position_id, executed, logs)

    # ── live：现货腿先行（用自有/借入资产，风险低）────────────────────────
    if direction == "reverse":
        # margin 借卖需抵押金就绪（Bitget crossed margin 为独立账户；UTA/Binance 各自处理）
        collateral = trade_usd * 0.6
        try:
            if sv.transfer_asset(quote, collateral, "spot", "margin"):
                logs.append(f"{spot_venue_id}: spot→margin 抵押划转 {collateral:.2f} {quote}")
        except Exception as e:
            logs.append(f"{spot_venue_id}: 抵押划转跳过 ({e})")

    res_spot = sv.execute_trades([spot_trade], spot_market, dry_run=False)
    executed.extend(res_spot)
    if not _filled(res_spot):
        logs.append(f"现货腿失败: {res_spot[0].get('error') if res_spot else 'no result'}")
        return CrossVenueResult(False, "aborted", "", executed, logs)
    exec_qty = _exec_qty(res_spot, base_amount)
    logs.append(f"现货腿成交 {spot_venue_id} {spot_trade['type']} {exec_qty} {base}")

    # 合约腿保证金就绪（所内 spot→futures，best effort）
    margin_usd = exec_qty * fut_px * MARGIN_BUFFER
    try:
        fut_bal = fv.fetch_usdt_account_balances()
        if float(fut_bal.get("futures", 0)) < margin_usd:
            if fv.transfer_asset(quote, margin_usd, "spot", "futures"):
                logs.append(f"{futures_venue_id}: spot→futures 划转 {margin_usd:.2f} {quote}")
    except Exception as e:
        logs.append(f"{futures_venue_id}: 保证金检查/划转跳过 ({e})")

    try:
        fv.initialize_futures_symbol(fut_mkt["pair"])
    except Exception:
        pass
    fut_trade["amount_base"] = _floor_qty(exec_qty, fut_mkt["quantity_precision"])
    res_fut = fv.execute_trades([fut_trade], fut_market, dry_run=False)
    executed.extend(res_fut)

    if _filled(res_fut):
        logs.append(f"合约腿成交 {futures_venue_id} {fut_trade['type']} {fut_trade['amount_base']} {base}")
        _record_position(
            {
                "id": position_id,
                "status": "open",
                "dry_run": False,
                "base": base,
                "direction": direction,
                "futures_venue": futures_venue_id,
                "spot_venue": spot_venue_id,
                "qty": exec_qty,
                "futures_qty": fut_trade["amount_base"],
                "spot_price": res_spot[0].get("exec_price", spot_px),
                "futures_price": res_fut[0].get("exec_price", fut_px),
                "trade_usd": trade_usd,
                "opened_at": int(time.time() * 1000),
            },
            positions_path,
        )
        return CrossVenueResult(True, "filled", position_id, executed, logs)

    # ── 合约腿失败 → 回滚现货腿 ──────────────────────────────────────────
    logs.append(f"合约腿失败: {res_fut[0].get('error') if res_fut else 'no result'}，回滚现货腿")
    rollback: dict[str, Any] = {
        "symbol": base,
        "type": "sell" if direction == "forward" else "buy",
        "amount_base": exec_qty,
        "amount_usdt": round(exec_qty * spot_px, 4),
        "reason": "ROLLBACK: cross-venue futures leg failed",
    }
    if direction == "reverse":
        rollback["account"] = "margin"
        rollback["side_effect"] = "auto_repay"
    send_notification(
        "Cross-Venue Leg Failure",
        f"{futures_venue_id} {fut_trade['type']} {base} failed; rolling back "
        f"{spot_venue_id} {rollback['type']} {exec_qty} {base}",
        config,
    )
    res_rb = sv.execute_trades([rollback], spot_market, dry_run=False)
    executed.extend(res_rb)
    if _filled(res_rb):
        logs.append("回滚成功，无裸露持仓")
        return CrossVenueResult(False, "rolled_back", "", executed, logs)

    logs.append("回滚失败！现货腿裸露，需人工处理")
    send_notification(
        "NAKED POSITION",
        f"Cross-venue rollback failed: {spot_venue_id} holds {exec_qty} {base} unhedged",
        config,
    )
    return CrossVenueResult(False, "naked", "", executed, logs)


# ── 平仓 ─────────────────────────────────────────────────────────────────────

def close_cross_venue_position(
    position_id: str,
    *,
    dry_run: bool | None = None,
    quote: str = "USDT",
    config: dict[str, Any] | None = None,
    futures_venue: Any = None,
    spot_venue: Any = None,
    positions_path: Path = POSITIONS_PATH,
) -> CrossVenueResult:
    """按持仓记录平仓。合约腿先平（消除 funding 暴露），再处理现货腿。"""
    pos = next(
        (p for p in load_positions(positions_path) if p.get("id") == position_id and p.get("status") == "open"),
        None,
    )
    if pos is None:
        return CrossVenueResult(False, "aborted", logs=[f"未找到 open 持仓 {position_id}"])

    base = pos["base"]
    direction: Direction = pos["direction"]
    fv_id, sv_id = pos["futures_venue"], pos["spot_venue"]
    qty = float(pos.get("futures_qty") or pos["qty"])
    spot_qty = float(pos["qty"])
    if dry_run is None:
        dry_run = bool(pos.get("dry_run", True))

    logs: list[str] = []
    executed: list[dict[str, Any]] = []
    fv = futures_venue or get_venue({"venue": {"type": fv_id}})
    sv = spot_venue or get_venue({"venue": {"type": sv_id}})
    spot_mkt = _leg_market(sv, base, quote, futures=False)
    fut_mkt = _leg_market(fv, base, quote, futures=True)
    spot_px = spot_mkt["price"]

    fut_trade: dict[str, Any] = {
        "symbol": base,
        "type": "close_short" if direction == "forward" else "close_long",
        "amount_base": qty,
        "amount_usdt": round(qty * (fut_mkt["price"] or spot_px), 4),
        "quantity_precision": fut_mkt["quantity_precision"],
        "reason": f"Cross-venue {direction} close {position_id}",
    }
    spot_trade: dict[str, Any] = {
        "symbol": base,
        "type": "sell" if direction == "forward" else "buy",
        "amount_base": spot_qty,
        "amount_usdt": round(spot_qty * spot_px, 4),
        "reason": fut_trade["reason"],
    }
    if direction == "reverse":
        spot_trade["account"] = "margin"
        spot_trade["side_effect"] = "auto_repay"
    fut_market = {base: fut_mkt}
    spot_market = {base: spot_mkt}

    if dry_run:
        executed.extend(fv.execute_trades([fut_trade], fut_market, dry_run=True))
        executed.extend(sv.execute_trades([spot_trade], spot_market, dry_run=True))
        _mark_closed(position_id, {"dry_run": True}, positions_path)
        logs.append(f"[DRY-RUN] close {direction} {base} qty={qty}")
        return CrossVenueResult(True, "simulated", position_id, executed, logs)

    # 合约腿先平
    res_fut = fv.execute_trades([fut_trade], fut_market, dry_run=False)
    executed.extend(res_fut)
    if not _filled(res_fut):
        logs.append(f"合约平仓失败: {res_fut[0].get('error') if res_fut else 'no result'}")
        return CrossVenueResult(False, "aborted", position_id, executed, logs)
    closed_qty = _exec_qty(res_fut, qty)
    logs.append(f"合约腿已平 {fv_id} {fut_trade['type']} {closed_qty} {base}")

    # reverse 平仓买回量对齐 margin 实际债务（含利息）
    if direction == "reverse" and hasattr(sv, "fetch_margin_debt"):
        try:
            debt = float(sv.fetch_margin_debt([base]).get(base, 0.0))
            if spot_qty < debt <= spot_qty * 1.02:
                spot_trade["amount_base"] = debt
                spot_trade["amount_usdt"] = round(debt * spot_px, 4)
        except Exception:
            pass

    res_spot = sv.execute_trades([spot_trade], spot_market, dry_run=False)
    executed.extend(res_spot)
    if _filled(res_spot):
        logs.append(f"现货腿已平 {sv_id} {spot_trade['type']} {spot_trade['amount_base']} {base}")
        _mark_closed(
            position_id,
            {
                "futures_price": res_fut[0].get("exec_price"),
                "spot_price": res_spot[0].get("exec_price"),
            },
            positions_path,
        )
        return CrossVenueResult(True, "filled", position_id, executed, logs)

    # 现货腿失败 → 重新开合约腿对冲，避免单边暴露
    logs.append("现货腿失败，重新开合约腿对冲")
    reopen = dict(fut_trade)
    reopen["type"] = "open_short" if direction == "forward" else "open_long"
    reopen["amount_base"] = closed_qty
    reopen["reason"] = "ROLLBACK: cross-venue spot close failed"
    send_notification(
        "Cross-Venue Close Rollback",
        f"{sv_id} {spot_trade['type']} {base} failed; re-opening {fv_id} {reopen['type']} {closed_qty}",
        config,
    )
    res_rb = fv.execute_trades([reopen], fut_market, dry_run=False)
    executed.extend(res_rb)
    if _filled(res_rb):
        logs.append("已重新对冲，持仓保持 open")
        return CrossVenueResult(False, "rolled_back", position_id, executed, logs)

    logs.append("重新对冲失败！现货腿裸露，需人工处理")
    send_notification(
        "NAKED POSITION",
        f"Cross-venue close rollback failed: {sv_id} {base} exposure unhedged",
        config,
    )
    return CrossVenueResult(False, "naked", position_id, executed, logs)
