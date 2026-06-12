# Project Overview (README)

Strategies, quick start, API, config

## Overview

<!-- id: overview -->

Cross-exchange funding rate arbitrage engine supporting Cash-and-Carry, unified cross-venue carry, and Pure Futures (perp–perp) spreads — with a Vue dashboard, CLI, and optional Tauri desktop shell.

| Category | Venues |
| --- | --- |
| CEX (spot + USDT-M perps) | Binance · Bitget · Bybit · OKX |
| Perp DEX (scan; trade where supported) | Hyperliquid · Aster · Lighter · EdgeX |
| Perp DEX (scan-only) | dYdX v4 (1h funding; trading adapter pending) |

## Dashboard opens

<!-- id: dashboard-open -->

All three Scanner tabs support in-table dry-run opens (live off by default). Live orders need API keys, balance, and the dry-run toggle off.

| Tab | API strategy | Executor |
| --- | --- | --- |
| Pure Futures | pure_futures | pure_futures_executor — dual perp legs |
| Cash & Carry | carry | cross_venue_executor — same-venue spot + perp |
| Unified C&C | unified | cross_venue_executor — cross-venue spot + perp |

> ⚠️ Scan-only venues (e.g. dYdX) disable Open. For EdgeX live, use verify_edgex_live.py before real orders.

## Strategies

<!-- id: strategies -->

| Strategy | CLI entry | Dashboard tab | Description |
| --- | --- | --- | --- |
| Pure Futures Spread | scan_pure_futures_spreads.py | Scanner → Pure Futures | Long perp on one venue, short on another; capture funding rate differential. No spot or borrow. |
| Cash & Carry | scan_funding_arbitrage.py | Scanner → Cash & Carry | Spot long + perp short (or reverse via borrow) on CEX. |
| Unified C&C | scan_unified_funding.py | Scanner → Unified C&C | Spot and futures legs on different venues for best combined edge. |
| Cross-asset C&C | run_cash_and_carry.py | — | Multi-asset slot contention; hold top spreads only. |

## Pure Futures metrics

<!-- id: pure-futures-metrics -->

| Field | Meaning |
| --- | --- |
| net_edge_pct | Funding spread minus open-leg taker fees (both sides) |
| mark_spread_pct | Mark-price gap between venues (entry slippage risk) |
| real_edge_pct | net_edge_pct − mark_spread_pct (conservative edge) |
| settle_mismatch | Different funding intervals (e.g. HL 1h vs CEX 8h) |

> ℹ️ Cross-interval pairs use a basis-blend model (mark vs index, weighted by settlement progress). See "Cross-Interval Funding Arbitrage" article.

## Quick Start

<!-- id: quick-start -->

```text
git clone <this-repo>
cd funding-arb
bash setup.sh
```

Browser mode: bash start.sh → http://localhost:8787

Desktop mode (requires Rust): bash start.sh --desktop

Windows: .\start.ps1 or .\start.ps1 -Desktop

## CLI scanning

<!-- id: cli-scan -->

```text
# Pure futures — default CEX
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --verbose

# Include DEX
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py \
  --venues binance,bitget,bybit,okx,hyperliquid --json

# Continuous watch → data/pure_futures_spreads.jsonl
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --watch 5

# Cash-and-carry
.venv/bin/python scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx,binance

# Unified
.venv/bin/python scripts/cli/scan_unified_funding.py --verbose
```

## Execution & trading

<!-- id: cli-trade -->

```text
# Manual open (dry-run default)
.venv/bin/python scripts/cli/pure_futures_trade.py open BTC \
  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run

# List positions
.venv/bin/python scripts/cli/pure_futures_trade.py list

# Close
.venv/bin/python scripts/cli/pure_futures_trade.py close <id> --dry-run

# Position watcher
.venv/bin/python scripts/execution/pure_futures_watcher.py \
  --config templates/config.pure_futures.spread.json --interval 30

# Orchestrator
.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose
```

> ⚠️ All trading commands default to dry-run. Never pass --live unless the user explicitly asks.

## Backtest & reports

<!-- id: backtest -->

```text
# From exchange history (no local data needed)
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --history-bases BTC,ETH,SOL --history-days 30 --capital 100000 --json

# From JSONL snapshots
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json

# Opportunity quality report
.venv/bin/python scripts/cli/report_pure_futures_spreads.py \
  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3
```

## Fee policy & VIP tiers

<!-- id: fee-policy -->

Net edge in the scanner deducts per-leg taker fees. Fee resolution lives in scripts/core/fee_providers.py.

| Mode | Behavior |
| --- | --- |
| auto | Live fee API when keys configured; else VIP tier table |
| tier | Static VIP ladder (scripts/core/vip_fee_tiers.py) |
| manual | Overrides in strategy config |

Configure in Settings → Strategy: fee_mode, venue_fee_tiers, scan thresholds (min_edge_annual / min_edge_1h / min_edge_mismatch).

## HTTP API summary

<!-- id: http-api -->

| Endpoint | Purpose |
| --- | --- |
| GET /api/scanner/opportunities | Cached scan results (venue-aware) |
| POST /api/scanner/trigger | On-demand scan |
| GET /api/scanner/status | Scan state, last scan time |
| GET/POST /api/settings/strategy | Thresholds, fee policy |
| GET /api/settings/venues | Scan/trade/live capability per venue |
| POST /api/positions/open | Open: strategy (pure_futures|carry|unified), dry_run (default true) |
| POST /api/backtest/run | Run backtest |
| WS /ws/events | scanner.update push |

## Configuration

<!-- id: config -->

- Copy .env.example → .env
- Paper mode: no keys required (dry_run: true in config)
- Live mode: exchange keys with spot + USDT-M futures trade permission; no withdrawal
- Strategy thresholds: Settings → Strategy → scripts/data/strategy_config.json
- CLI runners merge that file on start (same as dashboard)

| Exchange | Environment variables |
| --- | --- |
| Bitget | BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE |
| Binance | BINANCE_API_KEY, BINANCE_API_SECRET |
| OKX | OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE |
| Bybit | BYBIT_API_KEY, BYBIT_SECRET_KEY |
| Hyperliquid | sibling ../hyperliquid repo + wallet keys |
| Aster | ASTER_API_KEY, ASTER_API_SECRET |
| Lighter | LIGHTER_API_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX |
| EdgeX | EDGEX_ACCOUNT_ID, EDGEX_TRADING_PRIVATE_KEY |

## Testing

<!-- id: testing -->

```text
pip install -r requirements.txt
.venv/bin/python -m pytest scripts/tests/ -q
# 245+ tests — scanners, fees, venues, executor, backtest
```
