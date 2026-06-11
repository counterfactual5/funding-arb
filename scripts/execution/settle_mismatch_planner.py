#!/usr/bin/env python3
"""Settle-mismatch fee planner — funding rate planning for settlement period mismatches.

When two exchanges have different funding settlement periods (e.g. Bitget 2h vs Binance 8h),
the spread may look attractive, but the actual pay/receive frequency is asymmetrical, which can cause:
  - Overestimated returns (expecting 0.15% every 8h, but the short leg pays 4 times per 2h)
  - Cash flow mismatch (one side pays frequently, the other only receives every 8h)

This module:
  1. Calculates the effective funding rate (normalized to a common time window using the shorter period)
  2. Evaluates whether cash needs to be reserved for cash flow
  3. Generates an adjusted net_edge (accounting for period mismatch)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MismatchAnalysis:
    """Settlement period mismatch analysis result."""

    base: str
    long_venue: str
    short_venue: str
    long_rate_pct: float
    short_rate_pct: float
    long_interval_h: float
    short_interval_h: float
    is_mismatch: bool
    # Rates normalized to 8h window
    long_rate_per_8h_pct: float
    short_rate_per_8h_pct: float
    spread_per_8h_pct: float
    # Cash flow impact
    max_cumulative_outflow_pct: (
        float  # Max cumulative outflow within one period (% of notional)
    )
    # Adjustment recommendations
    adjusted_net_edge_pct: float  # Net edge after accounting for period mismatch
    capital_buffer_pct: float  # Recommended capital reservation (% of notional)
    viable: bool
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "long_venue": self.long_venue,
            "short_venue": self.short_venue,
            "long_rate_pct": self.long_rate_pct,
            "short_rate_pct": self.short_rate_pct,
            "long_interval_h": self.long_interval_h,
            "short_interval_h": self.short_interval_h,
            "is_mismatch": self.is_mismatch,
            "long_rate_per_8h_pct": round(self.long_rate_per_8h_pct, 6),
            "short_rate_per_8h_pct": round(self.short_rate_per_8h_pct, 6),
            "spread_per_8h_pct": round(self.spread_per_8h_pct, 6),
            "max_cumulative_outflow_pct": round(self.max_cumulative_outflow_pct, 6),
            "adjusted_net_edge_pct": round(self.adjusted_net_edge_pct, 6),
            "capital_buffer_pct": round(self.capital_buffer_pct, 4),
            "viable": self.viable,
            "note": self.note,
        }


def analyze_settle_mismatch(
    base: str,
    long_venue: str,
    short_venue: str,
    long_rate_pct: float,
    short_rate_pct: float,
    long_interval_h: float,
    short_interval_h: float,
    total_fee_pct: float = 0.11,
    *,
    max_spread_tolerance_pct: float = 0.5,
    min_periods_for_viable: int = 3,
) -> MismatchAnalysis:
    """Analyze the actual return and risk of settlement period mismatches.

    Core approach:
      - Normalize both legs' rates to an 8h window
      - Calculate cash flow mismatch: the short-period leg pays/receives every short period,
        while the long-period leg only pays/receives at the long period boundary
      - Maximum cumulative outflow = total paid/received by the short-period leg before
        the long-period leg settles

    Args:
        max_spread_tolerance_pct: Mark as high risk if the period gap exceeds this (%)
        min_periods_for_viable: Require at least N short periods of expected holding to be viable
    """
    is_mismatch = abs(long_interval_h - short_interval_h) > 0.5

    # Normalize to 8h window
    long_rate_per_8h = (
        long_rate_pct * (8.0 / long_interval_h) if long_interval_h > 0 else 0.0
    )
    short_rate_per_8h = (
        short_rate_pct * (8.0 / short_interval_h) if short_interval_h > 0 else 0.0
    )
    spread_per_8h = abs(short_rate_per_8h - long_rate_per_8h)

    if not is_mismatch:
        # No mismatch, use raw data directly
        return MismatchAnalysis(
            base=base,
            long_venue=long_venue,
            short_venue=short_venue,
            long_rate_pct=long_rate_pct,
            short_rate_pct=short_rate_pct,
            long_interval_h=long_interval_h,
            short_interval_h=short_interval_h,
            is_mismatch=False,
            long_rate_per_8h_pct=long_rate_per_8h,
            short_rate_per_8h_pct=short_rate_per_8h,
            spread_per_8h_pct=spread_per_8h,
            max_cumulative_outflow_pct=0.0,
            adjusted_net_edge_pct=spread_per_8h - total_fee_pct,
            capital_buffer_pct=0.0,
            viable=True,
            note="no mismatch",
        )

    # Mismatch present: analyze cash flow
    shorter_interval = min(long_interval_h, short_interval_h)
    longer_interval = max(long_interval_h, short_interval_h)

    if shorter_interval <= 0 or longer_interval <= 0:
        return MismatchAnalysis(
            base=base,
            long_venue=long_venue,
            short_venue=short_venue,
            long_rate_pct=long_rate_pct,
            short_rate_pct=short_rate_pct,
            long_interval_h=long_interval_h,
            short_interval_h=short_interval_h,
            is_mismatch=False,
            long_rate_per_8h_pct=0.0,
            short_rate_per_8h_pct=0.0,
            spread_per_8h_pct=0.0,
            max_cumulative_outflow_pct=0.0,
            adjusted_net_edge_pct=0.0,
            capital_buffer_pct=0.0,
            viable=False,
            note="invalid interval (<=0)",
        )

    # Number of short-period settlements within one long period
    settlements_in_longer = max(1, round(longer_interval / shorter_interval))

    # Determine which leg has the shorter period
    if long_interval_h <= short_interval_h:
        # Long leg settles more frequently
        # Long pays rate to shorts on each settlement
        # Worst case: long pays 3 times before short settles once
        per_settlement_rate = long_rate_pct
        max_cumulative = abs(per_settlement_rate) * (settlements_in_longer - 1)
    else:
        # Short leg settles more frequently
        per_settlement_rate = short_rate_pct
        max_cumulative = abs(per_settlement_rate) * (settlements_in_longer - 1)

    # Adjusted net edge: account for potential costs from mismatch
    # Conservative estimate: deduct a portion of max cumulative outflow (assuming spread won't fully reverse)
    spread_gap_penalty = max_cumulative * 0.3  # 30% buffer for timing risk
    adjusted_net = spread_per_8h - total_fee_pct - spread_gap_penalty

    # Capital buffer: recommend reserving funds to cover cash flow mismatch
    capital_buffer = max_cumulative * 0.5  # Reserve 50% of max cumulative outflow

    # Viability check
    viable = True
    notes: list[str] = []
    if adjusted_net <= 0:
        viable = False
        notes.append(f"adjusted_net={adjusted_net:.4f}% ≤ 0")
    if settlements_in_longer < min_periods_for_viable:
        viable = False
        notes.append(f"only {settlements_in_longer} settlements per long cycle")
    if max_cumulative > max_spread_tolerance_pct:
        notes.append(f"high cumulative outflow {max_cumulative:.3f}%")
        # Not auto-reject, but warn

    note = (
        "; ".join(notes)
        if notes
        else f"mismatch but viable ({settlements_in_longer}x per cycle)"
    )

    return MismatchAnalysis(
        base=base,
        long_venue=long_venue,
        short_venue=short_venue,
        long_rate_pct=long_rate_pct,
        short_rate_pct=short_rate_pct,
        long_interval_h=long_interval_h,
        short_interval_h=short_interval_h,
        is_mismatch=True,
        long_rate_per_8h_pct=long_rate_per_8h,
        short_rate_per_8h_pct=short_rate_per_8h,
        spread_per_8h_pct=spread_per_8h,
        max_cumulative_outflow_pct=round(max_cumulative, 6),
        adjusted_net_edge_pct=round(adjusted_net, 6),
        capital_buffer_pct=round(capital_buffer, 4),
        viable=viable,
        note=note,
    )


def effective_trade_usd(trade_usd: float, row: dict[str, Any]) -> float:
    """Scale down notional by capital_buffer_pct to reserve margin buffer for mismatched cash flows."""
    buffer_pct = float(row.get("capital_buffer_pct", 0) or 0)
    if buffer_pct <= 0:
        return trade_usd
    # Buffer is a % of notional; reduce position to leave account USDT buffer
    scale = max(0.5, 1.0 - buffer_pct / 100.0)
    return round(trade_usd * scale, 2)


def filter_candidates_with_mismatch(
    candidates: list[dict[str, Any]],
    *,
    allow_mismatch: bool = False,
    max_cumulative_outflow_pct: float = 0.5,
    min_adjusted_edge_pct: float = 0.0,
) -> list[dict[str, Any]]:
    """Filter settle-mismatch candidates from scan results and add analysis data.

    Args:
        candidates: rows returned by scan_pure_futures_spreads
        allow_mismatch: Whether to allow viable-but-mismatched candidates
        max_cumulative_outflow_pct: Maximum allowed cumulative outflow
        min_adjusted_edge_pct: Minimum adjusted net edge
    """
    filtered = []
    for row in candidates:
        if not row.get("settle_mismatch"):
            filtered.append(row)
            continue

        if not allow_mismatch:
            continue

        analysis = analyze_settle_mismatch(
            base=str(row.get("base", "")),
            long_venue=str(row.get("long_venue", "")),
            short_venue=str(row.get("short_venue", "")),
            long_rate_pct=float(row.get("long_rate_pct", 0)),
            short_rate_pct=float(row.get("short_rate_pct", 0)),
            long_interval_h=float(row.get("long_interval_h", 8)),
            short_interval_h=float(row.get("short_interval_h", 8)),
            total_fee_pct=float(row.get("fee_pct", 0.11)),
        )

        if not analysis.viable:
            continue
        if analysis.max_cumulative_outflow_pct > max_cumulative_outflow_pct:
            continue
        if analysis.adjusted_net_edge_pct < min_adjusted_edge_pct:
            continue

        # Add analysis data to the row
        row_with_analysis = dict(row)
        row_with_analysis["mismatch_analysis"] = analysis.to_dict()
        row_with_analysis["adjusted_net_edge_pct"] = analysis.adjusted_net_edge_pct
        row_with_analysis["capital_buffer_pct"] = analysis.capital_buffer_pct
        filtered.append(row_with_analysis)

    return filtered
