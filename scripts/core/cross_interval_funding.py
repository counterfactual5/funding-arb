"""Cross-interval funding rate estimation for perp-perp spread scanning.

When settlement intervals differ (e.g. HL 1h vs CEX 8h), naively scaling the
last-settled rate overstates edge mid-cycle.  We blend the observed rate with a
basis-implied hourly rate (mark vs index premium), weighted by progress through
the current funding period.
"""

from __future__ import annotations

from typing import Any

# Exchanges clamp premium before funding; uncapped mark-index can inflate scans.
MAX_BASIS_PCT_PER_PERIOD = 1.0


def infer_last_settle_ts(next_funding_ts: int, interval_h: float) -> int:
    """Derive last settlement timestamp from next funding time and interval."""
    if next_funding_ts <= 0 or interval_h <= 0:
        return 0
    return int(next_funding_ts - interval_h * 3600 * 1000)


def settle_progress(
    now_ms: int,
    *,
    next_funding_ts: int = 0,
    last_settle_ts: int = 0,
    interval_h: float = 8.0,
) -> float:
    """Fraction elapsed through the current funding period, in [0, 1]."""
    interval_ms = max(int(interval_h * 3600 * 1000), 1)
    if last_settle_ts > 0 and next_funding_ts > last_settle_ts:
        interval_ms = next_funding_ts - last_settle_ts
    if last_settle_ts > 0:
        elapsed = now_ms - last_settle_ts
        return max(0.0, min(1.0, elapsed / interval_ms))
    if next_funding_ts > now_ms:
        remaining = next_funding_ts - now_ms
        return max(0.0, min(1.0, 1.0 - remaining / interval_ms))
    return 0.5


def basis_pct(mark: float, index: float) -> float | None:
    """Mark-index premium as % of index; None when data missing."""
    if mark <= 0 or index <= 0:
        return None
    raw = (mark - index) / index * 100.0
    cap = MAX_BASIS_PCT_PER_PERIOD
    if abs(raw) > cap:
        return cap if raw > 0 else -cap
    return raw


def hourly_from_basis(basis_pct_val: float, interval_h: float) -> float:
    """Convert per-period basis premium to an hourly funding estimate."""
    if interval_h <= 0:
        return 0.0
    return basis_pct_val / interval_h


def blended_hourly_rate(
    rate_pct: float,
    interval_h: float,
    info: dict[str, Any],
    *,
    now_ms: int,
    use_basis_blend: bool = True,
) -> tuple[float, dict[str, Any]]:
    """Estimate hourly funding: linear rate, or progress-weighted basis blend."""
    rate_hourly = rate_pct / interval_h if interval_h > 0 else 0.0
    meta: dict[str, Any] = {
        "rate_hourly": rate_hourly,
        "basis_hourly": None,
        "settle_progress": None,
        "blend_alpha": 0.0,
        "used_basis": False,
        "basis_pct": None,
    }
    if not use_basis_blend:
        return rate_hourly, meta

    mark = float(info.get("mark_price", 0) or 0)
    index = float(info.get("index_price", 0) or 0)
    bp = basis_pct(mark, index)
    if bp is None:
        return rate_hourly, meta

    basis_hour = hourly_from_basis(bp, interval_h)
    progress = settle_progress(
        now_ms,
        next_funding_ts=int(info.get("next_funding_ts", 0) or 0),
        last_settle_ts=int(info.get("last_settle_ts", 0) or 0),
        interval_h=interval_h,
    )
    alpha = progress
    blended = (1 - alpha) * rate_hourly + alpha * basis_hour
    meta.update(
        basis_hourly=basis_hour,
        settle_progress=round(progress, 4),
        blend_alpha=round(alpha, 4),
        used_basis=True,
        basis_pct=round(bp, 6),
    )
    return blended, meta


def spread_source_for_pair(
    is_mismatch: bool,
    long_meta: dict[str, Any],
    short_meta: dict[str, Any],
) -> str:
    if not is_mismatch:
        return "rate"
    if long_meta.get("used_basis") or short_meta.get("used_basis"):
        return "basis_blend"
    return "rate_linear"
