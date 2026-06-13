<script setup lang="ts">
import { ref, h, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NConfigProvider,
  NLayout,
  NLayoutSider,
  NLayoutHeader,
  NLayoutContent,
  NMenu,
  NIcon,
  NBadge,
  NText,
  NTag,
  NMessageProvider,
  NSelect,
  darkTheme,
  type MenuOption,
} from 'naive-ui'
import {
  SearchOutline,
  BriefcaseOutline,
  StatsChartOutline,
  DocumentTextOutline,
  PulseOutline,
  KeyOutline,
  WalletOutline,
  ToggleOutline,
  CardOutline,
  LockClosedOutline,
} from '@vicons/ionicons5'
import { useWebSocket, type TradingMode } from '@/composables/useApi'
import i18n, { setLocale, SUPPORTED_LOCALES, type SupportedLocale } from '@/i18n'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const collapsed = ref(true)

const activeKey = computed(() =>
  route.path.startsWith('/docs') ? '/docs' : route.path,
)

const menuOptions = computed<MenuOption[]>(() => [
  {
    label: t('menu.scanner'),
    key: '/',
    icon: () => h(NIcon, null, { default: () => h(SearchOutline) }),
  },
  {
    label: t('menu.positions'),
    key: '/positions',
    icon: () => h(NIcon, null, { default: () => h(BriefcaseOutline) }),
  },
  {
    label: t('menu.backtest'),
    key: '/backtest',
    icon: () => h(NIcon, null, { default: () => h(StatsChartOutline) }),
  },
  {
    label: t('menu.cex'),
    key: '/cex',
    icon: () => h(NIcon, null, { default: () => h(KeyOutline) }),
  },
  {
    label: t('menu.dex'),
    key: '/dex',
    icon: () => h(NIcon, null, { default: () => h(WalletOutline) }),
  },
  {
    label: t('menu.strategy'),
    key: '/strategy',
    icon: () => h(NIcon, null, { default: () => h(ToggleOutline) }),
  },
  {
    label: t('menu.fees'),
    key: '/fees',
    icon: () => h(NIcon, null, { default: () => h(CardOutline) }),
  },
  {
    label: t('menu.advanced'),
    key: '/advanced',
    icon: () => h(NIcon, null, { default: () => h(LockClosedOutline) }),
  },
  {
    label: t('menu.docs'),
    key: '/docs',
    icon: () => h(NIcon, null, { default: () => h(DocumentTextOutline) }),
  },
])

const currentLocale = computed({
  get: () => i18n.global.locale.value as SupportedLocale,
  set: (val: SupportedLocale) => setLocale(val),
})

const { connected, connect: wsConnect, disconnect: wsDisconnect } = useWebSocket()

const tradingMode = ref<TradingMode | null>(null)
let tradingModeTimer: ReturnType<typeof setInterval> | null = null

async function fetchTradingMode() {
  try {
    const response = await fetch('/api/settings/trading-mode')
    const json = await response.json()
    if (json.success) tradingMode.value = json.data
  } catch {
    // ignore
  }
}

function handleMenuUpdate(key: string) {
  router.push(key)
}

onMounted(() => {
  wsConnect()
  fetchTradingMode()
  tradingModeTimer = setInterval(fetchTradingMode, 30000)
})

onUnmounted(() => {
  wsDisconnect()
  if (tradingModeTimer) {
    clearInterval(tradingModeTimer)
    tradingModeTimer = null
  }
})
</script>

<template>
  <n-config-provider :theme="darkTheme">
    <n-layout has-sider class="app-layout">
      <n-layout-sider
        bordered
        collapse-mode="width"
        :collapsed-width="64"
        :width="200"
        :collapsed="collapsed"
        show-trigger
        @collapse="collapsed = true"
        @expand="collapsed = false"
        :native-scrollbar="false"
        content-class="sider-content"
        class="app-sider"
      >
        <div class="sider-logo">
          <n-icon size="28" color="#18a058">
            <PulseOutline />
          </n-icon>
          <transition name="fade">
            <span v-if="!collapsed" class="logo-text">Funding Arb</span>
          </transition>
        </div>
        <n-menu
          :collapsed="collapsed"
          :collapsed-width="64"
          :collapsed-icon-size="22"
          :options="menuOptions"
          :value="activeKey"
          @update:value="handleMenuUpdate"
        />
      </n-layout-sider>

      <n-layout>
        <n-layout-header bordered class="app-header">
          <div class="header-left">
            <n-text class="header-title">{{ t('app.title') }}</n-text>
          </div>
          <div class="header-right">
            <n-tag
              v-if="tradingMode?.mode === 'live'"
              size="small"
              type="error"
              :bordered="false"
              style="margin-right: 4px"
            >
              LIVE
            </n-tag>
            <n-tag
              v-else
              size="small"
              type="warning"
              :bordered="false"
              style="margin-right: 4px"
            >
              {{ t('app.tradingMode') }}
            </n-tag>
            <n-select
              v-model:value="currentLocale"
              :options="SUPPORTED_LOCALES"
              size="tiny"
              style="width: 120px"
            />
            <n-badge :type="connected ? 'success' : 'error'" :dot="true" />
            <n-text depth="3" class="header-status">
              {{ connected ? t('app.connected') : t('app.disconnected') }}
            </n-text>
          </div>
        </n-layout-header>

        <n-layout-content class="app-content" :native-scrollbar="false">
          <n-message-provider>
            <router-view />
          </n-message-provider>
        </n-layout-content>
      </n-layout>
    </n-layout>
  </n-config-provider>
</template>

<style scoped>
.app-layout {
  height: 100vh;
  width: 100vw;
  box-sizing: border-box;
}

.app-sider {
  background-color: #101014 !important;
}

.sider-content {
  display: flex;
  flex-direction: column;
}

.sider-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  height: 56px;
  padding: 0 16px;
  border-bottom: 1px solid #2d2d30;
}

.logo-text {
  font-size: 16px;
  font-weight: 700;
  color: #18a058;
  white-space: nowrap;
  letter-spacing: 0.5px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.app-header {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background-color: #18181c;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-title {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.5px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-status {
  font-size: 13px;
}

.app-content {
  padding: 20px;
  background-color: #18181c;
  height: calc(100vh - 56px);
  box-sizing: border-box;
  overflow: auto;
}
</style>
