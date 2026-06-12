# CLI 手册（SKILL.md）

扫描、交易、回测命令速查

## CLI 概览

<!-- id: cli-overview -->

所有命令从仓库根目录运行，使用项目 venv：.venv/bin/python。网络请求命中真实交易所 API；一次 4 所扫描约 30-90 秒。

> ⚠️ 所有交易命令默认 dry-run。除非用户明确要求 live 交易，否则不要传 --live。

## 扫描机会

<!-- id: scan -->

```text
# Pure futures spread（主要策略）
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --json
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --verbose
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --min-edge 0.05 --json

# 加入 Perp DEX（1h 结算）
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py \
  --venues binance,bitget,bybit,okx,hyperliquid,aster,lighter --json

# 持续监控 → data/pure_futures_spreads.jsonl（JSONL 回测需要）
.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --watch 5

# Cash-and-carry（CEX）
.venv/bin/python scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx,binance

# 跨所 Unified
.venv/bin/python scripts/cli/scan_unified_funding.py --verbose
```

推荐 --json 解析结果。每条机会关键字段：base、direction（forward/reverse）、long_venue、short_venue、spread_pct、fee_pct、net_edge_pct（结算周期内扣费后）、annual_apy_pct、mark_spread_pct、settle_mismatch。

## 交易（手动开/平/列表）

<!-- id: trade -->

```text
# 开仓：默认 dry-run
.venv/bin/python scripts/cli/pure_futures_trade.py open BTC \
  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run

# 列表
.venv/bin/python scripts/cli/pure_futures_trade.py list

# 平仓
.venv/bin/python scripts/cli/pure_futures_trade.py close <position_id> --dry-run
```

持仓记录：scripts/data/pure-futures/positions.json（dry-run 和 live 共享，记录带 dry_run 标记）。注意：高价资产的小 trade-usd 可能触发 "Quantity floored to 0"。

## 持仓监控

<!-- id: watcher -->

```text
.venv/bin/python scripts/execution/pure_futures_watcher.py \
  --config templates/config.pure_futures.spread.json --interval 30 --verbose
```

长驻进程，建议后台运行。

## 回测

<!-- id: cli-backtest -->

```text
# 从交易所历史（无需本地数据）
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --history-bases BTC,ETH,SOL --history-days 30 --capital 100000 --json

# 从 JSONL（需要先 --watch 积累）
.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \
  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json

# 机会质量报告
.venv/bin/python scripts/cli/report_pure_futures_spreads.py \
  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3
```

## 编排器（扫描 → 可选自动开仓）

<!-- id: orchestrator -->

```text
.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --verbose
# 自动开仓 top pairs（dry-run）
.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose
```

## 凭证与环境

<!-- id: credentials -->

```text
.venv/bin/python scripts/cli/setup_credentials.py --check   # 状态
.venv/bin/python scripts/cli/setup_credentials.py           # 交互式设置（仅 live 交易需要）
```

扫描和 dry-run 无需 API key。支持的 DEX 凭证：

- hyperliquid — sibling ../hyperliquid repo + HYPERLIQUID_API_KEY / SECRET
- aster — ASTER_API_KEY / ASTER_API_SECRET（Binance 兼容 HMAC）
- lighter — lighter-sdk + LIGHTER_API_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX, LIGHTER_API_KEY_INDEX

> ℹ️ DEX 只有永续（无现货/借贷）；Cash-and-carry 和 Unified 策略仅限 CEX。

## 测试与仪表盘

<!-- id: tests-dash -->

```text
.venv/bin/python -m pytest scripts/tests/ -q     # 全量测试
bash start.sh                                    # Web 仪表盘 http://localhost:8787
```
