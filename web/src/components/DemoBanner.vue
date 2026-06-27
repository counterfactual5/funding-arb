<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { NAlert, NSpin } from 'naive-ui'

const props = defineProps<{
  /** CDN URL the demo is currently fetching from. */
  source?: string
  /** Scan timestamp from the snapshot (ISO string). */
  scanTime?: string | null
  /** Whether the snapshot fetch is still in flight. */
  loading?: boolean
}>()

const scanTimeHuman = computed(() => {
  if (!props.scanTime) return ''
  try {
    const d = new Date(props.scanTime)
    return d.toLocaleString(undefined, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'UTC',
      timeZoneName: 'short',
    })
  } catch {
    return props.scanTime
  }
})

const stale = ref(false)
const REFRESH_WINDOW_MIN = 90 // surfaced as "stale" if older than 90 min

onMounted(() => {
  if (!props.scanTime) return
  try {
    const ageMin =
      (Date.now() - new Date(props.scanTime).getTime()) / 1000 / 60
    stale.value = ageMin > REFRESH_WINDOW_MIN
  } catch {
    // ignore
  }
})
</script>

<template>
  <NAlert
    :type="stale ? 'warning' : 'info'"
    :show-icon="true"
    class="demo-banner"
    closable
  >
    <template #header>
      <span class="banner-title">
        🎭 Demo Mode
        <span v-if="loading" class="banner-meta">
          — <NSpin size="small" /> loading snapshot…
        </span>
        <span v-else-if="scanTimeHuman" class="banner-meta">
          — data refreshed hourly by GitHub Actions,
          <strong>last scan {{ scanTimeHuman }}</strong>
          <span v-if="stale" class="stale-flag"> (stale — pipeline may be down)</span>
        </span>
        <span v-else class="banner-meta">
          — connecting to raw.githubusercontent.com…
        </span>
      </span>
    </template>
    <div class="banner-body">
      <span>
        This is a read-only demo. Live trading, wallet connection, and order
        placement are disabled. The scanner table shows the most recent hourly
        snapshot pushed by CI to the <code>gh-pages</code> branch.
      </span>
      <a
        v-if="source"
        :href="source"
        target="_blank"
        rel="noreferrer noopener"
        class="source-link"
      >
        view raw snapshot ↗
      </a>
    </div>
  </NAlert>
</template>

<style scoped>
.demo-banner {
  margin: 12px 16px 0;
}

.banner-title {
  font-weight: 600;
}

.banner-meta {
  font-weight: 400;
  color: var(--n-text-color-3, inherit);
  margin-left: 4px;
}

.stale-flag {
  color: var(--n-color-warning, #f0a020);
  font-weight: 600;
}

.banner-body {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  font-size: 13px;
  margin-top: 4px;
}

.banner-body code {
  background: var(--n-color-target, rgba(127, 127, 127, 0.15));
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 12px;
}

.source-link {
  white-space: nowrap;
  text-decoration: none;
  opacity: 0.8;
}

.source-link:hover {
  opacity: 1;
}
</style>
