"""Tests for scripts/notify/snapshot_to_pages.py.

These cover the build_snapshot() pure function — no network. The CLI
main() path is exercised by manual smoke tests (same approach as the
existing test_telegram_push.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notify.snapshot_to_pages import _resolve_venues, build_snapshot  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_spread(**overrides) -> dict:
    base = {
        "base": "BTC",
        "direction": "forward",
        "long_venue": "okx",
        "short_venue": "bybit",
        "long_rate_pct": 0.0125,
        "short_rate_pct": -0.005,
        "spread_pct": 0.0175,
        "fee_pct": 0.008,
        "net_edge_pct": 0.0095,
        "annual_apy_pct": 83.0,
        "mark_spread_pct": 0.03,
        "settle_mismatch": False,
    }
    base.update(overrides)
    return base


def _make_scan_result(rows=None) -> dict:
    rows = rows if rows is not None else [_make_spread()]
    forward = [r for r in rows if r.get("direction", "forward") == "forward"]
    reverse = [r for r in rows if r.get("direction") == "reverse"]
    return {
        "venues": ["binance", "bitget", "bybit", "okx"],
        "total_assets_scanned": 187,
        "total_spreads_found": len(rows),
        "forward": forward,
        "reverse": reverse,
        "venue_pair_stats": [{"pair": "okx↔bybit", "count": 1}],
        "timestamp": "2026-06-21T08:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# build_snapshot
# ---------------------------------------------------------------------------


class TestBuildSnapshotShape:
    def test_has_meta_status_opportunities_keys(self):
        snap = build_snapshot(_make_scan_result())
        assert set(snap.keys()) == {
            "meta",
            "scanner_status",
            "scanner_opportunities",
            # Carry / Unified slices (added when --include-carry is on,
            # which is the default). Always present in the schema even if
            # empty, so the frontend's demo route table can rely on them.
            "scanner_carry_venues",
            "scanner_unified_routes",
        }

    def test_meta_carries_schema_version(self):
        snap = build_snapshot(_make_scan_result())
        assert snap["meta"]["schema_version"] == 1
        assert "generated_at" in snap["meta"]
        assert "scan_timestamp" in snap["meta"]
        assert "pipeline" in snap["meta"]

    def test_status_carries_demo_flag(self):
        snap = build_snapshot(_make_scan_result())
        assert snap["scanner_status"]["is_demo_snapshot"] is True
        assert snap["scanner_status"]["scanning"] is False
        assert snap["scanner_status"]["live"] is False

    def test_pipeline_info_is_passed_through(self):
        snap = build_snapshot(
            _make_scan_result(),
            pipeline_info={"runner": "Linux", "git_sha": "abc1234"},
        )
        assert snap["meta"]["pipeline"]["runner"] == "Linux"
        assert snap["meta"]["pipeline"]["git_sha"] == "abc1234"

    def test_timestamp_propagates_to_status_and_opportunities(self):
        ts = "2026-06-21T08:00:00+00:00"
        result = _make_scan_result()
        result["timestamp"] = ts
        snap = build_snapshot(result)
        assert snap["scanner_status"]["last_scan_time"] == ts
        assert snap["scanner_opportunities"]["timestamp"] == ts
        assert snap["meta"]["scan_timestamp"] == ts

    def test_carry_and_unified_defaults_to_empty(self):
        # When caller passes nothing, the snapshot still carries the keys
        # (empty) so the frontend demo route table has stable shape.
        snap = build_snapshot(_make_scan_result())
        assert snap["scanner_carry_venues"] == []
        assert snap["scanner_unified_routes"] == {
            "venues": [],
            "forward": [],
            "reverse": [],
        }

    def test_carry_and_unified_are_passed_through(self):
        carry = [{"venue": "binance", "forward": [{"base": "BTC"}], "reverse": []}]
        unified = {
            "venues": ["binance", "okx"],
            "forward": [{"base": "ETH"}],
            "reverse": [],
        }
        snap = build_snapshot(
            _make_scan_result(),
            carry_venues=carry,
            unified_routes=unified,
        )
        assert snap["scanner_carry_venues"] == carry
        assert snap["scanner_unified_routes"] == unified


class TestBuildSnapshotSortingAndTruncation:
    def test_top_n_truncates(self):
        rows = [
            _make_spread(base=f"COIN{i}", net_edge_pct=0.01 * (10 - i))
            for i in range(20)
        ]
        snap = build_snapshot(_make_scan_result(rows=rows), top_n=5)
        all_rows = (
            snap["scanner_opportunities"]["forward"]
            + snap["scanner_opportunities"]["reverse"]
        )
        assert len(all_rows) == 5
        # Highest edge first
        assert all_rows[0]["base"] == "COIN0"
        assert all_rows[-1]["base"] == "COIN4"

    def test_sorting_by_net_edge_desc(self):
        rows = [
            _make_spread(base="LOW", net_edge_pct=0.001),
            _make_spread(base="HIGH", net_edge_pct=0.099),
            _make_spread(base="MID", net_edge_pct=0.05),
        ]
        snap = build_snapshot(_make_scan_result(rows=rows), top_n=10)
        all_rows = (
            snap["scanner_opportunities"]["forward"]
            + snap["scanner_opportunities"]["reverse"]
        )
        bases = [r["base"] for r in all_rows]
        assert bases == ["HIGH", "MID", "LOW"]

    def test_forward_reverse_split_preserved(self):
        rows = [
            _make_spread(base="FWD1", direction="forward", net_edge_pct=0.05),
            _make_spread(base="REV1", direction="reverse", net_edge_pct=0.04),
            _make_spread(base="FWD2", direction="forward", net_edge_pct=0.03),
        ]
        snap = build_snapshot(_make_scan_result(rows=rows), top_n=10)
        fwd_bases = {r["base"] for r in snap["scanner_opportunities"]["forward"]}
        rev_bases = {r["base"] for r in snap["scanner_opportunities"]["reverse"]}
        assert fwd_bases == {"FWD1", "FWD2"}
        assert rev_bases == {"REV1"}

    def test_empty_scan_result_yields_empty_lists(self):
        snap = build_snapshot(_make_scan_result(rows=[]), top_n=10)
        assert snap["scanner_opportunities"]["forward"] == []
        assert snap["scanner_opportunities"]["reverse"] == []
        assert snap["scanner_status"]["has_data"] is False

    def test_zero_top_n_returns_empty_lists(self):
        snap = build_snapshot(_make_scan_result(), top_n=0)
        assert snap["scanner_opportunities"]["forward"] == []
        assert snap["scanner_opportunities"]["reverse"] == []

    def test_input_rows_not_mutated_by_direction_default(self):
        """The function should not write back into the caller's rows."""
        row = _make_spread()
        row.pop("direction")  # missing direction
        result = _make_scan_result(rows=[row])
        build_snapshot(result, top_n=10)
        # Original result.forward[0] should still have no 'direction' key
        assert "direction" not in result["forward"][0]


# ---------------------------------------------------------------------------
# _resolve_venues
# ---------------------------------------------------------------------------


class TestResolveVenues:
    def test_explicit_arg_wins(self):
        assert _resolve_venues("binance,bybit", include_dex=True) == [
            "binance",
            "bybit",
        ]

    def test_default_cex_plus_hl(self):
        v = _resolve_venues(None, include_dex=False)
        assert "binance" in v and "hyperliquid" in v
        assert "aster" not in v

    def test_include_dex_full_set(self):
        v = _resolve_venues(None, include_dex=True)
        for dex in ("hyperliquid", "aster", "lighter", "edgex", "dydx"):
            assert dex in v

    def test_whitespace_tolerant(self):
        assert _resolve_venues(" binance , bybit ,OKX ", include_dex=False) == [
            "binance",
            "bybit",
            "okx",
        ]
