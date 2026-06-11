#!/usr/bin/env python3
"""Cross-exchange chain name normalization -- maps each venue's native chain names to canonical IDs."""

from __future__ import annotations

# canonical_id -> {venue_id: native_chain_name}
# USDT primarily; other coins can be extended
USDT_CHAIN_ALIASES: dict[str, dict[str, str]] = {
    "plasma": {"bitget": "Plasma", "bybit": "PLASMA"},
    "aptos": {"bitget": "Aptos", "bybit": "APTOS", "okx": "USDT-Aptos"},
    "morph": {"bitget": "Morph"},
    "bsc": {"bitget": "BEP20", "bybit": "BSC", "okx": "USDT-BSC", "binance": "BSC"},
    "avax": {
        "bitget": "AVAXC-Chain",
        "bybit": "CAVAX",
        "okx": "USDT-Avalanche C-Chain",
    },
    "arbitrum": {"bitget": "ArbitrumOne", "bybit": "ARBI", "okx": "USDT-Arbitrum One"},
    "optimism": {"bitget": "Optimism", "bybit": "OP", "okx": "USDT-Optimism"},
    "polygon": {"bitget": "Polygon", "bybit": "MATIC", "okx": "USDT-Polygon"},
    "ton": {"bitget": "TON", "bybit": "TON", "okx": "USDT-TON"},
    "sol": {"bitget": "SOL", "bybit": "SOL", "okx": "USDT-Solana"},
    "eth": {"bitget": "ERC20", "bybit": "ETH", "okx": "USDT-ERC20", "binance": "ETH"},
    "trc20": {"bitget": "TRC20", "bybit": "TRX", "okx": "USDT-TRC20", "binance": "TRX"},
    "mantle": {"bybit": "MANTLE", "okx": "USDT-Mantle"},
    "bera": {"bybit": "BERA"},
    "hyperevm": {"bybit": "HYPEREVM"},
    "kava": {"bybit": "KAVAEVM"},
    "celo": {"bybit": "CELO"},
    "monad": {"bybit": "MONAD"},
    "klay": {"bybit": "KLAY"},
}

# Additional native name -> canonical mappings (case-insensitive/aliases)
_NATIVE_TO_CANONICAL: dict[str, str] = {}
for canon, venues in USDT_CHAIN_ALIASES.items():
    for native in venues.values():
        _NATIVE_TO_CANONICAL[native.upper()] = canon
        _NATIVE_TO_CANONICAL[native.lower()] = canon
# Common aliases
for alias, canon in [
    ("BEP20", "bsc"),
    ("BSC", "bsc"),
    ("BNB SMART CHAIN", "bsc"),
    ("TRX", "trc20"),
    ("TRC20", "trc20"),
    ("ERC20", "eth"),
    ("ETH", "eth"),
    ("ETHEREUM", "eth"),
    ("ARBI", "arbitrum"),
    ("ARBITRUMONE", "arbitrum"),
    ("ARBITRUM ONE", "arbitrum"),
    ("MATIC", "polygon"),
    ("POLYGON POS", "polygon"),
    ("OP", "optimism"),
    ("OPTIMISM", "optimism"),
    ("CAVAX", "avax"),
    ("AVAXC-CHAIN", "avax"),
    ("APTOS", "aptos"),
    ("PLASMA", "plasma"),
]:
    _NATIVE_TO_CANONICAL[alias.upper()] = canon


def to_canonical(native_chain: str) -> str | None:
    """Convert an exchange's native chain name to a canonical ID."""
    if not native_chain:
        return None
    key = native_chain.strip()
    if key in _NATIVE_TO_CANONICAL:
        return _NATIVE_TO_CANONICAL[key]
    upper = key.upper()
    if upper in _NATIVE_TO_CANONICAL:
        return _NATIVE_TO_CANONICAL[upper]
    # OKX format USDT-BSC -> bsc
    if key.upper().startswith("USDT-"):
        suffix = key[5:].upper()
        for alias, canon in _NATIVE_TO_CANONICAL.items():
            if alias.endswith(suffix) or suffix in alias:
                return canon
        slug = suffix.replace(" ", "").replace("-", "")
        for alias, canon in _NATIVE_TO_CANONICAL.items():
            if slug in alias.replace(" ", "").replace("-", ""):
                return canon
    return None


def native_chain(canon: str, venue: str) -> str | None:
    """canonical ID -> native chain name for a specific venue."""
    m = USDT_CHAIN_ALIASES.get(canon, {})
    return m.get(venue.lower())


def common_canonicals(venue_a: str, venue_b: str) -> list[str]:
    """List of canonical chains supported by both venues."""
    out: list[str] = []
    for canon, m in USDT_CHAIN_ALIASES.items():
        if venue_a.lower() in m and venue_b.lower() in m:
            out.append(canon)
    return out
