/**
 * Lightweight constant — venues that support browser wallet signing.
 * Kept in a separate file so Scanner.vue can check wallet-capability
 * without importing ethers / @nktkas/hyperliquid (which are heavy).
 */
export const WALLET_TRADE_VENUES = ["hyperliquid", "dydx"] as const;
export type WalletTradeVenue = (typeof WALLET_TRADE_VENUES)[number];
