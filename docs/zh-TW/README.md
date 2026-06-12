# 專案概覽（README）

策略、快速啟動、API、配置

## 專案概覽

<!-- id: overview -->

Funding Rate Arbitrage Engine —— 跨交易所資金費率套利引擎，支援 Cash-and-Carry、Unified 跨所 carry 以及 Pure Futures（永續對永續）spread，搭配 Vue 儀表盤、CLI 和可選 Tauri 桌面端。

| 類別 | 交易所 |
| --- | --- |
| CEX（現貨 + USDT-M 永續） | Binance · Bitget · Bybit · OKX |
| Perp DEX（掃描；支援交易處） | Hyperliquid · Aster · Lighter · EdgeX |

## 策略概覽

<!-- id: strategies -->

| 策略 | CLI 入口 | 儀表盤 Tab | 說明 |
| --- | --- | --- | --- |
| Pure Futures Spread | scan_pure_futures_spreads.py | Scanner → Pure Futures | 一所長做多，另一所做空，捕獲資金費率差。無需現貨或借貸。 |
| Cash & Carry | scan_funding_arbitrage.py | Scanner → Cash & Carry | CEX 現貨多頭 + 永續空頭（或借幣反向）。 |
| Unified C&C | scan_unified_funding.py | Scanner → Unified C&C | 現貨腿與期貨腿在不同交易所，取最優組合。 |
| Cross-asset C&C | run_cash_and_carry.py | — | 多資產槽位競爭，只保留最高 spread。 |

## Pure Futures 指標

<!-- id: pure-futures-metrics -->

| 欄位 | 含義 |
| --- | --- |
| net_edge_pct | funding spread − 雙邊開倉 taker 手續費 |
| mark_spread_pct | 兩所標記價差（入場滑點風險） |
| real_edge_pct | net_edge_pct − mark_spread_pct（保守邊際） |
| settle_mismatch | 結算週期不同（如 HL 1h vs CEX 8h） |

> ℹ️ 跨週期配對使用 basis-blend 模型（mark vs index，按結算進度加權）。詳見「跨週期資金費率套利」文件。

## 快速啟動

<!-- id: quick-start -->

```text
git clone <this-repo>
cd funding-arb
bash setup.sh
```

瀏覽器模式：bash start.sh → http://localhost:8787

桌面模式（需要 Rust）：bash start.sh --desktop

Windows：.\start.ps1 或 .\start.ps1 -Desktop

## CLI 掃描

<!-- id: cli-scan -->

```text
# Pure futures — 預設 CEX
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --verbose

# 加入 DEX
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py \
  --venues binance,bitget,bybit,okx,hyperliquid --json

# 持續監控 → data/pure_futures_spreads.jsonl
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --watch 5

# Cash-and-carry
.venv/bin/python scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx,binance

# Unified
.venv/bin/python scripts/cli/scan_unified_funding.py --verbose
```

## 執行與交易

<!-- id: cli-trade -->

```text
# 手動開倉（dry-run 預設）
.venv/bin/python scripts/cli/pure_futures_trade.py open BTC \
  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run

# 檢視持倉
.venv/bin/python scripts/cli/pure_futures_trade.py list

# 平倉
.venv/bin/python scripts/cli/pure_futures_trade.py close <id> --dry-run

# 持倉監控
.venv/bin/python scripts/execution/pure_futures_watcher.py \
  --config templates/config.pure_futures.spread.json --interval 30

# 編排器
.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose
```

> ⚠️ 所有交易命令預設 dry-run。除非使用者明確要求，否則不要傳 --live。

## 回測與報告

<!-- id: backtest -->

```text
# 從交易所歷史（無需本地資料）
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --history-bases BTC,ETH,SOL --history-days 30 --capital 100000 --json

# 從 JSONL 快照
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json

# 機會質量報告
.venv/bin/python scripts/cli/report_pure_futures_spreads.py \
  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3
```

## 費率策略與 VIP 檔位

<!-- id: fee-policy -->

Scanner 的 net_edge 已扣除每腿 taker 手續費。費率解析邏輯位於 scripts/core/fee_providers.py。

| 模式 | 行為 |
| --- | --- |
| auto | 有 API key 時用實時費率；否則用 VIP 檔位表 |
| tier | 靜態 VIP 檔位（scripts/core/vip_fee_tiers.py） |
| manual | 在策略配置中手動覆蓋 |

在 Settings → Strategy 中配置：fee_mode、venue_fee_tiers、掃描閾值（min_edge_annual / min_edge_1h / min_edge_mismatch）。

## HTTP API 摘要

<!-- id: http-api -->

| 端點 | 用途 |
| --- | --- |
| GET /api/scanner/opportunities | 快取掃描結果（venue 感知） |
| POST /api/scanner/trigger | 觸發掃描 |
| GET /api/scanner/status | 掃描狀態、上次掃描時間 |
| GET/POST /api/settings/strategy | 策略閾值、費率策略 |
| GET /api/settings/venues | 各所 scan/trade/live 能力 |
| POST /api/positions/open | 開倉（預設 dry-run） |
| POST /api/backtest/run | 執行回測 |
| WS /ws/events | scanner.update 推送 |

## 配置

<!-- id: config -->

- 複製 .env.example → .env
- Paper 模式不需要 API key（配置中 dry_run: true）
- Live 模式需要 spot + USDT-M futures 交易許可權；不需要提現許可權

| 交易所 | 環境變數 |
| --- | --- |
| Bitget | BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE |
| Binance | BINANCE_API_KEY, BINANCE_API_SECRET |
| OKX | OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE |
| Bybit | BYBIT_API_KEY, BYBIT_SECRET_KEY |
| Hyperliquid | sibling ../hyperliquid repo + wallet keys |
| Aster | ASTER_API_KEY, ASTER_API_SECRET |
| Lighter | LIGHTER_API_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX |
| EdgeX | EDGEX_ACCOUNT_ID, EDGEX_TRADING_PRIVATE_KEY |

## 測試

<!-- id: testing -->

```text
pip install -r requirements.txt
.venv/bin/python -m pytest scripts/tests/ -q
# 245+ tests — scanners, fees, venues, executor, backtest
```
