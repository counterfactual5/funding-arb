import { ref, reactive, onMounted, onUnmounted } from "vue";

// ─── Types ─────────────────────────────────────────────────────

export interface WalletState {
  connected: boolean;
  connecting: boolean;
  address: string;
  balance: number;
  chainId: string | null;
  error: string | null;
}

// ─── Keplr (dYdX / Cosmos) ─────────────────────────────────────

const DYDX_CHAIN_MAINNET = "dydx-4";
const DYDX_CHAIN_TESTNET = "dydx-testnet-4";

const keplrState = reactive<WalletState>({
  connected: false,
  connecting: false,
  address: "",
  balance: 0,
  chainId: null,
  error: null,
});

const hasKeplr = ref(false);

function detectKeplr() {
  hasKeplr.value = typeof window !== "undefined" && !!(window as any).keplr;
}

async function connectKeplr(testnet = false): Promise<void> {
  const keplr = (window as any).keplr;
  if (!keplr) {
    keplrState.error = "Keplr not found";
    return;
  }

  keplrState.connecting = true;
  keplrState.error = null;
  try {
    const chainId = testnet ? DYDX_CHAIN_TESTNET : DYDX_CHAIN_MAINNET;
    await keplr.enable(chainId);
    const key = await keplr.getKey(chainId);
    keplrState.address = key.bech32Address;
    keplrState.chainId = chainId;
    keplrState.connected = true;
    keplrState.balance = await fetchDydxBalance(key.bech32Address, testnet);
  } catch (e: any) {
    keplrState.error = e.message || "Connection failed";
  } finally {
    keplrState.connecting = false;
  }
}

function disconnectKeplr() {
  Object.assign(keplrState, {
    connected: false,
    address: "",
    balance: 0,
    chainId: null,
    error: null,
  });
}

// ─── MetaMask (Hyperliquid / EVM) ──────────────────────────────

const metamaskState = reactive<WalletState>({
  connected: false,
  connecting: false,
  address: "",
  balance: 0,
  chainId: null,
  error: null,
});

const hasMetaMask = ref(false);

function detectMetaMask() {
  hasMetaMask.value =
    typeof window !== "undefined" && !!(window as any).ethereum?.isMetaMask;
}

async function connectMetaMask(testnet = false): Promise<void> {
  const ethereum = (window as any).ethereum;
  if (!ethereum) {
    metamaskState.error = "MetaMask not found";
    return;
  }

  metamaskState.connecting = true;
  metamaskState.error = null;
  try {
    const accounts: string[] = await ethereum.request({
      method: "eth_requestAccounts",
    });
    if (!accounts.length) throw new Error("No accounts");
    metamaskState.address = accounts[0];
    metamaskState.chainId = await ethereum.request({ method: "eth_chainId" });
    metamaskState.connected = true;
    metamaskState.balance = await fetchHlBalance(accounts[0], testnet);
  } catch (e: any) {
    metamaskState.error = e.message || "Connection failed";
  } finally {
    metamaskState.connecting = false;
  }
}

function disconnectMetaMask() {
  Object.assign(metamaskState, {
    connected: false,
    address: "",
    balance: 0,
    chainId: null,
    error: null,
  });
}

// ─── Balance fetchers (via backend proxy to avoid CORS) ─────────

async function fetchDydxBalance(
  address: string,
  testnet = false,
): Promise<number> {
  return fetchVenueBalance("dydx", address, testnet);
}

async function fetchHlBalance(
  address: string,
  testnet = false,
): Promise<number> {
  return fetchVenueBalance("hyperliquid", address, testnet);
}

async function fetchVenueBalance(
  venue: string,
  address: string,
  testnet = false,
): Promise<number> {
  try {
    const net = testnet ? "&network=testnet" : "&network=mainnet";
    const resp = await fetch(
      `/api/settings/wallet/balance?venue=${venue}&address=${encodeURIComponent(address)}${net}`,
    );
    const json = await resp.json();
    if (json.success) return json.data.balance;
  } catch {
    /* ignore */
  }
  return 0;
}

// ─── Event listeners ───────────────────────────────────────────

let _cleanup: (() => void) | null = null;

function setupListeners() {
  const ethereum = (window as any).ethereum;
  if (!ethereum) return;

  const onAccountsChanged = (accounts: string[]) => {
    if (accounts.length === 0) {
      disconnectMetaMask();
      return;
    }
    metamaskState.address = accounts[0];
    fetchHlBalance(accounts[0]).then((b) => {
      metamaskState.balance = b;
    });
  };
  const onChainChanged = () => {
    window.location.reload();
  };

  ethereum.on("accountsChanged", onAccountsChanged);
  ethereum.on("chainChanged", onChainChanged);

  _cleanup = () => {
    ethereum.removeListener("accountsChanged", onAccountsChanged);
    ethereum.removeListener("chainChanged", onChainChanged);
  };
}

// ─── Composable ────────────────────────────────────────────────

export function useWallet() {
  onMounted(() => {
    detectKeplr();
    detectMetaMask();
    setupListeners();
    // Silently restore previously-authorized MetaMask accounts without popup.
    // eth_accounts is read-only (no popup) and returns already-connected accounts.
    const ethereum = (window as any).ethereum;
    if (ethereum) {
      ethereum
        .request({ method: "eth_accounts" })
        .then((accounts: string[]) => {
          if (accounts.length > 0) {
            metamaskState.address = accounts[0];
            ethereum
              .request({ method: "eth_chainId" })
              .then((cid: string) => {
                metamaskState.chainId = cid;
                metamaskState.connected = true;
                fetchHlBalance(accounts[0]).then((b) => {
                  metamaskState.balance = b;
                });
              })
              .catch(() => {});
          }
        })
        .catch(() => {});
    }
    // Same for Keplr — try to silently get the key without enable popup.
    // Keplr's getKey requires enable() which DOES popup, so we can't silently restore.
    // The user must click Connect Keplr again after refresh.
  });

  onUnmounted(() => {
    _cleanup?.();
  });

  return {
    hasKeplr,
    hasMetaMask,
    keplrState,
    metamaskState,
    connectKeplr,
    disconnectKeplr,
    connectMetaMask,
    disconnectMetaMask,
  };
}
