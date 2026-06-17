<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NCard, NSpace, NText, NTag, NButton, NForm, NFormItem, NInput, NSelect,
  NSwitch, NDivider, NIcon, NAlert, NTooltip, NSkeleton,
} from 'naive-ui'
import {
  KeyOutline, WalletOutline, CheckmarkCircleOutline, LinkOutline,
} from '@vicons/ionicons5'

interface Field {
  key: string
  label: string
  type: string
  placeholder?: string
  options?: string[]
}

interface Schema {
  name: string
  chain?: string
  fields: Field[]
  extra_fields: Field[]
  live_flag?: string | null
}

interface Status {
  connected: boolean
  balance_usdc: number
  live_enabled: boolean
  fields_masked: Record<string, string>
}

interface ExtInfo {
  supported: boolean
  detected: boolean
  connected: boolean
  connecting: boolean
  address: string
  balance: number
  error: string | null
}

interface VenueMeta {
  scan_capable?: boolean
  trade_capable?: boolean
  trade_reason?: string
  missing_keys?: string[]
}

const VENUE_FALLBACK: Record<string, string> = {
  binance: 'Binance', bitget: 'Bitget', bybit: 'Bybit', okx: 'OKX',
  hyperliquid: 'Hyperliquid', aster: 'Aster', edgex: 'EdgeX', lighter: 'Lighter', dydx: 'dYdX v4',
}

const props = defineProps<{
  venue: string
  rank?: number
  isCex: boolean
  schema?: Schema
  status?: Status
  form: Record<string, string>
  showManual: boolean
  ext: ExtInfo
  meta?: VenueMeta
  loading?: boolean
  walletTradeCapable?: boolean  // supports browser wallet signing for orders
}>()

const emit = defineEmits<{
  (e: 'toggle-manual'): void
  (e: 'connect'): void
  (e: 'disconnect'): void
  (e: 'toggle-live', value: boolean): void
  (e: 'connect-ext'): void
  (e: 'test-order'): void
}>()

const { t } = useI18n()

const displayName = computed(() => props.schema?.name || VENUE_FALLBACK[props.venue] || props.venue)

// Connected if backend creds are set (env) OR a browser wallet extension is linked.
const isConnected = computed(() => !!props.status?.connected || props.ext.connected)

const allFields = computed(() => [
  ...(props.schema?.fields ?? []),
  ...(props.schema?.extra_fields ?? []),
])

/** Compact wallet-only cards get a floor height so paired rows align. */
const bodyMinHeight = computed(() => {
  if (isConnected.value) return undefined
  if (props.isCex || props.showManual || !props.ext.supported) return undefined
  return '148px'
})

function setField(key: string, value: string) {
  props.form[key] = value
}

function extLabel(): string {
  return props.venue === 'dydx'
    ? t('settings.connectKeplr')
    : t('settings.connectMetaMask')
}

function extInstallUrl(): string {
  return props.venue === 'dydx'
    ? 'https://www.keplr.app/download'
    : 'https://metamask.io/download/'
}

function shortAddress(addr: string): string {
  if (!addr || addr.length < 12) return addr
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`
}
</script>

<template>
  <n-card
    size="small"
    class="venue-card"
    :bordered="true"
  >
    <template #header>
      <div class="venue-card__header">
        <div class="venue-card__title-row">
          <n-icon :size="18" :color="isConnected ? '#18a058' : '#666'" class="venue-card__icon">
            <WalletOutline v-if="!isCex" />
            <KeyOutline v-else />
          </n-icon>
          <div>
            <n-space align="center" :size="6">
              <n-text strong style="font-size: 14px">{{ displayName }}</n-text>
              <n-tag v-if="rank" size="tiny" :bordered="false" type="default" class="venue-rank-tag">
                #{{ rank }}
              </n-tag>
            </n-space>
            <n-space :size="6" style="margin-top: 4px">
              <n-tag size="tiny" :bordered="false" :type="isCex ? 'default' : 'info'">
                {{ isCex ? 'CEX' : 'DEX' }}
              </n-tag>
              <n-tag v-if="schema?.chain && !isCex" size="tiny" :bordered="false">
                {{ schema.chain }}
              </n-tag>
              <n-tag
                v-if="meta?.scan_capable"
                size="tiny"
                :bordered="false"
                type="success"
              >
                {{ t('settings.scanOk') }}
              </n-tag>
              <n-tag
                v-if="meta && meta.trade_capable === false"
                size="tiny"
                :bordered="false"
                type="warning"
              >
                {{ t('settings.scanOnly') }}
              </n-tag>
              <n-tag
                v-if="walletTradeCapable"
                size="tiny"
                :bordered="false"
                type="info"
              >
                {{ t('settings.walletTradeOrder') }}
              </n-tag>
            </n-space>
          </div>
        </div>
        <n-tag size="small" :type="isConnected ? 'success' : 'default'" :bordered="false">
          <template v-if="isConnected" #icon>
            <n-icon :component="CheckmarkCircleOutline" />
          </template>
          {{ isConnected ? t('settings.walletConnected') : t('settings.walletNotConnected') }}
        </n-tag>
      </div>
    </template>

    <!-- Loading -->
    <template v-if="loading && !schema">
      <n-skeleton text :repeat="3" />
    </template>

    <!-- Connected (backend creds and/or browser wallet extension) -->
    <template v-else-if="isConnected">
      <div class="venue-card__connected">
        <div v-if="!isCex && ext.connected" class="venue-card__row">
          <n-text depth="3">{{ t('settings.walletExtAddress') }}</n-text>
          <n-tooltip trigger="hover">
            <template #trigger>
              <n-text type="success" style="font-family: monospace; font-size: 12px">
                {{ shortAddress(ext.address) }}
              </n-text>
            </template>
            {{ ext.address }}
          </n-tooltip>
        </div>
        <div v-for="(val, key) in (status?.fields_masked ?? {})" :key="key" class="venue-card__row">
          <n-text depth="3" style="font-size: 12px">{{ key }}</n-text>
          <n-text style="font-size: 12px; font-family: monospace">{{ val }}</n-text>
        </div>
        <n-divider style="margin: 10px 0" />
        <div class="venue-card__row">
          <n-text depth="3">{{ t('settings.balance') }}</n-text>
          <n-text strong>{{ (ext.connected ? ext.balance : (status?.balance_usdc ?? 0)).toFixed(2) }} USDC</n-text>
        </div>
        <!-- Live trading requires backend keys, not just an extension link. -->
        <div v-if="schema?.live_flag && status?.connected" class="venue-card__row" style="margin-top: 8px">
          <n-text depth="3">{{ t('settings.modeLive') }}</n-text>
          <n-switch :value="status.live_enabled" @update:value="(v: boolean) => emit('toggle-live', v)" />
        </div>
        <n-button size="small" type="warning" ghost block style="margin-top: 12px" @click="emit('disconnect')">
          {{ t('settings.disconnectWallet') }}
        </n-button>
        <!-- Wallet trade test order button -->
        <n-button
          v-if="walletTradeCapable && !isCex && ext.connected"
          size="small"
          type="primary"
          ghost
          block
          style="margin-top: 6px"
          @click="emit('test-order')"
        >
          {{ t('settings.walletTradeOrder') }}
        </n-button>
      </div>
    </template>

    <!-- Not connected -->
    <template v-else>
      <n-space vertical :size="12" class="venue-card__body" :style="bodyMinHeight ? { minHeight: bodyMinHeight } : undefined">
        <n-text v-if="isCex" depth="3" style="font-size: 12px; line-height: 1.5">
          {{ t('settings.cexApiHint') }}
        </n-text>
        <n-text v-else-if="ext.supported" depth="3" style="font-size: 12px; line-height: 1.5">
          {{ t('settings.dexWalletHint') }}
        </n-text>
        <n-text v-else depth="3" style="font-size: 12px; line-height: 1.5">
          {{ t('settings.dexKeysHint') }}
        </n-text>

        <!-- Browser wallet (dYdX / Hyperliquid) -->
        <template v-if="ext.supported">
          <n-button
            type="primary"
            size="small"
            block
            :loading="ext.connecting"
            :disabled="!ext.detected"
            @click="emit('connect-ext')"
          >
            <template #icon><n-icon :component="WalletOutline" /></template>
            {{ extLabel() }}
          </n-button>
          <n-alert v-if="!ext.detected" type="info" :bordered="false" style="padding: 8px 10px">
            <n-text style="font-size: 12px">{{ t('settings.walletExtNotDetected') }}</n-text>
            <n-button
              text
              tag="a"
              :href="extInstallUrl()"
              target="_blank"
              rel="noopener"
              size="tiny"
              style="margin-left: 4px"
            >
              <template #icon><n-icon :component="LinkOutline" /></template>
              {{ t('settings.installExtension') }}
            </n-button>
          </n-alert>
          <n-text v-if="ext.error" type="error" style="font-size: 12px">{{ ext.error }}</n-text>
          <n-divider v-if="showManual" style="margin: 4px 0">{{ t('settings.orManualEntry') }}</n-divider>
        </template>

        <!-- API / manual credentials -->
        <template v-if="showManual || isCex || !ext.supported">
          <n-form label-placement="top" size="small" class="venue-card__form">
            <n-form-item
              v-for="field in allFields"
              :key="field.key"
              :label="field.label"
              :show-require-mark="schema?.fields.some((f) => f.key === field.key)"
            >
              <n-input
                v-if="field.type !== 'select'"
                :value="form[field.key] ?? ''"
                :type="field.type === 'password' ? 'password' : 'text'"
                :placeholder="field.placeholder || field.label"
                show-password-on="click"
                @update:value="(v: string) => setField(field.key, v)"
              />
              <n-select
                v-else
                :value="form[field.key] ?? ''"
                :options="field.options?.map((o) => ({ label: o, value: o }))"
                :placeholder="field.label"
                @update:value="(v: string) => setField(field.key, v)"
              />
            </n-form-item>
            <n-button type="primary" size="small" block @click="emit('connect')">
              <template #icon><n-icon :component="KeyOutline" /></template>
              {{ isCex ? t('settings.saveApiKeys') : t('settings.connectWallet') }}
            </n-button>
          </n-form>
        </template>

        <!-- DEX + wallet ext: toggle manual form -->
        <n-button
          v-if="ext.supported && !isCex && showManual"
          text
          size="tiny"
          @click="emit('toggle-manual')"
        >
          {{ t('settings.hideManualEntry') }}
        </n-button>
        <n-button
          v-else-if="ext.supported && !isCex && !showManual"
          text
          size="tiny"
          @click="emit('toggle-manual')"
        >
          {{ t('settings.showManualEntry') }}
        </n-button>
        <!-- Note: wallet trading not supported for this venue -->
        <n-text
          v-if="!isCex && ext.supported && !walletTradeCapable"
          depth="3"
          style="font-size: 11px; font-style: italic"
        >
          {{ t('settings.walletTradeNotSupported') }}
        </n-text>
      </n-space>
    </template>
  </n-card>
</template>

<style scoped>
.venue-card {
  height: 100%;
  background: rgba(255, 255, 255, 0.02);
  transition: border-color 0.2s;
}
.venue-card:hover {
  border-color: rgba(255, 255, 255, 0.12);
}
.venue-card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}
.venue-card__title-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.venue-card__icon {
  margin-top: 2px;
  flex-shrink: 0;
}
.venue-card__connected {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.venue-card__row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.venue-card__form :deep(.n-form-item-label) {
  font-size: 12px;
  padding-bottom: 4px;
}
.venue-card__form :deep(.n-form-item) {
  margin-bottom: 12px;
}
.venue-card__body {
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
}
.venue-rank-tag {
  font-variant-numeric: tabular-nums;
  opacity: 0.85;
}
</style>
