import type { DocArticleDef, DocSection } from '../types'

const zhCN: DocSection[] = [
  {
    id: 'fb-what',
    title: '资金费是什么',
    blocks: [
      {
        type: 'p',
        text: '永续合约没有到期日，交易所用资金费（funding）机制把永续价格锚定在现货指数附近。每隔一个结算周期（1h / 2h / 4h / 8h），多头与空头之间按持仓名义价值互相支付费用。',
      },
      {
        type: 'ul',
        items: [
          '费率为正：多头支付给空头（永续价格高于指数，市场偏多）',
          '费率为负：空头支付给多头（永续价格低于指数，市场偏空）',
        ],
      },
      {
        type: 'callout',
        variant: 'info',
        text: '资金费是多空双方互转，交易所不抽成（手续费另计）。套利者的目标：站在收钱的一边，同时用另一条腿对冲价格风险。',
      },
    ],
  },
  {
    id: 'fb-rate',
    title: '费率与结算周期',
    blocks: [
      {
        type: 'p',
        text: '各所公布的 rate_pct 是「当前结算周期内」的费率，周期长度不同，不能直接比较绝对值：',
      },
      {
        type: 'table',
        headers: ['交易所', '典型结算周期'],
        rows: [
          ['Binance / OKX / Bybit', '8h'],
          ['Bitget', '2h 或 8h（按合约）'],
          ['Hyperliquid / Lighter / dYdX v4', '1h'],
          ['EdgeX', '4h（主流合约）'],
          ['Aster', '按合约（常见 8h，读 fundingInfo）'],
        ],
      },
      {
        type: 'formula',
        lines: ['annual_pct ≈ |rate_pct| × (24 / interval_h) × 365'],
      },
      {
        type: 'p',
        text: '例如：8h 周期的 0.01% 年化约 10.95%；1h 周期的 0.01% 年化约 87.6%。同样的数字，周期越短年化越高。',
      },
    ],
  },
  {
    id: 'fb-premium',
    title: '溢价与费率的关系',
    blocks: [
      {
        type: 'p',
        text: '资金费率本质上跟踪「溢价」：标记价（mark）相对指数价（index）的偏离。永续被买得越高于指数，下一期费率越偏正；反之越偏负。',
      },
      {
        type: 'formula',
        lines: ['basis_pct = (mark_price − index_price) / index_price × 100%'],
      },
      {
        type: 'p',
        text: '在跨周期配对中，本系统用该基差对下一期 funding 做加权估计（basis blend），详见「跨周期资金费率套利」。',
      },
      {
        type: 'p',
        text: 'dYdX v4 类似：每分钟采样 impact 价相对 Oracle 的溢价，60 分钟平均后加利率项，每小时整点结算。Indexer 的 nextFundingRate 已是下一小时预测值。',
      },
    ],
  },
  {
    id: 'fb-delta',
    title: 'Delta 中性对冲',
    blocks: [
      {
        type: 'p',
        text: '只做一条腿收资金费会完全暴露在价格波动中。套利的关键是两条腿对冲：一腿收资金费，另一腿抵消价格涨跌。',
      },
      {
        type: 'ul',
        items: [
          '现货多 + 永续空 —— 正向 Cash & Carry',
          '借币卖出 + 永续多 —— 反向 Cash & Carry',
          '永续多 + 永续空（不同所）—— Pure Futures',
        ],
      },
      {
        type: 'callout',
        variant: 'info',
        text: '对冲后价格涨跌基本不影响净值（Delta ≈ 0），收益来源是 funding 减去手续费与借币成本。',
      },
    ],
  },
  {
    id: 'fb-strategies',
    title: '三种策略总览',
    blocks: [
      {
        type: 'table',
        headers: ['策略', '两条腿', '收益来源', '适用场景'],
        rows: [
          ['Cash & Carry（同所）', '现货 + 永续，同一所', '单所费率的绝对值', '某所费率极端（很正或很负）'],
          ['Unified C&C（跨所）', '现货腿与期货腿拆在不同所', '最高费率 + 最低成本的组合', '各所费率 / 手续费 / 借币成本差异大'],
          ['Pure Futures', '两所永续，一多一空', '两所费率之差', '跨所费率分化；DEX 可参与'],
        ],
      },
      {
        type: 'p',
        text: '跨周期配对（如 HL 1h vs CEX 8h）是 Pure Futures 的进阶专题，单独成篇。',
      },
    ],
  },
  {
    id: 'fb-risks',
    title: '共性风险',
    blocks: [
      {
        type: 'table',
        headers: ['风险', '说明', '系统应对'],
        rows: [
          ['费率翻转', '入场后费率转向，从收钱变倒贴', 'exit 阈值 + watcher 持续监控'],
          ['价格错配', '两腿开仓价偏离，入场即浮亏', 'mark_spread 过滤；real_edge 排序'],
          ['手续费侵蚀', '开 + 平共四次 taker', 'net_edge 预扣开仓费；VIP 费率策略'],
          ['强平风险', '永续腿有杠杆，极端行情可能爆仓', '保证金健康度监控（margin health）'],
          ['借币成本浮动', '反向 C&C 的利息随市场变化', 'borrow_per_period 计入边际并持续重估'],
        ],
      },
    ],
  },
]

const zhTW: DocSection[] = [
  {
    id: 'fb-what',
    title: '資金費是什麼',
    blocks: [
      {
        type: 'p',
        text: '永續合約沒有到期日，交易所用資金費（funding）機制把永續價格錨定在現貨指數附近。每隔一個結算週期（1h / 2h / 4h / 8h），多頭與空頭之間按持倉名義價值互相支付費用。',
      },
      {
        type: 'ul',
        items: [
          '費率為正：多頭支付給空頭（永續價格高於指數，市場偏多）',
          '費率為負：空頭支付給多頭（永續價格低於指數，市場偏空）',
        ],
      },
      {
        type: 'callout',
        variant: 'info',
        text: '資金費是多空雙方互轉，交易所不抽成（手續費另計）。套利者的目標：站在收錢的一邊，同時用另一條腿對沖價格風險。',
      },
    ],
  },
  {
    id: 'fb-rate',
    title: '費率與結算週期',
    blocks: [
      {
        type: 'p',
        text: '各所公佈的 rate_pct 是「當前結算週期內」的費率，週期長度不同，不能直接比較絕對值：',
      },
      {
        type: 'table',
        headers: ['交易所', '典型結算週期'],
        rows: [
          ['Binance / OKX / Bybit', '8h'],
          ['Bitget', '2h 或 8h（按合約）'],
          ['Hyperliquid / Lighter / dYdX v4', '1h'],
          ['EdgeX', '4h（主流合約）'],
          ['Aster', '按合約（常見 8h，讀 fundingInfo）'],
        ],
      },
      {
        type: 'formula',
        lines: ['annual_pct ≈ |rate_pct| × (24 / interval_h) × 365'],
      },
      {
        type: 'p',
        text: '例如：8h 週期的 0.01% 年化約 10.95%；1h 週期的 0.01% 年化約 87.6%。同樣的數字，週期越短年化越高。',
      },
    ],
  },
  {
    id: 'fb-premium',
    title: '溢價與費率的關係',
    blocks: [
      {
        type: 'p',
        text: '資金費率本質上跟蹤「溢價」：標記價（mark）相對指數價（index）的偏離。永續被買得越高於指數，下一期費率越偏正；反之越偏負。',
      },
      {
        type: 'formula',
        lines: ['basis_pct = (mark_price − index_price) / index_price × 100%'],
      },
      {
        type: 'p',
        text: '在跨週期配對中，本系統用該基差對下一期 funding 做加權估計（basis blend），詳見「跨週期資金費率套利」。',
      },
      {
        type: 'p',
        text: 'dYdX v4 類似：每分鐘取樣 impact 價相對 Oracle 的溢價，60 分鐘平均後加利率項，每小時整點結算。Indexer 的 nextFundingRate 已是下一小時預測值。',
      },
    ],
  },
  {
    id: 'fb-delta',
    title: 'Delta 中性對沖',
    blocks: [
      {
        type: 'p',
        text: '只做一條腿收資金費會完全暴露在價格波動中。套利的關鍵是兩條腿對沖：一腿收資金費，另一腿抵消價格漲跌。',
      },
      {
        type: 'ul',
        items: [
          '現貨多 + 永續空 —— 正向 Cash & Carry',
          '借幣賣出 + 永續多 —— 反向 Cash & Carry',
          '永續多 + 永續空（不同所）—— Pure Futures',
        ],
      },
      {
        type: 'callout',
        variant: 'info',
        text: '對沖後價格漲跌基本不影響淨值（Delta ≈ 0），收益來源是 funding 減去手續費與借幣成本。',
      },
    ],
  },
  {
    id: 'fb-strategies',
    title: '三種策略總覽',
    blocks: [
      {
        type: 'table',
        headers: ['策略', '兩條腿', '收益來源', '適用場景'],
        rows: [
          ['Cash & Carry（同所）', '現貨 + 永續，同一所', '單所費率的絕對值', '某所費率極端（很正或很負）'],
          ['Unified C&C（跨所）', '現貨腿與期貨腿拆在不同所', '最高費率 + 最低成本的組合', '各所費率 / 手續費 / 借幣成本差異大'],
          ['Pure Futures', '兩所永續，一多一空', '兩所費率之差', '跨所費率分化；DEX 可參與'],
        ],
      },
      {
        type: 'p',
        text: '跨週期配對（如 HL 1h vs CEX 8h）是 Pure Futures 的進階專題，單獨成篇。',
      },
    ],
  },
  {
    id: 'fb-risks',
    title: '共性風險',
    blocks: [
      {
        type: 'table',
        headers: ['風險', '說明', '系統應對'],
        rows: [
          ['費率翻轉', '入場後費率轉向，從收錢變倒貼', 'exit 閾值 + watcher 持續監控'],
          ['價格錯配', '兩腿開倉價偏離，入場即浮虧', 'mark_spread 過濾；real_edge 排序'],
          ['手續費侵蝕', '開 + 平共四次 taker', 'net_edge 預扣開倉費；VIP 費率策略'],
          ['強平風險', '永續腿有槓桿，極端行情可能爆倉', '保證金健康度監控（margin health）'],
          ['借幣成本浮動', '反向 C&C 的利息隨市場變化', 'borrow_per_period 計入邊際並持續重估'],
        ],
      },
    ],
  },
]

const en: DocSection[] = [
  {
    id: 'fb-what',
    title: 'What is funding',
    blocks: [
      {
        type: 'p',
        text: 'Perpetual contracts have no expiry; exchanges use a funding mechanism to anchor the perp price near the spot index. Every settlement period (1h / 2h / 4h / 8h), longs and shorts pay each other based on position notional.',
      },
      {
        type: 'ul',
        items: [
          'Positive rate: longs pay shorts (perp trades above index, market leans long)',
          'Negative rate: shorts pay longs (perp trades below index, market leans short)',
        ],
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'Funding is a transfer between longs and shorts — the exchange takes no cut (trading fees are separate). The arbitrageur aims to sit on the receiving side while hedging price risk with another leg.',
      },
    ],
  },
  {
    id: 'fb-rate',
    title: 'Rate and settlement period',
    blocks: [
      {
        type: 'p',
        text: 'Each venue publishes rate_pct for its own settlement period. Periods differ, so absolute values are not directly comparable:',
      },
      {
        type: 'table',
        headers: ['Exchange', 'Typical period'],
        rows: [
          ['Binance / OKX / Bybit', '8h'],
          ['Bitget', '2h or 8h (per contract)'],
          ['Hyperliquid / Lighter / dYdX v4', '1h'],
          ['EdgeX', '4h (majors)'],
          ['Aster', 'Per contract (often 8h)'],
        ],
      },
      {
        type: 'formula',
        lines: ['annual_pct ≈ |rate_pct| × (24 / interval_h) × 365'],
      },
      {
        type: 'p',
        text: 'Example: 0.01% on an 8h period is ~10.95% annualized; 0.01% on a 1h period is ~87.6%. Same number, shorter period, much higher APY.',
      },
    ],
  },
  {
    id: 'fb-premium',
    title: 'Premium and the funding rate',
    blocks: [
      {
        type: 'p',
        text: 'The funding rate essentially tracks the premium: how far mark price deviates from index price. The higher the perp trades above index, the more positive the next funding.',
      },
      {
        type: 'formula',
        lines: ['basis_pct = (mark_price − index_price) / index_price × 100%'],
      },
      {
        type: 'p',
        text: 'For cross-interval pairs, this system blends the basis into the next-period funding estimate (basis blend) — see "Cross-Interval Funding Arbitrage".',
      },
      {
        type: 'p',
        text: 'dYdX v4 samples impact prices vs the oracle each minute, averages over 60 minutes, adds an interest term, and settles hourly. The indexer nextFundingRate is the forecast for the next hour.',
      },
    ],
  },
  {
    id: 'fb-delta',
    title: 'Delta-neutral hedging',
    blocks: [
      {
        type: 'p',
        text: 'Holding a single funding-collecting leg leaves you fully exposed to price moves. The key is a two-leg hedge: one leg collects funding, the other offsets price risk.',
      },
      {
        type: 'ul',
        items: [
          'Spot long + perp short — Forward Cash & Carry',
          'Borrow-sell + perp long — Reverse Cash & Carry',
          'Perp long + perp short (different venues) — Pure Futures',
        ],
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'After hedging, price moves barely affect NAV (delta ≈ 0). Returns come from funding minus fees and borrow cost.',
      },
    ],
  },
  {
    id: 'fb-strategies',
    title: 'Strategy map',
    blocks: [
      {
        type: 'table',
        headers: ['Strategy', 'Legs', 'Return source', 'When it works'],
        rows: [
          ['Cash & Carry (same venue)', 'Spot + perp on one venue', 'Absolute funding rate', 'One venue has an extreme rate'],
          ['Unified C&C (cross-venue)', 'Spot and futures legs on different venues', 'Best rate + lowest cost combo', 'Rates / fees / borrow costs diverge across venues'],
          ['Pure Futures', 'Two perps, long one / short the other', 'Rate differential between venues', 'Cross-venue rate divergence; DEXs can participate'],
        ],
      },
      {
        type: 'p',
        text: 'Cross-interval pairs (e.g. HL 1h vs CEX 8h) are an advanced Pure Futures topic with a dedicated article.',
      },
    ],
  },
  {
    id: 'fb-risks',
    title: 'Common risks',
    blocks: [
      {
        type: 'table',
        headers: ['Risk', 'Description', 'Mitigation'],
        rows: [
          ['Rate flip', 'Funding turns against you after entry', 'Exit thresholds + watcher monitoring'],
          ['Price mismatch', 'Legs fill at diverging prices', 'mark_spread filter; sort by real_edge'],
          ['Fee erosion', 'Four taker fills across open + close', 'net_edge pre-deducts open fees; VIP fee policy'],
          ['Liquidation', 'Perp leg uses leverage', 'Margin health monitoring'],
          ['Floating borrow cost', 'Reverse C&C interest varies', 'borrow_per_period priced into the edge'],
        ],
      },
    ],
  },
]

export const fundingBasicsArticle: DocArticleDef = {
  slug: 'funding-basics',
  titleKey: 'docs.articles.fundingBasics.title',
  descKey: 'docs.articles.fundingBasics.desc',
  tagKey: 'docs.articles.fundingBasics.tag',
  tagType: 'info',
  sectionsByLocale: {
    'zh-CN': zhCN,
    'zh-TW': zhTW,
    en,
  },
}
