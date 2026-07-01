import type { DocArticleDef, DocSection } from '../types'

const zhCN: DocSection[] = [
  {
    id: 'pf-overview',
    title: '概述',
    blocks: [
      {
        type: 'p',
        text: 'Pure Futures 是本系统的主策略：在两个交易所分别持有同一币种的永续多头与空头，赚取两所资金费率之差。不需要现货、不需要借币，天然跨所，Perp DEX（Hyperliquid / Aster / Lighter / EdgeX；dYdX 可扫描）也能参与。',
      },
      {
        type: 'callout',
        variant: 'info',
        text: '相比 Cash & Carry：双腿都是永续，taker 费率更低；无现货滑点；不受现货上架与借贷额度限制。',
      },
    ],
  },
  {
    id: 'pf-mechanics',
    title: '核心机制',
    blocks: [
      {
        type: 'p',
        text: '对每个币种，比较各所费率：在费率高的所做空（收更多 / 付更少），在费率低的所做多（付更少 / 收更多）。',
      },
      {
        type: 'formula',
        lines: [
          'spread_pct  = short_rate − long_rate（做空高费率腿、做多低费率腿）',
          'net_edge_pct = spread_pct − (long_taker + short_taker)',
          'real_edge_pct = net_edge_pct − mark_spread_pct',
        ],
      },
      {
        type: 'p',
        text: 'mark_spread_pct 是两所标记价的相对偏差：开仓时一腿贵一腿便宜，相当于入场即承担的价格错配。real_edge 把它从边际中扣掉，是最保守的可执行边际，Scanner 默认按它排序与筛选。',
      },
    ],
  },
  {
    id: 'pf-direction',
    title: 'Forward 与 Reverse 的含义',
    blocks: [
      {
        type: 'p',
        text: '多空方向永远是「空高费率、多低费率」。direction 标签只描述费率所处的区域：',
      },
      {
        type: 'table',
        headers: ['direction', '条件', '典型形态'],
        rows: [
          ['forward', '至少一腿费率 ≥ 0', '空头腿收正费率（或混合正负）'],
          ['reverse', '两腿费率都 < 0', '多头腿收负费率，空头腿付得更少'],
        ],
      },
    ],
  },
  {
    id: 'pf-vs-cc',
    title: '与 Cash & Carry 对比',
    blocks: [
      {
        type: 'table',
        headers: ['', 'Cash & Carry', 'Pure Futures'],
        rows: [
          ['两条腿', '现货 + 永续', '永续 + 永续'],
          ['手续费', '现货 taker 较高（~0.1%）', '双永续 taker 较低'],
          ['跨所', 'Unified 才拆腿', '天然跨所'],
          ['借币', '反向需要', '不需要'],
          ['DEX 参与', '不支持', 'HL / Aster / Lighter / EdgeX / dYdX（扫描）'],
          ['收益来源', '单所费率绝对值', '两所费率之差'],
        ],
      },
    ],
  },
  {
    id: 'pf-thresholds',
    title: '阈值与过滤',
    blocks: [
      {
        type: 'table',
        headers: ['参数', '含义'],
        rows: [
          ['min_spread', '原始费率差下限（默认 0.03%）'],
          ['min_edge', '扣费后净边际下限（默认 0.01%）'],
          ['min_edge_1h', '双 1h 同周期对的专用（更低）阈值'],
          ['min_edge_mismatch', '跨周期对的专用（更高）阈值'],
          ['max_mark_spread_pct', '两所标记价差上限，超过即丢弃'],
        ],
      },
      {
        type: 'p',
        text: 'min_edge_1h 更低是因为 1h 周期资金周转快、同周期无 timing risk；min_edge_mismatch 更高是为跨周期的结算不同步留风险溢价。上述阈值在 Settings → Strategy 配置，写入 scripts/data/strategy_config.json；Scanner API 与 CLI runner 共用。',
      },
    ],
  },
  {
    id: 'pf-settings',
    title: 'Settings 与 CLI 配置统一',
    blocks: [
      {
        type: 'table',
        headers: ['Dashboard 字段', 'CLI / 模板字段'],
        rows: [
          ['min_spread_annual', 'pureFuturesArbitrage.minSpreadPct'],
          ['min_edge_annual', 'pureFuturesArbitrage.minNetEdgePct'],
          ['min_edge_1h / min_edge_mismatch', '按腿周期在 runner 内逐行应用（见跨周期文档）'],
          ['trade_usd', 'pureFuturesArbitrage.tradeUsdPerPair'],
          ['max_positions', 'pureFuturesArbitrage.maxConcurrentPairs'],
          ['scan_venues', 'pureFuturesArbitrage.venues'],
          ['scan_interval_sec', 'scanIntervalMinutes（秒÷60）'],
          ['fee_mode / venue_fee_tiers', '扫描时 fee_providers 解析'],
        ],
      },
      {
        type: 'p',
        text: 'templates/config.pure_futures.spread.json 仍保留执行细节（parallelLegs、depthCheck、dry_run 等）；阈值以 strategy_config.json 为准。合并逻辑：scripts/core/strategy_config.py → apply_strategy_to_pure_futures_cfg()。',
      },
    ],
  },
  {
    id: 'pf-cross-interval',
    title: '跨周期配对',
    blocks: [
      {
        type: 'p',
        text: '当两腿结算周期不同（settle_mismatch，如 HL 1h vs Binance 8h），不能直接比较 rate_pct。系统先归一化到每小时，再用 mark-index 基差按结算进度加权混合（basis blend）。',
      },
      {
        type: 'ul',
        items: [
          'spread_source = rate：同周期，直接用公布费率',
          'spread_source = basis_blend：跨周期且有 index，使用混合模型',
          'spread_source = rate_linear：跨周期但无独立 index（Lighter / EdgeX / dYdX 腿），线性回退',
        ],
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'dYdX indexer 目前仅暴露 oraclePrice，mark≈index，basis blend 对该腿几乎不生效；与 CEX 8h 配对时按 rate_linear + min_edge_mismatch 更保守。',
      },
      {
        type: 'p',
        text: '完整推导、各所 index 来源与数值示例见「跨周期资金费率套利」。',
      },
    ],
  },
  {
    id: 'pf-execution',
    title: '执行与监控',
    blocks: [
      {
        type: 'ul',
        items: [
          '仪表盘：Scanner → Pure Futures 表格「开仓」，默认 dry-run；scan-only venue 按钮禁用',
          '手动交易：pure_futures_trade.py open / list / close（默认 dry-run）',
          '自动执行：run_pure_futures_spread.py --once / --watch（合并 strategy_config.json）',
          '持仓监控：pure_futures_watcher.py；parallelLegs 默认 true，双腿并发下单',
          '开仓前深度检查：futures_depth.py，DEX 订单簿拉取失败则阻止开仓（depthCheckFailOpen=false）',
        ],
      },
      {
        type: 'callout',
        variant: 'warn',
        text: '跨周期对在执行/回测中会经 settle_mismatch_planner 叠加现金流惩罚（在 scanner 的 net_edge 之上）；planner 与 unified pool 已与扫描层共用 pair_pure_futures_spread 做 basis blend。',
      },
    ],
  },
  {
    id: 'pf-code',
    title: '代码地图',
    blocks: [
      {
        type: 'table',
        headers: ['路径', '职责'],
        rows: [
          ['scripts/cli/scan_pure_futures_spreads.py', '扫描入口（含 basis blend 调用）'],
          ['scripts/execution/run_pure_futures_spread.py', '自动执行 runner（scan → filter → open/close）'],
          ['scripts/execution/pure_futures_executor.py', '双腿下单与回滚'],
          ['scripts/execution/pure_futures_watcher.py', '持仓监控'],
          ['scripts/execution/settle_mismatch_planner.py', '跨周期现金流分析（执行侧）'],
          ['scripts/backtest/backtest_pure_futures_spread.py', '回测'],
          ['server/routes/scanner.py', 'API 缓存、阈值过滤、费率重算'],
        ],
      },
    ],
  },
]

const zhTW: DocSection[] = [
  {
    id: 'pf-overview',
    title: '概述',
    blocks: [
      {
        type: 'p',
        text: 'Pure Futures 是本系統的主策略：在兩個交易所分別持有同一幣種的永續多頭與空頭，賺取兩所資金費率之差。不需要現貨、不需要借幣，天然跨所，Perp DEX（Hyperliquid / Aster / Lighter / EdgeX；dYdX 可掃描）也能參與。',
      },
      {
        type: 'callout',
        variant: 'info',
        text: '相比 Cash & Carry：雙腿都是永續，taker 費率更低；無現貨滑點；不受現貨上架與借貸額度限制。',
      },
    ],
  },
  {
    id: 'pf-mechanics',
    title: '核心機制',
    blocks: [
      {
        type: 'p',
        text: '對每個幣種，比較各所費率：在費率高的所做空（收更多 / 付更少），在費率低的所做多（付更少 / 收更多）。',
      },
      {
        type: 'formula',
        lines: [
          'spread_pct  = short_rate − long_rate（做空高費率腿、做多低費率腿）',
          'net_edge_pct = spread_pct − (long_taker + short_taker)',
          'real_edge_pct = net_edge_pct − mark_spread_pct',
        ],
      },
      {
        type: 'p',
        text: 'mark_spread_pct 是兩所標記價的相對偏差：開倉時一腿貴一腿便宜，相當於入場即承擔的價格錯配。real_edge 把它從邊際中扣掉，是最保守的可執行邊際，Scanner 預設按它排序與篩選。',
      },
    ],
  },
  {
    id: 'pf-direction',
    title: 'Forward 與 Reverse 的含義',
    blocks: [
      {
        type: 'p',
        text: '多空方向永遠是「空高費率、多低費率」。direction 標籤只描述費率所處的區域：',
      },
      {
        type: 'table',
        headers: ['direction', '條件', '典型形態'],
        rows: [
          ['forward', '至少一腿費率 ≥ 0', '空頭腿收正費率（或混合正負）'],
          ['reverse', '兩腿費率都 < 0', '多頭腿收負費率，空頭腿付得更少'],
        ],
      },
    ],
  },
  {
    id: 'pf-vs-cc',
    title: '與 Cash & Carry 對比',
    blocks: [
      {
        type: 'table',
        headers: ['', 'Cash & Carry', 'Pure Futures'],
        rows: [
          ['兩條腿', '現貨 + 永續', '永續 + 永續'],
          ['手續費', '現貨 taker 較高（~0.1%）', '雙永續 taker 較低'],
          ['跨所', 'Unified 才拆腿', '天然跨所'],
          ['借幣', '反向需要', '不需要'],
          ['DEX 參與', '不支援', 'HL / Aster / Lighter / EdgeX / dYdX（掃描）'],
          ['收益來源', '單所費率絕對值', '兩所費率之差'],
        ],
      },
    ],
  },
  {
    id: 'pf-thresholds',
    title: '閾值與過濾',
    blocks: [
      {
        type: 'table',
        headers: ['引數', '含義'],
        rows: [
          ['min_spread', '原始費率差下限（預設 0.03%）'],
          ['min_edge', '扣費後淨邊際下限（預設 0.01%）'],
          ['min_edge_1h', '雙 1h 同週期對的專用（更低）閾值'],
          ['min_edge_mismatch', '跨週期對的專用（更高）閾值'],
          ['max_mark_spread_pct', '兩所標記價差上限，超過即丟棄'],
        ],
      },
      {
        type: 'p',
        text: 'min_edge_1h 更低是因為 1h 週期資金週轉快、同週期無 timing risk；min_edge_mismatch 更高是為跨週期的結算不同步留風險溢價。上述閾值在 Settings → Strategy 配置，寫入 scripts/data/strategy_config.json；Scanner API 與 CLI runner 共用。',
      },
    ],
  },
  {
    id: 'pf-settings',
    title: 'Settings 與 CLI 配置統一',
    blocks: [
      {
        type: 'table',
        headers: ['Dashboard 欄位', 'CLI / 模板欄位'],
        rows: [
          ['min_spread_annual', 'pureFuturesArbitrage.minSpreadPct'],
          ['min_edge_annual', 'pureFuturesArbitrage.minNetEdgePct'],
          ['min_edge_1h / min_edge_mismatch', '按腿週期在 runner 內逐行應用（見跨週期文件）'],
          ['trade_usd', 'pureFuturesArbitrage.tradeUsdPerPair'],
          ['max_positions', 'pureFuturesArbitrage.maxConcurrentPairs'],
          ['scan_venues', 'pureFuturesArbitrage.venues'],
          ['scan_interval_sec', 'scanIntervalMinutes（秒÷60）'],
          ['fee_mode / venue_fee_tiers', '掃描時 fee_providers 解析'],
        ],
      },
      {
        type: 'p',
        text: 'templates/config.pure_futures.spread.json 仍保留執行細節（parallelLegs、depthCheck、dry_run 等）；閾值以 strategy_config.json 為準。合併邏輯：scripts/core/strategy_config.py → apply_strategy_to_pure_futures_cfg()。',
      },
    ],
  },
  {
    id: 'pf-cross-interval',
    title: '跨週期配對',
    blocks: [
      {
        type: 'p',
        text: '當兩腿結算週期不同（settle_mismatch，如 HL 1h vs Binance 8h），不能直接比較 rate_pct。系統先歸一化到每小時，再用 mark-index 基差按結算進度加權混合（basis blend）。',
      },
      {
        type: 'ul',
        items: [
          'spread_source = rate：同週期，直接用公佈費率',
          'spread_source = basis_blend：跨週期且有 index，使用混合模型',
          'spread_source = rate_linear：跨週期但無獨立 index（Lighter / EdgeX / dYdX 腿），線性回退',
        ],
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'dYdX indexer 目前僅暴露 oraclePrice，mark≈index，basis blend 對該腿幾乎不生效；與 CEX 8h 配對時按 rate_linear + min_edge_mismatch 更保守。',
      },
      {
        type: 'p',
        text: '完整推導、各所 index 來源與數值示例見「跨週期資金費率套利」。',
      },
    ],
  },
  {
    id: 'pf-execution',
    title: '執行與監控',
    blocks: [
      {
        type: 'ul',
        items: [
          '儀表盤：Scanner → Pure Futures 表格「開倉」，預設 dry-run；scan-only venue 按鈕禁用',
          '手動交易：pure_futures_trade.py open / list / close（預設 dry-run）',
          '自動執行：run_pure_futures_spread.py --once / --watch（合併 strategy_config.json）',
          '持倉監控：pure_futures_watcher.py；parallelLegs 預設 true，雙腿併發下單',
          '開倉前深度檢查：futures_depth.py，DEX 訂單簿拉取失敗則阻止開倉（depthCheckFailOpen=false）',
        ],
      },
      {
        type: 'callout',
        variant: 'warn',
        text: '跨週期對在執行/回測中會經 settle_mismatch_planner 疊加現金流懲罰（在 scanner 的 net_edge 之上）；planner 與 unified pool 已與掃描層共用 pair_pure_futures_spread 做 basis blend。',
      },
    ],
  },
  {
    id: 'pf-code',
    title: '程式碼地圖',
    blocks: [
      {
        type: 'table',
        headers: ['路徑', '職責'],
        rows: [
          ['scripts/cli/scan_pure_futures_spreads.py', '掃描入口（含 basis blend 呼叫）'],
          ['scripts/execution/run_pure_futures_spread.py', '自動執行 runner（scan → filter → open/close）'],
          ['scripts/execution/pure_futures_executor.py', '雙腿下單與回滾'],
          ['scripts/execution/pure_futures_watcher.py', '持倉監控'],
          ['scripts/execution/settle_mismatch_planner.py', '跨週期現金流分析（執行側）'],
          ['scripts/backtest/backtest_pure_futures_spread.py', '回測'],
          ['server/routes/scanner.py', 'API 快取、閾值過濾、費率重算'],
        ],
      },
    ],
  },
]

const en: DocSection[] = [
  {
    id: 'pf-overview',
    title: 'Overview',
    blocks: [
      {
        type: 'p',
        text: 'Pure Futures is the primary strategy: hold a perp long on one exchange and a perp short on another for the same asset, capturing the funding rate differential. No spot, no borrowing, inherently cross-venue — perp DEXs (Hyperliquid / Aster / Lighter / EdgeX; dYdX scan-only) can participate.',
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'Versus Cash & Carry: both legs are perps with lower taker fees, no spot slippage, and no dependency on spot listings or borrow quotas.',
      },
    ],
  },
  {
    id: 'pf-mechanics',
    title: 'Core mechanics',
    blocks: [
      {
        type: 'p',
        text: 'For each asset, compare rates across venues: short where the rate is higher (receive more / pay less), long where it is lower.',
      },
      {
        type: 'formula',
        lines: [
          'spread_pct  = short_rate − long_rate (short the higher-rate leg, long the lower)',
          'net_edge_pct = spread_pct − (long_taker + short_taker)',
          'real_edge_pct = net_edge_pct − mark_spread_pct',
        ],
      },
      {
        type: 'p',
        text: 'mark_spread_pct is the relative mark price gap between venues: one leg fills rich, the other cheap — a price mismatch you absorb at entry. real_edge deducts it, giving the most conservative executable edge; the Scanner sorts and filters by it by default.',
      },
    ],
  },
  {
    id: 'pf-direction',
    title: 'Forward vs Reverse',
    blocks: [
      {
        type: 'p',
        text: 'The long/short assignment is always "short the higher rate, long the lower". The direction label only describes the rate regime:',
      },
      {
        type: 'table',
        headers: ['direction', 'Condition', 'Typical shape'],
        rows: [
          ['forward', 'At least one leg rate ≥ 0', 'Short leg collects positive funding (or mixed signs)'],
          ['reverse', 'Both legs negative', 'Long leg collects negative funding; short leg pays less'],
        ],
      },
    ],
  },
  {
    id: 'pf-vs-cc',
    title: 'Versus Cash & Carry',
    blocks: [
      {
        type: 'table',
        headers: ['', 'Cash & Carry', 'Pure Futures'],
        rows: [
          ['Legs', 'Spot + perp', 'Perp + perp'],
          ['Fees', 'Spot taker is high (~0.1%)', 'Two low perp takers'],
          ['Cross-venue', 'Only via Unified', 'Inherently cross-venue'],
          ['Borrowing', 'Required for reverse', 'Not needed'],
          ['DEX participation', 'No', 'HL / Aster / Lighter / EdgeX / dYdX (scan)'],
          ['Return source', 'Absolute rate at one venue', 'Rate differential between venues'],
        ],
      },
    ],
  },
  {
    id: 'pf-thresholds',
    title: 'Thresholds and filters',
    blocks: [
      {
        type: 'table',
        headers: ['Parameter', 'Meaning'],
        rows: [
          ['min_spread', 'Minimum raw rate spread (default 0.03%)'],
          ['min_edge', 'Minimum net edge after fees (default 0.01%)'],
          ['min_edge_1h', 'Dedicated (lower) bar for both-1h pairs'],
          ['min_edge_mismatch', 'Dedicated (higher) bar for cross-interval pairs'],
          ['max_mark_spread_pct', 'Discard if the cross-venue mark gap exceeds this'],
        ],
      },
      {
        type: 'p',
        text: 'min_edge_1h is lower because hourly settlement turns capital faster with no timing risk; min_edge_mismatch is higher as a risk premium for unsynchronized settlements. Configure in Settings → Strategy (scripts/data/strategy_config.json); shared by Scanner API and CLI runners.',
      },
    ],
  },
  {
    id: 'pf-settings',
    title: 'Settings and CLI config',
    blocks: [
      {
        type: 'table',
        headers: ['Dashboard field', 'CLI / template field'],
        rows: [
          ['min_spread_annual', 'pureFuturesArbitrage.minSpreadPct'],
          ['min_edge_annual', 'pureFuturesArbitrage.minNetEdgePct'],
          ['min_edge_1h / min_edge_mismatch', 'Applied per row by interval group in runners'],
          ['trade_usd', 'pureFuturesArbitrage.tradeUsdPerPair'],
          ['max_positions', 'pureFuturesArbitrage.maxConcurrentPairs'],
          ['scan_venues', 'pureFuturesArbitrage.venues'],
          ['scan_interval_sec', 'scanIntervalMinutes (sec ÷ 60)'],
          ['fee_mode / venue_fee_tiers', 'Resolved via fee_providers at scan time'],
        ],
      },
      {
        type: 'p',
        text: 'templates/config.pure_futures.spread.json keeps execution knobs (parallelLegs, depthCheck, dry_run). Thresholds come from strategy_config.json via core/strategy_config.py → apply_strategy_to_pure_futures_cfg().',
      },
    ],
  },
  {
    id: 'pf-cross-interval',
    title: 'Cross-interval pairs',
    blocks: [
      {
        type: 'p',
        text: 'When the legs settle on different intervals (settle_mismatch, e.g. HL 1h vs Binance 8h), rate_pct is not directly comparable. The system normalizes to hourly, then blends in mark-index basis weighted by settlement progress (basis blend).',
      },
      {
        type: 'ul',
        items: [
          'spread_source = rate: same interval, published rates used directly',
          'spread_source = basis_blend: cross-interval with index available, blend model active',
          'spread_source = rate_linear: cross-interval without index (Lighter / EdgeX legs), linear fallback',
        ],
      },
      {
        type: 'p',
        text: 'Full derivation, per-venue index sources, and a numerical example: see "Cross-Interval Funding Arbitrage".',
      },
    ],
  },
  {
    id: 'pf-execution',
    title: 'Execution and monitoring',
    blocks: [
      {
        type: 'ul',
        items: [
          'Manual trading: pure_futures_trade.py open / list / close (dry-run default)',
          'Automated: run_pure_futures_spread.py --once / --watch',
          'Position monitoring: pure_futures_watcher.py tracks rates and edge, alerts on exit conditions',
          'Pre-open depth check: futures_depth.py; DEX order-book fetch failures block opens',
        ],
      },
      {
        type: 'callout',
        variant: 'warn',
        text: 'Cross-interval pairs pass through settle_mismatch_planner for an extra cash-flow penalty on top of scanner net_edge. Planner and unified pool now share pair_pure_futures_spread with the scanner for basis blend.',
      },
    ],
  },
  {
    id: 'pf-code',
    title: 'Code map',
    blocks: [
      {
        type: 'table',
        headers: ['Path', 'Role'],
        rows: [
          ['scripts/cli/scan_pure_futures_spreads.py', 'Scan entry (invokes basis blend)'],
          ['scripts/execution/run_pure_futures_spread.py', 'Automated runner (scan → filter → open/close)'],
          ['scripts/execution/pure_futures_executor.py', 'Two-leg order placement and rollback'],
          ['scripts/execution/pure_futures_watcher.py', 'Position monitoring'],
          ['scripts/execution/settle_mismatch_planner.py', 'Cross-interval cash-flow analysis (executor side)'],
          ['scripts/backtest/backtest_pure_futures_spread.py', 'Backtest'],
          ['server/routes/scanner.py', 'API cache, threshold filters, fee recalc'],
        ],
      },
    ],
  },
]

export const pureFuturesArticle: DocArticleDef = {
  slug: 'pure-futures',
  titleKey: 'docs.articles.pureFutures.title',
  descKey: 'docs.articles.pureFutures.desc',
  tagKey: 'scanner.pureFutures',
  tagType: 'success',
  sectionsByLocale: {
    'zh-CN': zhCN,
    'zh-TW': zhTW,
    en,
  },
}
