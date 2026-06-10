#!/usr/bin/env python3
"""Settle-mismatch fee planner — 结算周期错配的资金费规划。

当两个交易所的 funding 结算周期不同时（如 Bitget 2h vs Binance 8h），虽然 spread
看起来不错，但实际收/付频率不对称，可能导致:
  - 预期收益高估（以为每 8h 收 0.15%，实际 short 腿 2h 一付 4 次）
  - 现金流错配（一边频繁付费，另一边 8h 才收一次）

本模块:
  1. 计算实际的有效资金费（按短周期折算到统一时间窗口）
  2. 评估现金流是否需要预留资金
  3. 生成调整后的 net_edge（考虑周期错配）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MismatchAnalysis:
    """结算周期错配分析结果。"""

    base: str
    long_venue: str
    short_venue: str
    long_rate_pct: float
    short_rate_pct: float
    long_interval_h: float
    short_interval_h: float
    is_mismatch: bool
    # 标准化到 8h 窗口的资金费
    long_rate_per_8h_pct: float
    short_rate_per_8h_pct: float
    spread_per_8h_pct: float
    # 现金流影响
    max_cumulative_outflow_pct: float  # 在一个周期内最大累计流出（占名义价值的百分比）
    # 调整建议
    adjusted_net_edge_pct: float  # 考虑周期错配后的净边际
    capital_buffer_pct: float  # 建议预留资金（占名义价值的百分比）
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
    """分析结算周期错配的实际收益和风险。

    核心思路:
      - 将两腿的 rate 标准化到 8h 窗口
      - 计算现金流错配: 短周期腿在每个短周期都要付/收，长周期腿只在长周期时收/付
      - 最大累积流出 = 在长周期腿收/付之前，短周期腿已付/收的总和

    Args:
        max_spread_tolerance_pct: 两腿周期差距超过此值（%）标记为高风险
        min_periods_for_viable: 至少期望持仓 N 个短周期才算 viable
    """
    is_mismatch = abs(long_interval_h - short_interval_h) > 0.5

    # 标准化到 8h 窗口
    long_rate_per_8h = long_rate_pct * (8.0 / long_interval_h) if long_interval_h > 0 else 0.0
    short_rate_per_8h = short_rate_pct * (8.0 / short_interval_h) if short_interval_h > 0 else 0.0
    spread_per_8h = abs(short_rate_per_8h - long_rate_per_8h)

    if not is_mismatch:
        # 无错配，直接用原始数据
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

    # 有错配: 分析现金流
    shorter_interval = min(long_interval_h, short_interval_h)
    longer_interval = max(long_interval_h, short_interval_h)

    # 在一个长周期内，短周期腿结算次数
    settlements_in_longer = max(1, round(longer_interval / shorter_interval))

    # 找出哪个腿是短周期的
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

    # Adjusted net edge: 考虑错配带来的潜在成本
    # 保守估计：扣除最大累积流出的一部分（假设 spread 不会完全反转）
    spread_gap_penalty = max_cumulative * 0.3  # 30% buffer for timing risk
    adjusted_net = spread_per_8h - total_fee_pct - spread_gap_penalty

    # Capital buffer: 建议预留资金以覆盖现金流错配
    capital_buffer = max_cumulative * 0.5  # 预留 50% 的最大累积流出

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

    note = "; ".join(notes) if notes else f"mismatch but viable ({settlements_in_longer}x per cycle)"

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
    """按 capital_buffer_pct 缩小名义本金，为错配现金流预留保证金余量。"""
    buffer_pct = float(row.get("capital_buffer_pct", 0) or 0)
    if buffer_pct <= 0:
        return trade_usd
    # buffer 是名义价值百分比；缩小仓位以留出账户 USDT 缓冲
    scale = max(0.5, 1.0 - buffer_pct / 100.0)
    return round(trade_usd * scale, 2)


def filter_candidates_with_mismatch(
    candidates: list[dict[str, Any]],
    *,
    allow_mismatch: bool = False,
    max_cumulative_outflow_pct: float = 0.5,
    min_adjusted_edge_pct: float = 0.0,
) -> list[dict[str, Any]]:
    """过滤扫描结果中的 settle-mismatch 候选，添加分析数据。

    Args:
        candidates: scan_pure_futures_spreads 返回的 rows
        allow_mismatch: 是否允许有错配但仍然 viable 的候选
        max_cumulative_outflow_pct: 最大允许的累积流出
        min_adjusted_edge_pct: 调整后最低净边际
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
