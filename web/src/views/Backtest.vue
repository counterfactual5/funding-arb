<script setup lang="ts">
import { ref, onMounted, h, computed } from 'vue'
import {
  NCard, NGrid, NGi, NButton, NForm, NFormItem, NInputNumber, NInput, NText,
  NStatistic, NDataTable, NEmpty, NIcon, NSelect, NSpace, useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { PlayOutline, RefreshOutline } from '@vicons/ionicons5'
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
const historyBases = ref<string>('')
const historyDays = ref<number>(90)
const historyVenues = ref<string[]>([])
const selectedVenues = ref<string[]>([])
const running = ref(false)
const latestResult = ref<BacktestResult | null>(null)

const hasResult = computed(() => latestResult.value !== null)

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
    <n-grid :cols="24" :x-gap="16">
      <n-gi :span="6">
        <n-card :title="t('backtest.title')" size="small">
          <n-form label-placement="top" size="small">
            <n-form-item :label="t('backtest.historicalBases')">
              <n-input v-model:value="historyBases" :placeholder="t('backtest.historicalBasesPlaceholder')" />
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
            <n-form-item :label="t('backtest.minSpread')">
              <n-input-number v-model:value="minSpread" :min="0" :max="100" style="width: 100%" />
            </n-form-item>
            <n-form-item :label="t('backtest.exitEdge')">
              <n-input-number v-model:value="exitEdge" :min="0" :max="100" style="width: 100%" />
            </n-form-item>
            <n-form-item :label="t('backtest.maxPositions')">
              <n-input-number v-model:value="maxPositions" :min="1" :max="20" style="width: 100%" />
            </n-form-item>
            <n-form-item>
              <n-button type="primary" block :loading="running" @click="runBacktest">
                <template #icon><n-icon><PlayOutline /></n-icon></template>
                {{ t('backtest.runBacktest') }}
              </n-button>
            </n-form-item>
          </n-form>
        </n-card>
      </n-gi>
      <n-gi :span="18">
        <n-card :title="t('backtest.results')" size="small" class="results-card">
          <template #header-extra>
            <n-space>
              <n-button v-if="hasResult" size="small" secondary @click="latestResult = null">
                {{ t('backtest.backToHistory') }}
              </n-button>
              <n-button size="small" secondary @click="history.refresh">
                <template #icon><n-icon><RefreshOutline /></n-icon></template>
                {{ t('backtest.refreshHistory') }}
              </n-button>
            </n-space>
          </template>

          <!-- Latest result summary -->
          <div v-if="hasResult" class="result-section">
            <n-grid :cols="4" :x-gap="12" :y-gap="12" class="stat-grid">
              <n-gi>
                <n-statistic :label="t('backtest.totalPnl')">
                  <template #default>
                    <n-text :type="latestResult!.summary.total_pnl_usd >= 0 ? 'success' : 'error'" :style="{ fontSize: '20px', fontWeight: 700 }">
                      ${{ latestResult!.summary.total_pnl_usd.toFixed(2) }}
                    </n-text>
                  </template>
                </n-statistic>
              </n-gi>
              <n-gi>
                <n-statistic :label="t('backtest.annualized')">
                  <n-text :style="{ fontSize: '20px', fontWeight: 700, color: '#f0a020' }">{{ latestResult!.summary.annualized_pct.toFixed(2) }}%</n-text>
                </n-statistic>
              </n-gi>
              <n-gi>
                <n-statistic :label="t('backtest.sharpe')">
                  <n-text :style="{ fontSize: '20px', fontWeight: 700, color: '#2080f0' }">{{ latestResult!.summary.sharpe.toFixed(2) }}</n-text>
                </n-statistic>
              </n-gi>
              <n-gi>
                <n-statistic :label="t('backtest.winRate')">
                  <n-text :style="{ fontSize: '20px', fontWeight: 700, color: '#18a058' }">{{ (latestResult!.summary.win_rate * 100).toFixed(1) }}%</n-text>
                </n-statistic>
              </n-gi>
            </n-grid>
            <n-grid :cols="4" :x-gap="12" :y-gap="12" class="stat-grid" style="margin-top: 12px">
              <n-gi>
                <n-statistic :label="t('backtest.maxDrawdown')">
                  <n-text :style="{ fontSize: '16px', fontWeight: 600, color: '#d03050' }">{{ latestResult!.summary.max_drawdown_pct.toFixed(2) }}%</n-text>
                </n-statistic>
              </n-gi>
              <n-gi>
                <n-statistic :label="t('backtest.totalTrades')">
                  <n-text :style="{ fontSize: '16px', fontWeight: 600 }">{{ latestResult!.summary.total_trades }}</n-text>
                </n-statistic>
              </n-gi>
              <n-gi>
                <n-statistic :label="t('backtest.avgHold')">
                  <n-text :style="{ fontSize: '16px', fontWeight: 600 }">{{ latestResult!.summary.avg_hold_days.toFixed(1) }}d</n-text>
                </n-statistic>
              </n-gi>
              <n-gi>
                <n-statistic :label="t('backtest.totalReturn')">
                  <n-text :style="{ fontSize: '16px', fontWeight: 600, color: '#18a058' }">{{ latestResult!.summary.total_pnl_pct.toFixed(2) }}%</n-text>
                </n-statistic>
              </n-gi>
            </n-grid>

            <n-card v-if="equityChartOption" :title="t('backtest.equityCurve')" size="small" style="margin-top: 12px">
              <v-chart :option="equityChartOption" autoresize style="height: 240px; width: 100%" />
            </n-card>

            <!-- Trades table -->
            <n-card :title="t('backtest.tradeDetails')" size="small" style="margin-top: 12px">
              <n-data-table
                v-if="latestResult!.trades.length > 0"
                :columns="tradeColumns"
                :data="latestResult!.trades"
                :bordered="false"
                :scroll-x="800"
                size="small"
                striped
                :max-height="200"
              />
              <n-empty v-else :description="t('backtest.noTrades')" style="padding: 20px 0" />
            </n-card>
          </div>

          <!-- Placeholder -->
          <div v-if="!hasResult && history.data.value?.length === 0" class="placeholder">
            <n-text depth="3">{{ t('backtest.placeholder') }}</n-text>
          </div>

          <!-- History -->
          <div v-if="history.data.value && history.data.value.length > 0 && !hasResult" class="history-section">
            <n-card :title="t('backtest.historyTitle')" size="small">
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
                size="small"
                striped
              />
            </n-card>
          </div>
        </n-card>
      </n-gi>
    </n-grid>
  </div>
</template>

<style scoped>
.backtest-page { height: 100%; }
.results-card { min-height: 200px; }
.placeholder {
  height: calc(100vh - 300px);
  display: flex; align-items: center; justify-content: center;
}
.stat-grid { margin-bottom: 8px; }
</style>
