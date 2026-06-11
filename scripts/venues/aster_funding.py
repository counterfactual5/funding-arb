#!/usr/bin/env python3
"""Aster funding rate provider — Binance-fapi-compatible Perp DEX (BNB chain ecosystem).

API base: https://fapi.asterdex.com — endpoints mirror Binance Futures:
  GET /fapi/v1/premiumIndex   — mark price / lastFundingRate / nextFundingTime
  GET /fapi/v1/fundingRate    — settled funding history
  GET /fapi/v1/fundingInfo    — per-symbol fundingIntervalHours (NOT uniform!)

IMPORTANT: Aster settlement intervals are per-symbol (8h default, some 4h/1h)
and can change dynamically — always read fundingInfo per scan, never assume 8h.
"""

from __future__ import annotations

from backtest.funding_providers import BinanceFundingProvider


class AsterFundingProvider(BinanceFundingProvider):
    """Binance-compatible provider pointed at Aster's fapi base URL."""

    venue_id = "aster"
    BASE = "https://fapi.asterdex.com"
