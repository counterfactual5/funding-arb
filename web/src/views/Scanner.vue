<template>
  <div class="scanner-page">
    <n-card class="table-card">
      <template #header>
        <div class="filter-toolbar">
          <!-- 第一行：标题 + 策略切换 | 状态 + 扫描按钮 -->
          <div class="toolbar-row">
            <n-text class="toolbar-title">{{ t('scanner.title') }}</n-text>
            <n-radio-group :value="strategy" @update:value="onStrategyChange" size="small">
              <n-radio-button value="pure">{{ t('scanner.pureFutures') }}</n-radio-button>
              <n-radio-button value="carry">{{ t('scanner.cashAndCarry') }}</n-radio-button>
              <n-radio-button value="unified">{{ t('scanner.unifiedCC') }}</n-radio-button>
            </n-radio-group>

            <div class="toolbar-spacer" />

            <n-tag v-if="refreshing" size="small" type="info" :bordered="false" class="status-tag">{{ t('scanner.scanningFromExchanges') }}</n-tag>
            <n-tag v-else-if="lastScanLabel" size="small" type="success" :bordered="false" class="status-tag">{{ lastScanLabel }}</n-tag>
            <n-button size="small" type="primary" ghost @click="handleTriggerScan" :loading="refreshing" class="action-btn">
              <template #icon><n-icon size="14"><SearchOutline /></n-icon></template>
              {{ t('scanner.scanNow') }}
            </n-button>
          </div>

          <!-- 第二行：筛选条件（可换行） -->
          <div class="toolbar-row toolbar-filters">
            <div class="filter-group">
              <n-text depth="3" class="filter-label">{{ t('scanner.venues') }}</n-text>
              <n-select
                :value="selectedVenues"
                :options="venueOptions"
                :render-label="renderVenueOptionLabel"
                :render-tag="renderVenueTag"
                :style="venueSelectStyle"
                multiple
                size="small"
                class="venue-filter"
                :placeholder="t('scanner.selectVenues')"
                @update:value="handleVenuesChange"
              />
            </div>

            <template v-if="strategy === 'pure'">
              <div class="filter-group filter-group-bordered">
                <n-text depth="3" class="filter-label">{{ t('scanner.minNetEdge') }}</n-text>
                <n-input-number
                  v-model:value="minEdgeFilter"
                  :min="0"
                  :max="100"
                  :step="0.05"
                  :show-button="false"
                  size="small"
                  class="edge-input"
                  :style="edgeInputStyle"
                  @input="onEdgeInput"
                >
                  <template #suffix>%</template>
                </n-input-number>
              </div>

              <div class="filter-group filter-group-bordered">
                <n-radio-group v-model:value="intervalFilter" size="small" class="interval-group">
                  <n-radio-button value="all" class="interval-btn">{{ t('scanner.all') }}</n-radio-button>
                  <n-radio-button value="same" class="interval-btn">{{ t('scanner.sameInterval') }}</n-radio-button>
                  <n-radio-button value="cross" class="interval-btn">
                    {{ t('scanner.cross') }}
                    <span class="cross-badge">⚠</span>
                  </n-radio-button>
                </n-radio-group>
              </div>
            </template>
          </div>
        </div>
      </template>

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
import { ref, onMounted, onUnmounted, computed, h, watch } from 'vue'
import { NCard, NGrid, NGi, NDataTable, NButton, NSpace, NText, NIcon, NSpin, NTag, NEmpty, NInputNumber, NRadioGroup, NRadioButton, NModal, NForm, NFormItem, NSwitch, NSelect, NTooltip, useMessage, type DataTableColumns, type SelectOption, type SelectGroupOption } from 'naive-ui'
import { SearchOutline, TrendingUpOutline, FlashOutline, AnalyticsOutline } from '@vicons/ionicons5'
import { post, useWebSocket, type ScannerOpportunities, type CarryVenue, type CarryCand, type UnifiedCarryCand, type WsMessage } from '@/composables/useApi'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const CEX_VENUES = ['binance', 'bitget', 'bybit', 'okx'] as const
const PERP_DEX_VENUES = ['hyperliquid', 'aster', 'lighter', 'edgex'] as const
const DEX_VENUES = new Set(['hyperliquid', 'aster', 'lighter', 'edgex'])
// Carry / Unified need spot + borrow — perp-only DEX venues are excluded server-side
const CARRY_CAPABLE = new Set(['binance', 'bitget', 'bybit', 'okx'])

const message = useMessage()

function colTitle(labelKey: string, tipKey: string) {
  return () => h(NTooltip, { trigger: 'hover' }, {
    trigger: () => h('span', { class: 'col-title-tip' }, t(labelKey)),
    default: () => t(tipKey),
  })
}

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
// Default to CEX only — DEX venues (hyperliquid/aster/lighter/edgex) are opt-in
const DEFAULT_VENUES = [...CEX_VENUES]
const selectedVenues = ref<string[]>([...DEFAULT_VENUES])
const lastScannedVenues = ref<string[]>([])

// Track raw input text so the width adapts while typing (e.g. "0." before it becomes a valid number)
const edgeInputText = ref('0')

function onEdgeInput(val: string | null) {
  edgeInputText.value = val || '0'
}

// Keep text in sync when the value changes via +/- buttons or programmatic updates
watch(minEdgeFilter, (v) => {
  edgeInputText.value = (v ?? 0).toString()
})

// Adaptive width: ch units track digit count; no stepper buttons (show-button=false)
// so the inner vertical divider cannot overlap the typed number.
const edgeInputStyle = computed(() => {
  const chars = Math.max(edgeInputText.value.length, 1)
  const widthCh = chars + 3 // room for "%" suffix + padding
  return {
    width: `max(4.5rem, ${widthCh}ch)`,
    minWidth: '4.5rem',
    maxWidth: '12rem',
  }
})

const venueOptions = computed<Array<SelectOption | SelectGroupOption>>(() => [
  {
    label: 'CEX',
    type: 'group',
    children: CEX_VENUES.map((v) => ({
      label: v.charAt(0).toUpperCase() + v.slice(1),
      value: v,
      disabled: strategy.value !== 'pure' && !CARRY_CAPABLE.has(v),
    })),
  },
  {
    label: 'DEX',
    type: 'group',
    children: PERP_DEX_VENUES.map((v) => ({
      label: v.charAt(0).toUpperCase() + v.slice(1),
      value: v,
      disabled: strategy.value !== 'pure' && !CARRY_CAPABLE.has(v),
    })),
  },
])

// Dynamically resize the venue select based on selected labels so every chip is visible and easy to close
const venueSelectStyle = computed(() => {
  const labels = selectedVenues.value.map((v) => v.charAt(0).toUpperCase() + v.slice(1))
  const charW = 7 // approximate per-char width at 11px font
  const chipW = labels.reduce((sum, l) => sum + l.length * charW + 32, 0) // 32 = close button + padding
  const gap = Math.max(labels.length - 1, 0) * 4
  const width = labels.length === 0 ? 180 : Math.min(560, Math.max(180, chipW + gap + 34)) // 34 = arrow + inner padding
  return { width: `${width}px`, maxWidth: '100%' }
})

// Render option label inside the dropdown — group headers are styled differently
function renderVenueOptionLabel(option: SelectOption | SelectGroupOption) {
  if ('type' in option && option.type === 'group') {
    return h('div', { class: 'venue-section-label' }, String(option.label))
  }
  return h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '6px' } }, [
    option.label as string,
    DEX_VENUES.has(option.value as string)
      ? h('span', { class: 'dex-mini-tag' }, 'DEX')
      : null,
  ])
}

// Compact tag renderer for the selected chips in the input
function renderVenueTag({ option, handleClose }: { option: SelectOption; handleClose: () => void }) {
  const isDex = DEX_VENUES.has(option.value as string)
  return h('div', { class: 'venue-chip' }, [
    h('span', { class: isDex ? 'venue-chip-name dex' : 'venue-chip-name' }, option.label as string),
    h('span', { class: 'venue-chip-close', onClick: handleClose }, '×'),
  ])
}

async function loadStrategyVenues() {
  try {
    const resp = await fetch('/api/settings/strategy')
    const json = await resp.json()
    const venues = json.data?.scan_venues
    if (Array.isArray(venues) && venues.length > 0) {
      selectedVenues.value = venuesForStrategy(strategy.value, venues)
    }
  } catch { /* ignore */ }
}

function venuesForStrategy(st: Strategy, venues: string[]): string[] {
  if (st === 'pure') return venues.length > 0 ? [...venues] : [...DEFAULT_VENUES]
  const cex = venues.filter((v) => CARRY_CAPABLE.has(v))
  return cex.length > 0 ? cex : [...DEFAULT_VENUES]
}

function effectiveVenues(): string[] {
  const v = selectedVenues.value
  return v.length > 0 ? v : [...DEFAULT_VENUES]
}

function venuesMatch(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false
  const set = new Set(a)
  return b.every((x) => set.has(x))
}

function scanVenuesForStrategy(st: Strategy): string[] {
  return venuesForStrategy(st, effectiveVenues())
}

function cacheMatchesSelection(st: Strategy): boolean {
  if (lastScannedVenues.value.length === 0) return false
  return venuesMatch(lastScannedVenues.value, scanVenuesForStrategy(st))
}

let _venuesWatchTimer: ReturnType<typeof setTimeout> | null = null

// Handles manual changes in the venue dropdown (both selection and removal of tags)
function handleVenuesChange(val: string[]) {
  if (val.length === 0) {
    message.warning(t('scanner.venuesRequired'))
    return
  }
  selectedVenues.value = val

  if (strategy.value === 'pure' && val.length < 2) {
    message.info(t('scanner.venuesNeedTwo'))
    return
  }

  // Rescan only the selected venues (debounced)
  if (refreshing.value) return
  if (_venuesWatchTimer) clearTimeout(_venuesWatchTimer)
  _venuesWatchTimer = setTimeout(() => {
    handleTriggerScan()
  }, 400)
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

function venuesQuery(st?: Strategy): string {
  return scanVenuesForStrategy(st ?? strategy.value).join(',')
}

function applyScanData(st: Strategy, data: unknown) {
  if (st === 'pure') {
    const d = data as ScannerOpportunities
    pureData.value = d
    lastScannedVenues.value = Array.isArray(d?.venues) ? [...d.venues] : scanVenuesForStrategy(st)
  } else if (st === 'carry') {
    const rows = Array.isArray(data) ? data as CarryVenue[] : []
    carryData.value = rows
    lastScannedVenues.value = rows.map((v) => v.venue).filter(Boolean)
  } else {
    unifiedData.value = Array.isArray(data) ? data as UnifiedCarryCand[] : []
    lastScannedVenues.value = scanVenuesForStrategy(st)
  }
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
  const vq = venuesQuery(st)
  while (Date.now() - start < maxMs) {
    await new Promise((r) => setTimeout(r, 2000))
    const resp = await fetch(
      `/api/scanner/opportunities?strategy=${st}&venues=${encodeURIComponent(vq)}`,
    )
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
    const vq = venuesQuery(st)
    const resp = await fetch(
      `/api/scanner/opportunities?strategy=${st}&venues=${encodeURIComponent(vq)}`,
    )
    const json = await resp.json()
    const available = json.live ?? false
    const hasData = json.has_data ?? false
    if (!available) {
      message.warning(t('scanner.scannerUnavailable'))
      return
    }
    if (json.success && hasData && !json.venues_mismatch) {
      applyScanData(st, json.data)
      await refreshScanLabel(st)
    } else if (json.venues_mismatch) {
      // Cached scan was for different venues — don't show stale rows
      if (st === 'pure') pureData.value = null
      else if (st === 'carry') carryData.value = []
      else unifiedData.value = []
      lastScannedVenues.value = []
    }
    if (autoScan && (!hasData || json.venues_mismatch) && !refreshing.value) {
      if (st === 'pure' && scanVenuesForStrategy(st).length < 2) return
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
  const st = strategy.value
  if (st === 'pure' && scanVenuesForStrategy(st).length < 2) {
    message.info(t('scanner.venuesNeedTwo'))
    return
  }
  refreshing.value = true
  try {
    const vq = venuesQuery(st)
    const url = `/api/scanner/trigger?strategy=${st}&venues=${encodeURIComponent(vq)}`
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
  selectedVenues.value = venuesForStrategy(val, selectedVenues.value)
  loadData(val, true)
}

// ---- WebSocket live updates (pushed by background scanner) ----
function onWsMessage(msg: WsMessage) {
  if (msg.event !== 'scanner.update') return
  const d = msg.data as Record<string, any>
  if (d.recalc_fees && d.data && typeof d.data === 'object') {
    const payload = d.data as Record<string, unknown>
    if (payload.pure) applyScanData('pure', payload.pure)
    if (payload.carry) applyScanData('carry', payload.carry)
    if (payload.unified) applyScanData('unified', payload.unified)
    return
  }
  if (d.strategy === 'carry') {
    const rows = Array.isArray(d.data) ? d.data as CarryVenue[] : []
    const scanned = rows.map((v) => v.venue).filter(Boolean)
    if (strategy.value === 'carry' && venuesMatch(scanned, scanVenuesForStrategy('carry'))) {
      carryData.value = rows
      lastScannedVenues.value = [...scanned]
      refreshScanLabel('carry')
    }
  } else if (d.strategy === 'unified') {
    unifiedData.value = Array.isArray(d.data) ? d.data : []
    refreshScanLabel('unified')
  } else if (d.forward || d.reverse) {
    const scanned = Array.isArray(d.venues) ? d.venues as string[] : []
    if (strategy.value === 'pure' && venuesMatch(scanned, scanVenuesForStrategy('pure'))) {
      pureData.value = d as ScannerOpportunities
      lastScannedVenues.value = [...scanned]
      refreshScanLabel('pure')
    }
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
  // Rows are already scoped to the venues used in the last scan — only apply UI filters here
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
  { label: t('scanner.scannedPairs'), value: cacheMatchesSelection('pure') ? (pureData.value?.total_assets_scanned ?? 0) : '—', icon: SearchOutline, color: '#2080f0' },
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
  { title: colTitle('scanner.fundingEdge', 'scanner.fundingEdgeTip'), key: 'net_edge_pct', width: 105, sorter: (a, b) => a.net_edge_pct - b.net_edge_pct,
    render: (row) => h(NText, { type: row.net_edge_pct > 0 ? 'success' : 'error', strong: true }, { default: () => row.net_edge_pct.toFixed(4) + '%' }) },
  { title: colTitle('scanner.markSpread', 'scanner.markSpreadTip'), key: 'mark_spread_pct', width: 105, sorter: (a, b) => a.mark_spread_pct - b.mark_spread_pct,
    render: (row) => { const v = row.mark_spread_pct; const c = v > row.net_edge_pct ? '#d03050' : v > row.net_edge_pct * 0.5 ? '#f0a020' : undefined; return h('span', { style: { color: c } }, v.toFixed(4) + '%') } },
  { title: colTitle('scanner.realEdge', 'scanner.realEdgeTip'), key: 'real_edge_pct', width: 105, sorter: (a, b) => a.real_edge_pct - b.real_edge_pct, defaultSortOrder: 'descend',
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

onMounted(async () => {
  await loadStrategyVenues()
  loadVenueCapabilities()
  loadData()
  ws.connect()
})
onUnmounted(() => {
  ws.disconnect()
  if (_venuesWatchTimer) clearTimeout(_venuesWatchTimer)
})
</script>

<style scoped>
.scanner-page { display: flex; flex-direction: column; gap: 16px; height: 100%; }
.stat-card-inner { display: flex; align-items: center; gap: 16px; }
.stat-icon { width: 48px; height: 48px; border-radius: 10px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.stat-info { display: flex; flex-direction: column; min-width: 0; flex: 1; overflow: hidden; }
.stat-info :deep(.n-text) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.table-card { flex: 1; min-height: 0; }
.table-card :deep(.n-card__content) { padding: 16px 20px; }
.table-card :deep(.n-card-header) { padding: 12px 20px; }

/* ---- Filter toolbar layout ---- */
.filter-toolbar {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.toolbar-row {
  display: flex;
  align-items: center;
  gap: 10px;
  row-gap: 8px;
  flex-wrap: wrap;
  min-height: 32px;
}

.toolbar-title {
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
}

/* 把状态/按钮推到行尾；空间不足时允许换行而不是挤压 */
.toolbar-spacer {
  flex: 1 1 auto;
  min-width: 8px;
}

.status-tag {
  flex-shrink: 0;
  max-width: 260px;
}
.status-tag :deep(.n-tag__content) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.filter-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.filter-label {
  font-size: 11px;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.45);
  white-space: nowrap;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  line-height: 1;
}

/* 用左边框代替竖线分隔符，避免 flex 换行时竖线叠在输入框上 */
.filter-group-bordered {
  padding-left: 10px;
  border-left: 1px solid rgba(255, 255, 255, 0.08);
}

.action-btn {
  font-weight: 500;
  line-height: 1;
  flex-shrink: 0;
}
.action-btn :deep(.n-button) {
  min-height: 32px;
}

/* ---- Venue select ---- */
.venue-filter {
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.venue-filter:hover {
  border-color: rgba(24, 160, 88, 0.4) !important;
}
.venue-filter :deep(.n-base-selection) {
  border-radius: 6px;
  min-height: 32px;
}
/* Force single-line tags; don't let long names wrap the whole input */
.venue-filter :deep(.n-base-selection-tags) {
  flex-wrap: nowrap;
  overflow: hidden;
  gap: 3px;
}
.venue-filter :deep(.n-base-selection-tag-wrapper) {
  flex-shrink: 0;
}
/* Hide the default tag border, use our own chip */
.venue-filter :deep(.n-tag) {
  border: none !important;
  background: transparent !important;
  padding: 0 !important;
  height: 22px !important;
}

/* Custom compact chip — width adapts to label length */
:deep(.venue-chip) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 4px 0 8px;
  border-radius: 4px;
  background: rgba(24, 160, 88, 0.15);
  border: 1px solid rgba(24, 160, 88, 0.3);
  font-size: 11px;
  line-height: 1;
  color: #18a058;
  white-space: nowrap;
  overflow: visible;
}
:deep(.venue-chip-name) {
  white-space: nowrap;
}
:deep(.venue-chip-name.dex) {
  font-style: italic;
  opacity: 0.9;
}
:deep(.venue-chip-close) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 3px;
  font-size: 14px;
  line-height: 1;
  color: rgba(24, 160, 88, 0.6);
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
}
:deep(.venue-chip-close:hover) {
  background: rgba(24, 160, 88, 0.2);
  color: #18a058;
}

/* DEX mini tag in dropdown options */
:deep(.dex-mini-tag) {
  display: inline-block;
  padding: 1px 5px;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.3px;
  border-radius: 3px;
  background: rgba(64, 158, 255, 0.15);
  color: #409eff;
  line-height: 1;
}

/* Edge input — width via ch units; show-button=false removes stepper divider */
.edge-input {
  flex-shrink: 0;
}
.edge-input :deep(.n-input__suffix) {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.4);
  padding-left: 2px;
}
.edge-input :deep(.n-input-wrapper) {
  min-height: 32px;
}
.edge-input :deep(.n-input__input-el) {
  min-width: 1.5ch;
  text-align: left;
}

/* Interval radio group */
.interval-group :deep(.n-radio-button) {
  font-size: 12px;
  padding: 0 12px;
  line-height: 30px;
  height: 32px;
}
.interval-group :deep(.n-radio-button--checked) {
  font-weight: 500;
}

.cross-badge {
  margin-left: 3px;
  font-size: 10px;
  opacity: 0.7;
}

/* ---- Dropdown panel ---- */
:deep(.n-base-select-menu) {
  border-radius: 10px;
  margin-top: 4px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.4);
  overflow: hidden;
}
:deep(.n-base-select-option) {
  border-radius: 6px;
  margin: 2px 4px;
  padding: 8px 10px;
  font-size: 13px;
  transition: background-color 0.15s ease;
}
:deep(.n-base-select-option:hover) {
  background-color: rgba(24, 160, 88, 0.08);
}
:deep(.n-base-select-option--selected) {
  background-color: rgba(24, 160, 88, 0.15) !important;
  color: #18a058 !important;
  font-weight: 500;
}

/* Section divider inside the dropdown */
.venue-section-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: rgba(255, 255, 255, 0.35);
  font-weight: 600;
  padding: 4px 10px 2px;
  pointer-events: none;
}

.col-title-tip {
  cursor: help;
  border-bottom: 1px dashed rgba(255, 255, 255, 0.25);
}
</style>
