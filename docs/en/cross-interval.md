# Cross-Interval Funding Arbitrage

Basis blend, real_edge, and implementation

## Background

<!-- id: ci-background -->

Each exchange publishes rate_pct for its own settlement period. Periods vary:

| Exchange | Typical period | Meaning |
| --- | --- | --- |
| Binance / OKX / Bybit | 8h | Settles every 8 hours |
| Bitget | 2h or 8h | Some contracts 2h |
| Hyperliquid / Lighter / dYdX v4 | 1h | Hourly settlement |
| EdgeX | 4h | Majors typically 240min |
| Aster | Per contract | Often 8h via fundingInfo |

Comparing 0.01% (1h) vs 0.05% (8h) directly would be severely distorted.

## Why linear extrapolation is not enough

<!-- id: ci-linear-problem -->

```text
# Naive normalization
rate_hourly = rate_pct / interval_h
spread = (short_hourly - long_hourly) × min(interval_long, interval_short)
```

Fine right after settlement (basis converged). But mid-period, premium (mark vs index) accumulates; next-period funding is often closer to the basis-implied rate than the published rate_pct.

## Model goals

<!-- id: ci-model-goal -->

- Normalize both sides to an hourly basis
- Use mark-index basis to estimate expected funding for the remainder of the period
- Weighted blend of published rate and basis-implied rate by settlement progress
- Output interpretable fields: spread_source, settle_progress, basis_pct

## When the model applies

<!-- id: ci-when -->

```text
is_mismatch = |long_interval_h − short_interval_h| > 0.5
```

- is_mismatch == false → same interval: rate_pct / interval_h, spread_source = rate
- is_mismatch == true → basis blend (with index) or linear fallback (without index)

## Data dependencies

<!-- id: ci-data-deps -->

| Field | Description |
| --- | --- |
| rate_pct | Pending funding rate (%) |
| interval_h | Settlement period (hours) |
| mark_price | Mark price |
| index_price | Index / oracle price |
| next_funding_ts | Next settlement time (ms) |
| last_settle_ts | Last settlement time (ms), derivable from next − interval |

| Exchange | index_price source | Basis blend |
| --- | --- | --- |
| Binance | premiumIndex.indexPrice | ✅ |
| Bitget | indexPrice | ✅ |
| Bybit | indexPrice | ✅ |
| OKX | idxPx | ✅ |
| Hyperliquid | oraclePx | ✅ |
| Aster | Inherits Binance provider | ✅ |
| Lighter | No public index → 0 | ❌ rate_linear |
| EdgeX | No public index → 0 | ❌ rate_linear |
| dYdX v4 | Indexer oraclePrice only (mark≈index) | ❌ rate_linear |

> ℹ️ dYdX on-chain rate = 60-min premium TWAP + interest; paid hourly. nextFundingRate is a forecast — use min_edge_mismatch vs 8h CEX.

## Settlement progress

<!-- id: ci-progress -->

```text
progress = elapsed / period_length   ∈ [0, 1]

# Priority:
1. Both timestamps: (now − last) / (next − last)
2. Only next_funding_ts: infer from time remaining
3. None: fallback 0.5
```

## Basis premium

<!-- id: ci-basis -->

```text
basis_pct = (mark_price − index_price) / index_price × 100%
```

| Type | Cap per period | Notes |
| --- | --- | --- |
| Binance / Bybit / Bitget / OKX / Aster / EdgeX | ±0.30% | ~3× typical funding clamp |
| Hyperliquid / Lighter | ±0.50% | No hard EMA premium cap |
| Unknown | ±0.50% | DEFAULT_BASIS_CAP_PCT |

## Blended hourly rate & edge

<!-- id: ci-blend -->

```text
rate_hourly  = rate_pct / interval_h
basis_hourly = basis_pct / interval_h
blended_hourly = (1 − progress) × rate_hourly + progress × basis_hourly
```

```text
eff_interval = min(long_interval_h, short_interval_h)
spread_pct   = (short_blended − long_blended) × eff_interval
net_edge_pct = spread_pct − fee_pct (open-leg taker both sides)
real_edge_pct = net_edge_pct − mark_spread_pct
```

## Flow

<!-- id: ci-flow -->

Fetch rate / mark / index / timestamps → check interval gap > 0.5h → compute progress & basis → if index: basis_blend, else: rate_linear → synthesize spread → net_edge = spread − fees → mark_spread filter + min_edge threshold.

## Scanner output fields

<!-- id: ci-fields -->

| Field | Description |
| --- | --- |
| settle_mismatch | Cross-interval flag |
| same_interval | not settle_mismatch |
| long_interval_h / short_interval_h | Per-leg settlement period |
| spread_source | rate / basis_blend / rate_linear |
| long_basis_pct / short_basis_pct | Per-leg mark-index premium (%) |
| long_settle_progress / short_settle_progress | Blend weight (= progress) |
| spread_pct | Blended spread (%) |
| net_edge_pct | Edge after fees (%) |
| mark_spread_pct | Mark price gap (%) |

## Risk overlays

<!-- id: ci-risk -->

- min_edge_mismatch: higher bar for cross-interval pairs (Settings)
- min_edge_1h: lower bar when both legs settle hourly
- max_mark_spread_pct: discard if cross-venue mark gap exceeds threshold
- settle_mismatch_planner: executor normalizes to 8h window, analyzes cash-flow asymmetry
- VIP fee policy affects fee_pct in net_edge / real_edge

## Code map

<!-- id: ci-code-map -->

| Path | Role |
| --- | --- |
| scripts/core/cross_interval_funding.py | Pure blend functions (unit-testable) |
| scripts/cli/scan_pure_futures_spreads.py | Scan entry, invokes blend model |
| scripts/tests/test_cross_interval_funding.py | Model unit tests |
| scripts/execution/settle_mismatch_planner.py | Executor cash-flow / 8h normalization |
| server/routes/scanner.py | API cache, min_edge_mismatch filter |
| web/src/views/Scanner.vue | UI: settle_mismatch, Cross filter, real edge |

## Numerical example

<!-- id: ci-example -->

Scenario: BTC, Hyperliquid vs Binance, cross-interval.

| Leg | rate_pct | interval_h | basis_pct | progress |
| --- | --- | --- | --- | --- |
| Short @ HL | 0.04 | 1 | +0.30% | 0.85 |
| Long @ Binance | 0.08 | 8 | +0.05% | 0.25 |

```text
# HL leg
rate_hourly  = 0.04 / 1 = 0.04
basis_hourly = 0.30 / 1 = 0.30
blended      = 0.15×0.04 + 0.85×0.30 ≈ 0.261 %/h

# Binance leg
rate_hourly  = 0.08 / 8 = 0.01
basis_hourly = 0.05 / 8 = 0.00625
blended      = 0.75×0.01 + 0.25×0.00625 ≈ 0.0094 %/h

# Spread (eff_interval = 1h)
spread_pct ≈ (0.261 − 0.0094) × 1 ≈ 0.252%
net_edge ≈ 0.252 − 0.11 = 0.14%
```

> ℹ️ With naive linear extrapolation, HL would be only 0.04%/h, underestimating its advantage as the short leg.

## EdgeX 4h linear fallback example

<!-- id: ci-example-edgex -->

Scenario: BTC, EdgeX (4h, no index) vs Binance (8h). The EdgeX leg uses rate_linear; Binance uses basis_blend.

| Leg | rate_pct | interval_h | blend |
| --- | --- | --- | --- |
| Short @ EdgeX | 0.02 | 4 | rate_linear → 0.02/4 = 0.005 %/h |
| Long @ Binance | 0.08 | 8 | basis_blend (has index) |

```text
eff_interval = min(4, 8) = 4h
spread ≈ (short_hourly − long_blended) × 4
Also requires min_edge_mismatch and settle_mismatch_planner cash-flow checks
```

## Known limitations

<!-- id: ci-limits -->

| Item | Description |
| --- | --- |
| Cash-flow penalty | planner adds timing penalty on scanner net_edge, not a second spread calc |
| Global basis cap | Fixed ±1%/period, not per-exchange premium clamp |
| No-index DEXs | Lighter, EdgeX can only use rate_linear |
| Legacy JSONL | Old snapshots without index_price / progress cannot replay blend model |
