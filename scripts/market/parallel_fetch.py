#!/usr/bin/env python3
"""Parallel market data fetch for live runners (I/O bound HTTP)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, TypeVar

K = TypeVar("K")
V = TypeVar("V")


def run_io_parallel(
    keys: list[K],
    fn: Callable[[K], tuple[K, V]],
    *,
    max_workers: int = 8,
    swallow_errors: bool = False,
    on_error: Callable[[K, Exception], None] | None = None,
) -> dict[K, V]:
    """Run I/O-bound ``fn(key) -> (key, value)`` concurrently."""
    if not keys:
        return {}
    workers = max(1, min(max_workers, len(keys)))
    out: dict[K, V] = {}

    if len(keys) == 1:
        try:
            k, v = fn(keys[0])
            return {k: v}
        except Exception as e:
            if swallow_errors:
                if on_error:
                    on_error(keys[0], e)
                return {}
            raise

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, key): key for key in keys}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                k, v = fut.result()
                out[k] = v
            except Exception as e:
                if swallow_errors:
                    if on_error:
                        on_error(key, e)
                    continue
                raise
    return out


def fetch_assets_market_parallel(
    venue: Any,
    assets: list[str],
    quote: str,
    cfg: dict[str, Any] | None,
    *,
    max_workers: int = 8,
    on_error: Callable[[str, Exception], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch ``fetch_asset_market`` for many assets concurrently."""
    if not assets:
        return {}

    def _one(sym: str) -> tuple[str, dict[str, Any]]:
        return sym, venue.fetch_asset_market(sym, quote, cfg)

    if on_error:
        out: dict[str, dict[str, Any]] = {}
        workers = max(1, min(max_workers, len(assets)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_one, sym): sym for sym in assets}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    s, mkt = fut.result()
                    out[s] = mkt
                except Exception as e:
                    on_error(sym, e)
        return out

    return run_io_parallel(assets, _one, max_workers=max_workers)
