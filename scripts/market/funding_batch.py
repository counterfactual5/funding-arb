#!/usr/bin/env python3
"""Batch / parallel funding-rate snapshot helpers."""

from __future__ import annotations

from typing import Any, Callable

from market.parallel_fetch import run_io_parallel
from venues.base import make_pair


def _snap_from_all_row(
    row: dict[str, Any], quote: str, interval_map: dict[str, float]
) -> dict[str, Any]:
    sym = str(row.get("symbol", "")).upper()
    interval_h = float(interval_map.get(sym, 8.0) or 8.0)
    interval_ms = int(interval_h * 3600 * 1000)
    next_ts = int(row.get("next_funding_ts", 0) or 0)
    return {
        "rate_pct": float(row.get("rate_pct", 0.0) or 0.0),
        "next_funding_ts": next_ts,
        "last_settle_ts": next_ts - interval_ms if next_ts else 0,
        "interval_ms": interval_ms,
        "mark_price": float(row.get("mark_price", 0.0) or 0.0),
    }


def fetch_funding_snaps_for_assets(
    fp: Any | None,
    assets: list[str],
    quote: str,
    *,
    workers: int = 8,
    fetch_current_fn: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build {asset: funding_snap} with one ``fetch_all`` when possible.

    Assets missing from bulk payload (or needing backfill e.g. Bitget next_ts=0)
    fall back to parallel ``fetch_current`` per symbol.
    """
    if not assets:
        return {}
    quote_u = quote.upper()
    snaps: dict[str, dict[str, Any]] = {}
    row_by_asset: dict[str, dict[str, Any]] = {}

    if fp is not None:
        try:
            rows = fp.fetch_all(quote_u)
            imap = fp.fetch_interval_map(quote_u)
            for row in rows:
                sym = str(row.get("symbol", "")).upper()
                if not sym.endswith(quote_u):
                    continue
                asset = sym[: -len(quote_u)]
                if asset:
                    row_by_asset[asset] = row
                    snaps[asset] = _snap_from_all_row(row, quote_u, imap)
        except Exception:
            pass

    need_current = [a for a in assets if a not in snaps]
    # Bitget fetch_all 常缺 next_funding_ts，需 per-symbol 补全
    need_current.extend(
        a
        for a in assets
        if a in snaps and int(snaps[a].get("next_funding_ts", 0) or 0) == 0
    )
    need_current = list(dict.fromkeys(need_current))

    if need_current and (fp is not None or fetch_current_fn is not None):

        def _fetch_one(asset: str) -> tuple[str, dict[str, Any]]:
            if fp is not None:
                return asset, fp.fetch_current(make_pair(asset, quote))
            assert fetch_current_fn is not None
            return asset, fetch_current_fn(asset)

        for asset, snap in run_io_parallel(
            need_current, _fetch_one, max_workers=workers, swallow_errors=True
        ).items():
            if snap:
                snaps[asset] = snap

    return {a: snaps[a] for a in assets if a in snaps}


def fetch_funding_history_parallel(
    fp: Any | None,
    symbols: list[str],
    quote: str,
    start_ms_by_sym: dict[str, int],
    *,
    workers: int = 6,
    fetch_since_fn: Callable[[str, int], list[dict[str, Any]]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Parallel ``fetch_since`` for multiple held positions."""
    syms = [s for s in symbols if int(start_ms_by_sym.get(s, 0) or 0) > 0]
    if not syms:
        return {}
    if fp is None and fetch_since_fn is None:
        return {}
    fp_ref = fp  # captured for closure type narrowing

    def _one(sym: str) -> tuple[str, list[dict[str, Any]]]:
        start = int(start_ms_by_sym[sym]) + 1
        pair = make_pair(sym, quote)
        if fetch_since_fn is not None:
            return sym, fetch_since_fn(pair, start)
        assert fp_ref is not None
        return sym, fp_ref.fetch_since(pair, start)

    raw = run_io_parallel(syms, _one, max_workers=workers, swallow_errors=True)
    out: dict[str, list[dict[str, Any]]] = {}
    for k, v in raw.items():
        if isinstance(v, list):
            out[k] = sorted(v, key=lambda r: int(r.get("ts", 0)))
    return out
