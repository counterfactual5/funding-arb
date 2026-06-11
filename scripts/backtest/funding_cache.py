#!/usr/bin/env python3
"""Fetch and cache historical funding rates for futures backtests."""

from __future__ import annotations

import bisect
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.config import runs_base

CACHE_DIR = runs_base() / "cache" / "funding"


def _http_get(url: str, max_retries: int = 5) -> Any:
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            # 429/418 = rate-limited, 5xx = exchange-side fault; both should back off and retry.
            # 4xx client errors are raised directly.
            transient = e.code in (418, 429) or e.code >= 500
            if transient and attempt < max_retries - 1:
                # Binance rate limit: honor Retry-After header (seconds)
                retry_after = e.headers.get("Retry-After") if e.headers else None
                wait_sec = float(retry_after) if retry_after else 2**attempt
                time.sleep(wait_sec)
            else:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < max_retries - 1:
                wait_sec = 2**attempt
                time.sleep(wait_sec)
            else:
                raise


def fetch_funding_history(
    symbol: str, limit_per_req: int = 1000
) -> list[dict[str, Any]]:
    print(f"Fetching funding rates for {symbol}...")
    url_base = "https://fapi.binance.com/fapi/v1/fundingRate"
    all_data = []
    # Fetch from beginning (Binance futures started around 2019)
    start_time = 0

    while True:
        url = f"{url_base}?symbol={symbol}&startTime={start_time}&limit={limit_per_req}"
        # Note: exceptions are no longer swallowed here. Treating partial data as complete
        # history would "poison" the cache — next backtest would silently read a truncated
        # funding series. Better to raise and let the caller retry than cache incomplete data.
        chunk = _http_get(url)

        if not chunk:
            break

        all_data.extend(chunk)
        if len(chunk) < limit_per_req:
            break

        start_time = int(chunk[-1]["fundingTime"]) + 1
        time.sleep(0.15)

    return all_data


def cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol.upper()}_funding.json"


def load_or_fetch_funding(symbol: str, refresh: bool = False) -> list[dict[str, Any]]:
    path = cache_path(symbol)
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))

    data = fetch_funding_history(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def build_funding_timeseries(
    data: list[dict[str, Any]],
) -> tuple[list[int], list[float]]:
    """Convert raw funding data into two parallel arrays for bisect lookup."""
    timestamps = []
    rates = []
    for row in data:
        timestamps.append(int(row["fundingTime"]))
        rates.append(
            float(row["fundingRate"]) * 100
        )  # Store as percentage (e.g. 0.01 = 0.01%)
    return timestamps, rates


_DEFAULT_INTERVAL_MS = 8 * 60 * 60 * 1000  # Most Binance perpetuals settle every 8h


def fetch_funding_interval_map() -> dict[str, float]:
    """{SYMBOL: fundingIntervalHours}. fundingInfo only returns symbols with non-default intervals; the rest default to 8h."""
    try:
        info = _http_get("https://fapi.binance.com/fapi/v1/fundingInfo")
    except Exception:
        return {}
    out: dict[str, float] = {}
    for row in info if isinstance(info, list) else []:
        out[str(row.get("symbol", "")).upper()] = float(
            row.get("fundingIntervalHours", 8) or 8
        )
    return out


def fetch_all_funding() -> list[dict[str, Any]]:
    """Fetch a single-request snapshot of all USDT-margined perpetual funding rates (for screener).

    Returns [{"symbol","rate_pct","next_funding_ts","mark_price"}, ...],
    where rate_pct is the last settled rate × 100 (percentage).
    """
    data = _http_get("https://fapi.binance.com/fapi/v1/premiumIndex")
    out: list[dict[str, Any]] = []
    for row in data if isinstance(data, list) else [data]:
        out.append(
            {
                "symbol": str(row.get("symbol", "")),
                "rate_pct": float(row.get("lastFundingRate", 0.0) or 0.0) * 100,
                "next_funding_ts": int(row.get("nextFundingTime", 0) or 0),
                "mark_price": float(row.get("markPrice", 0.0) or 0.0),
            }
        )
    return out


def fetch_funding_since(
    symbol: str, start_ms: int, max_pages: int = 10
) -> list[dict[str, Any]]:
    """Fetch all settled funding rates with ``fundingTime >= start_ms`` (lightweight incremental version).

    Used by long-running runners for "cross-period settlement catch-up": when a runner
    crashes or runInterval > settlement period, it may skip multiple settlement points
    and must catch up each one individually rather than only the most recent.
    Returns 0-1 entries during normal operation.
    Returns [{"ts": int(ms), "rate_pct": float}, ...] sorted by timestamp ascending.
    """
    if start_ms <= 0:
        return []
    url_base = "https://fapi.binance.com/fapi/v1/fundingRate"
    out: list[dict[str, Any]] = []
    cursor = int(start_ms)
    for _ in range(max_pages):
        url = f"{url_base}?symbol={symbol.upper()}&startTime={cursor}&limit=1000"
        chunk = _http_get(url)
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


def fetch_current_funding(symbol: str) -> dict[str, Any]:
    """Fetch a real-time funding rate snapshot for a perpetual contract (public endpoint, no auth).

    Returns:
      {
        "rate_pct": float,            # Last settled funding rate (percentage, 0.01 = 0.01%)
        "last_settle_ts": int,        # Settlement timestamp of that rate (ms)
        "next_funding_ts": int,       # Next settlement timestamp (ms)
        "interval_ms": int,           # Settlement interval (ms), used to normalize borrow rates
        "mark_price": float,
      }
    """
    url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol.upper()}"
    data = _http_get(url)
    if isinstance(
        data, list
    ):  # Without symbol param, returns an array; defensive check
        data = data[0]
    next_ts = int(data.get("nextFundingTime", 0) or 0)
    interval_ms = _DEFAULT_INTERVAL_MS
    # Try to correct interval via fundingInfo (some symbols use 4h); fall back to 8h on failure.
    try:
        info = _http_get("https://fapi.binance.com/fapi/v1/fundingInfo")
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


def get_funding_rate_at_ts(
    ts_array: list[int], rates_array: list[float], ts: int
) -> float:
    """Get the active funding rate at a given timestamp."""
    if not ts_array:
        return 0.0
    # bisect_right finds the insertion point after the timestamp
    idx = bisect.bisect_right(ts_array, ts)
    if idx == 0:
        return 0.0  # Before any funding history
    return rates_array[idx - 1]
