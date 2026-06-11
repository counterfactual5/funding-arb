<script setup lang="ts">
import { h, onMounted, computed, ref } from 'vue'
import {
  NCard, NGrid, NGi, NDataTable, NButton, NStatistic, NIcon, NSpin, NEmpty, NTag, NSelect, NModal, NSpace, NText, useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { WalletOutline, PieChartOutline, RefreshOutline, OpenOutline } from '@vicons/ionicons5'
import { getPositions, post, type PositionItem } from '@/composables/useApi'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const message = useMessage()
const positions = getPositions()

const statusFilter = ref<'open' | 'closed' | 'all'>('open')

const allItems = computed(() => positions.data.value ?? [])
const filteredItems = computed(() => {
  if (statusFilter.value === 'all') return allItems.value
  if (statusFilter.value === 'open') return allItems.value.filter((p) => p.status === 'open' || p.status === 'mock')
  return allItems.value.filter((p) => p.status === 'closed')
})

const summary = computed(() => {
  const items = allItems.value
  const openItems = items.filter((p) => p.status === 'open' || p.status === 'mock')
  const closedItems = items.filter((p) => p.status === 'closed')
  const totalTrade = items.reduce((s, p) => s + (p.trade_usd ?? p.amount_usd ?? 0), 0)
  return {
    openCount: openItems.length,
    closedCount: closedItems.length,
    totalCount: items.length,
    totalTrade,
  }
})

const emptyMessage = computed(() => {
  if (statusFilter.value === 'open') return t('positions.noOpenPositions')
  return t('positions.noPositions')
})

const summaryCards = computed(() => [
  { label: t('positions.openPositions'), value: summary.value.openCount, icon: OpenOutline, color: '#18a058' },
  { label: t('positions.closedPositions'), value: summary.value.closedCount, icon: PieChartOutline, color: '#808080' },
  { label: t('positions.totalTrade'), value: '$' + summary.value.totalTrade.toLocaleString(), icon: WalletOutline, color: '#f0a020' },
])

function formatTime(ts: string | number | undefined): string {
  if (!ts) return '—'
  let ms: number
  if (typeof ts === 'number') {
    ms = ts
  } else {
    ms = new Date(ts).getTime()
  }
  if (isNaN(ms)) return '—'
  return new Date(ms).toLocaleString(navigator.language, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatDuration(openedAt: string | number | undefined, closedAt?: string | number): string {
  if (!openedAt) return '—'
  let startMs: number
  if (typeof openedAt === 'number') startMs = openedAt
  else startMs = new Date(openedAt).getTime()
  if (isNaN(startMs)) return '—'

  let endMs = Date.now()
  if (closedAt) {
    if (typeof closedAt === 'number') endMs = closedAt
    else endMs = new Date(closedAt).getTime()
  }
  const diff = Math.max(0, endMs - startMs)
  const hours = Math.floor(diff / 3600000)
  const mins = Math.floor((diff % 3600000) / 60000)
  if (hours > 0) return `${hours}h ${mins}m`
  return `${mins}m`
}

const tableColumns = computed<DataTableColumns<PositionItem>>(() => [
  { title: t('positions.id'), key: 'id', width: 100, ellipsis: { tooltip: true } },
  { title: t('positions.pair'), key: 'base', width: 90, render: (row) => `${row.base}/USDT` },
  {
    title: t('positions.direction'),
    key: 'direction',
    width: 80,
    render: (row) => h(NTag, { size: 'small', type: row.direction === 'forward' ? 'success' : 'warning', bordered: false }, { default: () => row.direction }),
  },
  { title: 'Long', key: 'long_venue', width: 80 },
  { title: 'Short', key: 'short_venue', width: 80 },
  {
    title: t('positions.amount'),
    key: 'trade_usd',
    width: 100,
    render: (row) => '$' + (row.trade_usd ?? row.amount_usd ?? 0).toLocaleString(),
  },
  {
    title: t('positions.openSpread'),
    key: 'mark_spread_pct',
    width: 110,
    render: (row) => {
      const val = row.mark_spread_pct ?? row.open_spread_pct ?? 0
      // Real data: mark_spread_pct is a percentage already (e.g. 0.28 = 0.28%)
      // Mock data: open_spread_pct is decimal (e.g. 0.035 = 3.5%)
      const isReal = row.mark_spread_pct !== undefined
      return (isReal ? val : val * 100).toFixed(2) + '%'
    },
  },
  {
    title: t('positions.pnlUsdt'),
    key: 'pnl_usd',
    width: 110,
    render: (row) => {
      const pnl = row.unrealized_pnl_usd ?? row.pnl_usd
      if (pnl === undefined || pnl === null) return '—'
      const color = pnl >= 0 ? 'success' : 'error'
      return h(NText, { type: color, strong: true }, { default: () => (pnl >= 0 ? '+' : '') + pnl.toFixed(2) })
    },
  },
  {
    title: t('positions.opened'),
    key: 'opened_at',
    width: 130,
    render: (row) => formatTime(row.opened_at ?? row.open_time),
  },
  {
    title: t('positions.duration'),
    key: 'duration',
    width: 90,
    render: (row) => formatDuration(row.opened_at ?? row.open_time, row.closed_at),
  },
  {
    title: t('positions.statusCol'),
    key: 'status',
    width: 90,
    render: (row) => {
      const colorMap: Record<string, 'success' | 'warning' | 'error' | 'default'> = {
        open: 'success',
        mock: 'warning',
        closed: 'default',
      }
      return h(NTag, { size: 'small', type: colorMap[row.status] ?? 'default', bordered: false }, { default: () => row.status })
    },
  },
  {
    title: t('positions.action'),
    key: 'actions',
    width: 90,
    render: (row) => h(NButton, {
      size: 'tiny',
      type: 'error',
      secondary: true,
      disabled: row.status === 'closed',
      onClick: () => showCloseConfirm(row),
    }, { default: () => t('positions.close') }),
  },
])

const showCloseModal = ref(false)
const closing = ref(false)
const closeTarget = ref<PositionItem | null>(null)

function showCloseConfirm(row: PositionItem) {
  closeTarget.value = row
  showCloseModal.value = true
}

async function confirmClose() {
  const row = closeTarget.value
  if (!row) return
  closing.value = true
  try {
    await post(`/positions/${row.id}/close`, { reason: 'manual' })
    message.success(t('positions.closed', { base: row.base }))
    showCloseModal.value = false
    await positions.refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : t('positions.failedToClose'))
  } finally {
    closing.value = false
  }
}

onMounted(() => {
  positions.refresh()
})
</script>

<template>
  <div class="positions-page">
    <n-grid :cols="3" :x-gap="16" :y-gap="16" class="summary-row">
      <n-gi v-for="(card, i) in summaryCards" :key="i">
        <n-card size="small">
          <div class="summary-card-inner">
            <div class="summary-icon" :style="{ backgroundColor: card.color + '22', color: card.color }">
              <n-icon size="24"><component :is="card.icon" /></n-icon>
            </div>
            <n-statistic :label="card.label" :value="card.value" />
          </div>
        </n-card>
      </n-gi>
    </n-grid>

    <n-card :title="t('positions.title')">
      <template #header-extra>
        <n-space align="center">
          <n-text depth="3" style="font-size: 12px">{{ t('positions.status') }}</n-text>
          <n-select
            v-model:value="statusFilter"
            :options="[
              { label: t('positions.openFilter'), value: 'open' },
              { label: t('positions.closedFilter'), value: 'closed' },
              { label: t('positions.allFilter'), value: 'all' },
            ]"
            style="width: 100px"
            size="small"
          />
          <n-button size="small" secondary @click="positions.refresh">
            <template #icon><n-icon><RefreshOutline /></n-icon></template>
            {{ t('positions.refresh') }}
          </n-button>
        </n-space>
      </template>
      <n-spin :show="positions.loading.value">
        <n-data-table
          v-if="filteredItems.length > 0"
          :columns="tableColumns"
          :data="filteredItems"
          :bordered="false"
          :scroll-x="1060"
          size="small"
          striped
        />
        <n-empty v-else :description="emptyMessage" style="padding: 40px 0" />
      </n-spin>
    </n-card>

    <n-modal v-model:show="showCloseModal" preset="card" :title="t('positions.confirmClose')" style="width: 420px">
      <n-text v-if="closeTarget">
        {{ t('positions.closeMessage', { base: closeTarget.base, long: closeTarget.long_venue, short: closeTarget.short_venue }) }}
      </n-text>
      <template #footer>
        <n-space justify="end">
          <n-button size="small" @click="showCloseModal = false">{{ t('positions.cancel') }}</n-button>
          <n-button size="small" type="error" :loading="closing" @click="confirmClose">{{ t('positions.confirmCloseBtn') }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.positions-page { display: flex; flex-direction: column; gap: 16px; height: 100%; }
.summary-row { flex-shrink: 0; }
.summary-card-inner { display: flex; align-items: center; gap: 16px; }
.summary-icon {
  width: 48px; height: 48px; border-radius: 10px;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}
</style>
