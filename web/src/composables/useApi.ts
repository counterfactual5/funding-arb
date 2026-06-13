import type { Ref } from "vue";
import { ref } from "vue";

const API_BASE = "/api";

// ─── Types (aligned with backend) ───────────────────────────────────

export interface ScannerStatus {
  scanning: boolean;
  last_scan_time: string | null;
  has_data: boolean;
  live: boolean;
}

export interface OpportunityItem {
  base: string;
  direction?: string;
  long_venue: string;
  short_venue: string;
  long_rate_pct: number;
  short_rate_pct: number;
  spread_pct: number;
  net_edge_pct: number;
  real_edge_pct?: number;
  annual_apy_pct?: number;
  net_apy_pct?: number;
  breakeven_hours?: number | null;
  long_symbol?: string;
  short_symbol?: string;
  fee_pct?: number;
  round_trip_fee_pct?: number;
  long_mark?: number;
  short_mark?: number;
  mark_spread_pct?: number;
  settle_mismatch?: boolean;
  same_interval?: boolean;
  long_interval_h?: number;
  short_interval_h?: number;
  /** clean | caution | high — mark/basis risk vs strategy real-edge bar */
  basis_risk_level?: "clean" | "caution" | "high";
}

export interface ScannerOpportunities {
  venues: string[];
  total_assets_scanned: number;
  total_spreads_found: number;
  forward: OpportunityItem[];
  reverse: OpportunityItem[];
  venue_pair_stats: Array<{ pair: string; count: number }>;
  timestamp: string;
}

export interface PositionItem {
  id: string;
  base: string;
  direction: string;
  long_venue: string;
  short_venue: string;
  status: string;
  // Real data fields
  qty?: number;
  long_price?: number;
  short_price?: number;
  trade_usd?: number;
  amount_usd?: number; // legacy field name
  pnl_usd?: number;
  unrealized_pnl_usd?: number;
  mark_spread_pct?: number;
  open_spread_pct?: number; // legacy field name
  open_edge_pct?: number; // legacy field name
  opened_at?: number; // ms timestamp
  open_time?: string; // legacy ISO string
  closed_at?: number;
  dry_run?: boolean;
  strategy?: string;
  quote?: string;
  long_symbol?: string;
  short_symbol?: string;
  // ─── Detailed metrics (added for enhanced positions view) ──────
  /** Close information from executor (close prices, spreads) */
  close_info?: {
    long_price?: number;
    short_price?: number;
    futures_price?: number;
    spot_price?: number;
    open_mark_spread?: number;
    close_mark_spread?: number;
    dry_run?: boolean;
    [key: string]: unknown;
  };
  /** Long leg fill quantity (pure futures) */
  long_qty?: number;
  /** Short leg fill quantity (pure futures) */
  short_qty?: number;
  /** Futures venue (carry/unified strategies) */
  futures_venue?: string;
  /** Spot venue (carry/unified strategies) */
  spot_venue?: string;
  /** Futures open price (carry/unified) */
  futures_price?: number;
  /** Spot open price (carry/unified) */
  spot_price?: number;
  /** Whether legs were opened in parallel */
  parallel_legs?: boolean;
}

export interface BacktestSummary {
  total_pnl_usd: number;
  total_pnl_pct: number;
  annualized_pct: number;
  max_drawdown_pct: number;
  sharpe: number;
  win_rate: number;
  total_trades: number;
  avg_hold_days: number;
}

export interface BacktestTrade {
  base: string;
  direction: string;
  long_venue: string;
  short_venue: string;
  open_time: string;
  close_time: string;
  hold_days: number;
  pnl_usd: number;
}

export interface EquityPoint {
  ts: string;
  equity: number;
  open_pairs?: number;
  capital_free?: number;
}

export interface BacktestResult {
  id: string;
  params: Record<string, any>;
  summary: BacktestSummary;
  trades: BacktestTrade[];
  equity_curve?: EquityPoint[];
  run_time: string;
  live: boolean;
}

export interface VenueConfig {
  id: string;
  name: string;
  type: string;
  configured: boolean;
  missing_keys: string[];
  status: string;
  scan_capable?: boolean;
  trade_capable?: boolean;
  trade_reason?: string;
  live_ready?: boolean;
  live_reason?: string;
}

export interface StrategyParams {
  min_spread_annual: number;
  min_edge_annual: number;
  max_mark_spread_pct: number;
  trade_usd: number;
  max_positions: number;
  scan_interval_sec: number;
  scan_venues?: string[];
  min_edge_1h?: number;
  min_edge_mismatch?: number;
  fee_mode?: "auto" | "api" | "vip_tier";
  venue_fee_tiers?: Record<string, string>;
}

export interface FeeTierOption {
  id: string;
  label: string;
  spot_taker_pct: number;
  futures_taker_pct: number;
}

export interface ResolvedVenueFee {
  has_credentials: boolean;
  uses_api: boolean;
  tier: string | null;
  spot_taker_pct: number;
  futures_taker_pct: number;
  spot_source: "api" | "tier" | "default";
  futures_source: "api" | "tier" | "default";
}

export interface ResolvedFees {
  fee_mode: string;
  venue_fee_tiers: Record<string, string>;
  venues: Record<string, ResolvedVenueFee>;
}

export interface CredentialsStatus {
  backends: Record<
    string,
    {
      available: boolean;
      description: string;
      path?: string;
    }
  >;
  venues_configured: string[];
  venues_missing: string[];
}

export interface ApiResponse<T> {
  data: Ref<T | null>;
  error: Ref<string | null>;
  loading: Ref<boolean>;
  refresh: () => Promise<void>;
}

// ─── Request helpers ────────────────────────────────────────────────

async function request<T>(url: string): Promise<T> {
  const response = await fetch(`${API_BASE}${url}`);
  if (!response.ok) {
    throw new Error(`API ${response.status}: ${response.statusText}`);
  }
  const json = await response.json();
  if (json && typeof json === "object" && "success" in json && "data" in json) {
    if (!json.success) {
      throw new Error(
        json.error || json.message || "API returned success=false",
      );
    }
    return json.data as T;
  }
  return json as T;
}

export async function post<T>(
  url: string,
  body: Record<string, any>,
): Promise<T> {
  const response = await fetch(`${API_BASE}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`API ${response.status}: ${response.statusText}`);
  }
  const json = await response.json();
  if (json && typeof json === "object" && "success" in json && "data" in json) {
    if (!json.success) {
      throw new Error(
        json.error || json.message || "API returned success=false",
      );
    }
    return json.data as T;
  }
  return json as T;
}

// ─── Composable ─────────────────────────────────────────────────────

function useApi<T>(url: string, initialData: T | null = null): ApiResponse<T> {
  const data = ref<T | null>(initialData) as Ref<T | null>;
  const error = ref<string | null>(null);
  const loading = ref(false);

  async function refresh() {
    loading.value = true;
    error.value = null;
    try {
      const result = await request<T>(url);
      if (result !== null && result !== undefined) {
        data.value = result;
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : "Unknown error";
    } finally {
      loading.value = false;
    }
  }

  return { data, error, loading, refresh };
}

// ─── Composables for each API endpoint ──────────────────────────────

export function getScannerStatus(strategy: string = "pure") {
  return useApi<ScannerStatus>(`/scanner/status?strategy=${strategy}`);
}

export function getScannerOpportunities(strategy: string = "pure") {
  return useApi<ScannerOpportunities>(
    `/scanner/opportunities?strategy=${strategy}`,
  );
}

export function getPositions() {
  return useApi<PositionItem[]>("/positions");
}

export function getBacktestHistory() {
  return useApi<BacktestResult[]>("/backtest/history");
}

export function getVenues() {
  return useApi<VenueConfig[]>("/settings/venues");
}

export function getCredentialsStatus() {
  return useApi<CredentialsStatus>("/settings/credentials/status");
}

export function getStrategy() {
  return useApi<StrategyParams>("/settings/strategy");
}

export function getFeeTiers() {
  return useApi<Record<string, FeeTierOption[]>>("/settings/fee-tiers");
}

export function getResolvedFees() {
  return useApi<ResolvedFees>("/settings/fees");
}

// ─── Cash-and-Carry types ──────────────────────────────────────────

export interface CarryCand {
  base: string;
  symbol: string;
  rate_pct: number;
  annual_pct: number;
  next_ts: number;
  interval_h: number;
  has_spot?: boolean;
  borrowable?: boolean;
  spot_price?: number;
  net_edge_pct: number;
  mark_price?: number;
  fee_pct?: number;
  borrow_daily_pct?: number;
  borrow_annual_pct?: number;
}

export interface CarryVenue {
  venue: string;
  total_pairs: number;
  forward: CarryCand[];
  reverse: CarryCand[];
  spot_fee_pct?: number;
  futures_fee_pct?: number;
  two_leg_fee_pct?: number;
  error?: string;
}

export interface UnifiedCarryCand {
  base: string;
  direction: string;
  futures_venue: string;
  spot_venue: string;
  same_venue: boolean;
  funding_rate_pct: number;
  annual_pct: number;
  spot_fee_pct: number;
  futures_fee_pct: number;
  fee_pct: number;
  net_edge_pct: number;
  borrow_daily_pct?: number;
}

// ─── Wallet & Trading Mode types ────────────────────────────────

export interface WalletFieldSchema {
  key: string;
  label: string;
  type: "text" | "password" | "number" | "select";
  placeholder?: string;
  options?: string[];
  default?: string;
}

export interface WalletVenueSchema {
  name: string;
  chain: string;
  fields: WalletFieldSchema[];
  extra_fields: WalletFieldSchema[];
  live_flag: string | null;
}

export interface WalletVenueStatus {
  connected: boolean;
  chain: string;
  live_enabled: boolean;
  live_flag: string | null;
  fields_masked: Record<string, string>;
  balance_usdc: number;
}

export interface TradingModeVenue {
  mode: "backtest" | "dry_run" | "live";
  wallet_connected: boolean;
  live_enabled: boolean;
}

export interface TradingMode {
  mode: "backtest" | "dry_run" | "live";
  venues: Record<string, TradingModeVenue>;
}

export function getWalletSchemas() {
  return useApi<Record<string, WalletVenueSchema>>("/settings/wallet/schema");
}

export function getWalletStatus(venue?: string) {
  const url = venue
    ? `/settings/wallet/status?venue=${venue}`
    : "/settings/wallet/status";
  return useApi<Record<string, WalletVenueStatus>>(url);
}

export function getTradingMode() {
  return useApi<TradingMode>("/settings/trading-mode");
}

export async function connectWallet(
  venue: string,
  credentials: Record<string, string>,
) {
  return post<{ venue: string; connected: boolean }>(
    "/settings/wallet/connect",
    { venue, credentials },
  );
}

export async function disconnectWallet(venue: string) {
  return post<{ venue: string; connected: boolean }>(
    "/settings/wallet/disconnect",
    { venue },
  );
}

// ─── WebSocket ──────────────────────────────────────────────────────

export interface WsMessage {
  event: string;
  data: Record<string, any>;
}

export function useWebSocket(
  onMessage?: (msg: WsMessage) => void,
  onConnect?: () => void,
  onDisconnect?: () => void,
) {
  const connected = ref(false);
  let ws: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let pingTimer: ReturnType<typeof setInterval> | null = null;

  function connect() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const url = `${protocol}//${host}/ws/events`;

    ws = new WebSocket(url);

    ws.onopen = () => {
      connected.value = true;
      onConnect?.();
      // Send ping every 30s to keep alive
      pingTimer = setInterval(() => {
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send("ping");
        }
      }, 30000);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WsMessage;
        if (msg.event === "pong") return;
        onMessage?.(msg);
      } catch {
        // ignore non-JSON messages
      }
    };

    ws.onclose = () => {
      connected.value = false;
      if (pingTimer) {
        clearInterval(pingTimer);
        pingTimer = null;
      }
      onDisconnect?.();
      // Auto reconnect after 3s
      reconnectTimer = setTimeout(() => connect(), 3000);
    };

    ws.onerror = () => {
      ws?.close();
    };
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (pingTimer) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
    ws?.close();
    ws = null;
    connected.value = false;
  }

  return { connected, connect, disconnect };
}
