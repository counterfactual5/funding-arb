<template>
  <div class="scanner-page">
    <n-card class="table-card">
      <template #header>
        <div class="filter-toolbar">
          <!-- 标题独占一行居中 -->
          <div class="toolbar-title-row">
            <n-text class="toolbar-title">{{ t('scanner.title') }}</n-text>
          </div>

          <!-- 策略：下划线 Tab（主导航，与下方筛选区分） -->
          <div class="toolbar-tabs-row">
            <n-tabs
              :value="strategy"
              type="line"
              size="medium"
              class="strategy-tabs"
              @update:value="onStrategyChange"
            >
              <n-tab name="pure" :tab="t('scanner.pureFutures')" />
              <n-tab name="carry" :tab="t('scanner.cashAndCarry')" />
              <n-tab name="unified" :tab="t('scanner.unifiedCC')" />
            </n-tabs>
          </div>

          <!-- 筛选区：左侧可换行，右侧固定扫描/状态 -->
          <div class="toolbar-filters-row">
            <div class="filters-left">
              <div class="filter-group">
                <n-text depth="3" class="filter-label">{{ t('scanner.venues') }}</n-text>
                <div class="venue-filter-row">
                  <div class="venue-presets">
                    <button
                      type="button"
                      class="preset-chip"
                      :class="{ active: isVenuePresetActive('cex') }"
                      @click="applyVenuePreset('cex')"
                    >
                      {{ t('scanner.venuePresetCex') }}
                    </button>
                    <n-tooltip :disabled="strategy === 'pure'" trigger="hover">
                      <template #trigger>
                        <button
                          type="button"
                          class="preset-chip"
                          :class="{ active: isVenuePresetActive('dex') }"
                          :disabled="strategy !== 'pure'"
                          @click="applyVenuePreset('dex')"
                        >
                          {{ t('scanner.venuePresetDex') }}
                        </button>
                      </template>
                      {{ t('scanner.dexPureOnly') }}
                    </n-tooltip>
                    <button
                      v-if="strategy === 'pure'"
                      type="button"
                      class="preset-chip"
                      :class="{ active: isVenuePresetActive('all') }"
                      @click="applyVenuePreset('all')"
                    >
                      {{ t('scanner.venuePresetAll') }}
                    </button>
                  </div>
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
              </div>

              <template v-if="strategy === 'pure'">
                <div class="filter-group filter-group-bordered">
                  <n-text depth="3" class="filter-label">{{ t('scanner.minRealEdge') }}</n-text>
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
                  <n-text depth="3" class="filter-label">{{ t('scanner.interval') }}</n-text>
                  <div class="interval-row">
                    <n-select
                      v-model:value="intervalFilter"
                      :options="intervalOptions"
                      size="small"
                      class="interval-select"
                      :consistent-menu-width="false"
                    />
                    <router-link to="/docs/cross-interval#ci-blend" class="docs-link">{{ t('scanner.crossIntervalDocs') }}</router-link>
                  </div>
                </div>
              </template>

              <div class="filter-group actions-inline">
                <n-tag v-if="refreshing" size="small" type="info" :bordered="false" class="status-tag">{{ t('scanner.scanningFromExchanges') }}</n-tag>
                <n-tag v-else-if="lastScanLabel" size="small" type="success" :bordered="false" class="status-tag">{{ lastScanLabel }}</n-tag>
                <n-button size="small" type="primary" ghost @click="handleTriggerScan" :loading="refreshing" class="action-btn">
                  <template #icon><n-icon size="14"><SearchOutline /></n-icon></template>
                  {{ t('scanner.scanNow') }}
                </n-button>
              </div>
            </div>
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
          <n-data-table v-if="pureRows.length > 0" :columns="pureColumns" :data="pureRows" :bordered="false" :scroll-x="1480" :max-height="600" virtual size="small" striped />
          <n-empty v-else :description="t('scanner.noPureFutures')" style="padding:40px 0" />
        </template>

        <!-- CASH & CARRY -->
        <template v-if="strategy === 'carry'">
          <n-alert type="info" :bordered="false" style="margin-bottom: 12px" :show-icon="false">
            {{ t('scanner.scanOnlyStrategy') }}
            <router-link to="/docs/cash-and-carry" class="docs-link">{{ t('scanner.strategyDocsLink') }}</router-link>
          </n-alert>
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
                :data="carryRowsForVenue(ven)"
                :bordered="false" :scroll-x="580" size="small" striped
              />
            </n-card>
          </div>
        </template>

        <!-- UNIFIED C&C -->
        <template v-if="strategy === 'unified'">
          <n-alert type="info" :bordered="false" style="margin-bottom: 12px" :show-icon="false">
            {{ t('scanner.scanOnlyStrategy') }}
            <router-link to="/docs/unified-carry" class="docs-link">{{ t('scanner.strategyDocsLink') }}</router-link>
          </n-alert>
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
          <n-data-table v-if="unifiedRows.length > 0" :columns="unifiedColumns" :data="unifiedRows" :bordered="false" :scroll-x="800" :max-height="600" virtual size="small" striped />
          <n-empty v-else :description="t('scanner.unifiedRequiresScan')" style="padding:40px 0" />
        </template>
      </n-spin>
    </n-card>

    <n-modal v-model:show="showOpenModal" preset="card" :title="t('scanner.openPosition')" style="width: 460px">
      <n-form label-placement="left" label-width="110" size="small">
        <n-form-item :label="t('scanner.pair')">
          <n-text strong>{{ openModalSummary }}</n-text>
        </n-form-item>
        <n-form-item :label="t('scanner.amountUsdt')">
          <n-input-number v-model:value="openAmount" :min="10" :step="100" style="width: 100%" />
        </n-form-item>

        <!-- Execution mode selector (only for pure futures with wallet-capable venues) -->
        <n-form-item v-if="walletCapableVenues.length > 0" :label="t('scanner.openMode')">
          <n-radio-group v-model:value="openMode" size="small">
            <n-radio value="backend">{{ t('scanner.openModeBackend') }}</n-radio>
            <n-radio value="wallet" :disabled="!walletModeAvailable">{{ t('scanner.openModeWallet') }}</n-radio>
          </n-radio-group>
        </n-form-item>

        <!-- Wallet leg status -->
        <n-form-item v-if="openMode === 'wallet'" :label="' '" >
          <n-space vertical :size="4" style="width: 100%">
            <n-text v-for="leg in walletLegStatus" :key="leg.venue" depth="3" style="font-size: 12px">
              <n-tag size="tiny" :type="leg.connected ? 'success' : 'warning'" :bordered="false">
                {{ leg.venue }}
              </n-tag>
              {{ leg.connected ? t('scanner.walletLegReady') : t('scanner.walletLegNeedsKeys') }}
            </n-text>
          </n-space>
        </n-form-item>

        <n-form-item v-if="openMode === 'backend'" :label="t('scanner.dryRun')">
          <n-switch v-model:value="openDryRun" />
          <n-text v-if="!openDryRun" type="error" style="margin-left: 12px; font-size: 12px">{{ t('scanner.realOrdersWarning') }}</n-text>
        </n-form-item>

        <n-alert
          v-if="openTarget?.kind === 'pure' && openTarget.row.basis_risk_level !== 'clean'"
          :type="openTarget.row.basis_risk_level === 'high' ? 'error' : 'warning'"
          :title="openTarget.row.basis_risk_level === 'high' ? t('scanner.riskHigh') : t('scanner.riskCaution')"
          style="margin-top: 8px"
        >
          {{ pureRowRiskHint(openTarget.row) }}
          <template v-if="openTarget.row.basis_risk_level === 'high'">
            <br />
            <n-text depth="3" style="font-size: 12px">
              {{ t('scanner.riskHighDetail', { mark: openTarget.row.mark_spread_pct.toFixed(4), net: openTarget.row.net_edge_pct.toFixed(4), real: openTarget.row.real_edge_pct.toFixed(4) }) }}
            </n-text>
          </template>
        </n-alert>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button size="small" @click="showOpenModal = false">{{ t('scanner.cancel') }}</n-button>
          <n-button v-if="openMode === 'wallet'" size="small" type="info" :loading="opening" @click="confirmOpen">
            {{ t('scanner.walletSignOpen') }}
          </n-button>
          <n-button v-else size="small" :type="openDryRun ? 'primary' : 'error'" :loading="opening" @click="confirmOpen">
            {{ openDryRun ? t('scanner.openDryRun') : t('scanner.openLive') }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, h, watch } from 'vue'
import { NCard, NGrid, NGi, NDataTable, NButton, NSpace, NText, NIcon, NSpin, NTag, NEmpty, NInputNumber, NModal, NForm, NFormItem, NSwitch, NSelect, NTooltip, NTabs, NTab, NAlert, NRadioGroup, NRadio, useMessage, type DataTableColumns, type SelectOption, type SelectGroupOption } from 'naive-ui'
import { RouterLink } from 'vue-router'
import { SearchOutline, TrendingUpOutline, FlashOutline, AnalyticsOutline } from '@vicons/ionicons5'
import { post, useWebSocket, type ScannerOpportunities, type CarryVenue, type CarryCand, type UnifiedCarryCand, type WsMessage } from '@/composables/useApi'
import { WALLET_TRADE_VENUES } from '@/constants/walletTrade'
import { useWallet } from '@/composables/useWallet'
import { useI18n } from 'vue-i18n'
import { CEX_VENUE_RANK, DEX_VENUE_RANK } from '@/constants/venueOrder'

// Lazy-loaded wallet trade module — avoids pulling ethers + @nktkas/hyperliquid
// into the initial bundle (Scanner is eagerly loaded on the / route).
let _walletTradeModule: typeof import('@/composables/useWalletTrade') | null = null
async function getWalletTrade() {
  if (!_walletTradeModule) {
    _walletTradeModule = await import('@/composables/useWalletTrade')
  }
  return _walletTradeModule
}

const { t } = useI18n()

const CEX_VENUES = CEX_VENUE_RANK
const PERP_DEX_VENUES = DEX_VENUE_RANK
const DEX_VENUES = new Set<string>(DEX_VENUE_RANK)
// Carry / Unified need spot + borrow — perp-only DEX venues are excluded server-side
const CARRY_CAPABLE = new Set(['binance', 'bitget', 'bybit', 'okx'])

const message = useMessage()

function colTitle(labelKey: string, tipKey: string, docsPath?: string) {
  return () => h(NTooltip, { trigger: 'hover' }, {
    trigger: () => h('span', { class: 'col-title-tip' }, t(labelKey)),
    default: () => [
      h('span', null, t(tipKey)),
      docsPath
        ? h(RouterLink, { to: docsPath, class: 'docs-link', style: 'margin-left: 8px' }, () => t('scanner.columnDocsLink'))
        : null,
    ],
  })
}

type Strategy = 'pure' | 'carry' | 'unified'
const SCANNER_STRATEGY_KEY = 'scanner_strategy'

function loadSavedStrategy(): Strategy {
  try {
    const v = localStorage.getItem(SCANNER_STRATEGY_KEY)
    if (v === 'pure' || v === 'carry' || v === 'unified') return v
  } catch { /* private mode / SSR */ }
  return 'pure'
}

const strategy = ref<Strategy>(loadSavedStrategy())
const loading = ref(false)
const refreshing = ref(false)
const lastScanLabel = ref('')

// Data stores per strategy
const pureData = ref<ScannerOpportunities | null>(null)
const carryData = ref<CarryVenue[]>([])
const unifiedData = ref<UnifiedCarryCand[]>([])

const minEdgeFilter = ref<number>(0)
const intervalFilter = ref<'all' | 'same' | 'cross'>('all')

const intervalOptions = computed<SelectOption[]>(() => [
  { label: t('scanner.all'), value: 'all' },
  { label: t('scanner.sameInterval'), value: 'same' },
  { label: `${t('scanner.cross')} ⚠`, value: 'cross' },
])
// Default to CEX; DEX venues are opt-in via preset buttons
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

type VenuePreset = 'cex' | 'dex' | 'all'

function venuesForPreset(preset: VenuePreset): string[] {
  if (strategy.value !== 'pure') {
    return preset === 'dex' ? [] : [...CEX_VENUES]
  }
  if (preset === 'cex') return [...CEX_VENUES]
  if (preset === 'dex') return [...PERP_DEX_VENUES]
  return [...CEX_VENUES, ...PERP_DEX_VENUES]
}

function isVenuePresetActive(preset: VenuePreset): boolean {
  const expected = venuesForPreset(preset)
  if (expected.length === 0) return false
  const current = [...selectedVenues.value].sort()
  const exp = [...expected].sort()
  return current.length === exp.length && current.every((v, i) => v === exp[i])
}

function applyVenuePreset(preset: VenuePreset) {
  const next = venuesForPreset(preset)
  if (next.length === 0) return
  handleVenuesChange(next)
}

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
  try {
    localStorage.setItem(SCANNER_STRATEGY_KEY, val)
  } catch { /* ignore */ }
  selectedVenues.value = venuesForStrategy(val, selectedVenues.value)
  loadData(val, true)
}

// ---- WebSocket live updates (pushed by background scanner) ----
// Debounce WS messages: coalesce rapid updates into one render per ~200ms
// to avoid re-rendering hundreds of table rows on every push.
let _pendingWsMsg: WsMessage | null = null
let _wsDebounceTimer: ReturnType<typeof setTimeout> | null = null
function onWsMessage(msg: WsMessage) {
  if (msg.event !== 'scanner.update') return
  _pendingWsMsg = msg
  if (_wsDebounceTimer) return
  _wsDebounceTimer = setTimeout(() => {
    _wsDebounceTimer = null
    const m = _pendingWsMsg
    _pendingWsMsg = null
    if (m) _applyWsMessage(m)
  }, 200)
}
function _applyWsMessage(msg: WsMessage) {
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
type OpenTarget =
  | { kind: 'pure'; row: PureRow }
  | { kind: 'carry'; row: CarryRow }
  | { kind: 'unified'; row: UnifiedCarryCand }

const showOpenModal = ref(false)
const opening = ref(false)
const openTarget = ref<OpenTarget | null>(null)
const openAmount = ref<number>(1000)
const openDryRun = ref(true)
const openMode = ref<'backend' | 'wallet'>('backend')

// Synchronous wallet connection check — uses useWallet (no ethers import).
const { hasKeplr, hasMetaMask, keplrState, metamaskState } = useWallet()

// Fetch current mark price from backend scanner cache for size estimation.
async function fetchBasePrice(base: string): Promise<number> {
  try {
    const resp = await fetch('/api/scanner/opportunities?strategy=pure')
    const json = await resp.json()
    if (json.success && json.data) {
      const rows = [...(json.data.forward || []), ...(json.data.reverse || [])]
      const row = rows.find((r: any) => r.base === base)
      if (row) {
        const lm = row.long_mark || 0
        const sm = row.short_mark || 0
        if (lm > 0 && sm > 0) return (lm + sm) / 2
        if (lm > 0) return lm
        if (sm > 0) return sm
      }
    }
  } catch { /* ignore */ }
  return 0
}

function isWalletConnected(venue: string): boolean {
  if (venue === 'hyperliquid') return hasMetaMask.value && metamaskState.connected
  if (venue === 'dydx') return hasKeplr.value && keplrState.connected
  return false
}

const openModalSummary = computed(() => {
  const tgt = openTarget.value
  if (!tgt) return ''
  if (tgt.kind === 'pure') {
    const r = tgt.row
    return `${r.base}/USDT — long ${r.long_venue}, short ${r.short_venue}`
  }
  if (tgt.kind === 'carry') {
    const r = tgt.row
    return `${r.base}/USDT — ${r._direction} @ ${r._venue}`
  }
  const r = tgt.row
  return `${r.base}/USDT — ${r.direction} fut@${r.futures_venue} spot@${r.spot_venue}`
})

function showOpenDialog(target: OpenTarget) {
  openTarget.value = target
  openMode.value = 'backend'
  showOpenModal.value = true
}

/** Which venues in the current target support wallet signing? */
const walletCapableVenues = computed<string[]>(() => {
  const tgt = openTarget.value
  if (!tgt) return []
  if (tgt.kind === 'pure') return [tgt.row.long_venue, tgt.row.short_venue].filter(v => (WALLET_TRADE_VENUES as readonly string[]).includes(v))
  if (tgt.kind === 'carry') return []
  const r = tgt.row
  return [r.futures_venue, r.spot_venue].filter(v => v && (WALLET_TRADE_VENUES as readonly string[]).includes(v))
})

/** Can we use wallet mode for this pair? */
const walletModeAvailable = computed(() => walletCapableVenues.value.length > 0 && walletCapableVenues.value.some(v => isWalletConnected(v)))

/** Status per wallet-capable venue in this pair */
const walletLegStatus = computed(() => {
  return walletCapableVenues.value.map(v => ({
    venue: v,
    connected: isWalletConnected(v),
  }))
})

function venueTradeBlock(...venueIds: string[]): string {
  for (const vid of venueIds) {
    const cap = venueCaps.value[vid]
    if (cap && !cap.trade) return `${vid}: scan-only${cap.reason ? ` (${cap.reason})` : ''}`
  }
  return ''
}

async function confirmOpen() {
  const tgt = openTarget.value
  if (!tgt) return
  opening.value = true
  try {
    if (openMode.value === 'wallet') {
      await confirmOpenWallet(tgt)
      return
    }
    let body: Record<string, unknown>
    if (tgt.kind === 'pure') {
      const r = tgt.row
      body = {
        strategy: 'pure_futures',
        base: r.base,
        long_venue: r.long_venue,
        short_venue: r.short_venue,
        amount_usd: openAmount.value,
        direction: r.direction.toLowerCase(),
        dry_run: openDryRun.value,
      }
    } else if (tgt.kind === 'carry') {
      const r = tgt.row
      body = {
        strategy: 'carry',
        base: r.base,
        futures_venue: r._venue,
        spot_venue: r._venue,
        amount_usd: openAmount.value,
        direction: r._direction,
        dry_run: openDryRun.value,
      }
    } else {
      const r = tgt.row
      body = {
        strategy: 'unified',
        base: r.base,
        futures_venue: r.futures_venue,
        spot_venue: r.spot_venue,
        amount_usd: openAmount.value,
        direction: (r.direction || 'forward').toLowerCase(),
        dry_run: openDryRun.value,
      }
    }
    await post('/positions/open', body)
    const base = tgt.kind === 'pure' ? tgt.row.base : tgt.row.base
    message.success(t('scanner.opened', { base, mode: openDryRun.value ? 'dry-run' : 'LIVE' }))
    showOpenModal.value = false
  } catch (e) {
    message.error(e instanceof Error ? e.message : t('scanner.failedToOpen'))
  } finally {
    opening.value = false
  }
}

/**
 * Wallet-signed open: place one leg via browser wallet (HL/dYdX).
 * The other leg (CEX or non-wallet DEX) is NOT auto-placed here —
 * the user must manage it separately or use backend mode for both legs.
 */
async function confirmOpenWallet(tgt: OpenTarget) {
  if (tgt.kind !== 'pure') {
    message.error(t('scanner.walletNoEligible'))
    opening.value = false
    return
  }
  const r = tgt.row
  const base = r.base

  // Determine which leg to place via wallet
  const longWallet = (WALLET_TRADE_VENUES as readonly string[]).includes(r.long_venue) && isWalletConnected(r.long_venue)
  const shortWallet = (WALLET_TRADE_VENUES as readonly string[]).includes(r.short_venue) && isWalletConnected(r.short_venue)

  if (!longWallet && !shortWallet) {
    message.error(t('scanner.walletNoEligible'))
    opening.value = false
    return
  }

  // Get real price for size conversion (USD → base currency)
  let price = await fetchBasePrice(base)
  if (price <= 0) {
    message.error('Cannot determine market price for size calculation. Please scan first.')
    opening.value = false
    return
  }
  const size = openAmount.value / price

  try {
    // Lazy-load the wallet trade module (ethers + @nktkas/hyperliquid)
    const mod = await getWalletTrade()
    const { placeOrder: walletPlaceOrder, ensureAgent } = mod.useWalletTrade()

    // Ensure agent is ready for wallet venues
    for (const v of [r.long_venue, r.short_venue]) {
      if ((WALLET_TRADE_VENUES as readonly string[]).includes(v) && isWalletConnected(v)) {
        await ensureAgent(v)
      }
    }

    const results: Array<{ venue: string; success: boolean; error?: string }> = []

    // Place long leg if wallet-capable
    if (longWallet) {
      const result = await walletPlaceOrder({
        venue: r.long_venue,
        coin: base,
        isBuy: true,
        size,
        slippage: 0.01,
      })
      results.push({ venue: r.long_venue, ...result })
    }

    // Place short leg if wallet-capable
    if (shortWallet) {
      const result = await walletPlaceOrder({
        venue: r.short_venue,
        coin: base,
        isBuy: false,
        size,
        slippage: 0.01,
      })
      results.push({ venue: r.short_venue, ...result })
    }

    const allOk = results.every(r => r.success)
    if (allOk) {
      const venues = results.map(r => r.venue).join(', ')
      message.success(`${t('scanner.opened', { base, mode: 'wallet' })} (${venues})`)
      showOpenModal.value = false
    } else {
      const failed = results.filter(r => !r.success).map(r => `${r.venue}: ${r.error}`).join('; ')
      message.error(`${t('scanner.failedToOpen')} — ${failed}`)
    }

    // Warn about unplaced legs
    const unplacedLegs: string[] = []
    if (!longWallet && (WALLET_TRADE_VENUES as readonly string[]).includes(r.long_venue) === false) {
      unplacedLegs.push(`${r.long_venue} (long)`)
    }
    if (!shortWallet && (WALLET_TRADE_VENUES as readonly string[]).includes(r.short_venue) === false) {
      unplacedLegs.push(`${r.short_venue} (short)`)
    }
    if (unplacedLegs.length > 0) {
      message.warning(`${t('scanner.walletMixedMode')}: ${unplacedLegs.join(', ')}`)
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : t('scanner.failedToOpen'))
  } finally {
    opening.value = false
  }
}

// ---- Pure Futures ----
type BasisRiskLevel = 'clean' | 'caution' | 'high'
interface PureRow { base: string; direction: string; long_venue: string; short_venue: string; net_edge_pct: number; mark_spread_pct: number; real_edge_pct: number; annual_apy_pct: number; net_apy_pct: number; long_interval_h: number; short_interval_h: number; settle_mismatch: boolean; basis_risk_level: BasisRiskLevel }

function toPureRow(i: import('@/composables/useApi').OpportunityItem, direction: string): PureRow {
  return {
    base: i.base, direction, long_venue: i.long_venue, short_venue: i.short_venue,
    net_edge_pct: i.net_edge_pct ?? 0, mark_spread_pct: i.mark_spread_pct ?? 0,
    real_edge_pct: i.real_edge_pct ?? ((i.net_edge_pct ?? 0) - (i.mark_spread_pct ?? 0)), annual_apy_pct: i.annual_apy_pct ?? 0,
    net_apy_pct: i.net_apy_pct ?? 0,
    long_interval_h: i.long_interval_h ?? 8, short_interval_h: i.short_interval_h ?? 8,
    settle_mismatch: i.settle_mismatch ?? (i.same_interval === false),
    basis_risk_level: inferBasisRiskLevel(i),
  }
}

function inferBasisRiskLevel(i: { basis_risk_level?: BasisRiskLevel; real_edge_pct?: number; net_edge_pct?: number; mark_spread_pct?: number }): BasisRiskLevel {
  if (i.basis_risk_level === 'clean' || i.basis_risk_level === 'caution' || i.basis_risk_level === 'high') {
    return i.basis_risk_level
  }
  const real = i.real_edge_pct ?? ((i.net_edge_pct ?? 0) - (i.mark_spread_pct ?? 0))
  if (real >= 0) return real > 0.05 ? 'clean' : 'caution'
  return 'high'
}

function basisRiskTag(level: BasisRiskLevel) {
  if (level === 'clean') {
    return h(NTooltip, { trigger: 'hover' }, {
      trigger: () => h(NTag, { size: 'small', type: 'success', bordered: false }, { default: () => t('scanner.riskClean') }),
      default: () => t('scanner.riskCleanTip'),
    })
  }
  if (level === 'caution') {
    return h(NTooltip, { trigger: 'hover' }, {
      trigger: () => h(NTag, { size: 'small', type: 'warning', bordered: false }, { default: () => t('scanner.riskCaution') }),
      default: () => t('scanner.riskCautionTip'),
    })
  }
  return h(NTooltip, { trigger: 'hover' }, {
    trigger: () => h(NTag, { size: 'small', type: 'error', bordered: false }, { default: () => t('scanner.riskHigh') }),
    default: () => t('scanner.riskHighTip'),
  })
}

function pureRowRiskHint(row: PureRow): string {
  if (row.basis_risk_level === 'high') return t('scanner.riskHighTip')
  if (row.basis_risk_level === 'caution') return t('scanner.riskCautionTip')
  return ''
}

const pureRows = computed<PureRow[]>(() => {
  const d = pureData.value
  if (!d) return []
  // Rows are already scoped to the venues used in the last scan — only apply UI filters here
  let all = [
    ...(d.forward || []).map((i) => toPureRow(i, 'Forward')),
    ...(d.reverse || []).map((i) => toPureRow(i, 'Reverse')),
  ]
  if (minEdgeFilter.value > 0) all = all.filter((r) => r.real_edge_pct >= minEdgeFilter.value)
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
  { title: colTitle('scanner.fundingEdge', 'scanner.fundingEdgeTip', '/docs/fees-and-edge#fe-edges'), key: 'net_edge_pct', width: 105, sorter: (a, b) => a.net_edge_pct - b.net_edge_pct,
    render: (row) => h(NText, { type: row.net_edge_pct > 0 ? 'success' : 'error', strong: true }, { default: () => row.net_edge_pct.toFixed(4) + '%' }) },
  { title: colTitle('scanner.markSpread', 'scanner.markSpreadTip', '/docs/pure-futures#pf-mechanics'), key: 'mark_spread_pct', width: 105, sorter: (a, b) => a.mark_spread_pct - b.mark_spread_pct,
    render: (row) => { const v = row.mark_spread_pct; const c = v > row.net_edge_pct ? '#d03050' : v > row.net_edge_pct * 0.5 ? '#f0a020' : undefined; return h('span', { style: { color: c } }, v.toFixed(4) + '%') } },
  { title: colTitle('scanner.realEdge', 'scanner.realEdgeTip', '/docs/fees-and-edge#fe-edges'), key: 'real_edge_pct', width: 105, sorter: (a, b) => a.real_edge_pct - b.real_edge_pct, defaultSortOrder: 'descend',
    render: (row) => h(NText, { type: row.real_edge_pct > 0.05 ? 'success' : row.real_edge_pct > 0 ? 'warning' : 'error', strong: true }, { default: () => (row.real_edge_pct > 0 ? '+' : '') + row.real_edge_pct.toFixed(4) + '%' }) },
  { title: colTitle('scanner.basisRisk', 'scanner.basisRiskTip', '/docs/fees-and-edge#fe-edges'), key: 'basis_risk_level', width: 100,
    render: (row) => basisRiskTag(row.basis_risk_level) },
  { title: t('scanner.apy'), key: 'annual_apy_pct', width: 75, sorter: (a, b) => a.annual_apy_pct - b.annual_apy_pct,
    render: (row) => h(NText, { strong: true }, { default: () => row.annual_apy_pct.toFixed(0) + '%' }) },
  { title: colTitle('scanner.netApy', 'scanner.netApyTip'), key: 'net_apy_pct', width: 90, sorter: (a, b) => a.net_apy_pct - b.net_apy_pct,
    render: (row) => h(NText, { type: row.net_apy_pct > 0 ? 'success' : 'error' }, { default: () => row.net_apy_pct.toFixed(0) + '%' }) },
  { title: t('scanner.action'), key: 'actions', width: 80,
    render: (row) => {
      const block = rowTradeBlock(row)
      const riskHint = pureRowRiskHint(row)
      return h(NButton, {
        size: 'tiny', type: 'primary', secondary: true,
        disabled: !!block,
        title: block || riskHint || undefined,
        onClick: () => showOpenDialog({ kind: 'pure', row }),
      }, { default: () => t('scanner.open') })
    } },
])

// ---- Cash & Carry ----
type CarryRow = CarryCand & { _venue: string; _direction: 'forward' | 'reverse' }

function carryRowsForVenue(ven: CarryVenue): CarryRow[] {
  return [
    ...(ven.forward || []).map((r) => ({ ...r, _venue: ven.venue, _direction: 'forward' as const })),
    ...(ven.reverse || []).map((r) => ({ ...r, _venue: ven.venue, _direction: 'reverse' as const })),
  ]
}

const carryVenues = computed(() => carryData.value)
const carryTotalFwd = computed(() => carryVenues.value.reduce((s, v) => s + (v.forward?.length ?? 0), 0))
const carryTotalRev = computed(() => carryVenues.value.reduce((s, v) => s + (v.reverse?.length ?? 0), 0))
const carryStatCards = computed(() => [
  { label: t('scanner.venuesScanned'), value: carryVenues.value.length, icon: SearchOutline, color: '#2080f0' },
  { label: t('scanner.forwardSpotPerp'), value: carryTotalFwd.value, icon: TrendingUpOutline, color: '#18a058' },
  { label: t('scanner.reverseBorrowPerp'), value: carryTotalRev.value, icon: FlashOutline, color: '#f0a020' },
  { label: t('scanner.strategy'), value: 'Cash & Carry', icon: AnalyticsOutline, color: '#8a2be2' },
])

const carryColumns = computed<DataTableColumns<CarryRow>>(() => [
  { title: t('scanner.pair'), key: 'base', width: 90, render: (row) => `${row.base}/USDT` },
  { title: t('scanner.type'), key: 'type', width: 80, render: (row) => h(NTag, { size: 'small', type: row._direction === 'forward' ? 'success' : 'warning', bordered: false }, { default: () => row._direction === 'forward' ? t('scanner.forward') : t('scanner.reverse') }) },
  { title: t('scanner.rate'), key: 'rate_pct', width: 100, render: (row) => (row.rate_pct ?? 0).toFixed(4) + '%' },
  { title: t('scanner.ann'), key: 'annual_pct', width: 80, render: (row) => (row.annual_pct ?? 0).toFixed(0) + '%' },
  { title: t('scanner.spotBorrow'), key: 'spot', width: 100, render: (row) => row.has_spot === true ? 'Spot: $' + (row.spot_price ?? 0).toFixed(2) : row.borrowable === true ? 'Borrow' : 'N/A' },
  { title: t('scanner.netEdge'), key: 'net_edge_pct', width: 100, sorter: (a, b) => (a.net_edge_pct ?? 0) - (b.net_edge_pct ?? 0),
    render: (row) => h(NText, { type: (row.net_edge_pct ?? 0) > 0 ? 'success' : 'error', strong: true }, { default: () => (row.net_edge_pct ?? 0).toFixed(4) + '%' }) },
  { title: t('scanner.action'), key: 'actions', width: 80,
    render: (row) => {
      const block = venueTradeBlock(row._venue)
      return h(NButton, {
        size: 'tiny', type: 'primary', secondary: true,
        disabled: !!block,
        title: block || undefined,
        onClick: () => showOpenDialog({ kind: 'carry', row }),
      }, { default: () => t('scanner.open') })
    } },
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
  { title: t('scanner.action'), key: 'actions', width: 80,
    render: (row) => {
      const block = venueTradeBlock(row.futures_venue, row.spot_venue)
      return h(NButton, {
        size: 'tiny', type: 'primary', secondary: true,
        disabled: !!block,
        title: block || undefined,
        onClick: () => showOpenDialog({ kind: 'unified', row }),
      }, { default: () => t('scanner.open') })
    } },
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
  gap: 12px;
}

.toolbar-title-row {
  display: flex;
  justify-content: center;
  align-items: center;
  width: 100%;
}

.toolbar-title {
  font-size: 17px;
  font-weight: 600;
  white-space: nowrap;
  letter-spacing: -0.01em;
}

.toolbar-tabs-row {
  display: flex;
  justify-content: center;
  align-items: center;
  width: 100%;
}

/* 策略 Tab：下划线指示，无实心绿块 */
.strategy-tabs {
  width: auto;
  max-width: 100%;
}
.strategy-tabs :deep(.n-tabs-nav) {
  justify-content: center;
}
.strategy-tabs :deep(.n-tabs-pane-wrapper) {
  display: none;
}
.strategy-tabs :deep(.n-tabs-tab) {
  font-size: 13px;
  font-weight: 500;
  padding: 8px 16px;
}
.strategy-tabs :deep(.n-tabs-tab--active) {
  font-weight: 600;
}

.toolbar-filters-row {
  display: flex;
  width: 100%;
  padding-top: 4px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.filters-left {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 14px;
  width: 100%;
}

.actions-inline {
  margin-left: auto;
  flex-shrink: 0;
}

@media (max-width: 900px) {
  .actions-inline {
    margin-left: 0;
    width: 100%;
    justify-content: flex-end;
    padding-top: 4px;
  }
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

.venue-filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.venue-presets {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

/* 交易所预设：圆角轮廓 Chip，与策略 Tab / 周期下拉区分 */
.preset-chip {
  appearance: none;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.03);
  color: rgba(255, 255, 255, 0.55);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  padding: 4px 11px;
  line-height: 1.2;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}
.preset-chip:hover:not(:disabled) {
  border-color: rgba(255, 255, 255, 0.28);
  color: rgba(255, 255, 255, 0.85);
}
.preset-chip.active {
  border-color: rgba(24, 160, 88, 0.55);
  background: rgba(24, 160, 88, 0.1);
  color: #63e2b7;
}
.preset-chip:disabled {
  opacity: 0.35;
  cursor: not-allowed;
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

/* 周期：下拉选择，与 Tab / Chip 形态区分 */
.interval-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.docs-link {
  font-size: 12px;
  color: #63e2b7;
  text-decoration: none;
  white-space: nowrap;
}
.docs-link:hover {
  text-decoration: underline;
}
.interval-select {
  width: 148px;
}
.interval-select :deep(.n-base-selection) {
  border-radius: 6px;
  min-height: 32px;
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
