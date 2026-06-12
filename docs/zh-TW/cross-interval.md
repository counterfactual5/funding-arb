# 跨週期資金費率套利

basis blend、real_edge 與程式實作

## 問題背景

<!-- id: ci-background -->

各交易所公佈的 rate_pct 是當前結算週期內的費率，週期長度不同：

| 交易所 | 典型週期 | 含義 |
| --- | --- | --- |
| Binance / OKX / Bybit | 8h | 每 8 小時結算一次 |
| Bitget | 2h 或 8h | 部分合約 2h |
| Hyperliquid / Lighter / dYdX v4 | 1h | 每小時結算 |
| EdgeX | 4h | 多數主流合約 240min |
| Aster | 按合約 | 讀 fundingInfo，常見 8h |

若簡單做 spread_naive = short_rate_pct - long_rate_pct，會把 1h 的 0.01% 與 8h 的 0.05% 放在同一量級比較，嚴重失真。

## 為什麼不能只做線性外推

<!-- id: ci-linear-problem -->

```text
# 樸素歸一化
rate_hourly = rate_pct / interval_h
spread = (short_hourly - long_hourly) × min(interval_long, interval_short)
```

在週期剛結算完時合理（基差已收斂，rate_pct 反映新週期起點）。但在週期中途，premium（mark 相對 index 的偏離）會持續累積，下一期實際 funding 往往更接近基差隱含費率。

## 模型目標

<!-- id: ci-model-goal -->

- 將兩邊費率統一到每小時基準
- 用 mark-index 基差估計「本週期剩餘時間內的預期 funding」
- 按結算進度在「已公佈 rate」與「基差隱含 rate」之間加權混合
- 輸出可解釋欄位（spread_source、settle_progress、basis_pct）

## 何時啟用跨週期模型

<!-- id: ci-when -->

```text
is_mismatch = |long_interval_h − short_interval_h| > 0.5
```

- is_mismatch == false → 同週期，直接用 rate_pct / interval_h，spread_source = rate
- is_mismatch == true → 啟用 basis blend（有 index）或線性回退（無 index）

## 資料依賴

<!-- id: ci-data-deps -->

| 欄位 | 說明 |
| --- | --- |
| rate_pct | 當前待結算資金費率（%） |
| interval_h | 結算週期（小時） |
| mark_price | 標記價格 |
| index_price | 指數 / 預言機價格 |
| next_funding_ts | 下次結算時間（ms） |
| last_settle_ts | 上次結算時間（ms），可由 next - interval 推導 |

| 交易所 | index_price 來源 | 跨週期 basis blend |
| --- | --- | --- |
| Binance | premiumIndex.indexPrice | ✅ |
| Bitget | indexPrice | ✅ |
| Bybit | indexPrice | ✅ |
| OKX | idxPx（mark-price 介面） | ✅ |
| Hyperliquid | oraclePx | ✅ |
| Aster | 繼承 Binance provider | ✅ |
| Lighter | 無公開 index → 0 | ❌ 回退 rate_linear |
| EdgeX | 無公開 index → 0 | ❌ 回退 rate_linear |
| dYdX v4 | indexer 僅 oraclePrice（mark≈index） | ❌ 回退 rate_linear |

> ℹ️ dYdX 鏈上費率 = 60 分鐘 premium TWAP + 利率項，每小時整點支付；nextFundingRate 是預測值，與 CEX 8h 配對時務必用 min_edge_mismatch。

## 結算進度 progress

<!-- id: ci-progress -->

```text
progress = elapsed / period_length   ∈ [0, 1]

# 計算優先順序：
1. 有 last_settle_ts 與 next_funding_ts: (now − last) / (next − last)
2. 僅有 next_funding_ts: 用剩餘時間反推
3. 皆無: 回退 0.5
```

- progress ≈ 0：剛結算完，更信任已公佈的 rate_pct
- progress ≈ 1：即將結算，更信任 mark-index 基差隱含的下期費率

## 基差 basis_pct

<!-- id: ci-basis -->

```text
basis_pct = (mark_price − index_price) / index_price × 100%
```

按交易所對單週期溢價封頂（VENUE_BASIS_CAP_PCT），避免極端 mark-index 差製造虛假大邊際：

| 型別 | 單週期 cap | 說明 |
| --- | --- | --- |
| Binance / Bybit / Bitget / OKX / Aster / EdgeX | ±0.30% | 約為典型 funding clamp 的 3 倍，過濾極端噪聲 |
| Hyperliquid / Lighter / dYdX | ±0.50% | 無硬頂或 oracle-only，放寬 cap |
| 未知 venue | ±0.50% | DEFAULT_BASIS_CAP_PCT |

## 混合 hourly 與 spread

<!-- id: ci-blend -->

```text
rate_hourly  = rate_pct / interval_h
basis_hourly = basis_pct / interval_h
blended_hourly = (1 − progress) × rate_hourly + progress × basis_hourly
```

```text
eff_interval = min(long_interval_h, short_interval_h)
spread_pct   = (short_blended − long_blended) × eff_interval
net_edge_pct = spread_pct − fee_pct（雙邊開倉 taker）
real_edge_pct = net_edge_pct − mark_spread_pct
```

## 流程圖

<!-- id: ci-flow -->

拉取各所 rate / mark / index / 結算時間 → 判斷 interval 差 > 0.5h → 計算進度與基差 → 有 index 則 basis_blend，否則 rate_linear → 合成 spread → net_edge = spread − fees → mark_spread 過濾 + min_edge 閾值。

## 掃描輸出欄位

<!-- id: ci-fields -->

| 欄位 | 說明 |
| --- | --- |
| settle_mismatch | 是否跨週期 |
| same_interval | not settle_mismatch |
| long_interval_h / short_interval_h | 各腿結算週期 |
| spread_source | rate / basis_blend / rate_linear |
| long_basis_pct / short_basis_pct | 各腿 mark-index 溢價（%） |
| long_settle_progress / short_settle_progress | 各腿混合權重（= progress） |
| spread_pct | 混合後的週期 spread（%） |
| net_edge_pct | 扣費後淨邊際（%） |
| mark_spread_pct | 兩所標記價差（%） |

## 風控與配置疊加

<!-- id: ci-risk -->

- min_edge_mismatch：跨週期對可要求更高 net_edge_pct（Settings 可配）
- min_edge_1h：雙 1h 同週期可用更低閾值
- max_mark_spread_pct：兩所 mark 價差超閾值則丟棄
- settle_mismatch_planner：執行側將兩腿線性歸一化到 8h 視窗，分析現金流不對稱
- VIP 費率策略影響 net_edge / real_edge 中的 fee_pct

## 程式碼地圖

<!-- id: ci-code-map -->

| 路徑 | 職責 |
| --- | --- |
| scripts/core/cross_interval_funding.py | 混合模型純函式（可單測） |
| scripts/cli/scan_pure_futures_spreads.py | 掃描入口，呼叫混合模型 |
| scripts/tests/test_cross_interval_funding.py | 模型單測 |
| scripts/execution/settle_mismatch_planner.py | 執行側現金流 / 8h 歸一化分析 |
| server/routes/scanner.py | API 快取、min_edge_mismatch 過濾 |
| web/src/views/Scanner.vue | 展示 settle_mismatch、Cross 篩選、real edge |

## 數值示例

<!-- id: ci-example -->

場景：BTC，Hyperliquid vs Binance，跨週期。

| 腿 | rate_pct | interval_h | basis_pct | progress |
| --- | --- | --- | --- | --- |
| Short @ HL | 0.04 | 1 | +0.30% | 0.85 |
| Long @ Binance | 0.08 | 8 | +0.05% | 0.25 |

```text
# HL 腿
rate_hourly  = 0.04 / 1 = 0.04
basis_hourly = 0.30 / 1 = 0.30
blended      = 0.15×0.04 + 0.85×0.30 ≈ 0.261 %/h

# Binance 腿
rate_hourly  = 0.08 / 8 = 0.01
basis_hourly = 0.05 / 8 = 0.00625
blended      = 0.75×0.01 + 0.25×0.00625 ≈ 0.0094 %/h

# Spread (eff_interval = 1h)
spread_pct ≈ (0.261 − 0.0094) × 1 ≈ 0.252%
net_edge ≈ 0.252 − 0.11 = 0.14%
```

> ℹ️ 若用樸素線性外推，HL 僅 0.04%/h，spread 會低估 HL 作為 short 腿的優勢。

## EdgeX 4h 線性回退示例

<!-- id: ci-example-edgex -->

場景：BTC，EdgeX（4h，無 index）vs Binance（8h）。EdgeX 腿無法 basis blend，spread_source 對短腿為 rate_linear。

| 腿 | rate_pct | interval_h | blend |
| --- | --- | --- | --- |
| Short @ EdgeX | 0.02 | 4 | rate_linear → 0.02/4 = 0.005 %/h |
| Long @ Binance | 0.08 | 8 | basis_blend（有 index） |

```text
eff_interval = min(4, 8) = 4h
spread ≈ (short_hourly − long_blended) × 4
需同時滿足 min_edge_mismatch 與 settle_mismatch_planner 現金流檢查
```

## 已知限制

<!-- id: ci-limits -->

| 項 | 說明 |
| --- | --- |
| 現金流懲罰 | planner 在 scanner net_edge 上疊加 timing 懲罰，不重複計算 spread |
| 全域性 basis 封頂 | 固定 ±1%/週期，未按交易所真實 premium clamp 細分 |
| 無 index 的 DEX | Lighter、EdgeX 跨週期只能 rate_linear |
| 歷史 JSONL | 舊快照若無 index_price / progress 欄位，回放無法復現混合模型 |
