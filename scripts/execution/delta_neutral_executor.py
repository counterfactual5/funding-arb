"""Delta Neutral atomic executor with spot/futures synchronization and rollback."""

import sys
import time
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from core.notify import send_notification


def _margin_rollback_tags(spot_trade: dict[str, Any]) -> dict[str, Any]:
    """Reverse C&C spot leg uses margin: rollback direction is opposite, borrow/repay side_effect must also be inverted."""
    if str(spot_trade.get("account", "")).lower() != "margin":
        return {}
    inverse = {"auto_borrow": "auto_repay", "auto_repay": "auto_borrow"}
    tags: dict[str, Any] = {"account": "margin"}
    effect = inverse.get(str(spot_trade.get("side_effect", "")).lower())
    if effect:
        tags["side_effect"] = effect
    return tags


def execute_delta_neutral_trades(
    venue: Any,
    trades: list[dict[str, Any]],
    market: dict[str, dict[str, Any]],
    dry_run: bool,
    config: dict | None = None,
) -> list[dict[str, Any]]:
    """
    Executes a list of delta neutral trades atomically.
    Handles Binance spot/futures wallet transfers, precision mismatches, and rollbacks.
    """
    if dry_run:
        executed = []
        for t in trades:
            ex = dict(t)
            ex["status"] = "simulated"
            ex["price"] = market.get(t["symbol"], {}).get("price", 0.0)
            executed.append(ex)
        return executed

    executed = []

    # Group trades by pairs to ensure each pair (spot + futures) is processed together
    pairs_trades: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        pairs_trades.setdefault(t["symbol"], []).append(t)

    for sym, sym_trades in pairs_trades.items():
        if len(sym_trades) == 1:
            # Not a pair, just execute (e.g. forward liquidation where we only sell spot or close short independently)
            executed.extend(venue.execute_trades(sym_trades, market, dry_run=False))
            continue

        # We expect a spot trade and a futures trade
        spot_trade = next((t for t in sym_trades if t["type"] in ("buy", "sell")), None)
        futures_trade = next(
            (
                t
                for t in sym_trades
                if t["type"] in ("open_short", "close_short", "open_long", "close_long")
            ),
            None,
        )

        if not spot_trade or not futures_trade:
            executed.extend(venue.execute_trades(sym_trades, market, dry_run=False))
            continue

        # Apply Futures Symbol Rules
        # fetch_futures_symbol_rules gives futures specific precision
        quote = config.get("cash", "USDT") if config else "USDT"
        pair = f"{sym.upper()}{quote.upper()}"
        futures_rules = getattr(venue, "fetch_futures_symbol_rules", lambda x: None)(
            pair
        )
        if futures_rules:
            futures_trade["quantity_precision"] = futures_rules.get(
                "quantity_precision", spot_trade.get("quantity_precision", 3)
            )

            # Check min notional
            trade_usd = spot_trade["amount_base"] * market.get(sym, {}).get(
                "price", 0.0
            )
            if trade_usd < futures_rules.get("min_trade_usdt", 0) or spot_trade[
                "amount_base"
            ] < futures_rules.get("min_trade_base", 0):
                # Cannot execute due to rules
                continue

        if "open_" in futures_trade["type"]:
            # FORWARD: Spot Buy + Open Short (or Spot Sell + Open Long)

            # ATOMIC EXECUTION: Spot First, then Transfer, then Futures
            # Spot buy is safer to do first because we use our cash.
            res_spot = venue.execute_trades([spot_trade], market, dry_run=False)
            executed.extend(res_spot)

            if res_spot and res_spot[0].get("status") == "filled":
                exec_qty = res_spot[0].get("exec_qty", spot_trade["amount_base"])
                exec_price = res_spot[0].get(
                    "exec_price", market.get(sym, {}).get("price", 0.0)
                )
                actual_trade_usd = exec_qty * exec_price

                # Match futures quantity to actual spot quantity
                futures_trade["amount_base"] = exec_qty

                # Transfer margin
                transfer_success = True
                transfer_amount = (
                    actual_trade_usd * 1.05
                )  # Add some buffer for margin + fees
                if hasattr(venue, "transfer_asset"):
                    transfer_success = venue.transfer_asset(
                        "USDT", transfer_amount, "spot", "futures"
                    )

                if transfer_success:
                    # Spot succeeded & Transfer succeeded, execute futures
                    res_fut = venue.execute_trades(
                        [futures_trade], market, dry_run=False
                    )
                    executed.extend(res_fut)

                    if not res_fut or res_fut[0].get("status") != "filled":
                        # ROLLBACK: Futures failed, need to undo Spot
                        rollback_type = "sell" if spot_trade["type"] == "buy" else "buy"
                        rollback_trade = {
                            "symbol": sym,
                            "type": rollback_type,
                            "amount_base": exec_qty,
                            "amount_usdt": exec_qty
                            * market.get(sym, {}).get("price", 0.0),
                            "reason": "ROLLBACK: Futures execution failed",
                            **_margin_rollback_tags(spot_trade),
                        }
                        send_notification(
                            "Leg Failure Rollback",
                            f"Futures execution failed for {sym}. Rolling back Spot {rollback_type} {exec_qty} {sym}.",
                            config,
                        )
                        rollback_res = venue.execute_trades(
                            [rollback_trade], market, dry_run=False
                        )
                        executed.extend(rollback_res)
                        if hasattr(venue, "transfer_asset"):
                            venue.transfer_asset(
                                "USDT", transfer_amount, "futures", "spot"
                            )
                else:
                    # Transfer failed, rollback spot
                    rollback_type = "sell" if spot_trade["type"] == "buy" else "buy"
                    rollback_trade = {
                        "symbol": sym,
                        "type": rollback_type,
                        "amount_base": exec_qty,
                        "amount_usdt": exec_qty * market.get(sym, {}).get("price", 0.0),
                        "reason": "ROLLBACK: Margin transfer failed",
                        **_margin_rollback_tags(spot_trade),
                    }
                    send_notification(
                        "Margin Transfer Failed",
                        f"Transfer to futures failed for {sym}. Rolling back Spot {rollback_type} {exec_qty} {sym}.",
                        config,
                    )
                    rollback_res = venue.execute_trades(
                        [rollback_trade], market, dry_run=False
                    )
                    executed.extend(rollback_res)
        elif "close_" in futures_trade["type"]:
            # UNWIND: Close Short + Spot Sell
            # Futures First, then Spot
            res_fut = venue.execute_trades([futures_trade], market, dry_run=False)
            executed.extend(res_fut)

            if res_fut and res_fut[0].get("status") == "filled":
                exec_qty = res_fut[0].get("exec_qty", spot_trade["amount_base"])
                spot_trade["amount_base"] = exec_qty

                # Reverse close: align buy-back quantity with actual margin debt (borrowed+interest),
                # use AUTO_REPAY to clear interest dust in one go; deviation >2% treated as anomalous data and ignored.
                if (
                    spot_trade["type"] == "buy"
                    and str(spot_trade.get("account", "")).lower() == "margin"
                    and hasattr(venue, "fetch_margin_debt")
                ):
                    try:
                        debt = float(venue.fetch_margin_debt([sym]).get(sym, 0.0))
                        if exec_qty < debt <= exec_qty * 1.02:
                            spot_trade["amount_base"] = debt
                    except Exception:
                        pass

                # Futures succeeded, now Spot
                res_spot = venue.execute_trades([spot_trade], market, dry_run=False)
                executed.extend(res_spot)

                if not res_spot or res_spot[0].get("status") != "filled":
                    # ROLLBACK: Spot failed, need to undo Futures Close
                    # This means we closed short but couldn't sell spot -> we are long. We must re-open short.
                    rollback_type = (
                        "open_short"
                        if futures_trade["type"] == "close_short"
                        else "open_long"
                    )
                    rollback_trade = dict(futures_trade)
                    rollback_trade["type"] = rollback_type
                    rollback_trade["amount_base"] = exec_qty
                    rollback_trade["reason"] = "ROLLBACK: Spot execution failed"
                    send_notification(
                        "Leg Failure Rollback",
                        f"Spot execution failed for {sym}. Re-opening Futures {rollback_type} {exec_qty} {sym} to close naked exposure.",
                        config,
                    )
                    rollback_res = venue.execute_trades(
                        [rollback_trade], market, dry_run=False
                    )
                    executed.extend(rollback_res)
                else:
                    # Both succeeded, transfer margin back to spot
                    if hasattr(venue, "transfer_asset"):
                        spot_px = res_spot[0].get(
                            "exec_price", market.get(sym, {}).get("price", 0.0)
                        )
                        actual_trade_usd = exec_qty * spot_px
                        transfer_amount = actual_trade_usd * 0.98
                        venue.transfer_asset("USDT", transfer_amount, "futures", "spot")

    return executed
