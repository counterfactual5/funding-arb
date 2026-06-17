<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NModal, NForm, NFormItem, NInputNumber, NSelect,
  NButton, NSpace, NAlert, NIcon, NDivider, NSwitch, useMessage,
} from 'naive-ui'
import { SwapHorizontalOutline } from '@vicons/ionicons5'
import { useWalletTrade } from '@/composables/wallet'

const props = defineProps<{
  show: boolean
  venue: string   // 'hyperliquid' or 'dydx'
  testnet?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
}>()

const { t } = useI18n()
const message = useMessage()
const { placeOrder, isAgentReady, ensureAgent, hlTradeState, dydxTradeState } = useWalletTrade()

const coin = ref('BTC')
const isBuy = ref<string>('buy')
const size = ref(0.001)
const slippage = ref(1) // percent
const ordering = ref(false)
const useTestnet = ref(!!props.testnet) // mutable, defaults to prop value

// Sync with prop when modal reopens
watch(() => props.show, (open) => {
  if (open) useTestnet.value = !!props.testnet
})

const COIN_OPTIONS = [
  { label: 'BTC', value: 'BTC' },
  { label: 'ETH', value: 'ETH' },
  { label: 'SOL', value: 'SOL' },
  { label: 'ARB', value: 'ARB' },
]

const SIDE_OPTIONS = computed(() => [
  { label: t('settings.testOrderBuy'), value: 'buy' },
  { label: t('settings.testOrderSell'), value: 'sell' },
])

const agentReady = computed(() => isAgentReady(props.venue))

const agentHint = computed(() => {
  if (props.venue === 'hyperliquid') return t('settings.walletTradeAgentHint')
  if (props.venue === 'dydx') return t('settings.walletTradeDydxHint')
  return ''
})

const venueState = computed(() => {
  if (props.venue === 'hyperliquid') return hlTradeState
  if (props.venue === 'dydx') return dydxTradeState
  return null
})

// Safe access to "approving" which only exists on HL state
const isApprovingOrOrdering = computed(() => {
  const s = venueState.value as any
  return s?.approving || s?.ordering
})

async function handleApprove() {
  const ok = await ensureAgent(props.venue, useTestnet.value)
  if (!ok) {
    message.error(venueState.value?.error || t('settings.walletTradeFailed'))
  } else {
    message.success(t('settings.walletTradeApproved'))
  }
}

async function handleSubmit() {
  ordering.value = true
  try {
    const result = await placeOrder({
      venue: props.venue,
      coin: coin.value,
      isBuy: isBuy.value === 'buy',
      size: size.value,
      slippage: slippage.value / 100, // convert % to fraction
      testnet: useTestnet.value,
    })
    if (result.success) {
      message.success(`${t('settings.walletTradeSuccess')} ${result.txHash ? `(tx: ${result.txHash.slice(0, 10)}...)` : ''}`)
      emit('update:show', false)
    } else {
      message.error(result.error || t('settings.walletTradeFailed'))
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : t('settings.walletTradeFailed'))
  } finally {
    ordering.value = false
  }
}

function handleClose() {
  emit('update:show', false)
}
</script>

<template>
  <n-modal :show="show" @update:show="emit('update:show', $event)" preset="card" :title="t('settings.testOrderTitle')" style="width: 440px">
    <n-form label-placement="left" label-width="80" size="small">
      <n-form-item :label="t('settings.testOrderCoin')">
        <n-select v-model:value="coin" :options="COIN_OPTIONS" />
      </n-form-item>
      <n-form-item :label="t('settings.testOrderSide')">
        <n-select v-model:value="isBuy" :options="SIDE_OPTIONS" />
      </n-form-item>
      <n-form-item :label="t('settings.testOrderSize')">
        <n-input-number v-model:value="size" :min="0.001" :step="0.001" :precision="4" style="width: 100%">
          <template #suffix>{{ coin }}</template>
        </n-input-number>
      </n-form-item>
      <n-form-item :label="t('settings.testOrderSlippage')">
        <n-input-number v-model:value="slippage" :min="0.1" :max="10" :step="0.1" :precision="1" style="width: 100%">
          <template #suffix>%</template>
        </n-input-number>
      </n-form-item>
      <n-form-item :label="t('settings.testOrderNetwork')">
        <n-space align="center" :size="8">
          <n-switch v-model:value="useTestnet">
            <template #checked>{{ t('settings.testOrderTestnet') }}</template>
            <template #unchecked>{{ t('settings.testOrderMainnet') }}</template>
          </n-switch>
        </n-space>
      </n-form-item>
    </n-form>

    <!-- Agent status -->
    <n-divider style="margin: 12px 0" />
    <n-alert v-if="!agentReady" type="info" :bordered="false" style="margin-bottom: 12px">
      {{ agentHint }}
    </n-alert>
    <n-alert v-if="venueState?.error" type="error" :bordered="false" style="margin-bottom: 12px">
      {{ venueState.error }}
    </n-alert>

    <template #footer>
      <n-space justify="end">
        <n-button size="small" @click="handleClose">{{ t('settings.testOrderCancel') }}</n-button>
        <n-button v-if="!agentReady" size="small" type="info" :loading="isApprovingOrOrdering" @click="handleApprove">
          {{ t('settings.walletTradeApprove') }}
        </n-button>
        <n-button v-else size="small" type="primary" :loading="ordering" @click="handleSubmit">
          <template #icon><n-icon :component="SwapHorizontalOutline" /></template>
          {{ t('settings.testOrderSubmit') }}
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>
