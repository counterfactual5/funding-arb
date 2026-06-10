#!/usr/bin/env python3
"""纯永续资金费差套利决策引擎。

策略核心:
  对同资产在不同交易所的永续合约资金费率进行价差套利，
  无需现货、无需借贷、无需跨所转账。

  在 rate 高的交易所做多（收到 funding），rate 低的交易所做空（少付 funding）。
  两侧头寸完全 delta-neutral，利润来自 funding rate 差异。

用法（通常由 runner / orchestrator 调用，不直接运行）:

  from strategies.futures.pure_futures_spread import decide_pure_futures_spread
  trades, meta = decide_pure_futures_spread(state, prices, cfg, funding_rates)
"""

from __future__ import annotations

from typing import Any

from core.fee_providers import pair_open_taker_fee_pct, taker_fee_pct


def decide_pure_futures_spread(
    futures_state: dict[str, Any],
    prices: dict[str, float],
    cfg: dict[str, Any],
    funding_rates: dict[str, dict[str, float]],
    current_time_ms: int = 0,
    fee_cache: dict[tuple[str, str], dict[str, float]] | None = None,
    mark_prices: dict[str, dict[str, float]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """纯永续资金费差套利决策。

    Args:
        futures_state: 当前持仓状态 {"positions": {symbol: {amount, entry_price, side}}}
        prices: {asset: current_price}  e.g. {"BTC": 100000.0}
        cfg: 全局配置，需含 pureFuturesArbitrage 子键
        funding_rates: {venue: {symbol: rate_pct}}
            e.g. {"binance": {"BTCUSDT": 0.05, "ETHUSDT": 0.02},
                  "okx":     {"BTCUSDT": -0.10}}
        current_time_ms: 当前时间戳
        mark_prices: 可选，{venue: {symbol: mark_price}}，用于过滤标记价差过大的候选

    Returns:
        (trades, meta) tuple:
          trades: [{"symbol", "type", "venue", "amount_base", "amount_usdt",
                    "pair_id", "funding_rate_pct", "meta": {...}}]
          meta: {"strategy", "pairs_opened", "pairs_closed", "spread_matrix", "skipped_reasons"}
    """
    trades: list[dict[str, Any]] = []
    meta: dict[str, Any] = {
        "strategy": "pure_futures_spread",
        "pairs_opened": [],
        "pairs_closed": [],
        "spread_matrix": [],
        "skipped_reasons": [],
    }

    cfg_pfs = cfg.get("pureFuturesArbitrage") or {}
    if not cfg_pfs:
        meta["skipped_reasons"].append("pureFuturesArbitrage config missing")
        return trades, meta

    max_pairs = int(cfg_pfs.get("maxConcurrentPairs", 3))
    trade_usd = float(cfg_pfs.get("tradeUsdPerPair", 500.0))
    min_spread = float(cfg_pfs.get("minSpreadPct", 0.05))
    max_spread = float(cfg_pfs.get("maxSpreadPct", 0.50))
    exit_edge = float(cfg_pfs.get("exitThresholdPct", 0.01))
    fee_rates = cfg_pfs.get("feeRates") or {}  # {venue: pct}
    max_mark_spread = float(cfg_pfs.get("maxMarkSpreadPct", 1.0))

    # 1) Build funding rate matrix
    #    For each asset, find all venue pairs with their spreads
    all_assets: dict[str, dict[str, float]] = {}  # {asset: {venue: rate}}
    for venue, symbols in funding_rates.items():
        for symbol, rate in symbols.items():
            asset = _base_from_symbol(symbol)
            if not asset:
                continue
            all_assets.setdefault(asset, {})[venue] = float(rate)

    # 2) Check existing positions for exits
    existing_pairs = _extract_existing_pairs(futures_state)
    for pair_id, pair_info in existing_pairs.items():
        asset = pair_info["base"]
        long_venue = pair_info["long_venue"]
        short_venue = pair_info["short_venue"]

        long_rate = all_assets.get(asset, {}).get(long_venue)
        short_rate = all_assets.get(asset, {}).get(short_venue)
        if long_rate is None or short_rate is None:
            meta["skipped_reasons"].append(
                f"{pair_id}: rate unavailable for exit check"
            )
            continue

        current_spread = short_rate - long_rate
        if current_spread <= exit_edge:
            # Close both legs
            trades.append(
                {
                    "symbol": f"{asset}USDT",
                    "type": "close_long",
                    "venue": long_venue,
                    "amount_base": pair_info["amount"],
                    "pair_id": pair_id,
                    "reason": f"spread_collapse: {current_spread:.4f}% ≤ {exit_edge}%",
                }
            )
            trades.append(
                {
                    "symbol": f"{asset}USDT",
                    "type": "close_short",
                    "venue": short_venue,
                    "amount_base": pair_info["amount"],
                    "pair_id": pair_id,
                    "reason": f"spread_collapse: {current_spread:.4f}% ≤ {exit_edge}%",
                }
            )
            meta["pairs_closed"].append(
                {
                    "pair_id": pair_id,
                    "reason": "spread_collapse",
                    "current_spread": round(current_spread, 6),
                }
            )

    # 3) Build spread matrix and find candidates
    active_pair_keys = {p["base"] for p in existing_pairs.values()}
    candidates: list[dict[str, Any]] = []

    for asset, venue_rates in all_assets.items():
        if asset in active_pair_keys:
            continue

        venues = sorted(venue_rates.keys())
        for i, va in enumerate(venues):
            for vb in venues[i + 1 :]:
                rate_a = venue_rates[va]
                rate_b = venue_rates[vb]

                # short at higher rate, long at lower
                if rate_a >= rate_b:
                    short_venue, short_rate = va, rate_a
                    long_venue, long_rate = vb, rate_b
                else:
                    short_venue, short_rate = vb, rate_b
                    long_venue, long_rate = va, rate_a

                spread = short_rate - long_rate
                if spread < min_spread or spread > max_spread:
                    continue

                long_sym = f"{asset}USDT"
                short_sym = f"{asset}USDT"
                long_fee, short_fee, total_fee = pair_open_taker_fee_pct(
                    long_venue,
                    long_sym,
                    short_venue,
                    short_sym,
                    fee_cache=fee_cache,
                    config_overrides=fee_rates,
                )
                net_edge = spread - total_fee

                if net_edge <= 0:
                    continue

                annual = _annual_pct(net_edge, 8.0)

                # 标记价差过滤：如果提供了 mark_prices，计算并过滤
                mark_spread_pct = 0.0
                if mark_prices is not None:
                    long_sym = f"{asset}USDT"
                    long_mp = mark_prices.get(long_venue, {}).get(long_sym, 0.0)
                    short_mp = mark_prices.get(short_venue, {}).get(long_sym, 0.0)
                    if long_mp > 0 and short_mp > 0:
                        mark_spread_pct = (
                            abs(long_mp - short_mp) / max(long_mp, short_mp) * 100.0
                        )
                        if mark_spread_pct > max_mark_spread:
                            continue

                candidates.append(
                    {
                        "base": asset,
                        "long_venue": long_venue,
                        "short_venue": short_venue,
                        "long_rate_pct": long_rate,
                        "short_rate_pct": short_rate,
                        "spread_pct": round(spread, 6),
                        "total_fee_pct": round(total_fee, 4),
                        "net_edge_pct": round(net_edge, 6),
                        "annual_pct": round(annual, 1),
                        "mark_spread_pct": round(mark_spread_pct, 6),
                    }
                )

    candidates.sort(key=lambda x: -x["net_edge_pct"])
    meta["spread_matrix"] = candidates[:20]

    # 4) Open top-N
    slots = max(0, max_pairs - len(existing_pairs))
    for pair in candidates[:slots]:
        asset = pair["base"]
        price = prices.get(asset, 0.0)
        if price <= 0:
            meta["skipped_reasons"].append(f"{asset}: price unavailable")
            continue

        amount_base = round(trade_usd / price, 6)
        pair_id = f"{asset}:{pair['long_venue']}:{pair['short_venue']}"

        trades.append(
            {
                "symbol": f"{asset}USDT",
                "type": "open_long",
                "venue": pair["long_venue"],
                "amount_base": amount_base,
                "amount_usdt": round(trade_usd, 2),
                "pair_id": pair_id,
                "funding_rate_pct": pair["long_rate_pct"],
                "meta": {
                    "spread_pct": pair["spread_pct"],
                    "net_edge_pct": pair["net_edge_pct"],
                    "annual_pct": pair["annual_pct"],
                },
            }
        )
        trades.append(
            {
                "symbol": f"{asset}USDT",
                "type": "open_short",
                "venue": pair["short_venue"],
                "amount_base": amount_base,
                "amount_usdt": round(trade_usd, 2),
                "pair_id": pair_id,
                "funding_rate_pct": pair["short_rate_pct"],
                "meta": {
                    "spread_pct": pair["spread_pct"],
                    "net_edge_pct": pair["net_edge_pct"],
                    "annual_pct": pair["annual_pct"],
                },
            }
        )

        meta["pairs_opened"].append(pair)

    return trades, meta


def _base_from_symbol(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith("USDT"):
        base = s[:-4]
        return base if base else ""
    return s


def _fee_pct(
    venue: str,
    symbol: str,
    fee_rates: dict[str, float],
    fee_cache: dict[tuple[str, str], dict[str, float]] | None = None,
) -> float:
    """获取交易所永续合约 taker 费率（按 symbol，可缓存/配置覆盖）。"""
    return taker_fee_pct(
        venue,
        symbol,
        fee_cache=fee_cache,
        config_overrides=fee_rates or None,
    )


def _annual_pct(rate_pct_per_8h: float, interval_h: float = 8.0) -> float:
    if interval_h <= 0:
        interval_h = 8.0
    periods_per_year = (365.0 * 24.0) / interval_h
    return abs(rate_pct_per_8h / 100.0) * periods_per_year * 100.0


def _extract_existing_pairs(
    futures_state: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """从持仓状态中提取已有的配对头寸。

    Pure futures pairs are tracked by pair_id convention:
    pair_id = "BASE:long_venue:short_venue"
    """
    existing: dict[str, dict[str, Any]] = {}
    positions = futures_state.get("positions", {})
    # Group by base asset and pair_id
    pair_positions: dict[str, list[dict[str, Any]]] = {}
    for symbol, pos in positions.items():
        pair_id = pos.get("pair_id")
        if pair_id:
            pair_positions.setdefault(pair_id, []).append(
                {
                    "symbol": symbol,
                    **pos,
                }
            )

    for pair_id, legs in pair_positions.items():
        if len(legs) < 2:
            continue
        long_leg = next((leg for leg in legs if leg.get("side") == "long"), None)
        short_leg = next((leg for leg in legs if leg.get("side") == "short"), None)
        if not long_leg or not short_leg:
            continue

        base = _base_from_symbol(long_leg["symbol"])
        amount = min(
            float(long_leg.get("amount", 0)), float(short_leg.get("amount", 0))
        )
        existing[pair_id] = {
            "base": base,
            "long_venue": long_leg.get("venue", ""),
            "short_venue": short_leg.get("venue", ""),
            "amount": amount,
        }

    return existing
