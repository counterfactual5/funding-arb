<script setup lang="ts">
import { reactive, computed, onMounted } from 'vue'
import {
  NCard, NForm, NFormItem, NInputNumber, NButton, NIcon, NSelect, useMessage,
} from 'naive-ui'
import { SaveOutline } from '@vicons/ionicons5'
import {
  getVenues, getStrategy, getResolvedFees, post, type StrategyParams,
} from '@/composables/useApi'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const message = useMessage()
const venues = getVenues()
const strategy = getStrategy()
const resolvedFees = getResolvedFees()

const scanVenueOptions = computed(() =>
  (venues.data.value ?? []).map((v) => ({ label: v.name, value: v.id }))
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
  min_edge_mismatch: 0,
  fee_mode: 'auto' as 'auto' | 'api' | 'vip_tier',
  venue_fee_tiers: {} as Record<string, string>,
})

onMounted(async () => {
  await Promise.all([venues.refresh(), strategy.refresh(), resolvedFees.refresh()])
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
    if (typeof s.min_edge_mismatch === 'number') strategyForm.min_edge_mismatch = s.min_edge_mismatch
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
        <n-form-item :label="t('settings.minEdgeMismatch')">
          <n-input-number v-model:value="strategyForm.min_edge_mismatch" :min="0" :max="100" :step="0.005" style="width: 100%">
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
  </div>
</template>

<style scoped>
.settings-page {
  height: 100%;
  max-width: 880px;
  margin: 0 auto;
}

.settings-page :deep(.n-card) {
  border-radius: 8px;
}
</style>
