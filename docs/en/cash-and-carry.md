# Forward & Reverse Cash & Carry

Spot+perp hedge, borrow reverse, thresholds

## Overview

<!-- id: cc-overview -->

Cash & Carry hedges spot against perp on the same exchange to collect funding. The Cash & Carry tab scans each venue independently, producing forward / reverse candidates per venue. CEX only (DEXs have no spot or margin borrow).

## Forward (rate > 0)

<!-- id: cc-forward -->

```text
Leg 1: buy spot (equal notional)
Leg 2: short perp (equal notional)
```

With a positive rate, longs pay shorts: the perp short collects funding each period while the spot long hedges price. No borrow cost.

```text
net_edge_pct = rate_pct − (spot_taker + futures_taker)
```

> ℹ️ Forward requires a spot pair on that venue (has_spot). Perp-only listings are reported under forward_no_spot.

## Reverse (rate < 0)

<!-- id: cc-reverse -->

```text
Leg 1: borrow on margin and sell (equal notional)
Leg 2: long perp (equal notional)
```

With a negative rate, shorts pay longs: the perp long collects funding while the borrowed-and-sold spot hedges price. Borrow interest must be deducted from the edge.

```text
borrow_per_period = borrow_annual_pct / (365 × 24) × interval_h
net_edge_pct = |rate_pct| − borrow_per_period − (spot_taker + futures_taker)
```

> ⚠️ Reverse carries an ongoing cost: borrow interest accrues every period and the rate floats. If the negative funding fades and you do not exit, interest quickly eats the profit.

## Reverse feasibility constraints

<!-- id: cc-constraints -->

- The asset must be borrowable with sufficient quota (max_borrow)
- The venue must implement margin borrow/repay (supports_reverse_arbitrage); in live mode unsupported venues are force-disabled
- If borrow cost is too high, net_edge ≤ 0 and the candidate is excluded automatically

Negative-rate assets that cannot be borrowed are listed under reverse_not_borrowable — informational only, not executable.

## Entry / exit thresholds (config)

<!-- id: cc-thresholds -->

| Parameter | Meaning |
| --- | --- |
| entryFundingRatePct | Forward entry rate (e.g. 0.05%) |
| exitFundingRatePct | Forward exit rate (e.g. 0.01%; close below this) |
| reverseEntryFundingRatePct | Reverse entry rate (negative, e.g. −0.05%) |
| reverseExitFundingRatePct | Reverse exit rate (e.g. −0.01%) |
| minNetEdgePct | Universal fee gate: minimum net edge after fees |
| minReverseSpreadPct | Extra reverse bar: |rate| − borrow must exceed this |
| maxMinutesToSettlement | Time lock: skip entry if next settlement is more than N minutes away |

Multi-asset mode (crossAssetArbitrage) runs slot contention: a higher-edge candidate can preempt an existing position, but only if it beats preemptionFrictionBufferPct — preventing churn that bleeds fees.

## Scanner fields

<!-- id: cc-fields -->

| Field | Meaning |
| --- | --- |
| rate_pct | Current funding rate (positive = forward, negative = reverse) |
| interval_h / annual_pct | Settlement period / annualized |
| has_spot / spot_price | Spot pair availability and price (forward) |
| borrowable / max_borrow | Borrowability and quota (reverse) |
| borrow_daily_pct / borrow_annual_pct | Daily / annual borrow rate |
| borrow_per_period_pct | Borrow cost per settlement period |
| fee_pct | Spot + futures taker fees combined |
| net_edge_pct | Net edge after fees (and borrow cost) |

## Code map

<!-- id: cc-code -->

| Path | Role |
| --- | --- |
| scripts/cli/scan_funding_arbitrage.py | Per-venue scan entry (forward / reverse candidates) |
| scripts/strategies/futures/cash_and_carry.py | Single-asset decision (delegates to cross_asset engine) |
| scripts/strategies/futures/cross_asset_arbitrage.py | Multi-asset slot contention and preemption |
| scripts/execution/run_cash_and_carry.py | Execution loop (NAV sync, liquidation checks, notifications) |
| scripts/backtest/borrow_providers.py | Per-venue borrow rates and quotas |
