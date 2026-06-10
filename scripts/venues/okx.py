#!/usr/bin/env python3
"""OKX spot + USDT-SWAP venue adapter."""

from __future__ import annotations

import base64
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

from core.config import resolve_timeframes
from venues.base import make_pair
from venues.http_util import http_get_json, parse_kline_ohlcv, rules_for_price

BASE = "https://www.okx.com"
CONFIG_PATH = os.path.expanduser("~/.funding-arb/funding-arb.json")
_symbol_rules_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_futures_rules_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_initialized_symbols: set[str] = set()
_env_loaded = False
_acct_config_cache: tuple[float, dict] | None = None
_futures_ticker_loaded_at: float = 0.0
_futures_ticker_prices: dict[str, float] = {}


def _ensure_env() -> None:
    global _env_loaded
    if _env_loaded:
        return
    if os.environ.get("OKX_API_KEY"):
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
    return os.environ.get("OKX_API_KEY") or ""


def _get_secret() -> str:
    _ensure_env()
    return os.environ.get("OKX_SECRET_KEY") or ""


def _get_pass() -> str:
    _ensure_env()
    return os.environ.get("OKX_PASSPHRASE") or ""


def _sign(timestamp: str, method: str, path: str, body: str) -> str:
    prehash = timestamp + method.upper() + path + body
    return base64.b64encode(
        hmac.new(_get_secret().encode(), prehash.encode(), hashlib.sha256).digest()
    ).decode()


def _api_call(
    method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None
) -> dict:
    key, secret, passp = _get_key(), _get_secret(), _get_pass()
    if not (key and secret and passp):
        raise RuntimeError(
            "OKX API 凭证缺失：请设置 OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE，"
            "或在 ~/.funding-arb/funding-arb.json 的 env 中配置。"
        )

    query = urllib.parse.urlencode(params) if params else ""
    body_str = json.dumps(body) if body else ""
    full_path = path + ("?" + query if query else "")

    retries = 3 if method == "GET" else 1
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        sig = _sign(ts, method, full_path, body_str)
        headers = {
            "OK-ACCESS-KEY": key,
            "OK-ACCESS-SIGN": sig,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": passp,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; funding-arb-cex/1.0)",
        }
        url = BASE + full_path
        req = urllib.request.Request(url, headers=headers, method=method)
        if body_str:
            req.data = body_str.encode()
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            if data.get("code") != "0":
                raise RuntimeError(
                    f"OKX API error: {data.get('msg', data.get('code'))}"
                )
            return data
        except Exception as e:
            last_err = e
            if method == "GET" and attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    raise last_err if last_err else RuntimeError("okx _api_call failed")


def _inst_id(asset: str, quote: str = "USDT") -> str:
    return f"{asset.upper()}-{quote.upper()}"


def _sz_precision(val: float) -> int:
    """lotSz/tickSz 小数位；避免 str(1e-06) 无小数点导致精度为 0。"""
    s = f"{val:.12f}".rstrip("0")
    if "." in s:
        return len(s.split(".")[1])
    return 0


def _swap_inst_id(asset: str, quote: str = "USDT") -> str:
    return f"{asset.upper()}-{quote.upper()}-SWAP"


class OkxSpotVenue:
    venue_id = "okx"

    GRANULARITY_MAP: dict[str, str] = {
        "1m": "1m",
        "3m": "3m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1H",
        "2h": "2H",
        "4h": "4H",
        "6h": "6H",
        "12h": "12H",
        "1day": "1D",
        "1d": "1D",
        "1week": "1W",
        "1w": "1W",
    }

    def get_ticker(self, pair: str) -> float:
        inst = pair
        url = f"{BASE}/api/v5/market/ticker?instId={inst}"
        try:
            data = http_get_json(url)
            return float(data.get("data", [{}])[0].get("last", 0))
        except Exception:
            return 0.0

    def get_futures_ticker(self, pair: str) -> float:
        """永续合约最新价。pair 格式可以是 BTCUSDT（自动转为 BTC-USDT-SWAP）或完整 instId。"""
        inst = pair
        if "-" not in pair:
            # BTCUSDT → BTC-USDT-SWAP
            base = pair.replace("USDT", "")
            if base:
                inst = f"{base}-USDT-SWAP"
        url = f"{BASE}/api/v5/market/ticker?instId={inst}"
        try:
            data = http_get_json(url)
            return float(data.get("data", [{}])[0].get("last", 0))
        except Exception:
            return 0.0

    def get_all_futures_tickers(self, cache_sec: int = 5) -> dict[str, float]:
        """Bulk SWAP last prices {BTCUSDT: price, BTC-USDT-SWAP: price}. Cached briefly."""
        global _futures_ticker_loaded_at, _futures_ticker_prices
        now = time.time()
        if _futures_ticker_prices and (now - _futures_ticker_loaded_at) < cache_sec:
            return dict(_futures_ticker_prices)
        try:
            rows = http_get_json(f"{BASE}/api/v5/market/tickers?instType=SWAP").get(
                "data", []
            )
            new_prices: dict[str, float] = {}
            for r in rows:
                inst_id = str(r.get("instId", "")).upper()
                last = float(r.get("last", 0) or 0)
                if not inst_id or last <= 0:
                    continue
                # Map both formats: BTC-USDT-SWAP → BTCUSDT
                new_prices[inst_id] = last  # e.g. "BTC-USDT-SWAP"
                if "-USDT-SWAP" in inst_id:
                    base = inst_id.replace("-USDT-SWAP", "")
                    new_prices[f"{base}USDT"] = last
            _futures_ticker_prices = new_prices
            _futures_ticker_loaded_at = now
        except Exception:
            pass
        return dict(_futures_ticker_prices)

    def get_klines(
        self, pair: str, granularity: str = "1day", limit: int = 200
    ) -> list:
        bar = self.GRANULARITY_MAP.get(granularity, granularity)
        url = f"{BASE}/api/v5/market/candles?instId={pair}&bar={bar}&limit={limit}"
        try:
            data = http_get_json(url)
            rows = data.get("data", [])
            return list(reversed(rows))
        except Exception:
            return []

    def fetch_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> Optional[dict[str, Any]]:
        now = time.time()
        cached = _symbol_rules_cache.get(pair)
        if cached and (now - cached[0]) < cache_sec:
            return dict(cached[1])
        url = f"{BASE}/api/v5/public/instruments?instType=SPOT&instId={pair}"
        try:
            payload = http_get_json(url)
        except Exception:
            return dict(cached[1]) if cached else None
        rows = payload.get("data", [])
        if not rows:
            return dict(cached[1]) if cached else None
        info = rows[0]
        lot_sz = float(info.get("lotSz", "0.00000001"))
        min_sz = float(info.get("minSz", "0"))
        tick_sz = float(info.get("tickSz", "0.01"))
        qty_prec = _sz_precision(lot_sz)
        quote_prec = _sz_precision(tick_sz) or 2
        rules = {
            "symbol": pair,
            "min_trade_usdt": 0,
            "min_trade_base": max(min_sz, lot_sz),
            "quantity_precision": qty_prec,
            "quote_precision": quote_prec,
            "status": "live" if info.get("state") == "live" else "",
        }
        _symbol_rules_cache[pair] = (now, rules)
        return dict(rules)

    def fetch_futures_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        now = time.time()
        cached = _futures_rules_cache.get(pair)
        if cached and (now - cached[0]) < cache_sec:
            return dict(cached[1])
        asset = pair.replace("USDT", "") if pair.endswith("USDT") else pair
        swap = _swap_inst_id(asset)
        url = f"{BASE}/api/v5/public/instruments?instType=SWAP&instId={swap}"
        try:
            payload = http_get_json(url)
        except Exception:
            return dict(cached[1]) if cached else None
        rows = payload.get("data", [])
        if not rows:
            return dict(cached[1]) if cached else None
        info = rows[0]
        lot_sz = float(info.get("lotSz", "1"))
        min_sz = float(info.get("minSz", "1"))
        ct_val = float(info.get("ctVal", "1"))
        tick_sz = float(info.get("tickSz", "0.01"))
        qty_prec = _sz_precision(lot_sz)
        rules = {
            "symbol": pair,
            "min_trade_usdt": 0,
            "min_trade_base": max(min_sz * ct_val, lot_sz),
            "quantity_precision": qty_prec,
            "quote_precision": _sz_precision(tick_sz) or 2,
            "ct_val": ct_val,
            "status": "live" if info.get("state") == "live" else "",
        }
        _futures_rules_cache[pair] = (now, rules)
        return dict(rules)

    def _get_account_config(self, cache_sec: int = 300) -> dict[str, Any]:
        """GET /api/v5/account/config — acctLv / autoLoan / enableSpotBorrow 等（带 TTL 缓存）。"""
        global _acct_config_cache
        now = time.time()
        if _acct_config_cache and (now - _acct_config_cache[0]) < cache_sec:
            return dict(_acct_config_cache[1])
        try:
            data = _api_call("GET", "/api/v5/account/config")
            rows = data.get("data") or []
            cfg = rows[0] if rows else {}
        except Exception:
            return {}
        if cfg:
            _acct_config_cache = (now, cfg)
        return dict(cfg)

    def _is_unified_margin_account(self, cfg: dict[str, Any] | None = None) -> bool:
        """多币种/组合保证金模式下 spot+margin+derivatives 共用交易账户，无需独立划转。"""
        cfg = cfg if cfg is not None else self._get_account_config()
        return str(cfg.get("acctLv", "")) in ("3", "4")

    def transfer_asset(
        self, asset: str, amount: float, from_account: str, to_account: str
    ) -> bool:
        # OKX 各账户模式下 margin 都在交易账户（18）内，spot↔margin 无需划转
        if "margin" in (from_account, to_account):
            return True

        if from_account == "spot" and to_account == "futures":
            okx_from, okx_to = "18", "27"
        elif from_account == "futures" and to_account == "spot":
            okx_from, okx_to = "27", "18"
        else:
            return False
        try:
            _api_call(
                "POST",
                "/api/v5/asset/transfer",
                body={
                    "from": okx_from,
                    "to": okx_to,
                    "currency": asset.upper(),
                    "amount": f"{amount:.8f}".rstrip("0").rstrip("."),
                },
            )
            return True
        except Exception:
            return False

    def fetch_asset_market(
        self, asset: str, quote: str = "USDT", cfg: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        pair = _inst_id(asset, quote)
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
            parse_kline_ohlcv(k)
            for k in self.get_klines(pair, tf["slow"]["interval"], tf["slow"]["limit"])
            if k
        ]
        klines_4h = [
            parse_kline_ohlcv(k)
            for k in self.get_klines(pair, tf["mid"]["interval"], tf["mid"]["limit"])
            if k
        ]
        klines_1w = [
            parse_kline_ohlcv(k)
            for k in self.get_klines(
                pair, tf["macro"]["interval"], tf["macro"]["limit"]
            )
            if k
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
        balances: dict[str, float] = {c: 0.0 for c in coins}
        data = _api_call("GET", "/api/v5/account/balance", params={"ccy": ""})
        for detail in data.get("data", []):
            for item in detail.get("details", []):
                coin = str(item.get("ccy", "")).upper()
                if coin in balances:
                    balances[coin] = float(item.get("availBal", item.get("cashBal", 0)))
        return balances

    def fetch_live_state(self, assets: list[str]) -> dict[str, Any]:
        balances = self.fetch_balances(assets)

        positions: dict[str, dict[str, Any]] = {}
        try:
            pos_data = _api_call("GET", "/api/v5/account/positions")
            for pos in pos_data.get("data", []):
                inst = str(pos.get("instId", ""))
                if not inst.endswith("-SWAP"):
                    continue
                base = inst.split("-")[0]
                amt = float(pos.get("pos", 0))
                if abs(amt) < 1e-9:
                    continue
                positions[base] = {
                    "amount": abs(amt),
                    "side": "long" if amt > 0 else "short",
                    "entry_price": float(pos.get("avgPx", 0) or 0),
                    "unrealized_pnl": float(pos.get("upl", 0) or 0),
                    "leverage": float(pos.get("lever", 1) or 1),
                }
        except Exception:
            pass

        return {"balances": balances, "futures_positions": positions}

    def fetch_futures_positions(self, quote: str = "USDT") -> list[dict[str, Any]]:
        """USDT 永续持仓列表（单端点，失败抛异常）。"""
        pos_data = _api_call("GET", "/api/v5/account/positions")
        quote_u = quote.upper()
        out: list[dict[str, Any]] = []
        for pos in pos_data.get("data", []) or []:
            inst = str(pos.get("instId", ""))
            if not inst.endswith(f"-{quote_u}-SWAP"):
                continue
            amt = float(pos.get("pos", 0) or 0)
            if abs(amt) <= 1e-12:
                continue
            base = inst.split("-")[0]
            out.append(
                {
                    "symbol": f"{base}{quote_u}",
                    "side": "long" if amt > 0 else "short",
                    "qty": abs(amt),
                    "entry_price": float(pos.get("avgPx", 0) or 0),
                    "liq_price": float(pos.get("liqPx", 0) or 0),
                    "leverage": float(pos.get("lever", 1) or 1),
                    "unrealized_pnl": float(pos.get("upl", 0) or 0),
                }
            )
        return out

    def initialize_futures_symbol(self, pair: str) -> None:
        if pair in _initialized_symbols:
            return
        asset = pair.replace("USDT", "") if pair.endswith("USDT") else pair
        swap = _swap_inst_id(asset)
        try:
            _api_call(
                "POST",
                "/api/v5/account/set-position-mode",
                body={"posMode": "net_mode"},
            )
        except Exception:
            pass
        try:
            _api_call(
                "POST",
                "/api/v5/account/set-leverage",
                body={
                    "instId": swap,
                    "lever": "1",
                    "mgnMode": "isolated",
                },
            )
        except Exception:
            pass
        _initialized_symbols.add(pair)

    # ── cross margin（Reverse C&C：借币卖出 / 买回还币） ──────────────────────

    def _quote_ccy(self, pair: str) -> str:
        if "-" in pair:
            return pair.split("-")[-1].upper()
        if pair.endswith("USDT"):
            return "USDT"
        return "USDT"

    def _base_ccy(self, pair: str) -> str:
        if "-" in pair:
            return pair.split("-")[0].upper()
        if pair.endswith("USDT"):
            return pair[:-4].upper()
        return pair.upper()

    def _can_borrow_for_sell(self, pair: str) -> bool:
        """合约模式：交易对级 cross margin，max-loan side=sell 可借则支持 reverse。"""
        try:
            data = _api_call(
                "GET",
                "/api/v5/account/max-loan",
                params={
                    "instId": pair,
                    "mgnMode": "cross",
                    "mgnCcy": self._quote_ccy(pair),
                },
            )
            base = self._base_ccy(pair)
            for row in data.get("data", []):
                if str(row.get("ccy", "")).upper() != base:
                    continue
                if str(row.get("side", "")).lower() == "sell":
                    return float(row.get("maxLoan", 0) or 0) > 0
        except Exception:
            return False
        return False

    def supports_reverse_arbitrage(self) -> bool:
        """Reverse 现货腿能力：按账户实测 max-loan / 借币开关，不单靠 acctLv 推断。

        合约模式 acctLv=2：交易对级 cross margin 下单隐式借币（与网页杠杆一致）；
        多币种 acctLv=3/4：账户级 autoLoan oversell；Spot+enableSpotBorrow 亦支持。
        无 API 密钥时假定代码路径可用（供 paper/dry-run 扫描）。
        """
        if not (_get_key() and _get_secret()):
            return True
        cfg = self._get_account_config()
        if not cfg:
            return False
        acct_lv = str(cfg.get("acctLv", ""))
        if acct_lv in ("3", "4"):
            return True
        if acct_lv == "1" and cfg.get("enableSpotBorrow"):
            return True
        if acct_lv == "2":
            return self._can_borrow_for_sell(_inst_id("ETH", "USDT"))
        return False

    def _ensure_margin_auto_flags(self, side_effect: str) -> None:
        """多币种/组合保证金：下单前打开 autoLoan/autoRepay。合约模式走手动借还或 ccy 全仓单。"""
        cfg = self._get_account_config()
        if str(cfg.get("acctLv", "")) not in ("3", "4"):
            return
        se = side_effect.lower()
        try:
            if se == "auto_borrow":
                _api_call(
                    "POST", "/api/v5/account/set-auto-loan", body={"autoLoan": True}
                )
            elif se == "auto_repay":
                _api_call(
                    "POST", "/api/v5/account/set-auto-repay", body={"autoRepay": True}
                )
        except Exception:
            pass

    def fetch_margin_debt(self, assets: list[str]) -> dict[str, float]:
        """各币种负债：balance.liab（Spot/多币种/组合）+ MARGIN 持仓 liab（合约模式）。"""
        debt: dict[str, float] = {a.upper(): 0.0 for a in assets}
        try:
            data = _api_call("GET", "/api/v5/account/balance", params={"ccy": ""})
            for acct in data.get("data", []):
                for item in acct.get("details", []):
                    coin = str(item.get("ccy", "")).upper()
                    if coin in debt:
                        debt[coin] = abs(float(item.get("liab", 0) or 0))
        except Exception as e:
            print(f"okx fetch_margin_debt balance failed: {e}", file=sys.stderr)
        # 合约模式下杠杆负债挂在 MARGIN 持仓的 liab/liabCcy 上
        try:
            pos = _api_call(
                "GET", "/api/v5/account/positions", params={"instType": "MARGIN"}
            )
            for p in pos.get("data", []):
                coin = str(p.get("liabCcy", "")).upper()
                liab = abs(float(p.get("liab", 0) or 0))
                if coin in debt:
                    debt[coin] += liab
        except Exception as e:
            print(f"okx fetch_margin_debt positions failed: {e}", file=sys.stderr)
        return debt

    def _margin_borrow_repay(self, asset: str, amount: float, side: str) -> bool:
        """POST /api/v5/account/spot-manual-borrow-repay（官方仅 Spot mode 开通借币时适用）。"""
        try:
            _api_call(
                "POST",
                "/api/v5/account/spot-manual-borrow-repay",
                body={
                    "ccy": asset.upper(),
                    "side": side,
                    "amt": f"{amount:.8f}".rstrip("0").rstrip("."),
                },
            )
            return True
        except Exception as e:
            print(f"okx margin {side} {asset} failed: {e}", file=sys.stderr)
            return False

    def margin_borrow(self, asset: str, amount: float) -> bool:
        cfg = self._get_account_config()
        lv = str(cfg.get("acctLv", ""))
        if lv in ("3", "4"):
            # 多币种/组合保证金：借币在下单时由 autoLoan 触发
            self._ensure_margin_auto_flags("auto_borrow")
            return True
        if lv == "2":
            # 合约模式：tdMode=cross 下单时隐式借币，无需也不能手动借
            return True
        return self._margin_borrow_repay(asset, amount, "borrow")

    def margin_repay(self, asset: str, amount: float) -> bool:
        cfg = self._get_account_config()
        lv = str(cfg.get("acctLv", ""))
        if lv in ("3", "4"):
            self._ensure_margin_auto_flags("auto_repay")
            return True
        if lv == "2":
            # 合约模式：平掉 MARGIN 仓位即自动还币还息（官方帮助文档）
            return True
        return self._margin_borrow_repay(asset, amount, "repay")

    def place_margin_order(
        self,
        pair: str,
        side: str,
        amount_base: float,
        quantity_precision: int = 6,
        ref_price: float = 0.0,
        side_effect: str = "",
    ) -> tuple[bool, dict[str, Any]]:
        """Cross margin 单（tdMode=cross）。

        acctLv=2 合约模式：需传 ccy=保证金币；sell 下单时隐式借币，buy 平仓自动还币还息。
        acctLv=3/4：依赖 set-auto-loan / set-auto-repay。
        sell 用市价单（sz=base）；buy 用 IOC 限价单（px 上浮 1%），因为
        MARGIN 市价买的 sz 单位歧义且 tgtCcy 仅适用于 SPOT 市价单。
        """
        cfg = self._get_account_config()
        acct_lv = str(cfg.get("acctLv", ""))
        se = side_effect.lower()
        if se and acct_lv in ("3", "4"):
            self._ensure_margin_auto_flags(side_effect)

        client_oid = f"qmgn{int(time.time())}{random.randint(0, 9999)}"
        sz = f"{amount_base:.{quantity_precision}f}".rstrip("0").rstrip(".")
        body: dict[str, Any] = {
            "instId": pair,
            "tdMode": "cross",
            "side": side,
            "ordType": "market",
            "sz": sz or f"{amount_base:.{quantity_precision}f}",
            "clOrdId": client_oid,
        }
        if acct_lv == "2":
            body["ccy"] = self._quote_ccy(pair)
        if side == "buy":
            # IOC 限价上浮 1%：精确控制 base 数量，等效市价成交
            px_ref = ref_price if ref_price and ref_price > 0 else self.get_ticker(pair)
            if not px_ref or px_ref <= 0:
                return False, {
                    "error": "okx margin buy requires ref_price for IOC limit"
                }
            rules = self.fetch_symbol_rules(pair) or {}
            qp = int(rules.get("quote_precision", 4))
            body["ordType"] = "ioc"
            body["px"] = f"{px_ref * 1.01:.{qp}f}"
            if se == "auto_repay" and acct_lv in ("2", "3"):
                body["reduceOnly"] = True
        submit_ts = time.time()
        try:
            result = _api_call("POST", "/api/v5/trade/order", body=body)
            fill_ts = time.time()
            order_id = result.get("data", [{}])[0].get("ordId", "?")
            time.sleep(0.3)
            detail = _api_call(
                "GET", "/api/v5/trade/order", params={"instId": pair, "ordId": order_id}
            )
            od = detail.get("data", [{}])[0]
            state = str(od.get("state", ""))
            exec_price = float(od.get("avgPx", 0) or ref_price)
            fill_sz = float(od.get("accFillSz", 0) or od.get("fillSz", 0) or 0)
            if state == "filled" and fill_sz <= 0:
                fill_sz = amount_base
            if fill_sz <= 0:
                return False, {
                    "error": f"okx margin {side} {pair} not filled (state={state or '?'})",
                    "order_id": order_id,
                    "order_status": state,
                }
            exec_qty = fill_sz
            exec_quote = exec_qty * exec_price
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
                "order_status": state,
            }
        except Exception as e:
            return False, {"error": str(e)}

    def fetch_borrow_rates(self, coins: list[str]) -> dict[str, float]:
        """Fetch annualized borrow rates (decimal). 公共批量端点，rate 为日利率。"""
        rates: dict[str, float] = {c: 0.0 for c in coins}
        try:
            data = _api_call("GET", "/api/v5/public/interest-rate-loan-quota")
            rows = (data.get("data") or [{}])[0].get("basic", [])
            for item in rows:
                coin = str(item.get("ccy", "")).upper()
                if coin in rates:
                    rates[coin] = float(item.get("rate", 0) or 0) * 365
        except Exception:
            pass
        return rates

    def place_buy(
        self,
        pair: str,
        amount_usdt: float,
        quote_precision: int = 2,
        ref_price: float = 0.0,
    ) -> tuple[bool, dict[str, Any]]:
        client_oid = f"qbuy{int(time.time())}{random.randint(0, 9999)}"
        sz = f"{amount_usdt:.{quote_precision}f}"
        submit_ts = time.time()
        try:
            result = _api_call(
                "POST",
                "/api/v5/trade/order",
                body={
                    "instId": pair,
                    "tdMode": "cash",
                    "side": "buy",
                    "ordType": "market",
                    "tgtCcy": "quote_ccy",
                    "sz": sz,
                    "clOrdId": client_oid,
                },
            )
            fill_ts = time.time()
            order_id = result.get("data", [{}])[0].get("ordId", "?")
            time.sleep(0.3)
            detail = _api_call(
                "GET", "/api/v5/trade/order", params={"instId": pair, "ordId": order_id}
            )
            od = detail.get("data", [{}])[0]
            exec_price = float(od.get("avgPx", 0) or ref_price)
            exec_quote = float(od.get("fillCxqFee", od.get("fillSzQuote", sz)))
            exec_qty = exec_quote / exec_price if exec_price > 0 else 0
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
                "order_status": od.get("state", ""),
            }
        except Exception as e:
            return False, {"error": str(e)}

    def place_sell(
        self,
        pair: str,
        amount_base: float,
        quantity_precision: int = 6,
        ref_price: float = 0.0,
    ) -> tuple[bool, dict[str, Any]]:
        client_oid = f"qsell{int(time.time())}{random.randint(0, 9999)}"
        sz = f"{amount_base:.{quantity_precision}f}".rstrip("0").rstrip(".")
        submit_ts = time.time()
        try:
            result = _api_call(
                "POST",
                "/api/v5/trade/order",
                body={
                    "instId": pair,
                    "tdMode": "cash",
                    "side": "sell",
                    "ordType": "market",
                    "sz": sz,
                    "clOrdId": client_oid,
                },
            )
            fill_ts = time.time()
            order_id = result.get("data", [{}])[0].get("ordId", "?")
            time.sleep(0.3)
            detail = _api_call(
                "GET", "/api/v5/trade/order", params={"instId": pair, "ordId": order_id}
            )
            od = detail.get("data", [{}])[0]
            exec_price = float(od.get("avgPx", 0) or ref_price)
            exec_qty = float(od.get("fillSz", 0) or amount_base)
            exec_quote = exec_qty * exec_price
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
                "order_status": od.get("state", ""),
            }
        except Exception as e:
            return False, {"error": str(e)}

    def place_futures_order(
        self,
        pair: str,
        side: str,
        amount_base: float,
        quantity_precision: int = 3,
        ref_price: float = 0.0,
    ) -> tuple[bool, dict[str, Any]]:
        asset = pair.replace("USDT", "") if pair.endswith("USDT") else pair
        swap = _swap_inst_id(asset)
        client_oid = f"qfut{int(time.time())}{random.randint(0, 9999)}"
        sz = f"{amount_base:.{quantity_precision}f}".rstrip("0").rstrip(".")
        submit_ts = time.time()

        self.initialize_futures_symbol(pair)

        okx_side = "buy" if side in ("open_long", "close_short") else "sell"
        try:
            result = _api_call(
                "POST",
                "/api/v5/trade/order",
                body={
                    "instId": swap,
                    "tdMode": "isolated",
                    "side": okx_side,
                    "ordType": "market",
                    "sz": sz,
                    "clOrdId": client_oid,
                },
            )
            fill_ts = time.time()
            order_id = result.get("data", [{}])[0].get("ordId", "?")

            exec_price = ref_price
            try:
                time.sleep(0.5)
                detail = _api_call(
                    "GET",
                    "/api/v5/trade/order",
                    params={"instId": swap, "ordId": order_id},
                )
                od = detail.get("data", [{}])[0]
                exec_price = float(od.get("avgPx", 0) or ref_price)
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
                "latency_ms": round((fill_ts - submit_ts) * 1000),
                "order_status": "filled",
            }
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
            pair = str(mkt.get("pair") or _inst_id(symbol, "USDT"))
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
            elif trade["type"] in (
                "open_short",
                "close_long",
                "close_short",
                "open_long",
            ):
                ok, detail = self.place_futures_order(
                    pair,
                    trade["type"],
                    trade["amount_base"],
                    int(
                        trade.get("quantity_precision")
                        or mkt.get("quantity_precision", 3)
                    ),
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
