#!/usr/bin/env python3
"""Tests for scanner per-interval-group net-edge thresholds (1h / mismatch)."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
ROOT = SCRIPTS.parent
for p in (str(ROOT), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from server.routes.scanner import (
    _apply_group_thresholds,
    _basis_risk_level,
    _row_edge_threshold,
    _row_real_edge,
)


def _row(long_h, short_h, edge, mismatch=None, mark_spread=0.0):
    return {
        "long_interval_h": long_h,
        "short_interval_h": short_h,
        "net_edge_pct": edge,
        "mark_spread_pct": mark_spread,
        "real_edge_pct": round(edge - mark_spread, 6),
        "settle_mismatch": (long_h != short_h) if mismatch is None else mismatch,
    }


class TestRowThreshold:
    def test_both_1h_uses_1h_bar(self):
        r = _row(1.0, 1.0, 0.015)
        assert _row_edge_threshold(r, 0.02, 0.01, 0.03) == 0.01

    def test_mismatch_uses_premium_bar(self):
        r = _row(4.0, 8.0, 0.025)  # EdgeX 4h vs CEX 8h
        assert _row_edge_threshold(r, 0.02, 0.01, 0.03) == 0.03

    def test_same_interval_non_1h_uses_base(self):
        r = _row(8.0, 8.0, 0.025)
        assert _row_edge_threshold(r, 0.02, 0.01, 0.03) == 0.02

    def test_1h_takes_precedence_over_mismatch(self):
        # both legs 1h → not a mismatch; 1h bar wins even if mismatch set
        r = _row(1.0, 1.0, 0.015)
        assert _row_edge_threshold(r, 0.02, 0.01, 0.03) == 0.01

    def test_none_premiums_fall_back_to_base(self):
        r = _row(4.0, 8.0, 0.025)
        assert _row_edge_threshold(r, 0.02, None, None) == 0.02


class TestApplyGroupThresholds:
    def _result(self):
        return {
            "forward": [
                _row(1.0, 1.0, 0.015),   # 1h: passes 0.01, fails base 0.02
                _row(8.0, 8.0, 0.025),   # same 8h: passes base 0.02
                _row(4.0, 8.0, 0.025),   # mismatch: fails premium 0.03
                _row(4.0, 8.0, 0.035),   # mismatch: passes premium 0.03
            ],
            "reverse": [],
            "total_spreads_found": 4,
        }

    def test_full_grouping(self):
        out = _apply_group_thresholds(self._result(), 0.02, 0.01, 0.03)
        edges = sorted(r["net_edge_pct"] for r in out["forward"])
        # keep: 1h@0.015, same@0.025, mismatch@0.035 ; drop mismatch@0.025
        assert edges == [0.015, 0.025, 0.035]
        assert out["total_spreads_found"] == 3

    def test_base_net_edge_when_no_premiums(self):
        out = _apply_group_thresholds(self._result(), 0.02, None, None)
        edges = sorted(r["net_edge_pct"] for r in out["forward"])
        # Judged on net edge; 1h@0.015 dropped (below base 0.02)
        assert edges == [0.025, 0.025, 0.035]
        assert out["total_spreads_found"] == 3

    def test_mismatch_only_premium(self):
        # no 1h loosening, only mismatch tightening
        out = _apply_group_thresholds(self._result(), 0.02, None, 0.03)
        edges = sorted(r["net_edge_pct"] for r in out["forward"])
        # 1h@0.015 now judged at base 0.02 → dropped; mismatch@0.025 dropped
        assert edges == [0.025, 0.035]


class TestRealEdgeFilter:
    def test_real_edge_is_net_minus_mark(self):
        assert _row_real_edge(_row(8.0, 8.0, 0.05, mark_spread=0.03)) == 0.02

    def test_basis_eaten_edge_kept_with_risk_tag(self):
        # net passes but mark divergence eats real edge → kept, tagged for UI.
        res = {
            "forward": [
                _row(8.0, 8.0, 0.05, mark_spread=0.04),  # real 0.01 → caution
                _row(8.0, 8.0, 0.03, mark_spread=0.00),  # real 0.03 → clean
            ],
            "reverse": [],
            "total_spreads_found": 2,
        }
        out = _apply_group_thresholds(res, 0.02, None, None)
        assert sorted(r["net_edge_pct"] for r in out["forward"]) == [0.03, 0.05]
        by_net = {r["net_edge_pct"]: r for r in out["forward"]}
        assert by_net[0.05]["basis_risk_level"] == "caution"
        assert by_net[0.03]["basis_risk_level"] == "clean"

    def test_negative_real_edge_tagged_high(self):
        row = _row(8.0, 8.0, 0.03, mark_spread=0.05)  # real -0.02
        assert _basis_risk_level(row, 0.02) == "high"

    def test_falls_back_to_net_when_real_absent(self):
        # Older cached rows without real_edge_pct → net − mark_spread.
        row = {"long_interval_h": 8, "short_interval_h": 8,
               "net_edge_pct": 0.05, "mark_spread_pct": 0.04}
        assert abs(_row_real_edge(row) - 0.01) < 1e-9


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
