# 正向與反向 Cash & Carry

現貨+永續對沖、借幣反向與門檻

## 概述

<!-- id: cc-overview -->

Cash & Carry 在同一交易所內用現貨與永續構成對沖，收取資金費。Scanner 的 Cash & Carry Tab 按交易所獨立掃描，每個所分別給出正向 / 反向候選。僅支援 CEX（DEX 無現貨與借貸）。

## 正向（費率 > 0）

<!-- id: cc-forward -->

```text
腿 1：買入現貨（等額）
腿 2：永續做空（等額）
```

費率為正時多頭付空頭：永續空頭每個週期收取資金費，現貨多頭對沖價格。無借幣成本。

```text
net_edge_pct = rate_pct − (spot_taker + futures_taker)
```

> ℹ️ 正向的前提是該所有現貨交易對（has_spot）。部分小幣只有永續沒有現貨，會被列入 forward_no_spot。

## 反向（費率 < 0）

<!-- id: cc-reverse -->

```text
腿 1：margin 借幣並賣出（等額）
腿 2：永續做多（等額）
```

費率為負時空頭付多頭：永續多頭收取資金費，借幣賣出的現貨空頭對沖價格。但借幣要付利息，須從邊際中扣除。

```text
borrow_per_period = borrow_annual_pct / (365 × 24) × interval_h
net_edge_pct = |rate_pct| − borrow_per_period − (spot_taker + futures_taker)
```

> ⚠️ 反向比正向多一項持續成本：借幣利息按週期累積，且利率會隨市場浮動。負費率消失後若不及時平倉，利息會迅速吃掉利潤。

## 反向可行性約束

<!-- id: cc-constraints -->

- 幣必須可借（borrowable）且有足夠借貸額度（max_borrow）
- 交易所必須實現 margin 借/還介面（supports_reverse_arbitrage）；live 模式下不支援的所會強制禁用反向
- 借幣利率過高時 net_edge ≤ 0，自動從候選中排除

Scanner 會把負費率但不可借的幣單獨列為 reverse_not_borrowable，僅供參考，不可執行。

## 入場 / 退出閾值（配置）

<!-- id: cc-thresholds -->

| 引數 | 含義 |
| --- | --- |
| entryFundingRatePct | 正向入場費率（如 0.05%） |
| exitFundingRatePct | 正向退出費率（如 0.01%，低於即平倉） |
| reverseEntryFundingRatePct | 反向入場費率（負值，如 −0.05%） |
| reverseExitFundingRatePct | 反向退出費率（如 −0.01%） |
| minNetEdgePct | 通用費率閘門：扣費後淨邊際下限 |
| minReverseSpreadPct | 反向額外門檻：|rate| − borrow 需超過此值 |
| maxMinutesToSettlement | 時間鎖：距下次結算超過 N 分鐘則暫不入場 |

多資產模式（crossAssetArbitrage）有槽位競爭：淨邊際更高的新機會可搶佔舊倉位，但必須超出 preemptionFrictionBufferPct 的切換摩擦緩衝，避免來回倒倉被手續費磨損。

## Scanner 欄位

<!-- id: cc-fields -->

| 欄位 | 含義 |
| --- | --- |
| rate_pct | 當前週期資金費率（正=正向候選，負=反向候選） |
| interval_h / annual_pct | 結算週期 / 年化 |
| has_spot / spot_price | 是否有現貨交易對及價格（正向） |
| borrowable / max_borrow | 是否可借及額度（反向） |
| borrow_daily_pct / borrow_annual_pct | 借幣日息 / 年息 |
| borrow_per_period_pct | 折算到一個結算週期的借幣成本 |
| fee_pct | 現貨 + 永續兩腿 taker 之和 |
| net_edge_pct | 扣費（與借幣成本）後淨邊際 |

## 程式碼地圖

<!-- id: cc-code -->

| 路徑 | 職責 |
| --- | --- |
| scripts/cli/scan_funding_arbitrage.py | 按所掃描入口（正向 / 反向候選） |
| scripts/strategies/futures/cash_and_carry.py | 單資產決策（委託給 cross_asset 引擎） |
| scripts/strategies/futures/cross_asset_arbitrage.py | 多資產槽位競爭與搶佔邏輯 |
| scripts/execution/run_cash_and_carry.py | 執行迴圈（NAV 同步、強平檢查、通知） |
| scripts/backtest/borrow_providers.py | 各所借幣利率與可借額度 |
