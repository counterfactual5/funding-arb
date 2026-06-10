# Migration from cex-adaptive-dca

Funding-arb was extracted from [cex-adaptive-dca](https://github.com/counterfactual5/cex-adaptive-dca) on 2026-06-10.

**Source repo**: `counterfactual5/cex-adaptive-dca`
**Source commit**: `3a938857bdf23b1cd9ddcba59ec8df5752ebc22e`

## What was vendored

| Layer | Files | Description |
|---|---|---|
| A (strategy/runner/CLI) | 12 | All funding-arb exclusive code |
| B (funding data + accounting) | 4 | `funding_cache.py`, `funding_providers.py`, `funding_batch.py`, `delta_neutral_portfolio.py` — duplicated copy |
| Neutral infra | ~15 | `venues/`, `core/`, `transfer/`, `market/parallel_fetch.py`, `market/price_oracle.py`, `execution/cross_venue_executor.py` — duplicated copy |
| Templates | 6 | `config.cash_and_carry.*.json` |

## Cross-repo synchronization

Layer B (funding data + accounting) exists in both repos. If a bug fix is applied to one copy, it must be ported to the other. The hermetic tests (`test_funding_arbitrage.py`, `test_reverse_margin.py`) are shared between repos and should catch regressions in both.
