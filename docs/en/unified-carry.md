# Unified Cross-Venue Carry

Split-leg routing and transfer costs

## Overview

<!-- id: u-overview -->

Unified C&C uses the same principle as same-venue Cash & Carry, but the two legs can be split across exchanges: the futures leg goes where the rate is best, the spot / borrow leg where the cost is lowest. All CEXs are abstracted into one routing table, picking the globally best combination per asset.

> ℹ️ Even when no single venue has an extreme rate, "high rate at A + cheap spot at B" combinations often exist — Unified typically finds more opportunities than single-venue C&C. CEX only.

## Forward routing

<!-- id: u-forward -->

- Futures leg: short at the venue with the highest rate ≥ entry threshold
- Spot leg: buy at the venue with the lowest spot fee among venues that list the spot pair

```text
net_edge_pct = funding_rate_pct − futures_fee − spot_fee
```

Both legs may land on the same venue (same_venue = true), reducing to plain C&C with no transfer cost.

## Reverse routing

<!-- id: u-reverse -->

- Futures leg: long at the venue with the most negative rate
- Borrow leg: among venues that are borrowable and reverse-executable, borrow-sell where the per-period borrow cost is lowest

```text
borrow_per_period = borrow cost normalized to the futures leg interval_h
net_edge_pct = |funding_rate_pct| − borrow_per_period − futures_fee − spot_fee
```

## Cross-venue transfer cost

<!-- id: u-transfer -->

When legs sit on different venues, capital must move across exchanges. The system prices the on-chain transfer per route, producing an all-in edge:

```text
net_edge_all_in_pct = net_edge_pct − transfer_fee_pct
```

- Cross-venue routes sort by net_edge_all_in_pct (transfer included)
- Same-venue routes sort by net_edge_pct (no transfer)
- transfer_chain records the suggested chain (e.g. TRC20 / BEP20)

> ⚠️ The transfer fee is one-off and amortizes over the holding period. For short holds with small size, it can consume the entire edge — watch the gap between all-in and net.

## Dashboard opens

<!-- id: u-dashboard -->

Scanner → Unified C&C supports dry-run opens: strategy=unified with separate futures_venue and spot_venue. Cross-venue routes need USDT on both sides — no automatic transfer.

> ℹ️ Compare net_edge_pct vs net_edge_all_in_pct before opening; transfer fees can erase small cross-venue edges.

## Scanner fields

<!-- id: u-fields -->

| Field | Meaning |
| --- | --- |
| direction | forward / reverse |
| futures_venue / spot_venue | Venue of the futures / spot (borrow) leg |
| funding_rate_pct / interval_h | Futures leg rate and period |
| borrow_per_period_pct | Per-period borrow cost (reverse) |
| futures_fee_pct / spot_fee_pct | Taker fees per leg |
| net_edge_pct | Net edge after fees (excluding transfer) |
| net_edge_all_in_pct | All-in edge after transfer fee |
| transfer_chain / transfer_fee_pct | Transfer chain and cost |
| same_venue | Whether both legs share a venue |

## Code map

<!-- id: u-code -->

| Path | Role |
| --- | --- |
| scripts/backtest/unified_funding_pool.py | Core routing: best_forward / best_reverse / scan_routes |
| scripts/cli/scan_unified_funding.py | CLI scan entry |
| scripts/backtest/borrow_providers.py | Borrow rates and reverse executability |
| server/routes/scanner.py | Unified cache and fee recalculation (_recalc_unified_fees) |
