# 无服务器数据流水线

GitHub Actions → gh-pages → raw.githubusercontent.com → Vercel 的零成本实时 demo 架构

## 总览

<!-- id: overview -->

公开 demo 仪表盘需要每小时刷新一次扫描数据，但我们不想为它付服务器费用、也不想每次数据更新都触发 Vercel 重新构建。本篇记录的方案让 GitHub Actions 每小时把扫描结果以静态 JSON 推到 gh-pages 孤儿分支，前端在运行时直接从 raw.githubusercontent.com 拉取（5 分钟边缘缓存 + 完整 CORS 支持），从而实现零服务器、零 Vercel 重建、零持续成本的实时 demo。

> ℹ️ 四个构建块：GitHub Actions（每小时触发）/ gh-pages 孤儿分支（存放 scanner-latest.json）/ raw.githubusercontent.com（5 分钟边缘缓存，原始文件镜像）/ Vercel 静态站点（运行时拉 JSON）。

## 数据流

<!-- id: data-flow -->

整条链路是单向的：扫描器在 CI 里跑完后把结果写成一个 JSON 文件，提交到孤儿分支；前端运行时直接从 raw.githubusercontent.com 拉这个 JSON。没有任何反向请求落到我们自己的服务器。

- GitHub Actions 每小时触发（cron-job.org → workflow_dispatch；见 .github/workflows/telegram-push.yml）
- Scanner 扫描 9 个所、约 946 个永续资产
- scripts/notify/telegram_push.py 把 Top-10 摘要推送到 Telegram 频道
- scripts/notify/snapshot_to_pages.py 写出 scanner-latest.json
- Workflow 用 [skip ci] 提交到 gh-pages 孤儿分支
- Vercel 仪表盘运行时直接从 raw.githubusercontent.com 拉取 JSON —— 不触发任何重建

## 为什么用孤儿分支

<!-- id: orphan-branch -->

如果每小时把 scanner-latest.json 提交到 main，一个月就会污染 720 条自动提交历史，还会触发 24 次/天的 Vercel 重建（Vercel 免费额度很快被打爆）。

孤儿分支（orphan branch）与 main 没有任何共享历史，每次提交只动 scanner-latest.json 这一个文件。main 分支保持干净，仍可正常开发；gh-pages 上则是一个独立的、只放产物的线性历史。

> ℹ️ Workflow 首次运行时执行 git checkout --orphan gh-pages，后续运行直接 fetch 已存在的分支（详见 .github/workflows/telegram-push.yml）。

## 前端 Demo 模式

<!-- id: demo-mode -->

useDemoSnapshot.ts 在 VITE_DEMO_MODE=1 时拦截所有 /api/scanner/* 的 GET 请求，改为从快照缓存返回数据，从而让静态部署也能展示「实时」扫描结果。

未知端点（POST、wallet、settings 等）故意落到 404 —— demo 模式禁用一切写操作，只展示 Scanner 表格。

```text
# .env.production on Vercel
VITE_DEMO_MODE=1
VITE_DEMO_SNAPSHOT_PATH=/gh/counterfactual5/funding-arb@gh-pages/scanner-latest.json
```

> ⚠️ Demo 模式禁用 WebSocket 连接、实盘交易、钱包连接 —— 只有 Scanner 表格可用。

## 本地调试

<!-- id: local-debug -->

```text
# Build the snapshot locally (writes /tmp/scanner-latest.json)
python scripts/notify/snapshot_to_pages.py --out /tmp/scanner-latest.json --top 30

# Run the Vite dev server in demo mode
cd web
VITE_DEMO_MODE=1 npm run dev

# Or force demo mode on any deployment via query string
open 'http://localhost:1420/?demo=1'
```

useDemoSnapshot.ts 每 10 分钟自动刷新一次快照，从而把流水线新提交的 JSON 拉到前端。
