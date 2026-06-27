/**
 * Demo-mode data layer.
 *
 * When `import.meta.env.VITE_DEMO_MODE === '1'`, the frontend runs without a
 * backend. Instead, it fetches a static JSON snapshot produced every hour by
 * the GitHub Actions workflow `telegram-push.yml` (which also pushes it to the
 * `gh-pages` orphan branch). The snapshot is fetched directly from
 * `raw.githubusercontent.com` (5-minute edge TTL + permissive CORS), so Vercel
 * never needs to rebuild to surface fresh data.
 *
 * The rest of the app is unaware of this — `useApi.ts` short-circuits known
 * GET endpoints to read from this layer instead of `fetch('/api/...')`.
 *
 * Snapshot schema lives in `scripts/notify/snapshot_to_pages.py`.
 */

import { ref, type Ref } from "vue";
import type { ScannerOpportunities, ScannerStatus } from "@/composables/useApi";

// ─── Configuration ────────────────────────────────────────────────

const DEMO_SNAPSHOT_PATH =
  (import.meta.env.VITE_DEMO_SNAPSHOT_PATH as string | undefined) ??
  "/counterfactual5/funding-arb/gh-pages/scanner-latest.json";

// IMPORTANT: we fetch from raw.githubusercontent.com, NOT jsDelivr.
// jsDelivr caches gh content for up to 12 hours (s-maxage=43200) and ignores
// cache-buster query strings, which made the demo show stale data long after
// the hourly GitHub Actions run had refreshed the snapshot. raw.githubusercontent
// returns ``cache-control: max-age=300`` and supports CORS
// (``access-control-allow-origin: *``), so the browser sees fresh data within
// 5 minutes of each push — good enough for an hourly-updated demo.
export const DEMO_SNAPSHOT_URL = `https://raw.githubusercontent.com${DEMO_SNAPSHOT_PATH}`;

function _detectDemoMode(): boolean {
  // 1. Explicit env var (set at build time on Vercel: VITE_DEMO_MODE=1).
  if ((import.meta.env.VITE_DEMO_MODE as string | undefined) === "1") {
    return true;
  }
  if (typeof window === "undefined") return false;

  // 2. `?demo=1` / `?demo=0` query override (ad-hoc testing).
  const q = new URLSearchParams(window.location.search).get("demo");
  if (q === "1") return true;
  if (q === "0") return false;

  // 3. Auto-detect: any `*.vercel.app` deployment is treated as the static
  //    demo (no backend). This is the safety net for when the VITE_DEMO_MODE
  //    env var wasn't baked into the build — without it, the dashboard would
  //    show "Disconnected" and an empty table because /api/* 404s.
  //    Opt out explicitly with `?demo=0` or by setting VITE_DEMO_MODE=0.
  const host = window.location.hostname;
  if (host.endsWith(".vercel.app")) return true;

  return false;
}

export const isDemoMode: boolean = _detectDemoMode();

// ─── Cache (singleton) ────────────────────────────────────────────

interface DemoSnapshot {
  meta: {
    schema_version: number;
    generated_at: string;
    scan_timestamp: string;
    pipeline: {
      runner?: string;
      repo?: string;
      run_id?: string;
      git_sha?: string;
      elapsed_sec?: number;
      [k: string]: unknown;
    };
  };
  scanner_status: ScannerStatus & { is_demo_snapshot?: boolean };
  scanner_opportunities: ScannerOpportunities;
  // Carry / Unified slices — optional so older snapshots stay readable.
  scanner_carry_venues?: unknown[];
  scanner_unified_routes?: {
    venues?: string[];
    forward?: unknown[];
    reverse?: unknown[];
  };
}

const _state = {
  snapshot: ref<DemoSnapshot | null>(null) as Ref<DemoSnapshot | null>,
  error: ref<string | null>(null) as Ref<string | null>,
  loading: ref(false) as Ref<boolean>,
  promise: null as Promise<DemoSnapshot | null> | null,
};

let _refreshTimer: ReturnType<typeof setInterval> | null = null;

// ─── Fetch ────────────────────────────────────────────────────────

async function fetchSnapshot(force = false): Promise<DemoSnapshot | null> {
  if (!force && _state.snapshot.value) return _state.snapshot.value;
  if (!force && _state.promise) return _state.promise;

  _state.loading.value = true;
  _state.error.value = null;

  // Cache-bust so the browser doesn't serve a stale entry across the 5-min
  // raw.githubusercontent.com edge TTL. Per-minute granularity is plenty —
  // the snapshot itself only updates hourly.
  const cacheBust = `?t=${Math.floor(Date.now() / 60000)}`;
  const url = `${DEMO_SNAPSHOT_URL}${cacheBust}`;

  _state.promise = (async () => {
    try {
      const resp = await fetch(url, {
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
      }
      const data = (await resp.json()) as DemoSnapshot;
      _state.snapshot.value = data;
      return data;
    } catch (e) {
      _state.error.value = e instanceof Error ? e.message : String(e);
      return null;
    } finally {
      _state.loading.value = false;
      _state.promise = null;
    }
  })();

  return _state.promise;
}

// ─── Public API ───────────────────────────────────────────────────

export function useDemoSnapshot() {
  // Auto-refresh every 5 minutes (matches raw.githubusercontent.com's
  // cache-control: max-age=300 — the snapshot itself updates hourly, so
  // polling faster would just waste bandwidth).
  if (isDemoMode && !_refreshTimer && typeof window !== "undefined") {
    _refreshTimer = setInterval(
      () => {
        fetchSnapshot(true).catch(() => {
          /* swallow — UI will surface error via _state.error */
        });
      },
      5 * 60 * 1000,
    );
  }

  return {
    snapshot: _state.snapshot,
    error: _state.error,
    loading: _state.loading,
    refresh: () => fetchSnapshot(true),
    ensure: () => fetchSnapshot(false),
    url: DEMO_SNAPSHOT_URL,
  };
}

// ─── Demo-mode route table ────────────────────────────────────────

/**
 * Map a frontend API path (e.g. `/scanner/opportunities?strategy=pure`) to
 * the matching slice of the demo snapshot. Returns `undefined` if the path
 * is unknown in demo mode — callers fall through to the normal fetch path,
 * which will simply 404 against the static host (intended: write endpoints
 * should fail loudly in demo).
 */
export function resolveDemoRoute(pathWithQuery: string): unknown | undefined {
  if (!isDemoMode || !_state.snapshot.value) return undefined;
  const path = pathWithQuery.split("?")[0];
  const snap = _state.snapshot.value;

  // Scanner
  if (path === "/scanner/opportunities") return snap.scanner_opportunities;
  if (path === "/scanner/status") return snap.scanner_status;

  // Anything else (positions, settings, wallet, backtest, POST endpoints)
  // intentionally has no demo answer — those features are disabled in demo.
  return undefined;
}
