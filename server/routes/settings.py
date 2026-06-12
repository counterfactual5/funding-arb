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
from core.strategy_config import (  # noqa: E402
    DEFAULT_STRATEGY as _DEFAULT_STRATEGY,
    STRATEGY_CONFIG_PATH as _CONFIG_PATH,
    load_strategy_config as _load_strategy_config,
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
    try:
        import keyring  # noqa: E402

        backends["keyring"] = {
            "available": True,
            "description": "macOS Keychain / System Key Management",
        }
    except ImportError:
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
