# Pure Futures 永续套利

双永续费率差、net / real edge

## 概述

<!-- id: pf-overview -->

Pure Futures 是本系统的主策略：在两个交易所分别持有同一币种的永续多头与空头，赚取两所资金费率之差。不需要现货、不需要借币，天然跨所，Perp DEX（Hyperliquid / Aster / Lighter / EdgeX；dYdX 可扫描）也能参与。

> ℹ️ 相比 Cash & Carry：双腿都是永续，taker 费率更低；无现货滑点；不受现货上架与借贷额度限制。

## 核心机制

<!-- id: pf-mechanics -->

对每个币种，比较各所费率：在费率高的所做空（收更多 / 付更少），在费率低的所做多（付更少 / 收更多）。

```text
spread_pct  = short_rate − long_rate（做空高费率腿、做多低费率腿）
net_edge_pct = spread_pct − (long_taker + short_taker)
real_edge_pct = net_edge_pct − mark_spread_pct
```

mark_spread_pct 是两所标记价的相对偏差：开仓时一腿贵一腿便宜，相当于入场即承担的价格错配。real_edge 把它从边际中扣掉，是最保守的可执行边际，Scanner 默认按它排序与筛选。

## Forward 与 Reverse 的含义

<!-- id: pf-direction -->

多空方向永远是「空高费率、多低费率」。direction 标签只描述费率所处的区域：

| direction | 条件 | 典型形态 |
| --- | --- | --- |
| forward | 至少一腿费率 ≥ 0 | 空头腿收正费率（或混合正负） |
| reverse | 两腿费率都 < 0 | 多头腿收负费率，空头腿付得更少 |

## 与 Cash & Carry 对比

<!-- id: pf-vs-cc -->

|  | Cash & Carry | Pure Futures |
| --- | --- | --- |
| 两条腿 | 现货 + 永续 | 永续 + 永续 |
| 手续费 | 现货 taker 较高（~0.1%） | 双永续 taker 较低 |
| 跨所 | Unified 才拆腿 | 天然跨所 |
| 借币 | 反向需要 | 不需要 |
| DEX 参与 | 不支持 | HL / Aster / Lighter / EdgeX / dYdX（扫描） |
| 收益来源 | 单所费率绝对值 | 两所费率之差 |

## 阈值与过滤

<!-- id: pf-thresholds -->

| 参数 | 含义 |
| --- | --- |
| min_spread | 原始费率差下限（默认 0.03%） |
| min_edge | 扣费后净边际下限（默认 0.01%） |
| min_edge_1h | 双 1h 同周期对的专用（更低）阈值 |
| min_edge_mismatch | 跨周期对的专用（更高）阈值 |
| max_mark_spread_pct | 两所标记价差上限，超过即丢弃 |

min_edge_1h 更低是因为 1h 周期资金周转快、同周期无 timing risk；min_edge_mismatch 更高是为跨周期的结算不同步留风险溢价。上述阈值在 Settings → Strategy 配置，写入 scripts/data/strategy_config.json；Scanner API 与 CLI runner 共用。

## Settings 与 CLI 配置统一

<!-- id: pf-settings -->

| Dashboard 字段 | CLI / 模板字段 |
| --- | --- |
| min_spread_annual | pureFuturesArbitrage.minSpreadPct |
| min_edge_annual | pureFuturesArbitrage.minNetEdgePct |
| min_edge_1h / min_edge_mismatch | 按腿周期在 runner 内逐行应用（见跨周期文档） |
| trade_usd | pureFuturesArbitrage.tradeUsdPerPair |
| max_positions | pureFuturesArbitrage.maxConcurrentPairs |
| scan_venues | pureFuturesArbitrage.venues |
| scan_interval_sec | scanIntervalMinutes（秒÷60） |
| fee_mode / venue_fee_tiers | 扫描时 fee_providers 解析 |

templates/config.pure_futures.spread.json 仍保留执行细节（parallelLegs、depthCheck、dry_run 等）；阈值以 strategy_config.json 为准。合并逻辑：scripts/core/strategy_config.py → apply_strategy_to_pure_futures_cfg()。

## 跨周期配对

<!-- id: pf-cross-interval -->

当两腿结算周期不同（settle_mismatch，如 HL 1h vs Binance 8h），不能直接比较 rate_pct。系统先归一化到每小时，再用 mark-index 基差按结算进度加权混合（basis blend）。

- spread_source = rate：同周期，直接用公布费率
- spread_source = basis_blend：跨周期且有 index，使用混合模型
- spread_source = rate_linear：跨周期但无独立 index（Lighter / EdgeX / dYdX 腿），线性回退

> ℹ️ dYdX indexer 目前仅暴露 oraclePrice，mark≈index，basis blend 对该腿几乎不生效；与 CEX 8h 配对时按 rate_linear + min_edge_mismatch 更保守。

完整推导、各所 index 来源与数值示例见「跨周期资金费率套利」。

## 执行与监控

<!-- id: pf-execution -->

- 仪表盘：Scanner → Pure Futures 表格「开仓」，默认 dry-run；scan-only venue 按钮禁用
- 手动交易：pure_futures_trade.py open / list / close（默认 dry-run）
- 自动执行：run_pure_futures_spread.py --once / --watch（合并 strategy_config.json）
- 持仓监控：pure_futures_watcher.py；parallelLegs 默认 true，双腿并发下单
- 开仓前深度检查：futures_depth.py，DEX 订单簿拉取失败则阻止开仓（depthCheckFailOpen=false）

> ⚠️ 跨周期对在执行/回测中会经 settle_mismatch_planner 叠加现金流惩罚（在 scanner 的 net_edge 之上）；planner 与 unified pool 已与扫描层共用 pair_pure_futures_spread 做 basis blend。

## 代码地图

<!-- id: pf-code -->

| 路径 | 职责 |
| --- | --- |
| scripts/cli/scan_pure_futures_spreads.py | 扫描入口（含 basis blend 调用） |
| scripts/execution/run_pure_futures_spread.py | 自动执行 runner（scan → filter → open/close） |
| scripts/execution/pure_futures_executor.py | 双腿下单与回滚 |
| scripts/execution/pure_futures_watcher.py | 持仓监控 |
| scripts/execution/settle_mismatch_planner.py | 跨周期现金流分析（执行侧） |
| scripts/backtest/backtest_pure_futures_spread.py | 回测 |
| server/routes/scanner.py | API 缓存、阈值过滤、费率重算 |
