#!/usr/bin/env python3
"""多交易所资金费公开数据提供者（Bitget / OKX / Bybit / Binance）。"""

from __future__ import annotations

import time
from typing import Any

from venues.http_util import http_get_json

_DEFAULT_INTERVAL_MS = 8 * 60 * 60 * 1000


def _http_get_with_retry(url: str, max_retries: int = 5) -> Any:
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            return http_get_json(url, timeout=20, retries=1)
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(2**attempt)
    raise last_err if last_err else RuntimeError("funding http failed")


class FundingProvider:
    venue_id: str = "unknown"

    def fetch_all(self, quote: str = "USDT") -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_current(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError

    def fetch_since(
        self, symbol: str, start_ms: int, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_interval_map(self, quote: str = "USDT") -> dict[str, float]:
        return {}


class BinanceFundingProvider(FundingProvider):
    venue_id = "binance"

    def fetch_all(self, quote: str = "USDT") -> list[dict[str, Any]]:
        data = _http_get_with_retry("https://fapi.binance.com/fapi/v1/premiumIndex")
        out: list[dict[str, Any]] = []
        for row in data if isinstance(data, list) else [data]:
            sym = str(row.get("symbol", ""))
            if not sym.endswith(quote.upper()):
                continue
            out.append(
                {
                    "symbol": sym,
                    "rate_pct": float(row.get("lastFundingRate", 0.0) or 0.0) * 100,
                    "next_funding_ts": int(row.get("nextFundingTime", 0) or 0),
                    "mark_price": float(row.get("markPrice", 0.0) or 0.0),
                }
            )
        return out

    def fetch_current(self, symbol: str) -> dict[str, Any]:
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol.upper()}"
        data = _http_get_with_retry(url)
        if isinstance(data, list):
            data = data[0]
        next_ts = int(data.get("nextFundingTime", 0) or 0)
        interval_ms = _DEFAULT_INTERVAL_MS
        try:
            info = _http_get_with_retry("https://fapi.binance.com/fapi/v1/fundingInfo")
            for row in info if isinstance(info, list) else []:
                if str(row.get("symbol", "")).upper() == symbol.upper():
                    hrs = float(row.get("fundingIntervalHours", 8) or 8)
                    interval_ms = int(hrs * 60 * 60 * 1000)
                    break
        except Exception:
            pass
        last_settle_ts = next_ts - interval_ms if next_ts else 0
        return {
            "rate_pct": float(data.get("lastFundingRate", 0.0) or 0.0) * 100,
            "last_settle_ts": last_settle_ts,
            "next_funding_ts": next_ts,
            "interval_ms": interval_ms,
            "mark_price": float(data.get("markPrice", 0.0) or 0.0),
        }

    def fetch_since(
        self, symbol: str, start_ms: int, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        if start_ms <= 0:
            return []
        out: list[dict[str, Any]] = []
        cursor = int(start_ms)
        url_base = "https://fapi.binance.com/fapi/v1/fundingRate"
        for _ in range(max_pages):
            url = f"{url_base}?symbol={symbol.upper()}&startTime={cursor}&limit=1000"
            chunk = _http_get_with_retry(url)
            if not chunk:
                break
            for row in chunk:
                out.append(
                    {
                        "ts": int(row["fundingTime"]),
                        "rate_pct": float(row["fundingRate"]) * 100,
                    }
                )
            if len(chunk) < 1000:
                break
            cursor = int(chunk[-1]["fundingTime"]) + 1
            time.sleep(0.15)
        return out

    def fetch_interval_map(self, quote: str = "USDT") -> dict[str, float]:
        try:
            info = _http_get_with_retry("https://fapi.binance.com/fapi/v1/fundingInfo")
        except Exception:
            return {}
        if not isinstance(info, list):
            return {}
        return {
            str(row.get("symbol", "")).upper(): float(
                row.get("fundingIntervalHours", 8) or 8
            )
            for row in info
        }


class BitgetFundingProvider(FundingProvider):
    venue_id = "bitget"
    BASE = "https://api.bitget.com"

    def fetch_all(self, quote: str = "USDT") -> list[dict[str, Any]]:
        url = f"{self.BASE}/api/v2/mix/market/tickers?productType=USDT-FUTURES"
        payload = _http_get_with_retry(url)
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        out: list[dict[str, Any]] = []
        quote_u = quote.upper()
        for row in rows:
            sym = str(row.get("symbol", "")).upper()
            if not sym.endswith(quote_u):
                continue
            rate = float(row.get("fundingRate", 0) or 0) * 100
            out.append(
                {
                    "symbol": sym,
                    "rate_pct": rate,
                    "next_funding_ts": 0,
                    "mark_price": float(
                        row.get("markPrice", row.get("lastPr", 0)) or 0
                    ),
                }
            )
        return out

    def fetch_current(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper()
        # URL-encode to handle non-ASCII symbols (e.g. meme coins with CJK names)
        import urllib.parse

        encoded_sym = urllib.parse.quote(sym, safe="")
        rate_url = (
            f"{self.BASE}/api/v2/mix/market/current-fund-rate"
            f"?symbol={encoded_sym}&productType=USDT-FUTURES"
        )
        payload = _http_get_with_retry(rate_url)
        row = (payload.get("data") or [{}])[0] if isinstance(payload, dict) else {}
        rate = float(row.get("fundingRate", 0) or 0) * 100
        interval_ms = int(float(row.get("fundingRateInterval", "8") or 8) * 3600 * 1000)
        if interval_ms <= 0:
            interval_ms = _DEFAULT_INTERVAL_MS
        # Fetch next funding time from dedicated endpoint
        next_ts = 0
        try:
            ft_url = (
                f"{self.BASE}/api/v2/mix/market/funding-time"
                f"?symbol={encoded_sym}&productType=USDT-FUTURES"
            )
            ft_payload = _http_get_with_retry(ft_url)
            ft_row = (
                (ft_payload.get("data") or [{}])[0]
                if isinstance(ft_payload, dict)
                else {}
            )
            next_ts = int(ft_row.get("nextFundingTime", 0) or 0)
        except Exception:
            pass
        last_settle_ts = next_ts - interval_ms if next_ts else 0
        return {
            "rate_pct": rate,
            "last_settle_ts": last_settle_ts,
            "next_funding_ts": next_ts,
            "interval_ms": interval_ms,
            "mark_price": float(row.get("markPrice", 0) or 0),
        }

    def fetch_since(
        self, symbol: str, start_ms: int, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        if start_ms <= 0:
            return []
        sym = symbol.upper()
        out: list[dict[str, Any]] = []
        page_no = 1
        for _ in range(max_pages):
            url = (
                f"{self.BASE}/api/v2/mix/market/history-fund-rate"
                f"?symbol={sym}&productType=USDT-FUTURES&pageSize=100&pageNo={page_no}"
            )
            payload = _http_get_with_retry(url)
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if not rows:
                break
            for row in rows:
                ts = int(row.get("fundingTime", 0) or 0)
                if ts <= start_ms:
                    continue
                out.append(
                    {
                        "ts": ts,
                        "rate_pct": float(row.get("fundingRate", 0) or 0) * 100,
                    }
                )
            if len(rows) < 100:
                break
            page_no += 1
            time.sleep(0.15)
        out.sort(key=lambda x: x["ts"])
        return out


class BybitFundingProvider(FundingProvider):
    venue_id = "bybit"
    BASE = "https://api.bybit.com"

    def fetch_all(self, quote: str = "USDT") -> list[dict[str, Any]]:
        url = f"{self.BASE}/v5/market/tickers?category=linear"
        payload = _http_get_with_retry(url)
        rows = (
            payload.get("result", {}).get("list", [])
            if isinstance(payload, dict)
            else []
        )
        out: list[dict[str, Any]] = []
        quote_u = quote.upper()
        for row in rows:
            sym = str(row.get("symbol", "")).upper()
            if not sym.endswith(quote_u):
                continue
            rate = float(row.get("fundingRate", 0) or 0) * 100
            next_ts = int(row.get("nextFundingTime", 0) or 0)
            out.append(
                {
                    "symbol": sym,
                    "rate_pct": rate,
                    "next_funding_ts": next_ts,
                    "mark_price": float(
                        row.get("markPrice", row.get("lastPrice", 0)) or 0
                    ),
                }
            )
        return out

    def fetch_current(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper()
        url = f"{self.BASE}/v5/market/tickers?category=linear&symbol={sym}"
        payload = _http_get_with_retry(url)
        row = (payload.get("result", {}).get("list") or [{}])[0]
        rate = float(row.get("fundingRate", 0) or 0) * 100
        next_ts = int(row.get("nextFundingTime", 0) or 0)
        interval_ms = _DEFAULT_INTERVAL_MS
        last_settle_ts = next_ts - interval_ms if next_ts else 0
        return {
            "rate_pct": rate,
            "last_settle_ts": last_settle_ts,
            "next_funding_ts": next_ts,
            "interval_ms": interval_ms,
            "mark_price": float(row.get("markPrice", row.get("lastPrice", 0)) or 0),
        }

    def fetch_since(
        self, symbol: str, start_ms: int, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        if start_ms <= 0:
            return []
        sym = symbol.upper()
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(max_pages):
            url = (
                f"{self.BASE}/v5/market/funding/history"
                f"?category=linear&symbol={sym}&limit=200"
            )
            if cursor:
                url += f"&cursor={cursor}"
            payload = _http_get_with_retry(url)
            result = payload.get("result", {}) if isinstance(payload, dict) else {}
            rows = result.get("list", [])
            if not rows:
                break
            for row in rows:
                ts = int(row.get("fundingRateTimestamp", 0) or 0)
                if ts <= start_ms:
                    continue
                out.append(
                    {
                        "ts": ts,
                        "rate_pct": float(row.get("fundingRate", 0) or 0) * 100,
                    }
                )
            cursor = result.get("nextPageCursor")
            if not cursor:
                break
            time.sleep(0.15)
        out.sort(key=lambda x: x["ts"])
        return out


class OkxFundingProvider(FundingProvider):
    venue_id = "okx"
    BASE = "https://www.okx.com"
    _ANY_CACHE_TTL_S = 60.0

    def __init__(self) -> None:
        self._any_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._mark_cache: tuple[float, dict[str, float]] | None = None

    def _inst_id(self, symbol: str) -> str:
        sym = symbol.upper()
        if sym.endswith("USDT"):
            base = sym[:-4]
            return f"{base}-USDT-SWAP"
        return sym

    def _fetch_any(self) -> list[dict[str, Any]]:
        """instId=ANY 一次拉全市场 funding（fetch_all/interval_map 共享 60s 缓存）。"""
        now = time.time()
        if self._any_cache and now - self._any_cache[0] < self._ANY_CACHE_TTL_S:
            return self._any_cache[1]
        url = f"{self.BASE}/api/v5/public/funding-rate?instId=ANY"
        payload = _http_get_with_retry(url)
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        self._any_cache = (now, rows)
        return rows

    def _fetch_mark_prices(self) -> dict[str, float]:
        """批量获取 OKX SWAP 标记价格（60s 缓存，与 _any_cache 同步）。"""
        now = time.time()
        if self._mark_cache and now - self._mark_cache[0] < 60.0:
            return self._mark_cache[1]
        try:
            payload = _http_get_with_retry(
                "https://www.okx.com/api/v5/public/mark-price?instType=SWAP"
            )
            mp: dict[str, float] = {}
            for row in payload.get("data", []) if isinstance(payload, dict) else []:
                inst = str(row.get("instId", ""))
                px = float(row.get("markPx", 0) or 0)
                if inst and px > 0:
                    mp[inst] = px
            self._mark_cache = (now, mp)
            return mp
        except Exception:
            return {}

    def fetch_all(self, quote: str = "USDT") -> list[dict[str, Any]]:
        """批量端点 instId=ANY：1 次请求替代逐币并行（~400 合约 <1s）。"""
        quote_u = quote.upper()
        mp_map = self._fetch_mark_prices()
        out: list[dict[str, Any]] = []
        for row in self._fetch_any():
            inst = str(row.get("instId", ""))
            if not inst.endswith(f"-{quote_u}-SWAP"):
                continue
            base = inst.split("-")[0]
            swap_inst = f"{base}-USDT-SWAP"
            out.append(
                {
                    "symbol": f"{base}{quote_u}",
                    # fundingRate = 当期费率，将于 fundingTime 结算
                    "rate_pct": float(row.get("fundingRate", 0) or 0) * 100,
                    "next_funding_ts": int(row.get("fundingTime", 0) or 0),
                    "mark_price": float(mp_map.get(swap_inst, 0.0)),
                }
            )
        return out

    def fetch_interval_map(self, quote: str = "USDT") -> dict[str, float]:
        """从 ANY 响应的 fundingTime/nextFundingTime 差推断各币结算周期。"""
        quote_u = quote.upper()
        out: dict[str, float] = {}
        for row in self._fetch_any():
            inst = str(row.get("instId", ""))
            if not inst.endswith(f"-{quote_u}-SWAP"):
                continue
            ft = int(row.get("fundingTime", 0) or 0)
            nft = int(row.get("nextFundingTime", 0) or 0)
            if ft > 0 and nft > ft:
                base = inst.split("-")[0]
                out[f"{base}{quote_u}"] = round((nft - ft) / 3600000.0, 2)
        return out

    def fetch_current(self, symbol: str) -> dict[str, Any]:
        inst = self._inst_id(symbol)
        url = f"{self.BASE}/api/v5/public/funding-rate?instId={inst}"
        payload = _http_get_with_retry(url)
        row = (payload.get("data") or [{}])[0]
        rate = float(row.get("fundingRate", 0) or 0) * 100
        next_ts = int(row.get("nextFundingTime", 0) or 0)
        interval_ms = int(float(row.get("fundingInterval", "8") or 8) * 3600 * 1000)
        if interval_ms <= 0:
            interval_ms = _DEFAULT_INTERVAL_MS
        last_settle_ts = next_ts - interval_ms if next_ts else 0
        mark_price = 0.0
        try:
            mp_url = f"{self.BASE}/api/v5/public/mark-price?instId={inst}&instType=SWAP"
            mp_payload = _http_get_with_retry(mp_url)
            mp_rows = mp_payload.get("data", [])
            if mp_rows:
                mark_price = float(mp_rows[0].get("markPx", 0) or 0)
        except Exception:
            pass
        return {
            "rate_pct": rate,
            "last_settle_ts": last_settle_ts,
            "next_funding_ts": next_ts,
            "interval_ms": interval_ms,
            "mark_price": mark_price,
        }

    def fetch_since(
        self, symbol: str, start_ms: int, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        if start_ms <= 0:
            return []
        inst = self._inst_id(symbol)
        out: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(max_pages):
            url = f"{self.BASE}/api/v5/public/funding-rate-history?instId={inst}&limit=100"
            if after:
                url += f"&after={after}"
            payload = _http_get_with_retry(url)
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if not rows:
                break
            for row in rows:
                ts = int(row.get("fundingTime", 0) or 0)
                if ts <= start_ms:
                    continue
                out.append(
                    {
                        "ts": ts,
                        "rate_pct": float(
                            row.get("realizedRate", row.get("fundingRate", 0)) or 0
                        )
                        * 100,
                    }
                )
            if len(rows) < 100:
                break
            after = str(rows[-1].get("fundingTime", ""))
            time.sleep(0.15)
        out.sort(key=lambda x: x["ts"])
        return out


_PROVIDERS: dict[str, FundingProvider] = {
    "binance": BinanceFundingProvider(),
    "bitget": BitgetFundingProvider(),
    "bybit": BybitFundingProvider(),
    "okx": OkxFundingProvider(),
}


def get_funding_provider(venue: str) -> FundingProvider:
    v = str(venue or "binance").strip().lower()
    provider = _PROVIDERS.get(v)
    if provider is None:
        raise ValueError(
            f"不支持的 funding venue={v!r}，可选: {', '.join(sorted(_PROVIDERS))}"
        )
    return provider
