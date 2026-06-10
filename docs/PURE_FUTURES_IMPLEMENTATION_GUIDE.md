# Pure Futures Spread —— 实现快速指南

**使用场景**: 当你需要快速启动 Phase 1 和 Phase 2 的开发时，参考本文档

---

## 第一部分：Phase 1-A（决策引擎）— 3 天完成

### Step 1: 修改 `unified_funding_pool.py` (+100 行)

**目标**: 新增两个方法用于查询纯永续资金费差

在文件末尾添加：

```python
def best_pure_futures_spread(
    self,
    base: str,
    min_spread_pct: float = 0.10,
) -> dict[str, Any] | None:
    """
    同资产在不同交易所的最优永续资金费差。
    
    返回:
    {
        "base": "BTC",
        "long_venue": "binance",           # 正费最高(多头收费最少)
        "short_venue": "okx",              # 负费最低(空头付费最多)
        "long_rate_pct": 0.05,
        "short_rate_pct": -0.10,
        "spread_pct": 0.15,                # 原始价差
        "total_fee_pct": 0.10,             # 2 × futures_fee_pct(venue)
        "net_edge_pct": 0.05,              # 0.15 - 0.10
        "annual_pct": 21.9,                # (0.05 / 8) × 365 × 24
    }
    或 None（无利可图）
    """
    legs = self.legs_by_base.get(base, [])
    if len(legs) < 2:
        return None
    
    best = None
    for i, long_leg in enumerate(legs):
        for short_leg in legs[i+1:]:
            # 配对逻辑：取最优组合
            spread = long_leg.rate_pct - short_leg.rate_pct
            if spread <= min_spread_pct:
                continue
            
            fee = _futures_fee_pct(long_leg.venue) + _futures_fee_pct(short_leg.venue)
            net_edge = spread - fee
            
            if net_edge > 0:
                if best is None or net_edge > best.get("net_edge_pct", 0):
                    annual = (net_edge / 8.0) * (365.0 * 24.0 / 8.0)  # per 8h cycle
                    best = {
                        "base": base,
                        "long_venue": long_leg.venue,
                        "short_venue": short_leg.venue,
                        "long_rate_pct": long_leg.rate_pct,
                        "short_rate_pct": short_leg.rate_pct,
                        "spread_pct": spread,
                        "total_fee_pct": fee,
                        "net_edge_pct": net_edge,
                        "annual_pct": annual,
                        "long_interval_h": long_leg.interval_h,
                        "short_interval_h": short_leg.interval_h,
                        "next_funding_ts": long_leg.next_funding_ts,
                    }
    return best


def funding_spread_matrix_pure(self, base: str) -> list[dict[str, Any]]:
    """
    构建资金费差矩阵（仅永续，所有交易所对）。
    
    返回按 net_edge 倒序的列表，每项格式同 best_pure_futures_spread()
    """
    legs = self.legs_by_base.get(base, [])
    if len(legs) < 2:
        return []
    
    pairs = []
    for i, long_leg in enumerate(legs):
        for short_leg in legs[i+1:]:
            spread = long_leg.rate_pct - short_leg.rate_pct
            if spread <= 0.05:  # 最小 0.05% 净边际
                continue
            
            fee = _futures_fee_pct(long_leg.venue) + _futures_fee_pct(short_leg.venue)
            net_edge = spread - fee
            
            if net_edge > 0:
                annual = (net_edge / 8.0) * (365.0 * 24.0 / 8.0)
                pairs.append({
                    "base": base,
                    "long_venue": long_leg.venue,
                    "short_venue": short_leg.venue,
                    "long_rate_pct": long_leg.rate_pct,
                    "short_rate_pct": short_leg.rate_pct,
                    "spread_pct": spread,
                    "total_fee_pct": fee,
                    "net_edge_pct": net_edge,
                    "annual_pct": annual,
                })
    
    return sorted(pairs, key=lambda x: -x["net_edge_pct"])
```

---

### Step 2: 创建 `scripts/strategies/futures/pure_futures_spread.py` (200 行)

```python
#!/usr/bin/env python3
"""纯永续资金费差套利决策引擎。"""

from __future__ import annotations
from typing import Any
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from backtest.unified_funding_pool import UnifiedFundingPool


def decide_pure_futures_spread(
    futures_state: dict[str, Any],
    prices: dict[str, float],
    cfg: dict[str, Any],
    funding_rates: dict[str, dict[str, float]],
    current_time_ms: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    纯永续资金费差套利决策。
    
    入参:
      - futures_state: 当前持仓状态
      - prices: {asset: price}
      - cfg: 配置（包含 pureFuturesArbitrage 字段）
      - funding_rates: {venue: {symbol: rate_pct}}
      - current_time_ms: 当前时间戳
    
    出参:
      - (trades, meta) tuple
    """
    trades: list[dict[str, Any]] = []
    meta: dict[str, Any] = {
        "strategy": "pure_futures_spread",
        "pairs_opened": [],
        "spread_matrix": [],
        "skipped_reasons": [],
    }
    
    cfg_pfs = cfg.get("pureFuturesArbitrage") or {}
    if not cfg_pfs:
        meta["skipped_reasons"].append("pureFuturesArbitrage config missing")
        return trades, meta
    
    venues = cfg_pfs.get("venues", ["binance", "okx", "bitget", "bybit"])
    max_pairs = int(cfg_pfs.get("maxConcurrentPairs", 5))
    trade_usd = float(cfg_pfs.get("tradeUsdPerPair", 5000.0))
    min_spread = float(cfg_pfs.get("minSpreadPct", 0.10))
    max_spread = float(cfg_pfs.get("maxSpreadPct", 0.50))
    
    # 1. 构建全市场价差矩阵
    pool = UnifiedFundingPool(venues=tuple(venues))
    pool.refresh(universe_min=0.03)  # 按需刷新
    
    all_pairs = []
    for base in pool.legs_by_base.keys():
        spreads = pool.funding_spread_matrix_pure(base)
        all_pairs.extend(spreads)
    
    meta["spread_matrix"] = all_pairs[:20]  # 记录 top-20
    
    # 2. 过滤和排序
    candidate_pairs = [
        p for p in all_pairs
        if min_spread <= p["spread_pct"] <= max_spread
    ]
    candidate_pairs.sort(key=lambda x: -x["net_edge_pct"])
    
    # 3. 取 top-N 并生成交易单
    open_count = 0
    for pair in candidate_pairs:
        if open_count >= max_pairs:
            break
        
        base = pair["base"]
        price = prices.get(base, 0.0)
        if price <= 0:
            meta["skipped_reasons"].append(f"{base}: price unavailable")
            continue
        
        amount_base = trade_usd / price
        
        # 长腿：做多（正费的交易所）
        trades.append({
            "symbol": f"{base}USDT",
            "type": "open_long",
            "venue": pair["long_venue"],
            "amount_base": round(amount_base, 6),
            "amount_usdt": round(trade_usd, 2),
            "pair_id": f"{base}:{pair['long_venue']}:{pair['short_venue']}",
            "funding_rate_pct": pair["long_rate_pct"],
            "meta": {
                "spread_pct": pair["spread_pct"],
                "net_edge_pct": pair["net_edge_pct"],
                "annual_pct": pair["annual_pct"],
            }
        })
        
        # 短腿：做空（负费的交易所）
        trades.append({
            "symbol": f"{base}USDT",
            "type": "open_short",
            "venue": pair["short_venue"],
            "amount_base": round(amount_base, 6),
            "amount_usdt": round(trade_usd, 2),
            "pair_id": f"{base}:{pair['long_venue']}:{pair['short_venue']}",
            "funding_rate_pct": pair["short_rate_pct"],
            "meta": {
                "spread_pct": pair["spread_pct"],
                "net_edge_pct": pair["net_edge_pct"],
                "annual_pct": pair["annual_pct"],
            }
        })
        
        meta["pairs_opened"].append(pair)
        open_count += 1
    
    return trades, meta
```

---

### Step 3: 创建 `scripts/cli/scan_pure_futures_spreads.py` (150 行)

```python
#!/usr/bin/env python3
"""CLI 工具：扫描纯永续资金费差机会。"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from backtest.unified_funding_pool import UnifiedFundingPool


def print_table(spreads: list[dict[str, Any]], top: int = 20) -> None:
    """打印美化表格。"""
    header = (
        f"{'Asset':<8} {'Long Venue':<12} {'Short Venue':<12} "
        f"{'Long Rate':<12} {'Short Rate':<12} {'Spread':<10} "
        f"{'Fee':<10} {'Net Edge':<10} {'Annual %':<10}"
    )
    print(header)
    print("=" * len(header))
    
    for spread in spreads[:top]:
        print(
            f"{spread['base']:<8} "
            f"{spread['long_venue']:<12} "
            f"{spread['short_venue']:<12} "
            f"{spread['long_rate_pct']:>+10.3f}% "
            f"{spread['short_rate_pct']:>+10.3f}% "
            f"{spread['spread_pct']:>8.3f}% "
            f"{spread['total_fee_pct']:>8.3f}% "
            f"{spread['net_edge_pct']:>8.3f}% "
            f"{spread['annual_pct']:>8.1f}%"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan pure futures funding rate spreads across venues"
    )
    parser.add_argument(
        "--asset", type=str, default=None,
        help="Filter by asset (e.g. BTC, ETH)"
    )
    parser.add_argument(
        "--venues", type=str, default="binance,okx,bitget,bybit",
        help="Comma-separated venue list"
    )
    parser.add_argument(
        "--min-spread", type=float, default=0.10,
        help="Minimum spread percentage"
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Show top N results"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON"
    )
    parser.add_argument(
        "--annual", action="store_true",
        help="Sort by annual percentage"
    )
    
    args = parser.parse_args()
    
    venues = tuple(args.venues.split(","))
    
    # Fetch funding pool
    print(f"[*] Fetching funding rates from {len(venues)} venues...", file=sys.stderr)
    pool = UnifiedFundingPool(venues=venues)
    pool.refresh(universe_min=0.03)
    
    # Build all spreads
    all_spreads = []
    for base in pool.legs_by_base.keys():
        if args.asset and base.upper() != args.asset.upper():
            continue
        
        matrix = pool.funding_spread_matrix_pure(base)
        all_spreads.extend(matrix)
    
    # Sort
    if args.annual:
        all_spreads.sort(key=lambda x: -x["annual_pct"])
    else:
        all_spreads.sort(key=lambda x: -x["net_edge_pct"])
    
    # Output
    if args.json:
        print(json.dumps(all_spreads[:args.top], indent=2, ensure_ascii=False))
    else:
        print(
            f"\n[+] Found {len(all_spreads)} profitable pairs "
            f"(min_spread={args.min_spread}%)\n"
        )
        print_table(all_spreads, top=args.top)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## 第二部分：Phase 1-B（回测框架）— 1-2 天

### Step 4: 创建 `scripts/backtest/backtest_pure_futures_spread.py` (300 行)

```python
#!/usr/bin/env python3
"""纯永续资金费差回测驱动。"""

import argparse
import json
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from backtest.unified_funding_pool import UnifiedFundingPool


@dataclass
class BacktestResult:
    total_return_pct: float
    annual_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    trade_count: int
    win_count: int
    win_rate_pct: float
    avg_trade_duration_days: float
    

def backtest_pure_futures_spread(
    funding_history: list[dict[str, Any]],  # 历史资金费数据
    initial_capital: float = 100000.0,
    trade_usd_per_pair: float = 5000.0,
    max_concurrent_pairs: int = 5,
    min_spread_pct: float = 0.10,
    exit_threshold_pct: float = 0.02,
) -> BacktestResult:
    """
    回放历史资金费差，统计策略表现。
    
    funding_history 格式:
    [
        {
            "timestamp": 1686...,
            "base": "BTC",
            "long_venue": "binance",
            "short_venue": "okx",
            "spread_pct": 0.15,
            "net_edge_pct": 0.05,
        },
        ...
    ]
    """
    
    capital = initial_capital
    trades_executed = 0
    trades_won = 0
    
    # TODO: 完整的回测逻辑
    # 1. 按时间戳排序
    # 2. 遍历每个时刻
    # 3. 计算当前可用的对
    # 4. 模拟开仓 / 平仓
    # 5. 累积 PnL
    
    return BacktestResult(
        total_return_pct=(capital - initial_capital) / initial_capital * 100,
        annual_return_pct=0.0,  # TODO
        max_drawdown_pct=0.0,   # TODO
        sharpe_ratio=0.0,       # TODO
        trade_count=trades_executed,
        win_count=trades_won,
        win_rate_pct=trades_won / max(trades_executed, 1) * 100,
        avg_trade_duration_days=0.0,  # TODO
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", default="BTC", help="Asset to backtest")
    parser.add_argument("--days", type=int, default=90, help="Backtest period in days")
    parser.add_argument("--capital", type=float, default=100000.0, help="Initial capital")
    parser.add_argument("--trade-usd", type=float, default=5000.0, help="USD per pair")
    
    args = parser.parse_args()
    
    print(f"[*] Loading {args.days}-day historical funding data for {args.asset}...")
    # TODO: Load history from OKX API or JSON file
    
    print(f"[*] Running backtest...")
    result = backtest_pure_futures_spread(
        funding_history=[],  # TODO
        initial_capital=args.capital,
        trade_usd_per_pair=args.trade_usd,
    )
    
    print("\n" + "="*50)
    print("Pure Futures Spread — Backtest Results")
    print("="*50)
    print(f"Total Return:        {result.total_return_pct:>8.2f}%")
    print(f"Annual Return:       {result.annual_return_pct:>8.2f}%")
    print(f"Max Drawdown:        {result.max_drawdown_pct:>8.2f}%")
    print(f"Sharpe Ratio:        {result.sharpe_ratio:>8.2f}")
    print(f"Trades Executed:     {result.trade_count:>8d}")
    print(f"Win Rate:            {result.win_rate_pct:>8.1f}%")
    print(f"Avg Duration:        {result.avg_trade_duration_days:>8.1f} days")
    print("="*50)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## 第三部分：Phase 2（执行框架）— 4-5 天

### Step 5: 修改 `scripts/execution/cross_venue_executor.py`

**在 `Direction` 定义中添加**:
```python
Direction = Literal["forward", "reverse", "pure_futures"]
```

**在 `open_cross_venue_position()` 函数开始处添加**:
```python
def open_cross_venue_position(
    base: str,
    direction: Direction,
    futures_venue_id: str,
    spot_venue_id: str | None = None,  # ← 新增：pure_futures 时为 None
    trade_usd: float = 5000.0,
    *,
    dry_run: bool = True,
    ...
) -> CrossVenueResult:
    """
    开仓逻辑现在支持 pure_futures 模式。
    当 direction == "pure_futures" 时，忽略 spot_venue_id，仅在两个 futures 交易所上执行。
    """
    
    # ... 现有逻辑 ...
    
    if direction == "pure_futures":
        # 新增分支：纯永续模式
        # spot_venue_id 应为 None 或被忽略
        # 逻辑:
        # 1. futures_venue_id 做多
        # 2. spot_venue_id 做空（或取第二个参数）
        # 3. 并行执行，任一失败则回滚
        pass
```

### Step 6: 创建 `scripts/accounting/futures/pure_futures_ledger.py` (300 行)

```python
#!/usr/bin/env python3
"""纯永续配对头寸的生命周期管理。"""

import json
import time
from pathlib import Path
from typing import Any


class PureFuturesPairLedger:
    """维护所有活跃的纯永续配对头寸。"""
    
    def __init__(self, ledger_path: Path):
        self.ledger_path = ledger_path
        self.pairs: dict[str, dict[str, Any]] = self._load()
    
    def _load(self) -> dict[str, dict[str, Any]]:
        """从磁盘加载现有头寸。"""
        if not self.ledger_path.exists():
            return {}
        try:
            data = json.loads(self.ledger_path.read_text())
            return {p["pair_id"]: p for p in data if isinstance(data, list)}
        except Exception:
            return {}
    
    def _save(self) -> None:
        """保存到磁盘。"""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        data = list(self.pairs.values())
        self.ledger_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )
    
    def open_pair(self, pair_info: dict[str, Any]) -> None:
        """记录新开配对。"""
        pair_id = pair_info["pair_id"]
        pair_info["status"] = "active"
        pair_info["opened_at"] = int(time.time() * 1000)
        self.pairs[pair_id] = pair_info
        self._save()
    
    def rebalance_if_needed(
        self, 
        pair_id: str,
        long_qty: float,
        short_qty: float,
        max_skew_pct: float = 1.0
    ) -> bool:
        """检查两腿是否偏离，返回是否需要对冲。"""
        if pair_id not in self.pairs:
            return False
        
        pair = self.pairs[pair_id]
        if pair.get("status") != "active":
            return False
        
        # 计算偏离
        if short_qty <= 0:
            return False
        
        skew = abs(long_qty - short_qty) / short_qty
        if skew > max_skew_pct / 100.0:  # 转换为小数
            pair["last_rebalance_at"] = int(time.time() * 1000)
            pair["rebalance_needed"] = True
            self._save()
            return True
        return False
    
    def close_pair(self, pair_id: str, reason: str = "") -> bool:
        """平仓并标记为已关闭。"""
        if pair_id not in self.pairs:
            return False
        
        pair = self.pairs[pair_id]
        pair["status"] = "closed"
        pair["closed_at"] = int(time.time() * 1000)
        pair["close_reason"] = reason
        self._save()
        return True
    
    def get_active_pairs(self) -> list[dict[str, Any]]:
        """获取所有活跃配对。"""
        return [p for p in self.pairs.values() if p.get("status") == "active"]
```

---

## 关键检查清单

### Phase 1-A 完成标准
- [ ] `unified_funding_pool.py` 可以成功调用 `funding_spread_matrix_pure()`
- [ ] CLI 工具能在 <5s 扫描 200 个对
- [ ] 输出格式正确（JSON/Table）
- [ ] 单元测试覆盖 >80%
- [ ] Dry-run 模式验证逻辑

### Phase 1-B 完成标准
- [ ] 历史资金费数据成功加载
- [ ] 回测能运行完整 3 个月周期
- [ ] Sharpe > 2.0
- [ ] 最大回撤 < 5%

### Phase 2 完成标准
- [ ] `cross_venue_executor` 支持 `pure_futures` 模式
- [ ] 头寸追踪系统可用
- [ ] 监听工作正常（每 60s 检查一次）
- [ ] 自动平仓逻辑无误
- [ ] 集成测试通过，无死锁

---

## 临界决策点

| 决策 | 推荐 | 理由 |
|------|------|------|
| 1x 杠杆还是无杠杆 | **无杠杆** | 完全对冲，无清算风险 |
| 开仓策略（一次 vs Grid） | **一次性** | 严格对称要求，Grid 破坏对冲 |
| 最小持仓周期 | **8-24h** | 等待下一个资金费周期 |
| 平仓触发点 | **spread < exitThreshold** | 0.02% 比较激进，可调整到 0.05% |
| 监听频率 | **每 60s** | 平衡 API 成本与响应速度 |

---

**下一步**: 按照上述步骤逐步实现，每完成一个 step 提交一个 PR，方便 review 和 debug。
