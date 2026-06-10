#!/usr/bin/env python3
"""Paper portfolio state for Futures & Delta Neutral strategies."""

from __future__ import annotations
from typing import Any

def default_futures_state() -> dict[str, Any]:
    return {
        "positions": {}, # symbol: {"amount": float, "entry_price": float, "side": "short" | "long"}
        "cumulative_funding_paid": 0.0,
        "cumulative_borrow_paid": 0.0,
        "cumulative_fees": 0.0,
    }

def liquidation_price(
    side: str, entry_price: float, leverage: float, mmr: float = 0.005
) -> float | None:
    """Isolated-margin 近似强平价。

    leverage<=0（视为无杠杆/全额保证金）→ 永不强平，返回 None。
    short: 价格涨到 entry*(1 + 1/L - mmr) 触发；long: 跌到 entry*(1 - 1/L + mmr)。
    这是单腿隔离保证金视角的保守估计——cross-margin 下现货腿会托住，实际更难触发。
    """
    if leverage <= 0 or entry_price <= 0:
        return None
    if side == "short":
        return entry_price * (1.0 + 1.0 / leverage - mmr)
    return max(0.0, entry_price * (1.0 - 1.0 / leverage + mmr))


def check_liquidations(
    futures_state: dict[str, Any], prices: dict[str, float]
) -> list[dict[str, Any]]:
    """返回已触及强平价的永续仓位（不修改状态，仅检测）。"""
    hits: list[dict[str, Any]] = []
    for sym, pos in futures_state.get("positions", {}).items():
        liq = pos.get("liq_price")
        if not liq or pos.get("amount", 0) <= 0:
            continue
        px = float(prices.get(sym, 0) or 0)
        if px <= 0:
            continue
        breached = (pos["side"] == "short" and px >= liq) or (pos["side"] == "long" and px <= liq)
        if breached:
            hits.append({"symbol": sym, "side": pos["side"], "liq_price": liq, "mark": px})
    return hits


def margin_health(
    holdings: dict[str, float],
    futures_state: dict[str, Any],
    prices: dict[str, float],
    cash: str = "USDT",
    mmr: float = 0.005,
) -> dict[str, Any]:
    """账户保证金健康度（cross-margin 视角）。

    used_margin = Σ notional/leverage；maintenance = Σ notional*mmr；
    margin_ratio = equity / maintenance（<1 即账户级强平）。返回最近一笔距强平的百分比。
    """
    equity = calculate_futures_nav(holdings, futures_state, prices, cash)
    used_margin = 0.0
    maintenance = 0.0
    nearest_pct = None
    for sym, pos in futures_state.get("positions", {}).items():
        px = float(prices.get(sym, 0) or 0)
        if px <= 0 or pos.get("amount", 0) <= 0:
            continue
        notional = pos["amount"] * px
        lev = float(pos.get("leverage", 1.0)) or 1.0
        used_margin += notional / lev
        maintenance += notional * mmr
        liq = pos.get("liq_price")
        if liq:
            dist = abs(px - liq) / px * 100.0
            nearest_pct = dist if nearest_pct is None else min(nearest_pct, dist)
    return {
        "equity_usd": round(equity, 2),
        "used_margin_usd": round(used_margin, 2),
        "maintenance_margin_usd": round(maintenance, 2),
        "free_margin_usd": round(equity - used_margin, 2),
        "margin_ratio": round(equity / maintenance, 2) if maintenance > 0 else None,
        "nearest_liq_distance_pct": round(nearest_pct, 2) if nearest_pct is not None else None,
    }


def normalize_executed_for_ledger(
    executed: list[dict[str, Any]],
    prices: dict[str, float],
) -> list[dict[str, Any]]:
    """把 venue/executor 返回的成交记录归一化成账本可入账的最小结构。

    必须保留 ``status`` 并补齐 ``amount_usdt``：apply_simulated_futures_trades 会静默
    跳过 status 不在 (simulated, filled) 的记录，且用 amount_usdt 计费/算加权入场价。
    （此前各 runner 手工归一化时丢掉这两个字段，导致纸面账本从不入账。）
    """
    out: list[dict[str, Any]] = []
    for ex in executed:
        status = ex.get("status")
        if status not in ("simulated", "filled"):
            continue
        sym = ex["symbol"]
        base = float(ex.get("exec_qty") or ex.get("amount_base") or 0)
        px = float(ex.get("exec_price") or ex.get("price") or prices.get(sym, 0) or 0)
        if base <= 0 or px <= 0:
            continue
        out.append({
            "symbol": sym,
            "type": ex["type"],
            "status": status,
            "amount_base": base,
            "amount_usdt": base * px,
            "price": px,
        })
    return out


def apply_simulated_futures_trades(
    holdings: dict[str, float],
    futures_state: dict[str, Any],
    executed: list[dict[str, Any]],
    prices: dict[str, float],
    cash: str = "USDT",
    spot_fee_rate: float = 0.001,
    perp_fee_rate: float = 0.0005, # Maker/Taker avg for perps
    leverage: float = 1.0,
    maintenance_margin_rate: float = 0.005,
) -> tuple[dict[str, float], dict[str, Any]]:
    """
    Update spot holdings and futures positions after simulated trades.
    Supports trade types: 'buy' (spot), 'sell' (spot),
    'open_short' (perp), 'close_short' (perp),
    'open_long' (perp), 'close_long' (perp)

    永续开仓会记录 leverage 并按加权入场价重算 liq_price（隔离保证金近似）。
    """
    h = dict(holdings)
    f_state = dict(futures_state)
    positions = dict(f_state.get("positions", {}))
    
    for t in executed:
        if t.get("status") not in ("simulated", "filled"):
            continue
            
        sym = t["symbol"]
        amount_usdt = float(t.get("amount_usdt", 0))
        price = prices.get(sym, t.get("price", 1.0))
        
        if t["type"] in ("buy", "sell"):
            # Spot Trades
            fee = float(t.get("fee_usd", amount_usdt * spot_fee_rate))
            if t["type"] == "buy":
                h[cash] = h.get(cash, 0) - amount_usdt - fee
                h[sym] = h.get(sym, 0) + t.get("amount_base", amount_usdt / price)
            elif t["type"] == "sell":
                h[sym] = h.get(sym, 0) - t.get("amount_base", amount_usdt / price)
                h[cash] = h.get(cash, 0) + amount_usdt - fee
                
        elif t["type"] in ("open_short", "open_long"):
            # Open Perp
            side = "short" if t["type"] == "open_short" else "long"
            fee = amount_usdt * perp_fee_rate
            h[cash] = h.get(cash, 0) - fee
            f_state["cumulative_fees"] = f_state.get("cumulative_fees", 0) + fee
            
            amount_base = t.get("amount_base", amount_usdt / price)
            pos = positions.get(sym)
            if not pos or pos["side"] != side:
                pos = {"amount": 0.0, "entry_price": 0.0, "side": side}

            # Weighted average entry price
            total_value = pos["amount"] * pos["entry_price"] + amount_usdt
            pos["amount"] += amount_base
            pos["entry_price"] = total_value / pos["amount"] if pos["amount"] > 0 else 0
            # 杠杆 + 强平价（按加权入场价重算）。trade 可覆盖默认 leverage。
            lev = float(t.get("leverage", leverage))
            pos["leverage"] = lev
            pos["liq_price"] = liquidation_price(side, pos["entry_price"], lev, maintenance_margin_rate)
            positions[sym] = pos
            
        elif t["type"] in ("close_short", "close_long"):
            # Close Perp
            side = "short" if t["type"] == "close_short" else "long"
            fee = amount_usdt * perp_fee_rate
            h[cash] = h.get(cash, 0) - fee
            f_state["cumulative_fees"] = f_state.get("cumulative_fees", 0) + fee
            
            pos = positions.get(sym)
            if pos and pos["side"] == side and pos["amount"] > 0:
                amount_base = t.get("amount_base", amount_usdt / price)
                close_amount = min(pos["amount"], amount_base)
                
                # Calculate PnL
                if side == "short":
                    pnl = (pos["entry_price"] - price) * close_amount
                else: # long
                    pnl = (price - pos["entry_price"]) * close_amount
                    
                h[cash] = h.get(cash, 0) + pnl
                
                pos["amount"] -= close_amount
                if pos["amount"] <= 1e-8:
                    del positions[sym]
                else:
                    positions[sym] = pos

    f_state["positions"] = positions
    return h, f_state

def calculate_futures_nav(
    holdings: dict[str, float],
    futures_state: dict[str, Any],
    prices: dict[str, float],
    cash: str = "USDT"
) -> float:
    """Calculate Total Net Asset Value including Spot and Unrealized Perp PnL."""
    nav = float(holdings.get(cash, 0))
    
    # Add Spot Value (Negative holdings implicitly deduct from NAV as spot debt)
    for sym, qty in holdings.items():
        if sym == cash:
            continue
        nav += float(qty) * float(prices.get(sym, 0))
        
    # Add Futures uPnL (Unrealized PnL)
    positions = futures_state.get("positions", {})
    for sym, pos in positions.items():
        current_price = float(prices.get(sym, 0))
        if current_price > 0:
            if pos["side"] == "short":
                upnl = (pos["entry_price"] - current_price) * pos["amount"]
            else:
                upnl = (current_price - pos["entry_price"]) * pos["amount"]
            nav += upnl
            
    return nav

def apply_funding_fees(
    holdings: dict[str, float],
    futures_state: dict[str, Any],
    prices: dict[str, float],
    funding_rates: dict[str, float], # {symbol: rate_percent_per_period}
    cash: str = "USDT"
) -> tuple[dict[str, float], dict[str, Any]]:
    """
    Deduct or Add funding fees based on current positions.
    Shorts: Positive funding -> Receive money, Negative funding -> Pay money.
    Longs: Positive funding -> Pay money, Negative funding -> Receive money.
    """
    h = dict(holdings)
    f_state = dict(futures_state)
    positions = f_state.get("positions", {})
    
    total_funding_exchanged = 0.0
    
    for sym, pos in positions.items():
        if pos["amount"] > 0:
            rate_pct = funding_rates.get(sym, 0.0)
            current_price = prices.get(sym, pos["entry_price"])
            position_notional = pos["amount"] * current_price
            
            # Decimal conversion
            rate_decimal = rate_pct / 100.0
            
            # Calculate payment (Positive value means we RECEIVE money, Negative means we PAY)
            if pos["side"] == "short":
                funding_payment = position_notional * rate_decimal
            else: # long
                funding_payment = position_notional * (-rate_decimal)
            
            h[cash] = h.get(cash, 0) + funding_payment
            total_funding_exchanged += funding_payment
            
    f_state["cumulative_funding_paid"] = f_state.get("cumulative_funding_paid", 0.0) - total_funding_exchanged
    return h, f_state

def apply_borrow_fees(
    holdings: dict[str, float],
    futures_state: dict[str, Any],
    prices: dict[str, float],
    borrow_rates: dict[str, float], # {symbol: rate_percent_per_period}
    cash: str = "USDT"
) -> tuple[dict[str, float], dict[str, Any]]:
    """
    Deduct borrow fees for negative spot holdings.
    """
    h = dict(holdings)
    f_state = dict(futures_state)
    
    total_borrow_paid = 0.0
    
    for sym, qty in holdings.items():
        if sym == cash or qty >= -1e-8:
            continue
            
        rate_pct = borrow_rates.get(sym, 0.0)
        if rate_pct <= 0:
            continue
            
        current_price = prices.get(sym, 0.0)
        if current_price <= 0:
            continue
            
        debt_notional = abs(qty) * current_price
        borrow_fee = debt_notional * (rate_pct / 100.0)
        
        h[cash] = h.get(cash, 0) - borrow_fee
        total_borrow_paid += borrow_fee
        
    f_state["cumulative_borrow_paid"] = f_state.get("cumulative_borrow_paid", 0.0) + total_borrow_paid
    return h, f_state
