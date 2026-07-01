# Pure Futures 永續套利

雙永續費率差、net / real edge

## 概述

<!-- id: pf-overview -->

Pure Futures 是本系統的主策略：在兩個交易所分別持有同一幣種的永續多頭與空頭，賺取兩所資金費率之差。不需要現貨、不需要借幣，天然跨所，Perp DEX（Hyperliquid / Aster / Lighter / EdgeX；dYdX 可掃描）也能參與。

> ℹ️ 相比 Cash & Carry：雙腿都是永續，taker 費率更低；無現貨滑點；不受現貨上架與借貸額度限制。

## 核心機制

<!-- id: pf-mechanics -->

對每個幣種，比較各所費率：在費率高的所做空（收更多 / 付更少），在費率低的所做多（付更少 / 收更多）。

```text
spread_pct  = short_rate − long_rate（做空高費率腿、做多低費率腿）
net_edge_pct = spread_pct − (long_taker + short_taker)
real_edge_pct = net_edge_pct − mark_spread_pct
```

mark_spread_pct 是兩所標記價的相對偏差：開倉時一腿貴一腿便宜，相當於入場即承擔的價格錯配。real_edge 把它從邊際中扣掉，是最保守的可執行邊際，Scanner 預設按它排序與篩選。

## Forward 與 Reverse 的含義

<!-- id: pf-direction -->

多空方向永遠是「空高費率、多低費率」。direction 標籤只描述費率所處的區域：

| direction | 條件 | 典型形態 |
| --- | --- | --- |
| forward | 至少一腿費率 ≥ 0 | 空頭腿收正費率（或混合正負） |
| reverse | 兩腿費率都 < 0 | 多頭腿收負費率，空頭腿付得更少 |

## 與 Cash & Carry 對比

<!-- id: pf-vs-cc -->

|  | Cash & Carry | Pure Futures |
| --- | --- | --- |
| 兩條腿 | 現貨 + 永續 | 永續 + 永續 |
| 手續費 | 現貨 taker 較高（~0.1%） | 雙永續 taker 較低 |
| 跨所 | Unified 才拆腿 | 天然跨所 |
| 借幣 | 反向需要 | 不需要 |
| DEX 參與 | 不支援 | HL / Aster / Lighter / EdgeX / dYdX（掃描） |
| 收益來源 | 單所費率絕對值 | 兩所費率之差 |

## 閾值與過濾

<!-- id: pf-thresholds -->

| 引數 | 含義 |
| --- | --- |
| min_spread | 原始費率差下限（預設 0.03%） |
| min_edge | 扣費後淨邊際下限（預設 0.01%） |
| min_edge_1h | 雙 1h 同週期對的專用（更低）閾值 |
| min_edge_mismatch | 跨週期對的專用（更高）閾值 |
| max_mark_spread_pct | 兩所標記價差上限，超過即丟棄 |

min_edge_1h 更低是因為 1h 週期資金週轉快、同週期無 timing risk；min_edge_mismatch 更高是為跨週期的結算不同步留風險溢價。上述閾值在 Settings → Strategy 配置，寫入 scripts/data/strategy_config.json；Scanner API 與 CLI runner 共用。

## Settings 與 CLI 配置統一

<!-- id: pf-settings -->

| Dashboard 欄位 | CLI / 模板欄位 |
| --- | --- |
| min_spread_annual | pureFuturesArbitrage.minSpreadPct |
| min_edge_annual | pureFuturesArbitrage.minNetEdgePct |
| min_edge_1h / min_edge_mismatch | 按腿週期在 runner 內逐行應用（見跨週期文件） |
| trade_usd | pureFuturesArbitrage.tradeUsdPerPair |
| max_positions | pureFuturesArbitrage.maxConcurrentPairs |
| scan_venues | pureFuturesArbitrage.venues |
| scan_interval_sec | scanIntervalMinutes（秒÷60） |
| fee_mode / venue_fee_tiers | 掃描時 fee_providers 解析 |

templates/config.pure_futures.spread.json 仍保留執行細節（parallelLegs、depthCheck、dry_run 等）；閾值以 strategy_config.json 為準。合併邏輯：scripts/core/strategy_config.py → apply_strategy_to_pure_futures_cfg()。

## 跨週期配對

<!-- id: pf-cross-interval -->

當兩腿結算週期不同（settle_mismatch，如 HL 1h vs Binance 8h），不能直接比較 rate_pct。系統先歸一化到每小時，再用 mark-index 基差按結算進度加權混合（basis blend）。

- spread_source = rate：同週期，直接用公佈費率
- spread_source = basis_blend：跨週期且有 index，使用混合模型
- spread_source = rate_linear：跨週期但無獨立 index（Lighter / EdgeX / dYdX 腿），線性回退

> ℹ️ dYdX indexer 目前僅暴露 oraclePrice，mark≈index，basis blend 對該腿幾乎不生效；與 CEX 8h 配對時按 rate_linear + min_edge_mismatch 更保守。

完整推導、各所 index 來源與數值示例見「跨週期資金費率套利」。

## 執行與監控

<!-- id: pf-execution -->

- 儀表盤：Scanner → Pure Futures 表格「開倉」，預設 dry-run；scan-only venue 按鈕禁用
- 手動交易：pure_futures_trade.py open / list / close（預設 dry-run）
- 自動執行：run_pure_futures_spread.py --once / --watch（合併 strategy_config.json）
- 持倉監控：pure_futures_watcher.py；parallelLegs 預設 true，雙腿併發下單
- 開倉前深度檢查：futures_depth.py，DEX 訂單簿拉取失敗則阻止開倉（depthCheckFailOpen=false）

> ⚠️ 跨週期對在執行/回測中會經 settle_mismatch_planner 疊加現金流懲罰（在 scanner 的 net_edge 之上）；planner 與 unified pool 已與掃描層共用 pair_pure_futures_spread 做 basis blend。

## 程式碼地圖

<!-- id: pf-code -->

| 路徑 | 職責 |
| --- | --- |
| scripts/cli/scan_pure_futures_spreads.py | 掃描入口（含 basis blend 呼叫） |
| scripts/execution/run_pure_futures_spread.py | 自動執行 runner（scan → filter → open/close） |
| scripts/execution/pure_futures_executor.py | 雙腿下單與回滾 |
| scripts/execution/pure_futures_watcher.py | 持倉監控 |
| scripts/execution/settle_mismatch_planner.py | 跨週期現金流分析（執行側） |
| scripts/backtest/backtest_pure_futures_spread.py | 回測 |
| server/routes/scanner.py | API 快取、閾值過濾、費率重算 |
