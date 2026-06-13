<script setup lang="ts">
import { computed } from 'vue'
import { NText } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import {
  groupByCredentialRows,
  type WalletSchemaFields,
} from '@/constants/venueOrder'
import type { VenueConfig, WalletVenueSchema, WalletVenueStatus } from '@/composables/useApi'
import VenueConnectCard from '@/components/VenueConnectCard.vue'

const props = defineProps<{
  venueIds: readonly string[]
  rankOrder: readonly string[]
  schemas?: Record<string, WalletSchemaFields> | null
  isCex: boolean
  loading?: boolean
  formFor: (venue: string) => Record<string, string>
  showManualFor: (venue: string) => boolean
  extFor: (venue: string) => {
    supported: boolean
    detected: boolean
    connected: boolean
    connecting: boolean
    address: string
    balance: number
    error: string | null
  }
  metaFor: (venue: string) => VenueConfig | undefined
  statusFor: (venue: string) => WalletVenueStatus | undefined
  schemaFor: (venue: string) => WalletVenueSchema | undefined
  walletTradeCapable?: (venue: string) => boolean
}>()

const emit = defineEmits<{
  (e: 'toggle-manual', venue: string): void
  (e: 'connect', venue: string): void
  (e: 'disconnect', venue: string): void
  (e: 'toggle-live', venue: string, value: boolean): void
  (e: 'connect-ext', venue: string): void
  (e: 'test-order', venue: string): void
}>()

const { t } = useI18n()

const groups = computed(() =>
  groupByCredentialRows(
    props.venueIds,
    props.schemas ?? undefined,
    props.rankOrder,
  ),
)

</script>

<template>
  <div class="venue-groups">
    <section
      v-for="group in groups"
      :key="group.rows"
      class="venue-section"
    >
      <n-text depth="3" class="venue-section__label">
        {{ t('settings.credentialRowsSection', { n: group.rows }) }}
      </n-text>
      <div class="venue-grid">
        <VenueConnectCard
          v-for="venueId in group.venues"
          :key="venueId"
          :venue="venueId"
          :is-cex="isCex"
          :schema="schemaFor(venueId)"
          :status="statusFor(venueId)"
          :form="formFor(venueId)"
          :show-manual="showManualFor(venueId)"
          :ext="extFor(venueId)"
          :meta="metaFor(venueId)"
          :loading="loading"
          :wallet-trade-capable="walletTradeCapable?.(venueId)"
          @toggle-manual="emit('toggle-manual', venueId)"
          @connect="emit('connect', venueId)"
          @disconnect="emit('disconnect', venueId)"
          @toggle-live="(v: boolean) => emit('toggle-live', venueId, v)"
          @connect-ext="emit('connect-ext', venueId)"
          @test-order="emit('test-order', venueId)"
        />
      </div>
    </section>
  </div>
</template>

<style scoped>
.venue-groups {
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.venue-section__label {
  display: block;
  margin-bottom: 10px;
  font-size: 12px;
  letter-spacing: 0.02em;
}

.venue-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  align-items: stretch;
}

@media (max-width: 900px) {
  .venue-grid {
    grid-template-columns: 1fr;
  }
}
</style>
