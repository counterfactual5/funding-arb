<script setup lang="ts">
import { reactive, onMounted, ref, computed } from 'vue'
import { NCard, NText, NTag, NSpin, NIcon, useMessage } from 'naive-ui'
import { CheckmarkCircleOutline } from '@vicons/ionicons5'
import {
  getVenues, getWalletSchemas, getWalletStatus, connectWallet, disconnectWallet, getTradingMode,
} from '@/composables/useApi'
import { useI18n } from 'vue-i18n'
import { useWallet, useWalletTrade } from '@/composables/wallet'
import { WALLET_TRADE_VENUES } from '@/constants/walletTrade'
import { VenueConnectGroupGrid, TestOrderModal } from '@/components/connection'
import { DEX_VENUE_RANK } from '@/constants/venueOrder'

const { t } = useI18n()
const message = useMessage()
const venues = getVenues()
const walletSchemas = getWalletSchemas()
const walletStatus = getWalletStatus()
const tradingMode = getTradingMode()

const walletForms = reactive<Record<string, Record<string, string>>>({})
const showManualEntry = reactive<Record<string, boolean>>({})

const DEX_VENUES = [...DEX_VENUE_RANK]

const {
  hasKeplr, hasMetaMask,
  keplrState, metamaskState,
  connectKeplr, disconnectKeplr,
  connectMetaMask, disconnectMetaMask,
} = useWallet()

// Initialize wallet trade composable (sets up agent session checks)
const { hlTradeState, init: initWalletTrade } = useWalletTrade()

// Agent approval status per venue (session-scoped)
const agentStatus = computed<Record<string, { active: boolean; address: string }>>(() => ({
  hyperliquid: {
    active: hlTradeState.connected,
    address: hlTradeState.agentAddress,
  },
}))

// Test order modal state
const showTestOrder = ref(false)
const testOrderVenue = ref('hyperliquid')
const testOrderTestnet = ref(false)

function openTestOrder(venue: string) {
  testOrderVenue.value = venue
  // Check testnet from wallet form
  if (venue === 'hyperliquid') testOrderTestnet.value = walletForms.hyperliquid?.HYPERLIQUID_NETWORK === 'testnet'
  else if (venue === 'dydx') testOrderTestnet.value = walletForms.dydx?.DYDX_NETWORK === 'testnet'
  showTestOrder.value = true
}

function walletTradeCapable(venue: string): boolean {
  return (WALLET_TRADE_VENUES as readonly string[]).includes(venue)
}

const WALLET_EXT: Record<string, 'keplr' | 'metamask'> = {
  dydx: 'keplr',
  hyperliquid: 'metamask',
  lighter: 'metamask',
  edgex: 'metamask',
  aster: 'metamask',
}

function supportsWalletExt(venue: string): boolean { return venue in WALLET_EXT }

function hasWalletExtension(venue: string): boolean {
  if (venue === 'dydx') return hasKeplr.value
  if (['hyperliquid', 'lighter', 'edgex', 'aster'].includes(venue)) return hasMetaMask.value
  return false
}

function isWalletExtConnected(venue: string): boolean {
  if (venue === 'dydx') return keplrState.connected
  if (['hyperliquid', 'lighter', 'edgex', 'aster'].includes(venue)) return metamaskState.connected
  return false
}

function getWalletExtAddress(venue: string): string {
  if (venue === 'dydx') return keplrState.address
  if (['hyperliquid', 'lighter', 'edgex', 'aster'].includes(venue)) return metamaskState.address
  return ''
}

function getWalletExtBalance(venue: string): number {
  if (venue === 'dydx') return keplrState.balance
  if (['hyperliquid', 'lighter', 'edgex', 'aster'].includes(venue)) return metamaskState.balance
  return 0
}

function isWalletExtConnecting(venue: string): boolean {
  if (venue === 'dydx') return keplrState.connecting
  if (['hyperliquid', 'lighter', 'edgex', 'aster'].includes(venue)) return metamaskState.connecting
  return false
}

function getWalletExtError(venue: string): string | null {
  if (venue === 'dydx') return keplrState.error
  if (['hyperliquid', 'lighter', 'edgex', 'aster'].includes(venue)) return metamaskState.error
  return null
}

async function connectWalletExtension(venue: string) {
  if (venue === 'dydx') {
    await connectKeplr(walletForms.dydx?.DYDX_NETWORK === 'testnet')
  } else if (venue === 'hyperliquid') {
    await connectMetaMask(walletForms.hyperliquid?.HYPERLIQUID_NETWORK === 'testnet')
  } else if (venue === 'lighter') {
    await connectMetaMask()
    if (metamaskState.connected && metamaskState.address) {
      if (!walletForms.lighter) walletForms.lighter = {}
      walletForms.lighter['LIGHTER_L1_ADDRESS'] = metamaskState.address
    }
  } else if (venue === 'edgex') {
    await connectMetaMask()
  } else if (venue === 'aster') {
    await connectMetaMask()
  }
}

function extInfo(venue: string) {
  return {
    supported: supportsWalletExt(venue),
    detected: hasWalletExtension(venue),
    connected: isWalletExtConnected(venue),
    connecting: isWalletExtConnecting(venue),
    address: getWalletExtAddress(venue),
    balance: getWalletExtBalance(venue),
    error: getWalletExtError(venue),
  }
}

function disconnectWalletExtension(venue: string) {
  if (venue === 'dydx') disconnectKeplr()
  else disconnectMetaMask()
}

function venueMeta(venueId: string) {
  return venues.data.value?.find((v) => v.id === venueId)
}

function formFor(venue: string): Record<string, string> {
  if (!walletForms[venue]) walletForms[venue] = {}
  return walletForms[venue]
}

function toggleManualEntry(venue: string) { showManualEntry[venue] = !showManualEntry[venue] }

function defaultManualOpen(_venue: string): boolean { return false }

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
    disconnectWalletExtension(venue)
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
    for (const vid of DEX_VENUES) {
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
  // Restore agent session from sessionStorage (if wallet is still connected)
  initWalletTrade()
})
</script>

<template>
  <div class="settings-page">
    <n-card :title="t('settings.dexColumn')">
      <template #header-extra>
        <n-tag v-if="tradingMode.data.value" :type="tradingMode.data.value.mode === 'live' ? 'error' : 'warning'" :bordered="false" size="small">
          {{ tradingMode.data.value.mode === 'live' ? t('settings.modeLive') : t('settings.modeDryRun') }}
        </n-tag>
      </template>
      <div class="page-intro">
        <n-text depth="3">
          {{ t('settings.venueConnectionHint') }}
        </n-text>
        <n-text depth="3">
          {{ t('settings.dexRankHint') }}
        </n-text>
      </div>
      <div v-if="agentStatus.hyperliquid?.active" class="agent-badge-row">
        <n-tag size="small" type="success" :bordered="false" round>
          <template #icon><n-icon><CheckmarkCircleOutline /></n-icon></template>
          Hyperliquid Agent Active
        </n-tag>
        <n-text depth="3" style="font-size: 11px; font-family: monospace">
          {{ agentStatus.hyperliquid.address.slice(0, 8) }}...{{ agentStatus.hyperliquid.address.slice(-4) }}
        </n-text>
        <n-text depth="3" style="font-size: 11px">
          (session ends when tab closes)
        </n-text>
      </div>
      <n-spin :show="walletSchemas.loading.value || walletStatus.loading.value">
        <VenueConnectGroupGrid
          :venue-ids="DEX_VENUES"
          :rank-order="DEX_VENUE_RANK"
          :schemas="walletSchemas.data.value"
          :is-cex="false"
          :loading="walletSchemas.loading.value"
          :form-for="formFor"
          :show-manual-for="(v) => !!showManualEntry[v]"
          :ext-for="extInfo"
          :meta-for="venueMeta"
          :status-for="(v) => walletStatus.data.value?.[v]"
          :schema-for="(v) => walletSchemas.data.value?.[v]"
          :wallet-trade-capable="walletTradeCapable"
          @toggle-manual="toggleManualEntry"
          @connect="handleConnect"
          @disconnect="handleDisconnect"
          @toggle-live="handleToggleLive"
          @connect-ext="connectWalletExtension"
          @test-order="openTestOrder"
        />
      </n-spin>
    </n-card>

    <!-- Wallet test order modal -->
    <TestOrderModal
      v-model:show="showTestOrder"
      :venue="testOrderVenue"
      :testnet="testOrderTestnet"
    />
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

.agent-badge-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
  background: rgba(24, 160, 88, 0.06);
  border-radius: 6px;
  border: 1px solid rgba(24, 160, 88, 0.15);
}
</style>
