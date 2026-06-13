import { reactive } from "vue";
import { ethers } from "ethers";
import { ExchangeClient, HttpTransport, InfoClient } from "@nktkas/hyperliquid";
import { useWallet } from "@/composables/useWallet";

// ─── Types ────────────────────────────────────────────────────

const SESSION_KEY = "hl_agent_wallet";

interface StoredAgent {
  privateKey: string;
  address: string;
}

interface AgentState {
  connected: boolean;
  approving: boolean; // waiting for MetaMask approveAgent signature
  ordering: boolean; // placing an order
  address: string; // user's MetaMask address (L1)
  agentAddress: string; // derived agent address
  error: string | null;
  lastTxHash: string | null;
}

// ─── Shared state (singleton per module) ──────────────────────

const state = reactive<AgentState>({
  connected: false,
  approving: false,
  ordering: false,
  address: "",
  agentAddress: "",
  error: null,
  lastTxHash: null,
});

// ─── Helpers ──────────────────────────────────────────────────

function getOrCreateAgentKey(): StoredAgent {
  const stored = sessionStorage.getItem(SESSION_KEY);
  if (stored) {
    return JSON.parse(stored) as StoredAgent;
  }
  const wallet = ethers.Wallet.createRandom();
  const entry: StoredAgent = {
    privateKey: wallet.privateKey,
    address: wallet.address,
  };
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(entry));
  return entry;
}

function clearAgentKey() {
  sessionStorage.removeItem(SESSION_KEY);
}

function createTransport(testnet: boolean): HttpTransport {
  return new HttpTransport({ isTestnet: testnet });
}

/**
 * Resolve a coin symbol (e.g. "BTC") to its numeric asset ID by fetching
 * perpetual metadata. Returns `undefined` if the coin is not found.
 */
async function resolveAssetId(
  transport: HttpTransport,
  coin: string,
): Promise<number | undefined> {
  const info = new InfoClient({ transport });
  const meta = await info.meta();
  const idx = meta.universe.findIndex(
    (a) => a.name.toLowerCase() === coin.toLowerCase(),
  );
  return idx >= 0 ? idx : undefined;
}

/**
 * Get a reasonable limit price for a market order by looking at the L2 book.
 * Adds slippage on top of the best bid/ask to improve fill probability.
 */
async function getMarketPrice(
  transport: HttpTransport,
  coin: string,
  isBuy: boolean,
  slippage: number,
): Promise<string> {
  const info = new InfoClient({ transport });
  const book = await info.l2Book({ coin });
  if (!book) throw new Error(`No order book for ${coin}`);

  const [bids, asks] = book.levels;

  if (isBuy) {
    const bestAsk = asks.length > 0 ? parseFloat(asks[0].px) : 0;
    if (bestAsk === 0) throw new Error(`No asks in book for ${coin}`);
    return (bestAsk * (1 + slippage)).toFixed(
      bestAsk >= 1000 ? 1 : bestAsk >= 1 ? 2 : 4,
    );
  } else {
    const bestBid = bids.length > 0 ? parseFloat(bids[0].px) : 0;
    if (bestBid === 0) throw new Error(`No bids in book for ${coin}`);
    return (bestBid * (1 - slippage)).toFixed(
      bestBid >= 1000 ? 1 : bestBid >= 1 ? 2 : 4,
    );
  }
}

// ─── Composable ───────────────────────────────────────────────

export function useHyperliquidTrade() {
  const { metamaskState, hasMetaMask } = useWallet();

  /**
   * Approve an agent wallet via MetaMask EIP-712 signature.
   * This only needs to happen once per agent key — after approval,
   * the agent private key (stored in sessionStorage) can sign all orders.
   */
  async function approveAgent(testnet = false): Promise<boolean> {
    if (!hasMetaMask.value || !metamaskState.address) {
      state.error = "MetaMask not connected";
      return false;
    }

    state.approving = true;
    state.error = null;
    state.address = metamaskState.address;

    try {
      const ethereum = (window as any).ethereum;
      const provider = new ethers.BrowserProvider(ethereum);
      const signer = await provider.getSigner();

      const agent = getOrCreateAgentKey();
      state.agentAddress = agent.address;

      const transport = createTransport(testnet);
      const exchange = new ExchangeClient({ transport, wallet: signer });

      // approveAgent is a user-signed (EIP-712) action — triggers MetaMask popup
      await exchange.approveAgent({
        agentAddress: agent.address,
        agentName: "FundingArb",
      });

      state.connected = true;
      return true;
    } catch (e: any) {
      state.error =
        e?.message || e?.cause?.message || "Failed to approve agent";
      return false;
    } finally {
      state.approving = false;
    }
  }

  /**
   * Place a market (IOC) order using the agent wallet.
   * The price is derived from the L2 book with slippage applied.
   */
  async function placeOrder(params: {
    coin: string; // e.g. 'BTC', 'ETH'
    isBuy: boolean;
    size: number; // in base currency (e.g. 0.01 BTC)
    slippage?: number; // default 0.01 (1%)
    testnet?: boolean;
  }): Promise<{ success: boolean; txHash?: string; error?: string }> {
    if (!state.connected) {
      return {
        success: false,
        error: "Agent not approved. Call approveAgent first.",
      };
    }

    state.ordering = true;
    state.error = null;

    try {
      const testnet = params.testnet ?? false;
      const slippage = params.slippage ?? 0.01;

      const agent = getOrCreateAgentKey();
      const agentWallet = new ethers.Wallet(agent.privateKey);
      const transport = createTransport(testnet);

      // Resolve coin → asset ID
      const assetId = await resolveAssetId(transport, params.coin);
      if (assetId === undefined) {
        throw new Error(`Unknown coin: ${params.coin}`);
      }

      // Get a price from the order book for the IOC order
      const limitPx = await getMarketPrice(
        transport,
        params.coin,
        params.isBuy,
        slippage,
      );

      const exchange = new ExchangeClient({ transport, wallet: agentWallet });

      const result = await exchange.order({
        orders: [
          {
            a: assetId,
            b: params.isBuy,
            p: limitPx,
            s: String(params.size),
            r: false,
            t: { limit: { tif: "Ioc" } },
          },
        ],
        grouping: "na",
      });

      // Check individual order status (union type: object variants + string literals)
      const firstStatus = result.response.data.statuses[0];
      if (typeof firstStatus === "object" && "error" in firstStatus) {
        const errMsg = firstStatus.error as string;
        state.error = errMsg;
        return { success: false, error: errMsg };
      }

      // filled or resting — both are success
      let oid: string | number = "sent";
      if (typeof firstStatus === "object" && "filled" in firstStatus) {
        oid = firstStatus.filled.oid;
      } else if (typeof firstStatus === "object" && "resting" in firstStatus) {
        oid = firstStatus.resting.oid;
      }
      state.lastTxHash = String(oid);
      return { success: true, txHash: state.lastTxHash ?? undefined };
    } catch (e: any) {
      const msg = e?.message || e?.cause?.message || "Order failed";
      state.error = msg;
      return { success: false, error: msg };
    } finally {
      state.ordering = false;
    }
  }

  /**
   * Close an existing position by placing a reduce-only market (IOC) order
   * in the opposite direction.
   */
  async function closePosition(params: {
    coin: string;
    testnet?: boolean;
  }): Promise<{ success: boolean; txHash?: string; error?: string }> {
    if (!state.connected) {
      return { success: false, error: "Agent not approved" };
    }

    state.ordering = true;
    state.error = null;

    try {
      const testnet = params.testnet ?? false;
      const transport = createTransport(testnet);

      // Fetch current positions via InfoClient
      const info = new InfoClient({ transport });
      const clearingState = await info.clearinghouseState({
        user: state.address,
      });

      const pos = clearingState.assetPositions.find(
        (p) => p.position.coin.toLowerCase() === params.coin.toLowerCase(),
      );

      if (!pos || parseFloat(pos.position.szi) === 0) {
        return { success: false, error: `No open position for ${params.coin}` };
      }

      const size = Math.abs(parseFloat(pos.position.szi));
      // If szi < 0 we are short → close with buy; if szi > 0 we are long → close with sell
      const isBuy = parseFloat(pos.position.szi) < 0;

      // Resolve coin → asset ID
      const assetId = await resolveAssetId(transport, params.coin);
      if (assetId === undefined) {
        throw new Error(`Unknown coin: ${params.coin}`);
      }

      // Get price for the closing order
      const limitPx = await getMarketPrice(transport, params.coin, isBuy, 0.01);

      const agent = getOrCreateAgentKey();
      const agentWallet = new ethers.Wallet(agent.privateKey);
      const exchange = new ExchangeClient({ transport, wallet: agentWallet });

      const result = await exchange.order({
        orders: [
          {
            a: assetId,
            b: isBuy,
            p: limitPx,
            s: String(size),
            r: true, // reduce-only
            t: { limit: { tif: "Ioc" } },
          },
        ],
        grouping: "na",
      });

      const firstStatus = result.response.data.statuses[0];
      if (typeof firstStatus === "object" && "error" in firstStatus) {
        const errMsg = firstStatus.error as string;
        state.error = errMsg;
        return { success: false, error: errMsg };
      }

      let oid: string | number = "sent";
      if (typeof firstStatus === "object" && "filled" in firstStatus) {
        oid = firstStatus.filled.oid;
      } else if (typeof firstStatus === "object" && "resting" in firstStatus) {
        oid = firstStatus.resting.oid;
      }
      return { success: true, txHash: String(oid) };
    } catch (e: any) {
      const msg = e?.message || e?.cause?.message || "Close failed";
      state.error = msg;
      return { success: false, error: msg };
    } finally {
      state.ordering = false;
    }
  }

  /** Disconnect: clear the agent key and reset state. */
  function disconnect() {
    clearAgentKey();
    state.connected = false;
    state.address = "";
    state.agentAddress = "";
    state.error = null;
    state.lastTxHash = null;
  }

  /**
   * Check if an agent key already exists in sessionStorage and MetaMask
   * is connected — if so, restore the connected state without another signature.
   */
  function checkExistingAgent() {
    const stored = sessionStorage.getItem(SESSION_KEY);
    if (stored && metamaskState.connected) {
      const parsed = JSON.parse(stored) as StoredAgent;
      state.connected = true;
      state.address = metamaskState.address;
      state.agentAddress = parsed.address;
    }
  }

  return {
    hlTradeState: state,
    approveAgent,
    placeOrder,
    closePosition,
    disconnect,
    checkExistingAgent,
  };
}
