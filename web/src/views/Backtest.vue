<script setup lang="ts">
import { ref, onMounted, h, computed } from 'vue'
import {
  NCard, NGrid, NGi, NButton, NForm, NFormItem, NInputNumber, NInput, NText,
  NDataTable, NEmpty, NIcon, NSelect, NSpace, NSwitch, useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { PlayOutline, RefreshOutline, TrendingUpOutline, TrendingDownOutline, PulseOutline, StatsChartOutline } from '@vicons/ionicons5'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import { getBacktestHistory, post, getVenues, type BacktestResult, type BacktestTrade } from '@/composables/useApi'
import { useI18n } from 'vue-i18n'

use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

const { t } = useI18n()
const message = useMessage()
const history = getBacktestHistory()
const venuesAPI = getVenues()

const venueOptions = computed(() =>
  (venuesAPI.data.value ?? []).map((v) => ({ label: v.name, value: v.id }))
)

const capital = ref<number>(100000)
const tradeUsd = ref<number>(5000)
const minSpread = ref<number>(0.08)
const exitEdge = ref<number>(0.02)
const maxPositions = ref<number>(3)
const minEdge = ref<number>(0.01)
const maxHoldingHours = ref<number>(720)
const allowMismatch = ref<boolean>(false)
const historyBases = ref<string>('')
const historyDays = ref<number>(90)
const historyVenues = ref<string[]>([])
const running = ref(false)
const latestResult = ref<BacktestResult | null>(null)

const hasResult = computed(() => latestResult.value !== null)

// ─── Summary cards (Positions-style) ───────────────────────────

function fmtUsd(v: number, signed = false): string {
  const prefix = signed ? (v >= 0 ? '+' : '') : ''
  return prefix + '$' + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })
}

function fmtPct(v: number, decimals = 2): string {
  return v.toFixed(decimals) + '%'
}

const summaryCards = computed(() => {
  if (!latestResult.value) return []
  const s = latestResult.value.summary
  return [
    {
      label: t('backtest.totalPnl'),
      value: fmtUsd(s.total_pnl_usd, true),
      icon: s.total_pnl_usd >= 0 ? TrendingUpOutline : TrendingDownOutline,
      color: s.total_pnl_usd >= 0 ? '#18a058' : '#d03050',
    },
    {
      label: t('backtest.totalReturn'),
      value: fmtPct(s.total_pnl_pct),
      icon: s.total_pnl_pct >= 0 ? TrendingUpOutline : TrendingDownOutline,
      color: s.total_pnl_pct >= 0 ? '#18a058' : '#d03050',
    },
    {
      label: t('backtest.annualized'),
      value: fmtPct(s.annualized_pct),
      icon: PulseOutline,
      color: '#f0a020',
    },
    {
      label: t('backtest.sharpe'),
      value: s.sharpe.toFixed(2),
      icon: StatsChartOutline,
      color: '#2080f0',
    },
    {
      label: t('backtest.winRate'),
      value: (s.win_rate * 100).toFixed(1) + '%',
      icon: s.win_rate >= 0.5 ? TrendingUpOutline : TrendingDownOutline,
      color: s.win_rate >= 0.5 ? '#18a058' : '#d03050',
    },
    {
      label: t('backtest.maxDrawdown'),
      value: fmtPct(s.max_drawdown_pct),
      icon: TrendingDownOutline,
      color: '#d03050',
    },
  ]
})

// ─── Equity curve ──────────────────────────────────────────────

const equityChartOption = computed(() => {
  const curve = latestResult.value?.equity_curve ?? []
  if (curve.length === 0) return null
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params: { dataIndex: number }[]) => {
        const idx = params[0]?.dataIndex ?? 0
        const pt = curve[idx]
        if (!pt) return ''
        return `${pt.ts}<br/>Equity: $${pt.equity.toLocaleString()}<br/>Open pairs: ${pt.open_pairs ?? 0}`
      },
    },
    grid: { left: 48, right: 16, top: 24, bottom: 32 },
    xAxis: {
      type: 'category',
      data: curve.map((p) => p.ts.slice(0, 10)),
      axisLabel: { fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { formatter: (v: number) => `$${(v / 1000).toFixed(0)}k` },
    },
    series: [
      {
        type: 'line',
        data: curve.map((p) => p.equity),
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2, color: '#18a058' },
        areaStyle: { color: 'rgba(24, 160, 88, 0.12)' },
      },
    ],
  }
})

// ─── Actions ───────────────────────────────────────────────────

async function runBacktest() {
  running.value = true
  try {
    const result = await post<BacktestResult>('/backtest/run', {
      jsonl_file: null,
      history_bases: historyBases.value || null,
      history_venues: historyVenues.value.length > 0 ? historyVenues.value.join(',') : null,
      history_days: historyDays.value,
      capital: capital.value,
      trade_usd: tradeUsd.value,
      min_spread: minSpread.value,
      exit_edge: exitEdge.value,
      max_positions: maxPositions.value,
      min_edge_pct: minEdge.value,
      max_holding_hours: maxHoldingHours.value,
      allow_mismatch: allowMismatch.value,
    })
    latestResult.value = result
    message.success(t('backtest.backtestComplete'))
    history.refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : t('backtest.backtestFailed'))
  } finally {
    running.value = false
  }
}

async function syncFromStrategy() {
  try {
    const resp = await fetch('/api/settings/strategy')
    const json = await resp.json()
    if (json.success && json.data) {
      const s = json.data
      minSpread.value = s.min_spread_annual ?? minSpread.value
      minEdge.value = s.min_edge_annual ?? minEdge.value
      exitEdge.value = s.min_edge_1h ?? exitEdge.value
      tradeUsd.value = s.trade_usd ?? tradeUsd.value
      maxPositions.value = s.max_positions ?? maxPositions.value
      allowMismatch.value = (s.min_edge_mismatch ?? 0) > 0
      message.success('Synced from strategy settings')
    }
  } catch {
    message.error('Failed to sync strategy settings')
  }
}

function loadHistoryResult(row: BacktestResult) {
  latestResult.value = row
}

const historyRowProps = (row: BacktestResult) => ({
  style: 'cursor: pointer',
  onClick: () => loadHistoryResult(row),
})

const tradeColumns = computed<DataTableColumns<BacktestTrade>>(() => [
  { title: t('backtest.pair'), key: 'base', width: 80, render: (row) => `${row.base}/USDT` },
  { title: t('backtest.direction'), key: 'direction', width: 90 },
  { title: t('backtest.longVenue'), key: 'long_venue', width: 120 },
  { title: t('backtest.shortVenue'), key: 'short_venue', width: 120 },
  { title: t('backtest.holdDays'), key: 'hold_days', width: 100, render: (row) => row.hold_days.toFixed(1) + 'd' },
  {
    title: t('backtest.pnlUsdt'), key: 'pnl_usd', width: 130,
    render: (row) => {
      const color = row.pnl_usd >= 0 ? 'success' : 'error'
      return h(NText, { type: color, strong: true }, { default: () => (row.pnl_usd >= 0 ? '+' : '') + row.pnl_usd.toFixed(2) })
    },
  },
])

onMounted(() => {
  history.refresh()
  venuesAPI.refresh()
})
</script>

<template>
  <div class="backtest-page">
    <!-- Parameters (uniform grid) -->
    <n-card size="small">
      <n-form label-placement="left" :label-width="110" size="small" :show-feedback="false">
        <div class="param-grid">
          <n-form-item :label="t('backtest.historicalBases')">
            <n-input v-model:value="historyBases" :placeholder="t('backtest.historicalBasesPlaceholder')" style="width: 100%" />
          </n-form-item>
          <n-form-item :label="t('backtest.historyDays')">
            <n-input-number v-model:value="historyDays" :min="1" :max="365" style="width: 100%" />
          </n-form-item>
          <n-form-item :label="t('backtest.venues')">
            <n-select v-model:value="historyVenues" :options="venueOptions" multiple style="width: 100%" />
          </n-form-item>
          <n-form-item :label="t('backtest.initialCapital')">
            <n-input-number v-model:value="capital" :min="1000" :step="10000" style="width: 100%" />
          </n-form-item>
          <n-form-item :label="t('backtest.tradeSize')">
            <n-input-number v-model:value="tradeUsd" :min="100" :step="1000" style="width: 100%" />
          </n-form-item>
          <n-form-item :label="t('backtest.maxPositions')">
            <n-input-number v-model:value="maxPositions" :min="1" :max="20" style="width: 100%" />
          </n-form-item>
          <n-form-item :label="t('backtest.minSpread')">
            <n-input-number v-model:value="minSpread" :min="0" :max="100" :step="0.01" style="width: 100%" />
          </n-form-item>
          <n-form-item :label="t('backtest.exitEdge')">
            <n-input-number v-model:value="exitEdge" :min="0" :max="100" :step="0.005" style="width: 100%" />
          </n-form-item>
          <n-form-item label="Min Edge">
            <n-input-number v-model:value="minEdge" :min="0" :max="10" :step="0.005" :precision="3" style="width: 100%" />
          </n-form-item>
          <n-form-item label="Max Hold (h)">
            <n-input-number v-model:value="maxHoldingHours" :min="1" :max="2160" :step="24" style="width: 100%" />
          </n-form-item>
          <n-form-item label="Cross-Interval">
            <n-switch v-model:value="allowMismatch" />
          </n-form-item>
          <div class="param-actions">
            <n-button secondary size="small" @click="syncFromStrategy">
              Sync from Strategy
            </n-button>
            <n-button type="primary" size="small" :loading="running" @click="runBacktest">
              <template #icon><n-icon><PlayOutline /></n-icon></template>
              {{ t('backtest.runBacktest') }}
            </n-button>
          </div>
        </div>
      </n-form>
    </n-card>

    <!-- Result summary cards (Positions-style) -->
    <n-grid v-if="hasResult" :cols="3" :x-gap="16" :y-gap="16" responsive="screen">
      <n-gi v-for="(card, i) in summaryCards" :key="i">
        <n-card size="small">
          <div class="summary-card-inner">
            <div class="summary-icon" :style="{ backgroundColor: card.color + '22', color: card.color }">
              <n-icon size="22"><component :is="card.icon" /></n-icon>
            </div>
            <div class="summary-stat">
              <n-text depth="3" class="summary-label">{{ card.label }}</n-text>
              <n-text strong class="summary-value">{{ card.value }}</n-text>
            </div>
          </div>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- Equity curve -->
    <n-card v-if="hasResult && equityChartOption" :title="t('backtest.equityCurve')" size="small">
      <template #header-extra>
        <n-space>
          <n-button size="small" secondary @click="latestResult = null">
            {{ t('backtest.backToHistory') }}
          </n-button>
          <n-button size="small" secondary @click="history.refresh">
            <template #icon><n-icon><RefreshOutline /></n-icon></template>
            {{ t('backtest.refreshHistory') }}
          </n-button>
        </n-space>
      </template>
      <v-chart :option="equityChartOption" autoresize style="height: 260px; width: 100%" />
    </n-card>

    <!-- Trade details -->
    <n-card v-if="hasResult" :title="t('backtest.tradeDetails')" size="small">
      <n-data-table
        v-if="latestResult!.trades.length > 0"
        :columns="tradeColumns"
        :data="latestResult!.trades"
        :bordered="false"
        :scroll-x="800"
        :max-height="400"
        virtual
        size="small"
        striped
      />
      <n-empty v-else :description="t('backtest.noTrades')" style="padding: 20px 0" />
    </n-card>

    <!-- Placeholder (no results yet) -->
    <n-card v-if="!hasResult && history.data.value?.length === 0" size="small" class="placeholder-card">
      <n-empty :description="t('backtest.placeholder')" style="padding: 60px 0" />
    </n-card>

    <!-- History list (no active result) -->
    <n-card v-if="!hasResult && history.data.value && history.data.value.length > 0" :title="t('backtest.historyTitle')" size="small">
      <template #header-extra>
        <n-button size="small" secondary @click="history.refresh">
          <template #icon><n-icon><RefreshOutline /></n-icon></template>
          {{ t('backtest.refreshHistory') }}
        </n-button>
      </template>
      <n-data-table
        :row-props="historyRowProps"
        :columns="[
          { title: t('backtest.id'), key: 'id', width: 150 },
          { title: t('backtest.runTime'), key: 'run_time', width: 200 },
          { title: t('backtest.pnl'), key: 'summary', width: 120, sorter: (a, b) => a.summary.total_pnl_usd - b.summary.total_pnl_usd, render: (row) => h(NText, { type: row.summary.total_pnl_usd >= 0 ? 'success' : 'error' }, { default: () => '$' + row.summary.total_pnl_usd.toFixed(2) }) },
          { title: t('backtest.sharpe'), key: 'summary', width: 80, render: (row) => row.summary.sharpe.toFixed(2) },
          { title: t('backtest.winRate'), key: 'summary', width: 100, render: (row) => (row.summary.win_rate * 100).toFixed(0) + '%' },
          { title: t('backtest.trades'), key: 'summary', width: 80, render: (row) => row.summary.total_trades },
        ]"
        :data="history.data.value"
        :bordered="false"
        :max-height="300"
        size="small"
        striped
      />
    </n-card>
  </div>
</template>

<style scoped>
.backtest-page { display: flex; flex-direction: column; gap: 16px; height: 100%; }

.param-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px 20px;
  align-items: flex-end;
}
.param-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  grid-column: -1 / -3;
  padding-top: 4px;
}

.summary-card-inner { display: flex; align-items: center; gap: 14px; }
.summary-icon {
  width: 44px; height: 44px; border-radius: 10px;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}
.summary-stat { display: flex; flex-direction: column; gap: 2px; }
.summary-label { font-size: 12px; }
.summary-value { font-size: 18px; font-weight: 700; }
</style>
