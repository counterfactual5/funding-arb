# 無伺服器資料管線

GitHub Actions → gh-pages → raw.githubusercontent.com → Vercel 的零成本即時 demo 架構

## 總覽

<!-- id: overview -->

公開 demo 儀表盤需要每小時刷新一次掃描資料，但我們不想為它付伺服器費用、也不想每次資料更新都觸發 Vercel 重新建置。本篇記錄的方案讓 GitHub Actions 每小時把掃描結果以靜態 JSON 推到 gh-pages 孤兒分支，前端在執行時直接從 raw.githubusercontent.com 拉取（5 分鐘邊緩存 + 完整 CORS 支援），從而實現零伺服器、零 Vercel 重建、零持續成本的即時 demo。

> ℹ️ 四個建構區塊：GitHub Actions（每小時觸發）/ gh-pages 孤兒分支（存放 scanner-latest.json）/ raw.githubusercontent.com（5 分鐘邊緩存，原始檔案鏡像）/ Vercel 靜態站點（執行時拉 JSON）。

## 資料流

<!-- id: data-flow -->

整條鏈路是單向的：掃描器在 CI 裡跑完後把結果寫成一個 JSON 檔案，提交到孤兒分支；前端執行時直接從 raw.githubusercontent.com 拉這個 JSON。沒有任何反向請求落在我們自己的伺服器。

- GitHub Actions 每小時觸發（cron-job.org → workflow_dispatch；見 .github/workflows/telegram-push.yml）
- Scanner 掃描 9 個所、約 946 個永續資產
- scripts/notify/telegram_push.py 把 Top-10 摘要推送到 Telegram 頻道
- scripts/notify/snapshot_to_pages.py 寫出 scanner-latest.json
- Workflow 用 [skip ci] 提交到 gh-pages 孤兒分支
- Vercel 儀表盤執行時直接從 raw.githubusercontent.com 拉取 JSON —— 不觸發任何重建

## 外部定時調度（cron-job.org）

<!-- id: cron-job-org -->

GitHub Actions 的 `workflow_dispatch` 不會自己按小時跑；需要外部 cron 服務呼叫 GitHub REST API。

**Endpoint**

```text
POST https://api.github.com/repos/{owner}/{repo}/actions/workflows/telegram-push.yml/dispatches
Authorization: Bearer <fine-grained PAT>
Accept: application/vnd.github+json
Content-Type: application/json
```

**PAT 權限**：Repository access 選本倉庫；Permissions → Actions → **Read and write**。

**POST body（推薦）**

```json
{
  "ref": "main",
  "inputs": {
    "source": "cron",
    "min_edge": "0.0",
    "top_n": "10",
    "include_dex": true
  }
}
```

- `source: "cron"` 會傳入 `--skip-if-unchanged`：Top-N 相對上次快照無 🆕/📈/📉 時不發 Telegram（demo 快照仍會更新）。
- 在 GitHub Actions UI 手動 Run workflow 時保持預設 `source=manual`，始終推送，便於除錯。
- 任一步驟失敗時 workflow 末尾的 `Alert on failure` 會向同一 `TELEGRAM_CHAT_ID` 發送帶執行連結的告警。

> ⚠️ 若 cron-job.org 的 body 未包含 `"source": "cron"`，防刷屏邏輯不會生效，每小時都會無條件推送 Telegram。

## 為什麼用孤兒分支

<!-- id: orphan-branch -->

如果每小時把 scanner-latest.json 提交到 main，一個月就會污染 720 條自動提交歷史，還會觸發 24 次/天的 Vercel 重建（Vercel 免費額度很快被打爆）。

孤兒分支（orphan branch）與 main 沒有任何共享歷史，每次提交只動 scanner-latest.json 這一個檔案。main 分支保持乾淨，仍可正常開發；gh-pages 上則是一個獨立的、只放產物的線性歷史。

> ℹ️ Workflow 首次執行時執行 git checkout --orphan gh-pages，後續執行直接 fetch 已存在的分支（詳見 .github/workflows/telegram-push.yml）。

## 前端 Demo 模式

<!-- id: demo-mode -->

useDemoSnapshot.ts 在 VITE_DEMO_MODE=1 時攔截所有 /api/scanner/* 的 GET 請求，改為從快取快照返回資料，從而讓靜態部署也能展示「即時」掃描結果。

未知端點（POST、wallet、settings 等）故意落到 404 —— demo 模式停用一切寫操作，只展示 Scanner 表格。

```text
# .env.production on Vercel
VITE_DEMO_MODE=1
VITE_DEMO_SNAPSHOT_PATH=/gh/counterfactual5/funding-arb@gh-pages/scanner-latest.json
```

> ⚠️ Demo 模式停用 WebSocket 連線、實盤交易、錢包連線 —— 只有 Scanner 表格可用。

## 本地除錯

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

useDemoSnapshot.ts 每 5 分鐘自動刷新一次快照，從而把管線新提交的 JSON 拉到前端。
