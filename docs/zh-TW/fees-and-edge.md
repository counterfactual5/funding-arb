# 費率與邊際計算

fee_mode、VIP 等級與各類邊際欄位

## 概述

<!-- id: fe-overview -->

資金費套利的毛利往往只有千分之幾，手續費直接決定一筆機會是否真實可做。Scanner 展示的所有邊際都已扣除開倉 taker 費；本篇解釋費率從哪來、各類邊際欄位的區別。

## 費率模式（fee_mode）

<!-- id: fe-modes -->

| 模式 | 行為 |
| --- | --- |
| auto | 已配置 API key 的所從賬戶 API 讀真實費率；未配置的所按 VIP 檔位表估算 |
| tier | 全部用靜態 VIP 檔位表（scripts/core/vip_fee_tiers.py） |
| manual | 用策略配置中的手動覆蓋值 |

在 Settings → 交易手續費 中配置 fee_mode 與各所 VIP 檔位（venue_fee_tiers）。已用 API 讀取的所會標記「已用 API」，檔位選擇對其無效。

## 現貨費與永續費

<!-- id: fe-spot-futures -->

現貨 taker（典型 0.1%）通常遠高於永續 taker（約 0.02% ~ 0.06%）。這是 Pure Futures 相對 C&C 的結構性優勢之一。

| 策略 | 開倉費組成 |
| --- | --- |
| Cash & Carry / Unified | spot_taker + futures_taker |
| Pure Futures | long_futures_taker + short_futures_taker |

> ⚠️ Scanner 的 net_edge 只扣開倉費。完整一輪（開 + 平）是兩倍：round_trip_fee_pct = fee_pct × 2。判斷持倉多久回本時要按 round-trip 算。

## VIP 檔位的影響

<!-- id: fe-vip -->

VIP 等級越高 taker 越低，直接放大 net_edge / real_edge。同一筆費率差，VIP0 可能為負邊際，VIP 高檔則為正——費率配置錯誤會讓 Scanner 整頁機會失真。

- 檔位表來源：各所官網公開費率表，維護於 vip_fee_tiers.py
- 設定入口：Settings → 交易手續費 → 各所 VIP 檔位
- 有 API key 時優先用賬戶真實費率（包含返傭後的實際值）

## Perp DEX 預設 taker（無 API 時）

<!-- id: fe-dex-defaults -->

DEX 無賬戶 API 時使用 fee_providers 中的公開預設值或合約後設資料（EdgeX defaultTakerFeeRate）。下表為 VIP0 / 預設檔參考，實際以 Settings 或鏈上費率為準。

| Venue | 預設 futures taker | 備註 |
| --- | --- | --- |
| Hyperliquid | 0.045% | userFees 可更低 |
| Aster | 0.04% | Binance-fapi 相容 |
| Lighter | 0% | 當前零費促銷，以鏈上為準 |
| EdgeX | 0.038% | getMetaData defaultTakerFeeRate |
| dYdX v4 | 0.05% | 掃描估算；交易未接入 |

## 各類邊際欄位

<!-- id: fe-edges -->

| 欄位 | 定義 | 適用 |
| --- | --- | --- |
| spread_pct | 毛費率差（或單所費率） | 全部 |
| fee_pct | 雙腿開倉 taker 之和 | 全部 |
| net_edge_pct | spread − fee（反向再扣借幣） | 全部 |
| mark_spread_pct | 兩所標記價相對偏差 | Pure Futures |
| real_edge_pct | net_edge − mark_spread | Pure Futures（預設排序） |
| net_edge_all_in_pct | net_edge − 跨所轉賬費 | Unified 跨所路由 |
| annual_apy_pct | 按結算週期年化的淨邊際 | 全部 |

保守程度：net_edge < real_edge（Pure Futures）/ net_edge_all_in（Unified）。看到大 net_edge 先看 real / all-in 是否同樣成立。

## 改費率後重算

<!-- id: fe-recalc -->

修改 fee_mode 或 VIP 檔位後，不需要重新掃描：POST /api/scanner/recalc-fees 會直接用新費率重算快取中所有機會的 net_edge / real_edge，並透過 WebSocket 推送前端。

- Settings 頁「儲存並重算淨收益」按鈕即呼叫該介面
- 重算覆蓋 pure / carry / unified 三類快取
- 費率解析入口：scripts/core/fee_providers.py 的 resolve_venue_fee / parse_fee_policy
