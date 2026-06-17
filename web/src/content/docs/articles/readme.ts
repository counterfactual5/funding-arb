import type { DocArticleDef, DocSection } from "../types";

const zhCN: DocSection[] = [
  {
    id: "overview",
    title: "项目概览",
    blocks: [
      {
        type: "p",
        text: "Funding Rate Arbitrage Engine —— 跨交易所资金费率套利引擎，支持 Cash-and-Carry、Unified 跨所 carry 以及 Pure Futures（永续对永续）spread，搭配 Vue 仪表盘、CLI 和可选 Tauri 桌面端。",
      },
      {
        type: "table",
        headers: ["类别", "交易所"],
        rows: [
          ["CEX（现货 + USDT-M 永续）", "Binance · Bitget · Bybit · OKX"],
          [
            "Perp DEX（扫描；支持交易处）",
            "Hyperliquid · Aster · Lighter · EdgeX",
          ],
          ["Perp DEX（仅扫描）", "dYdX v4（1h funding，交易适配器待做）"],
        ],
      },
    ],
  },
  {
    id: "dashboard-open",
    title: "仪表盘开仓",
    blocks: [
      {
        type: "p",
        text: "Scanner 三个 Tab 均支持表格内 Dry-run 开仓（默认不提交实盘）。实盘需对应 venue 配置 API、余额充足，并关闭 Dry-run 开关。",
      },
      {
        type: "table",
        headers: ["Tab", "API strategy", "执行路径"],
        rows: [
          ["Pure Futures", "pure_futures", "pure_futures_executor — 双永续腿"],
          ["Cash & Carry", "carry", "cross_venue_executor — 同所 spot + perp"],
          ["Unified C&C", "unified", "cross_venue_executor — 跨所 spot + perp"],
        ],
      },
      {
        type: "callout",
        variant: "warn",
        text: "DEX 若标记为 scan-only（如 dYdX），Open 按钮会禁用。EdgeX live 下单需 edgex-python-sdk 与账户密钥，建议先用 scripts/tools/verify_edgex_live.py 验证。",
      },
    ],
  },
  {
    id: "strategies",
    title: "策略概览",
    blocks: [
      {
        type: "table",
        headers: ["策略", "CLI 入口", "仪表盘 Tab", "说明"],
        rows: [
          [
            "Pure Futures Spread",
            "scan_pure_futures_spreads.py",
            "Scanner → Pure Futures",
            "一所长做多，另一所做空，捕获资金费率差。无需现货或借贷。",
          ],
          [
            "Cash & Carry",
            "scan_funding_arbitrage.py",
            "Scanner → Cash & Carry",
            "CEX 现货多头 + 永续空头（或借币反向）。",
          ],
          [
            "Unified C&C",
            "scan_unified_funding.py",
            "Scanner → Unified C&C",
            "现货腿与期货腿在不同交易所，取最优组合。",
          ],
          [
            "Cross-asset C&C",
            "run_cash_and_carry.py",
            "—",
            "多资产槽位竞争，只保留最高 spread。",
          ],
        ],
      },
    ],
  },
  {
    id: "pure-futures-metrics",
    title: "Pure Futures 指标",
    blocks: [
      {
        type: "table",
        headers: ["字段", "含义"],
        rows: [
          ["net_edge_pct", "funding spread − 双边开仓 taker 手续费"],
          ["mark_spread_pct", "两所标记价差（入场滑点风险）"],
          ["real_edge_pct", "net_edge_pct − mark_spread_pct（保守边际）"],
          ["settle_mismatch", "结算周期不同（如 HL 1h vs CEX 8h）"],
        ],
      },
      {
        type: "callout",
        variant: "info",
        text: "跨周期配对使用 basis-blend 模型（mark vs index，按结算进度加权）。详见「跨周期资金费率套利」文档。",
      },
    ],
  },
  {
    id: "quick-start",
    title: "快速启动",
    blocks: [
      {
        type: "formula",
        lines: ["git clone <this-repo>", "cd funding-arb", "bash setup.sh"],
      },
      {
        type: "p",
        text: "浏览器模式：bash start.sh → http://localhost:8787",
      },
      {
        type: "p",
        text: "桌面模式（需要 Rust）：bash start.sh --desktop",
      },
      {
        type: "p",
        text: "Windows：.\\start.ps1 或 .\\start.ps1 -Desktop",
      },
    ],
  },
  {
    id: "cli-scan",
    title: "CLI 扫描",
    blocks: [
      {
        type: "formula",
        lines: [
          "# Pure futures — 默认 CEX",
          ".venv/bin/python scripts/cli/scan_pure_futures_spreads.py --verbose",
          "",
          "# 加入 DEX",
          ".venv/bin/python scripts/cli/scan_pure_futures_spreads.py \\",
          "  --venues binance,bitget,bybit,okx,hyperliquid --json",
          "",
          "# 持续监控 → data/pure_futures_spreads.jsonl",
          ".venv/bin/python scripts/cli/scan_pure_futures_spreads.py --watch 5",
          "",
          "# Cash-and-carry",
          ".venv/bin/python scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx,binance",
          "",
          "# Unified",
          ".venv/bin/python scripts/cli/scan_unified_funding.py --verbose",
        ],
      },
    ],
  },
  {
    id: "cli-trade",
    title: "执行与交易",
    blocks: [
      {
        type: "formula",
        lines: [
          "# 手动开仓（dry-run 默认）",
          ".venv/bin/python scripts/cli/pure_futures_trade.py open BTC \\",
          "  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run",
          "",
          "# 查看持仓",
          ".venv/bin/python scripts/cli/pure_futures_trade.py list",
          "",
          "# 平仓",
          ".venv/bin/python scripts/cli/pure_futures_trade.py close <id> --dry-run",
          "",
          "# 持仓监控",
          ".venv/bin/python scripts/execution/pure_futures_watcher.py \\",
          "  --config templates/config.pure_futures.spread.json --interval 30",
          "",
          "# 编排器",
          ".venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose",
        ],
      },
      {
        type: "callout",
        variant: "warn",
        text: "所有交易命令默认 dry-run。除非用户明确要求，否则不要传 --live。",
      },
    ],
  },
  {
    id: "backtest",
    title: "回测与报告",
    blocks: [
      {
        type: "formula",
        lines: [
          "# 从交易所历史（无需本地数据）",
          ".venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \\",
          "  --history-bases BTC,ETH,SOL --history-days 30 --capital 100000 --json",
          "",
          "# 从 JSONL 快照",
          ".venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \\",
          "  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json",
          "",
          "# 机会质量报告",
          ".venv/bin/python scripts/cli/report_pure_futures_spreads.py \\",
          "  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3",
        ],
      },
    ],
  },
  {
    id: "fee-policy",
    title: "费率策略与 VIP 档位",
    blocks: [
      {
        type: "p",
        text: "Scanner 的 net_edge 已扣除每腿 taker 手续费。费率解析逻辑位于 scripts/core/fee_providers.py。",
      },
      {
        type: "table",
        headers: ["模式", "行为"],
        rows: [
          ["auto", "有 API key 时用实时费率；否则用 VIP 档位表"],
          ["tier", "静态 VIP 档位（scripts/core/vip_fee_tiers.py）"],
          ["manual", "在策略配置中手动覆盖"],
        ],
      },
      {
        type: "p",
        text: "在 Settings → Strategy 中配置：fee_mode、venue_fee_tiers、扫描阈值（min_edge_annual / min_edge_1h / min_edge_mismatch）。",
      },
    ],
  },
  {
    id: "http-api",
    title: "HTTP API 摘要",
    blocks: [
      {
        type: "table",
        headers: ["端点", "用途"],
        rows: [
          ["GET /api/scanner/opportunities", "缓存扫描结果（venue 感知）"],
          ["POST /api/scanner/trigger", "触发扫描"],
          ["GET /api/scanner/status", "扫描状态、上次扫描时间"],
          ["GET/POST /api/settings/strategy", "策略阈值、费率策略"],
          ["GET /api/settings/venues", "各所 scan/trade/live 能力"],
          [
            "POST /api/positions/open",
            "开仓：body 含 strategy（pure_futures|carry|unified）、dry_run（默认 true）",
          ],
          ["POST /api/backtest/run", "运行回测"],
          ["WS /ws/events", "scanner.update 推送"],
        ],
      },
    ],
  },
  {
    id: "config",
    title: "配置",
    blocks: [
      {
        type: "ul",
        items: [
          "复制 .env.example → .env",
          "Paper 模式不需要 API key（配置中 dry_run: true）",
          "Live 模式需要 spot + USDT-M futures 交易权限；不需要提现权限",
          "策略阈值与扫描场所：Settings → Strategy，持久化到 scripts/data/strategy_config.json",
          "CLI runner（run_pure_futures_spread / orchestrate --pure-futures / watcher）启动时自动合并该文件，与仪表盘同源",
        ],
      },
      {
        type: "table",
        headers: ["交易所", "环境变量"],
        rows: [
          ["Bitget", "BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE"],
          ["Binance", "BINANCE_API_KEY, BINANCE_API_SECRET"],
          ["OKX", "OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE"],
          ["Bybit", "BYBIT_API_KEY, BYBIT_SECRET_KEY"],
          ["Hyperliquid", "sibling ../hyperliquid repo + wallet keys"],
          ["Aster", "ASTER_API_KEY, ASTER_API_SECRET"],
          ["Lighter", "LIGHTER_API_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX"],
          ["EdgeX", "EDGEX_ACCOUNT_ID, EDGEX_TRADING_PRIVATE_KEY"],
        ],
      },
    ],
  },
  {
    id: "testing",
    title: "测试",
    blocks: [
      {
        type: "formula",
        lines: [
          "pip install -r requirements.txt",
          ".venv/bin/python -m pytest scripts/tests/ -q",
          "# 245+ tests — scanners, fees, venues, executor, backtest",
        ],
      },
    ],
  },
];

const zhTW: DocSection[] = [
  {
    id: "overview",
    title: "專案概覽",
    blocks: [
      {
        type: "p",
        text: "Funding Rate Arbitrage Engine —— 跨交易所資金費率套利引擎，支援 Cash-and-Carry、Unified 跨所 carry 以及 Pure Futures（永續對永續）spread，搭配 Vue 儀表盤、CLI 和可選 Tauri 桌面端。",
      },
      {
        type: "table",
        headers: ["類別", "交易所"],
        rows: [
          ["CEX（現貨 + USDT-M 永續）", "Binance · Bitget · Bybit · OKX"],
          [
            "Perp DEX（掃描；支援交易處）",
            "Hyperliquid · Aster · Lighter · EdgeX",
          ],
          ["Perp DEX（僅掃描）", "dYdX v4（1h funding，交易介面卡待做）"],
        ],
      },
    ],
  },
  {
    id: "dashboard-open",
    title: "儀表盤開倉",
    blocks: [
      {
        type: "p",
        text: "Scanner 三個 Tab 均支援表格內 Dry-run 開倉（預設不提交實盤）。實盤需對應 venue 配置 API、餘額充足，並關閉 Dry-run 開關。",
      },
      {
        type: "table",
        headers: ["Tab", "API strategy", "執行路徑"],
        rows: [
          ["Pure Futures", "pure_futures", "pure_futures_executor — 雙永續腿"],
          ["Cash & Carry", "carry", "cross_venue_executor — 同所 spot + perp"],
          ["Unified C&C", "unified", "cross_venue_executor — 跨所 spot + perp"],
        ],
      },
      {
        type: "callout",
        variant: "warn",
        text: "DEX 若標記為 scan-only（如 dYdX），Open 按鈕會禁用。EdgeX live 下單需 edgex-python-sdk 與賬戶金鑰，建議先用 scripts/tools/verify_edgex_live.py 驗證。",
      },
    ],
  },
  {
    id: "strategies",
    title: "策略概覽",
    blocks: [
      {
        type: "table",
        headers: ["策略", "CLI 入口", "儀表盤 Tab", "說明"],
        rows: [
          [
            "Pure Futures Spread",
            "scan_pure_futures_spreads.py",
            "Scanner → Pure Futures",
            "一所長做多，另一所做空，捕獲資金費率差。無需現貨或借貸。",
          ],
          [
            "Cash & Carry",
            "scan_funding_arbitrage.py",
            "Scanner → Cash & Carry",
            "CEX 現貨多頭 + 永續空頭（或借幣反向）。",
          ],
          [
            "Unified C&C",
            "scan_unified_funding.py",
            "Scanner → Unified C&C",
            "現貨腿與期貨腿在不同交易所，取最優組合。",
          ],
          [
            "Cross-asset C&C",
            "run_cash_and_carry.py",
            "—",
            "多資產槽位競爭，只保留最高 spread。",
          ],
        ],
      },
    ],
  },
  {
    id: "pure-futures-metrics",
    title: "Pure Futures 指標",
    blocks: [
      {
        type: "table",
        headers: ["欄位", "含義"],
        rows: [
          ["net_edge_pct", "funding spread − 雙邊開倉 taker 手續費"],
          ["mark_spread_pct", "兩所標記價差（入場滑點風險）"],
          ["real_edge_pct", "net_edge_pct − mark_spread_pct（保守邊際）"],
          ["settle_mismatch", "結算週期不同（如 HL 1h vs CEX 8h）"],
        ],
      },
      {
        type: "callout",
        variant: "info",
        text: "跨週期配對使用 basis-blend 模型（mark vs index，按結算進度加權）。詳見「跨週期資金費率套利」文件。",
      },
    ],
  },
  {
    id: "quick-start",
    title: "快速啟動",
    blocks: [
      {
        type: "formula",
        lines: ["git clone <this-repo>", "cd funding-arb", "bash setup.sh"],
      },
      {
        type: "p",
        text: "瀏覽器模式：bash start.sh → http://localhost:8787",
      },
      {
        type: "p",
        text: "桌面模式（需要 Rust）：bash start.sh --desktop",
      },
      {
        type: "p",
        text: "Windows：.\start.ps1 或 .\start.ps1 -Desktop",
      },
    ],
  },
  {
    id: "cli-scan",
    title: "CLI 掃描",
    blocks: [
      {
        type: "formula",
        lines: [
          "# Pure futures — 預設 CEX",
          ".venv/bin/python scripts/cli/scan_pure_futures_spreads.py --verbose",
          "",
          "# 加入 DEX",
          ".venv/bin/python scripts/cli/scan_pure_futures_spreads.py \\",
          "  --venues binance,bitget,bybit,okx,hyperliquid --json",
          "",
          "# 持續監控 → data/pure_futures_spreads.jsonl",
          ".venv/bin/python scripts/cli/scan_pure_futures_spreads.py --watch 5",
          "",
          "# Cash-and-carry",
          ".venv/bin/python scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx,binance",
          "",
          "# Unified",
          ".venv/bin/python scripts/cli/scan_unified_funding.py --verbose",
        ],
      },
    ],
  },
  {
    id: "cli-trade",
    title: "執行與交易",
    blocks: [
      {
        type: "formula",
        lines: [
          "# 手動開倉（dry-run 預設）",
          ".venv/bin/python scripts/cli/pure_futures_trade.py open BTC \\",
          "  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run",
          "",
          "# 檢視持倉",
          ".venv/bin/python scripts/cli/pure_futures_trade.py list",
          "",
          "# 平倉",
          ".venv/bin/python scripts/cli/pure_futures_trade.py close <id> --dry-run",
          "",
          "# 持倉監控",
          ".venv/bin/python scripts/execution/pure_futures_watcher.py \\",
          "  --config templates/config.pure_futures.spread.json --interval 30",
          "",
          "# 編排器",
          ".venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose",
        ],
      },
      {
        type: "callout",
        variant: "warn",
        text: "所有交易命令預設 dry-run。除非使用者明確要求，否則不要傳 --live。",
      },
    ],
  },
  {
    id: "backtest",
    title: "回測與報告",
    blocks: [
      {
        type: "formula",
        lines: [
          "# 從交易所歷史（無需本地資料）",
          ".venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \\",
          "  --history-bases BTC,ETH,SOL --history-days 30 --capital 100000 --json",
          "",
          "# 從 JSONL 快照",
          ".venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \\",
          "  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json",
          "",
          "# 機會質量報告",
          ".venv/bin/python scripts/cli/report_pure_futures_spreads.py \\",
          "  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3",
        ],
      },
    ],
  },
  {
    id: "fee-policy",
    title: "費率策略與 VIP 檔位",
    blocks: [
      {
        type: "p",
        text: "Scanner 的 net_edge 已扣除每腿 taker 手續費。費率解析邏輯位於 scripts/core/fee_providers.py。",
      },
      {
        type: "table",
        headers: ["模式", "行為"],
        rows: [
          ["auto", "有 API key 時用實時費率；否則用 VIP 檔位表"],
          ["tier", "靜態 VIP 檔位（scripts/core/vip_fee_tiers.py）"],
          ["manual", "在策略配置中手動覆蓋"],
        ],
      },
      {
        type: "p",
        text: "在 Settings → Strategy 中配置：fee_mode、venue_fee_tiers、掃描閾值（min_edge_annual / min_edge_1h / min_edge_mismatch）。",
      },
    ],
  },
  {
    id: "http-api",
    title: "HTTP API 摘要",
    blocks: [
      {
        type: "table",
        headers: ["端點", "用途"],
        rows: [
          ["GET /api/scanner/opportunities", "快取掃描結果（venue 感知）"],
          ["POST /api/scanner/trigger", "觸發掃描"],
          ["GET /api/scanner/status", "掃描狀態、上次掃描時間"],
          ["GET/POST /api/settings/strategy", "策略閾值、費率策略"],
          ["GET /api/settings/venues", "各所 scan/trade/live 能力"],
          [
            "POST /api/positions/open",
            "開倉：body 含 strategy（pure_futures|carry|unified）、dry_run（預設 true）",
          ],
          ["POST /api/backtest/run", "執行回測"],
          ["WS /ws/events", "scanner.update 推送"],
        ],
      },
    ],
  },
  {
    id: "config",
    title: "配置",
    blocks: [
      {
        type: "ul",
        items: [
          "複製 .env.example → .env",
          "Paper 模式不需要 API key（配置中 dry_run: true）",
          "Live 模式需要 spot + USDT-M futures 交易許可權；不需要提現許可權",
          "策略閾值與掃描場所：Settings → Strategy，持久化到 scripts/data/strategy_config.json",
          "CLI runner（run_pure_futures_spread / orchestrate --pure-futures / watcher）啟動時自動合併該檔案，與儀表盤同源",
        ],
      },
      {
        type: "table",
        headers: ["交易所", "環境變數"],
        rows: [
          ["Bitget", "BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE"],
          ["Binance", "BINANCE_API_KEY, BINANCE_API_SECRET"],
          ["OKX", "OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE"],
          ["Bybit", "BYBIT_API_KEY, BYBIT_SECRET_KEY"],
          ["Hyperliquid", "sibling ../hyperliquid repo + wallet keys"],
          ["Aster", "ASTER_API_KEY, ASTER_API_SECRET"],
          ["Lighter", "LIGHTER_API_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX"],
          ["EdgeX", "EDGEX_ACCOUNT_ID, EDGEX_TRADING_PRIVATE_KEY"],
        ],
      },
    ],
  },
  {
    id: "testing",
    title: "測試",
    blocks: [
      {
        type: "formula",
        lines: [
          "pip install -r requirements.txt",
          ".venv/bin/python -m pytest scripts/tests/ -q",
          "# 245+ tests — scanners, fees, venues, executor, backtest",
        ],
      },
    ],
  },
];

const en: DocSection[] = [
  {
    id: "overview",
    title: "Overview",
    blocks: [
      {
        type: "p",
        text: "Cross-exchange funding rate arbitrage engine supporting Cash-and-Carry, unified cross-venue carry, and Pure Futures (perp–perp) spreads — with a Vue dashboard, CLI, and optional Tauri desktop shell.",
      },
      {
        type: "table",
        headers: ["Category", "Venues"],
        rows: [
          ["CEX (spot + USDT-M perps)", "Binance · Bitget · Bybit · OKX"],
          [
            "Perp DEX (scan; trade where supported)",
            "Hyperliquid · Aster · Lighter · EdgeX",
          ],
          [
            "Perp DEX (scan-only)",
            "dYdX v4 (1h funding; trading adapter pending)",
          ],
        ],
      },
    ],
  },
  {
    id: "dashboard-open",
    title: "Dashboard opens",
    blocks: [
      {
        type: "p",
        text: "All three Scanner tabs support in-table dry-run opens (live off by default). Live orders need API keys, balance, and the dry-run toggle off.",
      },
      {
        type: "table",
        headers: ["Tab", "API strategy", "Executor"],
        rows: [
          [
            "Pure Futures",
            "pure_futures",
            "pure_futures_executor — dual perp legs",
          ],
          [
            "Cash & Carry",
            "carry",
            "cross_venue_executor — same-venue spot + perp",
          ],
          [
            "Unified C&C",
            "unified",
            "cross_venue_executor — cross-venue spot + perp",
          ],
        ],
      },
      {
        type: "callout",
        variant: "warn",
        text: "Scan-only venues (e.g. dYdX) disable Open. For EdgeX live, use verify_edgex_live.py before real orders.",
      },
    ],
  },
  {
    id: "strategies",
    title: "Strategies",
    blocks: [
      {
        type: "table",
        headers: ["Strategy", "CLI entry", "Dashboard tab", "Description"],
        rows: [
          [
            "Pure Futures Spread",
            "scan_pure_futures_spreads.py",
            "Scanner → Pure Futures",
            "Long perp on one venue, short on another; capture funding rate differential. No spot or borrow.",
          ],
          [
            "Cash & Carry",
            "scan_funding_arbitrage.py",
            "Scanner → Cash & Carry",
            "Spot long + perp short (or reverse via borrow) on CEX.",
          ],
          [
            "Unified C&C",
            "scan_unified_funding.py",
            "Scanner → Unified C&C",
            "Spot and futures legs on different venues for best combined edge.",
          ],
          [
            "Cross-asset C&C",
            "run_cash_and_carry.py",
            "—",
            "Multi-asset slot contention; hold top spreads only.",
          ],
        ],
      },
    ],
  },
  {
    id: "pure-futures-metrics",
    title: "Pure Futures metrics",
    blocks: [
      {
        type: "table",
        headers: ["Field", "Meaning"],
        rows: [
          [
            "net_edge_pct",
            "Funding spread minus open-leg taker fees (both sides)",
          ],
          [
            "mark_spread_pct",
            "Mark-price gap between venues (entry slippage risk)",
          ],
          [
            "real_edge_pct",
            "net_edge_pct − mark_spread_pct (conservative edge)",
          ],
          [
            "settle_mismatch",
            "Different funding intervals (e.g. HL 1h vs CEX 8h)",
          ],
        ],
      },
      {
        type: "callout",
        variant: "info",
        text: 'Cross-interval pairs use a basis-blend model (mark vs index, weighted by settlement progress). See "Cross-Interval Funding Arbitrage" article.',
      },
    ],
  },
  {
    id: "quick-start",
    title: "Quick Start",
    blocks: [
      {
        type: "formula",
        lines: ["git clone <this-repo>", "cd funding-arb", "bash setup.sh"],
      },
      {
        type: "p",
        text: "Browser mode: bash start.sh → http://localhost:8787",
      },
      {
        type: "p",
        text: "Desktop mode (requires Rust): bash start.sh --desktop",
      },
      {
        type: "p",
        text: "Windows: .\\start.ps1 or .\\start.ps1 -Desktop",
      },
    ],
  },
  {
    id: "cli-scan",
    title: "CLI scanning",
    blocks: [
      {
        type: "formula",
        lines: [
          "# Pure futures — default CEX",
          ".venv/bin/python scripts/cli/scan_pure_futures_spreads.py --verbose",
          "",
          "# Include DEX",
          ".venv/bin/python scripts/cli/scan_pure_futures_spreads.py \\",
          "  --venues binance,bitget,bybit,okx,hyperliquid --json",
          "",
          "# Continuous watch → data/pure_futures_spreads.jsonl",
          ".venv/bin/python scripts/cli/scan_pure_futures_spreads.py --watch 5",
          "",
          "# Cash-and-carry",
          ".venv/bin/python scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx,binance",
          "",
          "# Unified",
          ".venv/bin/python scripts/cli/scan_unified_funding.py --verbose",
        ],
      },
    ],
  },
  {
    id: "cli-trade",
    title: "Execution & trading",
    blocks: [
      {
        type: "formula",
        lines: [
          "# Manual open (dry-run default)",
          ".venv/bin/python scripts/cli/pure_futures_trade.py open BTC \\",
          "  --long-venue okx --short-venue bybit --trade-usd 500 --dry-run",
          "",
          "# List positions",
          ".venv/bin/python scripts/cli/pure_futures_trade.py list",
          "",
          "# Close",
          ".venv/bin/python scripts/cli/pure_futures_trade.py close <id> --dry-run",
          "",
          "# Position watcher",
          ".venv/bin/python scripts/execution/pure_futures_watcher.py \\",
          "  --config templates/config.pure_futures.spread.json --interval 30",
          "",
          "# Orchestrator",
          ".venv/bin/python scripts/cli/orchestrate_funding.py --pure-futures --run-executor --verbose",
        ],
      },
      {
        type: "callout",
        variant: "warn",
        text: "All trading commands default to dry-run. Never pass --live unless the user explicitly asks.",
      },
    ],
  },
  {
    id: "backtest",
    title: "Backtest & reports",
    blocks: [
      {
        type: "formula",
        lines: [
          "# From exchange history (no local data needed)",
          ".venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \\",
          "  --history-bases BTC,ETH,SOL --history-days 30 --capital 100000 --json",
          "",
          "# From JSONL snapshots",
          ".venv/bin/python scripts/backtest/backtest_pure_futures_spread.py \\",
          "  --jsonl-file data/pure_futures_spreads.jsonl --capital 100000 --json",
          "",
          "# Opportunity quality report",
          ".venv/bin/python scripts/cli/report_pure_futures_spreads.py \\",
          "  --jsonl-file data/pure_futures_spreads.jsonl --since-hours 24 --min-samples 3",
        ],
      },
    ],
  },
  {
    id: "fee-policy",
    title: "Fee policy & VIP tiers",
    blocks: [
      {
        type: "p",
        text: "Net edge in the scanner deducts per-leg taker fees. Fee resolution lives in scripts/core/fee_providers.py.",
      },
      {
        type: "table",
        headers: ["Mode", "Behavior"],
        rows: [
          ["auto", "Live fee API when keys configured; else VIP tier table"],
          ["tier", "Static VIP ladder (scripts/core/vip_fee_tiers.py)"],
          ["manual", "Overrides in strategy config"],
        ],
      },
      {
        type: "p",
        text: "Configure in Settings → Strategy: fee_mode, venue_fee_tiers, scan thresholds (min_edge_annual / min_edge_1h / min_edge_mismatch).",
      },
    ],
  },
  {
    id: "http-api",
    title: "HTTP API summary",
    blocks: [
      {
        type: "table",
        headers: ["Endpoint", "Purpose"],
        rows: [
          [
            "GET /api/scanner/opportunities",
            "Cached scan results (venue-aware)",
          ],
          ["POST /api/scanner/trigger", "On-demand scan"],
          ["GET /api/scanner/status", "Scan state, last scan time"],
          ["GET/POST /api/settings/strategy", "Thresholds, fee policy"],
          ["GET /api/settings/venues", "Scan/trade/live capability per venue"],
          [
            "POST /api/positions/open",
            "Open: strategy (pure_futures|carry|unified), dry_run (default true)",
          ],
          ["POST /api/backtest/run", "Run backtest"],
          ["WS /ws/events", "scanner.update push"],
        ],
      },
    ],
  },
  {
    id: "config",
    title: "Configuration",
    blocks: [
      {
        type: "ul",
        items: [
          "Copy .env.example → .env",
          "Paper mode: no keys required (dry_run: true in config)",
          "Live mode: exchange keys with spot + USDT-M futures trade permission; no withdrawal",
          "Strategy thresholds: Settings → Strategy → scripts/data/strategy_config.json",
          "CLI runners merge that file on start (same as dashboard)",
        ],
      },
      {
        type: "table",
        headers: ["Exchange", "Environment variables"],
        rows: [
          ["Bitget", "BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE"],
          ["Binance", "BINANCE_API_KEY, BINANCE_API_SECRET"],
          ["OKX", "OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE"],
          ["Bybit", "BYBIT_API_KEY, BYBIT_SECRET_KEY"],
          ["Hyperliquid", "sibling ../hyperliquid repo + wallet keys"],
          ["Aster", "ASTER_API_KEY, ASTER_API_SECRET"],
          ["Lighter", "LIGHTER_API_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX"],
          ["EdgeX", "EDGEX_ACCOUNT_ID, EDGEX_TRADING_PRIVATE_KEY"],
        ],
      },
    ],
  },
  {
    id: "testing",
    title: "Testing",
    blocks: [
      {
        type: "formula",
        lines: [
          "pip install -r requirements.txt",
          ".venv/bin/python -m pytest scripts/tests/ -q",
          "# 245+ tests — scanners, fees, venues, executor, backtest",
        ],
      },
    ],
  },
];

export const readmeArticle: DocArticleDef = {
  slug: "overview",
  titleKey: "docs.articles.readme.title",
  descKey: "docs.articles.readme.desc",
  tagKey: "docs.articles.readme.tag",
  tagType: "info",
  sectionsByLocale: {
    "zh-CN": zhCN,
    "zh-TW": zhTW,
    en,
  },
};
