import type { DocArticleDef, DocSection } from '../types'

const zhCN: DocSection[] = [
  {
    id: 'cc-overview',
    title: '概述',
    blocks: [
      {
        type: 'p',
        text: 'Cash & Carry 在同一交易所内用现货与永续构成对冲，收取资金费。Scanner 的 Cash & Carry Tab 按交易所独立扫描，每个所分别给出正向 / 反向候选。仅支持 CEX（DEX 无现货与借贷）。',
      },
    ],
  },
  {
    id: 'cc-forward',
    title: '正向（费率 > 0）',
    blocks: [
      {
        type: 'formula',
        lines: [
          '腿 1：买入现货（等额）',
          '腿 2：永续做空（等额）',
        ],
      },
      {
        type: 'p',
        text: '费率为正时多头付空头：永续空头每个周期收取资金费，现货多头对冲价格。无借币成本。',
      },
      {
        type: 'formula',
        lines: ['net_edge_pct = rate_pct − (spot_taker + futures_taker)'],
      },
      {
        type: 'callout',
        variant: 'info',
        text: '正向的前提是该所有现货交易对（has_spot）。部分小币只有永续没有现货，会被列入 forward_no_spot。',
      },
    ],
  },
  {
    id: 'cc-reverse',
    title: '反向（费率 < 0）',
    blocks: [
      {
        type: 'formula',
        lines: [
          '腿 1：margin 借币并卖出（等额）',
          '腿 2：永续做多（等额）',
        ],
      },
      {
        type: 'p',
        text: '费率为负时空头付多头：永续多头收取资金费，借币卖出的现货空头对冲价格。但借币要付利息，须从边际中扣除。',
      },
      {
        type: 'formula',
        lines: [
          'borrow_per_period = borrow_annual_pct / (365 × 24) × interval_h',
          'net_edge_pct = |rate_pct| − borrow_per_period − (spot_taker + futures_taker)',
        ],
      },
      {
        type: 'callout',
        variant: 'warn',
        text: '反向比正向多一项持续成本：借币利息按周期累积，且利率会随市场浮动。负费率消失后若不及时平仓，利息会迅速吃掉利润。',
      },
    ],
  },
  {
    id: 'cc-constraints',
    title: '反向可行性约束',
    blocks: [
      {
        type: 'ul',
        items: [
          '币必须可借（borrowable）且有足够借贷额度（max_borrow）',
          '交易所必须实现 margin 借/还接口（supports_reverse_arbitrage）；live 模式下不支持的所会强制禁用反向',
          '借币利率过高时 net_edge ≤ 0，自动从候选中排除',
        ],
      },
      {
        type: 'p',
        text: 'Scanner 会把负费率但不可借的币单独列为 reverse_not_borrowable，仅供参考，不可执行。',
      },
    ],
  },
  {
    id: 'cc-thresholds',
    title: '入场 / 退出阈值（配置）',
    blocks: [
      {
        type: 'table',
        headers: ['参数', '含义'],
        rows: [
          ['entryFundingRatePct', '正向入场费率（如 0.05%）'],
          ['exitFundingRatePct', '正向退出费率（如 0.01%，低于即平仓）'],
          ['reverseEntryFundingRatePct', '反向入场费率（负值，如 −0.05%）'],
          ['reverseExitFundingRatePct', '反向退出费率（如 −0.01%）'],
          ['minNetEdgePct', '通用费率闸门：扣费后净边际下限'],
          ['minReverseSpreadPct', '反向额外门槛：|rate| − borrow 需超过此值'],
          ['maxMinutesToSettlement', '时间锁：距下次结算超过 N 分钟则暂不入场'],
        ],
      },
      {
        type: 'p',
        text: '多资产模式（crossAssetArbitrage）有槽位竞争：净边际更高的新机会可抢占旧仓位，但必须超出 preemptionFrictionBufferPct 的切换摩擦缓冲，避免来回倒仓被手续费磨损。',
      },
    ],
  },
  {
    id: 'cc-fields',
    title: 'Scanner 字段',
    blocks: [
      {
        type: 'table',
        headers: ['字段', '含义'],
        rows: [
          ['rate_pct', '当前周期资金费率（正=正向候选，负=反向候选）'],
          ['interval_h / annual_pct', '结算周期 / 年化'],
          ['has_spot / spot_price', '是否有现货交易对及价格（正向）'],
          ['borrowable / max_borrow', '是否可借及额度（反向）'],
          ['borrow_daily_pct / borrow_annual_pct', '借币日息 / 年息'],
          ['borrow_per_period_pct', '折算到一个结算周期的借币成本'],
          ['fee_pct', '现货 + 永续两腿 taker 之和'],
          ['net_edge_pct', '扣费（与借币成本）后净边际'],
        ],
      },
    ],
  },
  {
    id: 'cc-code',
    title: '代码地图',
    blocks: [
      {
        type: 'table',
        headers: ['路径', '职责'],
        rows: [
          ['scripts/cli/scan_funding_arbitrage.py', '按所扫描入口（正向 / 反向候选）'],
          ['scripts/strategies/futures/cash_and_carry.py', '单资产决策（委托给 cross_asset 引擎）'],
          ['scripts/strategies/futures/cross_asset_arbitrage.py', '多资产槽位竞争与抢占逻辑'],
          ['scripts/execution/run_cash_and_carry.py', '执行循环（NAV 同步、强平检查、通知）'],
          ['scripts/backtest/borrow_providers.py', '各所借币利率与可借额度'],
        ],
      },
    ],
  },
]

const en: DocSection[] = [
  {
    id: 'cc-overview',
    title: 'Overview',
    blocks: [
      {
        type: 'p',
        text: 'Cash & Carry hedges spot against perp on the same exchange to collect funding. The Cash & Carry tab scans each venue independently, producing forward / reverse candidates per venue. CEX only (DEXs have no spot or margin borrow).',
      },
    ],
  },
  {
    id: 'cc-forward',
    title: 'Forward (rate > 0)',
    blocks: [
      {
        type: 'formula',
        lines: [
          'Leg 1: buy spot (equal notional)',
          'Leg 2: short perp (equal notional)',
        ],
      },
      {
        type: 'p',
        text: 'With a positive rate, longs pay shorts: the perp short collects funding each period while the spot long hedges price. No borrow cost.',
      },
      {
        type: 'formula',
        lines: ['net_edge_pct = rate_pct − (spot_taker + futures_taker)'],
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'Forward requires a spot pair on that venue (has_spot). Perp-only listings are reported under forward_no_spot.',
      },
    ],
  },
  {
    id: 'cc-reverse',
    title: 'Reverse (rate < 0)',
    blocks: [
      {
        type: 'formula',
        lines: [
          'Leg 1: borrow on margin and sell (equal notional)',
          'Leg 2: long perp (equal notional)',
        ],
      },
      {
        type: 'p',
        text: 'With a negative rate, shorts pay longs: the perp long collects funding while the borrowed-and-sold spot hedges price. Borrow interest must be deducted from the edge.',
      },
      {
        type: 'formula',
        lines: [
          'borrow_per_period = borrow_annual_pct / (365 × 24) × interval_h',
          'net_edge_pct = |rate_pct| − borrow_per_period − (spot_taker + futures_taker)',
        ],
      },
      {
        type: 'callout',
        variant: 'warn',
        text: 'Reverse carries an ongoing cost: borrow interest accrues every period and the rate floats. If the negative funding fades and you do not exit, interest quickly eats the profit.',
      },
    ],
  },
  {
    id: 'cc-constraints',
    title: 'Reverse feasibility constraints',
    blocks: [
      {
        type: 'ul',
        items: [
          'The asset must be borrowable with sufficient quota (max_borrow)',
          'The venue must implement margin borrow/repay (supports_reverse_arbitrage); in live mode unsupported venues are force-disabled',
          'If borrow cost is too high, net_edge ≤ 0 and the candidate is excluded automatically',
        ],
      },
      {
        type: 'p',
        text: 'Negative-rate assets that cannot be borrowed are listed under reverse_not_borrowable — informational only, not executable.',
      },
    ],
  },
  {
    id: 'cc-thresholds',
    title: 'Entry / exit thresholds (config)',
    blocks: [
      {
        type: 'table',
        headers: ['Parameter', 'Meaning'],
        rows: [
          ['entryFundingRatePct', 'Forward entry rate (e.g. 0.05%)'],
          ['exitFundingRatePct', 'Forward exit rate (e.g. 0.01%; close below this)'],
          ['reverseEntryFundingRatePct', 'Reverse entry rate (negative, e.g. −0.05%)'],
          ['reverseExitFundingRatePct', 'Reverse exit rate (e.g. −0.01%)'],
          ['minNetEdgePct', 'Universal fee gate: minimum net edge after fees'],
          ['minReverseSpreadPct', 'Extra reverse bar: |rate| − borrow must exceed this'],
          ['maxMinutesToSettlement', 'Time lock: skip entry if next settlement is more than N minutes away'],
        ],
      },
      {
        type: 'p',
        text: 'Multi-asset mode (crossAssetArbitrage) runs slot contention: a higher-edge candidate can preempt an existing position, but only if it beats preemptionFrictionBufferPct — preventing churn that bleeds fees.',
      },
    ],
  },
  {
    id: 'cc-fields',
    title: 'Scanner fields',
    blocks: [
      {
        type: 'table',
        headers: ['Field', 'Meaning'],
        rows: [
          ['rate_pct', 'Current funding rate (positive = forward, negative = reverse)'],
          ['interval_h / annual_pct', 'Settlement period / annualized'],
          ['has_spot / spot_price', 'Spot pair availability and price (forward)'],
          ['borrowable / max_borrow', 'Borrowability and quota (reverse)'],
          ['borrow_daily_pct / borrow_annual_pct', 'Daily / annual borrow rate'],
          ['borrow_per_period_pct', 'Borrow cost per settlement period'],
          ['fee_pct', 'Spot + futures taker fees combined'],
          ['net_edge_pct', 'Net edge after fees (and borrow cost)'],
        ],
      },
    ],
  },
  {
    id: 'cc-code',
    title: 'Code map',
    blocks: [
      {
        type: 'table',
        headers: ['Path', 'Role'],
        rows: [
          ['scripts/cli/scan_funding_arbitrage.py', 'Per-venue scan entry (forward / reverse candidates)'],
          ['scripts/strategies/futures/cash_and_carry.py', 'Single-asset decision (delegates to cross_asset engine)'],
          ['scripts/strategies/futures/cross_asset_arbitrage.py', 'Multi-asset slot contention and preemption'],
          ['scripts/execution/run_cash_and_carry.py', 'Execution loop (NAV sync, liquidation checks, notifications)'],
          ['scripts/backtest/borrow_providers.py', 'Per-venue borrow rates and quotas'],
        ],
      },
    ],
  },
]

export const cashAndCarryArticle: DocArticleDef = {
  slug: 'cash-and-carry',
  titleKey: 'docs.articles.cashAndCarry.title',
  descKey: 'docs.articles.cashAndCarry.desc',
  tagKey: 'scanner.cashAndCarry',
  tagType: 'success',
  sectionsByLocale: {
    'zh-CN': zhCN,
    'zh-TW': zhCN,
    en,
  },
}
