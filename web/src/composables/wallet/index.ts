/**
 * Wallet composables barrel — re-export everything for clean imports.
 *
 * Usage: `import { useWallet, useWalletTrade } from '@/composables/wallet'`
 */
export { useWallet } from './useWallet'
export { useWalletTrade, type PlaceOrderParams, type OrderResult } from './useWalletTrade'
export { useHyperliquidTrade } from './useHyperliquidTrade'
export { useDydxTrade } from './useDydxTrade'
