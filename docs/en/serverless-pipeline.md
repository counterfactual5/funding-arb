# Serverless Data Pipeline

GitHub Actions → gh-pages → raw.githubusercontent.com → Vercel: zero-cost live demo architecture

## Overview

<!-- id: overview -->

The public demo dashboard needs to refresh scanner data every hour, but we do not want to pay for a server or trigger a Vercel rebuild on every data update. The pattern documented here lets GitHub Actions push a static JSON snapshot to an orphan gh-pages branch every hour; the frontend fetches it at runtime directly from raw.githubusercontent.com (5-minute edge TTL + permissive CORS). The result: zero servers, zero Vercel rebuilds, zero ongoing cost.

> ℹ️ Four building blocks: GitHub Actions (hourly trigger) / gh-pages orphan branch (holds scanner-latest.json) / raw.githubusercontent.com (5-minute edge cache, raw file mirror) / Vercel static site (fetches the JSON at runtime).

## Data Flow

<!-- id: data-flow -->

The whole pipeline is one-directional: after the scanner runs in CI it writes a single JSON file and commits it to the orphan branch; the frontend fetches that JSON from the CDN at runtime. No request ever flows back to our own servers.

- GitHub Actions fires hourly (cron-job.org → workflow_dispatch; see .github/workflows/telegram-push.yml)
- Scanner scans 9 venues / ~946 perpetual assets
- scripts/notify/telegram_push.py posts the Top-10 digest to the Telegram channel
- scripts/notify/snapshot_to_pages.py writes scanner-latest.json
- Workflow commits to the gh-pages orphan branch with [skip ci]
- Vercel dashboard fetches directly from raw.githubusercontent.com at runtime — no rebuild

## External scheduler (cron-job.org)

<!-- id: cron-job-org -->

GitHub Actions workflow_dispatch does not run on its own; an external cron service must call the GitHub REST API. Use a fine-grained PAT (Actions: Read and write) to POST to actions/workflows/telegram-push.yml/dispatches.

```text
POST https://api.github.com/repos/{owner}/{repo}/actions/workflows/telegram-push.yml/dispatches
Authorization: Bearer <fine-grained PAT>

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

source=cron passes --skip-if-unchanged (skip Telegram when Top-N unchanged); manual Run workflow keeps source=manual and always posts. Alert on failure sends a run link to Telegram if any step fails.

> ⚠️ If the cron-job.org body omits source: cron, anti-spam is disabled and Telegram receives an unconditional post every hour.

## Telegram digest format

<!-- id: telegram-digest -->

telegram_push.py formats the Pure Futures Top-N as a compact HTML message (parse_mode=HTML). Each opportunity is one line: direction, asset, leg pair, net edge (after open-leg taker fees), APR (net annualized when available), recent persistence P% and spike ⚡, plus ⚠️ settlement-interval mismatch and 🆕/📈/📉 change markers vs the previous snapshot.

Material cross-venue mark divergence (mkΔ) is inlined when significant; full tables and Carry / Unified strategies are one tap away via URL buttons at the bottom (no callback server — plain links).

- 📊 Dashboard — demo site Pure Futures scanner
- 📈 Carry — /?strategy=carry deep-links to the Cash & Carry tab
- 🔀 Unified — /?strategy=unified deep-links to the Unified tab

Buttons default to the Vercel demo; override with --dashboard-url for self-hosted deployments, or pass an empty string to disable.

## Why an orphan branch

<!-- id: orphan-branch -->

Committing scanner-latest.json to main every hour would pollute git history with ~720 auto-commits per month and trigger 24 Vercel rebuilds a day (the free tier would burn through instantly).

An orphan branch has no shared history with main; each commit only touches the single scanner-latest.json file. Main stays clean for normal development, while gh-pages is an independent, artifact-only linear history.

> ℹ️ The workflow runs git checkout --orphan gh-pages on first run, and fetches the existing branch on subsequent runs; it also writes web/vercel.json on gh-pages (git.deploymentEnabled:false) so Vercel does not treat gh-pages pushes as failed preview builds (see .github/workflows/telegram-push.yml).

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

useDemoSnapshot.ts auto-refreshes every 5 minutes to surface new commits pushed by the pipeline.
