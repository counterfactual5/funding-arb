#!/usr/bin/env python3
"""One-time credential import tool — automatically selects the most secure backend for the current platform.

Backends (by security):
  keyring        — macOS Keychain / Windows Credential Manager / Linux Secret Service
  systemd-creds  — Linux machine-bound (TPM2 / machine-id)
  age            — Encrypted file (cross-platform)
  funding-arb.json  — Plaintext (backward compatible)

Usage:
  python3 scripts/cli/setup_credentials.py                # Interactive setup wizard
  python3 scripts/cli/setup_credentials.py --check         # Check status + current backend
  python3 scripts/cli/setup_credentials.py --delete bitget
  python3 scripts/cli/setup_credentials.py --migrate       # Migrate from funding-arb.json
  python3 scripts/cli/setup_credentials.py --backend age   # Force specific backend
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_FUNDING-ARB_DIR = Path.home() / ".funding-arb"
_IDENTITY_FILE = _FUNDING-ARB_DIR / "credentials.key"
_ENCRYPTED_FILE = _FUNDING-ARB_DIR / "credentials.enc"
_LEGACY_JSON = _FUNDING-ARB_DIR / "funding-arb.json"
_SYSTEMD_CREDS_DIR = Path("/etc/funding-arb/creds")
SERVICE_NAME = "funding-arb"

VENUE_FIELDS: dict[str, list[str]] = {
    "binance": [
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "BINANCE_TRADE_API_KEY",
        "BINANCE_TRADE_SECRET_KEY",
    ],
    "bitget": [
        "BITGET_API_KEY",
        "BITGET_SECRET_KEY",
        "BITGET_PASSPHRASE",
    ],
    "bybit": [
        "BYBIT_API_KEY",
        "BYBIT_SECRET_KEY",
    ],
    "okx": [
        "OKX_API_KEY",
        "OKX_SECRET_KEY",
        "OKX_PASSPHRASE",
    ],
    "hyperliquid": [
        "HYPERLIQUID_API_KEY",
        "HYPERLIQUID_API_SECRET",
    ],
    "aster": [
        "ASTER_API_KEY",
        "ASTER_API_SECRET",
    ],
    "lighter": [
        "LIGHTER_API_PRIVATE_KEY",
        "LIGHTER_ACCOUNT_INDEX",
        "LIGHTER_API_KEY_INDEX",
        "LIGHTER_L1_ADDRESS",
    ],
    "telegram": [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ],
}

ALL_KEYS: list[str] = [k for keys in VENUE_FIELDS.values() for k in keys]

# Backends sorted by priority
_BACKEND_PRIORITY = ["keyring", "systemd-creds", "age", "json"]


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------
def _detect_backend() -> str:
    """Auto-detect the best available backend."""
    # 1. keyring
    try:
        import keyring

        keyring.get_password(SERVICE_NAME, "__test_connection__")
        return "keyring"
    except Exception:
        pass

    # 2. systemd-creds (Linux only)
    if sys.platform == "linux" and shutil.which("systemd-creds"):
        return "systemd-creds"

    # 3. age
    if shutil.which("age") and shutil.which("age-keygen"):
        return "age"

    return "none"


def _backend_security_note(backend: str) -> str:
    notes = {
        "keyring": (
            "✅ Highest - System keychain\n"
            "       macOS: Keychain (tied to login password)\n"
            "       Windows: Credential Manager (tied to Windows account)\n"
            "       Linux: Secret Service / GNOME Keyring"
        ),
        "systemd-creds": (
            "✅ High - Linux machine-bound\n"
            "       With TPM2 -> bound to physical chip, key never leaves chip\n"
            "       Without TPM2 -> bound to machine-id, cannot decrypt if copied to another machine"
        ),
        "age": (
            "⚠️  Medium - Encrypted file\n"
            "       Key and enc in same directory, prevents accidental exposure (cat/grep/git)\n"
            "       But does not protect against same-user malicious processes or whole-file leaks"
        ),
        "json": "❌ Low - Plaintext JSON",
        "none": "❌ No available backend",
    }
    return notes.get(backend, notes["none"])


# ---------------------------------------------------------------------------
# Keyring backend
# ---------------------------------------------------------------------------
def _kr_load_all() -> dict[str, str]:
    import keyring

    result = {}
    for k in ALL_KEYS:
        v = keyring.get_password(SERVICE_NAME, k)
        if v:
            result[k] = v
    return result


def _kr_save_all(creds: dict[str, str]) -> None:
    import keyring

    for k, v in creds.items():
        keyring.set_password(SERVICE_NAME, k, v)


def _kr_delete_keys(keys: list[str]) -> int:
    import keyring

    deleted = 0
    for k in keys:
        try:
            keyring.delete_password(SERVICE_NAME, k)
            deleted += 1
        except Exception:
            pass
    return deleted


# ---------------------------------------------------------------------------
# Systemd-creds backend
# ---------------------------------------------------------------------------
def _sd_load_all() -> dict[str, str]:
    sd = shutil.which("systemd-creds")
    if not sd:
        return {}
    result = {}
    for k in ALL_KEYS:
        cred_file = _SYSTEMD_CREDS_DIR / f"{k}.cred"
        if not cred_file.exists():
            continue
        try:
            proc = subprocess.run(
                [sd, "decrypt", str(cred_file)],
                capture_output=True,
                timeout=5,
            )
            if proc.returncode == 0:
                result[k] = proc.stdout.decode().strip()
        except (subprocess.TimeoutExpired, OSError):
            pass
    return result


def _sd_save_all(creds: dict[str, str]) -> None:
    sd = shutil.which("systemd-creds")
    if not sd:
        print("systemd-creds not installed", file=sys.stderr)
        sys.exit(1)

    _SYSTEMD_CREDS_DIR.mkdir(parents=True, exist_ok=True)

    for k, v in creds.items():
        cred_file = _SYSTEMD_CREDS_DIR / f"{k}.cred"
        proc = subprocess.run(
            [sd, "encrypt", "--name", k, "-", str(cred_file)],
            input=v.encode(),
            capture_output=True,
        )
        if proc.returncode != 0:
            print(f"Failed to encrypt {k}: {proc.stderr.decode()}", file=sys.stderr)
            sys.exit(1)
        os.chmod(cred_file, 0o600)

    print(f"  written to {_SYSTEMD_CREDS_DIR}/")


def _sd_delete_keys(keys: list[str]) -> int:
    deleted = 0
    for k in keys:
        cred_file = _SYSTEMD_CREDS_DIR / f"{k}.cred"
        if cred_file.exists():
            cred_file.unlink()
            deleted += 1
    return deleted


# ---------------------------------------------------------------------------
# Age backend
# ---------------------------------------------------------------------------
def _age_find() -> str:
    age = shutil.which("age")
    if not age:
        print("age not installed:", file=sys.stderr)
        print("  macOS:  brew install age", file=sys.stderr)
        print("  Ubuntu: sudo apt install age", file=sys.stderr)
        sys.exit(1)
    return age


def _age_generate_identity() -> None:
    if _IDENTITY_FILE.exists():
        return
    _FUNDING-ARB_DIR.mkdir(parents=True, exist_ok=True)
    _age_find()

    keygen = shutil.which("age-keygen")
    if not keygen:
        print("Please install age-keygen (usually bundled with age)", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        [keygen, "-o", str(_IDENTITY_FILE)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"age-keygen failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    os.chmod(_IDENTITY_FILE, 0o600)
    print(f"  Identity key generated: {_IDENTITY_FILE}")


def _age_get_recipient() -> str:
    with open(_IDENTITY_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("# public key:"):
                return line.split(":", 1)[1].strip()
    print("Cannot extract public key from identity key", file=sys.stderr)
    sys.exit(1)


def _age_encrypt(data: dict[str, str]) -> None:
    age = _age_find()
    recipient = _age_get_recipient()
    plaintext = json.dumps(data, indent=2, ensure_ascii=False).encode()

    result = subprocess.run(
        [age, "-r", recipient, "-o", str(_ENCRYPTED_FILE)],
        input=plaintext,
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"Encryption failed: {result.stderr.decode()}", file=sys.stderr)
        sys.exit(1)
    os.chmod(_ENCRYPTED_FILE, 0o600)


def _age_decrypt() -> dict[str, str]:
    if not _ENCRYPTED_FILE.exists() or not _IDENTITY_FILE.exists():
        return {}
    age = _age_find()
    result = subprocess.run(
        [age, "-d", "-i", str(_IDENTITY_FILE), str(_ENCRYPTED_FILE)],
        capture_output=True,
    )
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout.decode())
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Unified interface
# ---------------------------------------------------------------------------
def load_all(backend: str) -> dict[str, str]:
    if backend == "keyring":
        return _kr_load_all()
    elif backend == "systemd-creds":
        return _sd_load_all()
    elif backend == "age":
        return _age_decrypt()
    return {}


def save_all(creds: dict[str, str], backend: str) -> None:
    if backend == "keyring":
        _kr_save_all(creds)
    elif backend == "systemd-creds":
        _sd_save_all(creds)
    elif backend == "age":
        _age_generate_identity()
        _age_encrypt(creds)


def delete_keys(keys: list[str], backend: str) -> int:
    if backend == "keyring":
        return _kr_delete_keys(keys)
    elif backend == "systemd-creds":
        return _sd_delete_keys(keys)
    elif backend == "age":
        creds = _age_decrypt()
        deleted = 0
        for k in keys:
            if creds.pop(k, None):
                deleted += 1
        if deleted:
            _age_encrypt(creds)
        return deleted
    return 0


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def check_status(backend: str) -> None:
    creds = load_all(backend)

    print(f"  Backend: {backend}")
    print(f"  {_backend_security_note(backend)}")
    print()

    if not creds:
        print("  No credentials configured yet")
        print(f"  Run python3 {__file__} to start setup")
        return

    for venue, fields in VENUE_FIELDS.items():
        icons = ["✅" if creds.get(f) else "❌" for f in fields]
        print(f"  {venue:<12} {' '.join(icons)}")
    print()
    print("  ✅ = stored   ❌ = not set")


def interactive_setup(backend: str) -> None:
    creds = load_all(backend)

    print()
    print("=" * 55)
    print("  Funding-Arb Credential Import (one-time)")
    print(f"  Backend: {backend}")
    for line in _backend_security_note(backend).split("\n"):
        print(f"  {line}")
    print("  Press Enter to skip unneeded exchanges")
    print("=" * 55)
    print()

    changed = False
    for venue, fields in VENUE_FIELDS.items():
        print(f"\n  {venue.upper()}")
        for env_key in fields:
            existing = creds.get(env_key)
            if existing:
                print(f"    {env_key}: ✅ already exists (Enter to keep, type new value to overwrite)")
            else:
                print(f"    {env_key}:")

            value = getpass.getpass("      > ").strip()
            if value:
                creds[env_key] = value
                changed = True
                print("      Saved")
            elif existing:
                print("      Kept original")
            else:
                print("      Skipped")

    if changed:
        save_all(creds, backend)
        print()
        print("=" * 55)
        print("  ✅ Import complete! Future runs will auto-load, no action needed")
        print("=" * 55)
    else:
        print("\n  No changes")


def delete_venue(venue: str, backend: str) -> None:
    fields = VENUE_FIELDS.get(venue)
    if not fields:
        print(f"  Unknown venue: {venue}，choices: {', '.join(VENUE_FIELDS)}")
        return
    deleted = delete_keys(fields, backend)
    if deleted:
        print(f"  Deleted {venue}  {deleted} credential(s)")
    else:
        print("  No credentials to delete")


def migrate_from_json(backend: str) -> None:
    if not _LEGACY_JSON.exists():
        print(f"  Not found {_LEGACY_JSON}; migration skipped")
        return

    creds = load_all(backend)

    with open(_LEGACY_JSON, encoding="utf-8") as f:
        legacy_env = json.load(f).get("env", {})

    migrated = 0
    for k, v in legacy_env.items():
        if v and not creds.get(k):
            creds[k] = str(v)
            migrated += 1

    if migrated:
        save_all(creds, backend)
        print(f"  Migrated {migrated} credential(s) from funding-arb.json to {backend}")
        print(f"  Verify correctness then delete: rm {_LEGACY_JSON}")
    else:
        print("  No new credentials to migrate")


def main() -> None:
    parser = argparse.ArgumentParser(description="Funding-Arb Credential Manager")
    parser.add_argument("--check", action="store_true", help="Check status + current backend")
    parser.add_argument("--migrate", action="store_true", help="Migrate from funding-arb.json")
    parser.add_argument("--delete", metavar="VENUE", help="Delete credentials for a venue")
    parser.add_argument(
        "--backend",
        choices=["auto", "keyring", "systemd-creds", "age"],
        default="auto",
        help="Force specific backend (Default: auto)",
    )
    args = parser.parse_args()

    _FUNDING-ARB_DIR.mkdir(parents=True, exist_ok=True)

    backend = args.backend if args.backend != "auto" else _detect_backend()
    if backend == "none":
        print("❌ No available backend. Please install one of:", file=sys.stderr)
        print("  pip install keyring             # Cross-platform system keychain", file=sys.stderr)
        print("  (Linux) systemd-creds           # Usually pre-installed", file=sys.stderr)
        print("  brew install age / apt install age  # Cross-platform encrypted file", file=sys.stderr)
        sys.exit(1)

    if args.check:
        check_status(backend)
    elif args.migrate:
        migrate_from_json(backend)
    elif args.delete:
        delete_venue(args.delete, backend)
    else:
        interactive_setup(backend)


if __name__ == "__main__":
    main()
