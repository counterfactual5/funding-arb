# Pure Futures Spread

Perp-perp rate differential, net / real edge

## Overview

<!-- id: pf-overview -->

Pure Futures is the primary strategy: hold a perp long on one exchange and a perp short on another for the same asset, capturing the funding rate differential. No spot, no borrowing, inherently cross-venue — perp DEXs (Hyperliquid / Aster / Lighter / EdgeX; dYdX scan-only) can participate.

> ℹ️ Versus Cash & Carry: both legs are perps with lower taker fees, no spot slippage, and no dependency on spot listings or borrow quotas.

## Core mechanics

<!-- id: pf-mechanics -->

For each asset, compare rates across venues: short where the rate is higher (receive more / pay less), long where it is lower.

```text
spread_pct  = short_rate − long_rate (short the higher-rate leg, long the lower)
net_edge_pct = spread_pct − (long_taker + short_taker)
real_edge_pct = net_edge_pct − mark_spread_pct
```

mark_spread_pct is the relative mark price gap between venues: one leg fills rich, the other cheap — a price mismatch you absorb at entry. real_edge deducts it, giving the most conservative executable edge; the Scanner sorts and filters by it by default.

## Forward vs Reverse

<!-- id: pf-direction -->

The long/short assignment is always "short the higher rate, long the lower". The direction label only describes the rate regime:

| direction | Condition | Typical shape |
| --- | --- | --- |
| forward | At least one leg rate ≥ 0 | Short leg collects positive funding (or mixed signs) |
| reverse | Both legs negative | Long leg collects negative funding; short leg pays less |

## Versus Cash & Carry

<!-- id: pf-vs-cc -->

|  | Cash & Carry | Pure Futures |
| --- | --- | --- |
| Legs | Spot + perp | Perp + perp |
| Fees | Spot taker is high (~0.1%) | Two low perp takers |
| Cross-venue | Only via Unified | Inherently cross-venue |
| Borrowing | Required for reverse | Not needed |
| DEX participation | No | HL / Aster / Lighter / EdgeX / dYdX (scan) |
| Return source | Absolute rate at one venue | Rate differential between venues |

## Thresholds and filters

<!-- id: pf-thresholds -->

| Parameter | Meaning |
| --- | --- |
| min_spread | Minimum raw rate spread (default 0.03%) |
| min_edge | Minimum net edge after fees (default 0.01%) |
| min_edge_1h | Dedicated (lower) bar for both-1h pairs |
| min_edge_mismatch | Dedicated (higher) bar for cross-interval pairs |
| max_mark_spread_pct | Discard if the cross-venue mark gap exceeds this |

min_edge_1h is lower because hourly settlement turns capital faster with no timing risk; min_edge_mismatch is higher as a risk premium for unsynchronized settlements. Configure in Settings → Strategy (scripts/data/strategy_config.json); shared by Scanner API and CLI runners.

## Settings and CLI config

<!-- id: pf-settings -->

| Dashboard field | CLI / template field |
| --- | --- |
| min_spread_annual | pureFuturesArbitrage.minSpreadPct |
| min_edge_annual | pureFuturesArbitrage.minNetEdgePct |
| min_edge_1h / min_edge_mismatch | Applied per row by interval group in runners |
| trade_usd | pureFuturesArbitrage.tradeUsdPerPair |
| max_positions | pureFuturesArbitrage.maxConcurrentPairs |
| scan_venues | pureFuturesArbitrage.venues |
| scan_interval_sec | scanIntervalMinutes (sec ÷ 60) |
| fee_mode / venue_fee_tiers | Resolved via fee_providers at scan time |

templates/config.pure_futures.spread.json keeps execution knobs (parallelLegs, depthCheck, dry_run). Thresholds come from strategy_config.json via core/strategy_config.py → apply_strategy_to_pure_futures_cfg().

## Cross-interval pairs

<!-- id: pf-cross-interval -->

When the legs settle on different intervals (settle_mismatch, e.g. HL 1h vs Binance 8h), rate_pct is not directly comparable. The system normalizes to hourly, then blends in mark-index basis weighted by settlement progress (basis blend).

- spread_source = rate: same interval, published rates used directly
- spread_source = basis_blend: cross-interval with index available, blend model active
- spread_source = rate_linear: cross-interval without index (Lighter / EdgeX legs), linear fallback

Full derivation, per-venue index sources, and a numerical example: see "Cross-Interval Funding Arbitrage".

## Execution and monitoring

<!-- id: pf-execution -->

- Manual trading: pure_futures_trade.py open / list / close (dry-run default)
- Automated: run_pure_futures_spread.py --once / --watch
- Position monitoring: pure_futures_watcher.py tracks rates and edge, alerts on exit conditions
- Pre-open depth check: futures_depth.py; DEX order-book fetch failures block opens

> ⚠️ Cross-interval pairs pass through settle_mismatch_planner for an extra cash-flow penalty on top of scanner net_edge. Planner and unified pool now share pair_pure_futures_spread with the scanner for basis blend.

## Code map

<!-- id: pf-code -->

| Path | Role |
| --- | --- |
| scripts/cli/scan_pure_futures_spreads.py | Scan entry (invokes basis blend) |
| scripts/strategies/futures/pure_futures_spread.py | Decision engine (pairing, filters, net edge) |
| scripts/execution/pure_futures_executor.py | Two-leg order placement and rollback |
| scripts/execution/pure_futures_watcher.py | Position monitoring |
| scripts/execution/settle_mismatch_planner.py | Cross-interval cash-flow analysis (executor side) |
| scripts/backtest/backtest_pure_futures_spread.py | Backtest |
| server/routes/scanner.py | API cache, threshold filters, fee recalc |
