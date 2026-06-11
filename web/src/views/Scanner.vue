<template>
  <div class="scanner-page">
    <n-card class="table-card">
      <template #header>
        <n-space align="center" style="width: 100%; justify-content: space-between;">
          <n-text style="font-size: 16px; font-weight: 600;">{{ t('scanner.title') }}</n-text>
          <n-radio-group :value="strategy" @update:value="onStrategyChange" size="small">
            <n-radio-button value="pure">{{ t('scanner.pureFutures') }}</n-radio-button>
            <n-radio-button value="carry">{{ t('scanner.cashAndCarry') }}</n-radio-button>
            <n-radio-button value="unified">{{ t('scanner.unifiedCC') }}</n-radio-button>
          </n-radio-group>
        </n-space>
      </template>
      <template #header-extra>
        <n-tag v-if="refreshing" size="small" type="info" :bordered="false">{{ t('scanner.scanningFromExchanges') }}</n-tag>
        <n-tag v-else-if="lastScanLabel" size="small" type="success" :bordered="false">{{ lastScanLabel }}</n-tag>
      </template>

      <n-space align="center" style="margin-bottom: 12px" wrap>
        <n-button size="small" @click="handleTriggerScan" :loading="refreshing">
          <template #icon><n-icon><SearchOutline /></n-icon></template>
          {{ t('scanner.scanNow') }}
        </n-button>
        <n-button size="small" secondary @click="loadData">
          <template #icon><n-icon><RefreshOutline /></n-icon></template>
          {{ t('scanner.refresh') }}
        </n-button>
        <n-text depth="3" style="font-size:12px">{{ t('scanner.venues') }}</n-text>
        <n-select
          v-model:value="selectedVenues"
          :options="venueOptions"
          multiple
          size="small"
          style="min-width: 220px; max-width: 360px"
          :placeholder="t('scanner.selectVenues')"
        />
        <template v-if="strategy === 'pure'">
          <n-text depth="3" style="font-size:12px">{{ t('scanner.minNetEdge') }}</n-text>
          <n-input-number v-model:value="minEdgeFilter" :min="0" :max="100" :step="0.05" size="small" style="width:100px"><template #suffix>%</template></n-input-number>
          <n-radio-group v-model:value="intervalFilter" size="small">
            <n-radio-button value="all">{{ t('scanner.all') }}</n-radio-button>
            <n-radio-button value="same">{{ t('scanner.sameInterval') }}</n-radio-button>
            <n-radio-button value="cross">{{ t('scanner.cross') }} ⚠</n-radio-button>
          </n-radio-group>
        </template>
      </n-space>

      <n-spin :show="loading || refreshing">
        <!-- PURE FUTURES -->
        <template v-if="strategy === 'pure'">
          <n-grid :cols="4" :x-gap="16" :y-gap="16" style="margin-bottom:16px">
            <n-gi v-for="(card, i) in pureStatCards" :key="i">
              <n-card size="small">
                <div class="stat-card-inner">
                  <div class="stat-icon" :style="{ background: card.color + '22', color: card.color }">
                    <n-icon size="24"><component :is="card.icon" /></n-icon>
                  </div>
                  <div class="stat-info">
                    <n-text depth="3" style="font-size:12px">{{ card.label }}</n-text>
                    <n-text style="font-size:22px;font-weight:700">{{ card.value }}</n-text>
                  </div>
                </div>
              </n-card>
            </n-gi>
          </n-grid>
          <n-data-table v-if="pureRows.length > 0" :columns="pureColumns" :data="pureRows" :bordered="false" :scroll-x="1300" size="small" striped />
          <n-empty v-else :description="t('scanner.noPureFutures')" style="padding:40px 0" />
        </template>

        <!-- CASH & CARRY -->
        <template v-if="strategy === 'carry'">
          <n-grid :cols="4" :x-gap="16" :y-gap="16" style="margin-bottom:16px">
            <n-gi v-for="(card, i) in carryStatCards" :key="i">
              <n-card size="small">
                <div class="stat-card-inner">
                  <div class="stat-icon" :style="{ background: card.color + '22', color: card.color }">
                    <n-icon size="24"><component :is="card.icon" /></n-icon>
                  </div>
                  <div class="stat-info">
                    <n-text depth="3" style="font-size:12px">{{ card.label }}</n-text>
                    <n-text style="font-size:22px;font-weight:700">{{ card.value }}</n-text>
                  </div>
                </div>
              </n-card>
            </n-gi>
          </n-grid>
          <n-empty v-if="carryVenues.length === 0 && !loading" :description="t('scanner.carryRequiresScan')" style="padding:40px 0" />
          <div v-for="ven in carryVenues" :key="ven.venue" style="margin-bottom:12px">
            <n-card :title="ven.venue.toUpperCase()" size="small">
              <template #header-extra>
                <n-tag size="small" :bordered="false">{{ ven.total_pairs }} {{ t('scanner.pairs') }}</n-tag>
              </template>
              <n-data-table
                :columns="carryColumns"
                :data="[...(ven.forward || []), ...(ven.reverse || [])]"
                :bordered="false" :scroll-x="500" size="small" striped
              />
            </n-card>
          </div>
        </template>

        <!-- UNIFIED C&C -->
        <template v-if="strategy === 'unified'">
          <n-grid :cols="4" :x-gap="16" :y-gap="16" style="margin-bottom:16px">
            <n-gi v-for="(card, i) in unifiedStatCards" :key="i">
              <n-card size="small">
                <div class="stat-card-inner">
                  <div class="stat-icon" :style="{ background: card.color + '22', color: card.color }">
                    <n-icon size="24"><component :is="card.icon" /></n-icon>
                  </div>
                  <div class="stat-info">
                    <n-text depth="3" style="font-size:12px">{{ card.label }}</n-text>
                    <n-text style="font-size:22px;font-weight:700">{{ card.value }}</n-text>
                  </div>
                </div>
              </n-card>
            </n-gi>
          </n-grid>
          <n-data-table v-if="unifiedRows.length > 0" :columns="unifiedColumns" :data="unifiedRows" :bordered="false" :scroll-x="800" size="small" striped />
          <n-empty v-else :description="t('scanner.unifiedRequiresScan')" style="padding:40px 0" />
        </template>
      </n-spin>
    </n-card>

    <n-modal v-model:show="showOpenModal" preset="card" :title="t('scanner.openPosition')" style="width: 420px">
      <n-form label-placement="left" label-width="110" size="small">
        <n-form-item :label="t('scanner.pair')">
          <n-text strong>{{ openTarget?.base }}/USDT — long {{ openTarget?.long_venue }}, short {{ openTarget?.short_venue }}</n-text>
        </n-form-item>
        <n-form-item :label="t('scanner.amountUsdt')">
          <n-input-number v-model:value="openAmount" :min="10" :step="100" style="width: 100%" />
        </n-form-item>
        <n-form-item :label="t('scanner.dryRun')">
          <n-switch v-model:value="openDryRun" />
          <n-text v-if="!openDryRun" type="error" style="margin-left: 12px; font-size: 12px">{{ t('scanner.realOrdersWarning') }}</n-text>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button size="small" @click="showOpenModal = false">{{ t('scanner.cancel') }}</n-button>
          <n-button size="small" :type="openDryRun ? 'primary' : 'error'" :loading="opening" @click="confirmOpen">
            {{ openDryRun ? t('scanner.openDryRun') : t('scanner.openLive') }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, h } from 'vue'
import { NCard, NGrid, NGi, NDataTable, NButton, NSpace, NText, NIcon, NSpin, NTag, NEmpty, NInputNumber, NRadioGroup, NRadioButton, NModal, NForm, NFormItem, NSwitch, NSelect, useMessage, type DataTableColumns, type SelectOption } from 'naive-ui'
import { RefreshOutline, SearchOutline, TrendingUpOutline, FlashOutline, AnalyticsOutline } from '@vicons/ionicons5'
import { post, useWebSocket, type ScannerOpportunities, type CarryVenue, type CarryCand, type UnifiedCarryCand, type WsMessage } from '@/composables/useApi'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const ALL_VENUES = ['binance', 'bitget', 'bybit', 'okx', 'hyperliquid', 'aster', 'lighter'] as const
const DEX_VENUES = new Set(['hyperliquid', 'aster', 'lighter'])
// Carry / Unified need spot + borrow — perp-only DEX venues are excluded server-side
const CARRY_CAPABLE = new Set(['binance', 'bitget', 'bybit', 'okx'])

const message = useMessage()

type Strategy = 'pure' | 'carry' | 'unified'
const strategy = ref<Strategy>('pure')
const loading = ref(false)
const refreshing = ref(false)
const lastScanLabel = ref('')

// Data stores per strategy
const pureData = ref<ScannerOpportunities | null>(null)
const carryData = ref<CarryVenue[]>([])
const unifiedData = ref<UnifiedCarryCand[]>([])

const minEdgeFilter = ref<number>(0)
const intervalFilter = ref<'all' | 'same' | 'cross'>('all')
const selectedVenues = ref<string[]>(['binance', 'bitget', 'bybit', 'okx', 'hyperliquid'])
const venueOptions = computed<SelectOption[]>(() => ALL_VENUES.map((v) => ({
  label: v.charAt(0).toUpperCase() + v.slice(1) + (DEX_VENUES.has(v) ? ' (DEX)' : ''),
  value: v,
  disabled: strategy.value !== 'pure' && !CARRY_CAPABLE.has(v),
})))

async function loadStrategyVenues() {
  try {
    const resp = await fetch('/api/settings/strategy')
    const json = await resp.json()
    const venues = json.data?.scan_venues
    if (Array.isArray(venues) && venues.length > 0) {
      selectedVenues.value = venues
    }
  } catch { /* ignore */ }
}

// venue id → trade_capable (scan-only venues get a disabled Open button)
const venueCaps = ref<Record<string, { trade: boolean; reason: string }>>({})

async function loadVenueCapabilities() {
  try {
    const resp = await fetch('/api/settings/venues')
    const json = await resp.json()
    if (json.success && Array.isArray(json.data)) {
      const caps: Record<string, { trade: boolean; reason: string }> = {}
      for (const v of json.data) {
        caps[v.id] = { trade: v.trade_capable !== false, reason: v.trade_reason || '' }
      }
      venueCaps.value = caps
    }
  } catch { /* ignore */ }
}

function rowTradeBlock(row: PureRow): string {
  for (const vid of [row.long_venue, row.short_venue]) {
    const cap = venueCaps.value[vid]
    if (cap && !cap.trade) return `${vid}: scan-only${cap.reason ? ` (${cap.reason})` : ''}`
  }
  return ''
}

function venuesQuery(): string {
  return selectedVenues.value.length > 0 ? selectedVenues.value.join(',') : ''
}

function applyScanData(st: Strategy, data: unknown) {
  if (st === 'pure') pureData.value = data as ScannerOpportunities
  else if (st === 'carry') carryData.value = Array.isArray(data) ? data : []
  else unifiedData.value = Array.isArray(data) ? data as UnifiedCarryCand[] : []
}

function formatScanTime(iso: string | null | undefined) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return t('scanner.lastScan', { time: d.toLocaleTimeString() })
  } catch {
    return ''
  }
}

async function refreshScanLabel(st: Strategy) {
  try {
    const resp = await fetch(`/api/scanner/status?strategy=${st}`)
    const json = await resp.json()
    lastScanLabel.value = formatScanTime(json.data?.last_scan_time)
  } catch {
    lastScanLabel.value = ''
  }
}

async function waitForScanData(st: Strategy, maxMs = 120000) {
  const start = Date.now()
  while (Date.now() - start < maxMs) {
    await new Promise((r) => setTimeout(r, 2000))
    const resp = await fetch(`/api/scanner/opportunities?strategy=${st}`)
    const json = await resp.json()
    if (json.success && json.has_data) {
      applyScanData(st, json.data)
      await refreshScanLabel(st)
      return true
    }
    const status = await fetch(`/api/scanner/status?strategy=${st}`).then((r) => r.json())
    if (!status.data?.scanning) break
  }
  return false
}

async function loadData(s?: Strategy | MouseEvent, autoScan = true) {
  const st = (s && typeof s !== 'object') ? s : strategy.value
  loading.value = true
  try {
    const resp = await fetch(`/api/scanner/opportunities?strategy=${st}`)
    const json = await resp.json()
    if (json.success) {
      applyScanData(st, json.data)
      await refreshScanLabel(st)
    }
    const available = json.live ?? false
    const hasData = json.has_data ?? false
    if (!available) {
      message.warning(t('scanner.scannerUnavailable'))
      return
    }
    if (autoScan && !hasData && !refreshing.value) {
      const status = await fetch(`/api/scanner/status?strategy=${st}`).then((r) => r.json())
      if (status.data?.scanning) {
        refreshing.value = true
        try {
          const ok = await waitForScanData(st)
          if (!ok) message.warning(t('scanner.scanFailed'))
        } finally {
          refreshing.value = false
        }
      } else {
        await handleTriggerScan()
      }
    }
  } catch { /* ignore */ }
  finally { loading.value = false }
}

async function handleTriggerScan() {
  refreshing.value = true
  try {
    const vq = venuesQuery()
    const st = strategy.value
    const url = `/api/scanner/trigger?strategy=${st}${vq ? `&venues=${encodeURIComponent(vq)}` : ''}`
    const resp = await fetch(url, { method: 'POST' })
    const json = await resp.json()
    if (json.success) {
      applyScanData(st, json.data)
      await refreshScanLabel(st)
      message.success(t('scanner.scanComplete'))
    } else if (json.error === 'Scan already in progress') {
      const ok = await waitForScanData(st)
      if (ok) message.success(t('scanner.scanComplete'))
      else message.warning(t('scanner.scanFailed'))
    } else {
      message.error(json.error || t('scanner.scanFailed'))
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : t('scanner.scanFailed'))
  }
  finally { refreshing.value = false }
}

function onStrategyChange(val: Strategy) {
  strategy.value = val
  loadData(val, true)
}

// ---- WebSocket live updates (pushed by background scanner) ----
function onWsMessage(msg: WsMessage) {
  if (msg.event !== 'scanner.update') return
  const d = msg.data as Record<string, any>
  if (d.strategy === 'carry') {
    carryData.value = Array.isArray(d.data) ? d.data : []
    refreshScanLabel('carry')
  } else if (d.strategy === 'unified') {
    unifiedData.value = Array.isArray(d.data) ? d.data : []
    refreshScanLabel('unified')
  } else if (d.forward || d.reverse) {
    pureData.value = d as ScannerOpportunities
    refreshScanLabel('pure')
  }
}
const ws = useWebSocket(onWsMessage)

// ---- Open position dialog ----
const showOpenModal = ref(false)
const opening = ref(false)
const openTarget = ref<PureRow | null>(null)
const openAmount = ref<number>(1000)
const openDryRun = ref(true)

function showOpenDialog(row: PureRow) {
  openTarget.value = row
  showOpenModal.value = true
}

async function confirmOpen() {
  const tgt = openTarget.value
  if (!tgt) return
  opening.value = true
  try {
    await post('/positions/open', {
      base: tgt.base,
      long_venue: tgt.long_venue,
      short_venue: tgt.short_venue,
      amount_usd: openAmount.value,
      direction: tgt.direction.toLowerCase(),
      dry_run: openDryRun.value,
    })
    message.success(t('scanner.opened', { base: tgt.base, mode: openDryRun.value ? 'dry-run' : 'LIVE' }))
    showOpenModal.value = false
  } catch (e) {
    message.error(e instanceof Error ? e.message : t('scanner.failedToOpen'))
  } finally {
    opening.value = false
  }
}

// ---- Pure Futures ----
interface PureRow { base: string; direction: string; long_venue: string; short_venue: string; net_edge_pct: number; mark_spread_pct: number; real_edge_pct: number; annual_apy_pct: number; long_interval_h: number; short_interval_h: number; settle_mismatch: boolean }

function toPureRow(i: import('@/composables/useApi').OpportunityItem, direction: string): PureRow {
  return {
    base: i.base, direction, long_venue: i.long_venue, short_venue: i.short_venue,
    net_edge_pct: i.net_edge_pct ?? 0, mark_spread_pct: i.mark_spread_pct ?? 0,
    real_edge_pct: (i.net_edge_pct ?? 0) - (i.mark_spread_pct ?? 0), annual_apy_pct: i.annual_apy_pct ?? 0,
    long_interval_h: i.long_interval_h ?? 8, short_interval_h: i.short_interval_h ?? 8,
    settle_mismatch: i.settle_mismatch ?? (i.same_interval === false),
  }
}

const pureRows = computed<PureRow[]>(() => {
  const d = pureData.value
  if (!d) return []
  let all = [
    ...(d.forward || []).map((i) => toPureRow(i, 'Forward')),
    ...(d.reverse || []).map((i) => toPureRow(i, 'Reverse')),
  ]
  if (minEdgeFilter.value > 0) all = all.filter((r) => r.net_edge_pct >= minEdgeFilter.value)
  if (intervalFilter.value === 'same') all = all.filter((r) => !r.settle_mismatch)
  else if (intervalFilter.value === 'cross') all = all.filter((r) => r.settle_mismatch)
  return all
})

function fmtInterval(h: number): string {
  return h >= 1 ? `${Math.round(h)}h` : `${Math.round(h * 60)}m`
}

function renderVenue(venue: string) {
  if (!DEX_VENUES.has(venue)) return venue
  return h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '4px' } }, [
    venue,
    h(NTag, { size: 'tiny', type: 'info', bordered: false }, { default: () => 'DEX' }),
  ])
}

const genuine = computed(() => pureRows.value.filter((r) => r.real_edge_pct > 0).length)
const bestReal = computed(() => pureRows.value.length > 0 ? Math.max(...pureRows.value.map((r) => r.real_edge_pct)) : 0)
const pureStatCards = computed(() => [
  { label: t('scanner.scannedPairs'), value: pureData.value?.total_assets_scanned ?? 0, icon: SearchOutline, color: '#2080f0' },
  { label: t('scanner.opportunities'), value: pureRows.value.length, icon: FlashOutline, color: '#18a058' },
  { label: t('scanner.genuineArb'), value: genuine.value, icon: TrendingUpOutline, color: genuine.value > 0 ? '#18a058' : '#d03050' },
  { label: t('scanner.bestRealEdge'), value: bestReal.value.toFixed(4) + '%', icon: AnalyticsOutline, color: bestReal.value > 0 ? '#18a058' : '#d03050' },
])

const pureColumns = computed<DataTableColumns<PureRow>>(() => [
  { title: t('scanner.pair'), key: 'base', width: 90, render: (row) => `${row.base}/USDT` },
  { title: t('scanner.dir'), key: 'direction', width: 75, render: (row) => h(NTag, { size: 'small', type: row.direction === 'Forward' ? 'success' : 'warning', bordered: false }, { default: () => row.direction }) },
  { title: t('scanner.long'), key: 'long_venue', width: 105, render: (row) => renderVenue(row.long_venue) },
  { title: t('scanner.short'), key: 'short_venue', width: 105, render: (row) => renderVenue(row.short_venue) },
  { title: t('scanner.interval'), key: 'interval', width: 90, render: (row) => {
    const label = `${fmtInterval(row.long_interval_h)}/${fmtInterval(row.short_interval_h)}`
    return h(NTag, { size: 'small', type: row.settle_mismatch ? 'warning' : 'default', bordered: false },
      { default: () => row.settle_mismatch ? `⚠ ${label}` : label })
  } },
  { title: t('scanner.fundingEdge'), key: 'net_edge_pct', width: 105, sorter: (a, b) => a.net_edge_pct - b.net_edge_pct,
    render: (row) => h(NText, { type: row.net_edge_pct > 0 ? 'success' : 'error', strong: true }, { default: () => row.net_edge_pct.toFixed(4) + '%' }) },
  { title: t('scanner.markSpread'), key: 'mark_spread_pct', width: 105, sorter: (a, b) => a.mark_spread_pct - b.mark_spread_pct,
    render: (row) => { const v = row.mark_spread_pct; const c = v > row.net_edge_pct ? '#d03050' : v > row.net_edge_pct * 0.5 ? '#f0a020' : undefined; return h('span', { style: { color: c } }, v.toFixed(4) + '%') } },
  { title: t('scanner.realEdge'), key: 'real_edge_pct', width: 105, sorter: (a, b) => a.real_edge_pct - b.real_edge_pct, defaultSortOrder: 'descend',
    render: (row) => h(NText, { type: row.real_edge_pct > 0.05 ? 'success' : row.real_edge_pct > 0 ? 'warning' : 'error', strong: true }, { default: () => (row.real_edge_pct > 0 ? '+' : '') + row.real_edge_pct.toFixed(4) + '%' }) },
  { title: t('scanner.apy'), key: 'annual_apy_pct', width: 75, sorter: (a, b) => a.annual_apy_pct - b.annual_apy_pct,
    render: (row) => h(NText, { strong: true }, { default: () => row.annual_apy_pct.toFixed(0) + '%' }) },
  { title: t('scanner.action'), key: 'actions', width: 80,
    render: (row) => {
      const block = rowTradeBlock(row)
      return h(NButton, {
        size: 'tiny', type: 'primary', secondary: true,
        disabled: !!block,
        title: block || undefined,
        onClick: () => showOpenDialog(row),
      }, { default: () => t('scanner.open') })
    } },
])

// ---- Cash & Carry ----
const carryVenues = computed(() => carryData.value)
const carryTotalFwd = computed(() => carryVenues.value.reduce((s, v) => s + (v.forward?.length ?? 0), 0))
const carryTotalRev = computed(() => carryVenues.value.reduce((s, v) => s + (v.reverse?.length ?? 0), 0))
const carryStatCards = computed(() => [
  { label: t('scanner.venuesScanned'), value: carryVenues.value.length, icon: SearchOutline, color: '#2080f0' },
  { label: t('scanner.forwardSpotPerp'), value: carryTotalFwd.value, icon: TrendingUpOutline, color: '#18a058' },
  { label: t('scanner.reverseBorrowPerp'), value: carryTotalRev.value, icon: FlashOutline, color: '#f0a020' },
  { label: t('scanner.strategy'), value: 'Cash & Carry', icon: AnalyticsOutline, color: '#8a2be2' },
])

const carryColumns = computed<DataTableColumns<CarryCand>>(() => [
  { title: t('scanner.pair'), key: 'base', width: 90, render: (row) => `${row.base}/USDT` },
  { title: t('scanner.type'), key: 'type', width: 80, render: (row) => h(NTag, { size: 'small', type: row.has_spot !== undefined ? 'success' : 'warning', bordered: false }, { default: () => row.has_spot !== undefined ? t('scanner.forward') : t('scanner.reverse') }) },
  { title: t('scanner.rate'), key: 'rate_pct', width: 100, render: (row) => (row.rate_pct ?? 0).toFixed(4) + '%' },
  { title: t('scanner.ann'), key: 'annual_pct', width: 80, render: (row) => (row.annual_pct ?? 0).toFixed(0) + '%' },
  { title: t('scanner.spotBorrow'), key: 'spot', width: 100, render: (row) => row.has_spot === true ? 'Spot: $' + (row.spot_price ?? 0).toFixed(2) : row.borrowable === true ? 'Borrow' : 'N/A' },
  { title: t('scanner.netEdge'), key: 'net_edge_pct', width: 100, sorter: (a, b) => (a.net_edge_pct ?? 0) - (b.net_edge_pct ?? 0),
    render: (row) => h(NText, { type: (row.net_edge_pct ?? 0) > 0 ? 'success' : 'error', strong: true }, { default: () => (row.net_edge_pct ?? 0).toFixed(4) + '%' }) },
])

// ---- Unified C&C ----
const unifiedRows = computed(() => unifiedData.value)
const unifiedCrossVenue = computed(() => unifiedRows.value.filter((u) => !u.same_venue).length)
const unifiedSameVenue = computed(() => unifiedRows.value.filter((u) => u.same_venue).length)
const unifiedStatCards = computed(() => [
  { label: t('scanner.routes'), value: unifiedRows.value.length, icon: SearchOutline, color: '#2080f0' },
  { label: t('scanner.crossVenue'), value: unifiedCrossVenue.value, icon: FlashOutline, color: '#18a058' },
  { label: t('scanner.sameVenue'), value: unifiedSameVenue.value, icon: AnalyticsOutline, color: '#f0a020' },
  { label: t('scanner.mode'), value: 'Unified', icon: TrendingUpOutline, color: '#8a2be2' },
])

const unifiedColumns = computed<DataTableColumns<UnifiedCarryCand>>(() => [
  { title: t('scanner.pair'), key: 'base', width: 90, render: (row) => `${row.base}/USDT` },
  { title: t('scanner.dir'), key: 'direction', width: 75, render: (row) => h(NTag, { size: 'small', type: row.direction === 'forward' ? 'success' : 'warning', bordered: false }, { default: () => row.direction }) },
  { title: t('scanner.futures'), key: 'futures_venue', width: 90 },
  { title: t('scanner.spot'), key: 'spot_venue', width: 90 },
  { title: t('scanner.funding'), key: 'funding_rate_pct', width: 90, render: (row) => (row.funding_rate_pct ?? 0).toFixed(4) + '%' },
  { title: t('scanner.fee'), key: 'fee_pct', width: 75, render: (row) => (row.fee_pct ?? 0).toFixed(3) + '%' },
  { title: t('scanner.netEdge'), key: 'net_edge_pct', width: 100, sorter: (a, b) => (a.net_edge_pct ?? 0) - (b.net_edge_pct ?? 0), defaultSortOrder: 'descend',
    render: (row) => h(NText, { type: (row.net_edge_pct ?? 0) > 0 ? 'success' : 'error', strong: true }, { default: () => (row.net_edge_pct ?? 0).toFixed(4) + '%' }) },
  { title: t('scanner.annual'), key: 'annual_pct', width: 80, render: (row) => (row.annual_pct ?? 0).toFixed(0) + '%' },
])

onMounted(() => {
  loadStrategyVenues()
  loadVenueCapabilities()
  loadData()
  ws.connect()
})
onUnmounted(() => ws.disconnect())
</script>

<style scoped>
.scanner-page { display: flex; flex-direction: column; gap: 16px; height: 100%; }
.stat-card-inner { display: flex; align-items: center; gap: 16px; }
.stat-icon { width: 48px; height: 48px; border-radius: 10px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.stat-info { display: flex; flex-direction: column; min-width: 0; flex: 1; overflow: hidden; }
.stat-info :deep(.n-text) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.table-card { flex: 1; min-height: 0; }
.table-card :deep(.n-card__content) { padding: 16px 20px; }
</style>
