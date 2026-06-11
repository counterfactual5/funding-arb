<script setup lang="ts">
import { reactive, onMounted } from 'vue'
import {
  NCard, NForm, NFormItem, NInputNumber, NButton, NGrid, NGi, NText, NTag, NSpin,
  NIcon, NDivider, NSelect, useMessage,
} from 'naive-ui'
import { CheckmarkCircleOutline, CloseCircleOutline, SaveOutline } from '@vicons/ionicons5'
import { getVenues, getCredentialsStatus, getStrategy, post, type StrategyParams } from '@/composables/useApi'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const message = useMessage()
const venues = getVenues()
const credentials = getCredentialsStatus()
const strategy = getStrategy()

const SCAN_VENUE_OPTIONS = [
  { label: 'Binance', value: 'binance' },
  { label: 'Bitget', value: 'bitget' },
  { label: 'Bybit', value: 'bybit' },
  { label: 'OKX', value: 'okx' },
  { label: 'Hyperliquid (DEX)', value: 'hyperliquid' },
  { label: 'Aster (DEX)', value: 'aster' },
  { label: 'Lighter (DEX)', value: 'lighter' },
]

const strategyForm = reactive({
  min_spread_annual: 0.04,
  min_edge_annual: 0.02,
  max_mark_spread_pct: 1.0,
  trade_usd: 5000,
  max_positions: 3,
  scan_interval_sec: 300,
  scan_venues: ['binance', 'bitget', 'bybit', 'okx', 'hyperliquid'] as string[],
  min_edge_1h: 0.01,
})

onMounted(async () => {
  await Promise.all([venues.refresh(), credentials.refresh(), strategy.refresh()])
  // Sync strategy form with API data
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
  }
})

async function handleSave() {
  try {
    await post<StrategyParams>('/settings/strategy', strategyForm)
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
              <n-select v-model:value="strategyForm.scan_venues" :options="SCAN_VENUE_OPTIONS" multiple style="width: 100%" />
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
</style>
