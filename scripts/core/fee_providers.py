#!/usr/bin/env python3
"""Per-symbol USDT-M perpetual fee rates from exchange account APIs.

Rates are returned as **percentage points** (0.06 = 0.06%, not 0.0006).
Falls back to venue defaults when API is unavailable.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from core.vip_fee_tiers import tier_rates
from market.parallel_fetch import run_io_parallel

# Venue-level VIP0 defaults (percentage points) — fallback only
DEFAULT_TAKER_PCT: dict[str, float] = {
    "bitget": 0.06,
    "binance": 0.05,
    "okx": 0.05,
    "bybit": 0.055,
    "hyperliquid": 0.035,
    "aster": 0.035,  # Aster perp taker VIP0
    "lighter": 0.0,  # Lighter currently zero-fee (orderBookDetails taker_fee=0)
}

DEFAULT_MAKER_PCT: dict[str, float] = {
    "bitget": 0.02,
    "binance": 0.02,
    "okx": 0.02,
    "bybit": 0.02,
    "hyperliquid": 0.01,
    "aster": 0.01,
    "lighter": 0.0,
}

# Spot VIP0 defaults (percentage points) — cash-and-carry spot leg
DEFAULT_SPOT_TAKER_PCT: dict[str, float] = {
    "bitget": 0.1,
    "binance": 0.1,
    "okx": 0.1,
    "bybit": 0.1,
}

# Backward-compatible alias used by scanners / history backfill
FUTURES_TAKER_FEE_PCT = dict(DEFAULT_TAKER_PCT)

_CACHE: dict[tuple[str, str], tuple[float, dict[str, float]]] = {}
_SPOT_CACHE: dict[tuple[str, str], tuple[float, dict[str, float]]] = {}
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


def _fetch_binance_spot(symbol: str) -> dict[str, float]:
    from venues.binance import _api_call

    rows = _api_call(
        "GET",
        "/sapi/v1/asset/tradeFee",
        {"symbol": normalize_symbol(symbol)},
        signed=True,
    )
    row = rows[0] if isinstance(rows, list) and rows else {}
    return {
        "taker_pct": _decimal_to_pct(row.get("takerCommission", 0.001)),
        "maker_pct": _decimal_to_pct(row.get("makerCommission", 0.001)),
    }


def _fetch_bitget_spot(symbol: str) -> dict[str, float]:
    from venues.bitget import _api_call

    r = _api_call(
        "GET",
        "/api/v2/common/trade-rate",
        params={"symbol": normalize_symbol(symbol), "businessType": "spot"},
    )
    d = r.get("data") or {}
    if isinstance(d, list) and d:
        d = d[0]
    return {
        "taker_pct": _decimal_to_pct(d.get("takerFeeRate", 0.001)),
        "maker_pct": _decimal_to_pct(d.get("makerFeeRate", 0.001)),
    }


def _fetch_bybit_spot(symbol: str) -> dict[str, float]:
    from venues.bybit import _api_call

    r = _api_call(
        "GET",
        "/v5/account/fee-rate",
        params={"category": "spot", "symbol": normalize_symbol(symbol)},
    )
    lst = r.get("result", {}).get("list") or [{}]
    d = lst[0]
    return {
        "taker_pct": _decimal_to_pct(d.get("takerFeeRate", 0.001)),
        "maker_pct": _decimal_to_pct(d.get("makerFeeRate", 0.001)),
    }


def _fetch_okx_spot(symbol: str) -> dict[str, float]:
    from venues.okx import _api_call

    family = _okx_inst_family(symbol)
    try:
        r = _api_call(
            "GET",
            "/api/v5/account/trade-fee",
            params={"instType": "SPOT", "instFamily": family},
        )
    except Exception:
        r = _api_call("GET", "/api/v5/account/trade-fee", params={"instType": "SPOT"})
    d = (r.get("data") or [{}])[0]
    taker = d.get("takerU") or d.get("taker") or "0.001"
    maker = d.get("makerU") or d.get("maker") or "0.001"
    return {
        "taker_pct": _decimal_to_pct(taker),
        "maker_pct": _decimal_to_pct(maker),
    }


_SPOT_FETCHERS: dict[str, Callable[[str], dict[str, float]]] = {
    "bitget": _fetch_bitget_spot,
    "bybit": _fetch_bybit_spot,
    "okx": _fetch_okx_spot,
    "binance": _fetch_binance_spot,
}


def default_taker_pct(venue: str) -> float:
    return DEFAULT_TAKER_PCT.get(venue.lower(), 0.06)


def default_spot_taker_pct(venue: str) -> float:
    return DEFAULT_SPOT_TAKER_PCT.get(venue.lower(), 0.1)


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


def fetch_spot_fee_rates(
    venue: str,
    symbol: str,
    *,
    use_cache: bool = True,
) -> dict[str, float]:
    """Fetch {taker_pct, maker_pct} for one venue + spot symbol."""
    v = venue.lower()
    sym = normalize_symbol(symbol)
    key = (v, sym)
    now = time.time()
    if use_cache and key in _SPOT_CACHE:
        ts, rates = _SPOT_CACHE[key]
        if now - ts < _CACHE_TTL_SEC:
            return dict(rates)

    fetcher = _SPOT_FETCHERS.get(v)
    if fetcher:
        try:
            rates = fetcher(sym)
            _SPOT_CACHE[key] = (now, rates)
            return dict(rates)
        except Exception:
            pass

    return {
        "taker_pct": default_spot_taker_pct(v),
        "maker_pct": default_spot_taker_pct(v),
    }


def prefetch_spot_fee_rates(
    pairs: list[tuple[str, str]],
    *,
    workers: int = 8,
) -> dict[tuple[str, str], dict[str, float]]:
    """Parallel prefetch spot fee rates. pairs = [(venue, symbol), ...]."""
    unique = list({(v.lower(), normalize_symbol(s)) for v, s in pairs})

    def _one(item: tuple[str, str]) -> tuple[tuple[str, str], dict[str, float]]:
        v, sym = item
        return (v, sym), fetch_spot_fee_rates(v, sym, use_cache=False)

    raw = run_io_parallel(unique, _one, max_workers=workers, swallow_errors=True)
    for k, v in raw.items():
        _SPOT_CACHE[k] = (time.time(), v)
    out: dict[tuple[str, str], dict[str, float]] = {}
    for v, sym in unique:
        out[(v, sym)] = raw.get((v, sym)) or {
            "taker_pct": default_spot_taker_pct(v),
            "maker_pct": default_spot_taker_pct(v),
        }
    return out


def carry_two_leg_fee_pct(
    venue: str,
    symbol: str,
    *,
    futures_cache: dict[tuple[str, str], dict[str, float]] | None = None,
    spot_cache: dict[tuple[str, str], dict[str, float]] | None = None,
) -> tuple[float, float, float]:
    """Cash-and-carry open cost: (spot_pct, futures_pct, total_pct) in percentage points."""
    v = venue.lower()
    sym = normalize_symbol(symbol)
    key = (v, sym)
    if futures_cache and key in futures_cache:
        futures_pct = float(futures_cache[key].get("taker_pct", default_taker_pct(v)))
    else:
        futures_pct = float(fetch_futures_fee_rates(v, sym)["taker_pct"])
    if spot_cache and key in spot_cache:
        spot_pct = float(spot_cache[key].get("taker_pct", default_spot_taker_pct(v)))
    else:
        spot_pct = float(fetch_spot_fee_rates(v, sym)["taker_pct"])
    return spot_pct, futures_pct, spot_pct + futures_pct


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


# ---------------------------------------------------------------------------
# Fee policy: API when credentials exist, else VIP tier from settings
# ---------------------------------------------------------------------------

_VENUE_CRED_KEYS: dict[str, list[str]] = {
    "binance": [
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "BINANCE_TRADE_API_KEY",
        "BINANCE_SECRET_KEY",
    ],
    "bitget": ["BITGET_API_KEY", "BITGET_SECRET_KEY"],
    "bybit": ["BYBIT_API_KEY", "BYBIT_SECRET_KEY"],
    "okx": ["OKX_API_KEY", "OKX_SECRET_KEY"],
    "hyperliquid": ["HYPERLIQUID_API_KEY", "HYPERLIQUID_API_SECRET"],
}


def venue_has_credentials(venue: str) -> bool:
    """True when any known API key env var is set for this venue."""
    for key in _VENUE_CRED_KEYS.get(venue.lower(), []):
        if os.environ.get(key):
            return True
    return False


def parse_fee_policy(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize fee policy from strategy config."""
    cfg = cfg or {}
    tiers = cfg.get("venue_fee_tiers")
    return {
        "mode": str(cfg.get("fee_mode") or "auto"),
        "venue_tiers": dict(tiers) if isinstance(tiers, dict) else {},
    }


def venue_uses_api(venue: str, policy: dict[str, Any] | None = None) -> bool:
    """Whether this venue should fetch fees from account API."""
    policy = policy or parse_fee_policy()
    mode = policy.get("mode", "auto")
    if mode == "vip_tier":
        return False
    if mode == "api":
        return venue_has_credentials(venue)
    return venue_has_credentials(venue)


def _tier_taker_pct(venue: str, leg: str, policy: dict[str, Any]) -> float:
    tier_id = policy.get("venue_tiers", {}).get(venue.lower())
    rates = tier_rates(venue, tier_id)
    if leg == "spot":
        return float(rates["spot_taker_pct"])
    return float(rates["futures_taker_pct"])


def resolve_venue_fee(
    venue: str,
    *,
    leg: str = "futures",
    symbol: str = "BTCUSDT",
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve taker fee for one venue with source metadata."""
    policy = policy or parse_fee_policy()
    v = venue.lower()
    sym = normalize_symbol(symbol)
    if venue_uses_api(v, policy):
        try:
            if leg == "spot":
                rates = fetch_spot_fee_rates(v, sym)
            else:
                rates = fetch_futures_fee_rates(v, sym)
            return {
                "taker_pct": float(rates["taker_pct"]),
                "maker_pct": float(rates.get("maker_pct", 0.02)),
                "source": "api",
                "tier": None,
            }
        except Exception:
            pass
    tier_id = policy.get("venue_tiers", {}).get(v) or (
        "vip0" if v in ("binance", "bitget", "bybit", "okx") else "default"
    )
    taker = _tier_taker_pct(v, leg, policy)
    return {
        "taker_pct": taker,
        "maker_pct": DEFAULT_MAKER_PCT.get(v, 0.02),
        "source": "tier",
        "tier": tier_id,
    }


def build_policy_futures_cache(
    by_base: dict[str, dict[str, dict[str, Any]]],
    policy: dict[str, Any] | None = None,
    *,
    workers: int = 8,
) -> dict[tuple[str, str], dict[str, float]]:
    """Futures fee cache honoring auto API vs VIP tier per venue."""
    policy = policy or parse_fee_policy()
    cache: dict[tuple[str, str], dict[str, float]] = {}
    api_pairs: list[tuple[str, str]] = []

    for base, venue_map in by_base.items():
        for venue, info in venue_map.items():
            v = venue.lower()
            sym = normalize_symbol(str(info.get("symbol") or f"{base}USDT"))
            key = (v, sym)
            if venue_uses_api(v, policy):
                api_pairs.append(key)
            else:
                cache[key] = {
                    "taker_pct": _tier_taker_pct(v, "futures", policy),
                    "maker_pct": DEFAULT_MAKER_PCT.get(v, 0.02),
                }

    if api_pairs:
        api_cache = prefetch_futures_fee_rates(api_pairs, workers=workers)
        cache.update(api_cache)

    return cache


def build_policy_carry_caches(
    venue: str,
    symbols: list[str],
    policy: dict[str, Any] | None = None,
    *,
    workers: int = 8,
) -> tuple[dict[tuple[str, str], dict[str, float]], dict[tuple[str, str], dict[str, float]]]:
    """Spot + futures caches for cash-and-carry scanner."""
    policy = policy or parse_fee_policy()
    v = venue.lower()
    pairs = [(v, normalize_symbol(s)) for s in symbols]
    futures_cache: dict[tuple[str, str], dict[str, float]] = {}
    spot_cache: dict[tuple[str, str], dict[str, float]] = {}

    if venue_uses_api(v, policy):
        futures_cache = prefetch_futures_fee_rates(pairs, workers=workers)
        spot_cache = prefetch_spot_fee_rates(pairs, workers=workers)
    else:
        spot_taker = _tier_taker_pct(v, "spot", policy)
        futures_taker = _tier_taker_pct(v, "futures", policy)
        maker = DEFAULT_MAKER_PCT.get(v, 0.02)
        for key in pairs:
            spot_cache[key] = {"taker_pct": spot_taker, "maker_pct": spot_taker}
            futures_cache[key] = {"taker_pct": futures_taker, "maker_pct": maker}

    return futures_cache, spot_cache


def futures_config_overrides(
    venues: list[str],
    policy: dict[str, Any] | None = None,
) -> dict[str, float] | None:
    """Per-venue taker overrides for venues not using API (pair_open_taker_fee_pct)."""
    policy = policy or parse_fee_policy()
    overrides: dict[str, float] = {}
    for venue in venues:
        v = venue.lower()
        if not venue_uses_api(v, policy):
            overrides[v] = _tier_taker_pct(v, "futures", policy)
    return overrides or None
