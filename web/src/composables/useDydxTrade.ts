/**
 * dYdX v4 browser-based wallet trading via Keplr signing.
 *
 * Every order requires Keplr to sign a Cosmos SDK transaction (standard dApp
 * flow — user sees and confirms each order in the Keplr popup).
 *
 * This module uses raw Keplr APIs and REST endpoints — no @cosmjs dependency.
 * The Amino signing path is used for simplicity; for production, prefer the
 * signDirect (protobuf) path with @dydxprotocol/v4-client-js.
 */
import { reactive } from "vue";
import { useWallet } from "@/composables/useWallet";

// ─── Types ─────────────────────────────────────────────────────────

export interface DydxOrderState {
  connected: boolean;
  ordering: boolean;
  address: string;
  error: string | null;
  lastTxHash: string | null;
  subaccountNumber: number;
}

export interface DydxPlaceOrderParams {
  coin: string;
  isBuy: boolean;
  size: number;
  slippage?: number;
  testnet?: boolean;
  subaccountNumber?: number;
}

export interface DydxOrderResult {
  success: boolean;
  txHash?: string;
  error?: string;
}

// ─── Constants ─────────────────────────────────────────────────────

const DYDX_CHAIN_MAINNET = "dydx-4";
const DYDX_CHAIN_TESTNET = "dydx-testnet-4";

const REST_MAINNET = "https://dydx-4-rest.kingnodes.com";
const REST_TESTNET = "https://dydx-testnet-rest.kingnodes.com";

// TODO: Replace with actual market metadata from the dYdX indexer.
const QUANTUM_CONVERSION_SIZE = 1e6;
const QUANTUM_CONVERSION_PRICE = 1e6;

// ─── Module-level state (singleton) ────────────────────────────────

const state = reactive<DydxOrderState>({
  connected: false,
  ordering: false,
  address: "",
  error: null,
  lastTxHash: null,
  subaccountNumber: 0,
});

// ─── Helpers ───────────────────────────────────────────────────────

function restUrl(testnet: boolean): string {
  return testnet ? REST_TESTNET : REST_MAINNET;
}

function chainId(testnet: boolean): string {
  return testnet ? DYDX_CHAIN_TESTNET : DYDX_CHAIN_MAINNET;
}

async function getAccountInfo(
  address: string,
  testnet: boolean,
): Promise<{ accountNumber: number; sequence: number }> {
  const base = restUrl(testnet);
  const resp = await fetch(`${base}/cosmos/auth/v1beta1/accounts/${address}`);
  if (!resp.ok) {
    throw new Error(
      `Failed to fetch account info: ${resp.status} ${resp.statusText}`,
    );
  }
  const json = await resp.json();
  const account = json.account?.base_account ?? json.account;
  return {
    accountNumber: parseInt(account.account_number ?? "0", 10),
    sequence: parseInt(account.sequence ?? "0", 10),
  };
}

async function getLatestBlockHeight(testnet: boolean): Promise<number> {
  const base = restUrl(testnet);
  const resp = await fetch(
    `${base}/cosmos/base/tendermint/v1beta1/blocks/latest`,
  );
  if (!resp.ok) {
    throw new Error(`Failed to fetch block height: ${resp.status}`);
  }
  const json = await resp.json();
  return parseInt(json.block?.header?.height ?? "0", 10);
}

async function getMarketPrice(coin: string, testnet: boolean): Promise<number> {
  const base = restUrl(testnet);
  const pair = `${coin}-USD`;
  const resp = await fetch(
    `${base}/dydxprotocol/clob/perpetual_markets/${pair}`,
  );
  if (!resp.ok) {
    throw new Error(`Failed to fetch market price for ${pair}: ${resp.status}`);
  }
  const json = await resp.json();
  const price = parseFloat(json?.market?.oraclePrice ?? "0");
  if (!price) {
    throw new Error(`No oracle price available for ${pair}`);
  }
  return price;
}

// ─── Composable ────────────────────────────────────────────────────

export function useDydxTrade() {
  const { keplrState, hasKeplr } = useWallet();

  async function placeOrder(
    params: DydxPlaceOrderParams,
  ): Promise<DydxOrderResult> {
    if (!hasKeplr.value || !keplrState.connected) {
      return { success: false, error: "Keplr not connected" };
    }

    state.ordering = true;
    state.error = null;

    const useTestnet = !!params.testnet;
    const cId = chainId(useTestnet);
    const keplr = (window as any).keplr;

    try {
      // 1. Fetch account info
      const accountInfo = await getAccountInfo(keplrState.address, useTestnet);

      // 2. Block height for goodTilBlock
      const blockHeight = await getLatestBlockHeight(useTestnet);
      if (!blockHeight) {
        throw new Error("Could not determine latest block height");
      }

      // 3. Oracle price + slippage
      const oraclePrice = await getMarketPrice(params.coin, useTestnet);
      const slippage = params.slippage ?? 0.005;
      const limitPrice = params.isBuy
        ? oraclePrice * (1 + slippage)
        : oraclePrice * (1 - slippage);

      // 4. Convert to atomic units
      const sizeQuantums = String(
        Math.round(params.size * QUANTUM_CONVERSION_SIZE),
      );
      const priceSubticks = String(
        Math.round(limitPrice * QUANTUM_CONVERSION_PRICE),
      );
      const subaccount = params.subaccountNumber ?? state.subaccountNumber;

      // 5. Build Amino message for dYdX v4 MsgCreateOrder
      //    TODO: Validate structure against dYdX v4 proto definitions.
      //    When @dydxprotocol/v4-client-js is added, replace this manual
      //    construction with SDK helpers.
      const aminoMsg = {
        type: "dydxprotocol/clob/MsgCreateOrder",
        value: {
          order: {
            order_id: {
              subaccount_id: {
                owner: keplrState.address,
                number: subaccount,
              },
              client_id: `${Date.now()}`,
              order_flags: 0,
              good_til_block: blockHeight + 20,
            },
            side: params.isBuy ? 1 : 2,
            quantums: sizeQuantums,
            subticks: priceSubticks,
            time_in_force: "TIME_IN_FORCE_IOC",
            reduce_only: false,
            client_metadata: "{}",
          },
        },
      };

      // 6. Build StdSignDoc (Amino JSON)
      const fee = {
        amount: [{ denom: "adydx", amount: "0" }],
        gas: "500000",
      };

      const signDoc = {
        chain_id: cId,
        account_number: String(accountInfo.accountNumber),
        sequence: String(accountInfo.sequence),
        fee,
        msgs: [aminoMsg],
        memo: `funding-arb ${Date.now()}`,
      };

      // 7. Ask Keplr to sign via Amino
      const signResponse = await keplr.signAmino(
        cId,
        keplrState.address,
        signDoc,
      );

      // 8. Broadcast via REST (Amino JSON broadcast)
      const base = restUrl(useTestnet);
      const txBody = {
        msg: signResponse.signed.msgs,
        fee: signResponse.signed.fee,
        signatures: [
          {
            pub_key: signResponse.signature.pub_key,
            signature: signResponse.signature.signature,
          },
        ],
        memo: signResponse.signed.memo,
      };

      const broadcastResp = await fetch(`${base}/cosmos/tx/v1beta1/txs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tx: txBody,
          mode: "BROADCAST_MODE_SYNC",
        }),
      });

      if (!broadcastResp.ok) {
        const errorText = await broadcastResp.text();
        throw new Error(
          `Broadcast failed (${broadcastResp.status}): ${errorText}`,
        );
      }

      const broadcastJson = await broadcastResp.json();
      if (broadcastJson.tx_response?.code !== 0) {
        throw new Error(
          `Tx failed on-chain: code ${broadcastJson.tx_response?.code} — ${broadcastJson.tx_response?.raw_log ?? "unknown"}`,
        );
      }

      const txHash = broadcastJson.tx_response.txhash as string;
      state.lastTxHash = txHash;
      state.connected = true;
      return { success: true, txHash };
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : "Order failed";
      state.error = message;
      return { success: false, error: message };
    } finally {
      state.ordering = false;
    }
  }

  function disconnect(): void {
    state.connected = false;
    state.address = "";
    state.error = null;
    state.lastTxHash = null;
  }

  function checkConnection(): void {
    state.connected = keplrState.connected;
    state.address = keplrState.address;
  }

  return {
    dydxTradeState: state,
    placeOrder,
    disconnect,
    checkConnection,
  };
}
