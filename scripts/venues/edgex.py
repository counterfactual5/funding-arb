#!/usr/bin/env python3
"""EdgeX perp-DEX venue adapter (StarkEx CLOB, edgex-python-sdk V2).

Implements the futures-only subset of the CexVenue interface used by
pure_futures_executor / pure_futures_watcher. Read paths (prices, symbol rules,
contractId mapping) reuse the public V1 REST via EdgexFundingProvider and work
without credentials. Write paths (orders, balances, positions) use the async
V2 SDK Client wrapped with asyncio.run().

Credentials (V2 EIP-712 model):
    EDGEX_ACCOUNT_ID            — numeric account id
    EDGEX_TRADING_PRIVATE_KEY   — trading key (signs orders)
    EDGEX_BASE_URL             — optional, default https://edgex-prod-v2.edgex.exchange
    EDGEX_ASSET_BASE_URL       — optional, default https://spot.edgex.exchange

SDK surface (verified against edgex-Tech/edgex-python-sdk main):
    from edgex_sdk import Client, OrderSide
    Client(base_url, asset_base_url, account_id, trading_private_key)
    await client.create_limit_order(contract_id, size, price, side)
    await client.get_account_positions()
    await client.get_account_asset()
EdgeX is a CLOB with netted positions and has no market-order primitive in the
SDK, so opens/closes use an aggressive limit price (cross the book by a slippage
bound) — the same technique as the Lighter adapter.

NOTE: the exact field layout of get_account_positions()/get_account_asset() is
not documented; the parsers below are defensive and degrade to empty on
mismatch (safe for the watcher). Validate against a live account before relying
on position/balance readback.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from venues.edgex_funding import EdgexFundingProvider

_DEFAULT_BASE_URL = "https://edgex-prod-v2.edgex.exchange"
_DEFAULT_ASSET_BASE_URL = "https://spot.edgex.exchange"


def _base_from_pair(pair: str) -> str:
    s = pair.upper()
    return s[:-4] if s.endswith("USDT") else s


def _pair_from_base(base: str) -> str:
    return f"{base.upper()}USDT"


def _decimals_from_step(step: float) -> int:
    """Decimal places implied by a tick/step size (0.001 -> 3, 0.1 -> 1, 1 -> 0).

    Uses Decimal(str(...)) so float imprecision (0.1 -> 0.100000000000000006)
    doesn't inflate the precision.
    """
    from decimal import Decimal

    if step <= 0:
        return 0
    exp = Decimal(str(step)).normalize().as_tuple().exponent
    return -exp if isinstance(exp, int) and exp < 0 else 0


def _fmt(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def _run_async(coro: Any) -> Any:
    """Run an SDK coroutine from sync executor code (no running loop expected)."""
    return asyncio.run(coro)


class EdgexVenue:
    """EdgeX perp-DEX adapter — pure futures only (USD-collateral, treated 1:1 USDT)."""

    venue_id: str = "edgex"

    def __init__(self) -> None:
        self._funding = EdgexFundingProvider()

    # ── market data (public REST via funding provider) ─────────────────

    def get_futures_ticker(self, pair: str) -> float:
        try:
            return float(self._funding.fetch_current(pair).get("mark_price", 0.0) or 0.0)
        except Exception:
            return 0.0

    def get_ticker(self, pair: str) -> float:
        """EdgeX is perps-only; spot ticker falls back to the perp mark price."""
        return self.get_futures_ticker(pair)

    def fetch_futures_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        base = _base_from_pair(pair)
        try:
            meta = self._funding.contract_meta_for_base(base)
        except Exception:
            return None
        if meta is None:
            return None
        return {
            "symbol": pair,
            "quantity_precision": _decimals_from_step(float(meta["step_size"])),
            "quote_precision": _decimals_from_step(float(meta["tick_size"])),
            "min_trade_base": float(meta["min_order_size"]),
            "min_trade_usdt": 0.0,  # EdgeX minimum is size-based, not notional
        }

    def fetch_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        return self.fetch_futures_symbol_rules(pair, cache_sec)

    # ── account / positions (V2 SDK, async wrapped) ────────────────────

    def _make_client(self) -> Any:
        from edgex_sdk import Client

        account_id = os.environ.get("EDGEX_ACCOUNT_ID", "").strip()
        private_key = os.environ.get("EDGEX_TRADING_PRIVATE_KEY", "").strip()
        if not account_id or not private_key:
            raise RuntimeError(
                "EdgeX credentials missing: set EDGEX_ACCOUNT_ID and "
                "EDGEX_TRADING_PRIVATE_KEY"
            )
        return Client(
            base_url=os.environ.get("EDGEX_BASE_URL", _DEFAULT_BASE_URL),
            asset_base_url=os.environ.get("EDGEX_ASSET_BASE_URL", _DEFAULT_ASSET_BASE_URL),
            account_id=int(account_id),
            trading_private_key=private_key,
        )

    async def _get_asset(self) -> Any:
        client = self._make_client()
        try:
            return await client.get_account_asset()
        finally:
            await self._close(client)

    async def _get_positions(self) -> Any:
        client = self._make_client()
        try:
            return await client.get_account_positions()
        finally:
            await self._close(client)

    @staticmethod
    async def _close(client: Any) -> None:
        close = getattr(client, "close", None)
        if close is None:
            return
        try:
            res = close()
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            pass

    def fetch_usdt_account_balances(self) -> dict[str, float]:
        """Return {'spot': 0, 'futures': <available USD>} (USD ≈ USDT 1:1)."""
        try:
            asset = _run_async(self._get_asset())
        except Exception:
            return {"spot": 0.0, "futures": 0.0}
        data = asset.get("data", asset) if isinstance(asset, dict) else {}
        # Defensive: EdgeX returns a collateral list; sum available USD balance.
        avail = 0.0
        rows = data.get("collateralList") or data.get("collateralAssetModelList") or []
        for row in rows if isinstance(rows, list) else []:
            try:
                avail += float(row.get("availableAmount", row.get("amount", 0)) or 0)
            except (TypeError, ValueError):
                continue
        if avail == 0.0:
            try:
                avail = float(data.get("availableAmount", 0) or 0)
            except (TypeError, ValueError):
                avail = 0.0
        return {"spot": 0.0, "futures": avail}

    def fetch_futures_positions(self, quote: str = "USDT") -> list[dict[str, Any]]:
        try:
            resp = _run_async(self._get_positions())
        except Exception:
            return []
        data = resp.get("data", resp) if isinstance(resp, dict) else {}
        rows = (
            data.get("positionList")
            or data.get("positionAssetList")
            or (data if isinstance(data, list) else [])
        )
        # contractId → base, resolved from cached metadata.
        id_to_base = {
            m["contract_id"]: base
            for base, m in self._funding.contract_meta_map().items()
        }
        out: list[dict[str, Any]] = []
        for p in rows if isinstance(rows, list) else []:
            try:
                size = float(p.get("openSize", p.get("size", 0)) or 0)
            except (TypeError, ValueError):
                continue
            if size == 0:
                continue
            cid = str(p.get("contractId", ""))
            base = id_to_base.get(cid)
            if not base:
                continue
            out.append(
                {
                    "symbol": _pair_from_base(base),
                    "side": "long" if size > 0 else "short",
                    "qty": abs(size),
                    "entry_price": float(p.get("openValue", 0) or 0) / abs(size)
                    if size and p.get("openValue")
                    else float(p.get("avgEntryPrice", 0) or 0),
                    "liq_price": float(p.get("liquidatePrice", 0) or 0),
                    "leverage": float(p.get("leverage", 1) or 1),
                    "unrealized_pnl": float(p.get("unrealizePnl", 0) or 0),
                }
            )
        return out

    # ── setup ──────────────────────────────────────────────────────────

    def initialize_futures_symbol(self, pair: str) -> None:
        """No-op: EdgeX uses cross-collateral; leverage is set per-order/account."""

    # ── execution ──────────────────────────────────────────────────────

    async def _submit_limit_order(
        self, contract_id: str, size: str, price: str, side: Any
    ) -> Any:
        client = self._make_client()
        try:
            return await client.create_limit_order(
                contract_id=contract_id, size=size, price=price, side=side
            )
        finally:
            await self._close(client)

    def execute_trades(
        self,
        trades: list[dict[str, Any]],
        market: dict[str, dict[str, Any]],
        dry_run: bool,
    ) -> list[dict[str, Any]]:
        """Pure-futures execution via aggressive limit orders (CLOB, netted).

        Trade type mapping:
            open_long / close_short → BUY (cross asks, +slippage bound)
            open_short / close_long → SELL (cross bids, −slippage bound)
        """
        results: list[dict[str, Any]] = []
        for trade in trades:
            symbol = trade["symbol"]
            typ = trade["type"]
            mkt = market.get(symbol, {})
            ref_price = float(mkt.get("price", 0))
            record = dict(trade)
            record["dry_run"] = dry_run
            record["venue"] = self.venue_id
            record["ref_price"] = ref_price

            if dry_run:
                record["status"] = "simulated"
                record["order_id"] = None
                record["exec_qty"] = trade.get("amount_base", 0)
                record["exec_price"] = ref_price
                record["slippage"] = 0.0
                record["latency_ms"] = 0
                record["error"] = None
                results.append(record)
                continue

            if typ not in ("open_long", "open_short", "close_long", "close_short"):
                record["status"] = "failed"
                record["order_id"] = None
                record["error"] = f"Unknown trade type: {typ}"
                results.append(record)
                continue

            base = _base_from_pair(symbol)
            meta = self._funding.contract_meta_for_base(base)
            if meta is None:
                record["status"] = "failed"
                record["order_id"] = None
                record["error"] = f"EdgeX contract not found for {base}"
                results.append(record)
                continue

            from edgex_sdk import OrderSide

            is_buy = typ in ("open_long", "close_short")
            side = OrderSide.BUY if is_buy else OrderSide.SELL
            size = float(trade.get("amount_base", 0))
            # Aggressive limit: cross the book by 2% to behave like a taker.
            bound = ref_price * (1.02 if is_buy else 0.98)
            size_str = _fmt(size, _decimals_from_step(float(meta["step_size"])))
            price_str = _fmt(bound, _decimals_from_step(float(meta["tick_size"])))
            if size <= 0 or bound <= 0:
                record["status"] = "failed"
                record["order_id"] = None
                record["error"] = f"invalid size/price: {size_str}/{price_str}"
                results.append(record)
                continue

            import time as _time

            submit_ts = _time.time()
            try:
                resp = _run_async(
                    self._submit_limit_order(meta["contract_id"], size_str, price_str, side)
                )
                fill_ts = _time.time()
                data = resp.get("data", resp) if isinstance(resp, dict) else {}
                order_id = str(data.get("orderId", data.get("id", "")) or "")
                record["status"] = "filled"
                record["order_id"] = order_id
                record["exec_price"] = ref_price
                record["exec_qty"] = size
                record["exec_quote_usd"] = round(size * ref_price, 4)
                record["slippage"] = None
                record["latency_ms"] = round((fill_ts - submit_ts) * 1000)
                record["error"] = None
            except Exception as e:
                record["status"] = "failed"
                record["order_id"] = None
                record["error"] = str(e)
            results.append(record)
        return results
