import type { DocArticleDef, DocSection } from '../types'

const zhCN: DocSection[] = [
  {
    id: 'ci-background',
    title: '问题背景',
    blocks: [
      {
        type: 'p',
        text: '各交易所公布的 rate_pct 是当前结算周期内的费率，周期长度不同：',
      },
      {
        type: 'table',
        headers: ['交易所', '典型周期', '含义'],
        rows: [
          ['Binance / OKX / Bybit', '8h', '每 8 小时结算一次'],
          ['Bitget', '2h 或 8h', '部分合约 2h'],
          ['Hyperliquid', '1h', '每小时结算'],
        ],
      },
      {
        type: 'p',
        text: '若简单做 spread_naive = short_rate_pct - long_rate_pct，会把 1h 的 0.01% 与 8h 的 0.05% 放在同一量级比较，严重失真。',
      },
    ],
  },
  {
    id: 'ci-linear-problem',
    title: '为什么不能只做线性外推',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# 朴素归一化',
          'rate_hourly = rate_pct / interval_h',
          'spread = (short_hourly - long_hourly) × min(interval_long, interval_short)',
        ],
      },
      {
        type: 'p',
        text: '在周期刚结算完时合理（基差已收敛，rate_pct 反映新周期起点）。但在周期中途，premium（mark 相对 index 的偏离）会持续累积，下一期实际 funding 往往更接近基差隐含费率。',
      },
    ],
  },
  {
    id: 'ci-model-goal',
    title: '模型目标',
    blocks: [
      {
        type: 'ul',
        items: [
          '将两边费率统一到每小时基准',
          '用 mark-index 基差估计「本周期剩余时间内的预期 funding」',
          '按结算进度在「已公布 rate」与「基差隐含 rate」之间加权混合',
          '输出可解释字段（spread_source、settle_progress、basis_pct）',
        ],
      },
    ],
  },
  {
    id: 'ci-when',
    title: '何时启用跨周期模型',
    blocks: [
      {
        type: 'formula',
        lines: ['is_mismatch = |long_interval_h − short_interval_h| > 0.5'],
      },
      {
        type: 'ul',
        items: [
          'is_mismatch == false → 同周期，直接用 rate_pct / interval_h，spread_source = rate',
          'is_mismatch == true → 启用 basis blend（有 index）或线性回退（无 index）',
        ],
      },
    ],
  },
  {
    id: 'ci-data-deps',
    title: '数据依赖',
    blocks: [
      {
        type: 'table',
        headers: ['字段', '说明'],
        rows: [
          ['rate_pct', '当前待结算资金费率（%）'],
          ['interval_h', '结算周期（小时）'],
          ['mark_price', '标记价格'],
          ['index_price', '指数 / 预言机价格'],
          ['next_funding_ts', '下次结算时间（ms）'],
          ['last_settle_ts', '上次结算时间（ms），可由 next - interval 推导'],
        ],
      },
      {
        type: 'table',
        headers: ['交易所', 'index_price 来源', '跨周期 basis blend'],
        rows: [
          ['Binance', 'premiumIndex.indexPrice', '✅'],
          ['Bitget', 'indexPrice', '✅'],
          ['Bybit', 'indexPrice', '✅'],
          ['OKX', 'idxPx（mark-price 接口）', '✅'],
          ['Hyperliquid', 'oraclePx', '✅'],
          ['Aster', '继承 Binance provider', '✅'],
          ['Lighter', '无公开 index → 0', '❌ 回退 rate_linear'],
          ['EdgeX', '无公开 index → 0', '❌ 回退 rate_linear'],
        ],
      },
    ],
  },
  {
    id: 'ci-progress',
    title: '结算进度 progress',
    blocks: [
      {
        type: 'formula',
        lines: [
          'progress = elapsed / period_length   ∈ [0, 1]',
          '',
          '# 计算优先级：',
          '1. 有 last_settle_ts 与 next_funding_ts: (now − last) / (next − last)',
          '2. 仅有 next_funding_ts: 用剩余时间反推',
          '3. 皆无: 回退 0.5',
        ],
      },
      {
        type: 'ul',
        items: [
          'progress ≈ 0：刚结算完，更信任已公布的 rate_pct',
          'progress ≈ 1：即将结算，更信任 mark-index 基差隐含的下期费率',
        ],
      },
    ],
  },
  {
    id: 'ci-basis',
    title: '基差 basis_pct',
    blocks: [
      {
        type: 'formula',
        lines: ['basis_pct = (mark_price − index_price) / index_price × 100%'],
      },
      {
        type: 'p',
        text: '按交易所对单周期溢价封顶（VENUE_BASIS_CAP_PCT），避免极端 mark-index 差制造虚假大边际：',
      },
      {
        type: 'table',
        headers: ['类型', '单周期 cap', '说明'],
        rows: [
          ['Binance / Bybit / Bitget / OKX / Aster / EdgeX', '±0.30%', '约为典型 funding clamp 的 3 倍，过滤极端噪声'],
          ['Hyperliquid / Lighter', '±0.50%', '无硬顶 EMA premium，放宽 cap'],
          ['未知 venue', '±0.50%', 'DEFAULT_BASIS_CAP_PCT'],
        ],
      },
    ],
  },
  {
    id: 'ci-blend',
    title: '混合 hourly 与 spread',
    blocks: [
      {
        type: 'formula',
        lines: [
          'rate_hourly  = rate_pct / interval_h',
          'basis_hourly = basis_pct / interval_h',
          'blended_hourly = (1 − progress) × rate_hourly + progress × basis_hourly',
        ],
      },
      {
        type: 'formula',
        lines: [
          'eff_interval = min(long_interval_h, short_interval_h)',
          'spread_pct   = (short_blended − long_blended) × eff_interval',
          'net_edge_pct = spread_pct − fee_pct（双边开仓 taker）',
          'real_edge_pct = net_edge_pct − mark_spread_pct',
        ],
      },
    ],
  },
  {
    id: 'ci-flow',
    title: '流程图',
    blocks: [
      {
        type: 'p',
        text: '拉取各所 rate / mark / index / 结算时间 → 判断 interval 差 > 0.5h → 计算进度与基差 → 有 index 则 basis_blend，否则 rate_linear → 合成 spread → net_edge = spread − fees → mark_spread 过滤 + min_edge 阈值。',
      },
    ],
  },
  {
    id: 'ci-fields',
    title: '扫描输出字段',
    blocks: [
      {
        type: 'table',
        headers: ['字段', '说明'],
        rows: [
          ['settle_mismatch', '是否跨周期'],
          ['same_interval', 'not settle_mismatch'],
          ['long_interval_h / short_interval_h', '各腿结算周期'],
          ['spread_source', 'rate / basis_blend / rate_linear'],
          ['long_basis_pct / short_basis_pct', '各腿 mark-index 溢价（%）'],
          ['long_settle_progress / short_settle_progress', '各腿混合权重（= progress）'],
          ['spread_pct', '混合后的周期 spread（%）'],
          ['net_edge_pct', '扣费后净边际（%）'],
          ['mark_spread_pct', '两所标记价差（%）'],
        ],
      },
    ],
  },
  {
    id: 'ci-risk',
    title: '风控与配置叠加',
    blocks: [
      {
        type: 'ul',
        items: [
          'min_edge_mismatch：跨周期对可要求更高 net_edge_pct（Settings 可配）',
          'min_edge_1h：双 1h 同周期可用更低阈值',
          'max_mark_spread_pct：两所 mark 价差超阈值则丢弃',
          'settle_mismatch_planner：执行侧将两腿线性归一化到 8h 窗口，分析现金流不对称',
          'VIP 费率策略影响 net_edge / real_edge 中的 fee_pct',
        ],
      },
    ],
  },
  {
    id: 'ci-code-map',
    title: '代码地图',
    blocks: [
      {
        type: 'table',
        headers: ['路径', '职责'],
        rows: [
          ['scripts/core/cross_interval_funding.py', '混合模型纯函数（可单测）'],
          ['scripts/cli/scan_pure_futures_spreads.py', '扫描入口，调用混合模型'],
          ['scripts/tests/test_cross_interval_funding.py', '模型单测'],
          ['scripts/execution/settle_mismatch_planner.py', '执行侧现金流 / 8h 归一化分析'],
          ['server/routes/scanner.py', 'API 缓存、min_edge_mismatch 过滤'],
          ['web/src/views/Scanner.vue', '展示 settle_mismatch、Cross 筛选、real edge'],
        ],
      },
    ],
  },
  {
    id: 'ci-example',
    title: '数值示例',
    blocks: [
      {
        type: 'p',
        text: '场景：BTC，Hyperliquid vs Binance，跨周期。',
      },
      {
        type: 'table',
        headers: ['腿', 'rate_pct', 'interval_h', 'basis_pct', 'progress'],
        rows: [
          ['Short @ HL', '0.04', '1', '+0.30%', '0.85'],
          ['Long @ Binance', '0.08', '8', '+0.05%', '0.25'],
        ],
      },
      {
        type: 'formula',
        lines: [
          '# HL 腿',
          'rate_hourly  = 0.04 / 1 = 0.04',
          'basis_hourly = 0.30 / 1 = 0.30',
          'blended      = 0.15×0.04 + 0.85×0.30 ≈ 0.261 %/h',
          '',
          '# Binance 腿',
          'rate_hourly  = 0.08 / 8 = 0.01',
          'basis_hourly = 0.05 / 8 = 0.00625',
          'blended      = 0.75×0.01 + 0.25×0.00625 ≈ 0.0094 %/h',
          '',
          '# Spread (eff_interval = 1h)',
          'spread_pct ≈ (0.261 − 0.0094) × 1 ≈ 0.252%',
          'net_edge ≈ 0.252 − 0.11 = 0.14%',
        ],
      },
      {
        type: 'callout',
        variant: 'info',
        text: '若用朴素线性外推，HL 仅 0.04%/h，spread 会低估 HL 作为 short 腿的优势。',
      },
    ],
  },
  {
    id: 'ci-limits',
    title: '已知限制',
    blocks: [
      {
        type: 'table',
        headers: ['项', '说明'],
        rows: [
          ['Planner / 回测未统一', 'settle_mismatch_planner、unified_funding_pool 仍用线性 rate/interval'],
          ['全局 basis 封顶', '固定 ±1%/周期，未按交易所真实 premium clamp 细分'],
          ['无 index 的 DEX', 'Lighter、EdgeX 跨周期只能 rate_linear'],
          ['历史 JSONL', '旧快照若无 index_price / progress 字段，回放无法复现混合模型'],
        ],
      },
    ],
  },
]

const en: DocSection[] = [
  {
    id: 'ci-background',
    title: 'Background',
    blocks: [
      {
        type: 'p',
        text: 'Each exchange publishes rate_pct for its own settlement period. Periods vary:',
      },
      {
        type: 'table',
        headers: ['Exchange', 'Typical period', 'Meaning'],
        rows: [
          ['Binance / OKX / Bybit', '8h', 'Settles every 8 hours'],
          ['Bitget', '2h or 8h', 'Some contracts 2h'],
          ['Hyperliquid', '1h', 'Hourly settlement'],
        ],
      },
      {
        type: 'p',
        text: 'Comparing 0.01% (1h) vs 0.05% (8h) directly would be severely distorted.',
      },
    ],
  },
  {
    id: 'ci-linear-problem',
    title: 'Why linear extrapolation is not enough',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# Naive normalization',
          'rate_hourly = rate_pct / interval_h',
          'spread = (short_hourly - long_hourly) × min(interval_long, interval_short)',
        ],
      },
      {
        type: 'p',
        text: 'Fine right after settlement (basis converged). But mid-period, premium (mark vs index) accumulates; next-period funding is often closer to the basis-implied rate than the published rate_pct.',
      },
    ],
  },
  {
    id: 'ci-model-goal',
    title: 'Model goals',
    blocks: [
      {
        type: 'ul',
        items: [
          'Normalize both sides to an hourly basis',
          'Use mark-index basis to estimate expected funding for the remainder of the period',
          'Weighted blend of published rate and basis-implied rate by settlement progress',
          'Output interpretable fields: spread_source, settle_progress, basis_pct',
        ],
      },
    ],
  },
  {
    id: 'ci-when',
    title: 'When the model applies',
    blocks: [
      {
        type: 'formula',
        lines: ['is_mismatch = |long_interval_h − short_interval_h| > 0.5'],
      },
      {
        type: 'ul',
        items: [
          'is_mismatch == false → same interval: rate_pct / interval_h, spread_source = rate',
          'is_mismatch == true → basis blend (with index) or linear fallback (without index)',
        ],
      },
    ],
  },
  {
    id: 'ci-data-deps',
    title: 'Data dependencies',
    blocks: [
      {
        type: 'table',
        headers: ['Field', 'Description'],
        rows: [
          ['rate_pct', 'Pending funding rate (%)'],
          ['interval_h', 'Settlement period (hours)'],
          ['mark_price', 'Mark price'],
          ['index_price', 'Index / oracle price'],
          ['next_funding_ts', 'Next settlement time (ms)'],
          ['last_settle_ts', 'Last settlement time (ms), derivable from next − interval'],
        ],
      },
      {
        type: 'table',
        headers: ['Exchange', 'index_price source', 'Basis blend'],
        rows: [
          ['Binance', 'premiumIndex.indexPrice', '✅'],
          ['Bitget', 'indexPrice', '✅'],
          ['Bybit', 'indexPrice', '✅'],
          ['OKX', 'idxPx', '✅'],
          ['Hyperliquid', 'oraclePx', '✅'],
          ['Aster', 'Inherits Binance provider', '✅'],
          ['Lighter', 'No public index → 0', '❌ rate_linear'],
          ['EdgeX', 'No public index → 0', '❌ rate_linear'],
        ],
      },
    ],
  },
  {
    id: 'ci-progress',
    title: 'Settlement progress',
    blocks: [
      {
        type: 'formula',
        lines: [
          'progress = elapsed / period_length   ∈ [0, 1]',
          '',
          '# Priority:',
          '1. Both timestamps: (now − last) / (next − last)',
          '2. Only next_funding_ts: infer from time remaining',
          '3. None: fallback 0.5',
        ],
      },
    ],
  },
  {
    id: 'ci-basis',
    title: 'Basis premium',
    blocks: [
      {
        type: 'formula',
        lines: ['basis_pct = (mark_price − index_price) / index_price × 100%'],
      },
      {
        type: 'table',
        headers: ['Type', 'Cap per period', 'Notes'],
        rows: [
          ['Binance / Bybit / Bitget / OKX / Aster / EdgeX', '±0.30%', '~3× typical funding clamp'],
          ['Hyperliquid / Lighter', '±0.50%', 'No hard EMA premium cap'],
          ['Unknown', '±0.50%', 'DEFAULT_BASIS_CAP_PCT'],
        ],
      },
    ],
  },
  {
    id: 'ci-blend',
    title: 'Blended hourly rate & edge',
    blocks: [
      {
        type: 'formula',
        lines: [
          'rate_hourly  = rate_pct / interval_h',
          'basis_hourly = basis_pct / interval_h',
          'blended_hourly = (1 − progress) × rate_hourly + progress × basis_hourly',
        ],
      },
      {
        type: 'formula',
        lines: [
          'eff_interval = min(long_interval_h, short_interval_h)',
          'spread_pct   = (short_blended − long_blended) × eff_interval',
          'net_edge_pct = spread_pct − fee_pct (open-leg taker both sides)',
          'real_edge_pct = net_edge_pct − mark_spread_pct',
        ],
      },
    ],
  },
  {
    id: 'ci-flow',
    title: 'Flow',
    blocks: [
      {
        type: 'p',
        text: 'Fetch rate / mark / index / timestamps → check interval gap > 0.5h → compute progress & basis → if index: basis_blend, else: rate_linear → synthesize spread → net_edge = spread − fees → mark_spread filter + min_edge threshold.',
      },
    ],
  },
  {
    id: 'ci-fields',
    title: 'Scanner output fields',
    blocks: [
      {
        type: 'table',
        headers: ['Field', 'Description'],
        rows: [
          ['settle_mismatch', 'Cross-interval flag'],
          ['same_interval', 'not settle_mismatch'],
          ['long_interval_h / short_interval_h', 'Per-leg settlement period'],
          ['spread_source', 'rate / basis_blend / rate_linear'],
          ['long_basis_pct / short_basis_pct', 'Per-leg mark-index premium (%)'],
          ['long_settle_progress / short_settle_progress', 'Blend weight (= progress)'],
          ['spread_pct', 'Blended spread (%)'],
          ['net_edge_pct', 'Edge after fees (%)'],
          ['mark_spread_pct', 'Mark price gap (%)'],
        ],
      },
    ],
  },
  {
    id: 'ci-risk',
    title: 'Risk overlays',
    blocks: [
      {
        type: 'ul',
        items: [
          'min_edge_mismatch: higher bar for cross-interval pairs (Settings)',
          'min_edge_1h: lower bar when both legs settle hourly',
          'max_mark_spread_pct: discard if cross-venue mark gap exceeds threshold',
          'settle_mismatch_planner: executor normalizes to 8h window, analyzes cash-flow asymmetry',
          'VIP fee policy affects fee_pct in net_edge / real_edge',
        ],
      },
    ],
  },
  {
    id: 'ci-code-map',
    title: 'Code map',
    blocks: [
      {
        type: 'table',
        headers: ['Path', 'Role'],
        rows: [
          ['scripts/core/cross_interval_funding.py', 'Pure blend functions (unit-testable)'],
          ['scripts/cli/scan_pure_futures_spreads.py', 'Scan entry, invokes blend model'],
          ['scripts/tests/test_cross_interval_funding.py', 'Model unit tests'],
          ['scripts/execution/settle_mismatch_planner.py', 'Executor cash-flow / 8h normalization'],
          ['server/routes/scanner.py', 'API cache, min_edge_mismatch filter'],
          ['web/src/views/Scanner.vue', 'UI: settle_mismatch, Cross filter, real edge'],
        ],
      },
    ],
  },
  {
    id: 'ci-example',
    title: 'Numerical example',
    blocks: [
      {
        type: 'p',
        text: 'Scenario: BTC, Hyperliquid vs Binance, cross-interval.',
      },
      {
        type: 'table',
        headers: ['Leg', 'rate_pct', 'interval_h', 'basis_pct', 'progress'],
        rows: [
          ['Short @ HL', '0.04', '1', '+0.30%', '0.85'],
          ['Long @ Binance', '0.08', '8', '+0.05%', '0.25'],
        ],
      },
      {
        type: 'formula',
        lines: [
          '# HL leg',
          'rate_hourly  = 0.04 / 1 = 0.04',
          'basis_hourly = 0.30 / 1 = 0.30',
          'blended      = 0.15×0.04 + 0.85×0.30 ≈ 0.261 %/h',
          '',
          '# Binance leg',
          'rate_hourly  = 0.08 / 8 = 0.01',
          'basis_hourly = 0.05 / 8 = 0.00625',
          'blended      = 0.75×0.01 + 0.25×0.00625 ≈ 0.0094 %/h',
          '',
          '# Spread (eff_interval = 1h)',
          'spread_pct ≈ (0.261 − 0.0094) × 1 ≈ 0.252%',
          'net_edge ≈ 0.252 − 0.11 = 0.14%',
        ],
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'With naive linear extrapolation, HL would be only 0.04%/h, underestimating its advantage as the short leg.',
      },
    ],
  },
  {
    id: 'ci-limits',
    title: 'Known limitations',
    blocks: [
      {
        type: 'table',
        headers: ['Item', 'Description'],
        rows: [
          ['Planner / backtest divergence', 'settle_mismatch_planner and unified pool still use linear rate'],
          ['Global basis cap', 'Fixed ±1%/period, not per-exchange premium clamp'],
          ['No-index DEXs', 'Lighter, EdgeX can only use rate_linear'],
          ['Legacy JSONL', 'Old snapshots without index_price / progress cannot replay blend model'],
        ],
      },
    ],
  },
]

export const crossIntervalArticle: DocArticleDef = {
  slug: 'cross-interval',
  titleKey: 'docs.articles.crossInterval.title',
  descKey: 'docs.articles.crossInterval.desc',
  tagKey: 'scanner.pureFutures',
  tagType: 'success',
  sectionsByLocale: {
    'zh-CN': zhCN,
    'zh-TW': zhCN,
    en,
  },
}
