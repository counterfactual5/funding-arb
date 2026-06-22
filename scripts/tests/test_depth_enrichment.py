#!/usr/bin/env python3
"""Hermetic unit tests for the liquidity (depth) enrichment in the scanner.

Covers the ``_enrich_with_depth`` helper without touching the network —
``fetch_futures_depth`` is monkeypatched to return synthetic books so we can
assert the USD-within-window math, the top-N cut, the (venue, base) cache
dedup, and the fail-open behavior on fetch errors.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cli import scan_pure_futures_spreads as scanner  # noqa: E402


def _entry(base: str, long_v: str, short_v: str, real_edge: float = 0.1):
    return {
        "base": base,
        "long_venue": long_v,
        "short_venue": short_v,
        "real_edge_pct": real_edge,
        "net_edge_pct": real_edge,
    }


def _book(mid: float, bid_qty: float, ask_qty: float, levels: int = 3):
    """Asymmetric book so asks/bids USD totals differ visibly."""
    return {
        "bids": [(mid - 0.01 * i, bid_qty) for i in range(1, levels + 1)],
        "asks": [(mid + 0.01 * i, ask_qty) for i in range(1, levels + 1)],
    }


def test_enrich_sets_depth_fields_on_top_n(monkeypatch):
    """Top-N entries get long/short/max_exec USD; the rest get None placeholders."""
    calls = {"count": 0}

    def fake_fetch(venue, base, quote="USDT"):
        calls["count"] += 1
        # Different liquidity per venue so we can distinguish legs.
        if venue == "binance":
            return _book(mid=100.0, bid_qty=50.0, ask_qty=20.0)  # deep
        return _book(mid=100.0, bid_qty=5.0, ask_qty=2.0)  # thin

    monkeypatch.setattr(scanner, "fetch_futures_depth", fake_fetch)

    entries = [
        _entry("BTC", "binance", "bybit", real_edge=0.5),  # rank 1
        _entry("ETH", "binance", "bybit", real_edge=0.3),  # rank 2
        _entry("SOL", "binance", "bybit", real_edge=0.1),  # rank 3
        _entry("DOGE", "binance", "bybit", real_edge=0.05),  # rank 4 — out of top 3
    ]
    scanner._enrich_with_depth(entries, top_n=3, window_pct=0.3)

    # BTC (top 3): long=binance asks=20*3 levels@~100 = ~6060 USD within window;
    # short=bybit bids=5*3@~100 = ~1485 USD. max_exec = min.
    btc = entries[0]
    assert btc["long_depth_usd"] is not None
    assert btc["short_depth_usd"] is not None
    assert btc["max_exec_usd"] == round(
        min(btc["long_depth_usd"], btc["short_depth_usd"]), 2
    )
    assert btc["depth_ok"] is True
    # Short leg (bybit, thin) should be the binding constraint.
    assert btc["short_depth_usd"] < btc["long_depth_usd"]

    # DOGE (rank 4, outside top_n=3): placeholders, no fetch.
    doge = entries[3]
    assert doge["long_depth_usd"] is None
    assert doge["short_depth_usd"] is None
    assert doge["max_exec_usd"] is None
    assert doge["depth_ok"] is None


def test_enrich_dedups_venue_base_across_entries(monkeypatch):
    """Multiple entries sharing a (venue, base) only trigger one fetch."""
    seen: list[tuple[str, str]] = []

    def fake_fetch(venue, base, quote="USDT"):
        seen.append((venue, base))
        return _book(mid=100.0, bid_qty=10.0, ask_qty=10.0)

    monkeypatch.setattr(scanner, "fetch_futures_depth", fake_fetch)

    # Three entries all touching BTC @ binance — should fetch (binance, BTC) once.
    entries = [
        _entry("BTC", "binance", "bybit", real_edge=0.5),
        _entry("BTC", "binance", "okx", real_edge=0.3),
        _entry("BTC", "binance", "bitget", real_edge=0.1),
    ]
    scanner._enrich_with_depth(entries, top_n=10, window_pct=0.3)

    # (binance, BTC) fetched exactly once across the three entries.
    binance_btc_calls = [s for s in seen if s == ("binance", "BTC")]
    assert len(binance_btc_calls) == 1


def test_enrich_fail_open_on_fetch_error(monkeypatch):
    """A failing depth fetch must not abort the scan — entry gets depth_ok=False."""

    def fake_fetch(venue, base, quote="USDT"):
        raise RuntimeError("api down")

    monkeypatch.setattr(scanner, "fetch_futures_depth", fake_fetch)

    entries = [_entry("BTC", "binance", "bybit")]
    scanner._enrich_with_depth(entries, top_n=5, window_pct=0.3)

    e = entries[0]
    assert e["long_depth_usd"] is None
    assert e["short_depth_usd"] is None
    assert e["max_exec_usd"] is None
    assert e["depth_ok"] is False  # explicitly False, not None — fetch attempted


def test_enrich_skips_when_no_entries(monkeypatch):
    """Empty input is a no-op (no fetches, no crash)."""
    monkeypatch.setattr(
        scanner,
        "fetch_futures_depth",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    scanner._enrich_with_depth([], top_n=5, window_pct=0.3)  # must not raise


def test_fmt_depth_usd_compact_notation():
    assert scanner._fmt_depth_usd(None) == "N/A"
    assert scanner._fmt_depth_usd(250) == "250"
    assert scanner._fmt_depth_usd(1400) == "1.4k"
    assert scanner._fmt_depth_usd(62_000) == "62.0k"
    assert scanner._fmt_depth_usd(2_500_000) == "2.50M"
