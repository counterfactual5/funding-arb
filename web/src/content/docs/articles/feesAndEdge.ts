import type { DocArticleDef, DocSection } from '../types'

const zhCN: DocSection[] = [
  {
    id: 'fe-overview',
    title: '概述',
    blocks: [
      {
        type: 'p',
        text: '资金费套利的毛利往往只有千分之几，手续费直接决定一笔机会是否真实可做。Scanner 展示的所有边际都已扣除开仓 taker 费；本篇解释费率从哪来、各类边际字段的区别。',
      },
    ],
  },
  {
    id: 'fe-modes',
    title: '费率模式（fee_mode）',
    blocks: [
      {
        type: 'table',
        headers: ['模式', '行为'],
        rows: [
          ['auto', '已配置 API key 的所从账户 API 读真实费率；未配置的所按 VIP 档位表估算'],
          ['tier', '全部用静态 VIP 档位表（scripts/core/vip_fee_tiers.py）'],
          ['manual', '用策略配置中的手动覆盖值'],
        ],
      },
      {
        type: 'p',
        text: '在 Settings → 交易手续费 中配置 fee_mode 与各所 VIP 档位（venue_fee_tiers）。已用 API 读取的所会标记「已用 API」，档位选择对其无效。',
      },
    ],
  },
  {
    id: 'fe-spot-futures',
    title: '现货费与永续费',
    blocks: [
      {
        type: 'p',
        text: '现货 taker（典型 0.1%）通常远高于永续 taker（约 0.02% ~ 0.06%）。这是 Pure Futures 相对 C&C 的结构性优势之一。',
      },
      {
        type: 'table',
        headers: ['策略', '开仓费组成'],
        rows: [
          ['Cash & Carry / Unified', 'spot_taker + futures_taker'],
          ['Pure Futures', 'long_futures_taker + short_futures_taker'],
        ],
      },
      {
        type: 'callout',
        variant: 'warn',
        text: 'Scanner 的 net_edge 只扣开仓费。完整一轮（开 + 平）是两倍：round_trip_fee_pct = fee_pct × 2。判断持仓多久回本时要按 round-trip 算。',
      },
    ],
  },
  {
    id: 'fe-vip',
    title: 'VIP 档位的影响',
    blocks: [
      {
        type: 'p',
        text: 'VIP 等级越高 taker 越低，直接放大 net_edge / real_edge。同一笔费率差，VIP0 可能为负边际，VIP 高档则为正——费率配置错误会让 Scanner 整页机会失真。',
      },
      {
        type: 'ul',
        items: [
          '档位表来源：各所官网公开费率表，维护于 vip_fee_tiers.py',
          '设置入口：Settings → 交易手续费 → 各所 VIP 档位',
          '有 API key 时优先用账户真实费率（包含返佣后的实际值）',
        ],
      },
    ],
  },
  {
    id: 'fe-edges',
    title: '各类边际字段',
    blocks: [
      {
        type: 'table',
        headers: ['字段', '定义', '适用'],
        rows: [
          ['spread_pct', '毛费率差（或单所费率）', '全部'],
          ['fee_pct', '双腿开仓 taker 之和', '全部'],
          ['net_edge_pct', 'spread − fee（反向再扣借币）', '全部'],
          ['mark_spread_pct', '两所标记价相对偏差', 'Pure Futures'],
          ['real_edge_pct', 'net_edge − mark_spread', 'Pure Futures（默认排序）'],
          ['net_edge_all_in_pct', 'net_edge − 跨所转账费', 'Unified 跨所路由'],
          ['annual_apy_pct', '按结算周期年化的净边际', '全部'],
        ],
      },
      {
        type: 'p',
        text: '保守程度：net_edge < real_edge（Pure Futures）/ net_edge_all_in（Unified）。看到大 net_edge 先看 real / all-in 是否同样成立。',
      },
    ],
  },
  {
    id: 'fe-recalc',
    title: '改费率后重算',
    blocks: [
      {
        type: 'p',
        text: '修改 fee_mode 或 VIP 档位后，不需要重新扫描：POST /api/scanner/recalc-fees 会直接用新费率重算缓存中所有机会的 net_edge / real_edge，并通过 WebSocket 推送前端。',
      },
      {
        type: 'ul',
        items: [
          'Settings 页「保存并重算净收益」按钮即调用该接口',
          '重算覆盖 pure / carry / unified 三类缓存',
          '费率解析入口：scripts/core/fee_providers.py 的 resolve_venue_fee / parse_fee_policy',
        ],
      },
    ],
  },
]

const zhTW: DocSection[] = [
  {
    id: 'fe-overview',
    title: '概述',
    blocks: [
      {
        type: 'p',
        text: '資金費套利的毛利往往只有千分之幾，手續費直接決定一筆機會是否真實可做。Scanner 展示的所有邊際都已扣除開倉 taker 費；本篇解釋費率從哪來、各類邊際欄位的區別。',
      },
    ],
  },
  {
    id: 'fe-modes',
    title: '費率模式（fee_mode）',
    blocks: [
      {
        type: 'table',
        headers: ['模式', '行為'],
        rows: [
          ['auto', '已配置 API key 的所從賬戶 API 讀真實費率；未配置的所按 VIP 檔位表估算'],
          ['tier', '全部用靜態 VIP 檔位表（scripts/core/vip_fee_tiers.py）'],
          ['manual', '用策略配置中的手動覆蓋值'],
        ],
      },
      {
        type: 'p',
        text: '在 Settings → 交易手續費 中配置 fee_mode 與各所 VIP 檔位（venue_fee_tiers）。已用 API 讀取的所會標記「已用 API」，檔位選擇對其無效。',
      },
    ],
  },
  {
    id: 'fe-spot-futures',
    title: '現貨費與永續費',
    blocks: [
      {
        type: 'p',
        text: '現貨 taker（典型 0.1%）通常遠高於永續 taker（約 0.02% ~ 0.06%）。這是 Pure Futures 相對 C&C 的結構性優勢之一。',
      },
      {
        type: 'table',
        headers: ['策略', '開倉費組成'],
        rows: [
          ['Cash & Carry / Unified', 'spot_taker + futures_taker'],
          ['Pure Futures', 'long_futures_taker + short_futures_taker'],
        ],
      },
      {
        type: 'callout',
        variant: 'warn',
        text: 'Scanner 的 net_edge 只扣開倉費。完整一輪（開 + 平）是兩倍：round_trip_fee_pct = fee_pct × 2。判斷持倉多久回本時要按 round-trip 算。',
      },
    ],
  },
  {
    id: 'fe-vip',
    title: 'VIP 檔位的影響',
    blocks: [
      {
        type: 'p',
        text: 'VIP 等級越高 taker 越低，直接放大 net_edge / real_edge。同一筆費率差，VIP0 可能為負邊際，VIP 高檔則為正——費率配置錯誤會讓 Scanner 整頁機會失真。',
      },
      {
        type: 'ul',
        items: [
          '檔位表來源：各所官網公開費率表，維護於 vip_fee_tiers.py',
          '設定入口：Settings → 交易手續費 → 各所 VIP 檔位',
          '有 API key 時優先用賬戶真實費率（包含返傭後的實際值）',
        ],
      },
    ],
  },
  {
    id: 'fe-edges',
    title: '各類邊際欄位',
    blocks: [
      {
        type: 'table',
        headers: ['欄位', '定義', '適用'],
        rows: [
          ['spread_pct', '毛費率差（或單所費率）', '全部'],
          ['fee_pct', '雙腿開倉 taker 之和', '全部'],
          ['net_edge_pct', 'spread − fee（反向再扣借幣）', '全部'],
          ['mark_spread_pct', '兩所標記價相對偏差', 'Pure Futures'],
          ['real_edge_pct', 'net_edge − mark_spread', 'Pure Futures（預設排序）'],
          ['net_edge_all_in_pct', 'net_edge − 跨所轉賬費', 'Unified 跨所路由'],
          ['annual_apy_pct', '按結算週期年化的淨邊際', '全部'],
        ],
      },
      {
        type: 'p',
        text: '保守程度：net_edge < real_edge（Pure Futures）/ net_edge_all_in（Unified）。看到大 net_edge 先看 real / all-in 是否同樣成立。',
      },
    ],
  },
  {
    id: 'fe-recalc',
    title: '改費率後重算',
    blocks: [
      {
        type: 'p',
        text: '修改 fee_mode 或 VIP 檔位後，不需要重新掃描：POST /api/scanner/recalc-fees 會直接用新費率重算快取中所有機會的 net_edge / real_edge，並透過 WebSocket 推送前端。',
      },
      {
        type: 'ul',
        items: [
          'Settings 頁「儲存並重算淨收益」按鈕即呼叫該介面',
          '重算覆蓋 pure / carry / unified 三類快取',
          '費率解析入口：scripts/core/fee_providers.py 的 resolve_venue_fee / parse_fee_policy',
        ],
      },
    ],
  },
]

const en: DocSection[] = [
  {
    id: 'fe-overview',
    title: 'Overview',
    blocks: [
      {
        type: 'p',
        text: 'Gross funding edges are often just a few basis points, so fees decide whether an opportunity is real. Every edge the Scanner shows already deducts open-leg taker fees; this article explains where fee rates come from and how the edge fields differ.',
      },
    ],
  },
  {
    id: 'fe-modes',
    title: 'Fee modes (fee_mode)',
    blocks: [
      {
        type: 'table',
        headers: ['Mode', 'Behavior'],
        rows: [
          ['auto', 'Venues with API keys read real account fees; others estimated from the VIP tier table'],
          ['tier', 'Static VIP ladder for all venues (scripts/core/vip_fee_tiers.py)'],
          ['manual', 'Manual overrides from strategy config'],
        ],
      },
      {
        type: 'p',
        text: 'Configure fee_mode and per-venue VIP tiers (venue_fee_tiers) in Settings → Trading Fees. Venues already using API rates are marked "API" — tier selection has no effect on them.',
      },
    ],
  },
  {
    id: 'fe-spot-futures',
    title: 'Spot vs futures fees',
    blocks: [
      {
        type: 'p',
        text: 'Spot taker (typically 0.1%) is far higher than perp taker (~0.02% – 0.06%). This is a structural advantage of Pure Futures over C&C.',
      },
      {
        type: 'table',
        headers: ['Strategy', 'Open-leg fee composition'],
        rows: [
          ['Cash & Carry / Unified', 'spot_taker + futures_taker'],
          ['Pure Futures', 'long_futures_taker + short_futures_taker'],
        ],
      },
      {
        type: 'callout',
        variant: 'warn',
        text: 'net_edge deducts open fees only. A full cycle (open + close) costs double: round_trip_fee_pct = fee_pct × 2. Use the round-trip figure when estimating break-even holding time.',
      },
    ],
  },
  {
    id: 'fe-vip',
    title: 'VIP tier impact',
    blocks: [
      {
        type: 'p',
        text: 'Higher VIP means lower taker, directly amplifying net_edge / real_edge. The same spread can be negative-edge at VIP0 and positive at a high tier — a wrong fee config distorts the whole Scanner page.',
      },
      {
        type: 'ul',
        items: [
          'Tier tables: public exchange fee schedules, maintained in vip_fee_tiers.py',
          'Where to set: Settings → Trading Fees → per-venue VIP tier',
          'With API keys, real account rates (including rebates) take priority',
        ],
      },
    ],
  },
  {
    id: 'fe-edges',
    title: 'Edge fields',
    blocks: [
      {
        type: 'table',
        headers: ['Field', 'Definition', 'Applies to'],
        rows: [
          ['spread_pct', 'Gross rate spread (or single-venue rate)', 'All'],
          ['fee_pct', 'Sum of both open-leg takers', 'All'],
          ['net_edge_pct', 'spread − fee (reverse also deducts borrow)', 'All'],
          ['mark_spread_pct', 'Relative mark price gap between venues', 'Pure Futures'],
          ['real_edge_pct', 'net_edge − mark_spread', 'Pure Futures (default sort)'],
          ['net_edge_all_in_pct', 'net_edge − cross-venue transfer fee', 'Unified cross-venue routes'],
          ['annual_apy_pct', 'Net edge annualized by settlement period', 'All'],
        ],
      },
      {
        type: 'p',
        text: 'Conservatism order: net_edge < real_edge (Pure Futures) / net_edge_all_in (Unified). When you see a big net_edge, check whether real / all-in still holds.',
      },
    ],
  },
  {
    id: 'fe-recalc',
    title: 'Recalculation after fee changes',
    blocks: [
      {
        type: 'p',
        text: 'After changing fee_mode or VIP tiers, no re-scan is needed: POST /api/scanner/recalc-fees recomputes net_edge / real_edge for all cached opportunities with the new rates and pushes the update over WebSocket.',
      },
      {
        type: 'ul',
        items: [
          'The "Save & recalculate" button in Settings calls this endpoint',
          'Recalculation covers pure / carry / unified caches',
          'Fee resolution entry points: resolve_venue_fee / parse_fee_policy in scripts/core/fee_providers.py',
        ],
      },
    ],
  },
]

export const feesAndEdgeArticle: DocArticleDef = {
  slug: 'fees-and-edge',
  titleKey: 'docs.articles.feesAndEdge.title',
  descKey: 'docs.articles.feesAndEdge.desc',
  tagKey: 'docs.articles.feesAndEdge.tag',
  tagType: 'default',
  sectionsByLocale: {
    'zh-CN': zhCN,
    'zh-TW': zhTW,
    en,
  },
}
