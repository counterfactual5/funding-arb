#!/usr/bin/env python3
"""Lighter perp-DEX venue adapter (zk order book, lighter-sdk).

Implements the futures-only subset of the CexVenue interface needed by
pure_futures_executor / pure_futures_watcher. Read paths (prices, rules,
market_id mapping) use the public REST API via LighterFundingProvider and
work without credentials; write paths (orders, balances, positions) use
the async lighter-sdk SignerClient wrapped with asyncio.run().

Credentials (zk model, unlike CEX HMAC keys):
    LIGHTER_API_PRIVATE_KEY   — API key private key (signs transactions)
    LIGHTER_ACCOUNT_INDEX     — numeric account index, or
    LIGHTER_L1_ADDRESS        — L1 wallet address (auto-resolves the index)
    LIGHTER_API_KEY_INDEX     — API key slot, default 2 (0/1 reserved for UI)

Notes:
    - Lighter quotes margin in USDC (treated 1:1 with USDT here).
    - Order sizes/prices are integers scaled by supported_size_decimals /
      supported_price_decimals from orderBookDetails.
    - Symbols are bases ("BTC", "1000PEPE"); CEX pairs map as BTCUSDT ↔ BTC.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any

from venues.http_util import http_get_json
from venues.lighter_funding import LighterFundingProvider

_BASE_URL = "https://mainnet.zklighter.elliot.ai"

# Short-TTL last-trade-price cache (orderBookDetails covers all markets at once)
_price_cache: tuple[float, dict[str, float]] | None = None
_PRICE_TTL_SEC = 5.0


def _base_from_pair(pair: str) -> str:
    s = pair.upper()
    return s[:-4] if s.endswith("USDT") else s


def _pair_from_base(base: str) -> str:
    return f"{base.upper()}USDT"


def _account_index() -> int:
    """Resolve the Lighter account index from env (numeric or via L1 address)."""
    raw = os.environ.get("LIGHTER_ACCOUNT_INDEX", "").strip()
    if raw:
        return int(raw)
    l1 = os.environ.get("LIGHTER_L1_ADDRESS", "").strip()
    if not l1:
        raise RuntimeError(
            "Lighter credentials missing: set LIGHTER_ACCOUNT_INDEX or LIGHTER_L1_ADDRESS"
        )
    data = http_get_json(
        f"{_BASE_URL}/api/v1/accountsByL1Address?l1_address={l1}", timeout=20
    )
    accounts = data.get("sub_accounts") or data.get("accounts") or []
    if not accounts:
        raise RuntimeError(f"Lighter account not found for L1 address {l1}")
    return int(accounts[0].get("index", accounts[0].get("account_index")))


def _run_async(coro: Any) -> Any:
    """Run an SDK coroutine from sync executor code (no running loop expected)."""
    return asyncio.run(coro)


class LighterVenue:
    """Lighter perp-DEX adapter — pure futures only (USDC margin)."""

    venue_id: str = "lighter"

    def __init__(self) -> None:
        self._funding = LighterFundingProvider()

    # ── market data ────────────────────────────────────────────────────

    def _fresh_prices(self) -> dict[str, float]:
        """base → last_trade_price for all perp markets (5s cache)."""
        global _price_cache
        now = time.time()
        if _price_cache and now - _price_cache[0] < _PRICE_TTL_SEC:
            return _price_cache[1]
        payload = http_get_json(f"{_BASE_URL}/api/v1/orderBookDetails", timeout=20)
        out: dict[str, float] = {}
        rows = payload.get("order_book_details", []) if isinstance(payload, dict) else []
        for row in rows:
            sym = str(row.get("symbol", "")).upper()
            px = float(row.get("last_trade_price", 0) or 0)
            if sym and px > 0:
                out[sym] = px
        _price_cache = (now, out)
        return out

    def get_futures_ticker(self, pair: str) -> float:
        try:
            return self._fresh_prices().get(_base_from_pair(pair), 0.0)
        except Exception:
            return 0.0

    def get_ticker(self, pair: str) -> float:
        """Lighter is perps-only; spot ticker falls back to the perp price."""
        return self.get_futures_ticker(pair)

    def fetch_futures_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        base = _base_from_pair(pair)
        try:
            meta = self._funding.market_meta_for_base(base)
        except Exception:
            return None
        if meta is None:
            return None
        return {
            "symbol": pair,
            "quantity_precision": int(meta["size_decimals"]),
            "quote_precision": int(meta["price_decimals"]),
            "min_trade_base": float(meta["min_base_amount"]),
            "min_trade_usdt": float(meta["min_quote_amount"]),
        }

    def fetch_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        return self.fetch_futures_symbol_rules(pair, cache_sec)

    # ── account / positions (lighter-sdk, async wrapped) ───────────────

    async def _fetch_account(self) -> Any:
        import lighter

        api_client = lighter.ApiClient(
            configuration=lighter.Configuration(host=_BASE_URL)
        )
        try:
            resp = await lighter.AccountApi(api_client).account(
                by="index", value=str(_account_index())
            )
        finally:
            await api_client.close()
        accounts = getattr(resp, "accounts", None) or []
        if not accounts:
            raise RuntimeError("Lighter account query returned no accounts")
        return accounts[0]

    def fetch_usdt_account_balances(self) -> dict[str, float]:
        """Return {'spot': 0, 'futures': <available USDC>} (USDC ≈ USDT 1:1)."""
        try:
            acct = _run_async(self._fetch_account())
            avail = float(getattr(acct, "available_balance", 0) or 0)
            return {"spot": 0.0, "futures": avail}
        except Exception:
            return {"spot": 0.0, "futures": 0.0}

    def fetch_futures_positions(self, quote: str = "USDT") -> list[dict[str, Any]]:
        try:
            acct = _run_async(self._fetch_account())
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for p in getattr(acct, "positions", None) or []:
            try:
                size = float(getattr(p, "position", 0) or 0)
            except (TypeError, ValueError):
                continue
            if size == 0:
                continue
            sign = int(getattr(p, "sign", 1) or 1)
            base = str(getattr(p, "symbol", "")).upper()
            if not base:
                continue
            out.append(
                {
                    "symbol": _pair_from_base(base),
                    "side": "long" if sign >= 0 else "short",
                    "qty": abs(size),
                    "entry_price": float(getattr(p, "avg_entry_price", 0) or 0),
                    "liq_price": float(getattr(p, "liquidation_price", 0) or 0),
                    "leverage": 1.0,
                    "unrealized_pnl": float(getattr(p, "unrealized_pnl", 0) or 0),
                }
            )
        return out

    # ── setup ──────────────────────────────────────────────────────────

    def initialize_futures_symbol(self, pair: str) -> None:
        """No-op: Lighter uses cross margin by default, no per-symbol setup."""

    # ── execution ──────────────────────────────────────────────────────

    def _make_signer(self) -> Any:
        import lighter

        private_key = os.environ.get("LIGHTER_API_PRIVATE_KEY", "").strip()
        if not private_key:
            raise RuntimeError(
                "Lighter credentials missing: set LIGHTER_API_PRIVATE_KEY"
            )
        key_index = int(os.environ.get("LIGHTER_API_KEY_INDEX", "2"))
        return lighter.SignerClient(
            url=_BASE_URL,
            account_index=_account_index(),
            api_private_keys={key_index: private_key},
        )

    async def _submit_market_order(
        self,
        market_id: int,
        base_scaled: int,
        price_scaled: int,
        is_ask: bool,
        reduce_only: bool,
    ) -> tuple[Any, Any, str | None]:
        client = self._make_signer()
        try:
            tx, resp, err = await client.create_market_order(
                market_index=market_id,
                client_order_index=int(time.time() * 1000) % 2**31
                + random.randint(0, 999),
                base_amount=base_scaled,
                avg_execution_price=price_scaled,
                is_ask=is_ask,
                reduce_only=reduce_only,
            )
            return tx, resp, err
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass

    def execute_trades(
        self,
        trades: list[dict[str, Any]],
        market: dict[str, dict[str, Any]],
        dry_run: bool,
    ) -> list[dict[str, Any]]:
        """Pure-futures trade execution via SignerClient market orders.

        Trade type mapping:
            open_long / close_short → bid (is_ask=False), reduce_only on close
            open_short / close_long → ask (is_ask=True), reduce_only on close
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
            meta = self._funding.market_meta_for_base(base)
            if meta is None:
                record["status"] = "failed"
                record["order_id"] = None
                record["error"] = f"Lighter market not found for {base}"
                results.append(record)
                continue

            is_ask = typ in ("open_short", "close_long")
            reduce_only = typ in ("close_long", "close_short")
            size = float(trade.get("amount_base", 0))
            base_scaled = int(round(size * 10 ** int(meta["size_decimals"])))
            # avg_execution_price caps market-order slippage: bids accept up to
            # +2% above ref, asks down to −2% below.
            bound = ref_price * (0.98 if is_ask else 1.02)
            price_scaled = int(round(bound * 10 ** int(meta["price_decimals"])))
            if base_scaled <= 0 or price_scaled <= 0:
                record["status"] = "failed"
                record["order_id"] = None
                record["error"] = f"invalid scaled qty/price: {base_scaled}/{price_scaled}"
                results.append(record)
                continue

            submit_ts = time.time()
            try:
                tx, resp, err = _run_async(
                    self._submit_market_order(
                        int(meta["market_id"]),
                        base_scaled,
                        price_scaled,
                        is_ask,
                        reduce_only,
                    )
                )
                fill_ts = time.time()
                if err:
                    record["status"] = "failed"
                    record["order_id"] = None
                    record["error"] = str(err)
                else:
                    record["status"] = "filled"
                    record["order_id"] = str(getattr(resp, "tx_hash", "") or "")
                    # Market tx is accepted by the sequencer; fill price is not
                    # returned synchronously — record the ref price.
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
