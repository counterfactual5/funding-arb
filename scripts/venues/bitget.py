#!/usr/bin/env python3
"""Bitget spot venue adapter."""

from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from venues.base import make_pair
from venues.http_util import http_get_json, parse_kline_ohlcv, rules_for_price
from core.config import resolve_timeframes

BASE = "https://api.bitget.com"
CONFIG_PATH = os.path.expanduser("~/.funding-arb/funding-arb.json")
_symbol_rules_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_futures_rules_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_spot_ticker_loaded_at: float = 0.0
_spot_ticker_prices: dict[str, float] = {}
_env_loaded = False
_initialized_symbols: set[str] = set()


def _ensure_env() -> None:
    global _env_loaded
    if _env_loaded:
        return
    if os.environ.get("BITGET_API_KEY"):
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
    return os.environ.get("BITGET_API_KEY") or ""


def _get_secret() -> str:
    _ensure_env()
    return os.environ.get("BITGET_SECRET_KEY") or ""


def _get_pass() -> str:
    _ensure_env()
    return os.environ.get("BITGET_PASSPHRASE") or ""


def _sign(ts: str, method: str, path: str, body: str) -> str:
    import base64
    import hashlib
    import hmac

    prehash = ts + method + path + (body or "")
    return base64.b64encode(
        hmac.new(_get_secret().encode(), prehash.encode(), hashlib.sha256).digest()
    ).decode()


def _api_call(
    method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None
) -> dict:
    from urllib.parse import urlencode

    # 凭证校验：缺失立即报错，绝不用空凭证发私有请求（避免静默失败/账实误判）
    key, secret, passp = _get_key(), _get_secret(), _get_pass()
    if not (key and secret and passp):
        raise RuntimeError(
            "Bitget API 凭证缺失：请设置环境变量 BITGET_API_KEY / BITGET_SECRET_KEY / "
            "BITGET_PASSPHRASE，或在 ~/.funding-arb/funding-arb.json 的 env 中配置。"
        )

    p = "?" + urlencode(params) if params else ""
    body_str = json.dumps(body) if body else ""
    # GET 可安全重试（含 429/网络抖动）；POST(下单) 不重试，避免重复下单
    retries = 3 if method == "GET" else 1
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        ts = str(int(time.time() * 1000))  # 每次重试刷新时间戳，避免签名过期
        sig = _sign(ts, method, path + p, body_str)
        headers = {
            "CONTENT-TYPE": "application/json",
            "ACCESS-KEY": key,
            "ACCESS-SIGN": sig,
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": passp,
            "locale": "en-US",
        }
        req = urllib.request.Request(BASE + path + p, headers=headers, method=method)
        if body_str:
            req.data = body_str.encode()
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            last_err = e
            if method == "GET" and attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    raise last_err if last_err else RuntimeError("bitget _api_call failed")


class BitgetSpotVenue:
    venue_id = "bitget"

    def get_ticker(self, pair: str) -> float:
        url = f"{BASE}/api/v2/spot/market/tickers?symbol={pair}"
        try:
            data = http_get_json(url).get("data", [])
            if data:
                return float(data[0].get("lastPr", "0"))
        except Exception:
            pass
        return 0.0

    def get_all_spot_tickers(self, cache_sec: int = 5) -> dict[str, float]:
        """Bulk spot last prices {PAIR: price}. Cached briefly for screener loops."""
        global _spot_ticker_loaded_at, _spot_ticker_prices
        now = time.time()
        if _spot_ticker_prices and (now - _spot_ticker_loaded_at) < cache_sec:
            return dict(_spot_ticker_prices)
        try:
            rows = http_get_json(f"{BASE}/api/v2/spot/market/tickers").get("data", [])
            _spot_ticker_prices = {
                str(r.get("symbol", "")).upper(): float(r.get("lastPr", 0) or 0)
                for r in rows
                if r.get("symbol")
            }
            _spot_ticker_loaded_at = now
        except Exception:
            pass
        return dict(_spot_ticker_prices)

    # Bitget 接受的 granularity 格式与通用简写的映射
    GRANULARITY_MAP: dict[str, str] = {
        "1m":   "1min",
        "3m":   "3min",
        "5m":   "5min",
        "15m":  "15min",
        "30m":  "30min",
        "1h":   "1h",
        "2h":   "2h",
        "4h":   "4h",
        "6h":   "6h",
        "12h":  "12h",
        "1d":   "1day",
        "1day": "1day",
        "1w":   "1week",
        "1week":"1week",
    }

    def get_klines(
        self, pair: str, granularity: str = "1day", limit: int = 200
    ) -> list:
        gran = self.GRANULARITY_MAP.get(granularity, granularity)
        url = f"{BASE}/api/v2/spot/market/candles?symbol={pair}&granularity={gran}&limit={limit}"
        try:
            return http_get_json(url).get("data", [])
        except Exception:
            return []

    def fetch_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> Optional[dict[str, Any]]:
        now = time.time()
        cached = _symbol_rules_cache.get(pair)
        if cached and (now - cached[0]) < cache_sec:
            return dict(cached[1])
        url = f"{BASE}/api/v2/spot/public/symbols?symbol={pair}"
        try:
            payload = http_get_json(url)
        except Exception:
            return dict(cached[1]) if cached else None
        if payload.get("code") != "00000" or not payload.get("data"):
            return dict(cached[1]) if cached else None
        info = payload["data"][0]
        qty_prec = int(info.get("quantityPrecision", 6))
        quote_prec = int(info.get("quotePrecision", 2))
        min_usdt = float(info.get("minTradeUSDT") or 0)
        min_base = float(info.get("minTradeAmount") or 0)
        if min_base <= 0:
            min_base = 10 ** (-qty_prec)
        rules = {
            "symbol": pair,
            "min_trade_usdt": min_usdt,
            "min_trade_base": min_base,
            "quantity_precision": qty_prec,
            "quote_precision": quote_prec,
            "status": info.get("status", ""),
        }
        _symbol_rules_cache[pair] = (now, rules)
        return dict(rules)

    def fetch_futures_symbol_rules(self, pair: str, cache_sec: int = 3600) -> dict[str, Any] | None:
        now = time.time()
        cached = _futures_rules_cache.get(pair)
        if cached and (now - cached[0]) < cache_sec:
            return dict(cached[1])
        url = f"{BASE}/api/v2/mix/market/contracts?productType=USDT-FUTURES"
        try:
            payload = http_get_json(url)
        except Exception:
            return dict(cached[1]) if cached else None
        if payload.get("code") != "00000" or not payload.get("data"):
            return dict(cached[1]) if cached else None
            
        sym_info = None
        target_sym = pair
        for s in payload["data"]:
            if s.get("symbol") == target_sym:
                sym_info = s
                break
                
        if not sym_info:
            return dict(cached[1]) if cached else None

        size_mult = str(sym_info.get("sizeMultiplier", "0.001"))
        qty_prec = len(size_mult.split(".")[1]) if "." in size_mult else 0
            
        quote_prec = int(sym_info.get("pricePlace", 2))
        min_base = float(sym_info.get("minTradeNum", 0))
        
        if min_base <= 0:
            min_base = 10 ** (-qty_prec)
            
        rules = {
            "symbol": pair,
            "min_trade_usdt": 0,
            "min_trade_base": min_base,
            "quantity_precision": qty_prec,
            "quote_precision": quote_prec,
            "status": sym_info.get("symbolStatus", ""),
        }
        _futures_rules_cache[pair] = (now, rules)
        return dict(rules)

    # 官方 wallet/transfer 文档：futures 账户类型为 usdt_futures（非 mix_usdt）
    _ACCOUNT_TYPES = {"spot": "spot", "futures": "usdt_futures", "margin": "crossed_margin"}

    def transfer_asset(self, asset: str, amount: float, from_account: str, to_account: str) -> bool:
        """Transfer between spot / USDT-M futures / cross margin on Bitget."""
        fromType = self._ACCOUNT_TYPES.get(from_account)
        toType = self._ACCOUNT_TYPES.get(to_account)
        if not fromType or not toType:
            return False

        try:
            res = _api_call(
                "POST",
                "/api/v2/spot/wallet/transfer",
                body={
                    "fromType": fromType,
                    "toType": toType,
                    "amount": f"{amount:.8f}".rstrip("0").rstrip("."),
                    "coin": asset,
                    "clientOid": f"t{int(time.time()*1000)}",
                },
            )
            return res.get("code") == "00000"
        except Exception:
            return False

    def fetch_usdt_account_balances(self) -> dict[str, float]:
        """Fetch separate Spot and Futures USDT balances. Returns {'spot': val, 'futures': val}."""
        spot_usdt = 0.0
        try:
            data = _api_call("GET", "/api/v2/spot/account/assets")
            for asset in data.get("data", []):
                if str(asset.get("coin", "")).upper() == "USDT":
                    spot_usdt = float(asset.get("available", "0"))
                    break
        except Exception as e:
            print(f"fetch_usdt_account_balances spot error: {e}", file=sys.stderr)
            raise e
            
        futures_usdt = 0.0
        try:
            f_data = _api_call("GET", "/api/v2/mix/account/accounts", params={"productType": "USDT-FUTURES"})
            for asset in f_data.get("data", []):
                if str(asset.get("marginCoin", "")).upper() == "USDT":
                    futures_usdt = float(asset.get("available", "0"))
                    break
        except Exception as e:
            print(f"fetch_usdt_account_balances futures error: {e}", file=sys.stderr)
            raise e
            
        return {"spot": spot_usdt, "futures": futures_usdt}

    def fetch_asset_market(
        self, asset: str, quote: str = "USDT", cfg: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        pair = make_pair(asset, quote)
        price = self.get_ticker(pair)
        rules = self.fetch_symbol_rules(pair)
        if rules is None:
            return {
                "symbol": asset,
                "price": price,
                "rules_error": True,
                "venue": self.venue_id,
            }
        limits = rules_for_price(rules, price)
        
        cfg = cfg or {}
        tf = resolve_timeframes(cfg)
        
        klines_1d = [
            parse_kline_ohlcv(k) for k in self.get_klines(pair, tf["slow"]["interval"], tf["slow"]["limit"]) if k
        ]
        klines_4h = [
            parse_kline_ohlcv(k)
            for k in self.get_klines(pair, tf["mid"]["interval"], tf["mid"]["limit"])
            if k
        ]
        klines_1w = [
            parse_kline_ohlcv(k) for k in self.get_klines(pair, tf["macro"]["interval"], tf["macro"]["limit"]) if k
        ]
        return {
            "symbol": asset,
            "pair": pair,
            "price": price,
            "rules_error": False,
            "venue": self.venue_id,
            "symbol_rules": rules,
            **limits,
            "klines_1d": klines_1d,
            "klines_4h": klines_4h,
            "klines_1w": klines_1w,
        }

    def fetch_balances(self, coins: list[str]) -> dict[str, float]:
        # 不吞异常：拉余额失败必须让上层 abort，绝不返回全 0（否则策略误判账户为空、只用现金下单）
        balances: dict[str, float] = {c: 0.0 for c in coins}
        data = _api_call("GET", "/api/v2/spot/account/assets")
        for asset in data.get("data", []):
            coin = str(asset.get("coin", "")).upper()
            if coin in balances:
                balances[coin] = float(asset.get("available", "0"))
                
        # Fetch futures balance if USDT is requested
        if "USDT" in balances:
            try:
                f_data = _api_call("GET", "/api/v2/mix/account/accounts", params={"productType": "USDT-FUTURES"})
                for asset in f_data.get("data", []):
                    if str(asset.get("marginCoin", "")).upper() == "USDT":
                        balances["USDT"] += float(asset.get("balance", "0"))
            except Exception as e:
                print(f"bitget fetch_futures_balances error: {e}", file=sys.stderr)
                
        return balances

    def fetch_live_state(self, assets: list[str]) -> dict[str, Any]:
        """Fetch unified global state for Bitget: spot balances and futures positions."""
        balances = self.fetch_balances(assets + ["USDT"])
        
        positions = {}
        try:
            pos_data = _api_call("GET", "/api/v2/mix/position/all-position", params={"productType": "USDT-FUTURES"})
            for pos in pos_data.get("data", []):
                total_amt = float(pos.get("total", "0") or 0)
                if total_amt > 1e-9:
                    raw_sym = pos.get("symbol", "")
                    base_sym = raw_sym
                    if raw_sym.endswith("USDT"):
                        base_sym = raw_sym[:-4]
                    
                    side = str(pos.get("holdSide", "")).lower() # "long" or "short"
                    entry = float(pos.get("averageOpenPrice", 0) or pos.get("openPriceAvg", 0) or 0)
                    unrealized = float(pos.get("unrealizedPL", 0) or 0)
                    liq_price = float(pos.get("liquidationPrice", 0) or 0)
                    lev = float(pos.get("leverage", 1) or 1)
                    
                    positions[base_sym] = {
                        "amount": total_amt,
                        "side": side,
                        "entry_price": entry,
                        "unrealized_pnl": unrealized,
                        "liq_price": liq_price,
                        "leverage": lev
                    }
        except Exception as e:
            print(f"fetch_live_state futures positions error: {e}", file=sys.stderr)
            raise e
            
        return {
            "balances": balances,
            "futures_positions": positions
        }
        
    def initialize_futures_symbol(self, pair: str) -> None:
        """Initialize futures configuration (marginType, leverage, positionSide) for a specific pair on Bitget."""
        if pair in _initialized_symbols:
            return
        mix_symbol = f"{pair}_UMCBL"
        try:
            _api_call("POST", "/api/v2/mix/account/set-margin-mode", body={
                "symbol": mix_symbol, "marginCoin": "USDT", "marginMode": "isolated",
            })
        except Exception:
            pass
        for side in ("long", "short"):
            try:
                _api_call("POST", "/api/v2/mix/account/set-leverage", body={
                    "symbol": mix_symbol, "marginCoin": "USDT", "leverage": "1", "holdSide": side,
                })
            except Exception:
                pass
        _initialized_symbols.add(pair)

    def fetch_borrow_rates(self, coins: list[str]) -> dict[str, float]:
        """Fetch annualized borrow rates from Bitget cross margin. Returns {coin: rate_decimal}."""
        rates: dict[str, float] = {c: 0.0 for c in coins}
        for coin in coins:
            try:
                data = _api_call(
                    "GET",
                    "/api/v2/margin/crossed/interest-rate-and-limit",
                    params={"coin": coin.upper()},
                )
                items = data.get("data") or []
                if not items or not items[0].get("borrowable"):
                    continue
                item = items[0]
                daily = float(item.get("dailyInterestRate", 0) or 0)
                annual = float(item.get("annualInterestRate", 0) or 0)
                if annual <= 0 and daily > 0:
                    annual = daily * 365
                if annual > 0:
                    rates[coin.upper()] = annual
            except Exception:
                pass
        return rates

    # ── cross margin（Reverse C&C：借币卖出 / 买回还币） ──────────────────────

    def supports_reverse_arbitrage(self) -> bool:
        """Cross margin 借还币能力：无 API 密钥时假定代码路径可用；有密钥则探测账户。"""
        if not (_get_key() and _get_secret()):
            return True
        try:
            data = _api_call(
                "GET", "/api/v2/margin/crossed/account/assets", params={"coin": "USDT"}
            )
            return data.get("code") == "00000"
        except Exception:
            return False

    def fetch_margin_debt(self, assets: list[str]) -> dict[str, float]:
        """Cross margin 各资产未偿债务（borrow + interest），单位为币本位数量。"""
        debt: dict[str, float] = {a.upper(): 0.0 for a in assets}
        try:
            data = _api_call("GET", "/api/v2/margin/crossed/account/assets")
            for item in data.get("data", []):
                coin = str(item.get("coin", "")).upper()
                if coin in debt:
                    debt[coin] = float(item.get("borrow", 0) or 0) + float(
                        item.get("interest", 0) or 0
                    )
        except Exception as e:
            print(f"bitget fetch_margin_debt failed: {e}", file=sys.stderr)
        return debt

    def _margin_borrow_repay(self, asset: str, amount: float, op: str) -> bool:
        path = f"/api/v2/margin/crossed/account/{op}"
        amt_key = "borrowAmount" if op == "borrow" else "repayAmount"
        try:
            res = _api_call(
                "POST",
                path,
                body={
                    "coin": asset.upper(),
                    amt_key: f"{amount:.8f}".rstrip("0").rstrip("."),
                    "clientid": f"qm{op[0]}{int(time.time())}{random.randint(0, 9999)}",
                },
            )
            return res.get("code") == "00000"
        except Exception as e:
            print(f"bitget margin {op} {asset} failed: {e}", file=sys.stderr)
            return False

    def margin_borrow(self, asset: str, amount: float) -> bool:
        return self._margin_borrow_repay(asset, amount, "borrow")

    def margin_repay(self, asset: str, amount: float) -> bool:
        return self._margin_borrow_repay(asset, amount, "repay")

    def _fetch_margin_fills(self, pair: str, order_id: str) -> dict[str, Any]:
        try:
            data = _api_call(
                "GET",
                "/api/v2/margin/crossed/fills",
                params={"symbol": pair, "orderId": order_id},
            )
        except Exception:
            return {}
        fills = (data.get("data") or {}).get("fills") or data.get("data") or []
        if isinstance(fills, dict):
            fills = fills.get("fills", [])
        exec_qty = 0.0
        exec_quote = 0.0
        for f in fills if isinstance(fills, list) else []:
            qty = float(f.get("size", 0) or 0)
            px = float(f.get("priceAvg", f.get("price", 0)) or 0)
            amt = float(f.get("amount", 0) or 0)
            exec_qty += qty
            exec_quote += amt if amt > 0 else qty * px
        return {
            "exec_qty": exec_qty,
            "exec_quote_usd": exec_quote,
            "exec_price": exec_quote / exec_qty if exec_qty > 0 else 0.0,
        }

    def place_margin_order(
        self,
        pair: str,
        side: str,
        amount_base: float,
        quantity_precision: int = 6,
        ref_price: float = 0.0,
        side_effect: str = "",
    ) -> tuple[bool, dict[str, Any]]:
        """Cross margin 市价单。

        side_effect:
          - auto_borrow → loanType=autoLoan : 自动借入缺口资产（SELL 即借币卖出）
          - auto_repay  → loanType=autoRepay: 成交后自动偿还借款（BUY 即买回还币）
        市价买以 quote 计量，按 ref_price 加 1% buffer 折算后由 autoRepay 清债。
        """
        loan_type = {"auto_borrow": "autoLoan", "auto_repay": "autoRepay"}.get(
            side_effect.lower(), "normal"
        )
        client_oid = f"qmgn{int(time.time())}{random.randint(0, 9999)}"
        body: dict[str, Any] = {
            "symbol": pair,
            "orderType": "market",
            "side": side,
            "force": "gtc",  # 官方必填；市价单 force 不参与撮合逻辑
            "loanType": loan_type,
            "clientOid": client_oid,
        }
        if side == "sell":
            sz = f"{amount_base:.{quantity_precision}f}".rstrip("0").rstrip(".")
            body["baseSize"] = sz or f"{amount_base:.{quantity_precision}f}"
        else:
            if ref_price <= 0:
                return False, {"error": "margin market buy 需要 ref_price 折算 quoteSize"}
            body["quoteSize"] = f"{amount_base * ref_price * 1.01:.4f}"
        submit_ts = time.time()
        try:
            result = _api_call("POST", "/api/v2/margin/crossed/place-order", body=body)
            fill_ts = time.time()
            if result.get("code") != "00000":
                return False, {"error": f"{result.get('code')}: {result.get('msg', 'unknown')}"}
            order_id = str((result.get("data") or {}).get("orderId", "?"))
            time.sleep(0.3)
            parsed = self._fetch_margin_fills(pair, order_id)
            exec_price = parsed.get("exec_price") or ref_price
            exec_qty = parsed.get("exec_qty") or amount_base
            slippage = (
                round((exec_price - ref_price) / ref_price, 6)
                if ref_price and exec_price
                else None
            )
            return True, {
                "order_id": order_id,
                "exec_price": exec_price,
                "exec_qty": exec_qty,
                "exec_quote_usd": parsed.get("exec_quote_usd") or exec_qty * exec_price,
                "ref_price": ref_price,
                "slippage": slippage,
                "submit_ts": round(submit_ts, 3),
                "fill_ts": round(fill_ts, 3),
                "latency_ms": round((fill_ts - submit_ts) * 1000),
                "order_status": "filled",
            }
        except urllib.error.HTTPError as e:
            try:
                body_txt = e.read().decode()
            except Exception:
                body_txt = ""
            return False, {"error": f"HTTP {e.code}: {body_txt[:200]}"}
        except Exception as e:
            return False, {"error": str(e)}

    def _fetch_order_detail(self, order_id: str) -> dict[str, Any]:
        try:
            return _api_call(
                "GET", "/api/v2/spot/trade/orderInfo", params={"orderId": order_id}
            )
        except Exception:
            return {}

    @staticmethod
    def _parse_order_detail(detail: dict[str, Any]) -> dict[str, Any]:
        exec_price = 0.0
        exec_qty = 0.0
        exec_quote = 0.0
        status = ""
        for od in detail.get("data", []):
            exec_price = float(od.get("priceAvg", 0))
            status = od.get("status", "")
            quote_vol = float(od.get("quoteVolume", od.get("volume", 0)))
            exec_quote = quote_vol
            if exec_price > 0:
                exec_qty = quote_vol / exec_price
            else:
                exec_qty = float(od.get("sizeAccumulate", od.get("size", 0)))
        return {
            "exec_price": exec_price,
            "exec_qty": exec_qty,
            "exec_quote_usd": exec_quote,
            "order_status": status,
        }

    def place_buy(
        self,
        pair: str,
        amount_usdt: float,
        quote_precision: int = 2,
        ref_price: float = 0.0,
    ) -> tuple[bool, dict[str, Any]]:
        client_oid = f"qbuy{int(time.time() // 3600)}n{int(round(amount_usdt * 100))}r{random.randint(0, 9999)}"
        size = f"{amount_usdt:.{quote_precision}f}"
        submit_ts = time.time()
        try:
            result = _api_call(
                "POST",
                "/api/v2/spot/trade/place-order",
                body={
                    "symbol": pair,
                    "side": "buy",
                    "orderType": "market",
                    "size": size,
                    "clientOid": client_oid,
                },
            )
            fill_ts = time.time()
            latency_ms = round((fill_ts - submit_ts) * 1000)
            if result.get("code") == "00000":
                order_id = str(result.get("data", {}).get("orderId", "?"))
                parsed = self._parse_order_detail(self._fetch_order_detail(order_id))
                exec_price = parsed["exec_price"]
                slippage = (
                    round((exec_price - ref_price) / ref_price, 6)
                    if ref_price and exec_price
                    else None
                )
                return True, {
                    "order_id": order_id,
                    "exec_price": exec_price,
                    "exec_qty": parsed["exec_qty"],
                    "exec_quote_usd": parsed["exec_quote_usd"],
                    "ref_price": ref_price,
                    "slippage": slippage,
                    "submit_ts": round(submit_ts, 3),
                    "fill_ts": round(fill_ts, 3),
                    "latency_ms": latency_ms,
                    "order_status": parsed["order_status"],
                }
            return False, {
                "error": f"{result.get('code')}: {result.get('msg', 'unknown')}"
            }
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode()
            except Exception:
                body = ""
            return False, {"error": f"HTTP {e.code}: {body[:200]}"}
        except Exception as e:
            return False, {"error": str(e)}

    def place_sell(
        self,
        pair: str,
        amount_base: float,
        quantity_precision: int = 6,
        ref_price: float = 0.0,
    ) -> tuple[bool, dict[str, Any]]:
        client_oid = f"qsell{int(time.time() // 3600)}n{int(round(amount_base * 1e6))}r{random.randint(0, 9999)}"
        size = f"{amount_base:.{quantity_precision}f}".rstrip("0").rstrip(".")
        if "." not in size:
            size = f"{amount_base:.{quantity_precision}f}"
        submit_ts = time.time()
        try:
            result = _api_call(
                "POST",
                "/api/v2/spot/trade/place-order",
                body={
                    "symbol": pair,
                    "side": "sell",
                    "orderType": "market",
                    "size": size,
                    "clientOid": client_oid,
                },
            )
            fill_ts = time.time()
            latency_ms = round((fill_ts - submit_ts) * 1000)
            if result.get("code") == "00000":
                order_id = str(result.get("data", {}).get("orderId", "?"))
                parsed = self._parse_order_detail(self._fetch_order_detail(order_id))
                exec_price = parsed["exec_price"]
                slippage = (
                    round((exec_price - ref_price) / ref_price, 6)
                    if ref_price and exec_price
                    else None
                )
                return True, {
                    "order_id": order_id,
                    "exec_price": exec_price,
                    "exec_qty": parsed["exec_qty"],
                    "exec_quote_usd": parsed["exec_quote_usd"],
                    "ref_price": ref_price,
                    "slippage": slippage,
                    "submit_ts": round(submit_ts, 3),
                    "fill_ts": round(fill_ts, 3),
                    "latency_ms": latency_ms,
                    "order_status": parsed["order_status"],
                }
            return False, {
                "error": f"{result.get('code')}: {result.get('msg', 'unknown')}"
            }
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode()
            except Exception:
                body = ""
            return False, {"error": f"HTTP {e.code}: {body[:200]}"}
        except Exception as e:
            return False, {"error": str(e)}

    def place_futures_order(
        self,
        pair: str,
        side: str, # "open_long", "open_short", "close_long", "close_short"
        amount_base: float,
        quantity_precision: int = 3,
        ref_price: float = 0.0,
    ) -> tuple[bool, dict[str, Any]]:
        client_oid = f"qfut{int(time.time() // 3600)}n{random.randint(0, 99999)}"
        size = f"{amount_base:.{quantity_precision}f}".rstrip("0").rstrip(".")
        if "." not in size:
            size = f"{amount_base:.{quantity_precision}f}"
        submit_ts = time.time()

        self.initialize_futures_symbol(pair)

        try:
            result = _api_call(
                "POST",
                "/api/v2/mix/order/place-order",
                body={
                    "symbol": pair,
                    "productType": "USDT-FUTURES",
                    "marginMode": "isolated",
                    "marginCoin": "USDT",
                    "side": side,
                    "orderType": "market",
                    "size": size,
                    "clientOid": client_oid,
                },
            )
            fill_ts = time.time()
            latency_ms = round((fill_ts - submit_ts) * 1000)
            if result.get("code") == "00000":
                order_id = str(result.get("data", {}).get("orderId", "?"))

                exec_price = ref_price
                try:
                    time.sleep(0.5)
                    detail = _api_call("GET", "/api/v2/mix/order/detail", params={
                        "symbol": pair, "orderId": order_id,
                    })
                    dp = detail.get("data", {})
                    if dp.get("priceAvg"):
                        exec_price = float(dp["priceAvg"])
                except Exception:
                    pass

                slippage = (
                    round((exec_price - ref_price) / ref_price, 6)
                    if ref_price and exec_price
                    else None
                )
                return True, {
                    "order_id": order_id,
                    "exec_price": exec_price,
                    "exec_qty": amount_base,
                    "exec_quote_usd": amount_base * exec_price,
                    "ref_price": ref_price,
                    "slippage": slippage,
                    "submit_ts": round(submit_ts, 3),
                    "fill_ts": round(fill_ts, 3),
                    "latency_ms": latency_ms,
                    "order_status": "filled",
                }
            return False, {
                "error": f"{result.get('code')}: {result.get('msg', 'unknown')}"
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
                # Simulate slippage for more realistic paper trading
                slippage_bps = float(trade.get("slippage_bps", 0)) or 2.0
                sim_slippage = slippage_bps / 10000.0
                if trade["type"] == "buy":
                    record["slippage"] = round(sim_slippage, 6)
                else:
                    record["slippage"] = round(-sim_slippage, 6)
                record["latency_ms"] = 0
                results.append(record)
                continue
            is_margin = str(trade.get("account", "")).lower() == "margin"
            if trade["type"] in ("buy", "sell") and is_margin:
                # Reverse C&C 的现货腿走 cross margin：
                # sell + auto_borrow = 借币卖出；buy + auto_repay = 买回自动还币。
                ok, detail = self.place_margin_order(
                    pair,
                    trade["type"],
                    trade["amount_base"],
                    int(mkt.get("quantity_precision", 6)),
                    ref_price=ref_price,
                    side_effect=str(trade.get("side_effect", "")),
                )
            elif trade["type"] == "buy":
                ok, detail = self.place_buy(
                    pair,
                    trade["amount_usdt"],
                    int(mkt.get("quote_precision", 2)),
                    ref_price=ref_price,
                )
            elif trade["type"] == "sell":
                ok, detail = self.place_sell(
                    pair,
                    trade["amount_base"],
                    int(mkt.get("quantity_precision", 6)),
                    ref_price=ref_price,
                )
            elif trade["type"] in ("open_short", "close_long", "close_short", "open_long"):
                # 永续开平
                ok, detail = self.place_futures_order(
                    pair,
                    trade["type"],
                    trade["amount_base"],
                    int(trade.get("quantity_precision") or mkt.get("quantity_precision", 3)),
                    ref_price=ref_price,
                )
            else:
                ok, detail = False, {"error": f"Unknown trade type {trade['type']}"}
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
