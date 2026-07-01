import type { DocArticleDef, DocSection } from "../types";

const zhCN: DocSection[] = [
  {
    id: "overview",
    title: "总览",
    blocks: [
      {
        type: "p",
        text: "公开 demo 仪表盘需要每小时刷新一次扫描数据，但我们不想为它付服务器费用、也不想每次数据更新都触发 Vercel 重新构建。本篇记录的方案让 GitHub Actions 每小时把扫描结果以静态 JSON 推到 gh-pages 孤儿分支，前端在运行时直接从 raw.githubusercontent.com 拉取（5 分钟边缘缓存 + 完整 CORS 支持），从而实现零服务器、零 Vercel 重建、零持续成本的实时 demo。",
      },
      {
        type: "callout",
        variant: "info",
        text: "四个构建块：GitHub Actions（每小时触发）/ gh-pages 孤儿分支（存放 scanner-latest.json）/ raw.githubusercontent.com（5 分钟边缘缓存，原始文件镜像）/ Vercel 静态站点（运行时拉 JSON）。",
      },
    ],
  },
  {
    id: "data-flow",
    title: "数据流",
    blocks: [
      {
        type: "p",
        text: "整条链路是单向的：扫描器在 CI 里跑完后把结果写成一个 JSON 文件，提交到孤儿分支；前端运行时去 CDN 拉这个 JSON。没有任何反向请求落到我们自己的服务器。",
      },
      {
        type: "ul",
        items: [
          "GitHub Actions 每小时触发（cron-job.org → workflow_dispatch；见 .github/workflows/telegram-push.yml）",
          "Scanner 扫描 9 个所、约 946 个永续资产",
          "scripts/notify/telegram_push.py 把 Top-10 摘要推送到 Telegram 频道",
          "scripts/notify/snapshot_to_pages.py 写出 scanner-latest.json",
          "Workflow 用 [skip ci] 提交到 gh-pages 孤儿分支",
          "Vercel 仪表盘运行时直接从 raw.githubusercontent.com 拉取 JSON —— 不触发任何重建",
        ],
      },
    ],
  },
  {
    id: "cron-job-org",
    title: "外部定时调度（cron-job.org）",
    blocks: [
      {
        type: "p",
        text: "GitHub Actions 的 workflow_dispatch 不会自己按小时跑；需要外部 cron 服务调用 GitHub REST API。使用 fine-grained PAT（Actions: Read and write）POST 到 actions/workflows/telegram-push.yml/dispatches。",
      },
      {
        type: "formula",
        lines: [
          'POST https://api.github.com/repos/{owner}/{repo}/actions/workflows/telegram-push.yml/dispatches',
          'Authorization: Bearer <fine-grained PAT>',
          "",
          '{',
          '  "ref": "main",',
          '  "inputs": {',
          '    "source": "cron",',
          '    "min_edge": "0.0",',
          '    "top_n": "10",',
          '    "include_dex": true',
          "  }",
          "}",
        ],
      },
      {
        type: "p",
        text: "source=cron 会传入 --skip-if-unchanged（Top-N 无变化则跳过 Telegram）；手动 Run workflow 默认 source=manual 始终推送。任一步失败时 Alert on failure 步骤会向 Telegram 发送运行链接。",
      },
      {
        type: "callout",
        variant: "warn",
        text: "若 cron-job.org 的 body 未包含 source: cron，防刷屏不会生效，每小时都会无条件推送。",
      },
    ],
  },
  {
    id: "telegram-digest",
    title: "Telegram 推送格式",
    blocks: [
      {
        type: "p",
        text: "telegram_push.py 把 Pure Futures Top-N 格式化为紧凑型 HTML 消息（parse_mode=HTML）。每条机会占一行：方向、资产、腿组合、net edge（扣开仓腿 taker 手续费后的边际）、APR（优先 net 年化）、近期持续性 P% 与异常波动 ⚡，以及 ⚠️ 结算周期错配、🆕/📈/📉 相对上轮变化标记。",
      },
      {
        type: "p",
        text: "当跨所 mark 价差（mkΔ）显著时，在同一行标注基差风险；完整表格与 Carry / Unified 策略见消息底部的 URL 按钮（无需额外服务端，纯跳转）。",
      },
      {
        type: "ul",
        items: [
          "📊 Dashboard — demo 站点 Pure Futures 扫描页",
          "📈 Carry — /?strategy=carry 深链到 Cash & Carry tab",
          "🔀 Unified — /?strategy=unified 深链到 Unified tab",
        ],
      },
      {
        type: "p",
        text: "默认按钮指向 Vercel demo；本地或自建部署可用 --dashboard-url 覆盖，传空字符串禁用按钮。",
      },
    ],
  },
  {
    id: "orphan-branch",
    title: "为什么用孤儿分支",
    blocks: [
      {
        type: "p",
        text: "如果每小时把 scanner-latest.json 提交到 main，一个月就会污染 720 条自动提交历史，还会触发 24 次/天的 Vercel 重建（Vercel 免费额度很快被打爆）。",
      },
      {
        type: "p",
        text: "孤儿分支（orphan branch）与 main 没有任何共享历史，每次提交只动 scanner-latest.json 这一个文件。main 分支保持干净，仍可正常开发；gh-pages 上则是一个独立的、只放产物的线性历史。",
      },
      {
        type: "callout",
        variant: "info",
        text: "Workflow 首次运行时执行 git checkout --orphan gh-pages，后续运行直接 fetch 已存在的分支；同时在 gh-pages 写入 web/vercel.json（git.deploymentEnabled:false），避免 Vercel 把 gh-pages 当成普通分支触发失败构建（详见 .github/workflows/telegram-push.yml）。",
      },
    ],
  },
  {
    id: "demo-mode",
    title: "前端 Demo 模式",
    blocks: [
      {
        type: "p",
        text: "useDemoSnapshot.ts 在 VITE_DEMO_MODE=1 时拦截所有 /api/scanner/* 的 GET 请求，改为从快照缓存返回数据，从而让静态部署也能展示「实时」扫描结果。",
      },
      {
        type: "p",
        text: "未知端点（POST、wallet、settings 等）故意落到 404 —— demo 模式禁用一切写操作，只展示 Scanner 表格。",
      },
      {
        type: "formula",
        lines: [
          "# .env.production on Vercel",
          "VITE_DEMO_MODE=1",
          "VITE_DEMO_SNAPSHOT_PATH=/gh/counterfactual5/funding-arb@gh-pages/scanner-latest.json",
        ],
      },
      {
        type: "callout",
        variant: "warn",
        text: "Demo 模式禁用 WebSocket 连接、实盘交易、钱包连接 —— 只有 Scanner 表格可用。",
      },
    ],
  },
  {
    id: "local-debug",
    title: "本地调试",
    blocks: [
      {
        type: "formula",
        lines: [
          "# Build the snapshot locally (writes /tmp/scanner-latest.json)",
          "python scripts/notify/snapshot_to_pages.py --out /tmp/scanner-latest.json --top 30",
          "",
          "# Run the Vite dev server in demo mode",
          "cd web",
          "VITE_DEMO_MODE=1 npm run dev",
          "",
          "# Or force demo mode on any deployment via query string",
          "open 'http://localhost:1420/?demo=1'",
        ],
      },
      {
        type: "p",
        text: "useDemoSnapshot.ts 每 5 分钟自动刷新一次快照，从而把流水线新提交的 JSON 拉到前端。",
      },
    ],
  },
];

const zhTW: DocSection[] = [
  {
    id: "overview",
    title: "總覽",
    blocks: [
      {
        type: "p",
        text: "公開 demo 儀表盤需要每小時刷新一次掃描資料，但我們不想為它付伺服器費用、也不想每次資料更新都觸發 Vercel 重新建置。本篇記錄的方案讓 GitHub Actions 每小時把掃描結果以靜態 JSON 推到 gh-pages 孤兒分支，前端在執行時直接從 raw.githubusercontent.com 拉取（5 分鐘邊緩存 + 完整 CORS 支援），從而實現零伺服器、零 Vercel 重建、零持續成本的即時 demo。",
      },
      {
        type: "callout",
        variant: "info",
        text: "四個建構區塊：GitHub Actions（每小時觸發）/ gh-pages 孤兒分支（存放 scanner-latest.json）/ raw.githubusercontent.com（5 分鐘邊緩存，原始檔案鏡像）/ Vercel 靜態站點（執行時拉 JSON）。",
      },
    ],
  },
  {
    id: "data-flow",
    title: "資料流",
    blocks: [
      {
        type: "p",
        text: "整條鏈路是單向的：掃描器在 CI 裡跑完後把結果寫成一個 JSON 檔案，提交到孤兒分支；前端執行時去 CDN 拉這個 JSON。沒有任何反向請求落在我們自己的伺服器。",
      },
      {
        type: "ul",
        items: [
          "GitHub Actions 每小時觸發（cron-job.org → workflow_dispatch；見 .github/workflows/telegram-push.yml）",
          "Scanner 掃描 9 個所、約 946 個永續資產",
          "scripts/notify/telegram_push.py 把 Top-10 摘要推送到 Telegram 頻道",
          "scripts/notify/snapshot_to_pages.py 寫出 scanner-latest.json",
          "Workflow 用 [skip ci] 提交到 gh-pages 孤兒分支",
          "Vercel 儀表盤執行時直接從 raw.githubusercontent.com 拉取 JSON —— 不觸發任何重建",
        ],
      },
    ],
  },
  {
    id: "cron-job-org",
    title: "外部定時調度（cron-job.org）",
    blocks: [
      {
        type: "p",
        text: "GitHub Actions 的 workflow_dispatch 不會自己按小時跑；需要外部 cron 服務呼叫 GitHub REST API。使用 fine-grained PAT（Actions: Read and write）POST 到 actions/workflows/telegram-push.yml/dispatches。",
      },
      {
        type: "formula",
        lines: [
          'POST https://api.github.com/repos/{owner}/{repo}/actions/workflows/telegram-push.yml/dispatches',
          'Authorization: Bearer <fine-grained PAT>',
          "",
          '{',
          '  "ref": "main",',
          '  "inputs": {',
          '    "source": "cron",',
          '    "min_edge": "0.0",',
          '    "top_n": "10",',
          '    "include_dex": true',
          "  }",
          "}",
        ],
      },
      {
        type: "p",
        text: "source=cron 會傳入 --skip-if-unchanged（Top-N 無變化則跳過 Telegram）；手動 Run workflow 預設 source=manual 始終推送。任一步失敗時 Alert on failure 步驟會向 Telegram 發送執行連結。",
      },
      {
        type: "callout",
        variant: "warn",
        text: "若 cron-job.org 的 body 未包含 source: cron，防刷屏不會生效，每小時都會無條件推送。",
      },
    ],
  },
  {
    id: "telegram-digest",
    title: "Telegram 推送格式",
    blocks: [
      {
        type: "p",
        text: "telegram_push.py 把 Pure Futures Top-N 格式化為緊湊型 HTML 訊息（parse_mode=HTML）。每條機會佔一行：方向、資產、腿組合、net edge（扣開倉腿 taker 手續費後的邊際）、APR（優先 net 年化）、近期持續性 P% 與異常波動 ⚡，以及 ⚠️ 結算週期錯配、🆕/📈/📉 相對上輪變化標記。",
      },
      {
        type: "p",
        text: "當跨所 mark 價差（mkΔ）顯著時，在同一行標註基差風險；完整表格與 Carry / Unified 策略見訊息底部的 URL 按鈕（無需額外服務端，純跳轉）。",
      },
      {
        type: "ul",
        items: [
          "📊 Dashboard — demo 站點 Pure Futures 掃描頁",
          "📈 Carry — /?strategy=carry 深鏈到 Cash & Carry tab",
          "🔀 Unified — /?strategy=unified 深鏈到 Unified tab",
        ],
      },
      {
        type: "p",
        text: "預設按鈕指向 Vercel demo；本地或自建部署可用 --dashboard-url 覆蓋，傳空字串禁用按鈕。",
      },
    ],
  },
  {
    id: "orphan-branch",
    title: "為什麼用孤兒分支",
    blocks: [
      {
        type: "p",
        text: "如果每小時把 scanner-latest.json 提交到 main，一個月就會污染 720 條自動提交歷史，還會觸發 24 次/天的 Vercel 重建（Vercel 免費額度很快被打爆）。",
      },
      {
        type: "p",
        text: "孤兒分支（orphan branch）與 main 沒有任何共享歷史，每次提交只動 scanner-latest.json 這一個檔案。main 分支保持乾淨，仍可正常開發；gh-pages 上則是一個獨立的、只放產物的線性歷史。",
      },
      {
        type: "callout",
        variant: "info",
        text: "Workflow 首次執行時執行 git checkout --orphan gh-pages，後續執行直接 fetch 已存在的分支；同時在 gh-pages 寫入 web/vercel.json（git.deploymentEnabled:false），避免 Vercel 把 gh-pages 當成普通分支觸發失敗建置（詳見 .github/workflows/telegram-push.yml）。",
      },
    ],
  },
  {
    id: "demo-mode",
    title: "前端 Demo 模式",
    blocks: [
      {
        type: "p",
        text: "useDemoSnapshot.ts 在 VITE_DEMO_MODE=1 時攔截所有 /api/scanner/* 的 GET 請求，改為從快取快照返回資料，從而讓靜態部署也能展示「即時」掃描結果。",
      },
      {
        type: "p",
        text: "未知端點（POST、wallet、settings 等）故意落到 404 —— demo 模式停用一切寫操作，只展示 Scanner 表格。",
      },
      {
        type: "formula",
        lines: [
          "# .env.production on Vercel",
          "VITE_DEMO_MODE=1",
          "VITE_DEMO_SNAPSHOT_PATH=/gh/counterfactual5/funding-arb@gh-pages/scanner-latest.json",
        ],
      },
      {
        type: "callout",
        variant: "warn",
        text: "Demo 模式停用 WebSocket 連線、實盤交易、錢包連線 —— 只有 Scanner 表格可用。",
      },
    ],
  },
  {
    id: "local-debug",
    title: "本地除錯",
    blocks: [
      {
        type: "formula",
        lines: [
          "# Build the snapshot locally (writes /tmp/scanner-latest.json)",
          "python scripts/notify/snapshot_to_pages.py --out /tmp/scanner-latest.json --top 30",
          "",
          "# Run the Vite dev server in demo mode",
          "cd web",
          "VITE_DEMO_MODE=1 npm run dev",
          "",
          "# Or force demo mode on any deployment via query string",
          "open 'http://localhost:1420/?demo=1'",
        ],
      },
      {
        type: "p",
        text: "useDemoSnapshot.ts 每 5 分鐘自動刷新一次快照，從而把管線新提交的 JSON 拉到前端。",
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
        text: "The public demo dashboard needs to refresh scanner data every hour, but we do not want to pay for a server or trigger a Vercel rebuild on every data update. The pattern documented here lets GitHub Actions push a static JSON snapshot to an orphan gh-pages branch every hour; the frontend fetches it at runtime directly from raw.githubusercontent.com (5-minute edge TTL + permissive CORS). The result: zero servers, zero Vercel rebuilds, zero ongoing cost.",
      },
      {
        type: "callout",
        variant: "info",
        text: "Four building blocks: GitHub Actions (hourly trigger) / gh-pages orphan branch (holds scanner-latest.json) / raw.githubusercontent.com (5-minute edge cache, raw file mirror) / Vercel static site (fetches the JSON at runtime).",
      },
    ],
  },
  {
    id: "data-flow",
    title: "Data Flow",
    blocks: [
      {
        type: "p",
        text: "The whole pipeline is one-directional: after the scanner runs in CI it writes a single JSON file and commits it to the orphan branch; the frontend fetches that JSON from the CDN at runtime. No request ever flows back to our own servers.",
      },
      {
        type: "ul",
        items: [
          "GitHub Actions fires hourly (cron-job.org → workflow_dispatch; see .github/workflows/telegram-push.yml)",
          "Scanner scans 9 venues / ~946 perpetual assets",
          "scripts/notify/telegram_push.py posts the Top-10 digest to the Telegram channel",
          "scripts/notify/snapshot_to_pages.py writes scanner-latest.json",
          "Workflow commits to the gh-pages orphan branch with [skip ci]",
          "Vercel dashboard fetches directly from raw.githubusercontent.com at runtime — no rebuild",
        ],
      },
    ],
  },
  {
    id: "cron-job-org",
    title: "External scheduler (cron-job.org)",
    blocks: [
      {
        type: "p",
        text: "GitHub Actions workflow_dispatch does not run on its own; an external cron service must call the GitHub REST API. Use a fine-grained PAT (Actions: Read and write) to POST to actions/workflows/telegram-push.yml/dispatches.",
      },
      {
        type: "formula",
        lines: [
          'POST https://api.github.com/repos/{owner}/{repo}/actions/workflows/telegram-push.yml/dispatches',
          'Authorization: Bearer <fine-grained PAT>',
          "",
          '{',
          '  "ref": "main",',
          '  "inputs": {',
          '    "source": "cron",',
          '    "min_edge": "0.0",',
          '    "top_n": "10",',
          '    "include_dex": true',
          "  }",
          "}",
        ],
      },
      {
        type: "p",
        text: "source=cron passes --skip-if-unchanged (skip Telegram when Top-N unchanged); manual Run workflow keeps source=manual and always posts. Alert on failure sends a run link to Telegram if any step fails.",
      },
      {
        type: "callout",
        variant: "warn",
        text: "If the cron-job.org body omits source: cron, anti-spam is disabled and Telegram receives an unconditional post every hour.",
      },
    ],
  },
  {
    id: "telegram-digest",
    title: "Telegram digest format",
    blocks: [
      {
        type: "p",
        text: "telegram_push.py formats the Pure Futures Top-N as a compact HTML message (parse_mode=HTML). Each opportunity is one line: direction, asset, leg pair, net edge (after open-leg taker fees), APR (net annualized when available), recent persistence P% and spike ⚡, plus ⚠️ settlement-interval mismatch and 🆕/📈/📉 change markers vs the previous snapshot.",
      },
      {
        type: "p",
        text: "Material cross-venue mark divergence (mkΔ) is inlined when significant; full tables and Carry / Unified strategies are one tap away via URL buttons at the bottom (no callback server — plain links).",
      },
      {
        type: "ul",
        items: [
          "📊 Dashboard — demo site Pure Futures scanner",
          "📈 Carry — /?strategy=carry deep-links to the Cash & Carry tab",
          "🔀 Unified — /?strategy=unified deep-links to the Unified tab",
        ],
      },
      {
        type: "p",
        text: "Buttons default to the Vercel demo; override with --dashboard-url for self-hosted deployments, or pass an empty string to disable.",
      },
    ],
  },
  {
    id: "orphan-branch",
    title: "Why an orphan branch",
    blocks: [
      {
        type: "p",
        text: "Committing scanner-latest.json to main every hour would pollute git history with ~720 auto-commits per month and trigger 24 Vercel rebuilds a day (the free tier would burn through instantly).",
      },
      {
        type: "p",
        text: "An orphan branch has no shared history with main; each commit only touches the single scanner-latest.json file. Main stays clean for normal development, while gh-pages is an independent, artifact-only linear history.",
      },
      {
        type: "callout",
        variant: "info",
        text: "The workflow runs git checkout --orphan gh-pages on first run, and fetches the existing branch on subsequent runs; it also writes web/vercel.json on gh-pages (git.deploymentEnabled:false) so Vercel does not treat gh-pages pushes as failed preview builds (see .github/workflows/telegram-push.yml).",
      },
    ],
  },
  {
    id: "demo-mode",
    title: "Frontend demo mode",
    blocks: [
      {
        type: "p",
        text: 'useDemoSnapshot.ts intercepts every /api/scanner/* GET request when VITE_DEMO_MODE=1 and returns data from the snapshot cache instead, so a static deployment can still show "live" scan results.',
      },
      {
        type: "p",
        text: "Unknown endpoints (POST, wallet, settings, etc.) intentionally fall through to 404 — demo mode disables every write operation, only the Scanner table is shown.",
      },
      {
        type: "formula",
        lines: [
          "# .env.production on Vercel",
          "VITE_DEMO_MODE=1",
          "VITE_DEMO_SNAPSHOT_PATH=/gh/counterfactual5/funding-arb@gh-pages/scanner-latest.json",
        ],
      },
      {
        type: "callout",
        variant: "warn",
        text: "Demo mode disables the WebSocket connection, live trading, and wallet connection — only the Scanner table works.",
      },
    ],
  },
  {
    id: "local-debug",
    title: "Local debugging",
    blocks: [
      {
        type: "formula",
        lines: [
          "# Build the snapshot locally (writes /tmp/scanner-latest.json)",
          "python scripts/notify/snapshot_to_pages.py --out /tmp/scanner-latest.json --top 30",
          "",
          "# Run the Vite dev server in demo mode",
          "cd web",
          "VITE_DEMO_MODE=1 npm run dev",
          "",
          "# Or force demo mode on any deployment via query string",
          "open 'http://localhost:1420/?demo=1'",
        ],
      },
      {
        type: "p",
        text: "useDemoSnapshot.ts auto-refreshes every 5 minutes to surface new commits pushed by the pipeline.",
      },
    ],
  },
];

export const serverlessPipelineArticle: DocArticleDef = {
  slug: "serverless-pipeline",
  titleKey: "docs.articles.serverlessPipeline.title",
  descKey: "docs.articles.serverlessPipeline.desc",
  tagKey: "docs.articles.serverlessPipeline.tag",
  tagType: "success",
  sectionsByLocale: {
    "zh-CN": zhCN,
    "zh-TW": zhTW,
    en,
  },
};
