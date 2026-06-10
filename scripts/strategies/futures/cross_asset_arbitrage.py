#!/usr/bin/env python3
"""Cross-Asset Cash and Carry Arbitrage Strategy（资金费率套利 · 多资产规范引擎）。

费率单位约定（重要）
====================
本模块所有费率都以「百分比 / 每个资金费结算周期」为单位：
  - funding_rates[sym]: 交易所原始资金费率 × 100（0.01 = 0.01% / 每 8h）。
  - borrow_rates[sym] : **调用方必须先把借币利率归一到同一个资金费周期再传进来**，
                        不能拿按小时/按日的借币利率直接和按 8h 的资金费比大小（量纲错配）。
                        归一化在 runner 侧完成（run_cash_and_carry.py）。
单资产 cash_and_carry 即本引擎 maxConcurrentPairs=1 的特例（见 cash_and_carry.py）。
"""
from __future__ import annotations
from typing import Any


_DUST = 1e-9


def _close_pair_trades(
    sym: str, pos: dict[str, Any], spot_held: float, price: float, reason: str
) -> list[dict[str, Any]]:
    """生成平仓腿（保守对齐）：双腿取 min(现货对冲腿, 永续腿)，避免裸敞口。

    现货腿缺失（漂移/已被单独处理）时只平永续，绝不凭空卖出/买回不存在的现货——
    否则实盘 spot 单失败会触发 executor 回滚重开永续，每轮死循环。
    """
    perp_qty = float(pos["amount"])
    if perp_qty <= _DUST or price <= 0:
        return []

    if pos["side"] == "short":
        spot_qty = max(float(spot_held), 0.0)
        spot_type, perp_type = "sell", "close_short"
    else:
        spot_qty = abs(min(float(spot_held), 0.0))  # reverse: 现货负数为借币债务
        spot_type, perp_type = "buy", "close_long"

    qty = min(perp_qty, spot_qty) if spot_qty > _DUST else perp_qty
    out = []
    if spot_qty > _DUST:
        spot_leg = {
            "symbol": sym, "type": spot_type,
            "amount_base": round(qty, 8),
            "amount_usdt": round(qty * price, 2),
            "reason": reason,
        }
        if pos["side"] == "long":
            # reverse 平仓：买回借来的币并自动还款（margin 账户）
            spot_leg["account"] = "margin"
            spot_leg["side_effect"] = "auto_repay"
        out.append(spot_leg)
    out.append({
        "symbol": sym, "type": perp_type,
        "amount_base": round(qty, 8),
        "amount_usdt": round(qty * price, 2),
        "reason": reason,
    })
    return out


def _held_net_spread(
    pos: dict[str, Any], rate: float, borrow_rate_pct: float, min_net_edge_pct: float
) -> float:
    if pos["side"] == "short":
        return rate if rate >= min_net_edge_pct else 0.0
    return max(abs(rate) - borrow_rate_pct, 0.0)


def decide_cross_asset_arbitrage(
    holdings: dict[str, float],
    futures_state: dict[str, Any],
    prices: dict[str, float], # {asset: price}
    market: dict[str, dict[str, Any]],
    cfg: dict[str, Any],
    funding_rates: dict[str, float], # {asset: rate_pct}
    borrow_rates: dict[str, float],  # {asset: rate_pct}（已归一到资金费周期）
    next_funding_times: dict[str, int] | None = None, # {asset: next_funding_ts_ms}
    current_time_ms: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Evaluates multiple assets and allocates cash to the ones with the most extreme funding rates.
    """
    trades: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"strategy": "cross_asset_arbitrage", "skipped": []}

    cash = cfg.get("cash", "USDT")
    cc_cfg = cfg.get("crossAssetArbitrage") or {}

    max_slots = int(cc_cfg.get("maxConcurrentPairs", 3))
    trade_usd = float(cc_cfg.get("tradeUsdPerSlot", 3000.0))

    entry_rate_pct = float(cc_cfg.get("entryFundingRatePct", 0.05))
    exit_rate_pct = float(cc_cfg.get("exitFundingRatePct", 0.01))

    rev_entry_rate_pct = float(cc_cfg.get("reverseEntryFundingRatePct", -0.05))
    rev_exit_rate_pct = float(cc_cfg.get("reverseExitFundingRatePct", -0.01))
    min_rev_spread = float(cc_cfg.get("minReverseSpreadPct", 0.02))
    # 每周期净边际下限：开平两腿摊薄手续费 + 安全垫；forward/reverse 入场都要过这道闸。
    min_net_edge_pct = float(cc_cfg.get("minNetEdgePct", 0.02))
    # 时间锁：仅在距离结算小于该分钟数时才允许开新仓（平仓不受限）。<=0 表示不限制。
    max_minutes_to_settle = float(cc_cfg.get("maxMinutesToSettlement", 0))

    positions = futures_state.get("positions", {})
    
    # 1. Check existing positions for exit conditions
    # We copy keys to list to allow modifications
    active_assets = list(positions.keys())
    for sym in active_assets:
        pos = positions[sym]
        if sym not in funding_rates:
            meta.setdefault("held_without_funding", []).append(sym)
            continue
        current_rate = funding_rates[sym]
        current_price = prices.get(sym, 0.0)
        if current_price <= 0:
            continue
        borrow_rate_pct = borrow_rates.get(sym, 0.0)
        spot_held = holdings.get(sym, 0.0)

        if pos["side"] == "short":
            if current_rate <= exit_rate_pct:
                trades.extend(_close_pair_trades(
                    sym, pos, spot_held, current_price,
                    f"Funding fell to {current_rate}%. Closing Forward Arbitrage."
                ))

        elif pos["side"] == "long":
            abs_funding = abs(current_rate)
            if current_rate >= rev_exit_rate_pct or abs_funding <= borrow_rate_pct:
                trades.extend(_close_pair_trades(
                    sym, pos, spot_held, current_price,
                    f"Funding normalized to {current_rate}%. Closing Reverse Arbitrage."
                ))
                
    # Temporarily calculate available slots (assuming all exits above execute immediately)
    exiting_symbols = [t["symbol"] for t in trades if t["type"] in ("close_short", "close_long")]
    current_active_slots = len(active_assets) - len(exiting_symbols)
    available_slots = max_slots - current_active_slots
    
    # 2. Rank candidates by net spread
    candidates = []
    for sym, rate in funding_rates.items():
        if sym in active_assets:
            continue
        if prices.get(sym, 0) <= 0:
            continue
            
        net_spread = 0.0
        borrow_rate_pct = borrow_rates.get(sym, 0.0)

        # Calculate potential net spread（同周期；reverse 要扣借币成本）
        if rate >= entry_rate_pct:
            # Forward 收资金费、无借币成本；净边际即资金费本身，但仍要盖过手续费闸。
            if rate >= min_net_edge_pct:
                net_spread = rate
        elif rate <= rev_entry_rate_pct:
            net_spread = abs(rate) - borrow_rate_pct
            # 同时满足 reverse 专用阈值与通用手续费闸，否则边际太薄不开。
            if net_spread < max(min_rev_spread, min_net_edge_pct):
                net_spread = 0.0

        if net_spread > 0:
            # 检查时间锁（只拦截新开仓，不拦截平仓）
            if max_minutes_to_settle > 0 and next_funding_times and current_time_ms > 0:
                nxt_ts = next_funding_times.get(sym, 0)
                if nxt_ts > current_time_ms:
                    mins_left = (nxt_ts - current_time_ms) / 60000.0
                    if mins_left > max_minutes_to_settle:
                        net_spread = 0.0 # 太早了，不入场

        if net_spread > 0:
            candidates.append({"symbol": sym, "rate": rate, "net_spread": net_spread})
            
    # Sort candidates by best spread
    candidates.sort(key=lambda x: x["net_spread"], reverse=True)
    
    # 3. Preemption (Mercenary Switch) Logic
    # If no slots available but we have strong candidates, check if we should kick the weakest holding
    if available_slots <= 0 and candidates:
        active_retained = []
        for sym in active_assets:
            if sym in exiting_symbols:
                continue
            pos = positions[sym]
            if sym not in funding_rates:
                continue
            rate = funding_rates[sym]
            b_rate = borrow_rates.get(sym, 0.0)
            net_spread = _held_net_spread(pos, rate, b_rate, min_net_edge_pct)
            active_retained.append({"symbol": sym, "net_spread": net_spread, "pos": pos})
            
        # Sort active holdings by worst spread
        active_retained.sort(key=lambda x: x["net_spread"])
        
        # Try to preempt one by one
        while available_slots <= 0 and candidates and active_retained:
            best_candidate = candidates[0]
            worst_active = active_retained[0]
            # Configurable friction buffer (default 0.40%)
            friction_buffer = float(cc_cfg.get("preemptionFrictionBufferPct", 0.40))

            if best_candidate["net_spread"] > worst_active["net_spread"] + friction_buffer:
                # Force Close worst active
                sym = worst_active["symbol"]
                pos = worst_active["pos"]
                current_price = prices.get(sym, 0.0)
                arb_kind = "Forward" if pos["side"] == "short" else "Reverse"
                close_trades = _close_pair_trades(
                    sym, pos, holdings.get(sym, 0.0), current_price,
                    f"Preempted: New {best_candidate['symbol']} spread is {best_candidate['net_spread']:.2f}%. Force Closing {arb_kind} Arbitrage."
                )
                if not close_trades:
                    active_retained.pop(0)
                    continue
                trades.extend(close_trades)
                available_slots += 1
                active_retained.pop(0)
            else:
                break # Not worth switching
                
    # 4. Enter new positions
    # 现金口径：假设上面的 exit/preempt 先执行——forward 平仓的现货卖出回笼现金，
    # reverse 平仓的现货买回则占用现金（那笔钱本就是借币卖出时挂在账上的，不能再拿去开新仓）。
    cash_available = holdings.get(cash, 0.0)
    for t in trades:
        if t["type"] == "sell":
            cash_available += float(t.get("amount_usdt", 0.0))
        elif t["type"] == "buy":
            cash_available -= float(t.get("amount_usdt", 0.0))

    reverse_cash_mult = float(cc_cfg.get("reverseRequiredCashMult", cc_cfg.get("reverseCashBufferMult", 1.5)))
    forward_cash_mult = float(cc_cfg.get("forwardRequiredCashMult", cc_cfg.get("forwardCashBufferMult", 2.1)))

    for cand in candidates:
        if available_slots <= 0:
            break

        sym = cand["symbol"]
        current_rate = cand["rate"]
        current_price = prices[sym]
        required_cash = trade_usd * forward_cash_mult if current_rate >= entry_rate_pct else trade_usd * reverse_cash_mult
        if cash_available < required_cash:
            continue

        base_amount = round(trade_usd / current_price, 8)
        
        # Check minimum notional / quantity
        mkt = market.get(sym, {})
        min_usd = float(mkt.get("min_trade_usdt", 5.0))
        min_base = float(mkt.get("min_trade_base", 0.0))
        if trade_usd < min_usd or base_amount < min_base:
            meta["skipped"].append({"symbol": sym, "reason": f"trade_usd ({trade_usd}) or base ({base_amount}) below exchange minimums"})
            continue
        
        # Check Forward Arbitrage
        if current_rate >= entry_rate_pct:
            trades.append({
                "symbol": sym, "type": "buy",
                "amount_base": base_amount, "amount_usdt": trade_usd,
                "reason": f"Funding spiked to {current_rate}%. Opening Forward Arbitrage."
            })
            trades.append({
                "symbol": sym, "type": "open_short",
                "amount_base": base_amount, "amount_usdt": trade_usd,
                "reason": f"Funding spiked to {current_rate}%. Opening Forward Arbitrage."
            })
            available_slots -= 1
            cash_available -= (trade_usd * 2) # Approximate cash deduction
            
        elif current_rate <= rev_entry_rate_pct:
            trades.append({
                "symbol": sym, "type": "sell",
                "amount_base": base_amount, "amount_usdt": trade_usd,
                # reverse 开仓：现货腿在 margin 账户自动借入后卖出
                "account": "margin", "side_effect": "auto_borrow",
                "reason": f"Funding crashed to {current_rate}%. Opening Reverse Arbitrage."
            })
            trades.append({
                "symbol": sym, "type": "open_long",
                "amount_base": base_amount, "amount_usdt": trade_usd,
                "reason": f"Funding crashed to {current_rate}%. Opening Reverse Arbitrage."
            })
            available_slots -= 1
            cash_available -= trade_usd # Approximate cash deduction
                
    return trades, meta
