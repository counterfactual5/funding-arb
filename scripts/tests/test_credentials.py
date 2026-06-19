#!/usr/bin/env python3
"""Credential loader: ~/.funding-arb store."""

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


def test_json_loads_values(tmp_path, monkeypatch):
    store = tmp_path / "funding-arb" / "credentials.json"
    _write_json(store, {"BINANCE_API_KEY": "k", "OKX_API_KEY": "o"})
    monkeypatch.setattr(creds, "_JSON_FILES", [store])

    out = creds._load_json()
    assert out["BINANCE_API_KEY"] == "k"
    assert out["OKX_API_KEY"] == "o"


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
