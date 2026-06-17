<script setup lang="ts">
import { reactive, onMounted } from 'vue'
import { NCard, NText, NTag, NSpin, useMessage } from 'naive-ui'
import {
  getVenues, getWalletSchemas, getWalletStatus,
  connectWallet, disconnectWallet, getTradingMode,
} from '@/composables/useApi'
import { useI18n } from 'vue-i18n'
import { VenueConnectGroupGrid } from '@/components/connection'
import { CEX_VENUE_RANK } from '@/constants/venueOrder'

const { t } = useI18n()
const message = useMessage()
const venues = getVenues()
const walletSchemas = getWalletSchemas()
const walletStatus = getWalletStatus()
const tradingMode = getTradingMode()

const walletForms = reactive<Record<string, Record<string, string>>>({})
const showManualEntry = reactive<Record<string, boolean>>({})

const CEX_VENUES = [...CEX_VENUE_RANK]

function extInfo(_venue: string) {
  return { supported: false, detected: false, connected: false, connecting: false, address: '', balance: 0, error: null }
}

function venueMeta(venueId: string) {
  return venues.data.value?.find((v) => v.id === venueId)
}

function formFor(venue: string): Record<string, string> {
  if (!walletForms[venue]) walletForms[venue] = {}
  return walletForms[venue]
}

function toggleManualEntry(venue: string) {
  showManualEntry[venue] = !showManualEntry[venue]
}

function defaultManualOpen(_venue: string): boolean {
  return true
}

async function handleConnect(venue: string) {
  try {
    await connectWallet(venue, walletForms[venue] || {})
    await walletStatus.refresh()
    await tradingMode.refresh()
    message.success(t('settings.connectSuccess'))
  } catch (e) {
    message.error(e instanceof Error ? e.message : t('settings.connectFailed'))
  }
}

async function handleDisconnect(venue: string) {
  try {
    await disconnectWallet(venue)
    await walletStatus.refresh()
    await tradingMode.refresh()
    const schemas = walletSchemas.data.value
    if (schemas?.[venue]) {
      const form: Record<string, string> = {}
      for (const f of [...schemas[venue].fields, ...schemas[venue].extra_fields]) { form[f.key] = f.default || '' }
      if (schemas[venue].live_flag) { form[schemas[venue].live_flag] = '' }
      walletForms[venue] = form
    }
    message.success(t('settings.disconnectSuccess'))
  } catch (e) {
    message.error(e instanceof Error ? e.message : t('settings.disconnectFailed'))
  }
}

async function handleToggleLive(venue: string, enabled: boolean) {
  const schema = walletSchemas.data.value?.[venue]
  if (!schema?.live_flag) return
  try {
    await connectWallet(venue, { [schema.live_flag]: enabled ? '1' : '0' })
    await walletStatus.refresh()
    await tradingMode.refresh()
  } catch (e) {
    message.error(t('settings.connectFailed'))
  }
}

onMounted(async () => {
  await Promise.all([venues.refresh(), walletSchemas.refresh(), walletStatus.refresh(), tradingMode.refresh()])
  const schemas = walletSchemas.data.value
  if (schemas) {
    for (const vid of CEX_VENUES) {
      const ws = schemas[vid]
      if (ws) {
        const form: Record<string, string> = {}
        for (const f of [...ws.fields, ...ws.extra_fields]) { form[f.key] = f.default || '' }
        if (ws.live_flag) { form[ws.live_flag] = '' }
        walletForms[vid] = form
      }
      if (showManualEntry[vid] === undefined) showManualEntry[vid] = defaultManualOpen(vid)
    }
  }
})
</script>

<template>
  <div class="settings-page">
    <n-card :title="t('settings.cexColumn')" size="small">
      <template #header-extra>
        <n-tag
          v-if="tradingMode.data.value"
          :type="tradingMode.data.value.mode === 'live' ? 'error' : 'warning'"
          :bordered="false"
        >
          {{ tradingMode.data.value.mode === 'live' ? t('settings.modeLive') : t('settings.modeDryRun') }}
        </n-tag>
      </template>

      <div class="page-intro">
        <n-text depth="3">
          {{ t('settings.venueConnectionHint') }}
        </n-text>
        <n-text depth="3">
          {{ t('settings.cexRankHint') }}
        </n-text>
      </div>

      <n-spin :show="walletSchemas.loading.value || walletStatus.loading.value">
        <VenueConnectGroupGrid
          :venue-ids="CEX_VENUES"
          :rank-order="CEX_VENUE_RANK"
          :schemas="walletSchemas.data.value"
          :is-cex="true"
          :loading="walletSchemas.loading.value"
          :form-for="formFor"
          :show-manual-for="(v) => showManualEntry[v] !== false"
          :ext-for="extInfo"
          :meta-for="venueMeta"
          :status-for="(v) => walletStatus.data.value?.[v]"
          :schema-for="(v) => walletSchemas.data.value?.[v]"
          @toggle-manual="toggleManualEntry"
          @connect="handleConnect"
          @disconnect="handleDisconnect"
          @toggle-live="handleToggleLive"
        />
      </n-spin>
    </n-card>
  </div>
</template>

<style scoped>
.settings-page {
  height: 100%;
  max-width: 1440px;
  margin: 0 auto;
}

.page-intro {
  display: grid;
  gap: 4px;
  margin-bottom: 16px;
  font-size: 12px;
  line-height: 1.6;
}

</style>
