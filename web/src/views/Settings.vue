<script setup lang="ts">
import { reactive, onMounted, computed } from 'vue'
import {
  NCard, NForm, NFormItem, NInputNumber, NButton, NGrid, NGi, NText, NTag, NSpin,
  NIcon, NDivider, NSelect, useMessage,
} from 'naive-ui'
import { CheckmarkCircleOutline, CloseCircleOutline, SaveOutline } from '@vicons/ionicons5'
import {
  getVenues, getCredentialsStatus, getStrategy, getFeeTiers, getResolvedFees,
  post, type StrategyParams,
} from '@/composables/useApi'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const message = useMessage()
const venues = getVenues()
const credentials = getCredentialsStatus()
const strategy = getStrategy()
const feeTiers = getFeeTiers()
const resolvedFees = getResolvedFees()

const scanVenueOptions = computed(
  () => (venues.data.value ?? []).map((v) => ({ label: v.name, value: v.id }))
)

const FEE_VENUE_ORDER = computed(
  () => (venues.data.value ?? []).map((v) => v.id)
)

const strategyForm = reactive({
  min_spread_annual: 0,
  min_edge_annual: 0,
  max_mark_spread_pct: 0,
  trade_usd: 0,
  max_positions: 0,
  scan_interval_sec: 0,
  scan_venues: [] as string[],
  min_edge_1h: 0,
  fee_mode: 'auto' as 'auto' | 'api' | 'vip_tier',
  venue_fee_tiers: {} as Record<string, string>,
})

const feeModeOptions = computed(() => [
  { label: t('settings.feeModeAuto'), value: 'auto' },
  { label: t('settings.feeModeApi'), value: 'api' },
  { label: t('settings.feeModeVip'), value: 'vip_tier' },
])

function tierOptionsFor(venueId: string) {
  const tiers = feeTiers.data.value?.[venueId] ?? []
  return tiers.map((tier) => ({
    label: `${tier.label} — ${t('settings.spot')} ${tier.spot_taker_pct}% / ${t('settings.futures')} ${tier.futures_taker_pct}%`,
    value: tier.id,
  }))
}

function venueDisplayName(venueId: string): string {
  return scanVenueOptions.value.find((v) => v.value === venueId)?.label ?? venueId
}

function feeSourceLabel(source: string | undefined): string {
  if (source === 'api') return t('settings.feeFromApi')
  if (source === 'tier') return t('settings.feeFromTier')
  return t('settings.feeFromDefault')
}

onMounted(async () => {
  await Promise.all([
    venues.refresh(),
    credentials.refresh(),
    strategy.refresh(),
    feeTiers.refresh(),
    resolvedFees.refresh(),
  ])
  const s = strategy.data.value
  if (s) {
    strategyForm.min_spread_annual = s.min_spread_annual
    strategyForm.min_edge_annual = s.min_edge_annual
    strategyForm.max_mark_spread_pct = s.max_mark_spread_pct
    strategyForm.trade_usd = s.trade_usd
    strategyForm.max_positions = s.max_positions
    strategyForm.scan_interval_sec = s.scan_interval_sec
    if (Array.isArray(s.scan_venues) && s.scan_venues.length > 0) strategyForm.scan_venues = s.scan_venues
    if (typeof s.min_edge_1h === 'number') strategyForm.min_edge_1h = s.min_edge_1h
    if (s.fee_mode) strategyForm.fee_mode = s.fee_mode
    if (s.venue_fee_tiers) strategyForm.venue_fee_tiers = { ...s.venue_fee_tiers }
  }
})

function onTierChange(venueId: string, tierId: string) {
  strategyForm.venue_fee_tiers = { ...strategyForm.venue_fee_tiers, [venueId]: tierId }
}

async function handleSave() {
  try {
    await post<StrategyParams>('/settings/strategy', strategyForm)
    await resolvedFees.refresh()
    try {
      await post('/scanner/recalc-fees', {})
    } catch {
      // No cached scan yet — ignore
    }
    message.success(t('settings.settingsSaved'))
  } catch (e) {
    message.error(e instanceof Error ? e.message : t('settings.failedToSave'))
  }
}
</script>

<template>
  <div class="settings-page">
    <n-grid :cols="24" :x-gap="16" :y-gap="16">
      <!-- Venues -->
      <n-gi :span="8">
        <n-card :title="t('settings.venueConfig')" size="small">
          <n-spin :show="venues.loading.value">
            <div class="venue-list">
              <div v-if="venues.data.value?.length === 0" style="padding: 20px; text-align: center">
                <n-text depth="3">{{ t('settings.noVenues') }}</n-text>
              </div>
              <div v-for="venue in venues.data.value" :key="venue.id" class="venue-item">
                <div class="venue-info">
                  <n-text class="venue-name">{{ venue.name }}</n-text>
                  <n-tag size="small" :type="venue.configured ? 'success' : 'warning'" :bordered="false">
                    {{ venue.configured ? t('settings.configured') : t('settings.notConfigured') }}
                  </n-tag>
                  <n-tag size="small" type="info" :bordered="false">{{ t('settings.scan') }}</n-tag>
                  <n-tag
                    size="small"
                    :type="venue.trade_capable ? (venue.live_ready ? 'success' : 'warning') : 'default'"
                    :bordered="false"
                    :title="venue.trade_capable ? (venue.live_ready ? '' : venue.live_reason) : venue.trade_reason"
                  >
                    {{ venue.trade_capable ? (venue.live_ready ? t('settings.trade') : t('settings.tradeDryRun')) : t('settings.scanOnly') }}
                  </n-tag>
                </div>
              </div>
            </div>
          </n-spin>
        </n-card>
      </n-gi>

      <!-- Strategy -->
      <n-gi :span="8">
        <n-card :title="t('settings.strategyParams')" size="small">
          <n-form label-placement="left" label-width="140" size="small">
            <n-form-item :label="t('settings.minSpreadAnnual')">
              <n-input-number v-model:value="strategyForm.min_spread_annual" :min="0" :max="100" style="width: 100%">
                <template #suffix>%</template>
              </n-input-number>
            </n-form-item>
            <n-form-item :label="t('settings.minEdgeAnnual')">
              <n-input-number v-model:value="strategyForm.min_edge_annual" :min="0" :max="100" style="width: 100%">
                <template #suffix>%</template>
              </n-input-number>
            </n-form-item>
            <n-form-item :label="t('settings.minEdge1h')">
              <n-input-number v-model:value="strategyForm.min_edge_1h" :min="0" :max="100" :step="0.005" style="width: 100%">
                <template #suffix>%</template>
              </n-input-number>
            </n-form-item>
            <n-form-item :label="t('settings.maxMarkSpread')">
              <n-input-number v-model:value="strategyForm.max_mark_spread_pct" :min="0" :max="100" style="width: 100%">
                <template #suffix>%</template>
              </n-input-number>
            </n-form-item>
            <n-form-item :label="t('settings.tradeSize')">
              <n-input-number v-model:value="strategyForm.trade_usd" :min="100" :step="1000" style="width: 100%">
                <template #suffix>USDT</template>
              </n-input-number>
            </n-form-item>
            <n-form-item :label="t('settings.maxPositions')">
              <n-input-number v-model:value="strategyForm.max_positions" :min="1" :max="20" style="width: 100%" />
            </n-form-item>
            <n-form-item :label="t('settings.scanInterval')">
              <n-input-number v-model:value="strategyForm.scan_interval_sec" :min="10" :step="30" style="width: 100%">
                <template #suffix>s</template>
              </n-input-number>
            </n-form-item>
            <n-form-item :label="t('settings.scanVenues')">
              <n-select v-model:value="strategyForm.scan_venues" :options="scanVenueOptions" multiple style="width: 100%" />
            </n-form-item>
            <n-form-item>
              <n-button type="primary" @click="handleSave">
                <template #icon><n-icon><SaveOutline /></n-icon></template>
                {{ t('settings.save') }}
              </n-button>
            </n-form-item>
          </n-form>
        </n-card>
      </n-gi>

      <!-- Credential Backends -->
      <n-gi :span="8">
        <n-card :title="t('settings.credentialBackends')" size="small">
          <n-spin :show="credentials.loading.value">
            <div class="backend-list">
              <div v-for="(info, name) in credentials.data.value?.backends" :key="name" class="backend-item">
                <div class="backend-left">
                  <n-icon v-if="info.available" color="#18a058" size="18"><CheckmarkCircleOutline /></n-icon>
                  <n-icon v-else color="#d03050" size="18"><CloseCircleOutline /></n-icon>
                  <n-text>{{ name }}</n-text>
                </div>
                <n-text depth="3" style="font-size: 11px">{{ info.description }}</n-text>
              </div>
            </div>
            <n-divider style="margin: 12px 0" />
            <div class="backend-summary">
              <n-text depth="3" style="font-size: 12px">{{ t('settings.configuredVenues') }}: {{ credentials.data.value?.venues_configured?.join(', ') || t('settings.none') }}</n-text>
            </div>
            <div class="backend-summary" style="margin-top: 4px">
              <n-text depth="3" style="font-size: 12px">{{ t('settings.missingVenues') }}: {{ credentials.data.value?.venues_missing?.join(', ') || t('settings.none') }}</n-text>
            </div>
          </n-spin>
        </n-card>
      </n-gi>

      <!-- Trading Fees -->
      <n-gi :span="24">
        <n-card :title="t('settings.tradingFees')" size="small">
          <n-spin :show="feeTiers.loading.value || resolvedFees.loading.value">
            <n-text depth="3" style="font-size: 12px; display: block; margin-bottom: 12px">
              {{ t('settings.feeModeHint') }}
            </n-text>
            <n-form label-placement="left" label-width="120" size="small" style="margin-bottom: 16px">
              <n-form-item :label="t('settings.feeMode')">
                <n-select
                  v-model:value="strategyForm.fee_mode"
                  :options="feeModeOptions"
                  style="width: 280px"
                />
              </n-form-item>
            </n-form>
            <div class="fee-table">
              <div class="fee-header">
                <n-text strong>{{ t('settings.venue') }}</n-text>
                <n-text strong>{{ t('settings.feeSource') }}</n-text>
                <n-text strong>{{ t('settings.spotTaker') }}</n-text>
                <n-text strong>{{ t('settings.futuresTaker') }}</n-text>
                <n-text strong>{{ t('settings.vipTier') }}</n-text>
              </div>
              <div v-for="venueId in FEE_VENUE_ORDER" :key="venueId" class="fee-row">
                <n-text>{{ venueDisplayName(venueId) }}</n-text>
                <div>
                  <n-tag
                    size="small"
                    :type="resolvedFees.data.value?.venues?.[venueId]?.uses_api ? 'success' : 'warning'"
                    :bordered="false"
                  >
                    {{ resolvedFees.data.value?.venues?.[venueId]?.uses_api
                      ? t('settings.feeFromApi')
                      : feeSourceLabel(resolvedFees.data.value?.venues?.[venueId]?.futures_source) }}
                  </n-tag>
                </div>
                <n-text>
                  {{ (resolvedFees.data.value?.venues?.[venueId]?.spot_taker_pct ?? 0).toFixed(3) }}%
                </n-text>
                <n-text>
                  {{ (resolvedFees.data.value?.venues?.[venueId]?.futures_taker_pct ?? 0).toFixed(3) }}%
                </n-text>
                <div>
                  <n-select
                    v-if="!resolvedFees.data.value?.venues?.[venueId]?.uses_api"
                    :value="strategyForm.venue_fee_tiers[venueId] ?? resolvedFees.data.value?.venues?.[venueId]?.tier ?? (['binance', 'bitget', 'bybit', 'okx'].includes(venueId) ? 'vip0' : 'default')"
                    :options="tierOptionsFor(venueId)"
                    size="small"
                    style="width: 100%"
                    @update:value="(v: string) => onTierChange(venueId, v)"
                  />
                  <n-text v-else depth="3" style="font-size: 12px">{{ t('settings.feeApiLocked') }}</n-text>
                </div>
              </div>
            </div>
            <n-divider style="margin: 12px 0" />
            <n-button type="primary" @click="handleSave">
              <template #icon><n-icon><SaveOutline /></n-icon></template>
              {{ t('settings.saveAndRecalc') }}
            </n-button>
          </n-spin>
        </n-card>
      </n-gi>
    </n-grid>
  </div>
</template>

<style scoped>
.settings-page { height: 100%; }
.venue-list { display: flex; flex-direction: column; gap: 12px; }
.venue-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px; background: rgba(255, 255, 255, 0.03); border-radius: 6px;
}
.venue-info { display: flex; align-items: center; gap: 10px; }
.venue-name { font-weight: 500; font-size: 14px; }

.backend-list { display: flex; flex-direction: column; gap: 10px; }
.backend-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 10px; background: rgba(255, 255, 255, 0.03); border-radius: 6px;
}
.backend-left { display: flex; align-items: center; gap: 8px; }
.backend-summary { font-weight: 500; }

.fee-table { display: flex; flex-direction: column; gap: 8px; }
.fee-header, .fee-row {
  display: grid;
  grid-template-columns: 1.2fr 1fr 0.8fr 0.8fr 1.6fr;
  gap: 12px;
  align-items: center;
  padding: 8px 12px;
  border-radius: 6px;
}
.fee-header { background: rgba(255, 255, 255, 0.05); }
.fee-row { background: rgba(255, 255, 255, 0.03); }
</style>
