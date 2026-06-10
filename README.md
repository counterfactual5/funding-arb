# Funding Rate Arbitrage Engine

跨交易所永续资金费率套利引擎（Cash-and-Carry + Cross-Asset Funding Arbitrage + Pure Futures Spread）。

支持 **Bitget / Binance / OKX / Bybit**（现货 + U本位永续），跨所拆分现货腿与合约腿追求全局最优价差。

## 策略

| 策略 | 入口 | 说明 |
|---|---|---|
| **Cash and Carry（单资产）** | `run_cash_and_carry.py` | 单资产 spot long + perp short，吃正资金费。单对冲腿。 |
| **Cross-Asset Arbitrage（多资产）** | `run_cash_and_carry.py` (配置 `crossAssetArbitrage.maxConcurrentPairs > 1`) | 多资产槽位抢占，只持仓最优价差对。 |
| **Reverse C&C** | via `reverse*` 参数 | 负费率时 margin borrow 卖出现货 + perp long。 |
| **Pure Futures Spread** ⭐ | `run_pure_futures_spread.py` / `orchestrate_funding.py --pure-futures` | 跨所 perp long + perp short，吃资金费差。无需现货/借贷/转账。 |

## 快速开始

```bash
git clone <this-repo>
cd funding-arb
bash setup.sh
```

### 扫描机会

```bash
# Cash-and-Carry 扫描
python3 scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx
python3 scripts/cli/scan_unified_funding.py --verbose   # 跨所拆分视图

# Pure Futures Spread 扫描
python3 scripts/cli/scan_pure_futures_spreads.py --verbose
python3 scripts/cli/scan_pure_futures_spreads.py --watch 5  # 持续监控写入 JSONL
```

### 纸面套利（dry-run）

```bash
# Cash-and-Carry
python3 scripts/execution/run_cash_and_carry.py \
  --config templates/config.cash_and_carry.btc.json --verbose

# Pure Futures Spread
python3 scripts/execution/run_pure_futures_spread.py \
  --config templates/config.pure_futures.spread.json --once --verbose
```

### 实盘运行

```bash
# Pure Futures Spread 持续运行
python3 scripts/execution/run_pure_futures_spread.py \
  --config templates/config.pure_futures.spread.json --watch 5 --verbose

# 独立 Watcher（常驻监控已有持仓）
python3 scripts/execution/pure_futures_watcher.py \
  --config templates/config.pure_futures.spread.json --interval 30 --verbose

# 通过编排器一键运行
python3 scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose
```

### 报告与回测

```bash
# 汇总最近 24 小时 Pure Futures 机会质量
python3 scripts/cli/report_pure_futures_spreads.py \
  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3

# 回测
python3 scripts/backtest/backtest_pure_futures_spread.py \
  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json

# 手动开/平仓
python3 scripts/cli/pure_futures_trade.py open BTC \
  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run
python3 scripts/cli/pure_futures_trade.py list
python3 scripts/pure_futures_trade.py close <position_id> --dry-run
```

### 跨所编排

```bash
python3 scripts/cli/orchestrate_funding.py --venues bitget,bybit
python3 scripts/cli/orchestrate_funding.py --pure-futures  # 纯永续模式
```

### 验证

```bash
pip install pytest
python3 -m pytest scripts/tests/ -q   # 118 tests, 全部 pass
```

## 配置

1. 复制 `.env.example` → `.env`
2. **Paper** 无需 API Key（`dry_run: true` 默认开启）
3. **Live** 填写对应交易所变量：

| 交易所 | 环境变量 |
|--------|----------|
| Bitget | `BITGET_API_KEY`, `BITGET_SECRET_KEY`, `BITGET_PASSPHRASE` |
| Binance | `BINANCE_API_KEY`, `BINANCE_API_SECRET` |
| OKX | `OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE` |
| Bybit | `BYBIT_API_KEY`, `BYBIT_SECRET_KEY` |

API Key 需勾选 **现货 + U本位合约** 读写/交易，禁止提币。

| 变量 | 含义 |
|------|------|
| `DCA_HOME` | 运行时数据根目录（state、journal、回测输出） |
| `DCA_RUNS_NAMESPACE` | 子目录名，默认 `cex-bitget` |
| `DCA_DRY_RUN=1` / `DCA_LIVE=1` | 强制模拟 / 实盘 |

## 目录结构

```
funding-arb/
├── templates/              # 策略配置模板
│   ├── config.cash_and_carry.*.json   # 各交易所 C&C 配置
│   └── config.pure_futures.spread.json # 纯永续资金费差配置
├── scripts/
│   ├── execution/
│   │   ├── run_cash_and_carry.py           # C&C runner
│   │   ├── run_pure_futures_spread.py      # Pure Futures runner
│   │   ├── pure_futures_executor.py        # 纯永续执行器（开/平/回滚）
│   │   ├── pure_futures_watcher.py         # 独立常驻监控进程
│   │   ├── settle_mismatch_planner.py      # 结算周期错配分析
│   │   ├── cross_venue_executor.py         # 跨所执行器
│   │   └── delta_neutral_executor.py       # 单所 delta-neutral 执行
│   ├── strategies/futures/
│   │   ├── pure_futures_spread.py          # 纯永续决策引擎
│   │   ├── cash_and_carry.py               # C&C 策略
│   │   └── cross_asset_arbitrage.py        # 跨资产策略
│   ├── backtest/
│   │   ├── unified_funding_pool.py         # 资金费统一池
│   │   ├── backtest_pure_futures_spread.py # 纯永续回测
│   │   ├── funding_providers.py            # 资金费提供者
│   │   └── borrow_providers.py             # 借贷提供者
│   ├── cli/
│   │   ├── orchestrate_funding.py          # 编排器（含 --pure-futures）
│   │   ├── scan_pure_futures_spreads.py    # 纯永续扫描 CLI
│   │   ├── report_pure_futures_spreads.py  # 持续性报告
│   │   ├── pure_futures_trade.py           # 手动交易 CLI
│   │   ├── scan_funding_arbitrage.py       # C&C 扫描 CLI
│   │   └── scan_unified_funding.py         # 跨所视图 CLI
│   ├── market/             # funding_batch, price_oracle, parallel_fetch
│   ├── accounting/futures/ # delta_neutral_portfolio
│   ├── venues/             # bitget / binance / okx / bybit
│   ├── core/               # config, notify
│   └── transfer/           # cross_venue_router, transfer_providers
├── docs/
│   ├── TODO_INDEX.md                          # 任务总览
│   ├── TODO_PURE_FUTURES_SPREAD.md            # 纯永续任务追踪
│   ├── PURE_FUTURES_SPREAD_ANALYSIS.md        # 深度架构分析
│   └── PURE_FUTURES_IMPLEMENTATION_GUIDE.md   # 实现指南
└── setup.sh
```

## 运行测试

```bash
pip install pytest
python3 -m pytest scripts/tests/ -q
# 78 tests — 覆盖 C&C、Reverse Margin、Pure Futures、Transfer Chain
```

## 来源

本项目从 [cex-adaptive-dca](https://github.com/counterfactual5/cex-adaptive-dca) 独立拆分而来。详见 [MIGRATION_FROM_DCA.md](MIGRATION_FROM_DCA.md)。

## License

Private repository — all rights reserved unless otherwise noted.
