#!/usr/bin/env python3
"""Multi-exchange borrow/lending availability and rate query (for negative funding rate reverse arbitrage)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from market.parallel_fetch import run_io_parallel

BorrowInfo = dict[str, Any]
DEFAULT_BORROW_WORKERS = 6


class BorrowProvider:
    venue_id: str = "unknown"
    max_workers: int = DEFAULT_BORROW_WORKERS

    def fetch_borrow_info(
        self, coins: list[str], *, max_workers: int | None = None
    ) -> dict[str, BorrowInfo]:
        raise NotImplementedError


def _empty_info() -> BorrowInfo:
    return {
        "borrowable": False,
        "daily_rate_pct": 0.0,
        "annual_rate_pct": 0.0,
        "max_borrow": "",
    }


def _parallel_coin_fetch(
    coins: list[str],
    fetch_one: Any,
    *,
    max_workers: int = DEFAULT_BORROW_WORKERS,
) -> dict[str, BorrowInfo]:
    uniq = list(dict.fromkeys(c.upper() for c in coins))
    out: dict[str, BorrowInfo] = {c: _empty_info() for c in uniq}
    if not uniq:
        return out

    def _wrap(coin: str) -> tuple[str, BorrowInfo]:
        return coin, fetch_one(coin)

    fetched = run_io_parallel(uniq, _wrap, max_workers=max_workers, swallow_errors=True)
    out.update(fetched)
    return out


class BitgetBorrowProvider(BorrowProvider):
    venue_id = "bitget"

    def fetch_borrow_info(
        self, coins: list[str], *, max_workers: int | None = None
    ) -> dict[str, BorrowInfo]:
        from venues.bitget import _api_call

        workers = max_workers if max_workers is not None else self.max_workers

        def _one(coin: str) -> BorrowInfo:
            try:
                data = _api_call(
                    "GET",
                    "/api/v2/margin/crossed/interest-rate-and-limit",
                    params={"coin": coin},
                )
                items = data.get("data") or []
                if not items:
                    return _empty_info()
                item = items[0]
                borrowable = bool(item.get("borrowable"))
                daily = float(item.get("dailyInterestRate", 0) or 0) * 100
                annual = float(item.get("annualInterestRate", 0) or 0) * 100
                if annual <= 0 and daily > 0:
                    annual = daily * 365
                return {
                    "borrowable": borrowable,
                    "daily_rate_pct": round(daily, 6),
                    "annual_rate_pct": round(annual, 2),
                    "max_borrow": item.get("maxBorrowableAmount", ""),
                }
            except Exception:
                return _empty_info()

        return _parallel_coin_fetch(coins, _one, max_workers=workers)


class BybitBorrowProvider(BorrowProvider):
    venue_id = "bybit"
    max_workers = 4

    def fetch_borrow_info(
        self, coins: list[str], *, max_workers: int | None = None
    ) -> dict[str, BorrowInfo]:
        from venues.bybit import _api_call

        workers = max_workers if max_workers is not None else self.max_workers

        def _one(coin: str) -> BorrowInfo:
            try:
                data = _api_call(
                    "GET",
                    "/v5/spot-margin-trade/data",
                    params={"vipLevel": "No VIP", "currency": coin},
                )
                for vip_group in data.get("result", {}).get("vipCoinList", []):
                    for item in vip_group.get("list", []):
                        if str(item.get("currency", "")).upper() != coin:
                            continue
                        borrowable = bool(item.get("borrowable"))
                        hourly = float(item.get("hourlyBorrowRate", 0) or 0)
                        daily = hourly * 24 * 100
                        annual = daily * 365
                        return {
                            "borrowable": borrowable,
                            "daily_rate_pct": round(daily, 6),
                            "annual_rate_pct": round(annual, 2),
                            "max_borrow": item.get("maxBorrowingAmount", ""),
                        }
            except Exception:
                pass
            return _empty_info()

        return _parallel_coin_fetch(coins, _one, max_workers=workers)


_reverse_capability_cache: dict[str, bool] = {}


def _venue_reverse_executable(venue: str) -> bool:
    """Probe venue reverse capability (with process-level cache: at most once per venue)."""
    if venue in _reverse_capability_cache:
        return _reverse_capability_cache[venue]
    try:
        parts = {
            "bitget": "venues.bitget.BitgetSpotVenue",
            "bybit": "venues.bybit.BybitSpotVenue",
            "okx": "venues.okx.OkxSpotVenue",
            "binance": "venues.binance.BinanceSpotVenue",
        }[venue].rsplit(".", 1)
        mod = __import__(parts[0], fromlist=[parts[1]])
        v = getattr(mod, parts[1])()
        fn = getattr(v, "supports_reverse_arbitrage", None)
        result = bool(fn()) if callable(fn) else False
    except Exception:
        result = False
    _reverse_capability_cache[venue] = result
    return result


class OkxBorrowProvider(BorrowProvider):
    venue_id = "okx"
    BASE = "https://www.okx.com"

    def _http_get(self, path: str) -> dict[str, Any]:
        url = self.BASE + path
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())

    def _margin_bases(self) -> set[str]:
        try:
            payload = self._http_get("/api/v5/public/instruments?instType=MARGIN")
            bases: set[str] = set()
            for row in payload.get("data", []):
                if str(row.get("state", "")).lower() != "live":
                    continue
                base = str(row.get("baseCcy", "")).upper()
                if base:
                    bases.add(base)
            return bases
        except Exception:
            return set()

    def _loan_rates(self) -> dict[str, tuple[float, str]]:
        """Single bulk fetch of all-coin daily rates {coin: (daily_rate_decimal, quota)}."""
        try:
            payload = self._http_get("/api/v5/public/interest-rate-loan-quota")
            data = payload.get("data") or []
            basic = data[0].get("basic", []) if data else []
            return {
                str(r.get("ccy", "")).upper(): (
                    float(r.get("rate", 0) or 0),
                    str(r.get("quota", "") or ""),
                )
                for r in basic
                if r.get("ccy")
            }
        except Exception:
            return {}

    def fetch_borrow_info(
        self, coins: list[str], *, max_workers: int | None = None
    ) -> dict[str, BorrowInfo]:
        # Both public endpoints return batch data; no per-coin parallelism needed
        margin_bases = self._margin_bases()
        loan_rates = self._loan_rates()
        okx_can_reverse = _venue_reverse_executable("okx")
        out: dict[str, BorrowInfo] = {c.upper(): _empty_info() for c in coins}
        for coin in out:
            if coin not in margin_bases:
                continue
            daily_dec, quota = loan_rates.get(coin, (0.0, ""))
            daily = daily_dec * 100
            out[coin] = {
                "borrowable": okx_can_reverse,
                "daily_rate_pct": round(daily, 6),
                "annual_rate_pct": round(daily * 365, 2),
                "max_borrow": quota,
            }
        return out


class BinanceBorrowProvider(BorrowProvider):
    venue_id = "binance"

    def fetch_borrow_info(
        self, coins: list[str], *, max_workers: int | None = None
    ) -> dict[str, BorrowInfo]:
        from venues.binance import _api_call

        wanted = {c.upper() for c in coins}
        out: dict[str, BorrowInfo] = {c.upper(): _empty_info() for c in coins}
        try:
            data = _api_call("GET", "/sapi/v1/margin/crossMarginData", signed=True)
            for item in data if isinstance(data, list) else []:
                c = str(item.get("coin", "")).upper()
                if c not in wanted:
                    continue
                daily = float(item.get("dailyInterest", 0) or 0) * 100
                annual = float(item.get("yearlyInterest", 0) or 0) * 100
                if annual <= 0 and daily > 0:
                    annual = daily * 365
                # crossMarginData includes borrowable field; default to borrowable if missing
                borrowable = bool(item.get("borrowable", True))
                out[c] = {
                    "borrowable": borrowable,
                    "daily_rate_pct": round(daily, 6),
                    "annual_rate_pct": round(annual, 2),
                    "max_borrow": item.get("borrowLimit", ""),
                }
        except Exception:
            pass
        return out


_PROVIDERS: dict[str, BorrowProvider] = {
    "binance": BinanceBorrowProvider(),
    "bitget": BitgetBorrowProvider(),
    "bybit": BybitBorrowProvider(),
    "okx": OkxBorrowProvider(),
}


def get_borrow_provider(venue: str) -> BorrowProvider:
    v = str(venue or "binance").strip().lower()
    provider = _PROVIDERS.get(v)
    if provider is None:
        raise ValueError(
            f"Unsupported borrow venue={v!r}, available: {', '.join(sorted(_PROVIDERS))}"
        )
    return provider


def borrow_cost_per_period(daily_rate_pct: float, interval_h: float) -> float:
    """Convert daily borrow rate to per-funding-period cost (percentage)."""
    if daily_rate_pct <= 0 or interval_h <= 0:
        return 0.0
    return daily_rate_pct * (interval_h / 24.0)
