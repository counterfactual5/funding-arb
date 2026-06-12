# 项目概览（README）

策略、快速启动、API、配置

## 项目概览

<!-- id: overview -->

Funding Rate Arbitrage Engine —— 跨交易所资金费率套利引擎，支持 Cash-and-Carry、Unified 跨所 carry 以及 Pure Futures（永续对永续）spread，搭配 Vue 仪表盘、CLI 和可选 Tauri 桌面端。

| 类别 | 交易所 |
| --- | --- |
| CEX（现货 + USDT-M 永续） | Binance · Bitget · Bybit · OKX |
| Perp DEX（扫描；支持交易处） | Hyperliquid · Aster · Lighter · EdgeX |
| Perp DEX（仅扫描） | dYdX v4（1h funding，交易适配器待做） |

## 仪表盘开仓

<!-- id: dashboard-open -->

Scanner 三个 Tab 均支持表格内 Dry-run 开仓（默认不提交实盘）。实盘需对应 venue 配置 API、余额充足，并关闭 Dry-run 开关。

| Tab | API strategy | 执行路径 |
| --- | --- | --- |
| Pure Futures | pure_futures | pure_futures_executor — 双永续腿 |
| Cash & Carry | carry | cross_venue_executor — 同所 spot + perp |
| Unified C&C | unified | cross_venue_executor — 跨所 spot + perp |

> ⚠️ DEX 若标记为 scan-only（如 dYdX），Open 按钮会禁用。EdgeX live 下单需 edgex-python-sdk 与账户密钥，建议先用 scripts/tools/verify_edgex_live.py 验证。

## 策略概览

<!-- id: strategies -->

| 策略 | CLI 入口 | 仪表盘 Tab | 说明 |
| --- | --- | --- | --- |
| Pure Futures Spread | scan_pure_futures_spreads.py | Scanner → Pure Futures | 一所长做多，另一所做空，捕获资金费率差。无需现货或借贷。 |
| Cash & Carry | scan_funding_arbitrage.py | Scanner → Cash & Carry | CEX 现货多头 + 永续空头（或借币反向）。 |
| Unified C&C | scan_unified_funding.py | Scanner → Unified C&C | 现货腿与期货腿在不同交易所，取最优组合。 |
| Cross-asset C&C | run_cash_and_carry.py | — | 多资产槽位竞争，只保留最高 spread。 |

## Pure Futures 指标

<!-- id: pure-futures-metrics -->

| 字段 | 含义 |
| --- | --- |
| net_edge_pct | funding spread − 双边开仓 taker 手续费 |
| mark_spread_pct | 两所标记价差（入场滑点风险） |
| real_edge_pct | net_edge_pct − mark_spread_pct（保守边际） |
| settle_mismatch | 结算周期不同（如 HL 1h vs CEX 8h） |

> ℹ️ 跨周期配对使用 basis-blend 模型（mark vs index，按结算进度加权）。详见「跨周期资金费率套利」文档。

## 快速启动

<!-- id: quick-start -->

```text
git clone <this-repo>
cd funding-arb
bash setup.sh
```

浏览器模式：bash start.sh → http://localhost:8787

桌面模式（需要 Rust）：bash start.sh --desktop

Windows：.\start.ps1 或 .\start.ps1 -Desktop

## CLI 扫描

<!-- id: cli-scan -->

```text
# Pure futures — 默认 CEX
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --verbose

# 加入 DEX
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py \
  --venues binance,bitget,bybit,okx,hyperliquid --json

# 持续监控 → data/pure_futures_spreads.jsonl
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --watch 5

# Cash-and-carry
.venv/bin/python scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx,binance

# Unified
.venv/bin/python scripts/cli/scan_unified_funding.py --verbose
```

## 执行与交易

<!-- id: cli-trade -->

```text
# 手动开仓（dry-run 默认）
.venv/bin/python scripts/cli/pure_futures_trade.py open BTC \
  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run

# 查看持仓
.venv/bin/python scripts/cli/pure_futures_trade.py list

# 平仓
.venv/bin/python scripts/cli/pure_futures_trade.py close <id> --dry-run

# 持仓监控
.venv/bin/python scripts/execution/pure_futures_watcher.py \
  --config templates/config.pure_futures.spread.json --interval 30

# 编排器
.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose
```

> ⚠️ 所有交易命令默认 dry-run。除非用户明确要求，否则不要传 --live。

## 回测与报告

<!-- id: backtest -->

```text
# 从交易所历史（无需本地数据）
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --history-bases BTC,ETH,SOL --history-days 30 --capital 100000 --json

# 从 JSONL 快照
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json

# 机会质量报告
.venv/bin/python scripts/cli/report_pure_futures_spreads.py \
  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3
```

## 费率策略与 VIP 档位

<!-- id: fee-policy -->

Scanner 的 net_edge 已扣除每腿 taker 手续费。费率解析逻辑位于 scripts/core/fee_providers.py。

| 模式 | 行为 |
| --- | --- |
| auto | 有 API key 时用实时费率；否则用 VIP 档位表 |
| tier | 静态 VIP 档位（scripts/core/vip_fee_tiers.py） |
| manual | 在策略配置中手动覆盖 |

在 Settings → Strategy 中配置：fee_mode、venue_fee_tiers、扫描阈值（min_edge_annual / min_edge_1h / min_edge_mismatch）。

## HTTP API 摘要

<!-- id: http-api -->

| 端点 | 用途 |
| --- | --- |
| GET /api/scanner/opportunities | 缓存扫描结果（venue 感知） |
| POST /api/scanner/trigger | 触发扫描 |
| GET /api/scanner/status | 扫描状态、上次扫描时间 |
| GET/POST /api/settings/strategy | 策略阈值、费率策略 |
| GET /api/settings/venues | 各所 scan/trade/live 能力 |
| POST /api/positions/open | 开仓：body 含 strategy（pure_futures|carry|unified）、dry_run（默认 true） |
| POST /api/backtest/run | 运行回测 |
| WS /ws/events | scanner.update 推送 |

## 配置

<!-- id: config -->

- 复制 .env.example → .env
- Paper 模式不需要 API key（配置中 dry_run: true）
- Live 模式需要 spot + USDT-M futures 交易权限；不需要提现权限
- 策略阈值与扫描场所：Settings → Strategy，持久化到 scripts/data/strategy_config.json
- CLI runner（run_pure_futures_spread / orchestrate --pure-futures / watcher）启动时自动合并该文件，与仪表盘同源

| 交易所 | 环境变量 |
| --- | --- |
| Bitget | BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE |
| Binance | BINANCE_API_KEY, BINANCE_API_SECRET |
| OKX | OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE |
| Bybit | BYBIT_API_KEY, BYBIT_SECRET_KEY |
| Hyperliquid | sibling ../hyperliquid repo + wallet keys |
| Aster | ASTER_API_KEY, ASTER_API_SECRET |
| Lighter | LIGHTER_API_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX |
| EdgeX | EDGEX_ACCOUNT_ID, EDGEX_TRADING_PRIVATE_KEY |

## 测试

<!-- id: testing -->

```text
pip install -r requirements.txt
.venv/bin/python -m pytest scripts/tests/ -q
# 245+ tests — scanners, fees, venues, executor, backtest
```
