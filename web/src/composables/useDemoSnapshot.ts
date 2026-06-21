/**
 * Demo-mode data layer.
 *
 * When `import.meta.env.VITE_DEMO_MODE === '1'`, the frontend runs without a
 * backend. Instead, it fetches a static JSON snapshot produced every hour by
 * the GitHub Actions workflow `telegram-push.yml` (which also pushes it to the
 * `gh-pages` orphan branch). The snapshot is delivered via the jsDelivr CDN,
 * so Vercel never needs to rebuild to surface fresh data.
 *
 * The rest of the app is unaware of this — `useApi.ts` short-circuits known
 * GET endpoints to read from this layer instead of `fetch('/api/...')`.
 *
 * Snapshot schema lives in `scripts/notify/snapshot_to_pages.py`.
 */

import { ref, type Ref } from 'vue'
import type {
  ScannerOpportunities,
  ScannerStatus,
} from '@/composables/useApi'

// ─── Configuration ────────────────────────────────────────────────

const DEMO_SNAPSHOT_PATH =
  (import.meta.env.VITE_DEMO_SNAPSHOT_PATH as string | undefined) ??
  '/gh/counterfactual5/funding-arb@gh-pages/scanner-latest.json'

export const DEMO_SNAPSHOT_URL = `https://cdn.jsdelivr.net${DEMO_SNAPSHOT_PATH}`

export const isDemoMode: boolean =
  (import.meta.env.VITE_DEMO_MODE as string | undefined) === '1' ||
  // Allow `?demo=1` query override for ad-hoc testing in any deployment.
  (typeof window !== 'undefined' &&
    new URLSearchParams(window.location.search).get('demo') === '1')

// ─── Cache (singleton) ────────────────────────────────────────────

interface DemoSnapshot {
  meta: {
    schema_version: number
    generated_at: string
    scan_timestamp: string
    pipeline: {
      runner?: string
      repo?: string
      run_id?: string
      git_sha?: string
      elapsed_sec?: number
      [k: string]: unknown
    }
  }
  scanner_status: ScannerStatus & { is_demo_snapshot?: boolean }
  scanner_opportunities: ScannerOpportunities
}

const _state = {
  snapshot: ref<DemoSnapshot | null>(null) as Ref<DemoSnapshot | null>,
  error: ref<string | null>(null) as Ref<string | null>,
  loading: ref(false) as Ref<boolean>,
  promise: null as Promise<DemoSnapshot | null> | null,
}

let _refreshTimer: ReturnType<typeof setInterval> | null = null

// ─── Fetch ────────────────────────────────────────────────────────

async function fetchSnapshot(
  force = false,
): Promise<DemoSnapshot | null> {
  if (!force && _state.snapshot.value) return _state.snapshot.value
  if (!force && _state.promise) return _state.promise

  _state.loading.value = true
  _state.error.value = null

  // Cache-bust so CDN refresh actually shows up; jsDelivr caches ~10 min,
  // this nudges the edge to fetch the latest commit on `gh-pages`.
  const cacheBust = `?t=${Math.floor(Date.now() / 60000)}`
  const url = `${DEMO_SNAPSHOT_URL}${cacheBust}`

  _state.promise = (async () => {
    try {
      const resp = await fetch(url, {
        headers: { Accept: 'application/json' },
      })
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status} ${resp.statusText}`)
      }
      const data = (await resp.json()) as DemoSnapshot
      _state.snapshot.value = data
      return data
    } catch (e) {
      _state.error.value = e instanceof Error ? e.message : String(e)
      return null
    } finally {
      _state.loading.value = false
      _state.promise = null
    }
  })()

  return _state.promise
}

// ─── Public API ───────────────────────────────────────────────────

export function useDemoSnapshot() {
  // Auto-refresh every 10 minutes (matches jsDelivr's typical edge TTL).
  if (isDemoMode && !_refreshTimer && typeof window !== 'undefined') {
    _refreshTimer = setInterval(() => {
      fetchSnapshot(true).catch(() => {
        /* swallow — UI will surface error via _state.error */
      })
    }, 10 * 60 * 1000)
  }

  return {
    snapshot: _state.snapshot,
    error: _state.error,
    loading: _state.loading,
    refresh: () => fetchSnapshot(true),
    ensure: () => fetchSnapshot(false),
    url: DEMO_SNAPSHOT_URL,
  }
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
  if (!isDemoMode || !_state.snapshot.value) return undefined
  const path = pathWithQuery.split('?')[0]
  const snap = _state.snapshot.value

  // Scanner
  if (path === '/scanner/opportunities') return snap.scanner_opportunities
  if (path === '/scanner/status') return snap.scanner_status

  // Anything else (positions, settings, wallet, backtest, POST endpoints)
  // intentionally has no demo answer — those features are disabled in demo.
  return undefined
}
