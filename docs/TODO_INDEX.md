# 项目待做任务总览

本文档汇总所有待做任务的索引，按优先级和状态组织。

---

## 🎯 进行中的主要任务

### 1. ✅ Pure Futures Funding Spread Arbitrage
**文件**: [TODO_PURE_FUTURES_SPREAD.md](TODO_PURE_FUTURES_SPREAD.md)

**概述**: 跨交易所纯永续资金费差套利 — 无需现货、无需借贷、无需转账

**阶段**:
- ✅ Phase 1 (MVP): 策略模块 + CLI 扫描工具 — 已完成
- ✅ Phase 2 (生产化): 执行 + 监听 + 自动平仓 — 已完成
- ✅ Phase 2.5 (增强): watcher / settle-mismatch / 编排器集成 / 回测 — 已完成
- ⬇️ Phase 3 (后置): 三角套利、ML 预测 — 未开始

**复杂度**: 🟢 中等
**收益**: 🟢 高 (30-60% APY)
**风险**: 🟢 低 (完全 Delta-Neutral)

**已实现的代码文件**:

| 模块 | 文件 | 说明 |
|------|------|------|
| 策略引擎 | `scripts/strategies/futures/pure_futures_spread.py` | 决策引擎（开仓/平仓/退出判断） |
| 扫描器 | `scripts/cli/scan_pure_futures_spreads.py` | CLI 全市场扫描 + --watch JSONL |
| 报告器 | `scripts/cli/report_pure_futures_spreads.py` | JSONL 持续性统计报告 |
| 执行器 | `scripts/execution/pure_futures_executor.py` | 开仓/平仓 + 回滚 + naked 处理 |
| Runner | `scripts/execution/run_pure_futures_spread.py` | scan→decide→execute 周期 |
| Watcher | `scripts/execution/pure_futures_watcher.py` | 独立常驻监控进程 |
| 结算错配 | `scripts/execution/settle_mismatch_planner.py` | 周期错配分析 + 资金规划 |
| 编排器 | `scripts/cli/orchestrate_funding.py --pure-futures` | 全流程编排入口 |
| 回测 | `scripts/backtest/backtest_pure_futures_spread.py` | 历史 JSONL 回放回测 |
| 手动交易 | `scripts/cli/pure_futures_trade.py` | 手动 open/close/list CLI |
| 配置 | `templates/config.pure_futures.spread.json` | 策略配置模板 |

**测试覆盖**: 78 tests, 100% pass（含 30 个新测试）

---

## 📊 完成状态速查表

| 任务 | 状态 | Phase | 优先级 |
|------|------|-------|--------|
| 策略模块 + 扫描器 | ✅ 完成 | Phase 1 | 🟡 中 |
| 执行器 + Runner | ✅ 完成 | Phase 2 | 🟡 中 |
| Watcher 常驻监控 | ✅ 完成 | Phase 2.5 | 🟡 中 |
| 结算错配资金规划 | ✅ 完成 | Phase 2.5 | 🟡 中 |
| 编排器 --pure-futures | ✅ 完成 | Phase 2.5 | 🟢 低 |
| 历史回测框架 | ✅ 完成 | Phase 2.5 | 🟢 低 |
| 三角套利 / 动态仓位 | ⬇️ 后置 | Phase 3 | ⬇️ 后置 |
| ML 预测 | ⬇️ 后置 | Phase 3 | ⬇️ 后置 |

---

## 🚦 状态说明

- 📋 **需求文档** — 已写需求，等待审批或开发启动
- 🔄 **开发中** — 正在进行中
- ✅ **完成** — 已完成并验证
- 🐛 **BUG** — 发现问题，需要修复
- ⏸️ **暂停** — 由于依赖或其他原因暂停
- ⬇️ **后置** — 低优先级，后续处理

---

## 💡 想法池（未正式立项）

这些都是有趣的想法，但还未形成正式需求文档：

### A. 三交易所三角套利
- BTC: A 做多 + B 中性 + C 做空
- 风险分散，收益潜力更高
- **依赖**: Pure Futures Phase 2 完成 ✅

### B. 机器学习价差预测
- 用 LSTM/Transformer 预测未来 1h 资金费
- 提前发现最优入场点
- **工作量**: 5-7 天

### C. 多交易所并发交易
- 目前单次开仓是序列执行，考虑改成并发
- 减少网络延迟带来的失配
- **工作量**: 2-3 天

### D. 对标交易所价格差异
- 同资产在不同交易所的现货价格差
- 可能挖掘到现货套利机会
- **工作量**: 3-4 天

---

## 📅 时间线展望

```
当前 (2026-06-10)
   │
   ├─ ✅ Pure Futures Phase 1: 已完成
   │  └─ MVP 完成，可扫描全市场
   │
   ├─ ✅ Pure Futures Phase 2: 已完成
   │  └─ 实盘可用，自动平仓就位
   │
   ├─ ✅ Phase 2.5 增强: 已完成
   │  └─ watcher + mismatch planner + 编排器 + 回测
   │
   └─ ⬇️ Phase 3 (后置): 三角套利、ML 预测
      └─ 等需求确认后启动
```

---

## 🔗 相关文档

- [README.md](../README.md) — 系统总体架构
- [MIGRATION_FROM_DCA.md](../MIGRATION_FROM_DCA.md) — 从 DCA 仓库拆分说明
- [PURE_FUTURES_IMPLEMENTATION_GUIDE.md](PURE_FUTURES_IMPLEMENTATION_GUIDE.md) — 实现指南
- [PURE_FUTURES_SPREAD_ANALYSIS.md](PURE_FUTURES_SPREAD_ANALYSIS.md) — 价差分析

---

**最后更新**: 2026-06-10
**维护者**: Agent
