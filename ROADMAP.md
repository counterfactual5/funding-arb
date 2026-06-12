# Roadmap

Last updated: 2026-06-12

## Completed

### Pure Futures Spread (CLI core)

| Area | Status | Entry points |
|------|--------|--------------|
| Strategy + scan | Done | `scripts/strategies/futures/pure_futures_spread.py`, `scripts/cli/scan_pure_futures_spreads.py` |
| Execution + positions | Done | `scripts/execution/pure_futures_executor.py`, `pure_futures_trade.py` |
| Runner + watcher | Done | `run_pure_futures_spread.py`, `pure_futures_watcher.py` |
| Settlement mismatch | Done | `settle_mismatch_planner.py` |
| Interval-group edge thresholds | Done | hourly-normalized spread + per-group net-edge bars: `min_edge_1h` (both-1h, lower) / `min_edge_mismatch` (cross-interval risk premium, higher) in `scanner.py`; Scanner UI same/cross filter |
| Orchestrator | Done | `orchestrate_funding.py --pure-futures` |
| Backtest + history | Done | `backtest_pure_futures_spread.py`, `funding_history_source.py` |
| Cash-and-carry / unified scan | Done | `scan_funding_arbitrage.py`, `scan_unified_funding.py` |
| Credentials | Done | `scripts/core/credentials.py`, `setup_credentials.py` |

### Perp DEX trading (scan + execute)

| Venue | Status | Notes |
|-------|--------|-------|
| **Hyperliquid** | Done | `venues/hyperliquid.py` — read paths and dry-run work standalone; live order signing lazily imports the sibling `../hyperliquid/scripts` repo (`HYPERLIQUID_API_KEY/SECRET`) |
| **Aster** | Done | `venues/aster.py` — Binance-fapi-compatible (`fapi.asterdex.com`), HMAC signing via `ASTER_API_KEY/SECRET`; public scan/dry-run need no keys |
| **Lighter** | Done | `venues/lighter.py` — zk order book via `lighter-sdk` SignerClient (async, wrapped); creds: `LIGHTER_API_PRIVATE_KEY` + `LIGHTER_ACCOUNT_INDEX` (or `LIGHTER_L1_ADDRESS`) + `LIGHTER_API_KEY_INDEX` |
| **EdgeX** | Scan done; trade dry-run done, live unverified | `venues/edgex_funding.py` (V1 scan) + `venues/edgex.py` (V2 SDK trade). No batch ticker + strict Cloudflare limits → bounded base whitelist (`EDGEX_SCAN_BASES`), low concurrency (`EDGEX_SCAN_WORKERS`), 60s snapshot cache. Trading via `edgex-python-sdk>=2.0.0` (aggressive-limit orders; creds `EDGEX_ACCOUNT_ID` + `EDGEX_TRADING_PRIVATE_KEY`); dry-run + read paths tested, live signing + position/balance field layout pending a real account. See `plans/edgex-integration-plan.md` |
| Depth checks | Done | `market/futures_depth.py` covers all 7 venues; `depthCheckFailOpen=false` blocks opens on DEX order-book fetch failures |
| Capability API | Done | `GET /api/settings/venues` reports `scan_capable` / `trade_capable` / `live_ready`; `positions/open` rejects scan-only venues; Scanner UI disables Open accordingly |

### Full-stack dashboard

| Area | Status | Notes |
|------|--------|-------|
| FastAPI backend | Done | `server/` — scanner, positions, backtest, settings, WebSocket |
| Vue 3 web UI | Done | `web/` — Scanner, Positions, Backtest, Settings |
| Startup scripts | Done | `start.sh`, `start.ps1` |

### AI / CLI skill

Project CLI skill: [`SKILL.md`](SKILL.md) (repo root)

---

## In progress / partial

| Item | State | Notes |
|------|-------|-------|
| **Hyperliquid live keys** | Env-dependent | Dry-run and mocked tests run anywhere; live orders additionally need the sibling `../hyperliquid` checkout and wallet keys |

---

## Planned — venue expansion (Perp DEX)

Goal: treat on-chain / hybrid perps like CEX venues in the same scan → decide → execute pipeline.
Hyperliquid / Aster / Lighter shipped (see Completed above); remaining candidates:

| Venue | Type | Priority | Work items |
|-------|------|----------|------------|
| **dYdX v4** | Cosmos app-chain | P2 | Funding + mark via indexer API; wallet signing (Keplr / private key) |
| **Drift** | Solana perp | P2 | Drift SDK, SOL/USDC margin, funding intervals |
| **GMX v2** | Arbitrum/Avalanche | P3 | Funding differs from CEX (borrow fees); adapter in `venues/` |
| **Vertex** | Arbitrum hybrid | P3 | REST + signing; cross-margin model |

**Shared engineering tasks for each new venue:**

1. `venues/<name>.py` — ticker, funding, fees, positions, dry-run + live orders
2. Register in `venues/__init__.py` and scanner default venue lists
3. Fee cache + settlement interval in `scan_pure_futures_spreads.py`
4. Historical funding in `funding_history_source.py` (for backtest)
5. Settings UI + credential schema in `server/routes/settings.py`
6. Integration tests with mocked HTTP (no live keys in CI)

---

## Planned — strategy & infra

| Item | Priority | Description |
|------|----------|-------------|
| Parallel leg execution | Medium | Open/close both legs concurrently to reduce latency skew |
| Triangle / 3-venue arb | Low | Diversify venue-pair risk; needs position graph solver |
| ML funding prediction | Low | Optional entry timing; not required for core arb |
| Dynamic position sizing | Low | Kelly / volatility-adjusted `trade_usd` |
| Strategy config → live runner | Medium | Wire dashboard `settings/strategy` into scanner thresholds and executor |
| Tauri desktop polish | Low | Native window packaging, auto-update |
| Spot cross-venue price arb | Low | Separate from funding; reuse `price_oracle` |

---

## Deferred / out of scope (for now)

- Cross-chain capital movement automation (bridges) — manual treasury only
- Options / basis trades
- Social / copy trading UI

---

## Documentation (2026-06-12)

Legacy `docs/` task checklists (`TODO_*`, `PURE_FUTURES_*`) were removed after shipping. Active reference docs:

- [`docs/cross-interval-funding-model.md`](docs/cross-interval-funding-model.md) — basis-blend model for mismatched funding intervals
- [`README.md`](README.md) — setup, dashboard, CLI, API
- [`SKILL.md`](SKILL.md) — agent playbook
