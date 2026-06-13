/**
 * Unified wallet-based trading composable.
 *
 * Provides a single interface for browser wallet order signing:
 *   - Hyperliquid: agent wallet (MetaMask → approve → agent key session)
 *   - dYdX v4:     Keplr per-tx signing
 *
 * Other DEX venues (Lighter, EdgeX, Aster) do NOT support wallet signing
 * and are excluded here.
 */
import { reactive } from "vue";
import { useWallet } from "@/composables/useWallet";
import { useHyperliquidTrade } from "@/composables/useHyperliquidTrade";
import { useDydxTrade } from "@/composables/useDydxTrade";

// ─── Which venues support browser wallet signing ───────────────

export const WALLET_TRADE_VENUES = ["hyperliquid", "dydx"] as const;
export type WalletTradeVenue = (typeof WALLET_TRADE_VENUES)[number];

export interface PlaceOrderParams {
  venue: string;
  coin: string; // e.g. 'BTC', 'ETH'
  isBuy: boolean;
  size: number; // base currency units
  slippage?: number;
  testnet?: boolean;
}

export interface OrderResult {
  success: boolean;
  txHash?: string;
  error?: string;
}

interface WalletTradeState {
  /** Whether wallet trade is available for a given venue */
  ready: Record<string, boolean>;
  /** Whether an order is in flight */
  ordering: boolean;
  /** Last result */
  lastResult: OrderResult | null;
  /** Per-venue error */
  errors: Record<string, string | null>;
}

const state = reactive<WalletTradeState>({
  ready: {},
  ordering: false,
  lastResult: null,
  errors: {},
});

export function useWalletTrade() {
  const { hasKeplr, hasMetaMask, keplrState, metamaskState } = useWallet();
  const {
    hlTradeState,
    approveAgent,
    placeOrder: hlPlaceOrder,
    checkExistingAgent,
  } = useHyperliquidTrade();
  const {
    dydxTradeState,
    placeOrder: dydxPlaceOrder,
    checkConnection: dydxCheckConnection,
  } = useDydxTrade();

  // ─── Venue readiness ────────────────────────────────────────

  function isVenueReady(venue: string): boolean {
    return state.ready[venue] === true;
  }

  /** Can this venue potentially do wallet signing? */
  function supportsWalletTrade(venue: string): boolean {
    return (WALLET_TRADE_VENUES as readonly string[]).includes(venue);
  }

  /** Does the user have the required extension connected? */
  function isWalletConnected(venue: string): boolean {
    if (venue === "hyperliquid")
      return hasMetaMask.value && metamaskState.connected;
    if (venue === "dydx") return hasKeplr.value && keplrState.connected;
    return false;
  }

  /** Is the signing agent/session active for this venue? */
  function isAgentReady(venue: string): boolean {
    if (venue === "hyperliquid") return hlTradeState.connected;
    if (venue === "dydx") return dydxTradeState.connected;
    return false;
  }

  // ─── Init / approve ─────────────────────────────────────────

  /** Ensure agent/session is active. Call after wallet extension connects. */
  async function ensureAgent(venue: string, testnet = false): Promise<boolean> {
    if (venue === "hyperliquid") {
      if (hlTradeState.connected) return true;
      return approveAgent(testnet);
    }
    if (venue === "dydx") {
      dydxCheckConnection();
      return dydxTradeState.connected;
    }
    return false;
  }

  // ─── Place order ────────────────────────────────────────────

  async function placeOrder(params: PlaceOrderParams): Promise<OrderResult> {
    state.ordering = true;
    state.lastResult = null;
    state.errors[params.venue] = null;

    let result: OrderResult;

    if (params.venue === "hyperliquid") {
      if (!hlTradeState.connected) {
        const ok = await approveAgent(params.testnet);
        if (!ok) {
          result = {
            success: false,
            error: hlTradeState.error || "Agent not approved",
          };
          state.lastResult = result;
          state.errors[params.venue] = result.error ?? null;
          state.ordering = false;
          return result;
        }
      }
      result = await hlPlaceOrder({
        coin: params.coin,
        isBuy: params.isBuy,
        size: params.size,
        slippage: params.slippage,
        testnet: params.testnet,
      });
    } else if (params.venue === "dydx") {
      result = await dydxPlaceOrder({
        coin: params.coin,
        isBuy: params.isBuy,
        size: params.size,
        slippage: params.slippage,
        testnet: params.testnet,
      });
    } else {
      result = {
        success: false,
        error: `${params.venue} does not support wallet trading`,
      };
    }

    state.lastResult = result;
    state.errors[params.venue] = result.error ?? null;
    state.ordering = false;
    return result;
  }

  // ─── Check existing sessions on mount ────────────────────────

  function init() {
    checkExistingAgent();
    dydxCheckConnection();
    state.ready.hyperliquid = hlTradeState.connected;
    state.ready.dydx = dydxTradeState.connected;
  }

  return {
    walletTradeState: state,
    supportsWalletTrade,
    isWalletConnected,
    isAgentReady,
    isVenueReady,
    ensureAgent,
    placeOrder,
    init,

    // Expose sub-states for direct UI binding
    hlTradeState,
    dydxTradeState,
  };
}
