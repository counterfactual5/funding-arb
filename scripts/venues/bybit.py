#!/usr/bin/env python3
"""Bybit spot + USDT linear perpetual venue adapter."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from venues.base import make_pair
from venues.http_util import http_get_json, parse_kline_ohlcv, rules_for_price
from core.config import resolve_timeframes

BASE = "https://api.bybit.com"
CONFIG_PATH = os.path.expanduser("~/.funding-arb/funding-arb.json")
_symbol_rules_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_futures_rules_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_spot_ticker_loaded_at: float = 0.0
_spot_ticker_prices: dict[str, float] = {}
_initialized_symbols: set[str] = set()
_env_loaded = False

KLINE_INTERVALS = {
    "1day": "D", "1d": "D", "4h": "240", "1week": "W", "1w": "W",
}


def _ensure_env() -> None:
    global _env_loaded
    if _env_loaded:
        return
    if os.environ.get("BYBIT_API_KEY"):
        _env_loaded = True
        return
    try:
        with open(CONFIG_PATH) as f:
            for k, v in json.load(f).get("env", {}).items():
                if v and k not in os.environ:
                    os.environ[k] = str(v)
    except (OSError, json.JSONDecodeError):
        pass
    _env_loaded = True


def _get_key() -> str:
    _ensure_env()
    return os.environ.get("BYBIT_API_KEY") or ""


def _get_secret() -> str:
    _ensure_env()
    return os.environ.get("BYBIT_SECRET_KEY") or ""


def _sign(payload: str) -> str:
    return hmac.new(_get_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()


def _api_call(
    method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None
) -> dict:
    key, secret = _get_key(), _get_secret()
    if not (key and secret):
        raise RuntimeError(
            "Bybit API 凭证缺失：请设置 BYBIT_API_KEY / BYBIT_SECRET_KEY，"
            "或在 ~/.funding-arb/funding-arb.json 的 env 中配置。"
    )

    recv_window = "5000"
    ts = str(int(time.time() * 1000))

    if method == "GET" and params:
        query = urllib.parse.urlencode(params)
    else:
        query = ""

    body_str = json.dumps(body) if body else ""
    full_path = path + ("?" + query if query else "")

    prehash = ts + key + recv_window + (query if method == "GET" else body_str)
    sig = _sign(prehash)

    headers = {
        "X-BAPI-API-KEY": key,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json",
    }

    retries = 3 if method == "GET" else 1
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        url = BASE + full_path
        req = urllib.request.Request(url, headers=headers, method=method)
        if body_str:
            req.data = body_str.encode()
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            if data.get("retCode") != 0:
                raise RuntimeError(f"Bybit API error: {data.get('retMsg', data.get('retCode'))}")
            return data
        except Exception as e:
            last_err = e
            if method == "GET" and attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    raise last_err if last_err else RuntimeError("bybit _api_call failed")


class BybitSpotVenue:
    venue_id = "bybit"

    def get_ticker(self, pair: str) -> float:
        url = f"{BASE}/v5/market/tickers?category=spot&symbol={pair}"
        try:
            data = http_get_json(url)
            return float(data.get("result", {}).get("list", [{}])[0].get("lastPrice", 0))
        except Exception:
            return 0.0

    def get_futures_ticker(self, pair: str) -> float:
        """USDT 永续合约最新价。pair 格式如 BTCUSDT。"""
        url = f"{BASE}/v5/market/tickers?category=linear&symbol={pair}"
        try:
            data = http_get_json(url)
            return float(data.get("result", {}).get("list", [{}])[0].get("lastPrice", 0))
        except Exception:
            return 0.0

    def get_all_spot_tickers(self, cache_sec: int = 5) -> dict[str, float]:
        """Bulk spot last prices {PAIR: price}. Cached briefly for screener loops."""
        global _spot_ticker_loaded_at, _spot_ticker_prices
        now = time.time()
        if _spot_ticker_prices and (now - _spot_ticker_loaded_at) < cache_sec:
            return dict(_spot_ticker_prices)
        try:
            data = http_get_json(f"{BASE}/v5/market/tickers?category=spot")
            _spot_ticker_prices = {
                str(r.get("symbol", "")).upper(): float(r.get("lastPrice", 0) or 0)
                for r in data.get("result", {}).get("list", [])
                if r.get("symbol")
            }
            _spot_ticker_loaded_at = now
        except Exception:
            pass
        return dict(_spot_ticker_prices)

    def get_klines(self, pair: str, granularity: str = "1day", limit: int = 200) -> list:
        interval = KLINE_INTERVALS.get(granularity, "D")
        limit = min(limit, 1000)
        url = f"{BASE}/v5/market/kline?category=spot&symbol={pair}&interval={interval}&limit={limit}"
        try:
            data = http_get_json(url)
            return list(reversed(data.get("result", {}).get("list", [])))
        except Exception:
            return []

    def fetch_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> Optional[dict[str, Any]]:
        now = time.time()
        cached = _symbol_rules_cache.get(pair)
        if cached and (now - cached[0]) < cache_sec:
            return dict(cached[1])
        url = f"{BASE}/v5/market/instruments-info?category=spot&symbol={pair}"
        try:
            payload = http_get_json(url)
        except Exception:
            return dict(cached[1]) if cached else None
        rows = payload.get("result", {}).get("list", [])
        if not rows:
            return dict(cached[1]) if cached else None
        info = rows[0]
        lot_filter = {}
        min_notional = {}
        for f in info.get("lotSizeFilter", []):
            lot_filter = f if isinstance(f, dict) else {}
            break
        for f in info.get("minNotionalFilter", []):
            min_notional = f if isinstance(f, dict) else {}
            break
        if not lot_filter:
            lot_filter = info.get("lotSizeFilter") or {}
        if not min_notional:
            min_notional = info.get("minNotionalFilter") or {}

        base_prec = len(str(lot_filter.get("basePrecision", "0.000001")).rstrip("0").split(".")[-1]) if "." in str(lot_filter.get("basePrecision", "0.000001")) else 6
        min_base = float(lot_filter.get("minOrderQty", 0))
        min_usdt = float(min_notional.get("minNotionalValue", 0))
        rules = {
            "symbol": pair,
            "min_trade_usdt": min_usdt,
            "min_trade_base": min_base,
            "quantity_precision": base_prec,
            "quote_precision": 2,
            "status": "Trading" if info.get("status") == "Trading" else "",
        }
        _symbol_rules_cache[pair] = (now, rules)
        return dict(rules)

    def fetch_futures_symbol_rules(self, pair: str, cache_sec: int = 3600) -> dict[str, Any] | None:
        now = time.time()
        cached = _futures_rules_cache.get(pair)
        if cached and (now - cached[0]) < cache_sec:
            return dict(cached[1])
        url = f"{BASE}/v5/market/instruments-info?category=linear&symbol={pair}"
        try:
            payload = http_get_json(url)
        except Exception:
            return dict(cached[1]) if cached else None
        rows = payload.get("result", {}).get("list", [])
        if not rows:
            return dict(cached[1]) if cached else None
        info = rows[0]
        lot_filter = info.get("lotSizeFilter") or {}
        qty_step = str(lot_filter.get("qtyStep", "0.001"))
        qty_prec = len(qty_step.rstrip("0").split(".")[-1]) if "." in qty_step else 3
        min_base = float(lot_filter.get("minOrderQty", 0))
        rules = {
            "symbol": pair,
            "min_trade_usdt": 0,
            "min_trade_base": min_base,
            "quantity_precision": qty_prec,
            "quote_precision": 2,
            "status": "Trading" if info.get("status") == "Trading" else "",
        }
        _futures_rules_cache[pair] = (now, rules)
        return dict(rules)

    def transfer_asset(self, asset: str, amount: float, from_account: str, to_account: str) -> bool:
        transfer_idx = None
        if from_account == "spot" and to_account == "futures":
            transfer_idx = "UNIFIED"
        elif from_account == "futures" and to_account == "spot":
            transfer_idx = "UNIFIED"
        else:
            return False
        try:
            _api_call("POST", "/v5/asset/transfer/inter-transfer", body={
                "transferAccountType": transfer_idx,
                "coin": asset.upper(),
                "amount": f"{amount:.8f}".rstrip("0").rstrip("."),
            })
            return True
        except Exception:
            return False

    def fetch_asset_market(
        self, asset: str, quote: str = "USDT", cfg: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        pair = make_pair(asset, quote)
        price = self.get_ticker(pair)
        rules = self.fetch_symbol_rules(pair)
        if rules is None:
            return {"symbol": asset, "price": price, "rules_error": True, "venue": self.venue_id}
        limits = rules_for_price(rules, price)
        cfg = cfg or {}
        tf = resolve_timeframes(cfg)
        klines_1d = [parse_kline_ohlcv(k) for k in self.get_klines(pair, tf["slow"]["interval"], tf["slow"]["limit"]) if k]
        klines_4h = [parse_kline_ohlcv(k) for k in self.get_klines(pair, tf["mid"]["interval"], tf["mid"]["limit"]) if k]
        klines_1w = [parse_kline_ohlcv(k) for k in self.get_klines(pair, tf["macro"]["interval"], tf["macro"]["limit"]) if k]
        return {
            "symbol": asset, "pair": pair, "price": price,
            "rules_error": False, "venue": self.venue_id,
            "symbol_rules": rules, **limits,
            "klines_1d": klines_1d, "klines_4h": klines_4h, "klines_1w": klines_1w,
        }

    def fetch_balances(self, coins: list[str]) -> dict[str, float]:
        balances: dict[str, float] = {c: 0.0 for c in coins}
        data = _api_call("GET", "/v5/account/wallet-balance", params={"accountType": "UNIFIED"})
        for acct in data.get("result", {}).get("list", []):
            for coin in acct.get("coin", []):
                c = str(coin.get("coin", "")).upper()
                if c in balances:
                    raw = coin.get("availableToWithdraw") or coin.get("walletBalance") or "0"
                    balances[c] = float(raw if raw else 0)
        return balances

    def fetch_live_state(self, assets: list[str]) -> dict[str, Any]:
        balances = self.fetch_balances(assets)
        positions: dict[str, dict[str, Any]] = {}
        try:
            pos_data = _api_call("GET", "/v5/position/list", params={"category": "linear", "settleCoin": "USDT"})
            for pos in pos_data.get("result", {}).get("list", []):
                amt = float(pos.get("size", 0) or 0)
                if amt < 1e-9:
                    continue
                sym_raw = str(pos.get("symbol", "")).upper()
                base = sym_raw.replace("USDT", "") if sym_raw.endswith("USDT") else sym_raw
                side_val = str(pos.get("side", "")).lower()
                positions[base] = {
                    "amount": amt,
                    "side": "long" if side_val == "buy" else "short",
                    "entry_price": float(pos.get("avgPrice", 0) or 0),
                    "unrealized_pnl": float(pos.get("unrealisedPnl", 0) or 0),
                    "leverage": float(pos.get("leverage", 1) or 1),
                }
        except Exception:
            pass
        return {"balances": balances, "futures_positions": positions}

    def initialize_futures_symbol(self, pair: str) -> None:
        if pair in _initialized_symbols:
            return
        try:
            _api_call("POST", "/v5/position/switch-mode", body={"coin": "USDT", "mode": 0})
        except Exception:
            pass
        try:
            _api_call("POST", "/v5/account/set-leverage", params={
                "category": "linear", "symbol": pair, "buyLeverage": "1", "sellLeverage": "1",
            })
        except Exception:
            pass
        try:
            _api_call("POST", "/v5/account/set-margin-mode", params={
                "category": "linear", "symbol": pair, "tradeMode": 1,
            })
        except Exception:
            pass
        _initialized_symbols.add(pair)

    def fetch_borrow_rates(self, coins: list[str]) -> dict[str, float]:
        """Fetch annualized borrow rates from Bybit spot margin. Returns {coin: rate_decimal}."""
        rates: dict[str, float] = {c: 0.0 for c in coins}
        for coin in coins:
            try:
                data = _api_call(
                    "GET",
                    "/v5/spot-margin-trade/data",
                    params={"vipLevel": "No VIP", "currency": coin.upper()},
                )
                for vip_group in data.get("result", {}).get("vipCoinList", []):
                    for item in vip_group.get("list", []):
                        if str(item.get("currency", "")).upper() != coin.upper():
                            continue
                        if not item.get("borrowable"):
                            continue
                        hourly = float(item.get("hourlyBorrowRate", 0) or 0)
                        if hourly > 0:
                            rates[coin.upper()] = hourly * 24 * 365
            except Exception:
                pass
        return rates

    # ── UTA spot margin（Reverse C&C：借币卖出 / 买回还币） ────────────────────

    def supports_reverse_arbitrage(self) -> bool:
        """UTA spot margin 能力：无密钥假定可用；有密钥探测开关，未开则尝试开启。"""
        if not (_get_key() and _get_secret()):
            return True
        try:
            data = _api_call("GET", "/v5/spot-margin-trade/state")
            mode = str((data.get("result") or {}).get("spotMarginMode", "0"))
            if mode == "1":
                return True
            _api_call("POST", "/v5/spot-margin-trade/switch-mode", body={"spotMarginMode": "1"})
            return True
        except Exception:
            return False

    def fetch_margin_debt(self, assets: list[str]) -> dict[str, float]:
        """UTA 各币种负债（borrowAmount + accruedInterest），单位为币本位数量。"""
        debt: dict[str, float] = {a.upper(): 0.0 for a in assets}
        try:
            data = _api_call("GET", "/v5/account/wallet-balance", params={"accountType": "UNIFIED"})
            for acct in data.get("result", {}).get("list", []):
                for coin in acct.get("coin", []):
                    c = str(coin.get("coin", "")).upper()
                    if c in debt:
                        debt[c] = float(coin.get("borrowAmount", 0) or 0) + float(
                            coin.get("accruedInterest", 0) or 0
                        )
        except Exception as e:
            print(f"bybit fetch_margin_debt failed: {e}", file=sys.stderr)
        return debt

    def margin_borrow(self, asset: str, amount: float) -> bool:
        """UTA 借币隐式发生于 isLeverage=1 下单，无独立借币接口。"""
        return False

    def margin_repay(self, asset: str, amount: float) -> bool:
        """UTA 手动还币：优先官方 /v5/account/repay，失败再试 quick-repayment。"""
        coin = asset.upper()
        amt = f"{amount:.8f}".rstrip("0").rstrip(".")
        try:
            _api_call(
                "POST",
                "/v5/account/repay",
                body={"coin": coin, "amount": amt, "repaymentType": "FLEXIBLE"},
            )
            return True
        except Exception as e:
            print(f"bybit margin repay {coin} via /account/repay failed: {e}", file=sys.stderr)
        try:
            _api_call("POST", "/v5/account/quick-repayment", body={"coin": coin})
            return True
        except Exception as e:
            print(f"bybit margin repay {coin} failed: {e}", file=sys.stderr)
            return False

    def place_margin_order(
        self,
        pair: str,
        side: str,
        amount_base: float,
        quantity_precision: int = 6,
        ref_price: float = 0.0,
    ) -> tuple[bool, dict[str, Any]]:
        """UTA spot margin 市价单（isLeverage=1）。

        sell: 余额不足自动借币卖出；buy: 买回的币自动冲销负债。
        买卖均以 base 数量计量（buy 用 marketUnit=baseCoin）。
        """
        client_oid = f"qmgn{int(time.time())}{random.randint(0, 9999)}"
        sz = f"{amount_base:.{quantity_precision}f}".rstrip("0").rstrip(".")
        body: dict[str, Any] = {
            "category": "spot",
            "symbol": pair,
            "side": "Buy" if side == "buy" else "Sell",
            "orderType": "Market",
            "qty": sz or f"{amount_base:.{quantity_precision}f}",
            "isLeverage": 1,
            "orderLinkId": client_oid,
        }
        if side == "buy":
            body["marketUnit"] = "baseCoin"
        submit_ts = time.time()
        try:
            result = _api_call("POST", "/v5/order/create", body=body)
            fill_ts = time.time()
            order_id = result.get("result", {}).get("orderId", "?")
            time.sleep(0.3)
            detail = _api_call(
                "GET", "/v5/order/realtime", params={"category": "spot", "orderId": order_id}
            )
            od = detail.get("result", {}).get("list", [{}])[0]
            exec_price = float(od.get("avgPrice", 0) or ref_price)
            exec_qty = float(od.get("cumExecQty", 0) or amount_base)
            exec_quote = float(od.get("cumExecValue", 0) or exec_qty * exec_price)
            slippage = (
                round((exec_price - ref_price) / ref_price, 6)
                if ref_price and exec_price
                else None
            )
            return True, {
                "order_id": order_id,
                "exec_price": exec_price,
                "exec_qty": exec_qty,
                "exec_quote_usd": exec_quote,
                "ref_price": ref_price,
                "slippage": slippage,
                "submit_ts": round(submit_ts, 3),
                "fill_ts": round(fill_ts, 3),
                "latency_ms": round((fill_ts - submit_ts) * 1000),
                "order_status": od.get("orderStatus", ""),
            }
        except Exception as e:
            return False, {"error": str(e)}

    def place_buy(
        self, pair: str, amount_usdt: float, quote_precision: int = 2, ref_price: float = 0.0,
    ) -> tuple[bool, dict[str, Any]]:
        client_oid = f"qbuy{int(time.time())}{random.randint(0, 9999)}"
        submit_ts = time.time()
        try:
            result = _api_call("POST", "/v5/order/create", body={
                "category": "spot", "symbol": pair, "side": "Buy",
                "orderType": "Market", "marketUnit": "quoteCoin",
                "qty": f"{amount_usdt:.{quote_precision}f}",
                "orderLinkId": client_oid,
            })
            fill_ts = time.time()
            order_id = result.get("result", {}).get("orderId", "?")
            time.sleep(0.3)
            detail = _api_call("GET", "/v5/order/realtime", params={"category": "spot", "orderId": order_id})
            od = detail.get("result", {}).get("list", [{}])[0]
            exec_price = float(od.get("avgPrice", 0) or ref_price)
            exec_qty = float(od.get("cumExecQty", 0))
            exec_quote = float(od.get("cumExecValue", amount_usdt))
            slippage = round((exec_price - ref_price) / ref_price, 6) if ref_price and exec_price else None
            return True, {
                "order_id": order_id, "exec_price": exec_price,
                "exec_qty": exec_qty, "exec_quote_usd": exec_quote,
                "ref_price": ref_price, "slippage": slippage,
                "submit_ts": round(submit_ts, 3), "fill_ts": round(fill_ts, 3),
                "latency_ms": round((fill_ts - submit_ts) * 1000),
                "order_status": od.get("orderStatus", ""),
            }
        except Exception as e:
            return False, {"error": str(e)}

    def place_sell(
        self, pair: str, amount_base: float, quantity_precision: int = 6, ref_price: float = 0.0,
    ) -> tuple[bool, dict[str, Any]]:
        client_oid = f"qsell{int(time.time())}{random.randint(0, 9999)}"
        sz = f"{amount_base:.{quantity_precision}f}".rstrip("0").rstrip(".")
        submit_ts = time.time()
        try:
            result = _api_call("POST", "/v5/order/create", body={
                "category": "spot", "symbol": pair, "side": "Sell",
                "orderType": "Market", "qty": sz,
                "orderLinkId": client_oid,
            })
            fill_ts = time.time()
            order_id = result.get("result", {}).get("orderId", "?")
            time.sleep(0.3)
            detail = _api_call("GET", "/v5/order/realtime", params={"category": "spot", "orderId": order_id})
            od = detail.get("result", {}).get("list", [{}])[0]
            exec_price = float(od.get("avgPrice", 0) or ref_price)
            exec_qty = float(od.get("cumExecQty", 0) or amount_base)
            exec_quote = exec_qty * exec_price
            slippage = round((exec_price - ref_price) / ref_price, 6) if ref_price and exec_price else None
            return True, {
                "order_id": order_id, "exec_price": exec_price,
                "exec_qty": exec_qty, "exec_quote_usd": exec_quote,
                "ref_price": ref_price, "slippage": slippage,
                "submit_ts": round(submit_ts, 3), "fill_ts": round(fill_ts, 3),
                "latency_ms": round((fill_ts - submit_ts) * 1000),
                "order_status": od.get("orderStatus", ""),
            }
        except Exception as e:
            return False, {"error": str(e)}

    def place_futures_order(
        self, pair: str, side: str, amount_base: float,
        quantity_precision: int = 3, ref_price: float = 0.0,
    ) -> tuple[bool, dict[str, Any]]:
        bybit_side = "Buy" if side in ("open_long", "close_short") else "Sell"
        client_oid = f"qfut{int(time.time())}{random.randint(0, 9999)}"
        sz = f"{amount_base:.{quantity_precision}f}".rstrip("0").rstrip(".")
        submit_ts = time.time()

        self.initialize_futures_symbol(pair)

        try:
            result = _api_call("POST", "/v5/order/create", body={
                "category": "linear", "symbol": pair, "side": bybit_side,
                "orderType": "Market", "qty": sz, "orderLinkId": client_oid,
            })
            fill_ts = time.time()
            order_id = result.get("result", {}).get("orderId", "?")

            exec_price = ref_price
            try:
                time.sleep(0.5)
                detail = _api_call("GET", "/v5/order/realtime", params={"category": "linear", "orderId": order_id})
                od = detail.get("result", {}).get("list", [{}])[0]
                exec_price = float(od.get("avgPrice", 0) or ref_price)
            except Exception:
                pass

            slippage = round((exec_price - ref_price) / ref_price, 6) if ref_price and exec_price else None
            return True, {
                "order_id": order_id, "exec_price": exec_price,
                "exec_qty": amount_base, "exec_quote_usd": amount_base * exec_price,
                "ref_price": ref_price, "slippage": slippage,
                "submit_ts": round(submit_ts, 3), "fill_ts": round(fill_ts, 3),
                "latency_ms": round((fill_ts - submit_ts) * 1000),
                "order_status": "filled",
            }
        except Exception as e:
            return False, {"error": str(e)}

    def execute_trades(
        self, trades: list[dict[str, Any]], market: dict[str, dict[str, Any]], dry_run: bool,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for trade in trades:
            symbol = trade["symbol"]
            mkt = market.get(symbol, {})
            pair = str(mkt.get("pair") or make_pair(symbol, "USDT"))
            ref_price = float(mkt.get("price", 0))
            record = dict(trade)
            record["dry_run"] = dry_run
            record["ref_price"] = ref_price
            record["venue"] = self.venue_id
            if dry_run:
                record["status"] = "simulated"
                record["order_id"] = None
                record["slippage"] = 0.0
                record["latency_ms"] = 0
                results.append(record)
                continue
            is_margin = str(trade.get("account", "")).lower() == "margin"
            if trade["type"] in ("buy", "sell") and is_margin:
                # Reverse C&C 现货腿走 UTA spot margin（isLeverage=1 自动借/还）。
                ok, detail = self.place_margin_order(
                    pair, trade["type"], trade["amount_base"],
                    int(mkt.get("quantity_precision", 6)), ref_price=ref_price,
                )
            elif trade["type"] == "buy":
                ok, detail = self.place_buy(pair, trade["amount_usdt"], int(mkt.get("quote_precision", 2)), ref_price=ref_price)
            elif trade["type"] == "sell":
                ok, detail = self.place_sell(pair, trade["amount_base"], int(mkt.get("quantity_precision", 6)), ref_price=ref_price)
            elif trade["type"] in ("open_short", "close_long", "close_short", "open_long"):
                ok, detail = self.place_futures_order(
                    pair, trade["type"], trade["amount_base"],
                    int(trade.get("quantity_precision") or mkt.get("quantity_precision", 3)),
                    ref_price=ref_price,
                )
            else:
                ok, detail = False, {"error": f"Unknown trade type {trade['type']}"}
            record["status"] = "filled" if ok else "failed"
            if ok:
                record.update({
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
                })
            else:
                record["order_id"] = None
                record["error"] = detail.get("error", str(detail))
            results.append(record)
        return results
