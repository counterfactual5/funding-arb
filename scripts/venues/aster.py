#!/usr/bin/env python3
"""Aster perp-DEX venue adapter (Binance-fapi-compatible API).

Aster exposes a Binance USDT-M futures clone at https://fapi.asterdex.com
(same endpoints, HMAC-SHA256 signing, filter format). This adapter
implements the futures-only subset of the CexVenue interface needed by
pure_futures_executor / pure_futures_watcher; spot / margin / transfer
methods use the Protocol's default no-op implementations.

Credentials: ASTER_API_KEY / ASTER_API_SECRET (public market data works
without keys; signed endpoints are required for orders and balances).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import random
import time
import urllib.error
import urllib.parse
from typing import Any, Optional

from venues.http_util import http_get_json

BASE = "https://fapi.asterdex.com"

_rules_cache: dict[str, dict[str, Any]] = {}
_rules_loaded_at: float = 0.0


def _get_key() -> str:
    return os.environ.get("ASTER_API_KEY", "")


def _get_secret() -> str:
    return os.environ.get("ASTER_API_SECRET", "")


def _api_call(
    method: str, path: str, params: Optional[dict] = None, signed: bool = False
) -> Any:
    """Binance-style fapi call. GET retried, POST not (avoid duplicate orders)."""
    import json as _json
    import urllib.request

    base_params = dict(params or {})
    if signed:
        key, secret = _get_key(), _get_secret()
        if not (key and secret):
            raise RuntimeError(
                "Aster API credentials missing: set ASTER_API_KEY / ASTER_API_SECRET"
            )
    retries = 3 if method == "GET" else 1
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        if signed:
            params2 = dict(base_params)
            params2["timestamp"] = int(time.time() * 1000)
            query = urllib.parse.urlencode(params2)
            sig = hmac.new(
                _get_secret().encode(), query.encode(), hashlib.sha256
            ).hexdigest()
            url = f"{BASE}{path}?{query}&signature={sig}"
            headers = {"X-MBX-APIKEY": _get_key()}
        else:
            query = urllib.parse.urlencode(base_params) if base_params else ""
            url = f"{BASE}{path}" + (f"?{query}" if query else "")
            headers = {}
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return _json.loads(resp.read().decode())
        except Exception as e:
            last_err = e
            if method == "GET" and attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    raise last_err if last_err else RuntimeError("aster _api_call failed")


class AsterVenue:
    """Aster perp-DEX adapter — futures only (no spot/margin)."""

    venue_id: str = "aster"

    # ── market data ────────────────────────────────────────────────────

    def get_futures_ticker(self, pair: str) -> float:
        try:
            data = http_get_json(
                f"{BASE}/fapi/v1/ticker/price?symbol={pair.upper()}", timeout=15
            )
            return float(data.get("price", 0))
        except Exception:
            return 0.0

    def get_ticker(self, pair: str) -> float:
        """Aster is perps-only; spot ticker falls back to the perp price."""
        return self.get_futures_ticker(pair)

    def _ensure_exchange_info(self, cache_sec: int = 3600) -> None:
        global _rules_cache, _rules_loaded_at
        now = time.time()
        if _rules_cache and (now - _rules_loaded_at) < cache_sec:
            return
        info = http_get_json(f"{BASE}/fapi/v1/exchangeInfo", timeout=20)
        rules: dict[str, dict[str, Any]] = {}
        for s in info.get("symbols", []) if isinstance(info, dict) else []:
            s_pair = str(s.get("symbol", "")).upper()
            if not s_pair:
                continue
            min_base = 0.0
            min_usdt = 0.0
            qty_prec = int(s.get("quantityPrecision", 3))
            quote_prec = int(s.get("pricePrecision", 2))
            for f in s.get("filters", []):
                ftype = f.get("filterType")
                if ftype == "LOT_SIZE":
                    min_base = float(f.get("minQty", 0))
                elif ftype in ("MIN_NOTIONAL", "NOTIONAL"):
                    min_usdt = float(f.get("notional", f.get("minNotional", 0)) or 0)
            if min_base <= 0:
                min_base = 10 ** (-qty_prec)
            rules[s_pair] = {
                "symbol": s_pair,
                "min_trade_usdt": min_usdt,
                "min_trade_base": min_base,
                "quantity_precision": qty_prec,
                "quote_precision": quote_prec,
                "status": s.get("status", ""),
            }
        _rules_cache = rules
        _rules_loaded_at = now

    def fetch_futures_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        try:
            self._ensure_exchange_info(cache_sec)
        except Exception:
            return None
        rules = _rules_cache.get(pair.upper())
        return dict(rules) if rules else None

    def fetch_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        return self.fetch_futures_symbol_rules(pair, cache_sec)

    # ── account / positions ────────────────────────────────────────────

    def fetch_usdt_account_balances(self) -> dict[str, float]:
        """Return {'spot': 0, 'futures': <available USDT>} (perps-only venue)."""
        data = _api_call("GET", "/fapi/v2/balance", signed=True)
        futures_usdt = 0.0
        for row in data if isinstance(data, list) else []:
            if str(row.get("asset", "")).upper() == "USDT":
                futures_usdt = float(row.get("availableBalance", 0) or 0)
                break
        return {"spot": 0.0, "futures": futures_usdt}

    def fetch_futures_positions(self, quote: str = "USDT") -> list[dict[str, Any]]:
        pos_data = _api_call("GET", "/fapi/v2/positionRisk", signed=True)
        out: list[dict[str, Any]] = []
        for pos in pos_data if isinstance(pos_data, list) else []:
            amt = float(pos.get("positionAmt", "0") or 0)
            if abs(amt) <= 1e-12:
                continue
            out.append(
                {
                    "symbol": str(pos.get("symbol", "")).upper(),
                    "side": "long" if amt > 0 else "short",
                    "qty": abs(amt),
                    "entry_price": float(pos.get("entryPrice", 0) or 0),
                    "liq_price": float(pos.get("liquidationPrice", 0) or 0),
                    "leverage": float(pos.get("leverage", 1) or 1),
                    "unrealized_pnl": float(pos.get("unRealizedProfit", 0) or 0),
                }
            )
        return out

    # ── setup ──────────────────────────────────────────────────────────

    def initialize_futures_symbol(self, pair: str) -> None:
        """Set 1× isolated margin and one-way mode (idempotent, non-fatal)."""
        for path, params in (
            ("/fapi/v1/marginType", {"symbol": pair, "marginType": "ISOLATED"}),
            ("/fapi/v1/leverage", {"symbol": pair, "leverage": 1}),
            ("/fapi/v1/positionSide/dual", {"dualSidePosition": "false"}),
        ):
            try:
                _api_call("POST", path, params, signed=True)
            except Exception:
                pass

    # ── execution ──────────────────────────────────────────────────────

    def place_futures_order(
        self,
        pair: str,
        side: str,
        amount_base: float,
        quantity_precision: int = 3,
        ref_price: float = 0.0,
        reduce_only: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        client_oid = f"afut{int(time.time())}{random.randint(0, 9999)}"
        qty = f"{amount_base:.{quantity_precision}f}".rstrip("0").rstrip(".")
        if "." not in qty:
            qty = f"{amount_base:.{quantity_precision}f}"
        submit_ts = time.time()
        params = {
            "symbol": pair,
            "side": side,
            "type": "MARKET",
            "quantity": qty,
            "newClientOrderId": client_oid,
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        try:
            result = _api_call("POST", "/fapi/v1/order", params, signed=True)
            fill_ts = time.time()
            exec_qty = float(result.get("executedQty", 0))
            exec_price = float(result.get("avgPrice", 0)) or ref_price
            slippage = (
                round((exec_price - ref_price) / ref_price, 6)
                if ref_price and exec_price
                else None
            )
            return True, {
                "order_id": str(result.get("orderId", "?")),
                "exec_price": exec_price,
                "exec_qty": exec_qty,
                "exec_quote_usd": exec_price * exec_qty,
                "ref_price": ref_price,
                "slippage": slippage,
                "submit_ts": round(submit_ts, 3),
                "fill_ts": round(fill_ts, 3),
                "latency_ms": round((fill_ts - submit_ts) * 1000),
                "order_status": result.get("status", ""),
            }
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode()
            except Exception:
                body = ""
            return False, {"error": f"HTTP {e.code}: {body[:200]}"}
        except Exception as e:
            return False, {"error": str(e)}

    def execute_trades(
        self,
        trades: list[dict[str, Any]],
        market: dict[str, dict[str, Any]],
        dry_run: bool,
    ) -> list[dict[str, Any]]:
        """Pure-futures trade execution.

        Trade type mapping (one-way mode):
            open_long / close_short → BUY  (reduce_only on close)
            open_short / close_long → SELL (reduce_only on close)
        """
        results: list[dict[str, Any]] = []
        for trade in trades:
            symbol = trade["symbol"]
            typ = trade["type"]
            mkt = market.get(symbol, {})
            pair = str(mkt.get("pair") or f"{symbol.upper()}USDT")
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

            side = "BUY" if typ in ("open_long", "close_short") else "SELL"
            reduce_only = typ in ("close_long", "close_short")
            f_rules = self.fetch_futures_symbol_rules(pair)
            f_prec = int(f_rules.get("quantity_precision", 3)) if f_rules else 3
            ok, detail = self.place_futures_order(
                pair,
                side,
                float(trade.get("amount_base", 0)),
                int(trade.get("quantity_precision") or f_prec),
                ref_price=ref_price,
                reduce_only=reduce_only,
            )
            record["status"] = "filled" if ok else "failed"
            if ok:
                record.update(
                    {
                        "order_id": detail.get("order_id"),
                        "exec_price": detail.get("exec_price"),
                        "exec_qty": detail.get("exec_qty"),
                        "exec_quote_usd": detail.get("exec_quote_usd"),
                        "slippage": detail.get("slippage"),
                        "latency_ms": detail.get("latency_ms"),
                        "submit_ts": detail.get("submit_ts"),
                        "fill_ts": detail.get("fill_ts"),
                        "order_status": detail.get("order_status"),
                        "error": None,
                    }
                )
            else:
                record["order_id"] = None
                record["error"] = detail.get("error", str(detail))
            results.append(record)
        return results
