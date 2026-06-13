<script setup lang="ts">
import { h, onMounted, computed, ref } from 'vue'
import {
  NCard, NGrid, NGi, NDataTable, NButton, NIcon, NSpin, NEmpty, NTag,
    NSelect, NModal, NSpace, NText, NDivider, NDescriptions, NDescriptionsItem,
    useMessage,
    type DataTableColumns,
} from 'naive-ui'
import {
  WalletOutline, PieChartOutline, RefreshOutline, OpenOutline,
  TrendingUpOutline, TrendingDownOutline, TimeOutline, CashOutline,
} from '@vicons/ionicons5'
import { getPositions, getResolvedFees, post, type PositionItem } from '@/composables/useApi'
import { useI18n } from 'vue-i18n'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'

use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const { t } = useI18n()
const message = useMessage()
const positions = getPositions()
const resolvedFees = getResolvedFees()

const statusFilter = ref<'open' | 'closed' | 'all'>('all')

const allItems = computed(() => positions.data.value ?? [])
const filteredItems = computed(() => {
  if (statusFilter.value === 'all') return allItems.value
  if (statusFilter.value === 'open') return allItems.value.filter((p) => p.status === 'open')
  return allItems.value.filter((p) => p.status === 'closed')
})

// ─── Helpers ───────────────────────────────────────────────────

function toMs(ts: string | number | undefined): number | null {
  if (!ts) return null
  if (typeof ts === 'number') return ts
  const ms = new Date(ts).getTime()
  return isNaN(ms) ? null : ms
}

function formatTime(ts: string | number | undefined): string {
  const ms = toMs(ts)
  if (ms === null) return '—'
  return new Date(ms).toLocaleString(navigator.language, {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

function formatDuration(openedAt: string | number | undefined, closedAt?: string | number): string {
  const start = toMs(openedAt)
  if (start === null) return '—'
  let end = Date.now()
  if (closedAt) {
    const c = toMs(closedAt)
    if (c !== null) end = c
  }
  const diff = Math.max(0, end - start)
  const days = Math.floor(diff / 86400000)
  const hours = Math.floor((diff % 86400000) / 3600000)
  const mins = Math.floor((diff % 3600000) / 60000)
  const parts: string[] = []
  if (days > 0) parts.push(`${days}${t('positions.days')}`)
  if (hours > 0) parts.push(`${hours}${t('positions.hours')}`)
  parts.push(`${mins}${t('positions.minutes')}`)
  return parts.join(' ')
}

function holdHours(openedAt: string | number | undefined, closedAt?: string | number): number {
  const start = toMs(openedAt)
  if (start === null) return 0
  let end = Date.now()
  if (closedAt) {
    const c = toMs(closedAt)
    if (c !== null) end = c
  }
  return Math.max(0, (end - start) / 3600000)
}

function fmtUsd(v: number | undefined | null, signed = false): string {
  if (v === undefined || v === null || isNaN(v)) return '—'
  const prefix = signed ? (v >= 0 ? '+' : '') : ''
  return prefix + '$' + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 }) + (signed && v < 0 ? '' : '')
}

function fmtPct(v: number | undefined | null, decimals = 2): string {
  if (v === undefined || v === null || isNaN(v)) return '—'
  return v.toFixed(decimals) + '%'
}

function fmtPrice(v: number | undefined | null): string {
  if (v === undefined || v === null || isNaN(v)) return '—'
  return v.toLocaleString(undefined, { maximumFractionDigits: 6 })
}

// ─── Computed metrics per position ─────────────────────────────

/** Get open prices depending on strategy */
function getOpenPrices(p: PositionItem): { long?: number; short?: number; futures?: number; spot?: number } {
  if (p.futures_venue || p.spot_venue) {
    return { futures: p.futures_price, spot: p.spot_price }
  }
  return { long: p.long_price, short: p.short_price }
}

/** Get close prices from close_info, falling back to open prices for dry-run positions
 *  (dry-run closes don't record actual fill prices).
 */
function getClosePrices(p: PositionItem): { long?: number; short?: number; futures?: number; spot?: number } {
  const ci = p.close_info
  if (!ci) {
    // No close_info at all — fall back to open prices (PnL ≈ 0)
    return getOpenPrices(p)
  }
  if (ci.futures_price !== undefined || ci.spot_price !== undefined) {
    return { futures: ci.futures_price as number, spot: ci.spot_price as number }
  }
  // Dry-run closes only have {dry_run, open_mark_spread, close_mark_spread}
  // — no actual fill prices. Fall back to open prices so PnL ≈ 0 (just fees).
  if (ci.long_price === undefined && ci.short_price === undefined) {
    return getOpenPrices(p)
  }
  return { long: ci.long_price as number, short: ci.short_price as number }
}

/**
 * Realized PnL for a closed position.
 * Pure futures: sum of (close - open) × qty for long leg, (open - close) × qty for short leg.
 * Carry/unified: (open - close) × qty for futures leg (short perp profits when price drops).
 */
function realizedPnl(p: PositionItem): number | null {
  if (p.status !== 'closed' || !p.close_info) return null
  const qty = p.qty ?? 0
  if (qty <= 0) return null

  const open = getOpenPrices(p)
  const close = getClosePrices(p)

  // Pure futures: long bought at open.long, sold at close.long; short sold at open.short, bought at close.short
  if (open.long !== undefined && close.long !== undefined && open.short !== undefined && close.short !== undefined) {
    const longPnl = (close.long - open.long) * qty
    const shortPnl = (open.short - close.short) * qty
    return longPnl + shortPnl
  }
  // Carry/unified: forward = short perp at open.futures, close at close.futures
  if (open.futures !== undefined && close.futures !== undefined) {
    const futPnl = p.direction === 'forward'
      ? (open.futures - close.futures) * qty
      : (close.futures - open.futures) * qty
    let spotPnl = 0
    if (open.spot !== undefined && close.spot !== undefined) {
      spotPnl = p.direction === 'forward'
        ? (close.spot - open.spot) * qty
        : (open.spot - close.spot) * qty
    }
    return futPnl + spotPnl
  }
  return null
}

/** Unrealized PnL from backend enrichment (open positions only) */
function unrealizedPnl(p: PositionItem): number | null {
  const v = p.unrealized_pnl_usd ?? p.pnl_usd
  return v === undefined ? null : v
}

/** Funding income estimate from backend enrichment */
function fundingPnl(p: PositionItem): number | null {
  const v = (p as any).funding_pnl_est_usd
  return v === undefined ? null : v
}

/** Total PnL (price + funding) from backend enrichment */
function totalPnl(p: PositionItem): number | null {
  const v = (p as any).total_pnl_usd
  if (v !== undefined) return v
  const u = unrealizedPnl(p)
  const f = fundingPnl(p)
  if (u === null) return null
  return u + (f ?? 0)
}

/** Funding rate spread (annualized %) from backend */
function fundingSpread(p: PositionItem): number | null {
  const v = (p as any).funding_rate_spread_pct
  return v === undefined ? null : v
}

/** Estimate round-trip fees from resolved fee data × trade USD */
function feeEstimate(p: PositionItem): number | null {
  const fees = resolvedFees.data.value
  if (!fees || !fees.venues) return null
  const tradeUsd = p.trade_usd ?? p.amount_usd ?? 0
  if (tradeUsd <= 0) return null

  let totalPct = 0
  const venues = [p.long_venue, p.short_venue, p.futures_venue, p.spot_venue].filter(Boolean) as string[]
  for (const vid of venues) {
    const fee = fees.venues[vid]
    if (fee) {
      // Use futures taker for perp legs, spot taker for spot legs
      const isSpot = vid === p.spot_venue && p.spot_venue !== p.futures_venue
      const takerPct = isSpot ? fee.spot_taker_pct : fee.futures_taker_pct
      totalPct += takerPct
    }
  }
  // Round trip = 2× (open + close), but we only have taker rates per side
  // Approximation: sum of taker rates for all legs × trade_usd × 2 (open + close)
  return tradeUsd * (totalPct / 100) * 2
}

/** Annualized return based on PnL, trade size, and hold time */
function annualizedReturn(p: PositionItem): number | null {
  const pnl = realizedPnl(p) ?? unrealizedPnl(p)
  const tradeUsd = p.trade_usd ?? p.amount_usd ?? 0
  const hours = holdHours(p.opened_at ?? p.open_time, p.closed_at)
  if (pnl === null || tradeUsd <= 0 || hours <= 0) return null
  const periodReturn = pnl / tradeUsd
  return periodReturn * (8760 / hours) * 100
}

/** Net PnL after fees */
function netPnl(p: PositionItem): number | null {
  const gross = realizedPnl(p) ?? unrealizedPnl(p)
  const fees = feeEstimate(p)
  if (gross === null) return null
  return gross - (fees ?? 0)
}

// ─── Summary aggregations ──────────────────────────────────────

const emptyMessage = computed(() => {
  if (statusFilter.value === 'open') return t('positions.noOpenPositions')
  return t('positions.noPositions')
})

const summary = computed(() => {
  const items = allItems.value
  const openItems = items.filter((p) => p.status === 'open')
  const closedItems = items.filter((p) => p.status === 'closed')
  const totalTrade = items.reduce((s, p) => s + (p.trade_usd ?? p.amount_usd ?? 0), 0)

  // Closed positions PnL stats
  const closedPnls = closedItems.map((p) => realizedPnl(p)).filter((v): v is number => v !== null)
  const totalRealizedPnl = closedPnls.reduce((s, v) => s + v, 0)
  const wins = closedPnls.filter((v) => v > 0)
  const winRate = closedPnls.length > 0 ? (wins.length / closedPnls.length) * 100 : 0
  const bestTrade = closedPnls.length > 0 ? Math.max(...closedPnls) : 0
  const worstTrade = closedPnls.length > 0 ? Math.min(...closedPnls) : 0

  // Open positions unrealized (use totalPnl which includes funding estimate)
  const openPnls = openItems.map((p) => totalPnl(p) ?? unrealizedPnl(p)).filter((v): v is number => v !== null)
  const totalUnrealizedPnl = openPnls.reduce((s, v) => s + v, 0)

  // Total funding income estimate
  const fundingPnls = items.map((p) => fundingPnl(p)).filter((v): v is number => v !== null)
  const totalFundingPnl = fundingPnls.reduce((s, v) => s + v, 0)

  // Total fees
  const totalFees = items.reduce((s, p) => s + (feeEstimate(p) ?? 0), 0)

  // Avg hold for closed
  const holdTimes = closedItems.map((p) => holdHours(p.opened_at ?? p.open_time, p.closed_at))
  const avgHold = holdTimes.length > 0 ? holdTimes.reduce((s, v) => s + v, 0) / holdTimes.length : 0

  return {
    openCount: openItems.length,
    closedCount: closedItems.length,
    totalCount: items.length,
    totalTrade,
    totalRealizedPnl,
    totalUnrealizedPnl,
    totalPnl: totalRealizedPnl + totalUnrealizedPnl,
    totalFundingPnl,
    winRate,
    bestTrade,
    worstTrade,
    totalFees,
    avgHold,
    closedCount_: closedPnls.length,
  }
})

function fmtDuration(hours: number): string {
  if (hours <= 0) return '—'
  const days = Math.floor(hours / 24)
  const h = Math.floor(hours % 24)
  if (days > 0) return `${days}${t('positions.days')} ${h}${t('positions.hours')}`
  return `${h}${t('positions.hours')}`
}

const summaryCards = computed(() => [
  {
    label: t('positions.openPositions'),
    value: summary.value.openCount,
    icon: OpenOutline,
    color: '#18a058',
  },
  {
    label: t('positions.totalPnl'),
    value: fmtUsd(summary.value.totalPnl, true),
    icon: summary.value.totalPnl >= 0 ? TrendingUpOutline : TrendingDownOutline,
    color: summary.value.totalPnl >= 0 ? '#18a058' : '#d03050',
  },
  {
    label: t('positions.totalFees'),
    value: fmtUsd(summary.value.totalFees),
    icon: CashOutline,
    color: '#f0a020',
  },
  {
    label: t('positions.winRate'),
    value: summary.value.winRate.toFixed(1) + '%',
    icon: PieChartOutline,
    color: '#2080f0',
  },
  {
    label: t('positions.avgHold'),
    value: fmtDuration(summary.value.avgHold),
    icon: TimeOutline,
    color: '#808080',
  },
  {
    label: t('positions.totalTrade'),
    value: '$' + summary.value.totalTrade.toLocaleString(),
    icon: WalletOutline,
    color: '#8a2be2',
  },
])

// ─── Equity curve (cumulative realized PnL) ─────────────────

const equityCurveData = computed(() => {
  const closed = allItems.value
    .filter((p) => p.status === 'closed')
    .map((p) => ({
      time: toMs(p.closed_at ?? p.opened_at ?? p.open_time),
      pnl: realizedPnl(p) ?? 0,
    }))
    .filter((d) => d.time !== null)
    .sort((a, b) => (a.time! - b.time!))

  let cumulative = 0
  return closed.map((d) => {
    cumulative += d.pnl
    return [d.time, cumulative]
  })
})

const equityChartOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    valueFormatter: (v: number) => '$' + (v ?? 0).toFixed(2),
  },
  grid: { left: 50, right: 20, top: 20, bottom: 30 },
  xAxis: { type: 'time' },
  yAxis: { type: 'value', name: 'PnL (USD)', scale: true },
  series: [
    {
      type: 'line',
      data: equityCurveData.value,
      smooth: true,
      showSymbol: false,
      areaStyle: { opacity: 0.1, color: '#18a058' },
      lineStyle: { width: 2, color: '#18a058' },
      itemStyle: { color: '#18a058' },
    },
  ],
}))

// ─── Table columns ──────────────────────────────────────────

function pnlColor(v: number | null): 'success' | 'error' | undefined {
  if (v === null) return undefined
  return v >= 0 ? 'success' : 'error'
}

const tableColumns = computed<DataTableColumns<PositionItem>>(() => [
  {
    type: 'expand',
    expandColumnWidth: 40,
    renderExpand: (row) => renderDetail(row),
  },
  { title: t('positions.id'), key: 'id', width: 110, ellipsis: { tooltip: true } },
  {
    title: t('positions.pair'),
    key: 'base',
    width: 90,
    render: (row) => `${row.base}/${row.quote ?? 'USDT'}`,
  },
  {
    title: t('positions.strategy'),
    key: 'strategy',
    width: 90,
    render: (row) => {
      const s = row.strategy ?? 'pure_futures'
      const label = s === 'pure_futures' || s === 'pure_futures_spread' ? 'Pure' : s === 'carry' ? 'Carry' : s
      return h(NTag, { size: 'tiny', bordered: false, type: 'info' }, { default: () => label })
    },
  },
  {
    title: t('positions.direction'),
    key: 'direction',
    width: 80,
    render: (row) => h(NTag, {
      size: 'small',
      type: row.direction === 'forward' ? 'success' : 'warning',
      bordered: false,
    }, { default: () => row.direction }),
  },
  {
    title: t('positions.long') + '/' + t('positions.short'),
    key: 'venues',
    width: 130,
    render: (row) => {
      if (row.futures_venue) {
        return `${row.futures_venue} / ${row.spot_venue ?? '-'}`
      }
      return `${row.long_venue} / ${row.short_venue}`
    },
  },
  {
    title: t('positions.amount'),
    key: 'trade_usd',
    width: 100,
    render: (row) => '$' + (row.trade_usd ?? row.amount_usd ?? 0).toLocaleString(),
  },
  {
    title: t('positions.quantity'),
    key: 'qty',
    width: 80,
    render: (row) => {
      const qty = row.qty
      if (!qty) return '—'
      return qty.toLocaleString(undefined, { maximumFractionDigits: 6 })
    },
  },
  {
    title: t('positions.openPrice'),
    key: 'open_price',
    width: 120,
    render: (row) => {
      const open = getOpenPrices(row)
      if (open.futures !== undefined) return `${fmtPrice(open.futures)} / ${fmtPrice(open.spot)}`
      return `${fmtPrice(open.long)} / ${fmtPrice(open.short)}`
    },
  },
  {
    title: t('positions.entrySpread'),
    key: 'mark_spread_pct',
    width: 100,
    render: (row) => {
      const val = row.mark_spread_pct ?? row.open_spread_pct ?? 0
      const isReal = row.mark_spread_pct !== undefined
      return fmtPct(isReal ? val : val * 100, 3)
    },
  },
  {
    title: t('positions.realizedPnl') + '/' + t('positions.unrealizedPnl'),
    key: 'pnl',
    width: 120,
    render: (row) => {
      const rpnl = realizedPnl(row)
      const upnl = unrealizedPnl(row)
      const v = rpnl ?? upnl
      if (v === null) return '—'
      return h(NText, { type: pnlColor(v), strong: true }, {
        default: () => fmtUsd(v, true),
      })
    },
  },
  {
    title: t('positions.feeEstimate'),
    key: 'fees',
    width: 90,
    render: (row) => fmtUsd(feeEstimate(row)),
  },
  {
    title: t('positions.annualized'),
    key: 'annualized',
    width: 80,
    render: (row) => {
      const v = annualizedReturn(row)
      if (v === null) return '—'
      return h(NText, { type: pnlColor(v) }, { default: () => fmtPct(v, 1) })
    },
  },
  {
    title: t('positions.holdTime'),
    key: 'duration',
    width: 100,
    render: (row) => formatDuration(row.opened_at ?? row.open_time, row.closed_at),
  },
  {
    title: t('positions.openedAt'),
    key: 'opened_at',
    width: 120,
    render: (row) => formatTime(row.opened_at ?? row.open_time),
  },
  {
    title: t('positions.statusCol'),
    key: 'status',
    width: 90,
    render: (row) => {
      const colorMap: Record<string, 'success' | 'warning' | 'error' | 'default'> = {
        open: 'success',
        closed: 'default',
      }
      return h(NTag, { size: 'small', type: colorMap[row.status] ?? 'default', bordered: false }, { default: () => row.status })
    },
  },
  {
    title: '',
    key: 'actions',
    width: 80,
    fixed: 'right',
    render: (row) => h(NButton, {
      size: 'tiny',
      type: 'error',
      secondary: true,
      disabled: row.status === 'closed',
      onClick: () => showCloseConfirm(row),
    }, { default: () => t('positions.close') }),
  },
])

// ─── Expandable detail panel ───────────────────────────────────

function renderDetail(row: PositionItem) {
  const open = getOpenPrices(row)
  const close = getClosePrices(row)
  const rpnl = realizedPnl(row)
  const upnl = unrealizedPnl(row)
  const fees = feeEstimate(row)
  const npnl = netPnl(row)
  const ann = annualizedReturn(row)
  const isPureFutures = !row.futures_venue

  const items: Array<{ label: string; value: string; type?: 'success' | 'error' }> = []

  // Prices
  if (isPureFutures) {
    items.push({ label: t('positions.openPrice') + ' (Long/Short)', value: `${fmtPrice(open.long)} / ${fmtPrice(open.short)}` })
    if (close.long !== undefined) {
      items.push({ label: t('positions.closePrice') + ' (Long/Short)', value: `${fmtPrice(close.long)} / ${fmtPrice(close.short)}` })
    }
    if (row.long_qty || row.short_qty) {
      items.push({ label: t('positions.longQty') + '/' + t('positions.shortQty'), value: `${row.long_qty ?? '-'} / ${row.short_qty ?? '-'}` })
    }
  } else {
    items.push({ label: t('positions.futuresPrice'), value: fmtPrice(open.futures) })
    items.push({ label: t('positions.spotPrice'), value: fmtPrice(open.spot) })
    if (close.futures !== undefined) {
      items.push({ label: t('positions.closePrice') + ' (' + t('positions.futuresVenue') + ')', value: fmtPrice(close.futures) })
    }
    if (close.spot !== undefined) {
      items.push({ label: t('positions.closePrice') + ' (' + t('positions.spotVenue') + ')', value: fmtPrice(close.spot) })
    }
  }

  // Spreads
  const entrySpread = row.mark_spread_pct ?? row.open_spread_pct
  if (entrySpread !== undefined) {
    items.push({ label: t('positions.entrySpread'), value: fmtPct(row.mark_spread_pct !== undefined ? entrySpread : (entrySpread as number) * 100, 4) })
  }
  if (row.close_info?.close_mark_spread !== undefined) {
    items.push({ label: t('positions.exitSpread'), value: fmtPct(row.close_info.close_mark_spread as number, 4) })
  }
  if (row.close_info?.open_mark_spread !== undefined) {
    items.push({ label: t('positions.entrySpread') + ' (recorded)', value: fmtPct(row.close_info.open_mark_spread as number, 4) })
  }

  items.push({ label: t('positions.holdTime'), value: formatDuration(row.opened_at ?? row.open_time, row.closed_at) })

  // PnL breakdown
  items.push({ label: t('positions.grossPnl'), value: fmtUsd(rpnl ?? upnl, true), type: (rpnl ?? upnl ?? 0) >= 0 ? 'success' : 'error' })
  const fpnl = fundingPnl(row)
  if (fpnl !== null) {
    items.push({ label: t('positions.realizedPnl') + ' (' + t('positions.funding') + ')', value: fmtUsd(fpnl, true), type: fpnl >= 0 ? 'success' : 'error' })
  }
  const spread = fundingSpread(row)
  if (spread !== null) {
    items.push({ label: t('positions.funding') + ' Spread (ann)', value: fmtPct(spread, 1), type: spread >= 0 ? 'success' : 'error' })
  }
  items.push({ label: t('positions.feeEstimate'), value: fmtUsd(fees) })
  items.push({ label: t('positions.netPnl'), value: fmtUsd(npnl, true), type: (npnl ?? 0) >= 0 ? 'success' : 'error' })
  if (ann !== null) {
    items.push({ label: t('positions.annualized'), value: fmtPct(ann, 1), type: ann >= 0 ? 'success' : 'error' })
  }

  // Metadata
  if (row.dry_run !== undefined) {
    items.push({ label: t('positions.strategy'), value: row.dry_run ? t('positions.dryRun') : t('positions.live') })
  }
  if (row.parallel_legs) {
    items.push({ label: 'Mode', value: 'Parallel legs' })
  }
  if (row.closed_at) {
    items.push({ label: t('positions.closedAt'), value: formatTime(row.closed_at) })
  }

  return h('div', { style: 'padding: 8px 0;' }, [
    h(NDivider, { style: 'margin: 4px 0 12px' }),
    h(NDescriptions, {
      'label-placement': 'left',
      'bordered': true,
      size: 'small',
      'column': 3,
    }, {
      default: () => items.map((item) => h(NDescriptionsItem, { label: item.label }, {
        default: () => h(NText, { type: item.type, strong: !!item.type }, { default: () => item.value }),
      })),
    }),
  ])
}

// ─── Close modal ───────────────────────────────────────────────

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
  resolvedFees.refresh()
})
</script>

<template>
  <div class="positions-page">
    <n-grid :cols="3" :x-gap="16" :y-gap="16" class="summary-row" responsive="screen">
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

    <n-card v-if="equityCurveData.length > 0" :title="t('positions.equityCurve')" size="small">
      <v-chart :option="equityChartOption" style="height: 240px" autoresize />
    </n-card>

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
          :scroll-x="1400"
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
.summary-card-inner { display: flex; align-items: center; gap: 14px; }
.summary-icon {
  width: 44px; height: 44px; border-radius: 10px;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}
.summary-stat { display: flex; flex-direction: column; gap: 2px; }
.summary-label { font-size: 12px; }
.summary-value { font-size: 18px; font-weight: 700; }
</style>
