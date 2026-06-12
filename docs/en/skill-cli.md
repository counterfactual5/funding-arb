# CLI Handbook (SKILL.md)

Scan, trade, backtest command reference

## CLI overview

<!-- id: cli-overview -->

All commands run from the repo root. Use the project venv: .venv/bin/python. Network calls hit real exchange APIs; a full 4-venue scan takes ~30-90s.

> ⚠️ All trading commands default to dry-run. Never pass --live unless the user explicitly asks for live trading.

## Scan opportunities

<!-- id: scan -->

```text
# Pure futures spread (primary strategy)
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --json
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --verbose
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --min-edge 0.05 --json

# Include perp DEXs (1h settlement)
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py \
  --venues binance,bitget,bybit,okx,hyperliquid,aster,lighter --json

# Continuous monitoring → data/pure_futures_spreads.jsonl
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --watch 5

# Cash-and-carry
.venv/bin/python scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx,binance

# Unified cross-venue
.venv/bin/python scripts/cli/scan_unified_funding.py --verbose
```

Prefer --json for parsing. Key fields: base, direction, long_venue, short_venue, spread_pct, fee_pct, net_edge_pct, annual_apy_pct, mark_spread_pct, settle_mismatch.

## Trade (open / close / list)

<!-- id: trade -->

```text
# Open: dry-run by default
.venv/bin/python scripts/cli/pure_futures_trade.py open BTC \
  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run

# List
.venv/bin/python scripts/cli/pure_futures_trade.py list

# Close
.venv/bin/python scripts/cli/pure_futures_trade.py close <position_id> --dry-run
```

Positions ledger: scripts/data/pure-futures/positions.json. Very small trade-usd may abort with "Quantity floored to 0" for high-priced assets.

## Watcher

<!-- id: watcher -->

```text
.venv/bin/python scripts/execution/pure_futures_watcher.py \
  --config templates/config.pure_futures.spread.json --interval 30 --verbose
```

Long-running; start in the background.

## Backtest

<!-- id: cli-backtest -->

```text
# From exchange history (no local data needed)
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --history-bases BTC,ETH,SOL --history-days 30 --capital 100000 --json

# From scanner JSONL
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json

# Quality report
.venv/bin/python scripts/cli/report_pure_futures_spreads.py \
  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3
```

## Orchestrator

<!-- id: orchestrator -->

```text
.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --verbose
# Auto-open top pairs (dry-run)
.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose
```

## Credentials

<!-- id: credentials -->

```text
.venv/bin/python scripts/cli/setup_credentials.py --check   # status
.venv/bin/python scripts/cli/setup_credentials.py           # interactive setup (live only)
```

Dry-run scans need no keys. DEX credentials:

- hyperliquid — sibling ../hyperliquid repo + HYPERLIQUID_API_KEY / SECRET
- aster — ASTER_API_KEY / ASTER_API_SECRET (Binance-compatible HMAC)
- lighter — lighter-sdk + LIGHTER_API_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX

> ℹ️ DEX legs are perp-only (no spot/margin); cash-and-carry and unified strategies remain CEX-only.

## Tests & dashboard

<!-- id: tests-dash -->

```text
.venv/bin/python -m pytest scripts/tests/ -q     # full suite
bash start.sh                                    # web dashboard at http://localhost:8787
```
