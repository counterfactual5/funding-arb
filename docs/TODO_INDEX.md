# 项目待做任务总览

本文档汇总所有待做任务的索引，按优先级和状态组织。

---

## 🎯 进行中的主要任务

### 1. 🟡 Pure Futures Funding Spread Arbitrage
**文件**: [TODO_PURE_FUTURES_SPREAD.md](TODO_PURE_FUTURES_SPREAD.md)

**概述**: 跨交易所纯永续资金费差套利 — 无需现货、无需借贷、无需转账

**阶段**:
- 📋 Phase 1 (MVP): 策略模块 + CLI 扫描工具 (~1-2 天)
- 🔄 Phase 2 (生产化): 执行 + 监听 + 自动平仓 (~2-3 天)
- ✨ Phase 3 (优化): 三角套利、ML 预测、回测框架 (~3-5 天)

**复杂度**: 🟢 中等
**收益**: 🟢 高 (30-60% APY)
**风险**: 🟢 低 (完全 Delta-Neutral)

**相关代码文件**:
- `../scripts/strategies/futures/` — 策略引擎
- `../scripts/backtest/unified_funding_pool.py` — 资金费汇聚
- `../scripts/execution/cross_venue_executor.py` — 跨交易所执行
- `../scripts/cli/orchestrate_funding.py` — 编排器

---

## 📊 完成状态速查表

| 任务 | 状态 | Phase | 优先级 | 预计时间 | 负责 |
|------|------|-------|--------|---------|------|
| Pure Futures Spread | 📋 需求 | 1-3 | 🟡 中 | 6-10 天 | - |

---

## 🚦 状态说明

- 📋 **需求文档** — 已写需求，等待审批或开发启动
- 🔄 **开发中** — 正在进行中
- ✅ **完成** — 已完成并验证
- 🐛 **BUG** — 发现问题，需要修复
- ⏸️ **暂停** — 由于依赖或其他原因暂停

---

## 💡 想法池（未正式立项）

这些都是有趣的想法，但还未形成正式需求文档：

### A. 三交易所三角套利
- BTC: A 做多 + B 中性 + C 做空
- 风险分散，收益潜力更高
- **依赖**: Pure Futures Phase 2 完成

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
   ├─ Pure Futures Phase 1: 1-2 天
   │  └─ MVP 完成，可扫描全市场
   │
   ├─ Pure Futures Phase 2: 2-3 天
   │  └─ 实盘可用，自动平仓就位
   │
   └─ Pure Futures Phase 3: 3-5 天 (可选)
      └─ 三角套利、ML 预测等增强功能
```

---

## 🔗 相关文档

- [README.md](../README.md) — 系统总体架构
- [MIGRATION_FROM_DCA.md](../MIGRATION_FROM_DCA.md) — 从 DCA 仓库拆分说明

---

**最后更新**: 2026-06-10
**维护者**: Agent
