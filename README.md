# Funding Rate Arbitrage Engine

跨交易所永续资金费率套利引擎（Cash-and-Carry + Cross-Asset Funding Arbitrage）。

支持 **Bitget / Binance / OKX / Bybit**（现货 + U本位永续），跨所拆分现货腿与合约腿追求全局最优价差。

## 策略

| 策略 | 入口 | 说明 |
|---|---|---|
| **Cash and Carry（单资产）** | `run_cash_and_carry.py` | 单资产 spot long + perp short，吃正资金费。单对冲腿。 |
| **Cross-Asset Arbitrage（多资产）** | `run_cash_and_carry.py` (配置 `crossAssetArbitrage.maxConcurrentPairs > 1`) | 多资产槽位抢占，只持仓最优价差对。 |
| **Reverse C&C** | via `reverse*` 参数 | 负费率时 margin borrow 卖出现货 + perp long。 |

## 快速开始

```bash
git clone <this-repo>
cd funding-arb
bash setup.sh
```

### 扫描机会

```bash
python3 scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx
python3 scripts/cli/scan_unified_funding.py --verbose   # 跨所拆分视图
```

### 纸面套利（dry-run）

```bash
python3 scripts/execution/run_cash_and_carry.py \
  --config templates/config.cash_and_carry.btc.json --verbose
```

### 跨所编排

```bash
python3 scripts/cli/orchestrate_funding.py --venues bitget,bybit
```

### 回测验证

```bash
python3 scripts/backtest/walk_forward.py \
  --config templates/config.cash_and_carry.btc.json --days 730
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
│   └── config.cash_and_carry.*.json  # 各交易所 C&C 配置
├── scripts/
│   ├── execution/          # run_cash_and_carry, delta_neutral_executor
│   ├── strategies/futures/ # cash_and_carry, cross_asset_arbitrage
│   ├── backtest/           # unified_funding_pool, borrow_providers
│   ├── cli/                # scan / orchestrate / margin_smoke_test
│   ├── market/             # funding_batch, price_oracle, parallel_fetch
│   ├── accounting/futures/ # delta_neutral_portfolio
│   ├── venues/             # bitget / binance / okx / bybit
│   ├── core/               # config, notify, indicators
│   └── transfer/           # cross_venue_router, transfer_providers
└── setup.sh
```

## 运行测试

```bash
pip install pytest
python3 -m pytest scripts/tests/test_funding_arbitrage.py scripts/tests/test_reverse_margin.py scripts/tests/test_transfer_chain.py -q
```

## 来源

本项目从 [cex-adaptive-dca](https://github.com/counterfactual5/cex-adaptive-dca) 独立拆分而来。详见 [MIGRATION_FROM_DCA.md](MIGRATION_FROM_DCA.md)。

## License

Private repository — all rights reserved unless otherwise noted.
