---
name: funding-arb-cli
description: Run funding rate arbitrage workflows in the funding-arb repo purely from the command line — scan cross-venue funding spreads, open/close pure-futures positions, run backtests, generate reports, and manage exchange credentials. Use when the user asks to scan funding rates, find arbitrage opportunities, open or close spread positions, backtest funding strategies, or operate this project without the web UI.
---

# Funding Arb CLI

All commands run from the repo root. Use the project venv: `.venv/bin/python` (fall back to `python3` if missing). Network calls hit real exchange APIs; a full 4-venue scan takes ~30-90s.

**Safety: all trading commands default to dry-run. Never pass `--live` unless the user explicitly asks for live trading.**

## 1. Scan opportunities

```bash
# Pure futures spread (perp long on one venue + perp short on another) — primary strategy
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --json
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --verbose            # human-readable
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --min-edge 0.05 --json

# Include perp DEXs (1h settlement) alongside CEXs
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py \
  --venues binance,bitget,bybit,okx,hyperliquid,aster,lighter --json

# Continuous monitoring, appends snapshots to data/pure_futures_spreads.jsonl (needed for JSONL backtests)
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --watch 5

# Cash-and-carry per venue (spot + perp on same venue)
.venv/bin/python scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx,binance

# Cross-venue unified view (spot leg and futures leg on different venues)
.venv/bin/python scripts/cli/scan_unified_funding.py --verbose
```

Prefer `--json` when you need to parse results. Key fields per opportunity: `base`, `direction` (forward/reverse), `long_venue`, `short_venue`, `spread_pct`, `fee_pct`, `net_edge_pct` (per settlement cycle, after fees), `annual_apy_pct`, `mark_spread_pct` (price divergence between venues — subtract from edge for the real entry edge), `settle_mismatch`.

## 2. Trade (manual open/close/list)

```bash
# Open: dry-run by default
.venv/bin/python scripts/cli/pure_futures_trade.py open BTC \
  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run

.venv/bin/python scripts/cli/pure_futures_trade.py list
.venv/bin/python scripts/cli/pure_futures_trade.py close <position_id> --dry-run
```

Positions ledger: `scripts/data/pure-futures/positions.json` (shared by dry-run and live; records carry a `dry_run` flag). Note: very small `--trade-usd` may abort with "Quantity floored to 0" for high-priced assets.

## 3. Watcher (monitor open positions)

```bash
.venv/bin/python scripts/execution/pure_futures_watcher.py \
  --config templates/config.pure_futures.spread.json --interval 30 --verbose
```

Long-running; start it in the background.

## 4. Backtest

```bash
# From exchange funding history (no local data needed)
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --history-bases BTC,ETH,SOL --history-days 30 --capital 100000 --json

# From scanner JSONL (requires prior --watch runs)
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json

# Opportunity quality report over recorded snapshots
.venv/bin/python scripts/cli/report_pure_futures_spreads.py \
  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3
```

## 5. Orchestrator (scan → optionally auto-open)

```bash
.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --verbose
.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose  # auto-open top pairs (dry-run)
```

## 6. Credentials & environment

```bash
.venv/bin/python scripts/cli/setup_credentials.py --check   # status; dry-run scans/backtests need no keys
.venv/bin/python scripts/cli/setup_credentials.py           # interactive setup (live trading only)
```

Supported venues: CEX `binance`, `bitget`, `bybit`, `okx` + perp DEX `hyperliquid`, `aster`, `lighter`. All seven scan and dry-run without keys. Live trading requirements:

- `hyperliquid` — sibling `../hyperliquid` repo checkout + `HYPERLIQUID_API_KEY` / `HYPERLIQUID_API_SECRET`
- `aster` — `ASTER_API_KEY` / `ASTER_API_SECRET` (Binance-compatible HMAC)
- `lighter` — `lighter-sdk` installed + `LIGHTER_API_PRIVATE_KEY`, `LIGHTER_ACCOUNT_INDEX` (or `LIGHTER_L1_ADDRESS`), optional `LIGHTER_API_KEY_INDEX` (default 2)

DEX legs are perp-only (no spot/margin); cash-and-carry and unified strategies remain CEX-only.

## 7. Tests & dashboard

```bash
.venv/bin/python -m pytest scripts/tests/ -q     # full test suite
bash start.sh                                    # web dashboard at http://localhost:8787 (FastAPI serves web/dist)
```
