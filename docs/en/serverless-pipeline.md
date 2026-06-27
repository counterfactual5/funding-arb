# Serverless Data Pipeline

GitHub Actions → gh-pages → raw.githubusercontent.com → Vercel: zero-cost live demo architecture

## Overview

<!-- id: overview -->

The public demo dashboard needs to refresh scanner data every hour, but we do not want to pay for a server or trigger a Vercel rebuild on every data update. The pattern documented here lets GitHub Actions push a static JSON snapshot to an orphan gh-pages branch every hour; the frontend fetches it at runtime directly from raw.githubusercontent.com (5-minute edge cache + permissive CORS). The result: zero servers, zero Vercel rebuilds, zero ongoing cost.

> ℹ️ Four building blocks: GitHub Actions (hourly trigger) / gh-pages orphan branch (holds scanner-latest.json) / raw.githubusercontent.com (5-minute edge cache, raw file mirror) / Vercel static site (fetches the JSON at runtime).

## Data Flow

<!-- id: data-flow -->

The whole pipeline is one-directional: after the scanner runs in CI it writes a single JSON file and commits it to the orphan branch; the frontend fetches that JSON directly from raw.githubusercontent.com at runtime. No request ever flows back to our own servers.

- GitHub Actions fires hourly (cron-job.org → workflow_dispatch; see .github/workflows/telegram-push.yml)
- Scanner scans 9 venues / ~946 perpetual assets
- scripts/notify/telegram_push.py posts the Top-10 digest to the Telegram channel
- scripts/notify/snapshot_to_pages.py writes scanner-latest.json
- Workflow commits to the gh-pages orphan branch with [skip ci]
- Vercel dashboard fetches directly from raw.githubusercontent.com at runtime — no rebuild

## Why an orphan branch

<!-- id: orphan-branch -->

Committing scanner-latest.json to main every hour would pollute git history with ~720 auto-commits per month and trigger 24 Vercel rebuilds a day (the free tier would burn through instantly).

An orphan branch has no shared history with main; each commit only touches the single scanner-latest.json file. Main stays clean for normal development, while gh-pages is an independent, artifact-only linear history.

> ℹ️ The workflow runs git checkout --orphan gh-pages on first run, and fetches the existing branch on subsequent runs (see .github/workflows/telegram-push.yml).

## Frontend demo mode

<!-- id: demo-mode -->

useDemoSnapshot.ts intercepts every /api/scanner/* GET request when VITE_DEMO_MODE=1 and returns data from the snapshot cache instead, so a static deployment can still show "live" scan results.

Unknown endpoints (POST, wallet, settings, etc.) intentionally fall through to 404 — demo mode disables every write operation, only the Scanner table is shown.

```text
# .env.production on Vercel
VITE_DEMO_MODE=1
VITE_DEMO_SNAPSHOT_PATH=/gh/counterfactual5/funding-arb@gh-pages/scanner-latest.json
```

> ⚠️ Demo mode disables the WebSocket connection, live trading, and wallet connection — only the Scanner table works.

## Local debugging

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

useDemoSnapshot.ts auto-refreshes every 10 minutes to surface new commits pushed by the pipeline.
