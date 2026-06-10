#!/usr/bin/env python3
"""Hyperliquid venue adapter for funding-arb pure futures execution.

Implements the subset of the CexVenue interface needed by
pure_futures_executor.py.  Spot / margin / transfer methods use the
Protocol's default no-op implementations.

Read operations (prices, meta, funding) use direct HTTP POST to avoid
the hyperliquid-python-sdk spot_meta IndexError bug on mainnet.
Write operations (orders, leverage) reuse the hyperliquid skill's
functions which handle trade-signer / local-key signing.
"""
from __future__ import annotations

import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Bootstrap: import from the hyperliquid skill (write-path only)
# ---------------------------------------------------------------------------
_HL_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "hyperliquid" / "scripts"
)
if str(_HL_DIR) not in sys.path:
    sys.path.insert(0, str(_HL_DIR))

# Write-path imports (these use SDK exchange client for signing)
from hyperliquid_order import (  # noqa: E402
    get_account_value as hl_get_account_value,
    get_positions as hl_get_positions,
    place_market_order as hl_place_market_order,
    set_leverage as hl_set_leverage,
)
from common import get_base_url as _hl_get_base_url  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://api.hyperliquid.xyz"

# Trade type → buy/sell direction mapping for Hyperliquid
_DIRECTION_MAP: dict[str, bool] = {
    "open_long": True,    # buy  = go long
    "close_long": False,  # sell = close long
    "open_short": False,  # sell = go short
    "close_short": True,  # buy  = close short
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coin_from_pair(pair: str) -> str:
    """CEX pair → HL coin name.  'BTCUSDT' → 'BTC'."""
    s = pair.upper()
    return s[:-4] if s.endswith("USDT") else s


def _pair_from_coin(coin: str) -> str:
    """HL coin name → CEX pair.  'BTC' → 'BTCUSDT'."""
    return f"{coin.upper()}USDT"


def _info_post(body: dict[str, Any]) -> Any:
    """Direct HTTP POST to Hyperliquid Info endpoint (bypasses SDK bugs)."""
    r = requests.post(f"{_BASE_URL}/info", json=body, timeout=20)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Venue implementation
# ---------------------------------------------------------------------------

class HyperliquidVenue:
    """Hyperliquid perp-DEX adapter — pure futures only (no spot).

    Read path: direct HTTP (avoids SDK spot_meta IndexError on mainnet).
    Write path: reuses hyperliquid skill functions (trade-signer support).
    """

    venue_id: str = "hyperliquid"

    def __init__(self) -> None:
        self._rules_cache: dict[str, dict[str, Any]] = {}
        self._meta_cache: dict[str, Any] | None = None
        self._leverage_set: set[str] = set()

    # ── meta cache (shared by ticker + rules) ───────────────────────

    def _get_meta(self) -> dict[str, Any]:
        """Cached universe metadata from metaAndAssetCtxs."""
        if self._meta_cache is not None:
            return self._meta_cache
        try:
            data = _info_post({"type": "metaAndAssetCtxs"})
            if isinstance(data, list) and len(data) >= 2:
                universe = data[0].get("universe", [])
                # Build name → {szDecimals, ...} map
                meta = {}
                for entry in universe:
                    name = str(entry.get("name", "")).upper()
                    if name:
                        meta[name] = entry
                self._meta_cache = meta
                return meta
        except Exception:
            pass
        return {}

    # ── market data ────────────────────────────────────────────────────

    def get_futures_ticker(self, pair: str) -> float:
        """Return mid price for a coin via all_mids HTTP endpoint."""
        coin = _coin_from_pair(pair)
        try:
            mids = _info_post({"type": "allMids"})
            if isinstance(mids, dict):
                price_str = mids.get(coin, mids.get(coin.upper()))
                if price_str is not None:
                    return float(price_str)
        except Exception:
            pass
        return 0.0

    def get_ticker(self, pair: str) -> float:
        """Same as get_futures_ticker (HL is perps-only)."""
        return self.get_futures_ticker(pair)

    def fetch_futures_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        """Return quantity precision and min-trade info from HL meta."""
        coin = _coin_from_pair(pair).upper()
        if coin in self._rules_cache:
            return self._rules_cache[coin]
        meta = self._get_meta()
        entry = meta.get(coin)
        sz_dec = int(entry.get("szDecimals", 4)) if entry else 5
        rules = {
            "symbol": pair,
            "quantity_precision": sz_dec,
            "quote_precision": 2,
            "min_trade_usdt": 10.0,  # HL minimum ~$10
            "min_trade_base": 0.0,
        }
        self._rules_cache[coin] = rules
        return rules

    def fetch_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        return self.fetch_futures_symbol_rules(pair, cache_sec)

    # ── account / positions ────────────────────────────────────────────

    def fetch_usdt_account_balances(self) -> dict[str, float]:
        """Return {'spot': 0, 'futures': <USDC_account_value>}.

        HL uses USDC margin, but the numeric value is compatible with
        margin checks (USDC ≈ USDT 1:1).
        """
        try:
            acct = hl_get_account_value()
            val = float(acct.get("totalAccountValue", 0) or 0)
            return {"spot": 0.0, "futures": val}
        except Exception:
            return {"spot": 0.0, "futures": 0.0}

    def fetch_futures_positions(self, quote: str = "USDT") -> list[dict[str, Any]]:
        """Return standardised position list.

        Output: [{symbol, side, qty, entry_price, liq_price, leverage, unrealized_pnl}]
        """
        try:
            raw_positions = hl_get_positions()
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for p in raw_positions:
            coin = str(p.get("coin", ""))
            if not coin:
                continue
            size_str = str(p.get("size", "0"))
            try:
                size = float(Decimal(size_str))
            except Exception:
                continue
            if size == 0:
                continue
            side = "long" if size > 0 else "short"
            out.append({
                "symbol": _pair_from_coin(coin),
                "side": side,
                "qty": abs(size),
                "entry_price": float(p.get("entryPrice", 0) or 0),
                "liq_price": float(p.get("liquidationPrice", 0) or 0),
                "leverage": float(p.get("leverage", 1) or 1),
                "unrealized_pnl": float(p.get("unrealizedPnl", 0) or 0),
            })
        return out

    # ── setup ──────────────────────────────────────────────────────────

    def initialize_futures_symbol(self, pair: str) -> None:
        """Set 1× cross margin leverage for a coin (idempotent)."""
        coin = _coin_from_pair(pair)
        if coin in self._leverage_set:
            return
        try:
            hl_set_leverage(coin=coin, leverage=1, is_cross=True)
            self._leverage_set.add(coin)
        except Exception:
            pass  # Non-fatal: executor catches failures downstream

    # ── execution ──────────────────────────────────────────────────────

    def execute_trades(
        self,
        trades: list[dict[str, Any]],
        market: dict[str, dict[str, Any]],
        dry_run: bool,
    ) -> list[dict[str, Any]]:
        """Execute pure-futures trades via Hyperliquid.

        Trade type mapping:
            open_long   → place_market_order(is_buy=True)
            open_short  → place_market_order(is_buy=False)
            close_long  → place_market_order(is_buy=False)
            close_short → place_market_order(is_buy=True)
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

            # Live execution
            is_buy = _DIRECTION_MAP.get(typ)
            if is_buy is None:
                record["status"] = "failed"
                record["error"] = f"Unknown trade type: {typ}"
                record["order_id"] = None
                results.append(record)
                continue

            coin = _coin_from_pair(symbol)
            size = float(trade.get("amount_base", 0))
            submit_ts = time.time()

            try:
                res = hl_place_market_order(
                    coin=coin,
                    is_buy=is_buy,
                    size=size,
                    slippage=0.01,
                )
                fill_ts = time.time()
                latency_ms = round((fill_ts - submit_ts) * 1000)
                status_str = str(res.get("status", ""))
                if status_str == "ok":
                    fill_price = float(res.get("price", ref_price))
                    slippage = (
                        round((fill_price - ref_price) / ref_price, 6)
                        if ref_price and fill_price
                        else 0.0
                    )
                    record["status"] = "filled"
                    record["order_id"] = None  # HL doesn't return simple oid
                    record["exec_price"] = fill_price
                    record["exec_qty"] = size
                    record["exec_quote_usd"] = round(size * fill_price, 4)
                    record["slippage"] = slippage
                    record["latency_ms"] = latency_ms
                    record["error"] = None
                else:
                    record["status"] = "failed"
                    record["order_id"] = None
                    record["error"] = f"HL order failed: {status_str}"
            except Exception as e:
                record["status"] = "failed"
                record["order_id"] = None
                record["error"] = str(e)

            results.append(record)
        return results
