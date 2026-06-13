<script setup lang="ts">
import { reactive, onMounted, computed } from 'vue'
import {
  NCard, NForm, NFormItem, NText, NTag, NSelect, NButton, NDivider, NIcon, NSpin, useMessage,
} from 'naive-ui'
import { SaveOutline } from '@vicons/ionicons5'
import {
  getVenues, getStrategy, getFeeTiers, getResolvedFees, post, type StrategyParams,
} from '@/composables/useApi'
import { useI18n } from 'vue-i18n'
import { CEX_VENUE_RANK, DEX_VENUE_RANK } from '@/constants/venueOrder'

const { t } = useI18n()
const message = useMessage()
const venues = getVenues()
const strategy = getStrategy()
const feeTiers = getFeeTiers()
const resolvedFees = getResolvedFees()

const strategyForm = reactive({
  fee_mode: 'auto' as 'auto' | 'api' | 'vip_tier',
  venue_fee_tiers: {} as Record<string, string>,
})

const feeModeOptions = computed(() => [
  { label: t('settings.feeModeAuto'), value: 'auto' },
  { label: t('settings.feeModeApi'), value: 'api' },
  { label: t('settings.feeModeVip'), value: 'vip_tier' },
])

const FEE_VENUE_ORDER = computed(() => {
  const rank = new Map<string, number>(
    [...CEX_VENUE_RANK, ...DEX_VENUE_RANK].map((id, i) => [id, i]),
  )
  const ids = (venues.data.value ?? []).map((v) => v.id)
  return [...ids].sort((a, b) => (rank.get(a) ?? 999) - (rank.get(b) ?? 999))
})

const scanVenueOptions = computed(() =>
  (venues.data.value ?? []).map((v) => ({ label: v.name, value: v.id }))
)

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

function onTierChange(venueId: string, tierId: string) {
  strategyForm.venue_fee_tiers = { ...strategyForm.venue_fee_tiers, [venueId]: tierId }
}

onMounted(async () => {
  await Promise.all([venues.refresh(), strategy.refresh(), feeTiers.refresh(), resolvedFees.refresh()])
  const s = strategy.data.value
  if (s) {
    if (s.fee_mode) strategyForm.fee_mode = s.fee_mode
    if (s.venue_fee_tiers) strategyForm.venue_fee_tiers = { ...s.venue_fee_tiers }
  }
})

async function handleSave() {
  try {
    await post<StrategyParams>('/settings/strategy', strategyForm)
    await resolvedFees.refresh()
    try { await post('/scanner/recalc-fees', {}) } catch { /* No cached scan yet */ }
    message.success(t('settings.settingsSaved'))
  } catch (e) {
    message.error(e instanceof Error ? e.message : t('settings.failedToSave'))
  }
}
</script>

<template>
  <div class="settings-page">
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
  </div>
</template>

<style scoped>
.settings-page {
  height: 100%;
  max-width: 1200px;
  margin: 0 auto;
}

.fee-table {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.fee-header,
.fee-row {
  display: grid;
  grid-template-columns: 1.2fr 1fr 0.8fr 0.8fr 1.6fr;
  gap: 12px;
  align-items: center;
  min-height: 44px;
  padding: 8px 12px;
  border-radius: 6px;
}

.fee-header {
  background: rgba(255, 255, 255, 0.05);
  position: sticky;
  top: 0;
  z-index: 1;
}

.fee-row {
  background: rgba(255, 255, 255, 0.03);
}

.fee-row:hover {
  background: rgba(255, 255, 255, 0.055);
}

@media (max-width: 900px) {
  .fee-table {
    overflow-x: auto;
    padding-bottom: 4px;
  }
  .fee-header,
  .fee-row {
    min-width: 780px;
  }
}
</style>
