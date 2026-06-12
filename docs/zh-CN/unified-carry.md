# Unified 跨所套利

两腿拆所路由与转账成本

## 概述

<!-- id: u-overview -->

Unified C&C 与同所 Cash & Carry 原理相同，区别在于两条腿可以拆在不同交易所：期货腿选费率最优的所，现货 / 借币腿选成本最低的所。所有 CEX 被抽象成统一路由表，按币取全场最优组合。

> ℹ️ 同所费率不极端但「A 所费率高 + B 所现货费用低」的组合常常存在 —— Unified 的机会往往多于单所 C&C。仅支持 CEX。

## 正向路由

<!-- id: u-forward -->

- 期货腿：在所有所中选「费率最高」且 ≥ entry 阈值的所开空
- 现货腿：在有现货的所中选「现货手续费最低」的所买入

```text
net_edge_pct = funding_rate_pct − futures_fee − spot_fee
```

两腿可以同所（same_venue = true），此时退化为普通 C&C，无转账成本。

## 反向路由

<!-- id: u-reverse -->

- 期货腿：选「费率最负」的所开多
- 借币腿：在可借（borrowable）且支持反向执行的所中，选「单周期借币成本最低」的所借币卖出

```text
borrow_per_period = 按期货腿 interval_h 折算的借币成本
net_edge_pct = |funding_rate_pct| − borrow_per_period − futures_fee − spot_fee
```

## 跨所转账成本

<!-- id: u-transfer -->

两腿不同所时，资金需要跨所调度。系统按转账链路计提链上转账费，得到全成本边际：

```text
net_edge_all_in_pct = net_edge_pct − transfer_fee_pct
```

- 跨所路由按 net_edge_all_in_pct 排序（含转账费）
- 同所路由按 net_edge_pct 排序（无转账费）
- transfer_chain 字段记录建议的转账链路（如 TRC20 / BEP20）

> ⚠️ 转账费是一次性成本，持仓时间越长摊薄越多。短持仓 + 跨所小额时，转账费可能吃掉全部边际，留意 all-in 与 net 的差值。

## Scanner 字段

<!-- id: u-fields -->

| 字段 | 含义 |
| --- | --- |
| direction | forward / reverse |
| futures_venue / spot_venue | 期货腿 / 现货（借币）腿所在所 |
| funding_rate_pct / interval_h | 期货腿费率与周期 |
| borrow_per_period_pct | 单周期借币成本（反向） |
| futures_fee_pct / spot_fee_pct | 两腿 taker 费率 |
| net_edge_pct | 扣费后净边际（不含转账） |
| net_edge_all_in_pct | 再扣转账费的全成本边际 |
| transfer_chain / transfer_fee_pct | 转账链路与费用 |
| same_venue | 两腿是否同所 |

## 代码地图

<!-- id: u-code -->

| 路径 | 职责 |
| --- | --- |
| scripts/backtest/unified_funding_pool.py | 核心路由：best_forward / best_reverse / scan_routes |
| scripts/cli/scan_unified_funding.py | CLI 扫描入口 |
| scripts/backtest/borrow_providers.py | 借币利率与反向可执行性 |
| server/routes/scanner.py | Unified 缓存与费率重算（_recalc_unified_fees） |
