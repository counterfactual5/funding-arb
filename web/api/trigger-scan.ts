/**
 * External cron (cron-job.org) → GitHub Actions trigger.
 *
 * An external free cron service (cron-job.org) hits this endpoint hourly.
 * The handler calls GitHub's `workflow_dispatch` API to launch the
 * "TG Funding Push" workflow on main, which does the actual scan +
 * Telegram broadcast + gh-pages snapshot publish.
 *
 * Why this exists:
 *   GitHub Actions' built-in `schedule:` cron is heavily deprioritized for
 *   low-activity repos — we observed 3-6 hour gaps where scheduled runs were
 *   silently skipped. cron-job.org's free tier runs at 1-minute precision
 *   and gives us a real hourly cadence without a VPS or Vercel Pro.
 *
 *   (Vercel Cron itself is Hobby-limited to once-per-day, so we can't use
 *   the built-in `crons` config — hence the external trigger.)
 *
 * Auth:
 *   The external cron sends `Authorization: Bearer <CRON_SECRET>`. We compare
 *   against the `CRON_SECRET` env var to reject random external POSTs.
 *
 * Env vars (set in Vercel project settings):
 *   - CRON_SECRET  random string, also configured in cron-job.org's headers
 *   - GH_PAT       GitHub Personal Access Token with `actions: write` on this repo
 *   - GH_REPO      owner/name, defaults to "counterfactual5/funding-arb"
 */

// Vercel injects process.env at build time on both Node and Edge runtimes.
// Declared as a typed alias avoids pulling in @types/node (zero new deps).
const env: Record<string, string | undefined> =
  (globalThis as { process?: { env?: Record<string, string | undefined> } })
    .process?.env ?? {};

export const config = {
  runtime: "edge",
};

const DEFAULT_REPO = "counterfactual5/funding-arb";
const WORKFLOW_ID = "telegram-push.yml";
const WORKFLOW_REF = "main";

export default async function handler(req: Request): Promise<Response> {
  const authHeader = req.headers.get("authorization") || "";
  const expected = `Bearer ${env.CRON_SECRET || ""}`;
  if (!expected || authHeader !== expected) {
    return new Response(JSON.stringify({ error: "unauthorized" }), {
      status: 401,
      headers: { "content-type": "application/json" },
    });
  }

  const token = env.GH_PAT;
  if (!token) {
    return new Response(JSON.stringify({ error: "GH_PAT env var not set" }), {
      status: 500,
      headers: { "content-type": "application/json" },
    });
  }

  const repo = env.GH_REPO || DEFAULT_REPO;
  const url =
    `https://api.github.com/repos/${repo}/actions/workflows/` +
    `${WORKFLOW_ID}/dispatches`;

  const ghResp = await fetch(url, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "vercel-cron-funding-arb",
    },
    body: JSON.stringify({ ref: WORKFLOW_REF }),
  });

  const status = ghResp.status;
  let body: string = "";
  if (status !== 204) {
    body = await ghResp.text().catch(() => "");
  }

  return new Response(JSON.stringify({ ok: status === 204, status, body }), {
    status: status === 204 ? 200 : 502,
    headers: { "content-type": "application/json" },
  });
}
