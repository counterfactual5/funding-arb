#!/usr/bin/env python3
"""Credential loader back-compat: new ~/.funding-arb store + legacy ~/.funding-arb."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.credentials as creds  # noqa: E402


def _write_json(path: Path, env: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"env": env}), encoding="utf-8")


def test_new_json_overrides_legacy(tmp_path, monkeypatch):
    new = tmp_path / "funding-arb" / "credentials.json"
    legacy = tmp_path / "funding-arb" / "funding-arb.json"
    _write_json(legacy, {"BINANCE_API_KEY": "legacy", "OKX_API_KEY": "legacy-only"})
    _write_json(new, {"BINANCE_API_KEY": "new"})
    # _JSON_FILES is ordered highest-priority first.
    monkeypatch.setattr(creds, "_JSON_FILES", [new, legacy])

    out = creds._load_json()
    assert out["BINANCE_API_KEY"] == "new"  # new store wins
    assert out["OKX_API_KEY"] == "legacy-only"  # legacy still read


def test_legacy_only_still_loads(tmp_path, monkeypatch):
    legacy = tmp_path / "funding-arb.json"
    _write_json(legacy, {"BYBIT_API_KEY": "k"})
    monkeypatch.setattr(creds, "_JSON_FILES", [tmp_path / "missing.json", legacy])
    assert creds._load_json() == {"BYBIT_API_KEY": "k"}


def test_missing_files_return_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(
        creds, "_JSON_FILES", [tmp_path / "a.json", tmp_path / "b.json"]
    )
    assert creds._load_json() == {}


def test_ensure_env_does_not_overwrite_existing_env(tmp_path, monkeypatch):
    new = tmp_path / "credentials.json"
    _write_json(new, {"BITGET_API_KEY": "from-file"})
    monkeypatch.setattr(creds, "_JSON_FILES", [new])
    monkeypatch.setattr(creds, "_load_keyring", lambda: {})
    monkeypatch.setattr(creds, "_load_age", lambda: {})
    monkeypatch.setattr(creds, "_load_systemd_creds", lambda: {})
    monkeypatch.setattr(creds, "_cache", None)
    monkeypatch.setattr(creds, "_loaded", False)
    monkeypatch.setenv("BITGET_API_KEY", "from-env")

    creds.ensure_env()
    assert os.environ["BITGET_API_KEY"] == "from-env"  # explicit env always wins
