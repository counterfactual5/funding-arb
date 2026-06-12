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
| Interval-group edge thresholds | Done | `min_edge_1h` / `min_edge_mismatch` in scanner + CLI runners |
| Orchestrator | Done | `orchestrate_funding.py --pure-futures` |
| Backtest + history | Done | `backtest_pure_futures_spread.py`, `funding_history_source.py` |
| Cash-and-carry / unified scan | Done | `scan_funding_arbitrage.py`, `scan_unified_funding.py` |
| Credentials | Done | `scripts/core/credentials.py`, `setup_credentials.py` |
| Strategy config → CLI | Done | `core/strategy_config.py` — Dashboard `strategy_config.json` shared by runner / orchestrate / watcher |
| Basis blend (cross-interval) | Done | `pair_pure_futures_spread()` in scanner, planner, backtest, unified pool |

### Perp DEX trading (scan + execute)

| Venue | Status | Notes |
|-------|--------|-------|
| **Hyperliquid** | Done | `venues/hyperliquid.py` — live needs sibling `../hyperliquid` repo |
| **Aster** | Done | `venues/aster.py` — Binance-fapi-compatible |
| **Lighter** | Done | `venues/lighter.py` — `lighter-sdk` |
| **EdgeX** | Scan + dry-run done; live unverified | `edgex_funding.py` + `edgex.py`; verify: `scripts/tools/verify_edgex_live.py` |
| **dYdX v4** | Scan + live code done; testnet pending | `dydx_funding.py` (orderbook-mid index via `DYDX_INDEX_MID=1`) + `dydx.py` (CexVenue adapter with SDK order builder; live needs `DYDX_ENABLE_LIVE=1` + testnet verification) |
| Depth checks | Done | `market/futures_depth.py` for CEX + DEX venues with books |
| Capability API | Done | `GET /api/settings/venues` — scan / trade / live_ready |

### Full-stack dashboard

| Area | Status | Notes |
|------|--------|-------|
| FastAPI backend | Done | `server/` — scanner, positions, backtest, settings, WebSocket |
| Vue 3 web UI | Done | `web/` — Scanner, Positions, Backtest, Docs, Settings |
| Docs (8 articles, 3 langs) | Done | `/docs` + `docs/{zh-CN,en,zh-TW}/`; CI `check_docs_sync.sh` |
| Dashboard open | Done | Pure Futures + C&C + Unified via `POST /positions/open` |
| Startup scripts | Done | `start.sh`, `start.ps1` |

### AI / CLI skill

Project CLI skill: [`SKILL.md`](SKILL.md) (repo root)

---

## In progress / partial

| Item | State | Notes |
|------|-------|-------|
| **Hyperliquid live keys** | Env-dependent | Sibling `../hyperliquid` + wallet keys for live |
| **EdgeX live trade** | Unverified | Run `verify_edgex_live.py --read-account` with funded account |
| **dYdX live orders** | Live code done | Adapter + wallet + SDK order builder implemented; testnet verification pending — see `plans/dydx-trading-plan.md` |
| **Drift** | Not started | Solana perp SDK — P2 |

---

## Planned — venue expansion (Perp DEX)

| Venue | Type | Priority | Work items |
|-------|------|----------|------------|
| **dYdX v4** | Cosmos app-chain | P2 | ~~Funding scan~~ ~~venue adapter + dry-run~~ ~~live order builder~~ done; remaining: testnet end-to-end verification |
| **Drift** | Solana perp | P2 | Drift SDK, SOL/USDC margin, funding intervals |
| **GMX v2** | Arbitrum/Avalanche | P3 | Borrow-fee model differs from CEX funding |
| **Vertex** | Arbitrum hybrid | P3 | REST + signing |

**Shared engineering tasks for each new venue:**

1. `venues/<name>.py` — ticker, funding, fees, positions, dry-run + live orders
2. Register in `venues/__init__.py` and scanner `PURE_ALL_VENUES`
3. Fee cache + settlement interval in `scan_pure_futures_spreads.py`
4. Historical funding in `funding_history_source.py`
5. Settings UI + credential schema
6. Integration tests with mocked HTTP

---

## Planned — strategy & infra

| Item | Priority | Description |
|------|----------|-------------|
| Parallel leg execution | Done | `parallelLegs: true` default |
| Strategy config → live runner | Done | `core/strategy_config.py` |
| Triangle / 3-venue arb | Low | Position graph solver |
| ML funding prediction | Low | Optional entry timing |
| Dynamic position sizing | Low | Kelly / vol-adjusted `trade_usd` |
| Tauri desktop polish | Low | Native packaging |
| Spot cross-venue price arb | Low | Reuse `price_oracle` |

---

## Deferred / out of scope (for now)

- Cross-chain capital movement automation (bridges)
- Options / basis trades
- Social / copy trading UI

---

## Documentation

In-app **Docs** (`/docs`) mirrors `docs/{zh-CN,en,zh-TW}/`. Regenerate: `npx tsx scripts/tools/export_docs_md.mts`.

- [`docs/README.md`](docs/README.md)
- [`docs/cross-interval-funding-model.md`](docs/cross-interval-funding-model.md)
- [`README.md`](README.md) · [`SKILL.md`](SKILL.md)
- EdgeX plan: [`plans/edgex-integration-plan.md`](plans/edgex-integration-plan.md)
