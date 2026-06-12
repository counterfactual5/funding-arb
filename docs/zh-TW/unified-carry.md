# Unified 跨所套利

兩腿拆所路由與轉帳成本

## 概述

<!-- id: u-overview -->

Unified C&C 與同所 Cash & Carry 原理相同，區別在於兩條腿可以拆在不同交易所：期貨腿選費率最優的所，現貨 / 借幣腿選成本最低的所。所有 CEX 被抽象成統一路由表，按幣取全場最優組合。

> ℹ️ 同所費率不極端但「A 所費率高 + B 所現貨費用低」的組合常常存在 —— Unified 的機會往往多於單所 C&C。僅支援 CEX。

## 正向路由

<!-- id: u-forward -->

- 期貨腿：在所有所中選「費率最高」且 ≥ entry 閾值的所開空
- 現貨腿：在有現貨的所中選「現貨手續費最低」的所買入

```text
net_edge_pct = funding_rate_pct − futures_fee − spot_fee
```

兩腿可以同所（same_venue = true），此時退化為普通 C&C，無轉賬成本。

## 反向路由

<!-- id: u-reverse -->

- 期貨腿：選「費率最負」的所開多
- 借幣腿：在可借（borrowable）且支援反向執行的所中，選「單週期借幣成本最低」的所借幣賣出

```text
borrow_per_period = 按期貨腿 interval_h 折算的借幣成本
net_edge_pct = |funding_rate_pct| − borrow_per_period − futures_fee − spot_fee
```

## 跨所轉賬成本

<!-- id: u-transfer -->

兩腿不同所時，資金需要跨所排程。系統按轉賬鏈路計提鏈上轉賬費，得到全成本邊際：

```text
net_edge_all_in_pct = net_edge_pct − transfer_fee_pct
```

- 跨所路由按 net_edge_all_in_pct 排序（含轉賬費）
- 同所路由按 net_edge_pct 排序（無轉賬費）
- transfer_chain 欄位記錄建議的轉賬鏈路（如 TRC20 / BEP20）

> ⚠️ 轉賬費是一次性成本，持倉時間越長攤薄越多。短持倉 + 跨所小額時，轉賬費可能吃掉全部邊際，留意 all-in 與 net 的差值。

## Scanner 欄位

<!-- id: u-fields -->

| 欄位 | 含義 |
| --- | --- |
| direction | forward / reverse |
| futures_venue / spot_venue | 期貨腿 / 現貨（借幣）腿所在所 |
| funding_rate_pct / interval_h | 期貨腿費率與週期 |
| borrow_per_period_pct | 單週期借幣成本（反向） |
| futures_fee_pct / spot_fee_pct | 兩腿 taker 費率 |
| net_edge_pct | 扣費後淨邊際（不含轉賬） |
| net_edge_all_in_pct | 再扣轉賬費的全成本邊際 |
| transfer_chain / transfer_fee_pct | 轉賬鏈路與費用 |
| same_venue | 兩腿是否同所 |

## 程式碼地圖

<!-- id: u-code -->

| 路徑 | 職責 |
| --- | --- |
| scripts/backtest/unified_funding_pool.py | 核心路由：best_forward / best_reverse / scan_routes |
| scripts/cli/scan_unified_funding.py | CLI 掃描入口 |
| scripts/backtest/borrow_providers.py | 借幣利率與反向可執行性 |
| server/routes/scanner.py | Unified 快取與費率重算（_recalc_unified_fees） |
