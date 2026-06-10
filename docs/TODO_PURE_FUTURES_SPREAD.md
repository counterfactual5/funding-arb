# Pure Futures Funding Spread Arbitrage — 待做任务

**状态**: 📋 需求文档 | **优先级**: 🟡 中 | **复杂度**: 🟢 中 | **收益**: 🟢 高

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

### 与现有套利模式对比

| 模式 | 需求 | 成本 | APY | 启动难度 | 风险 |
|------|------|------|-----|--------|------|
| **Forward** | 现货市场 | spot(0.1%) + perp(0.05%) = 0.15% | 20-40% | 高 | 跨所转账 |
| **Reverse** | 借贷市场 | spot(0.1%) + perp(0.05%) + borrow(1-8%) = 1.25%+ | 30-50% | 高 | 清算风险 |
| **Pure Futures** ⭐ | 仅永续市场 | 2×perp(0.05%) = 0.10% | 30-60% | **低** | **无** |

---

## 🎯 MVP 阶段（第一阶段）

### 任务 1.1: 新增策略模块

**文件**: `scripts/strategies/futures/pure_futures_spread.py`

```python
def decide_pure_futures_spread(
    futures_state: dict[str, Any],
    prices: dict[str, float],
    cfg: dict[str, Any],
    funding_rates: dict[str, dict[str, float]],  # {venue: {symbol: rate_pct}}
    current_time_ms: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    纯永续资金费差套利决策引擎。
    
    入参:
      - funding_rates: {
            "binance": {"BTCUSDT": 0.05, "ETHUSDT": 0.02},
            "okx": {"BTCUSDT": -0.10, "ETHUSDT": -0.05},
            ...
        }
      - cfg.pureFuturesArbitrage: {
            "maxConcurrentPairs": 5,
            "tradeUsdPerPair": 5000.0,
            "venues": ["binance", "bitget", "okx", "bybit"],
            "minSpreadPct": 0.10,    # 入场最小价差 (每周期)
            "maxSpreadPct": 0.50,    # 防异常价格上限
            "exitThreshold": 0.02,   # 价差低于此值平仓
            "rebalanceIntervalMin": 60,  # 每60分钟检查重平衡
        }
    
    出参:
      - trades: [
            {
              "symbol": "BTCUSDT",
              "type": "open_long",
              "venue": "binance",
              "amount_base": 0.5,
              "amount_usdt": 15000,
              "funding_rate_pct": 0.05,
              "pair_id": "BTC:binance:okx",  # 用于跟踪双腿
            },
            {
              "symbol": "BTCUSDT",
              "type": "open_short",
              "venue": "okx",
              "amount_base": 0.5,
              "amount_usdt": 15000,
              "funding_rate_pct": -0.10,
              "pair_id": "BTC:binance:okx",
            }
        ]
      - meta: {
            "strategy": "pure_futures_spread",
            "pairs_opened": [{...}],
            "pairs_closed": [{...}],
            "spread_matrix": [{...}],
        }
    """
```

**关键逻辑**:

1. **资金费矩阵**
   - 对每个资产，扫描所有交易所的 funding 率
   - 构建 (high_venue, low_venue, spread) 三元组
   - 按 spread 倒序排列

2. **候选对生成**
   - 筛选: spread ≥ minSpreadPct
   - 筛选: spread ≤ maxSpreadPct (防异常)
   - 按 (spread / total_fee) 排名
   - 取 top-N (≤ maxConcurrentPairs)

3. **头寸管理**
   - 每对 (asset, long_venue, short_venue) 作为一个「配对头寸」
   - 用 `pair_id` 关联两条腿，确保同时开平
   - 追踪: 入场价差、入场时间、平仓目标

4. **平仓决策**
   - 监听实时资金费
   - 当 current_spread < exitThreshold 时触发平仓
   - 两腿同时平 (无先后)

---

### 任务 1.2: 扩展资金费汇聚池

**文件**: `scripts/backtest/unified_funding_pool.py`

**新增方法**:

```python
def best_pure_futures_spread(
    self,
    base: str,
    min_spread: float = 0.10,
) -> dict[str, Any] | None:
    """
    同资产在不同交易所的最优永续资金费差。
    
    返回:
    {
        "base": "BTC",
        "long_venue": "binance",           # 正费最高(多头收费少)
        "short_venue": "okx",              # 负费最低(空头付费)
        "long_rate_pct": 0.05,
        "short_rate_pct": -0.10,
        "spread_pct": 0.15,                # 原始价差
        "total_fee_pct": 0.10,             # 2 × futures_fee_pct(venue)
        "net_edge_pct": 0.05,              # 0.15 - 0.10
        "annual_pct": 21.9,                # (0.05 / 8) × 365 × 24
        "long_interval_h": 8,
        "short_interval_h": 8,
        "next_funding_ts": 1686....,
    }
    """
```

**实现要点**:

```python
def funding_spread_matrix_pure(self, base: str) -> list[dict[str, Any]]:
    """
    构建资金费差矩阵（仅永续）。
    
    返回按 spread 倒序的三元组列表:
    [
        {
            "base": "BTC",
            "long_venue": "binance",
            "long_rate": 0.05,
            "short_venue": "okx",
            "short_rate": -0.10,
            "spread": 0.15,
            "net_edge": 0.05,
        },
        ...
    ]
    """
    legs = self.legs_by_base.get(base, [])
    pairs = []
    for i, long_leg in enumerate(legs):
        for short_leg in legs[i+1:]:
            # 两个都是 positive -> long 配高, short 配低
            if long_leg.rate_pct >= 0 and short_leg.rate_pct >= 0:
                if long_leg.rate_pct < short_leg.rate_pct:
                    long_leg, short_leg = short_leg, long_leg
            # 两个都是 negative -> long 配负最轻, short 配负最重
            elif long_leg.rate_pct < 0 and short_leg.rate_pct < 0:
                if long_leg.rate_pct > short_leg.rate_pct:  # e.g. -0.05 > -0.10
                    long_leg, short_leg = short_leg, long_leg
            # 一正一负 -> 正的做多, 负的做空
            elif long_leg.rate_pct < short_leg.rate_pct:
                long_leg, short_leg = short_leg, long_leg
            
            spread = long_leg.rate_pct - short_leg.rate_pct
            fee = _futures_fee_pct(long_leg.venue) + _futures_fee_pct(short_leg.venue)
            net_edge = spread - fee
            
            if net_edge >= 0:  # 只记录正收益
                pairs.append({...})
    
    return sorted(pairs, key=lambda x: -x["spread"])
```

---

### 任务 1.3: 新增扫描 CLI 工具

**文件**: `scripts/cli/scan_pure_futures_spreads.py`

```bash
# 用法示例
python3 scripts/cli/scan_pure_futures_spreads.py
python3 scripts/cli/scan_pure_futures_spreads.py --asset BTC --min-spread 0.10 --json
python3 scripts/cli/scan_pure_futures_spreads.py --top 10 --annual
```

**功能**:

```python
def main():
    """扫描全市场永续资金费差机会。"""
    
    # 1. 从所有交易所拉取 funding rates (并行)
    pool = UnifiedFundingPool(venues=["binance", "bitget", "okx", "bybit"])
    pool.refresh(universe_min=0.03)
    
    # 2. 计算每个资产的最优配对
    spreads_by_asset = {}
    for base in pool.legs_by_base.keys():
        spreads = pool.funding_spread_matrix_pure(base)
        if spreads:
            spreads_by_asset[base] = spreads[0]  # top-1
    
    # 3. 按年化收益排序
    all_spreads = list(spreads_by_asset.values())
    all_spreads.sort(key=lambda x: -x["net_edge_pct"])
    
    # 4. 输出表格 / JSON
    if args.json:
        print(json.dumps([s.to_dict() for s in all_spreads[:args.top]]))
    else:
        # 美化表格输出
        print_table(all_spreads[:args.top])
```

**输出示例**:

```
Asset | Long Venue | Short Venue | Long Rate | Short Rate | Spread | Fee   | Net Edge | Annual %
------|------------|-------------|-----------|------------|--------|-------|----------|----------
BTC   | Binance    | OKX         | +0.05%    | -0.10%     | 0.15%  | 0.10% | 0.05%    | 21.9%
ETH   | Bitget     | OKX         | +0.03%    | -0.08%     | 0.11%  | 0.10% | 0.01%    | 4.4%
SOL   | Bybit      | OKX         | +0.08%    | -0.12%     | 0.20%  | 0.10% | 0.10%    | 43.8%
```

---

## 🚀 Phase 2: 生产化执行（第二阶段）

### 任务 2.1: 跨交易所同步执行

**修改**: `scripts/execution/cross_venue_executor.py`

**新增**:

```python
def open_pure_futures_pair(
    pair_id: str,                # "BTC:binance:okx"
    long_venue: str,             # "binance"
    short_venue: str,            # "okx"
    asset: str,                  # "BTC"
    trade_usd: float,            # 5000
    ref_price: float,
    dry_run: bool = False,
) -> CrossVenueResult:
    """
    纯永续配对开仓 — 同时在两个交易所开双腿。
    
    核心逻辑:
      1. 计算两个交易所的入场数量 (按 trade_usd 和各自 mark price 分配)
      2. 并行提交两条永续单
      3. 如果一条失败，自动回滚另一条
      4. 记录 pair_id 到头寸追踪文件（供后续平仓用）
    
    头寸存储格式:
    {
        "pair_id": "BTC:binance:okx",
        "timestamp": 1686...,
        "long": {
            "venue": "binance",
            "symbol": "BTCUSDT",
            "side": "long",
            "amount": 0.5,
            "entry_price": 30000,
            "leverage": 1,
            "order_id": "xxx",
            "order_status": "filled",
        },
        "short": {
            "venue": "okx",
            "symbol": "BTCUSDT",
            "side": "short",
            "amount": 0.5,
            "entry_price": 30050,
            "leverage": 1,
            "order_id": "yyy",
            "order_status": "filled",
        },
        "status": "active",
        "spread_at_entry": 0.15,
    }
    """
```

### 任务 2.2: 头寸跟踪与同步监控

**新增**: `scripts/accounting/futures/pure_futures_ledger.py`

```python
class PureFuturesPairLedger:
    """纯永续配对头寸的生命周期管理。"""
    
    def __init__(self, ledger_path: Path):
        self.ledger_path = ledger_path
        self.pairs: dict[str, dict] = self._load()
    
    def open_pair(self, pair_info: dict) -> None:
        """记录新开配对。"""
        pair_id = pair_info["pair_id"]
        self.pairs[pair_id] = pair_info
        self._save()
    
    def rebalance_if_needed(self, pair_id: str, max_skew_pct: float = 1.0) -> bool:
        """
        检查两腿头寸是否偏离，如果超过 max_skew_pct 则小额对冲。
        
        场景:
          • Long 腿意外被清算 → 立即平仓 Short
          • Short 腿被强平 → 立即平仓 Long
          • 标记价格漂移 > 1% → 小额重平衡
        
        返回: 是否需要重平衡
        """
    
    def close_pair(self, pair_id: str, reason: str = "") -> CrossVenueResult:
        """同时平仓两条腿，更新账本。"""
        pair = self.pairs[pair_id]
        result = cross_venue_executor.close_cross_venue_position(
            pair["long"]["venue"],
            pair["short"]["venue"],
            pair["asset"],
            pair["long"]["amount"],
            ...
        )
        pair["status"] = "closed"
        pair["close_reason"] = reason
        self._save()
        return result
```

### 任务 2.3: 实时价差监听

**新增**: `scripts/execution/pure_futures_watcher.py`

```python
class PureFuturesWatcher:
    """实时监听配对的资金费差，自动平仓。"""
    
    def __init__(self, config: dict):
        self.exit_threshold = config.get("exitThreshold", 0.02)
        self.rebalance_interval_min = config.get("rebalanceIntervalMin", 60)
        self.ledger = PureFuturesPairLedger(...)
    
    async def watch_forever(self):
        """后台常驻任务。"""
        while True:
            for pair_id, pair in self.ledger.pairs.items():
                if pair["status"] != "active":
                    continue
                
                # 获取最新资金费
                long_rate = fetch_funding(pair["long"]["venue"], pair["asset"])
                short_rate = fetch_funding(pair["short"]["venue"], pair["asset"])
                current_spread = long_rate - short_rate
                
                # 判断平仓条件
                if current_spread < self.exit_threshold:
                    print(f"[{pair_id}] Spread collapsed to {current_spread}% → Closing")
                    self.ledger.close_pair(pair_id, f"Spread {current_spread}% < {self.exit_threshold}%")
                
                # 每小时检查重平衡
                if time_to_rebalance():
                    self.ledger.rebalance_if_needed(pair_id)
            
            await asyncio.sleep(60)
```

---

### 任务 2.4: 集成到编排器

**修改**: `scripts/cli/orchestrate_funding.py`

```python
# 新增命令行选项
parser.add_argument("--pure-futures", action="store_true",
                    help="仅扫描纯永续资金费差机会（无现货/借贷）")
parser.add_argument("--auto-spread-watch", action="store_true",
                    help="启用实时价差监听与自动平仓")

if args.pure_futures:
    # 只扫描纯永续机会
    pool.refresh(universe_min=0.03)
    spreads = {}
    for base in pool.legs_by_base.keys():
        best = pool.best_pure_futures_spread(base, min_spread=0.10)
        if best:
            spreads[base] = best
    
    if args.run_executor:
        executor = PureFuturesExecutor(config)
        for base, spread in spreads.items():
            result = executor.open_pure_futures_pair(spread)
            print(result)
    
    if args.auto_spread_watch:
        # 启动后台监听任务
        watcher = PureFuturesWatcher(config)
        asyncio.run(watcher.watch_forever())
```

---

## ✅ Phase 3: 优化与扩展（第三阶段）

### 任务 3.1: 三交易所三角套利

```
交易所 A: BTC funding = +0.05%
交易所 B: BTC funding = +0.02%
交易所 C: BTC funding = -0.10%

策略: A 做多 + B 中性 + C 做空
      (多一个对冲腿，风险分散)
```

### 任务 3.2: 动态头寸大小

```
根据实时资金费波动调整开仓规模：
  • 价差 > 0.20% → 满仓
  • 价差 0.10-0.20% → 半仓
  • 价差 < 0.10% → 闭仓
```

### 任务 3.3: 机器学习价差预测

```
用 LSTM/Transformer 预测未来 1h 的资金费走向，
提前发现最优入场点
```

### 任务 3.4: 回测框架

```
脚本: scripts/backtest/backtest_pure_futures_spread.py

功能:
  • 加载历史资金费数据
  • 回放价差变化
  • 统计 Sharpe / 最大回撤 / 交易次数
  • 对比不同阈值参数
```

---

## 📊 成功指标

| 指标 | 目标 | 说明 |
|------|------|------|
| **年化收益** | 30-60% | 基于当前市场资金费水平 |
| **最大回撤** | <5% | 纯对冲，回撤应极小 |
| **Sharpe 比** | >2.0 | 风险调整收益好 |
| **平均持仓周期** | 7-14 天 | 价差收敛时间 |
| **成功率** | >80% | 平仓时盈利的比例 |
| **平均佣金** | <0.15% | 不超过单个交易费 |

---

## 📋 检查清单

### Phase 1 完成标准

- [ ] `pure_futures_spread.py` 实现完成
- [ ] 单元测试覆盖 >80%
- [ ] `scan_pure_futures_spreads.py` CLI 工具可用
- [ ] 文档更新: README/USAGE/reference
- [ ] 在 dry-run 环境验证逻辑

### Phase 2 完成标准

- [ ] `cross_venue_executor` 支持纯永续双腿
- [ ] 头寸追踪系统可用
- [ ] 实时价差监听工作正常
- [ ] 自动平仓触发无误
- [ ] 集成测试通过
- [ ] 小额实盘验证 (paper first)

### Phase 3 完成标准

- [ ] 多交易所三角套利实现
- [ ] 回测框架可用
- [ ] 参数优化工具就位
- [ ] 性能基准测试完成

---

## 🔗 相关文档

- [README.md](README.md) — 系统总体架构
- [USAGE.md](USAGE.md) — 使用指南
- [reference.md](reference.md) — 配置完整参考
- [scripts/backtest/unified_funding_pool.py](scripts/backtest/unified_funding_pool.py) — 资金费汇聚池
- [scripts/execution/cross_venue_executor.py](scripts/execution/cross_venue_executor.py) — 跨交易所执行框架

---

## 💬 设计决策记录

### Q: 为什么不用现有的 Forward/Reverse C&C？

**A**: 现有模式依赖现货流动性或借贷市场：
  - Forward: 需要现货买卖，成本高 (0.1% spot 费)，且跨所转账风险
  - Reverse: 需要借贷额度，利息成本 1-8%，还有清算风险
  
纯永续完全规避这些：
  - 永续市场流动性充足（深度>现货）
  - 成本仅 0.1% (双方都是永续费)
  - 无系统性风险（完全对冲）

### Q: 头寸不对齐怎么办？

**A**: 三重防护：
  1. **同时下单** — 用 `asyncio.gather()` 并行，failure → 自动回滚
  2. **定期检查** — 每小时验证两腿头寸是否偏离 >1%
  3. **自动对冲** — 如果一腿异常，立即平掉另一腿

### Q: 为什么不用 Grid/Ladder 开仓？

**A**: 纯永续配对必须严格对称：
  - 一旦两腿头寸不对，就不再是 Delta-Neutral
  - 市场波动时，不对称的配对会暴露风险
  
所以采用 all-or-nothing 单笔开仓的策略。

### Q: 清算风险？

**A**: 理论上不存在：
  - 1x 杠杆，无清算可能
  - 如果用 leverage，则两腿必须同等倍数
  - 监听预警：如果标记价格偏差 >2%，自动平仓

---

## 📅 预计时间线

| Phase | 任务 | 工作量 | 时间 |
|-------|------|--------|------|
| 1 | 策略 + CLI | 3 个文件, 800 行 | 1-2 天 |
| 2 | 执行 + 监听 | 3 个文件, 1200 行 | 2-3 天 |
| 3 | 优化 + 回测 | 4 个文件, 2000 行 | 3-5 天 |
| **总计** | | **6000+ 行** | **6-10 天** |

---

**最后更新**: 2026-06-10
**作者**: Agent (基于用户想法)
**状态**: 🟡 待审批
