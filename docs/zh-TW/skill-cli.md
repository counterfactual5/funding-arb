# CLI 手冊（SKILL.md）

掃描、交易、回測命令速查

## CLI 概覽

<!-- id: cli-overview -->

所有命令從倉庫根目錄執行，使用專案 venv：.venv/bin/python。網路請求命中真實交易所 API；一次 4 所掃描約 30-90 秒。

> ⚠️ 所有交易命令預設 dry-run。除非使用者明確要求 live 交易，否則不要傳 --live。

## 掃描機會

<!-- id: scan -->

```text
# Pure futures spread（主要策略）
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --json
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --verbose
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --min-edge 0.05 --json

# 加入 Perp DEX（1h 結算）
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py \
  --venues binance,bitget,bybit,okx,hyperliquid,aster,lighter,edgex,dydx --json

# 持續監控 → data/pure_futures_spreads.jsonl（JSONL 回測需要）
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --watch 5

# Cash-and-carry（CEX）
.venv/bin/python scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx,binance

# 跨所 Unified
.venv/bin/python scripts/cli/scan_unified_funding.py --verbose
```

推薦 --json 解析結果。每條機會關鍵欄位：base、direction（forward/reverse）、long_venue、short_venue、spread_pct、fee_pct、net_edge_pct（結算週期內扣費後）、annual_apy_pct、mark_spread_pct、settle_mismatch。

## 策略配置（與儀表盤同源）

<!-- id: strategy-config -->

Settings → Strategy 寫入 scripts/data/strategy_config.json。以下 CLI 啟動時會自動合併該檔案（閾值、scan_venues、fee 策略），無需手改模板 JSON：

- run_pure_futures_spread.py --config templates/config.pure_futures.spread.json
- orchestrate_funding.py --pure-futures
- pure_futures_watcher.py

模板仍控制 parallelLegs、depthCheck、dry_run 等執行細節。EdgeX 賬戶驗證：python3 scripts/tools/verify_edgex_live.py

## 交易（手動開/平/列表）

<!-- id: trade -->

```text
# 開倉：預設 dry-run
.venv/bin/python scripts/cli/pure_futures_trade.py open BTC \
  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run

# 列表
.venv/bin/python scripts/cli/pure_futures_trade.py list

# 平倉
.venv/bin/python scripts/cli/pure_futures_trade.py close <position_id> --dry-run
```

持倉記錄：scripts/data/pure-futures/positions.json（dry-run 和 live 共享，記錄帶 dry_run 標記）。注意：高價資產的小 trade-usd 可能觸發 "Quantity floored to 0"。

## 持倉監控

<!-- id: watcher -->

```text
.venv/bin/python scripts/execution/pure_futures_watcher.py \
  --config templates/config.pure_futures.spread.json --interval 30 --verbose
```

長駐程序，建議後臺執行。

## 回測

<!-- id: cli-backtest -->

```text
# 從交易所歷史（無需本地資料）
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --history-bases BTC,ETH,SOL --history-days 30 --capital 100000 --json

# 從 JSONL（需要先 --watch 積累）
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json

# 機會質量報告
.venv/bin/python scripts/cli/report_pure_futures_spreads.py \
  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3
```

## 編排器（掃描 → 可選自動開倉）

<!-- id: orchestrator -->

```text
.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --verbose
# 自動開倉 top pairs（dry-run）
.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose
```

## 憑證與環境

<!-- id: credentials -->

```text
.venv/bin/python scripts/cli/setup_credentials.py --check   # 狀態
.venv/bin/python scripts/cli/setup_credentials.py           # 互動式設定（僅 live 交易需要）
```

掃描和 dry-run 無需 API key。支援的 DEX 憑證：

- hyperliquid — sibling ../hyperliquid repo + HYPERLIQUID_API_KEY / SECRET
- aster — ASTER_API_KEY / ASTER_API_SECRET（Binance 相容 HMAC）
- lighter — lighter-sdk + LIGHTER_API_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX, LIGHTER_API_KEY_INDEX

> ℹ️ DEX 只有永續（無現貨/借貸）；Cash-and-carry 和 Unified 策略僅限 CEX。

## 測試與儀表盤

<!-- id: tests-dash -->

```text
.venv/bin/python -m pytest scripts/tests/ -q     # 全量測試
bash start.sh                                    # Web 儀表盤 http://localhost:8787
```
