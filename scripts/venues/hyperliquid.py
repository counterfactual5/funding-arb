#!/usr/bin/env python3
"""Hyperliquid perp-DEX venue adapter for funding-arb.

Implements the CexVenue interface subset used by pure_futures_executor /
pure_futures_watcher.

Read path (prices, meta, funding) uses direct HTTP POST to avoid the
hyperliquid-python-sdk spot_meta IndexError bug on mainnet.
Write path (orders, leverage, positions) uses the hyperliquid-python-sdk
with trade-signer or local-key signing.

The SDK is optional: scanning and dry-run execution work without it.
Live order placement raises a clear error if the SDK is not installed.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# SDK imports — optional, only needed for live order execution
# ---------------------------------------------------------------------------

_sdk_cache: dict[str, Any] | None = None


def _get_sdk() -> dict[str, Any]:
    """Lazy-load hyperliquid-python-sdk components for write-path."""
    global _sdk_cache
    if _sdk_cache is not None:
        return _sdk_cache
    try:
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
    except ImportError as e:
        raise RuntimeError(
            "Hyperliquid live execution requires 'hyperliquid-python-sdk'. "
            "Install with: pip install hyperliquid-python-sdk"
        ) from e
    _sdk_cache = {"Info": Info, "Exchange": Exchange}
    return _sdk_cache


def _get_base_url() -> str:
    """Return API URL based on HYPERLIQUID_TESTNET env var."""
    if os.environ.get("HYPERLIQUID_TESTNET", "").strip() in ("1", "true", "yes"):
        return "https://api.hyperliquid-testnet.xyz"
    return "https://api.hyperliquid.xyz"


def _get_wallet_address() -> str:
    """Read wallet address from environment."""
    env = os.environ.get
    addr = (
        env("HYPERLIQUID_WALLET_ADDRESS_MAINNET")
        or env("HYPERLIQUID_WALLET_ADDRESS")
        or env("HYPERLIQUID_WALLET_ADDRESS_TESTNET")
        or ""
    ).strip()
    if addr and not addr.startswith("0x"):
        addr = "0x" + addr
    return addr


def _make_info_client() -> Any:
    """Create Info client with spot_meta workaround (avoids IndexError)."""
    sdk = _get_sdk()
    url = _get_base_url()
    # Workaround: fetch spot_meta manually to fix token array misalignment
    try:
        spot_meta = requests.post(
            f"{url}/info", json={"type": "spotMeta"}, timeout=15
        ).json()
        tokens = spot_meta.get("tokens", [])
        if tokens:
            max_index = max(t.get("index", 0) for t in tokens)
            fixed = [None] * (max_index + 1)
            for t in tokens:
                idx = t.get("index", 0)
                if idx < len(fixed):
                    fixed[idx] = t
            spot_meta = dict(spot_meta, tokens=fixed)
        return sdk["Info"](url, skip_ws=True, spot_meta=spot_meta)
    except Exception:
        return sdk["Info"](url, skip_ws=True)


def _make_exchange_client() -> Any:
    """Create Exchange client with local key or trade-signer."""
    sdk = _get_sdk()
    url = _get_base_url()
    wallet = _get_wallet_address()
    if not wallet:
        raise RuntimeError(
            "HYPERLIQUID_WALLET_ADDRESS not set. Set it in .env or environment."
        )
    key = (os.environ.get("HYPERLIQUID_PRIVATE_KEY") or "").strip()
    if key:
        if not key.startswith("0x"):
            key = "0x" + key
        return sdk["Exchange"](url, wallet=wallet, account=wallet, secret=key)
    # Try trade-signer
    signer_url = os.environ.get("TRADE_SIGNER_URL", "").strip()
    if signer_url:
        return _make_exchange_with_tradesigner(url, wallet, signer_url)
    raise RuntimeError(
        "Hyperliquid signing requires HYPERLIQUID_PRIVATE_KEY or "
        "TRADE_SIGNER_URL to be set."
    )


def _make_exchange_with_tradesigner(base_url: str, wallet: str, signer_url: str) -> Any:
    """Create Exchange client that delegates signing to trade-signer."""
    import hyperliquid.utils.signing as hl_signing

    sdk = _get_sdk()
    api_token = os.environ.get("TRADE_SIGNER_API_TOKEN", "")

    # Monkey-patch sign_inner to redirect to trade-signer
    _original_sign = hl_signing.sign_inner

    def _patched_sign(wallet_obj: Any, data: dict[str, Any]) -> dict[str, str]:

        import requests as _requests

        domain = data.get("domain", {})
        types = data.get("types", {})
        primary_type = data.get("primaryType", "")
        message = data.get("message", {})
        payload = {
            "context": {
                "service": "hyperliquid",
                "chain": "arbitrum",
                "tokenIn": "USDC",
                "tokenOut": "USDC",
                "amount": "1000000",
                "kind": "hyperliquid_perp_order",
                "domain": domain,
            },
            "typedData": {
                "domain": domain,
                "types": types,
                "primaryType": primary_type,
                "message": message,
            },
        }
        headers = {"Content-Type": "application/json"}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        resp = _requests.post(
            f"{signer_url}/sign-typed-data",
            json=payload,
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 403:
            raise PermissionError(f"trade-signer denied: {resp.json()}")
        if resp.status_code != 200:
            raise RuntimeError(f"trade-signer error: {resp.status_code} {resp.text}")
        result = resp.json()
        return {"r": result["r"], "s": result["s"], "v": result["v"]}

    hl_signing.sign_inner = _patched_sign

    class _DummyWallet:
        def __init__(self, address: str):
            self._address = address.lower()

        @property
        def address(self) -> str:
            return self._address

        @property
        def key(self) -> str:
            return self._address

    return sdk["Exchange"](_DummyWallet(wallet), base_url)


# ---------------------------------------------------------------------------
# Load .env.local if present (mirrors hyperliquid skill's pattern)
# ---------------------------------------------------------------------------


def _load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent.parent / ".env.local"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAINNET_URL = "https://api.hyperliquid.xyz"
_TESTNET_URL = "https://api.hyperliquid-testnet.xyz"


def _base_url() -> str:
    """Resolve the Info/Exchange host from env (read at call time so the
    wallet-connect flow can switch network without a restart).

    HYPERLIQUID_BASE_URL overrides everything; otherwise
    HYPERLIQUID_NETWORK=testnet selects the testnet host.
    """
    override = os.environ.get("HYPERLIQUID_BASE_URL", "").strip()
    if override:
        return override
    net = os.environ.get("HYPERLIQUID_NETWORK", "mainnet").strip().lower()
    return _TESTNET_URL if net == "testnet" else _MAINNET_URL


_DIRECTION_MAP: dict[str, bool] = {
    "open_long": True,
    "close_long": False,
    "open_short": False,
    "close_short": True,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coin_from_pair(pair: str) -> str:
    s = pair.upper()
    return s[:-4] if s.endswith("USDT") else s


def _pair_from_coin(coin: str) -> str:
    return f"{coin.upper()}USDT"


def _info_post(body: dict[str, Any]) -> Any:
    """Direct HTTP POST to Hyperliquid Info endpoint (bypasses SDK bugs)."""
    r = requests.post(f"{_base_url()}/info", json=body, timeout=20)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Venue implementation
# ---------------------------------------------------------------------------


class HyperliquidVenue:
    """Hyperliquid perp-DEX adapter — pure futures only (no spot).

    Read path: direct HTTP (no SDK needed).
    Write path: hyperliquid-python-sdk (lazy-loaded, optional for dry-run).
    """

    venue_id: str = "hyperliquid"

    def __init__(self) -> None:
        self._rules_cache: dict[str, dict[str, Any]] = {}
        self._meta_cache: dict[str, Any] | None = None
        self._leverage_set: set[str] = set()

    # ── meta cache ─────────────────────────────────────────────────────

    def _get_meta(self) -> dict[str, Any]:
        if self._meta_cache is not None:
            return self._meta_cache
        try:
            data = _info_post({"type": "metaAndAssetCtxs"})
            if isinstance(data, list) and len(data) >= 2:
                universe = data[0].get("universe", [])
                meta = {}
                for entry in universe:
                    name = str(entry.get("name", "")).upper()
                    if name:
                        meta[name] = entry
                self._meta_cache = meta
                return meta
        except Exception:
            pass
        return {}

    # ── market data (HTTP, no SDK) ────────────────────────────────────

    def get_futures_ticker(self, pair: str) -> float:
        coin = _coin_from_pair(pair)
        try:
            mids = _info_post({"type": "allMids"})
            if isinstance(mids, dict):
                price_str = mids.get(coin, mids.get(coin.upper()))
                if price_str is not None:
                    return float(price_str)
        except Exception:
            pass
        return 0.0

    def get_ticker(self, pair: str) -> float:
        return self.get_futures_ticker(pair)

    def fetch_futures_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        coin = _coin_from_pair(pair).upper()
        if coin in self._rules_cache:
            return self._rules_cache[coin]
        meta = self._get_meta()
        entry = meta.get(coin)
        sz_dec = int(entry.get("szDecimals", 4)) if entry else 5
        rules = {
            "symbol": pair,
            "quantity_precision": sz_dec,
            "quote_precision": 2,
            "min_trade_usdt": 10.0,
            "min_trade_base": 0.0,
        }
        self._rules_cache[coin] = rules
        return rules

    def fetch_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        return self.fetch_futures_symbol_rules(pair, cache_sec)

    # ── account / positions (SDK) ─────────────────────────────────────

    def fetch_usdt_account_balances(self) -> dict[str, float]:
        try:
            info = _make_info_client()
            wallet = _get_wallet_address()
            if not wallet:
                return {"spot": 0.0, "futures": 0.0}
            state = info.user_state(wallet)
            margin = state.get("marginSummary", {})
            val = float(margin.get("accountValue", 0) or 0)
            return {"spot": 0.0, "futures": val}
        except Exception:
            return {"spot": 0.0, "futures": 0.0}

    def fetch_futures_positions(self, quote: str = "USDT") -> list[dict[str, Any]]:
        try:
            info = _make_info_client()
            wallet = _get_wallet_address()
            if not wallet:
                return []
            state = info.user_state(wallet)
            positions = []
            for p in state.get("assetPositions", []):
                pos = p.get("position", {})
                szi = str(pos.get("szi", "0"))
                try:
                    size = float(Decimal(szi))
                except Exception:
                    continue
                if size == 0:
                    continue
                coin = str(pos.get("coin", ""))
                side = "long" if size > 0 else "short"
                positions.append(
                    {
                        "symbol": _pair_from_coin(coin),
                        "side": side,
                        "qty": abs(size),
                        "entry_price": float(pos.get("entryPx", 0) or 0),
                        "liq_price": float(pos.get("liquidationPx", 0) or 0),
                        "leverage": float(p.get("leverage", {}).get("value", 1) or 1),
                        "unrealized_pnl": float(pos.get("unrealizedPnl", 0) or 0),
                    }
                )
            return positions
        except Exception:
            return []

    # ── setup (SDK) ───────────────────────────────────────────────────

    def initialize_futures_symbol(self, pair: str) -> None:
        coin = _coin_from_pair(pair)
        if coin in self._leverage_set:
            return
        try:
            exchange = _make_exchange_client()
            exchange.leverage_update(coin=coin, leverage=1, is_cross=True)
            self._leverage_set.add(coin)
        except Exception:
            pass

    # ── execution ──────────────────────────────────────────────────────

    def execute_trades(
        self,
        trades: list[dict[str, Any]],
        market: dict[str, dict[str, Any]],
        dry_run: bool,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for trade in trades:
            symbol = trade["symbol"]
            typ = trade["type"]
            mkt = market.get(symbol, {})
            ref_price = float(mkt.get("price", 0))
            record = dict(trade)
            record["dry_run"] = dry_run
            record["venue"] = self.venue_id
            record["ref_price"] = ref_price

            if dry_run:
                record["status"] = "simulated"
                record["order_id"] = None
                record["exec_qty"] = trade.get("amount_base", 0)
                record["exec_price"] = ref_price
                record["slippage"] = 0.0
                record["latency_ms"] = 0
                record["error"] = None
                results.append(record)
                continue

            # Live execution via SDK
            is_buy = _DIRECTION_MAP.get(typ)
            if is_buy is None:
                record["status"] = "failed"
                record["error"] = f"Unknown trade type: {typ}"
                record["order_id"] = None
                results.append(record)
                continue

            coin = _coin_from_pair(symbol)
            size = float(trade.get("amount_base", 0))
            submit_ts = time.time()

            try:
                exchange = _make_exchange_client()
                info = _make_info_client()
                # Get mid price for slippage calc
                all_mids = info.all_mids()
                mid = Decimal(str(all_mids[coin]))
                slippage_dec = Decimal("0.01")
                if is_buy:
                    limit_px = mid * (1 + slippage_dec)
                else:
                    limit_px = mid * (1 - slippage_dec)

                result = exchange.order(
                    coin=coin,
                    is_buy=is_buy,
                    sz=size,
                    limit_px=float(limit_px),
                    order_type={"limit": {"tif": "IOC"}},
                )
                fill_ts = time.time()
                latency_ms = round((fill_ts - submit_ts) * 1000)

                status_str = str(result.get("status", ""))
                if status_str == "ok":
                    fill_price = float(limit_px)
                    slippage_val = (
                        round((fill_price - ref_price) / ref_price, 6)
                        if ref_price and fill_price
                        else 0.0
                    )
                    record["status"] = "filled"
                    record["order_id"] = None
                    record["exec_price"] = fill_price
                    record["exec_qty"] = size
                    record["exec_quote_usd"] = round(size * fill_price, 4)
                    record["slippage"] = slippage_val
                    record["latency_ms"] = latency_ms
                    record["error"] = None
                else:
                    record["status"] = "failed"
                    record["order_id"] = None
                    record["error"] = f"HL order failed: {status_str}"
            except Exception as e:
                record["status"] = "failed"
                record["order_id"] = None
                record["error"] = str(e)

            results.append(record)
        return results
