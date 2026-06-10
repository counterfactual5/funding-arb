#!/usr/bin/env python3
"""Per-symbol USDT-M perpetual fee rates from exchange account APIs.

Rates are returned as **percentage points** (0.06 = 0.06%, not 0.0006).
Falls back to venue defaults when API is unavailable.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from market.parallel_fetch import run_io_parallel

# Venue-level VIP0 defaults (percentage points) — fallback only
DEFAULT_TAKER_PCT: dict[str, float] = {
    "bitget": 0.06,
    "binance": 0.05,
    "okx": 0.05,
    "bybit": 0.055,
    "hyperliquid": 0.035,
}

DEFAULT_MAKER_PCT: dict[str, float] = {
    "bitget": 0.02,
    "binance": 0.02,
    "okx": 0.02,
    "bybit": 0.02,
    "hyperliquid": 0.01,
}

# Backward-compatible alias used by scanners / history backfill
FUTURES_TAKER_FEE_PCT = dict(DEFAULT_TAKER_PCT)

_CACHE: dict[tuple[str, str], tuple[float, dict[str, float]]] = {}
_CACHE_TTL_SEC = 3600


def normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("-USDT-SWAP", "USDT").replace("-USDT", "USDT")


def _decimal_to_pct(rate: float | str) -> float:
    v = abs(float(rate))
    if v < 0.01:
        return v * 100.0
    return v


def _okx_inst_family(symbol: str) -> str:
    sym = normalize_symbol(symbol)
    if sym.endswith("USDT"):
        return f"{sym[:-4]}-USDT"
    return sym


def _fetch_bitget(symbol: str) -> dict[str, float]:
    from venues.bitget import _api_call

    r = _api_call(
        "GET",
        "/api/v2/common/trade-rate",
        params={"symbol": normalize_symbol(symbol), "businessType": "mix"},
    )
    d = r.get("data") or {}
    if isinstance(d, list) and d:
        d = d[0]
    return {
        "taker_pct": _decimal_to_pct(d.get("takerFeeRate", 0.0006)),
        "maker_pct": _decimal_to_pct(d.get("makerFeeRate", 0.0002)),
    }


def _fetch_bybit(symbol: str) -> dict[str, float]:
    from venues.bybit import _api_call

    r = _api_call(
        "GET",
        "/v5/account/fee-rate",
        params={"category": "linear", "symbol": normalize_symbol(symbol)},
    )
    lst = r.get("result", {}).get("list") or [{}]
    d = lst[0]
    return {
        "taker_pct": _decimal_to_pct(d.get("takerFeeRate", 0.00055)),
        "maker_pct": _decimal_to_pct(d.get("makerFeeRate", 0.0002)),
    }


def _fetch_okx(symbol: str) -> dict[str, float]:
    from venues.okx import _api_call

    family = _okx_inst_family(symbol)
    try:
        r = _api_call(
            "GET",
            "/api/v5/account/trade-fee",
            params={"instType": "SWAP", "instFamily": family},
        )
    except Exception:
        r = _api_call("GET", "/api/v5/account/trade-fee", params={"instType": "SWAP"})
    d = (r.get("data") or [{}])[0]
    taker = d.get("takerU") or d.get("taker") or "0.0005"
    maker = d.get("makerU") or d.get("maker") or "0.0002"
    return {
        "taker_pct": _decimal_to_pct(taker),
        "maker_pct": _decimal_to_pct(maker),
    }


def _fetch_binance(symbol: str) -> dict[str, float]:
    from venues.binance import _api_call

    r = _api_call(
        "GET",
        "/fapi/v1/commissionRate",
        {"symbol": normalize_symbol(symbol)},
        signed=True,
    )
    return {
        "taker_pct": _decimal_to_pct(r.get("takerCommissionRate", 0.0005)),
        "maker_pct": _decimal_to_pct(r.get("makerCommissionRate", 0.0002)),
    }


_FETCHERS: dict[str, Callable[[str], dict[str, float]]] = {
    "bitget": _fetch_bitget,
    "bybit": _fetch_bybit,
    "okx": _fetch_okx,
    "binance": _fetch_binance,
}


def default_taker_pct(venue: str) -> float:
    return DEFAULT_TAKER_PCT.get(venue.lower(), 0.06)


def fetch_futures_fee_rates(
    venue: str,
    symbol: str,
    *,
    use_cache: bool = True,
) -> dict[str, float]:
    """Fetch {taker_pct, maker_pct} for one venue + symbol."""
    v = venue.lower()
    sym = normalize_symbol(symbol)
    key = (v, sym)
    now = time.time()
    if use_cache and key in _CACHE:
        ts, rates = _CACHE[key]
        if now - ts < _CACHE_TTL_SEC:
            return dict(rates)

    fetcher = _FETCHERS.get(v)
    if fetcher:
        try:
            rates = fetcher(sym)
            _CACHE[key] = (now, rates)
            return dict(rates)
        except Exception:
            pass

    return {
        "taker_pct": default_taker_pct(v),
        "maker_pct": DEFAULT_MAKER_PCT.get(v, 0.02),
    }


def prefetch_futures_fee_rates(
    pairs: list[tuple[str, str]],
    *,
    workers: int = 8,
) -> dict[tuple[str, str], dict[str, float]]:
    """Parallel prefetch. pairs = [(venue, symbol), ...]."""
    unique = list({(v.lower(), normalize_symbol(s)) for v, s in pairs})

    def _one(item: tuple[str, str]) -> tuple[tuple[str, str], dict[str, float]]:
        v, sym = item
        return (v, sym), fetch_futures_fee_rates(v, sym, use_cache=False)

    raw = run_io_parallel(unique, _one, max_workers=workers, swallow_errors=True)
    for k, v in raw.items():
        _CACHE[k] = (time.time(), v)
    # Fill missing with defaults
    out: dict[tuple[str, str], dict[str, float]] = {}
    for v, sym in unique:
        out[(v, sym)] = raw.get((v, sym)) or {
            "taker_pct": default_taker_pct(v),
            "maker_pct": DEFAULT_MAKER_PCT.get(v, 0.02),
        }
    return out


def offline_fee_cache_from_by_base(
    by_base: dict[str, dict[str, dict[str, Any]]],
) -> dict[tuple[str, str], dict[str, float]]:
    """Build venue-default fee cache without network (tests / offline backtest)."""
    out: dict[tuple[str, str], dict[str, float]] = {}
    for base, venue_map in by_base.items():
        for venue, info in venue_map.items():
            v = venue.lower()
            sym = normalize_symbol(str(info.get("symbol") or f"{base}USDT"))
            out[(v, sym)] = {
                "taker_pct": default_taker_pct(v),
                "maker_pct": DEFAULT_MAKER_PCT.get(v, 0.02),
            }
    return out


def build_fee_cache_from_by_base(
    by_base: dict[str, dict[str, dict[str, Any]]],
    *,
    workers: int = 8,
) -> dict[tuple[str, str], dict[str, float]]:
    """Build fee cache from scan_pure_futures_spreads `by_base` structure."""
    pairs: list[tuple[str, str]] = []
    for base, venue_map in by_base.items():
        for venue, info in venue_map.items():
            sym = str(info.get("symbol") or f"{base}USDT")
            pairs.append((venue, sym))
    return prefetch_futures_fee_rates(pairs, workers=workers)


def taker_fee_pct(
    venue: str,
    symbol: str,
    *,
    fee_cache: dict[tuple[str, str], dict[str, float]] | None = None,
    config_overrides: dict[str, float] | None = None,
) -> float:
    """Taker fee in percentage points for venue+symbol."""
    v = venue.lower()
    sym = normalize_symbol(symbol)
    if config_overrides and v in config_overrides:
        return float(config_overrides[v])
    if fee_cache is not None:
        cached = fee_cache.get((v, sym))
        if cached:
            return float(cached.get("taker_pct", default_taker_pct(v)))
    return fetch_futures_fee_rates(v, sym)["taker_pct"]


def pair_open_taker_fee_pct(
    long_venue: str,
    long_symbol: str,
    short_venue: str,
    short_symbol: str,
    *,
    fee_cache: dict[tuple[str, str], dict[str, float]] | None = None,
    config_overrides: dict[str, float] | None = None,
) -> tuple[float, float, float]:
    """Returns (long_fee_pct, short_fee_pct, total_open_fee_pct)."""
    long_fee = taker_fee_pct(
        long_venue, long_symbol, fee_cache=fee_cache, config_overrides=config_overrides
    )
    short_fee = taker_fee_pct(
        short_venue, short_symbol, fee_cache=fee_cache, config_overrides=config_overrides
    )
    return long_fee, short_fee, long_fee + short_fee
