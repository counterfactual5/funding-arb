#!/usr/bin/env python3
"""Settings & credentials API routes."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["settings"])

# ---------------------------------------------------------------------------
# Try importing real credential manager
# ---------------------------------------------------------------------------
_ensure_env = None
try:
    from core.credentials import ensure_env  # noqa: E402

    _ensure_env = ensure_env
    # Populate os.environ from keyring/age/json once at import so the
    # configuration status below reflects stored credentials.
    ensure_env()
except Exception:
    pass

# ---------------------------------------------------------------------------
# Known venues
# ---------------------------------------------------------------------------
VENUES = {
    "binance": {
        "name": "Binance",
        "type": "cefi",
        "prefix": "BINANCE_",
        "required_keys": ["BINANCE_API_KEY", "BINANCE_API_SECRET"],
        "trade_keys": ["BINANCE_TRADE_API_KEY", "BINANCE_SECRET_KEY"],
    },
    "bitget": {
        "name": "Bitget",
        "type": "cefi",
        "prefix": "BITGET_",
        "required_keys": ["BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"],
    },
    "bybit": {
        "name": "Bybit",
        "type": "cefi",
        "prefix": "BYBIT_",
        "required_keys": ["BYBIT_API_KEY", "BYBIT_SECRET_KEY"],
    },
    "okx": {
        "name": "OKX",
        "type": "cefi",
        "prefix": "OKX_",
        "required_keys": ["OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"],
    },
    "hyperliquid": {
        "name": "Hyperliquid",
        "type": "dex",
        "prefix": "HYPERLIQUID_",
        "required_keys": ["HYPERLIQUID_API_KEY", "HYPERLIQUID_API_SECRET"],
    },
    "aster": {
        "name": "Aster",
        "type": "dex",
        "prefix": "ASTER_",
        "required_keys": [],  # scanning uses public fapi data, no keys needed
        "trade_keys": ["ASTER_API_KEY", "ASTER_API_SECRET"],
    },
    "lighter": {
        "name": "Lighter",
        "type": "dex",
        "prefix": "LIGHTER_",
        "required_keys": [],  # scanning uses public REST, no keys needed
        "trade_keys": ["LIGHTER_API_PRIVATE_KEY", "LIGHTER_ACCOUNT_INDEX"],
    },
    "edgex": {
        "name": "EdgeX",
        "type": "dex",
        "prefix": "EDGEX_",
        "required_keys": [],  # scanning uses public V1 REST, no keys needed
        "trade_keys": ["EDGEX_ACCOUNT_ID", "EDGEX_TRADING_PRIVATE_KEY"],
    },
    "dydx": {
        "name": "dYdX v4",
        "type": "dex",
        "prefix": "DYDX_",
        "required_keys": [],  # scan via public indexer; no keys for dry-run
        "trade_keys": ["DYDX_MNEMONIC", "DYDX_ADDRESS"],
    },
}


# ---------------------------------------------------------------------------
# Venue capability model (scan vs trade)
# ---------------------------------------------------------------------------
from pathlib import Path

_ROOT_DIR = Path(__file__).resolve().parent.parent.parent
# Hyperliquid order signing reuses the sibling `hyperliquid` skill repo.
_HL_SKILL_DIR = _ROOT_DIR.parent / "hyperliquid" / "scripts"


def venue_trade_capability(venue_id: str) -> tuple[bool, str]:
    """Whether the executor can route orders (incl. dry-run) for this venue.

    Returns (trade_capable, reason_if_not).
    """
    v = str(venue_id).strip().lower()
    if v in ("binance", "bitget", "bybit", "okx", "hyperliquid", "aster"):
        return True, ""
    if v == "lighter":
        from importlib.util import find_spec

        if find_spec("lighter") is not None:
            return True, ""
        return False, "lighter-sdk not installed (pip install lighter-sdk)"
    if v == "edgex":
        from importlib.util import find_spec

        if find_spec("edgex_sdk") is not None:
            return True, ""
        return (
            False,
            "edgex-python-sdk not installed (pip install edgex-python-sdk>=2.0.0)",
        )
    if v == "dydx":
        from importlib.util import find_spec

        if find_spec("dydx_v4_client") is not None:
            return True, ""
        return False, "dydx-v4-client not installed (pip install dydx-v4-client>=1.1.5)"
    return False, f"unknown venue {v!r}"


def venue_live_ready(venue_id: str) -> tuple[bool, str]:
    """Whether LIVE (non-dry-run) orders can actually be signed and submitted."""
    v = str(venue_id).strip().lower()
    capable, reason = venue_trade_capability(v)
    if not capable:
        return False, reason
    info = VENUES.get(v, {})
    keys = info.get("trade_keys") or info.get("required_keys") or []
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        return False, f"missing credentials: {', '.join(missing)}"
    if v == "hyperliquid" and not _HL_SKILL_DIR.exists():
        return False, "sibling ../hyperliquid repo not found (needed for order signing)"
    return True, ""


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Wallet connection models
# ---------------------------------------------------------------------------


class WalletConnectRequest(BaseModel):
    venue: str = Field(..., description="Venue ID: dydx, hyperliquid, edgex, lighter")
    credentials: dict[str, str] = Field(
        ..., description="Key-value pairs for venue credentials"
    )


class WalletDisconnectRequest(BaseModel):
    venue: str = Field(..., description="Venue ID")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class StrategyParams(BaseModel):
    min_spread_annual: float | None = Field(
        None, description="Minimum spread threshold (%) per funding period"
    )
    min_edge_annual: float | None = Field(
        None, description="Minimum net edge (%) after fees"
    )
    max_mark_spread_pct: float | None = Field(
        None, description="Maximum mark spread (%)"
    )
    trade_usd: float | None = Field(
        None, gt=0, description="Trade size per transaction"
    )
    max_positions: int | None = Field(
        None, ge=1, description="Maximum number of positions"
    )
    scan_interval_sec: int | None = Field(
        None, ge=10, description="Scan interval (seconds)"
    )
    scan_venues: list[str] | None = Field(
        None, description="Venues to include in scanner runs"
    )
    min_edge_1h: float | None = Field(
        None,
        description=(
            "Lower net-edge threshold (%) for pairs where both legs settle "
            "hourly (1h group turns capital over faster)"
        ),
    )
    min_edge_mismatch: float | None = Field(
        None,
        description=(
            "Higher net-edge threshold (%) for cross-interval pairs (legs settle "
            "on different schedules, e.g. 4h vs 8h) to price in settlement-timing "
            "risk. Applied when intervals differ; None disables the premium."
        ),
    )
    fee_mode: str | None = Field(
        None,
        description="Fee source: auto (API when keys set, else VIP tier), api, vip_tier",
    )
    venue_fee_tiers: dict[str, str] | None = Field(
        None,
        description="Per-venue VIP tier when API keys are not configured",
    )


# ---------------------------------------------------------------------------
# Strategy config, persisted to scripts/data/strategy_config.json
# ---------------------------------------------------------------------------
from core.strategy_config import (
    load_strategy_config as _load_strategy_config,
)
from core.strategy_config import (
    save_strategy_config as _save_strategy_config,
)

_strategy_config: dict[str, Any] = _load_strategy_config()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/settings/venues")
async def get_venues():
    """Return venue configuration and connection status."""
    result = []
    for venue_id, info in VENUES.items():
        # Check if credentials are configured (no keys required = public data venue)
        required = info.get("required_keys", [])
        configured = not required
        missing_keys: list[str] = []
        for key in required:
            val = os.environ.get(key)
            if val:
                configured = True
            else:
                missing_keys.append(key)

        # Also check trade keys
        for key in info.get("trade_keys", []):
            if os.environ.get(key):
                configured = True

        trade_capable, trade_reason = venue_trade_capability(venue_id)
        live_ready, live_reason = venue_live_ready(venue_id)
        result.append(
            {
                "id": venue_id,
                "name": info["name"],
                "type": info["type"],
                "configured": configured,
                "missing_keys": missing_keys if not configured else [],
                "status": "connected" if configured else "not_configured",
                "scan_capable": True,
                "trade_capable": trade_capable,
                "trade_reason": trade_reason,
                "live_ready": live_ready,
                "live_reason": live_reason,
            }
        )

    return {"success": True, "data": result}


@router.get("/settings/credentials/status")
async def credentials_status():
    """Return credential backend status and venue configuration."""
    backends: dict[str, Any] = {}

    # Check keyring
    import importlib.util

    if importlib.util.find_spec("keyring") is not None:
        backends["keyring"] = {
            "available": True,
            "description": "macOS Keychain / System Key Management",
        }
    else:
        backends["keyring"] = {
            "available": False,
            "description": "keyring package not installed",
        }

    # Check systemd-creds
    import shutil
    import sys

    if sys.platform == "linux" and shutil.which("systemd-creds"):
        backends["systemd_creds"] = {
            "available": True,
            "description": "systemd-creds (Linux TPM2)",
        }
    else:
        backends["systemd_creds"] = {"available": False, "description": "Linux only"}

    # Check age
    if shutil.which("age"):
        backends["age"] = {"available": True, "description": "age encrypted file"}
    else:
        backends["age"] = {"available": False, "description": "age tool not installed"}

    # Check legacy JSON
    from pathlib import Path

    legacy_path = Path.home() / ".funding-arb" / "funding-arb.json"
    backends["funding-arb_json"] = {
        "available": legacy_path.exists(),
        "description": "Plaintext JSON (fallback)",
        "path": str(legacy_path),
    }

    # Determine which venues have credentials
    venues_configured: list[str] = []
    venues_missing: list[str] = []
    for venue_id, info in VENUES.items():
        required = info.get("required_keys", [])
        has_key = not required or any(os.environ.get(k) for k in required)
        if not has_key:
            has_key = any(os.environ.get(k) for k in info.get("trade_keys", []))
        if has_key:
            venues_configured.append(venue_id)
        else:
            venues_missing.append(venue_id)

    return {
        "success": True,
        "data": {
            "backends": backends,
            "venues_configured": venues_configured,
            "venues_missing": venues_missing,
        },
    }


@router.post("/settings/strategy")
async def update_strategy(params: StrategyParams):
    """Update strategy parameters."""
    updates = params.model_dump(exclude_none=True)
    _strategy_config.update(updates)
    _save_strategy_config(_strategy_config)

    return {
        "success": True,
        "data": _strategy_config,
        "message": f"Updated {len(updates)} parameter(s)",
    }


@router.get("/settings/strategy")
async def get_strategy():
    """Return current strategy parameters."""
    return {"success": True, "data": _strategy_config}


@router.get("/settings/fee-tiers")
async def get_fee_tiers():
    """Return available VIP tiers per venue for the settings UI."""
    from core.vip_fee_tiers import ALL_VENUES, list_venue_tiers  # noqa: E402

    return {
        "success": True,
        "data": {v: list_venue_tiers(v) for v in ALL_VENUES},
    }


@router.get("/settings/fees")
async def get_resolved_fees():
    """Return resolved spot/futures taker fees per venue (API or VIP tier)."""
    from core.fee_providers import (  # noqa: E402
        parse_fee_policy,
        resolve_venue_fee,
        venue_has_credentials,
        venue_uses_api,
    )
    from core.vip_fee_tiers import ALL_VENUES  # noqa: E402

    policy = parse_fee_policy(_strategy_config)
    venues_out: dict[str, Any] = {}
    for v in ALL_VENUES:
        uses_api = venue_uses_api(v, policy)
        spot = resolve_venue_fee(v, leg="spot", policy=policy)
        futures = resolve_venue_fee(v, leg="futures", policy=policy)
        tier = policy.get("venue_tiers", {}).get(v)
        if not tier:
            tier = spot.get("tier") or futures.get("tier")
        venues_out[v] = {
            "has_credentials": venue_has_credentials(v),
            "uses_api": uses_api,
            "tier": tier,
            "spot_taker_pct": spot["taker_pct"],
            "futures_taker_pct": futures["taker_pct"],
            "spot_source": spot["source"],
            "futures_source": futures["source"],
        }

    return {
        "success": True,
        "data": {
            "fee_mode": policy.get("mode", "auto"),
            "venue_fee_tiers": policy.get("venue_tiers", {}),
            "venues": venues_out,
        },
    }


# ---------------------------------------------------------------------------
# Wallet connection
# ---------------------------------------------------------------------------

_WALLET_SCHEMAS: dict[str, dict[str, Any]] = {
    "binance": {
        "name": "Binance",
        "chain": "CEX",
        "fields": [
            {
                "key": "BINANCE_API_KEY",
                "label": "API Key",
                "type": "password",
                "placeholder": "API Key",
            },
            {
                "key": "BINANCE_API_SECRET",
                "label": "API Secret",
                "type": "password",
                "placeholder": "API Secret",
            },
        ],
        "extra_fields": [],
        "live_flag": None,
    },
    "bitget": {
        "name": "Bitget",
        "chain": "CEX",
        "fields": [
            {
                "key": "BITGET_API_KEY",
                "label": "API Key",
                "type": "password",
                "placeholder": "API Key",
            },
            {
                "key": "BITGET_SECRET_KEY",
                "label": "Secret Key",
                "type": "password",
                "placeholder": "Secret Key",
            },
            {
                "key": "BITGET_PASSPHRASE",
                "label": "Passphrase",
                "type": "password",
                "placeholder": "Passphrase",
            },
        ],
        "extra_fields": [],
        "live_flag": None,
    },
    "bybit": {
        "name": "Bybit",
        "chain": "CEX",
        "fields": [
            {
                "key": "BYBIT_API_KEY",
                "label": "API Key",
                "type": "password",
                "placeholder": "API Key",
            },
            {
                "key": "BYBIT_SECRET_KEY",
                "label": "Secret Key",
                "type": "password",
                "placeholder": "Secret Key",
            },
        ],
        "extra_fields": [],
        "live_flag": None,
    },
    "okx": {
        "name": "OKX",
        "chain": "CEX",
        "fields": [
            {
                "key": "OKX_API_KEY",
                "label": "API Key",
                "type": "password",
                "placeholder": "API Key",
            },
            {
                "key": "OKX_SECRET_KEY",
                "label": "Secret Key",
                "type": "password",
                "placeholder": "Secret Key",
            },
            {
                "key": "OKX_PASSPHRASE",
                "label": "Passphrase",
                "type": "password",
                "placeholder": "Passphrase",
            },
        ],
        "extra_fields": [],
        "live_flag": None,
    },
    "dydx": {
        "name": "dYdX v4",
        "chain": "Cosmos",
        "fields": [
            {
                "key": "DYDX_MNEMONIC",
                "label": "Mnemonic",
                "type": "password",
                "placeholder": "24-word BIP-39 mnemonic",
            },
            {
                "key": "DYDX_ADDRESS",
                "label": "Address",
                "type": "text",
                "placeholder": "dydx1...",
            },
        ],
        "extra_fields": [
            {
                "key": "DYDX_NETWORK",
                "label": "Network",
                "type": "select",
                "options": ["mainnet", "testnet"],
                "default": "testnet",
            },
            {
                "key": "DYDX_SUBACCOUNT_NUMBER",
                "label": "Subaccount",
                "type": "number",
                "default": "0",
            },
        ],
        "live_flag": "DYDX_ENABLE_LIVE",
    },
    "hyperliquid": {
        "name": "Hyperliquid",
        "chain": "Arbitrum",
        "fields": [
            {
                "key": "HYPERLIQUID_API_KEY",
                "label": "Wallet Address",
                "type": "text",
                "placeholder": "0x...",
            },
            {
                "key": "HYPERLIQUID_API_SECRET",
                "label": "Private Key",
                "type": "password",
                "placeholder": "0x...",
            },
        ],
        "extra_fields": [
            {
                "key": "HYPERLIQUID_NETWORK",
                "label": "Network",
                "type": "select",
                "options": ["mainnet", "testnet"],
                "default": "mainnet",
            },
        ],
        "live_flag": None,
    },
    "aster": {
        "name": "Aster",
        "chain": "BNB Chain",
        "fields": [
            {
                "key": "ASTER_API_KEY",
                "label": "API Key",
                "type": "password",
                "placeholder": "API Key",
            },
            {
                "key": "ASTER_API_SECRET",
                "label": "API Secret",
                "type": "password",
                "placeholder": "API Secret",
            },
        ],
        "extra_fields": [],
        "live_flag": None,
    },
    "edgex": {
        "name": "EdgeX",
        "chain": "StarkEx",
        "fields": [
            {
                "key": "EDGEX_ACCOUNT_ID",
                "label": "Account ID",
                "type": "text",
                "placeholder": "Numeric account ID",
            },
            {
                "key": "EDGEX_TRADING_PRIVATE_KEY",
                "label": "Trading Key",
                "type": "password",
                "placeholder": "Private key",
            },
        ],
        "extra_fields": [
            {
                "key": "EDGEX_NETWORK",
                "label": "Network",
                "type": "select",
                "options": ["mainnet", "testnet"],
                "default": "mainnet",
            },
        ],
        "live_flag": None,
    },
    "lighter": {
        "name": "Lighter",
        "chain": "zk",
        "fields": [
            {
                "key": "LIGHTER_API_PRIVATE_KEY",
                "label": "API Private Key",
                "type": "password",
                "placeholder": "Private key",
            },
        ],
        "extra_fields": [
            {
                "key": "LIGHTER_ACCOUNT_INDEX",
                "label": "Account Index",
                "type": "text",
                "placeholder": "Numeric index (or use L1 address)",
            },
            {
                "key": "LIGHTER_L1_ADDRESS",
                "label": "L1 Address",
                "type": "text",
                "placeholder": "0x... (alternative to index)",
            },
            {
                "key": "LIGHTER_NETWORK",
                "label": "Network",
                "type": "select",
                "options": ["mainnet", "testnet"],
                "default": "mainnet",
            },
        ],
        "live_flag": None,
    },
}


def _mask(value: str, visible: int = 4) -> str:
    """Mask a secret, showing only first and last few chars."""
    if not value or len(value) <= visible * 2:
        return "***" if value else ""
    return f"{value[:visible]}...{value[-visible:]}"


@router.get("/settings/wallet/schema")
async def get_wallet_schemas():
    """Return connection schemas for all venues (CEX + DEX)."""
    schemas = {}
    for vid, info in _WALLET_SCHEMAS.items():
        schemas[vid] = {
            "name": info["name"],
            "chain": info["chain"],
            "fields": info["fields"],
            "extra_fields": info["extra_fields"],
            "live_flag": info.get("live_flag"),
        }
    return {"success": True, "data": schemas}


@router.get("/settings/wallet/status")
async def get_wallet_status(venue: str | None = None):
    """Check connection status for all venues (CEX + DEX)."""
    venues_to_check = [venue] if venue else list(_WALLET_SCHEMAS.keys())
    result = {}
    for vid in venues_to_check:
        schema = _WALLET_SCHEMAS.get(vid)
        if not schema:
            continue
        # Check if all required fields have values in env
        all_keys = [f["key"] for f in schema["fields"]]
        connected = all(os.environ.get(k, "").strip() for k in all_keys)
        # Mask the values for display
        masked_fields = {}
        for f in schema["fields"]:
            val = os.environ.get(f["key"], "")
            masked_fields[f["key"]] = _mask(val) if val else ""
        for f in schema.get("extra_fields", []):
            val = os.environ.get(f["key"], "")
            if val:
                masked_fields[f["key"]] = _mask(val) if f["type"] == "password" else val

        live_flag = schema.get("live_flag")
        live_enabled = False
        if live_flag:
            live_enabled = os.environ.get(live_flag, "").strip() in ("1", "true", "yes")

        # Try to get balance if connected
        balance = 0.0
        if connected:
            try:
                if vid == "dydx":
                    from venues.dydx import DydxVenue

                    v = DydxVenue()
                    bal = v.fetch_usdt_account_balances()
                    balance = bal.get("futures", 0.0)
                elif vid == "hyperliquid":
                    from venues.hyperliquid import HyperliquidVenue

                    v = HyperliquidVenue()
                    bal = v.fetch_usdt_account_balances()
                    balance = bal.get("futures", 0.0)
                elif vid in ("binance", "bitget", "bybit", "okx"):
                    from venues import get_venue

                    v = get_venue({"venue": {"type": vid}})
                    bal = v.fetch_usdt_account_balances()
                    balance = bal.get("futures", 0.0)
            except Exception:
                pass

        result[vid] = {
            "connected": connected,
            "chain": schema["chain"],
            "live_enabled": live_enabled,
            "live_flag": live_flag,
            "fields_masked": masked_fields,
            "balance_usdc": balance,
        }
    return {"success": True, "data": result}


@router.post("/settings/wallet/connect")
async def connect_wallet(req: WalletConnectRequest):
    """Connect a venue by setting its credentials as process env vars.

    Session-scoped only: the credentials live in this server process's
    environment and are NOT persisted to disk, so they are lost on restart.
    Reconnect after a restart, or store them via ``setup_credentials.py``
    (keyring/age) for unattended 7x24 use. This keeps secrets typed into the
    browser off disk by default.
    """
    schema = _WALLET_SCHEMAS.get(req.venue)
    if not schema:
        return {"success": False, "error": f"Unknown venue: {req.venue}"}

    # Validate required fields
    required_keys = [f["key"] for f in schema["fields"]]
    missing = [k for k in required_keys if not req.credentials.get(k, "").strip()]
    if missing:
        return {
            "success": False,
            "error": f"Missing required fields: {', '.join(missing)}",
        }

    # Set env vars for this session
    all_field_keys = [f["key"] for f in schema["fields"]] + [
        f["key"] for f in schema.get("extra_fields", [])
    ]
    for key in all_field_keys:
        val = req.credentials.get(key, "").strip()
        if val:
            os.environ[key] = val

    # Enable live flag if present
    live_flag = schema.get("live_flag")
    if live_flag and req.credentials.get(live_flag):
        os.environ[live_flag] = req.credentials[live_flag]

    return {"success": True, "data": {"venue": req.venue, "connected": True}}


@router.post("/settings/wallet/disconnect")
async def disconnect_wallet(req: WalletDisconnectRequest):
    """Disconnect a wallet by clearing its credentials from the session."""
    schema = _WALLET_SCHEMAS.get(req.venue)
    if not schema:
        return {"success": False, "error": f"Unknown venue: {req.venue}"}

    all_keys = [f["key"] for f in schema["fields"]] + [
        f["key"] for f in schema.get("extra_fields", [])
    ]
    live_flag = schema.get("live_flag")
    if live_flag:
        all_keys.append(live_flag)

    for key in all_keys:
        os.environ.pop(key, None)

    return {"success": True, "data": {"venue": req.venue, "connected": False}}


@router.get("/settings/trading-mode")
async def get_trading_mode():
    """Return current trading mode per venue: backtest, dry_run, or live."""
    venues_out = {}
    for vid, info in VENUES.items():
        schema = _WALLET_SCHEMAS.get(vid)
        if schema:
            # Venue is in wallet schemas (CEX or DEX) — check via schema fields
            all_keys = [f["key"] for f in schema["fields"]]
            connected = all(os.environ.get(k, "").strip() for k in all_keys)
            live_flag = schema.get("live_flag")
            live_enabled = False
            if live_flag:
                live_enabled = os.environ.get(live_flag, "").strip() in (
                    "1",
                    "true",
                    "yes",
                )
            mode = "live" if (connected and live_enabled) else "dry_run"
            venues_out[vid] = {
                "mode": mode,
                "wallet_connected": connected,
                "live_enabled": live_enabled,
            }
        else:
            # Fallback: venue not in wallet schemas
            trade_capable, _ = venue_trade_capability(vid)
            keys = info.get("required_keys", []) or info.get("trade_keys", [])
            has_creds = all(os.environ.get(k) for k in keys) if keys else False
            if has_creds and trade_capable:
                venues_out[vid] = {
                    "mode": "dry_run",
                    "wallet_connected": True,
                    "live_enabled": False,
                }
            else:
                venues_out[vid] = {
                    "mode": "dry_run",
                    "wallet_connected": False,
                    "live_enabled": False,
                }

    # Overall mode: live if any venue is live, else dry_run
    overall = (
        "live" if any(v["mode"] == "live" for v in venues_out.values()) else "dry_run"
    )

    return {"success": True, "data": {"mode": overall, "venues": venues_out}}


@router.get("/settings/wallet/balance")
async def get_wallet_balance(venue: str, address: str, network: str | None = None):
    """Query wallet balance via public APIs (no credentials needed).

    Used by frontend wallet extension flow — the address comes from
    Keplr/MetaMask, not from stored env vars. ``network`` (mainnet/testnet)
    comes from the extension's selected chain; it overrides the env default.
    """
    if not address or len(address) < 10:
        return {"success": False, "error": "Invalid address"}

    balance = 0.0
    equity = 0.0
    net = (network or "").strip().lower()

    try:
        if venue == "dydx":
            # dYdX v4 indexer: query subaccount by address
            import requests as _requests

            base = "https://indexer.dydx.trade"
            is_testnet = net == "testnet" or (
                not net and os.environ.get("DYDX_NETWORK", "").lower() == "testnet"
            )
            if is_testnet:
                base = "https://indexer.v4testnet.dydx.exchange"
            subaccount_number = int(os.environ.get("DYDX_SUBACCOUNT_NUMBER", "0") or 0)
            resp = _requests.get(
                f"{base}/v4/subaccounts",
                params={"address": address, "subaccountNumber": subaccount_number},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            # Response: { "subaccount": { "equity": "...", "freeCollateral": "..." } }
            sub = data.get("subaccount", {}) if isinstance(data, dict) else {}
            if not sub:
                # Try alternative response format
                subs = data.get("subaccounts", [])
                if isinstance(subs, list) and subs:
                    sub = subs[0] if isinstance(subs[0], dict) else {}
            equity = float(sub.get("equity", 0) or 0)
            free = float(sub.get("freeCollateral", 0) or 0)
            balance = free or equity

        elif venue == "hyperliquid":
            import requests as _requests

            hl_base = "https://api.hyperliquid.xyz"
            is_testnet = net == "testnet" or (
                not net
                and os.environ.get("HYPERLIQUID_NETWORK", "").lower() == "testnet"
            )
            if is_testnet:
                hl_base = "https://api.hyperliquid-testnet.xyz"
            resp = _requests.post(
                f"{hl_base}/info",
                json={"type": "clearinghouseState", "user": address},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            margin = data.get("marginSummary", {}) if isinstance(data, dict) else {}
            equity = float(margin.get("accountValue", 0) or 0)
            balance = equity

        elif venue == "lighter":
            import requests as _requests

            lighter_base = "https://mainnet.zklighter.elliot.ai"
            # Lighter uses L1 address to look up account
            data = _requests.get(
                f"{lighter_base}/api/v1/accountsByL1Address?l1_address={address}",
                timeout=15,
            )
            data.raise_for_status()
            acct = data.json()
            accounts = acct.get("sub_accounts") or acct.get("accounts") or []
            if accounts:
                equity = float(accounts[0].get("equity", 0) or 0)
                balance = equity

        elif venue == "edgex":
            # EdgeX V2: balance query needs authenticated SDK;
            # public balance-by-address is not available yet.
            balance = 0.0

        elif venue == "aster":
            # Aster: Binance-fapi API, balance requires API key auth.
            # Wallet address alone is not sufficient for balance query.
            balance = 0.0

        elif venue in ("binance", "bitget", "bybit", "okx"):
            # CEX — balance requires API keys; can't query by address alone
            return {
                "success": False,
                "error": "CEX balance requires API key connection",
            }

        else:
            return {"success": False, "error": f"Unsupported venue: {venue}"}

    except Exception as e:
        return {"success": False, "error": str(e)}

    return {
        "success": True,
        "data": {
            "balance": balance,
            "equity": equity,
            "address": address,
            "venue": venue,
        },
    }
