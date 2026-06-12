# 跨周期资金费率套利

basis blend、real_edge 与代码实现

## 问题背景

<!-- id: ci-background -->

各交易所公布的 rate_pct 是当前结算周期内的费率，周期长度不同：

| 交易所 | 典型周期 | 含义 |
| --- | --- | --- |
| Binance / OKX / Bybit | 8h | 每 8 小时结算一次 |
| Bitget | 2h 或 8h | 部分合约 2h |
| Hyperliquid / Lighter / dYdX v4 | 1h | 每小时结算 |
| EdgeX | 4h | 多数主流合约 240min |
| Aster | 按合约 | 读 fundingInfo，常见 8h |

若简单做 spread_naive = short_rate_pct - long_rate_pct，会把 1h 的 0.01% 与 8h 的 0.05% 放在同一量级比较，严重失真。

## 为什么不能只做线性外推

<!-- id: ci-linear-problem -->

```text
# 朴素归一化
rate_hourly = rate_pct / interval_h
spread = (short_hourly - long_hourly) × min(interval_long, interval_short)
```

在周期刚结算完时合理（基差已收敛，rate_pct 反映新周期起点）。但在周期中途，premium（mark 相对 index 的偏离）会持续累积，下一期实际 funding 往往更接近基差隐含费率。

## 模型目标

<!-- id: ci-model-goal -->

- 将两边费率统一到每小时基准
- 用 mark-index 基差估计「本周期剩余时间内的预期 funding」
- 按结算进度在「已公布 rate」与「基差隐含 rate」之间加权混合
- 输出可解释字段（spread_source、settle_progress、basis_pct）

## 何时启用跨周期模型

<!-- id: ci-when -->

```text
is_mismatch = |long_interval_h − short_interval_h| > 0.5
```

- is_mismatch == false → 同周期，直接用 rate_pct / interval_h，spread_source = rate
- is_mismatch == true → 启用 basis blend（有 index）或线性回退（无 index）

## 数据依赖

<!-- id: ci-data-deps -->

| 字段 | 说明 |
| --- | --- |
| rate_pct | 当前待结算资金费率（%） |
| interval_h | 结算周期（小时） |
| mark_price | 标记价格 |
| index_price | 指数 / 预言机价格 |
| next_funding_ts | 下次结算时间（ms） |
| last_settle_ts | 上次结算时间（ms），可由 next - interval 推导 |

| 交易所 | index_price 来源 | 跨周期 basis blend |
| --- | --- | --- |
| Binance | premiumIndex.indexPrice | ✅ |
| Bitget | indexPrice | ✅ |
| Bybit | indexPrice | ✅ |
| OKX | idxPx（mark-price 接口） | ✅ |
| Hyperliquid | oraclePx | ✅ |
| Aster | 继承 Binance provider | ✅ |
| Lighter | 无公开 index → 0 | ❌ 回退 rate_linear |
| EdgeX | 无公开 index → 0 | ❌ 回退 rate_linear |
| dYdX v4 | indexer 仅 oraclePrice（mark≈index） | ❌ 回退 rate_linear |

> ℹ️ dYdX 链上费率 = 60 分钟 premium TWAP + 利率项，每小时整点支付；nextFundingRate 是预测值，与 CEX 8h 配对时务必用 min_edge_mismatch。

## 结算进度 progress

<!-- id: ci-progress -->

```text
progress = elapsed / period_length   ∈ [0, 1]

# 计算优先级：
1. 有 last_settle_ts 与 next_funding_ts: (now − last) / (next − last)
2. 仅有 next_funding_ts: 用剩余时间反推
3. 皆无: 回退 0.5
```

- progress ≈ 0：刚结算完，更信任已公布的 rate_pct
- progress ≈ 1：即将结算，更信任 mark-index 基差隐含的下期费率

## 基差 basis_pct

<!-- id: ci-basis -->

```text
basis_pct = (mark_price − index_price) / index_price × 100%
```

按交易所对单周期溢价封顶（VENUE_BASIS_CAP_PCT），避免极端 mark-index 差制造虚假大边际：

| 类型 | 单周期 cap | 说明 |
| --- | --- | --- |
| Binance / Bybit / Bitget / OKX / Aster / EdgeX | ±0.30% | 约为典型 funding clamp 的 3 倍，过滤极端噪声 |
| Hyperliquid / Lighter / dYdX | ±0.50% | 无硬顶或 oracle-only，放宽 cap |
| 未知 venue | ±0.50% | DEFAULT_BASIS_CAP_PCT |

## 混合 hourly 与 spread

<!-- id: ci-blend -->

```text
rate_hourly  = rate_pct / interval_h
basis_hourly = basis_pct / interval_h
blended_hourly = (1 − progress) × rate_hourly + progress × basis_hourly
```

```text
eff_interval = min(long_interval_h, short_interval_h)
spread_pct   = (short_blended − long_blended) × eff_interval
net_edge_pct = spread_pct − fee_pct（双边开仓 taker）
real_edge_pct = net_edge_pct − mark_spread_pct
```

## 流程图

<!-- id: ci-flow -->

拉取各所 rate / mark / index / 结算时间 → 判断 interval 差 > 0.5h → 计算进度与基差 → 有 index 则 basis_blend，否则 rate_linear → 合成 spread → net_edge = spread − fees → mark_spread 过滤 + min_edge 阈值。

## 扫描输出字段

<!-- id: ci-fields -->

| 字段 | 说明 |
| --- | --- |
| settle_mismatch | 是否跨周期 |
| same_interval | not settle_mismatch |
| long_interval_h / short_interval_h | 各腿结算周期 |
| spread_source | rate / basis_blend / rate_linear |
| long_basis_pct / short_basis_pct | 各腿 mark-index 溢价（%） |
| long_settle_progress / short_settle_progress | 各腿混合权重（= progress） |
| spread_pct | 混合后的周期 spread（%） |
| net_edge_pct | 扣费后净边际（%） |
| mark_spread_pct | 两所标记价差（%） |

## 风控与配置叠加

<!-- id: ci-risk -->

- min_edge_mismatch：跨周期对可要求更高 net_edge_pct（Settings 可配）
- min_edge_1h：双 1h 同周期可用更低阈值
- max_mark_spread_pct：两所 mark 价差超阈值则丢弃
- settle_mismatch_planner：执行侧将两腿线性归一化到 8h 窗口，分析现金流不对称
- VIP 费率策略影响 net_edge / real_edge 中的 fee_pct

## 代码地图

<!-- id: ci-code-map -->

| 路径 | 职责 |
| --- | --- |
| scripts/core/cross_interval_funding.py | 混合模型纯函数（可单测） |
| scripts/cli/scan_pure_futures_spreads.py | 扫描入口，调用混合模型 |
| scripts/tests/test_cross_interval_funding.py | 模型单测 |
| scripts/execution/settle_mismatch_planner.py | 执行侧现金流 / 8h 归一化分析 |
| server/routes/scanner.py | API 缓存、min_edge_mismatch 过滤 |
| web/src/views/Scanner.vue | 展示 settle_mismatch、Cross 筛选、real edge |

## 数值示例

<!-- id: ci-example -->

场景：BTC，Hyperliquid vs Binance，跨周期。

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

> ℹ️ 若用朴素线性外推，HL 仅 0.04%/h，spread 会低估 HL 作为 short 腿的优势。

## EdgeX 4h 线性回退示例

<!-- id: ci-example-edgex -->

场景：BTC，EdgeX（4h，无 index）vs Binance（8h）。EdgeX 腿无法 basis blend，spread_source 对短腿为 rate_linear。

| 腿 | rate_pct | interval_h | blend |
| --- | --- | --- | --- |
| Short @ EdgeX | 0.02 | 4 | rate_linear → 0.02/4 = 0.005 %/h |
| Long @ Binance | 0.08 | 8 | basis_blend（有 index） |

```text
eff_interval = min(4, 8) = 4h
spread ≈ (short_hourly − long_blended) × 4
需同时满足 min_edge_mismatch 与 settle_mismatch_planner 现金流检查
```

## 已知限制

<!-- id: ci-limits -->

| 项 | 说明 |
| --- | --- |
| 现金流惩罚 | planner 在 scanner net_edge 上叠加 timing 惩罚，不重复计算 spread |
| 全局 basis 封顶 | 固定 ±1%/周期，未按交易所真实 premium clamp 细分 |
| 无 index 的 DEX | Lighter、EdgeX 跨周期只能 rate_linear |
| 历史 JSONL | 旧快照若无 index_price / progress 字段，回放无法复现混合模型 |
