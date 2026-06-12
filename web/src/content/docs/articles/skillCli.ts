import type { DocArticleDef, DocSection } from '../types'

const zhCN: DocSection[] = [
  {
    id: 'cli-overview',
    title: 'CLI 概览',
    blocks: [
      {
        type: 'p',
        text: '所有命令从仓库根目录运行，使用项目 venv：.venv/bin/python。网络请求命中真实交易所 API；一次 4 所扫描约 30-90 秒。',
      },
      {
        type: 'callout',
        variant: 'warn',
        text: '所有交易命令默认 dry-run。除非用户明确要求 live 交易，否则不要传 --live。',
      },
    ],
  },
  {
    id: 'scan',
    title: '扫描机会',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# Pure futures spread（主要策略）',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --json',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --verbose',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --min-edge 0.05 --json',
          '',
          '# 加入 Perp DEX（1h 结算）',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py \\',
          '  --venues binance,bitget,bybit,okx,hyperliquid,aster,lighter --json',
          '',
          '# 持续监控 → data/pure_futures_spreads.jsonl（JSONL 回测需要）',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --watch 5',
          '',
          '# Cash-and-carry（CEX）',
          '.venv/bin/python scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx,binance',
          '',
          '# 跨所 Unified',
          '.venv/bin/python scripts/cli/scan_unified_funding.py --verbose',
        ],
      },
      {
        type: 'p',
        text: '推荐 --json 解析结果。每条机会关键字段：base、direction（forward/reverse）、long_venue、short_venue、spread_pct、fee_pct、net_edge_pct（结算周期内扣费后）、annual_apy_pct、mark_spread_pct、settle_mismatch。',
      },
    ],
  },
  {
    id: 'trade',
    title: '交易（手动开/平/列表）',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# 开仓：默认 dry-run',
          '.venv/bin/python scripts/cli/pure_futures_trade.py open BTC \\',
          '  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run',
          '',
          '# 列表',
          '.venv/bin/python scripts/cli/pure_futures_trade.py list',
          '',
          '# 平仓',
          '.venv/bin/python scripts/cli/pure_futures_trade.py close <position_id> --dry-run',
        ],
      },
      {
        type: 'p',
        text: '持仓记录：scripts/data/pure-futures/positions.json（dry-run 和 live 共享，记录带 dry_run 标记）。注意：高价资产的小 trade-usd 可能触发 "Quantity floored to 0"。',
      },
    ],
  },
  {
    id: 'watcher',
    title: '持仓监控',
    blocks: [
      {
        type: 'formula',
        lines: [
          '.venv/bin/python scripts/execution/pure_futures_watcher.py \\',
          '  --config templates/config.pure_futures.spread.json --interval 30 --verbose',
        ],
      },
      {
        type: 'p',
        text: '长驻进程，建议后台运行。',
      },
    ],
  },
  {
    id: 'cli-backtest',
    title: '回测',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# 从交易所历史（无需本地数据）',
          '.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \\',
          '  --history-bases BTC,ETH,SOL --history-days 30 --capital 100000 --json',
          '',
          '# 从 JSONL（需要先 --watch 积累）',
          '.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \\',
          '  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json',
          '',
          '# 机会质量报告',
          '.venv/bin/python scripts/cli/report_pure_futures_spreads.py \\',
          '  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3',
        ],
      },
    ],
  },
  {
    id: 'orchestrator',
    title: '编排器（扫描 → 可选自动开仓）',
    blocks: [
      {
        type: 'formula',
        lines: [
          '.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --verbose',
          '# 自动开仓 top pairs（dry-run）',
          '.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose',
        ],
      },
    ],
  },
  {
    id: 'credentials',
    title: '凭证与环境',
    blocks: [
      {
        type: 'formula',
        lines: [
          '.venv/bin/python scripts/cli/setup_credentials.py --check   # 状态',
          '.venv/bin/python scripts/cli/setup_credentials.py           # 交互式设置（仅 live 交易需要）',
        ],
      },
      {
        type: 'p',
        text: '扫描和 dry-run 无需 API key。支持的 DEX 凭证：',
      },
      {
        type: 'ul',
        items: [
          'hyperliquid — sibling ../hyperliquid repo + HYPERLIQUID_API_KEY / SECRET',
          'aster — ASTER_API_KEY / ASTER_API_SECRET（Binance 兼容 HMAC）',
          'lighter — lighter-sdk + LIGHTER_API_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX, LIGHTER_API_KEY_INDEX',
        ],
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'DEX 只有永续（无现货/借贷）；Cash-and-carry 和 Unified 策略仅限 CEX。',
      },
    ],
  },
  {
    id: 'tests-dash',
    title: '测试与仪表盘',
    blocks: [
      {
        type: 'formula',
        lines: [
          '.venv/bin/python -m pytest scripts/tests/ -q     # 全量测试',
          'bash start.sh                                    # Web 仪表盘 http://localhost:8787',
        ],
      },
    ],
  },
]

const zhTW: DocSection[] = [
  {
    id: 'cli-overview',
    title: 'CLI 概覽',
    blocks: [
      {
        type: 'p',
        text: '所有命令從倉庫根目錄執行，使用專案 venv：.venv/bin/python。網路請求命中真實交易所 API；一次 4 所掃描約 30-90 秒。',
      },
      {
        type: 'callout',
        variant: 'warn',
        text: '所有交易命令預設 dry-run。除非使用者明確要求 live 交易，否則不要傳 --live。',
      },
    ],
  },
  {
    id: 'scan',
    title: '掃描機會',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# Pure futures spread（主要策略）',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --json',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --verbose',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --min-edge 0.05 --json',
          '',
          '# 加入 Perp DEX（1h 結算）',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py \\',
          '  --venues binance,bitget,bybit,okx,hyperliquid,aster,lighter --json',
          '',
          '# 持續監控 → data/pure_futures_spreads.jsonl（JSONL 回測需要）',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --watch 5',
          '',
          '# Cash-and-carry（CEX）',
          '.venv/bin/python scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx,binance',
          '',
          '# 跨所 Unified',
          '.venv/bin/python scripts/cli/scan_unified_funding.py --verbose',
        ],
      },
      {
        type: 'p',
        text: '推薦 --json 解析結果。每條機會關鍵欄位：base、direction（forward/reverse）、long_venue、short_venue、spread_pct、fee_pct、net_edge_pct（結算週期內扣費後）、annual_apy_pct、mark_spread_pct、settle_mismatch。',
      },
    ],
  },
  {
    id: 'trade',
    title: '交易（手動開/平/列表）',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# 開倉：預設 dry-run',
          '.venv/bin/python scripts/cli/pure_futures_trade.py open BTC \\',
          '  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run',
          '',
          '# 列表',
          '.venv/bin/python scripts/cli/pure_futures_trade.py list',
          '',
          '# 平倉',
          '.venv/bin/python scripts/cli/pure_futures_trade.py close <position_id> --dry-run',
        ],
      },
      {
        type: 'p',
        text: '持倉記錄：scripts/data/pure-futures/positions.json（dry-run 和 live 共享，記錄帶 dry_run 標記）。注意：高價資產的小 trade-usd 可能觸發 "Quantity floored to 0"。',
      },
    ],
  },
  {
    id: 'watcher',
    title: '持倉監控',
    blocks: [
      {
        type: 'formula',
        lines: [
          '.venv/bin/python scripts/execution/pure_futures_watcher.py \\',
          '  --config templates/config.pure_futures.spread.json --interval 30 --verbose',
        ],
      },
      {
        type: 'p',
        text: '長駐程序，建議後臺執行。',
      },
    ],
  },
  {
    id: 'cli-backtest',
    title: '回測',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# 從交易所歷史（無需本地資料）',
          '.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \\',
          '  --history-bases BTC,ETH,SOL --history-days 30 --capital 100000 --json',
          '',
          '# 從 JSONL（需要先 --watch 積累）',
          '.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \\',
          '  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json',
          '',
          '# 機會質量報告',
          '.venv/bin/python scripts/cli/report_pure_futures_spreads.py \\',
          '  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3',
        ],
      },
    ],
  },
  {
    id: 'orchestrator',
    title: '編排器（掃描 → 可選自動開倉）',
    blocks: [
      {
        type: 'formula',
        lines: [
          '.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --verbose',
          '# 自動開倉 top pairs（dry-run）',
          '.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose',
        ],
      },
    ],
  },
  {
    id: 'credentials',
    title: '憑證與環境',
    blocks: [
      {
        type: 'formula',
        lines: [
          '.venv/bin/python scripts/cli/setup_credentials.py --check   # 狀態',
          '.venv/bin/python scripts/cli/setup_credentials.py           # 互動式設定（僅 live 交易需要）',
        ],
      },
      {
        type: 'p',
        text: '掃描和 dry-run 無需 API key。支援的 DEX 憑證：',
      },
      {
        type: 'ul',
        items: [
          'hyperliquid — sibling ../hyperliquid repo + HYPERLIQUID_API_KEY / SECRET',
          'aster — ASTER_API_KEY / ASTER_API_SECRET（Binance 相容 HMAC）',
          'lighter — lighter-sdk + LIGHTER_API_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX, LIGHTER_API_KEY_INDEX',
        ],
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'DEX 只有永續（無現貨/借貸）；Cash-and-carry 和 Unified 策略僅限 CEX。',
      },
    ],
  },
  {
    id: 'tests-dash',
    title: '測試與儀表盤',
    blocks: [
      {
        type: 'formula',
        lines: [
          '.venv/bin/python -m pytest scripts/tests/ -q     # 全量測試',
          'bash start.sh                                    # Web 儀表盤 http://localhost:8787',
        ],
      },
    ],
  },
]

const en: DocSection[] = [
  {
    id: 'cli-overview',
    title: 'CLI overview',
    blocks: [
      {
        type: 'p',
        text: 'All commands run from the repo root. Use the project venv: .venv/bin/python. Network calls hit real exchange APIs; a full 4-venue scan takes ~30-90s.',
      },
      {
        type: 'callout',
        variant: 'warn',
        text: 'All trading commands default to dry-run. Never pass --live unless the user explicitly asks for live trading.',
      },
    ],
  },
  {
    id: 'scan',
    title: 'Scan opportunities',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# Pure futures spread (primary strategy)',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --json',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --verbose',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --min-edge 0.05 --json',
          '',
          '# Include perp DEXs (1h settlement)',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py \\',
          '  --venues binance,bitget,bybit,okx,hyperliquid,aster,lighter --json',
          '',
          '# Continuous monitoring → data/pure_futures_spreads.jsonl',
          '.venv/bin/python scripts/cli/scan_pure_futures_spreads.py --watch 5',
          '',
          '# Cash-and-carry',
          '.venv/bin/python scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx,binance',
          '',
          '# Unified cross-venue',
          '.venv/bin/python scripts/cli/scan_unified_funding.py --verbose',
        ],
      },
      {
        type: 'p',
        text: 'Prefer --json for parsing. Key fields: base, direction, long_venue, short_venue, spread_pct, fee_pct, net_edge_pct, annual_apy_pct, mark_spread_pct, settle_mismatch.',
      },
    ],
  },
  {
    id: 'trade',
    title: 'Trade (open / close / list)',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# Open: dry-run by default',
          '.venv/bin/python scripts/cli/pure_futures_trade.py open BTC \\',
          '  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run',
          '',
          '# List',
          '.venv/bin/python scripts/cli/pure_futures_trade.py list',
          '',
          '# Close',
          '.venv/bin/python scripts/cli/pure_futures_trade.py close <position_id> --dry-run',
        ],
      },
      {
        type: 'p',
        text: 'Positions ledger: scripts/data/pure-futures/positions.json. Very small trade-usd may abort with "Quantity floored to 0" for high-priced assets.',
      },
    ],
  },
  {
    id: 'watcher',
    title: 'Watcher',
    blocks: [
      {
        type: 'formula',
        lines: [
          '.venv/bin/python scripts/execution/pure_futures_watcher.py \\',
          '  --config templates/config.pure_futures.spread.json --interval 30 --verbose',
        ],
      },
      {
        type: 'p',
        text: 'Long-running; start in the background.',
      },
    ],
  },
  {
    id: 'cli-backtest',
    title: 'Backtest',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# From exchange history (no local data needed)',
          '.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \\',
          '  --history-bases BTC,ETH,SOL --history-days 30 --capital 100000 --json',
          '',
          '# From scanner JSONL',
          '.venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \\',
          '  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json',
          '',
          '# Quality report',
          '.venv/bin/python scripts/cli/report_pure_futures_spreads.py \\',
          '  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3',
        ],
      },
    ],
  },
  {
    id: 'orchestrator',
    title: 'Orchestrator',
    blocks: [
      {
        type: 'formula',
        lines: [
          '.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --verbose',
          '# Auto-open top pairs (dry-run)',
          '.venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose',
        ],
      },
    ],
  },
  {
    id: 'credentials',
    title: 'Credentials',
    blocks: [
      {
        type: 'formula',
        lines: [
          '.venv/bin/python scripts/cli/setup_credentials.py --check   # status',
          '.venv/bin/python scripts/cli/setup_credentials.py           # interactive setup (live only)',
        ],
      },
      {
        type: 'p',
        text: 'Dry-run scans need no keys. DEX credentials:',
      },
      {
        type: 'ul',
        items: [
          'hyperliquid — sibling ../hyperliquid repo + HYPERLIQUID_API_KEY / SECRET',
          'aster — ASTER_API_KEY / ASTER_API_SECRET (Binance-compatible HMAC)',
          'lighter — lighter-sdk + LIGHTER_API_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX',
        ],
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'DEX legs are perp-only (no spot/margin); cash-and-carry and unified strategies remain CEX-only.',
      },
    ],
  },
  {
    id: 'tests-dash',
    title: 'Tests & dashboard',
    blocks: [
      {
        type: 'formula',
        lines: [
          '.venv/bin/python -m pytest scripts/tests/ -q     # full suite',
          'bash start.sh                                    # web dashboard at http://localhost:8787',
        ],
      },
    ],
  },
]

export const skillCliArticle: DocArticleDef = {
  slug: 'skill-cli',
  titleKey: 'docs.articles.skillCli.title',
  descKey: 'docs.articles.skillCli.desc',
  tagKey: 'docs.articles.skillCli.tag',
  tagType: 'default',
  sectionsByLocale: {
    'zh-CN': zhCN,
    'zh-TW': zhTW,
    en,
  },
}
