# Funding Rate Basics

Funding mechanics, delta-neutral hedging, strategy map

## What is funding

<!-- id: fb-what -->

Perpetual contracts have no expiry; exchanges use a funding mechanism to anchor the perp price near the spot index. Every settlement period (1h / 2h / 4h / 8h), longs and shorts pay each other based on position notional.

- Positive rate: longs pay shorts (perp trades above index, market leans long)
- Negative rate: shorts pay longs (perp trades below index, market leans short)

> ℹ️ Funding is a transfer between longs and shorts — the exchange takes no cut (trading fees are separate). The arbitrageur aims to sit on the receiving side while hedging price risk with another leg.

## Rate and settlement period

<!-- id: fb-rate -->

Each venue publishes rate_pct for its own settlement period. Periods differ, so absolute values are not directly comparable:

| Exchange | Typical period |
| --- | --- |
| Binance / OKX / Bybit | 8h |
| Bitget | 2h or 8h (per contract) |
| Hyperliquid / Lighter | 1h |
| Aster / EdgeX | 1h ~ 8h (per contract) |

```text
annual_pct ≈ |rate_pct| × (24 / interval_h) × 365
```

Example: 0.01% on an 8h period is ~10.95% annualized; 0.01% on a 1h period is ~87.6%. Same number, shorter period, much higher APY.

## Premium and the funding rate

<!-- id: fb-premium -->

The funding rate essentially tracks the premium: how far mark price deviates from index price. The higher the perp trades above index, the more positive the next funding.

```text
basis_pct = (mark_price − index_price) / index_price × 100%
```

For cross-interval pairs, this system blends the basis into the next-period funding estimate (basis blend) — see "Cross-Interval Funding Arbitrage".

## Delta-neutral hedging

<!-- id: fb-delta -->

Holding a single funding-collecting leg leaves you fully exposed to price moves. The key is a two-leg hedge: one leg collects funding, the other offsets price risk.

- Spot long + perp short — Forward Cash & Carry
- Borrow-sell + perp long — Reverse Cash & Carry
- Perp long + perp short (different venues) — Pure Futures

> ℹ️ After hedging, price moves barely affect NAV (delta ≈ 0). Returns come from funding minus fees and borrow cost.

## Strategy map

<!-- id: fb-strategies -->

| Strategy | Legs | Return source | When it works |
| --- | --- | --- | --- |
| Cash & Carry (same venue) | Spot + perp on one venue | Absolute funding rate | One venue has an extreme rate |
| Unified C&C (cross-venue) | Spot and futures legs on different venues | Best rate + lowest cost combo | Rates / fees / borrow costs diverge across venues |
| Pure Futures | Two perps, long one / short the other | Rate differential between venues | Cross-venue rate divergence; DEXs can participate |

Cross-interval pairs (e.g. HL 1h vs CEX 8h) are an advanced Pure Futures topic with a dedicated article.

## Common risks

<!-- id: fb-risks -->

| Risk | Description | Mitigation |
| --- | --- | --- |
| Rate flip | Funding turns against you after entry | Exit thresholds + watcher monitoring |
| Price mismatch | Legs fill at diverging prices | mark_spread filter; sort by real_edge |
| Fee erosion | Four taker fills across open + close | net_edge pre-deducts open fees; VIP fee policy |
| Liquidation | Perp leg uses leverage | Margin health monitoring |
| Floating borrow cost | Reverse C&C interest varies | borrow_per_period priced into the edge |
