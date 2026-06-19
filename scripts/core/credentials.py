#!/usr/bin/env python3
"""Unified credential provider — automatically selects the most secure backend available for the current platform.

Backend priority (highest security first):
  1. keyring       — macOS Keychain / Windows Credential Manager / Linux Secret Service
  2. systemd-creds — Linux machine-bound (TPM2 / machine-id), recommended for headless servers
  3. age           — encrypted files, protects against accidental exposure but not malicious same-user processes
  4. credentials.json — plaintext JSON fallback (also reads legacy ~/.funding-arb/funding-arb.json)

Usage (in venue modules):
  from core.credentials import ensure_env
  ensure_env()           # Load all credentials once
  ensure_env("BITGET_")  # Only load BITGET_* series

After that, simply use os.environ["BITGET_API_KEY"] as usual.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Credential store locations. New installs use ~/.funding-arb (keyring service
# "funding-arb"); the legacy ~/.funding-arb paths and "funding-arb" service are still
# read for backward compatibility so existing setups keep working. Each list is
# ordered highest priority first (new overrides legacy).
_APP_DIR = Path.home() / ".funding-arb"
_LEGACY_DIR = Path.home() / ".funding-arb"
_AGE_DIRS = [_APP_DIR, _LEGACY_DIR]
_SYSTEMD_CREDS_DIRS = [Path("/etc/funding-arb/creds"), Path("/etc/funding-arb/creds")]
_SERVICES = ["funding-arb", "funding-arb"]
_JSON_FILES = [_APP_DIR / "credentials.json", _LEGACY_DIR / "funding-arb.json"]

_KNOWN_PREFIXES = (
    "BINANCE_",
    "BITGET_",
    "BYBIT_",
    "OKX_",
    "HYPERLIQUID_",
    "EDGEX_",
    "TELEGRAM_",
)

_ALL_KEYS = [
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_TRADE_API_KEY",
    "BINANCE_SECRET_KEY",
    "BINANCE_TRADE_SECRET_KEY",
    "BITGET_API_KEY",
    "BITGET_SECRET_KEY",
    "BITGET_PASSPHRASE",
    "BYBIT_API_KEY",
    "BYBIT_SECRET_KEY",
    "OKX_API_KEY",
    "OKX_SECRET_KEY",
    "OKX_PASSPHRASE",
    "HYPERLIQUID_API_KEY",
    "HYPERLIQUID_API_SECRET",
    "EDGEX_ACCOUNT_ID",
    "EDGEX_TRADING_PRIVATE_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]

_loaded = False
_cache: dict[str, str] | None = None


# Backend 1: keyring (macOS Keychain / Windows Credential Manager / Linux Secret Service)
def _load_keyring() -> dict[str, str] | None:
    """Read from system keychain. Returns None if unavailable, empty dict if available but no data."""
    try:
        import keyring
    except ImportError:
        return None

    result: dict[str, str] = {}
    try:
        for k in _ALL_KEYS:
            for svc in _SERVICES:  # new service wins, fall back to legacy
                v = keyring.get_password(svc, k)
                if v:
                    result[k] = v
                    break
    except Exception:
        return None
    return result


# Backend 2: systemd-creds (Linux machine-bound, TPM2 / machine-id)
def _load_systemd_creds() -> dict[str, str]:
    """Read from systemd-creds encrypted files (Linux only)."""
    if sys.platform != "linux":
        return {}

    sd_creds = shutil.which("systemd-creds")
    if not sd_creds:
        return {}

    result: dict[str, str] = {}
    for creds_dir in _SYSTEMD_CREDS_DIRS:  # new dir wins, fall back to legacy
        for key_name in _ALL_KEYS:
            if key_name in result:
                continue
            cred_file = creds_dir / f"{key_name}.cred"
            if not cred_file.exists():
                continue
            try:
                proc = subprocess.run(
                    [sd_creds, "decrypt", str(cred_file)],
                    capture_output=True,
                    timeout=5,
                )
                if proc.returncode == 0:
                    result[key_name] = proc.stdout.decode().strip()
            except (subprocess.TimeoutExpired, OSError):
                pass

    return result


# Backend 3: age encrypted files
def _load_age() -> dict[str, str]:
    """Read from age encrypted files (new dir first, legacy ~/.funding-arb fallback)."""
    age = shutil.which("age")
    if not age:
        return {}

    for d in _AGE_DIRS:
        identity_file = d / "credentials.key"
        encrypted_file = d / "credentials.enc"
        if not encrypted_file.exists() or not identity_file.exists():
            continue
        try:
            result = subprocess.run(
                [age, "-d", "-i", str(identity_file), str(encrypted_file)],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return json.loads(result.stdout.decode())
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            pass

    return {}


# Backend 4: JSON plaintext (fallback). Reads new credentials.json and legacy
# funding-arb.json; values from the new file override the legacy one.
def _load_json() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in reversed(_JSON_FILES):  # legacy first, new overrides
        try:
            with open(path, encoding="utf-8") as f:
                env = json.load(f).get("env", {})
            merged.update({k: str(v) for k, v in env.items() if v})
        except (OSError, json.JSONDecodeError):
            pass
    return merged


# Unified loading (with cache, merges low-to-high security, higher overwrites lower)
def _load_all() -> dict[str, str]:
    global _cache
    if _cache is not None:
        return _cache

    merged: dict[str, str] = {}

    # Lowest: JSON plaintext
    merged.update(_load_json())

    # Medium: age encrypted
    merged.update(_load_age())

    # High: systemd-creds (Linux)
    merged.update(_load_systemd_creds())

    # Highest: keyring (macOS / Windows / Linux desktop)
    kr = _load_keyring()
    if kr is not None:
        merged.update(kr)

    # Only keep known keys
    merged = {k: v for k, v in merged.items() if _is_known_key(k)}

    _cache = merged
    return _cache


def _is_known_key(key: str) -> bool:
    return any(key.startswith(p) for p in _KNOWN_PREFIXES)


# Public API
def ensure_env(prefix: str = "") -> None:
    """Load credentials into os.environ.

    Values already present in os.environ are not overwritten (env vars always have highest priority).
    """
    global _loaded
    if _loaded and not prefix:
        return

    all_creds = _load_all()

    # Supplement with keys that may exist in JSON but are not listed in _ALL_KEYS
    json_env = _load_json()
    for k in json_env:
        if _is_known_key(k) and k not in all_creds:
            all_creds[k] = json_env[k]

    for key, value in all_creds.items():
        if prefix and not key.startswith(prefix):
            continue
        if not os.environ.get(key):
            os.environ[key] = value

    if not prefix:
        _loaded = True


def get_credential(key: str) -> str | None:
    """Retrieve a single credential directly without writing to os.environ."""
    val = os.environ.get(key)
    if val:
        return val
    return _load_all().get(key)
