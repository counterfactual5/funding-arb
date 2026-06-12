# Fees & Edge Calculation

fee_mode, VIP tiers, and edge fields

## Overview

<!-- id: fe-overview -->

Gross funding edges are often just a few basis points, so fees decide whether an opportunity is real. Every edge the Scanner shows already deducts open-leg taker fees; this article explains where fee rates come from and how the edge fields differ.

## Fee modes (fee_mode)

<!-- id: fe-modes -->

| Mode | Behavior |
| --- | --- |
| auto | Venues with API keys read real account fees; others estimated from the VIP tier table |
| tier | Static VIP ladder for all venues (scripts/core/vip_fee_tiers.py) |
| manual | Manual overrides from strategy config |

Configure fee_mode and per-venue VIP tiers (venue_fee_tiers) in Settings → Trading Fees. Venues already using API rates are marked "API" — tier selection has no effect on them.

## Spot vs futures fees

<!-- id: fe-spot-futures -->

Spot taker (typically 0.1%) is far higher than perp taker (~0.02% – 0.06%). This is a structural advantage of Pure Futures over C&C.

| Strategy | Open-leg fee composition |
| --- | --- |
| Cash & Carry / Unified | spot_taker + futures_taker |
| Pure Futures | long_futures_taker + short_futures_taker |

> ⚠️ net_edge deducts open fees only. A full cycle (open + close) costs double: round_trip_fee_pct = fee_pct × 2. Use the round-trip figure when estimating break-even holding time.

## VIP tier impact

<!-- id: fe-vip -->

Higher VIP means lower taker, directly amplifying net_edge / real_edge. The same spread can be negative-edge at VIP0 and positive at a high tier — a wrong fee config distorts the whole Scanner page.

- Tier tables: public exchange fee schedules, maintained in vip_fee_tiers.py
- Where to set: Settings → Trading Fees → per-venue VIP tier
- With API keys, real account rates (including rebates) take priority

## Perp DEX default taker (no API)

<!-- id: fe-dex-defaults -->

When no account API is available, fee_providers uses public defaults or contract metadata (EdgeX defaultTakerFeeRate). Reference VIP0 / default tiers below.

| Venue | Default futures taker | Notes |
| --- | --- | --- |
| Hyperliquid | 0.045% | userFees can be lower |
| Aster | 0.04% | Binance-fapi compatible |
| Lighter | 0% | Promotional zero fee; verify on-chain |
| EdgeX | 0.038% | getMetaData defaultTakerFeeRate |
| dYdX v4 | 0.05% | Scan estimate; trading not wired |

## Edge fields

<!-- id: fe-edges -->

| Field | Definition | Applies to |
| --- | --- | --- |
| spread_pct | Gross rate spread (or single-venue rate) | All |
| fee_pct | Sum of both open-leg takers | All |
| net_edge_pct | spread − fee (reverse also deducts borrow) | All |
| mark_spread_pct | Relative mark price gap between venues | Pure Futures |
| real_edge_pct | net_edge − mark_spread | Pure Futures (default sort) |
| net_edge_all_in_pct | net_edge − cross-venue transfer fee | Unified cross-venue routes |
| annual_apy_pct | Net edge annualized by settlement period | All |

Conservatism order: net_edge < real_edge (Pure Futures) / net_edge_all_in (Unified). When you see a big net_edge, check whether real / all-in still holds.

## Recalculation after fee changes

<!-- id: fe-recalc -->

After changing fee_mode or VIP tiers, no re-scan is needed: POST /api/scanner/recalc-fees recomputes net_edge / real_edge for all cached opportunities with the new rates and pushes the update over WebSocket.

- The "Save & recalculate" button in Settings calls this endpoint
- Recalculation covers pure / carry / unified caches
- Fee resolution entry points: resolve_venue_fee / parse_fee_policy in scripts/core/fee_providers.py
