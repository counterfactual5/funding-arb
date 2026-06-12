import type { DocArticleDef, DocSection } from '../types'

const zhCN: DocSection[] = [
  {
    id: 'u-overview',
    title: '概述',
    blocks: [
      {
        type: 'p',
        text: 'Unified C&C 与同所 Cash & Carry 原理相同，区别在于两条腿可以拆在不同交易所：期货腿选费率最优的所，现货 / 借币腿选成本最低的所。所有 CEX 被抽象成统一路由表，按币取全场最优组合。',
      },
      {
        type: 'callout',
        variant: 'info',
        text: '同所费率不极端但「A 所费率高 + B 所现货费用低」的组合常常存在 —— Unified 的机会往往多于单所 C&C。仅支持 CEX。',
      },
    ],
  },
  {
    id: 'u-forward',
    title: '正向路由',
    blocks: [
      {
        type: 'ul',
        items: [
          '期货腿：在所有所中选「费率最高」且 ≥ entry 阈值的所开空',
          '现货腿：在有现货的所中选「现货手续费最低」的所买入',
        ],
      },
      {
        type: 'formula',
        lines: ['net_edge_pct = funding_rate_pct − futures_fee − spot_fee'],
      },
      {
        type: 'p',
        text: '两腿可以同所（same_venue = true），此时退化为普通 C&C，无转账成本。',
      },
    ],
  },
  {
    id: 'u-reverse',
    title: '反向路由',
    blocks: [
      {
        type: 'ul',
        items: [
          '期货腿：选「费率最负」的所开多',
          '借币腿：在可借（borrowable）且支持反向执行的所中，选「单周期借币成本最低」的所借币卖出',
        ],
      },
      {
        type: 'formula',
        lines: [
          'borrow_per_period = 按期货腿 interval_h 折算的借币成本',
          'net_edge_pct = |funding_rate_pct| − borrow_per_period − futures_fee − spot_fee',
        ],
      },
    ],
  },
  {
    id: 'u-transfer',
    title: '跨所转账成本',
    blocks: [
      {
        type: 'p',
        text: '两腿不同所时，资金需要跨所调度。系统按转账链路计提链上转账费，得到全成本边际：',
      },
      {
        type: 'formula',
        lines: ['net_edge_all_in_pct = net_edge_pct − transfer_fee_pct'],
      },
      {
        type: 'ul',
        items: [
          '跨所路由按 net_edge_all_in_pct 排序（含转账费）',
          '同所路由按 net_edge_pct 排序（无转账费）',
          'transfer_chain 字段记录建议的转账链路（如 TRC20 / BEP20）',
        ],
      },
      {
        type: 'callout',
        variant: 'warn',
        text: '转账费是一次性成本，持仓时间越长摊薄越多。短持仓 + 跨所小额时，转账费可能吃掉全部边际，留意 all-in 与 net 的差值。',
      },
    ],
  },
  {
    id: 'u-fields',
    title: 'Scanner 字段',
    blocks: [
      {
        type: 'table',
        headers: ['字段', '含义'],
        rows: [
          ['direction', 'forward / reverse'],
          ['futures_venue / spot_venue', '期货腿 / 现货（借币）腿所在所'],
          ['funding_rate_pct / interval_h', '期货腿费率与周期'],
          ['borrow_per_period_pct', '单周期借币成本（反向）'],
          ['futures_fee_pct / spot_fee_pct', '两腿 taker 费率'],
          ['net_edge_pct', '扣费后净边际（不含转账）'],
          ['net_edge_all_in_pct', '再扣转账费的全成本边际'],
          ['transfer_chain / transfer_fee_pct', '转账链路与费用'],
          ['same_venue', '两腿是否同所'],
        ],
      },
    ],
  },
  {
    id: 'u-code',
    title: '代码地图',
    blocks: [
      {
        type: 'table',
        headers: ['路径', '职责'],
        rows: [
          ['scripts/backtest/unified_funding_pool.py', '核心路由：best_forward / best_reverse / scan_routes'],
          ['scripts/cli/scan_unified_funding.py', 'CLI 扫描入口'],
          ['scripts/backtest/borrow_providers.py', '借币利率与反向可执行性'],
          ['server/routes/scanner.py', 'Unified 缓存与费率重算（_recalc_unified_fees）'],
        ],
      },
    ],
  },
]

const en: DocSection[] = [
  {
    id: 'u-overview',
    title: 'Overview',
    blocks: [
      {
        type: 'p',
        text: 'Unified C&C uses the same principle as same-venue Cash & Carry, but the two legs can be split across exchanges: the futures leg goes where the rate is best, the spot / borrow leg where the cost is lowest. All CEXs are abstracted into one routing table, picking the globally best combination per asset.',
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'Even when no single venue has an extreme rate, "high rate at A + cheap spot at B" combinations often exist — Unified typically finds more opportunities than single-venue C&C. CEX only.',
      },
    ],
  },
  {
    id: 'u-forward',
    title: 'Forward routing',
    blocks: [
      {
        type: 'ul',
        items: [
          'Futures leg: short at the venue with the highest rate ≥ entry threshold',
          'Spot leg: buy at the venue with the lowest spot fee among venues that list the spot pair',
        ],
      },
      {
        type: 'formula',
        lines: ['net_edge_pct = funding_rate_pct − futures_fee − spot_fee'],
      },
      {
        type: 'p',
        text: 'Both legs may land on the same venue (same_venue = true), reducing to plain C&C with no transfer cost.',
      },
    ],
  },
  {
    id: 'u-reverse',
    title: 'Reverse routing',
    blocks: [
      {
        type: 'ul',
        items: [
          'Futures leg: long at the venue with the most negative rate',
          'Borrow leg: among venues that are borrowable and reverse-executable, borrow-sell where the per-period borrow cost is lowest',
        ],
      },
      {
        type: 'formula',
        lines: [
          'borrow_per_period = borrow cost normalized to the futures leg interval_h',
          'net_edge_pct = |funding_rate_pct| − borrow_per_period − futures_fee − spot_fee',
        ],
      },
    ],
  },
  {
    id: 'u-transfer',
    title: 'Cross-venue transfer cost',
    blocks: [
      {
        type: 'p',
        text: 'When legs sit on different venues, capital must move across exchanges. The system prices the on-chain transfer per route, producing an all-in edge:',
      },
      {
        type: 'formula',
        lines: ['net_edge_all_in_pct = net_edge_pct − transfer_fee_pct'],
      },
      {
        type: 'ul',
        items: [
          'Cross-venue routes sort by net_edge_all_in_pct (transfer included)',
          'Same-venue routes sort by net_edge_pct (no transfer)',
          'transfer_chain records the suggested chain (e.g. TRC20 / BEP20)',
        ],
      },
      {
        type: 'callout',
        variant: 'warn',
        text: 'The transfer fee is one-off and amortizes over the holding period. For short holds with small size, it can consume the entire edge — watch the gap between all-in and net.',
      },
    ],
  },
  {
    id: 'u-fields',
    title: 'Scanner fields',
    blocks: [
      {
        type: 'table',
        headers: ['Field', 'Meaning'],
        rows: [
          ['direction', 'forward / reverse'],
          ['futures_venue / spot_venue', 'Venue of the futures / spot (borrow) leg'],
          ['funding_rate_pct / interval_h', 'Futures leg rate and period'],
          ['borrow_per_period_pct', 'Per-period borrow cost (reverse)'],
          ['futures_fee_pct / spot_fee_pct', 'Taker fees per leg'],
          ['net_edge_pct', 'Net edge after fees (excluding transfer)'],
          ['net_edge_all_in_pct', 'All-in edge after transfer fee'],
          ['transfer_chain / transfer_fee_pct', 'Transfer chain and cost'],
          ['same_venue', 'Whether both legs share a venue'],
        ],
      },
    ],
  },
  {
    id: 'u-code',
    title: 'Code map',
    blocks: [
      {
        type: 'table',
        headers: ['Path', 'Role'],
        rows: [
          ['scripts/backtest/unified_funding_pool.py', 'Core routing: best_forward / best_reverse / scan_routes'],
          ['scripts/cli/scan_unified_funding.py', 'CLI scan entry'],
          ['scripts/backtest/borrow_providers.py', 'Borrow rates and reverse executability'],
          ['server/routes/scanner.py', 'Unified cache and fee recalculation (_recalc_unified_fees)'],
        ],
      },
    ],
  },
]

export const unifiedCarryArticle: DocArticleDef = {
  slug: 'unified-carry',
  titleKey: 'docs.articles.unifiedCarry.title',
  descKey: 'docs.articles.unifiedCarry.desc',
  tagKey: 'scanner.unifiedCC',
  tagType: 'success',
  sectionsByLocale: {
    'zh-CN': zhCN,
    'zh-TW': zhCN,
    en,
  },
}
