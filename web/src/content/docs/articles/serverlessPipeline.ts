import type { DocArticleDef, DocSection } from '../types'

const zhCN: DocSection[] = [
  {
    id: 'overview',
    title: '总览',
    blocks: [
      {
        type: 'p',
        text: '公开 demo 仪表盘需要每小时刷新一次扫描数据，但我们不想为它付服务器费用、也不想每次数据更新都触发 Vercel 重新构建。本篇记录的方案让 GitHub Actions 每小时把扫描结果以静态 JSON 推到 gh-pages 孤儿分支，前端在运行时通过 jsDelivr CDN 直接拉取，从而实现零服务器、零 Vercel 重建、零持续成本的实时 demo。',
      },
      {
        type: 'callout',
        variant: 'info',
        text: '四个构建块：GitHub Actions（每小时 :07 触发）/ gh-pages 孤儿分支（存放 scanner-latest.json）/ jsDelivr CDN（全球镜像 gh-pages）/ Vercel 静态站点（运行时拉 JSON）。',
      },
    ],
  },
  {
    id: 'data-flow',
    title: '数据流',
    blocks: [
      {
        type: 'p',
        text: '整条链路是单向的：扫描器在 CI 里跑完后把结果写成一个 JSON 文件，提交到孤儿分支；前端运行时去 CDN 拉这个 JSON。没有任何反向请求落到我们自己的服务器。',
      },
      {
        type: 'ul',
        items: [
          'GitHub Actions cron 每小时 :07 触发（见 .github/workflows/telegram-push.yml）',
          'Scanner 扫描 9 个所、约 946 个永续资产',
          'scripts/notify/telegram_push.py 把 Top-10 摘要推送到 Telegram 频道',
          'scripts/notify/snapshot_to_pages.py 写出 scanner-latest.json',
          'Workflow 用 [skip ci] 提交到 gh-pages 孤儿分支',
          'Vercel 仪表盘运行时通过 jsDelivr CDN 拉取 JSON —— 不触发任何重建',
        ],
      },
    ],
  },
  {
    id: 'orphan-branch',
    title: '为什么用孤儿分支',
    blocks: [
      {
        type: 'p',
        text: '如果每小时把 scanner-latest.json 提交到 main，一个月就会污染 720 条自动提交历史，还会触发 24 次/天的 Vercel 重建（Vercel 免费额度很快被打爆）。',
      },
      {
        type: 'p',
        text: '孤儿分支（orphan branch）与 main 没有任何共享历史，每次提交只动 scanner-latest.json 这一个文件。main 分支保持干净，仍可正常开发；gh-pages 上则是一个独立的、只放产物的线性历史。',
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'Workflow 首次运行时执行 git checkout --orphan gh-pages，后续运行直接 fetch 已存在的分支（详见 .github/workflows/telegram-push.yml）。',
      },
    ],
  },
  {
    id: 'demo-mode',
    title: '前端 Demo 模式',
    blocks: [
      {
        type: 'p',
        text: 'useDemoSnapshot.ts 在 VITE_DEMO_MODE=1 时拦截所有 /api/scanner/* 的 GET 请求，改为从快照缓存返回数据，从而让静态部署也能展示「实时」扫描结果。',
      },
      {
        type: 'p',
        text: '未知端点（POST、wallet、settings 等）故意落到 404 —— demo 模式禁用一切写操作，只展示 Scanner 表格。',
      },
      {
        type: 'formula',
        lines: [
          '# .env.production on Vercel',
          'VITE_DEMO_MODE=1',
          'VITE_DEMO_SNAPSHOT_PATH=/gh/counterfactual5/funding-arb@gh-pages/scanner-latest.json',
        ],
      },
      {
        type: 'callout',
        variant: 'warn',
        text: 'Demo 模式禁用 WebSocket 连接、实盘交易、钱包连接 —— 只有 Scanner 表格可用。',
      },
    ],
  },
  {
    id: 'local-debug',
    title: '本地调试',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# Build the snapshot locally (writes /tmp/scanner-latest.json)',
          'python scripts/notify/snapshot_to_pages.py --out /tmp/scanner-latest.json --top 30',
          '',
          '# Run the Vite dev server in demo mode',
          'cd web',
          'VITE_DEMO_MODE=1 npm run dev',
          '',
          '# Or force demo mode on any deployment via query string',
          "open 'http://localhost:1420/?demo=1'",
        ],
      },
      {
        type: 'p',
        text: 'useDemoSnapshot.ts 每 10 分钟自动刷新一次快照，从而把流水线新提交的 JSON 拉到前端。',
      },
    ],
  },
]

const zhTW: DocSection[] = [
  {
    id: 'overview',
    title: '總覽',
    blocks: [
      {
        type: 'p',
        text: '公開 demo 儀表盤需要每小時刷新一次掃描資料，但我們不想為它付伺服器費用、也不想每次資料更新都觸發 Vercel 重新建置。本篇記錄的方案讓 GitHub Actions 每小時把掃描結果以靜態 JSON 推到 gh-pages 孤兒分支，前端在執行時透過 jsDelivr CDN 直接拉取，從而實現零伺服器、零 Vercel 重建、零持續成本的即時 demo。',
      },
      {
        type: 'callout',
        variant: 'info',
        text: '四個建構區塊：GitHub Actions（每小時 :07 觸發）/ gh-pages 孤兒分支（存放 scanner-latest.json）/ jsDelivr CDN（全球鏡像 gh-pages）/ Vercel 靜態站點（執行時拉 JSON）。',
      },
    ],
  },
  {
    id: 'data-flow',
    title: '資料流',
    blocks: [
      {
        type: 'p',
        text: '整條鏈路是單向的：掃描器在 CI 裡跑完後把結果寫成一個 JSON 檔案，提交到孤兒分支；前端執行時去 CDN 拉這個 JSON。沒有任何反向請求落在我們自己的伺服器。',
      },
      {
        type: 'ul',
        items: [
          'GitHub Actions cron 每小時 :07 觸發（見 .github/workflows/telegram-push.yml）',
          'Scanner 掃描 9 個所、約 946 個永續資產',
          'scripts/notify/telegram_push.py 把 Top-10 摘要推送到 Telegram 頻道',
          'scripts/notify/snapshot_to_pages.py 寫出 scanner-latest.json',
          'Workflow 用 [skip ci] 提交到 gh-pages 孤兒分支',
          'Vercel 儀表盤執行時透過 jsDelivr CDN 拉取 JSON —— 不觸發任何重建',
        ],
      },
    ],
  },
  {
    id: 'orphan-branch',
    title: '為什麼用孤兒分支',
    blocks: [
      {
        type: 'p',
        text: '如果每小時把 scanner-latest.json 提交到 main，一個月就會污染 720 條自動提交歷史，還會觸發 24 次/天的 Vercel 重建（Vercel 免費額度很快被打爆）。',
      },
      {
        type: 'p',
        text: '孤兒分支（orphan branch）與 main 沒有任何共享歷史，每次提交只動 scanner-latest.json 這一個檔案。main 分支保持乾淨，仍可正常開發；gh-pages 上則是一個獨立的、只放產物的線性歷史。',
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'Workflow 首次執行時執行 git checkout --orphan gh-pages，後續執行直接 fetch 已存在的分支（詳見 .github/workflows/telegram-push.yml）。',
      },
    ],
  },
  {
    id: 'demo-mode',
    title: '前端 Demo 模式',
    blocks: [
      {
        type: 'p',
        text: 'useDemoSnapshot.ts 在 VITE_DEMO_MODE=1 時攔截所有 /api/scanner/* 的 GET 請求，改為從快取快照返回資料，從而讓靜態部署也能展示「即時」掃描結果。',
      },
      {
        type: 'p',
        text: '未知端點（POST、wallet、settings 等）故意落到 404 —— demo 模式停用一切寫操作，只展示 Scanner 表格。',
      },
      {
        type: 'formula',
        lines: [
          '# .env.production on Vercel',
          'VITE_DEMO_MODE=1',
          'VITE_DEMO_SNAPSHOT_PATH=/gh/counterfactual5/funding-arb@gh-pages/scanner-latest.json',
        ],
      },
      {
        type: 'callout',
        variant: 'warn',
        text: 'Demo 模式停用 WebSocket 連線、實盤交易、錢包連線 —— 只有 Scanner 表格可用。',
      },
    ],
  },
  {
    id: 'local-debug',
    title: '本地除錯',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# Build the snapshot locally (writes /tmp/scanner-latest.json)',
          'python scripts/notify/snapshot_to_pages.py --out /tmp/scanner-latest.json --top 30',
          '',
          '# Run the Vite dev server in demo mode',
          'cd web',
          'VITE_DEMO_MODE=1 npm run dev',
          '',
          '# Or force demo mode on any deployment via query string',
          "open 'http://localhost:1420/?demo=1'",
        ],
      },
      {
        type: 'p',
        text: 'useDemoSnapshot.ts 每 10 分鐘自動刷新一次快照，從而把管線新提交的 JSON 拉到前端。',
      },
    ],
  },
]

const en: DocSection[] = [
  {
    id: 'overview',
    title: 'Overview',
    blocks: [
      {
        type: 'p',
        text: 'The public demo dashboard needs to refresh scanner data every hour, but we do not want to pay for a server or trigger a Vercel rebuild on every data update. The pattern documented here lets GitHub Actions push a static JSON snapshot to an orphan gh-pages branch every hour; the frontend fetches it at runtime via the jsDelivr CDN. The result: zero servers, zero Vercel rebuilds, zero ongoing cost.',
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'Four building blocks: GitHub Actions (hourly :07 trigger) / gh-pages orphan branch (holds scanner-latest.json) / jsDelivr CDN (mirrors gh-pages globally) / Vercel static site (fetches the JSON at runtime).',
      },
    ],
  },
  {
    id: 'data-flow',
    title: 'Data Flow',
    blocks: [
      {
        type: 'p',
        text: 'The whole pipeline is one-directional: after the scanner runs in CI it writes a single JSON file and commits it to the orphan branch; the frontend fetches that JSON from the CDN at runtime. No request ever flows back to our own servers.',
      },
      {
        type: 'ul',
        items: [
          'GitHub Actions cron fires hourly at :07 (see .github/workflows/telegram-push.yml)',
          'Scanner scans 9 venues / ~946 perpetual assets',
          'scripts/notify/telegram_push.py posts the Top-10 digest to the Telegram channel',
          'scripts/notify/snapshot_to_pages.py writes scanner-latest.json',
          'Workflow commits to the gh-pages orphan branch with [skip ci]',
          'Vercel dashboard fetches via the jsDelivr CDN at runtime — no rebuild',
        ],
      },
    ],
  },
  {
    id: 'orphan-branch',
    title: 'Why an orphan branch',
    blocks: [
      {
        type: 'p',
        text: 'Committing scanner-latest.json to main every hour would pollute git history with ~720 auto-commits per month and trigger 24 Vercel rebuilds a day (the free tier would burn through instantly).',
      },
      {
        type: 'p',
        text: 'An orphan branch has no shared history with main; each commit only touches the single scanner-latest.json file. Main stays clean for normal development, while gh-pages is an independent, artifact-only linear history.',
      },
      {
        type: 'callout',
        variant: 'info',
        text: 'The workflow runs git checkout --orphan gh-pages on first run, and fetches the existing branch on subsequent runs (see .github/workflows/telegram-push.yml).',
      },
    ],
  },
  {
    id: 'demo-mode',
    title: 'Frontend demo mode',
    blocks: [
      {
        type: 'p',
        text: 'useDemoSnapshot.ts intercepts every /api/scanner/* GET request when VITE_DEMO_MODE=1 and returns data from the snapshot cache instead, so a static deployment can still show "live" scan results.',
      },
      {
        type: 'p',
        text: 'Unknown endpoints (POST, wallet, settings, etc.) intentionally fall through to 404 — demo mode disables every write operation, only the Scanner table is shown.',
      },
      {
        type: 'formula',
        lines: [
          '# .env.production on Vercel',
          'VITE_DEMO_MODE=1',
          'VITE_DEMO_SNAPSHOT_PATH=/gh/counterfactual5/funding-arb@gh-pages/scanner-latest.json',
        ],
      },
      {
        type: 'callout',
        variant: 'warn',
        text: 'Demo mode disables the WebSocket connection, live trading, and wallet connection — only the Scanner table works.',
      },
    ],
  },
  {
    id: 'local-debug',
    title: 'Local debugging',
    blocks: [
      {
        type: 'formula',
        lines: [
          '# Build the snapshot locally (writes /tmp/scanner-latest.json)',
          'python scripts/notify/snapshot_to_pages.py --out /tmp/scanner-latest.json --top 30',
          '',
          '# Run the Vite dev server in demo mode',
          'cd web',
          'VITE_DEMO_MODE=1 npm run dev',
          '',
          '# Or force demo mode on any deployment via query string',
          "open 'http://localhost:1420/?demo=1'",
        ],
      },
      {
        type: 'p',
        text: 'useDemoSnapshot.ts auto-refreshes every 10 minutes to surface new commits pushed by the pipeline.',
      },
    ],
  },
]

export const serverlessPipelineArticle: DocArticleDef = {
  slug: 'serverless-pipeline',
  titleKey: 'docs.articles.serverlessPipeline.title',
  descKey: 'docs.articles.serverlessPipeline.desc',
  tagKey: 'docs.articles.serverlessPipeline.tag',
  tagType: 'success',
  sectionsByLocale: {
    'zh-CN': zhCN,
    'zh-TW': zhTW,
    en,
  },
}
