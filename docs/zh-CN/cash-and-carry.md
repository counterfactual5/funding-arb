# 正向与反向 Cash & Carry

现货+永续对冲、借币反向与阈值

## 概述

<!-- id: cc-overview -->

Cash & Carry 在同一交易所内用现货与永续构成对冲，收取资金费。Scanner 的 Cash & Carry Tab 按交易所独立扫描，每个所分别给出正向 / 反向候选。仅支持 CEX（DEX 无现货与借贷）。

## 正向（费率 > 0）

<!-- id: cc-forward -->

```text
腿 1：买入现货（等额）
腿 2：永续做空（等额）
```

费率为正时多头付空头：永续空头每个周期收取资金费，现货多头对冲价格。无借币成本。

```text
net_edge_pct = rate_pct − (spot_taker + futures_taker)
```

> ℹ️ 正向的前提是该所有现货交易对（has_spot）。部分小币只有永续没有现货，会被列入 forward_no_spot。

## 反向（费率 < 0）

<!-- id: cc-reverse -->

```text
腿 1：margin 借币并卖出（等额）
腿 2：永续做多（等额）
```

费率为负时空头付多头：永续多头收取资金费，借币卖出的现货空头对冲价格。但借币要付利息，须从边际中扣除。

```text
borrow_per_period = borrow_annual_pct / (365 × 24) × interval_h
net_edge_pct = |rate_pct| − borrow_per_period − (spot_taker + futures_taker)
```

> ⚠️ 反向比正向多一项持续成本：借币利息按周期累积，且利率会随市场浮动。负费率消失后若不及时平仓，利息会迅速吃掉利润。

## 反向可行性约束

<!-- id: cc-constraints -->

- 币必须可借（borrowable）且有足够借贷额度（max_borrow）
- 交易所必须实现 margin 借/还接口（supports_reverse_arbitrage）；live 模式下不支持的所会强制禁用反向
- 借币利率过高时 net_edge ≤ 0，自动从候选中排除

Scanner 会把负费率但不可借的币单独列为 reverse_not_borrowable，仅供参考，不可执行。

## 入场 / 退出阈值（配置）

<!-- id: cc-thresholds -->

| 参数 | 含义 |
| --- | --- |
| entryFundingRatePct | 正向入场费率（如 0.05%） |
| exitFundingRatePct | 正向退出费率（如 0.01%，低于即平仓） |
| reverseEntryFundingRatePct | 反向入场费率（负值，如 −0.05%） |
| reverseExitFundingRatePct | 反向退出费率（如 −0.01%） |
| minNetEdgePct | 通用费率闸门：扣费后净边际下限 |
| minReverseSpreadPct | 反向额外门槛：|rate| − borrow 需超过此值 |
| maxMinutesToSettlement | 时间锁：距下次结算超过 N 分钟则暂不入场 |

多资产模式（crossAssetArbitrage）有槽位竞争：净边际更高的新机会可抢占旧仓位，但必须超出 preemptionFrictionBufferPct 的切换摩擦缓冲，避免来回倒仓被手续费磨损。

## 仪表盘开仓

<!-- id: cc-dashboard -->

Scanner → Cash & Carry 表格支持 Dry-run 开仓：同所 spot + perp（forward 或 reverse，由该行费率符号决定）。API：POST /api/positions/open，strategy=carry，futures_venue 与 spot_venue 相同。

> ⚠️ 反向需交易所支持 margin 借卖（supports_reverse_arbitrage）。live 前确认借币额度与利率；net_edge 已扣借币成本但仍会浮动。

## Scanner 字段

<!-- id: cc-fields -->

| 字段 | 含义 |
| --- | --- |
| rate_pct | 当前周期资金费率（正=正向候选，负=反向候选） |
| interval_h / annual_pct | 结算周期 / 年化 |
| has_spot / spot_price | 是否有现货交易对及价格（正向） |
| borrowable / max_borrow | 是否可借及额度（反向） |
| borrow_daily_pct / borrow_annual_pct | 借币日息 / 年息 |
| borrow_per_period_pct | 折算到一个结算周期的借币成本 |
| fee_pct | 现货 + 永续两腿 taker 之和 |
| net_edge_pct | 扣费（与借币成本）后净边际 |

## 代码地图

<!-- id: cc-code -->

| 路径 | 职责 |
| --- | --- |
| scripts/cli/scan_funding_arbitrage.py | 按所扫描入口（正向 / 反向候选） |
| scripts/strategies/futures/cash_and_carry.py | 单资产决策（委托给 cross_asset 引擎） |
| scripts/strategies/futures/cross_asset_arbitrage.py | 多资产槽位竞争与抢占逻辑 |
| scripts/execution/run_cash_and_carry.py | 执行循环（NAV 同步、强平检查、通知） |
| scripts/backtest/borrow_providers.py | 各所借币利率与可借额度 |
