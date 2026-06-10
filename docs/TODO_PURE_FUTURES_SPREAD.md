# Pure Futures Funding Spread Arbitrage — 任务追踪

**状态**: ✅ Phase 1+2+2.5 完成 | **优先级**: 🟡 中 | **复杂度**: 🟢 中 | **收益**: 🟢 高

---

## 📌 核心思路

跨交易所纯永续资金费差套利 — **无需现货、无需借贷、无需转账**，仅利用永续合约的资金费率差异进行 Delta-Neutral 对冲。

### 机制

```
交易所 A: BTC 永续 funding = +0.05% (多头付费、空头收费)
交易所 B: BTC 永续 funding = -0.10% (多头收费、空头付费)

策略:
  • A 中做多 0.5 BTC (+0.05% 每8h)
  • B 中做空 0.5 BTC (-0.10% 每8h)
  
净收益:
  • 每8h: |−0.10% − (+0.05%)| = 0.15%
  • 每年: 0.15% × (365×24/8) = 65.25% APY
  
成本:
  • 交易费: 2 × (0.05% 永续) = 0.10%
  • 净边际: 0.15% - 0.10% = 0.05% / 8h ≈ 22% APY

完全对冲:
  • Δ = +0.5 BTC - 0.5 BTC = 0 (价格无风险)
  • Γ = 0 (无凸性)
  • 纯资金费收益
```

---

## 📋 检查清单

### Phase 1 完成标准

- [x] `pure_futures_spread.py` 策略引擎实现完成
- [x] 单元测试覆盖 >80%
- [x] `scan_pure_futures_spreads.py` CLI 扫描工具可用
- [x] `report_pure_futures_spreads.py` 持续性报告可用
- [x] 配置模板 `config.pure_futures.spread.json`

### Phase 2 完成标准

- [x] `pure_futures_executor.py` 支持纯永续双腿（开仓/平仓/回滚）
  - [x] 开仓前保证金硬校验：futures 余额不足时自动从 spot 划转差额，
        仍不足则在下首单前放弃（含 capital_buffer_pct 预留）
- [x] `run_pure_futures_spread.py` Runner 可用（scan→decide→execute）
- [x] `pure_futures_trade.py` 手动 CLI 可用
- [x] 位置追踪系统 (`data/pure-futures/positions.json`)
- [x] Journal 日志记录 (`data/pure-futures/journal.jsonl`)

### Phase 2.5 完成标准（增强）

- [x] `pure_futures_watcher.py` 独立常驻监控进程
  - [x] 价差收窄退出检查
  - [x] 标记价漂移重平衡告警
  - [x] 自动重平衡执行（`autoRebalance=true` 时裁剪数量错配的超重腿）
  - [x] 单腿清算/强平检测 + emergency close
  - [x] Watcher 日志 (`data/pure-futures/watcher.jsonl`)
- [x] `settle_mismatch_planner.py` 结算周期错配分析
  - [x] 标准化 8h 窗口的实际资金费
  - [x] 现金流错配最大累积流出计算
  - [x] 调整后净边际 (adjusted net edge)
  - [x] 资金预留建议 (capital buffer)
  - [x] Runner 集成过滤
  - [x] capital_buffer 落地：缩小开仓名义本金 + 计入开仓前保证金校验
- [x] `orchestrate_funding.py --pure-futures` 编排器集成
  - [x] 扫描展示
  - [x] `--run-executor` 自动开仓
  - [x] `--auto-spread-watch` 启动 watcher
- [x] `backtest_pure_futures_spread.py` 历史回测框架
  - [x] JSONL 快照加载
  - [x] 历史 funding API 回测（`--history-bases`，无需采集，4 所公开端点 + 6h 缓存）
  - [x] 回放模拟（开仓/平仓/持仓）
  - [x] 统计指标（总收益/年化/回撤/Sharpe/胜率）
  - [x] Equity 曲线
  - [x] 最大持仓时间强制平仓
  - [x] 资金费按各腿真实 `interval_h` 在 UTC 对齐结算边界逐腿累计
        （结果与快照采集频率无关；开仓瞬间不计费）
  - [x] mismatch 候选接入 planner：开仓门槛用 `adjusted_net_edge_pct`
  - [x] 退出判断与入场阈值解耦（exit 用未过滤行情，`exit_edge` 真正生效）

### 测试覆盖

- [x] 78 tests, 100% pass（无回归）
  - 10 tests — watcher (exit/rebalance/leg_alive)
  - 6 tests — settle_mismatch_planner
  - 10 tests — pure_futures_strategy
  - 5 tests — backtest
  - 6 tests — pure_futures_executor
  - 8 tests — scan_pure_futures_spreads
  - 3 tests — report_pure_futures_spreads
  - 1 test — run_pure_futures_spread
  - 其余 — 现有模块

### Phase 3（后置，未开始）

- [ ] 三交易所三角套利
- [ ] 动态头寸大小（ML/规则）
- [ ] 机器学习价差预测 (LSTM/Transformer)
- [ ] 参数优化工具

---

## 📁 已实现的文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| `scripts/strategies/futures/pure_futures_spread.py` | ~200 | 决策引擎 |
| `scripts/cli/scan_pure_futures_spreads.py` | ~350 | 全市场扫描 CLI |
| `scripts/cli/report_pure_futures_spreads.py` | ~250 | JSONL 报告 CLI |
| `scripts/cli/pure_futures_trade.py` | ~100 | 手动交易 CLI |
| `scripts/execution/pure_futures_executor.py` | ~350 | 执行器（开/平/回滚） |
| `scripts/execution/run_pure_futures_spread.py` | ~150 | Runner |
| `scripts/execution/pure_futures_watcher.py` | ~300 | 独立常驻监控 |
| `scripts/execution/settle_mismatch_planner.py` | ~200 | 结算错配分析 |
| `scripts/backtest/backtest_pure_futures_spread.py` | ~350 | 历史回测 |
| `scripts/cli/orchestrate_funding.py` | ~400 | 编排器（含 --pure-futures） |
| `templates/config.pure_futures.spread.json` | ~30 | 配置模板 |
| **Total** | **~2700** | |

---

## 🚀 用法速查

```bash
# 1. 扫描全市场价差
python3 scripts/cli/scan_pure_futures_spreads.py --verbose
python3 scripts/cli/scan_pure_futures_spreads.py --watch 5  # 持续监控

# 2. 查看历史统计
python3 scripts/cli/report_pure_futures_spreads.py --since-hours 24

# 3. Dry-run 开仓
python3 scripts/execution/run_pure_futures_spread.py \
  --config templates/config.pure_futures.spread.json --once --verbose

# 4. 持续运行
python3 scripts/execution/run_pure_futures_spread.py \
  --config templates/config.pure_futures.spread.json --watch 5 --verbose

# 5. 独立 watcher（常驻监控已有持仓）
python3 scripts/execution/pure_futures_watcher.py \
  --config templates/config.pure_futures.spread.json --interval 30

# 6. 通过编排器一键运行
python3 scripts/cli/orchestrate_funding.py --pure-futures
python3 scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose

# 7. 回测
python3 scripts/backtest/backtest_pure_futures_spread.py \
  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json

# 8. 手动交易
python3 scripts/cli/pure_futures_trade.py open BTC --long-venue okx --short-venue bybit --trade-usd 500 --dry-run
python3 scripts/cli/pure_futures_trade.py list
python3 scripts/cli/pure_futures_trade.py close <position_id> --dry-run
```

---

## 💬 设计决策记录

### Q: Watcher 和 Runner 有什么区别？

**A**:
- **Runner**: 周期性 scan→decide→execute，负责**开仓+平仓**。适合定时任务。
- **Watcher**: 纯监控进程，只做**退出/对冲/告警**。适合作为 systemd/launchd 常驻服务。
- 两者可以同时运行。Runner 负责寻找新机会，Watcher 负责守护已有持仓。

### Q: settle_mismatch 的策略？

**A**:
- 默认 `allowSettleMismatch=false` → 直接跳过所有周期错配的候选对
- 设为 `true` 时，settle_mismatch_planner 会：
  1. 将两腿 rate 标准化到 8h 窗口
  2. 计算最大累积流出
  3. 扣除 30% timing risk penalty
  4. 只有 adjusted_net_edge > 0 才允许开仓
  5. 建议预留 capital_buffer（已接入开仓 sizing：`effective_trade_usd` 按 buffer 缩小名义本金）

### Q: Watcher 的重平衡怎么执行？

**A**:
- 数量错配（部分强平/ADL → 真实 delta 敞口）→ `rebalance_pure_futures_pair` 裁剪超重腿到与轻腿一致，只减不加
- 纯标记价漂移（两腿数量一致）→ 不可交易消除，保持告警；executor 返回 `balanced`
- 默认 `autoRebalance=false` 仅告警；设为 `true` 且持仓为 live 时自动执行
- 重平衡结果写入持仓记录 `last_rebalance` 与 `watcher.jsonl`

### Q: 回测数据从哪来？

**A**: 两种来源：

1. **scanner JSONL**：`scan_pure_futures_spreads.py --watch 5 --jsonl-file data/pure_futures_spreads.jsonl` 持续采集，回测读这个文件。优点是完整保留 scanner 视角（含 mark price），缺点是只有采集期内的数据。
2. **历史 funding API**（`--history-bases BTC,ETH --history-days 90`）：直接拉 4 所已结算的历史资金费（公开端点，磁盘缓存 6h），在每个真实结算点合成快照。无需提前采集，可回看任意天数；局限是无 mark price、且时刻 t 的可见费率取「下一个结算将结算的费率」（多数所当期费率区间内实时可见，与实盘 scanner 行为一致）。

### Q: 回测的资金费怎么计？

**A**: 不再按「每快照一期」近似。每条腿独立维护 `interval_h`，资金费只在跨过
UTC epoch 对齐的结算边界时累计（8h 腿 → 00:00/08:00/16:00；2h 腿 → 每偶数小时）。
多头腿每次结算支付 `long_rate`，空头腿收取 `short_rate`，费率取最近一次快照值。
因此 5 分钟高频采集与 8 小时低频采集得到的资金费一致，且能正确模拟 2h vs 8h
错配腿的不对称现金流。

---

**最后更新**: 2026-06-10
**状态**: ✅ Phase 1+2+2.5 完成
