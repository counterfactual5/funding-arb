#!/usr/bin/env python3
"""Verify dYdX v4 live trading integration — public scan + optional account reads.

Usage:
  python3 scripts/tools/verify_dydx_live.py
  python3 scripts/tools/verify_dydx_live.py --read-account
  python3 scripts/tools/verify_dydx_live.py --dry-trade
  python3 scripts/tools/verify_dydx_live.py --network testnet --read-account

Exits 0 when all requested checks pass; non-zero on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _warn(msg: str) -> None:
    print(f"  ⚠ {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗ {msg}", file=sys.stderr)


def check_sdk() -> bool:
    """Check if dydx-v4-client SDK is installed."""
    try:
        import dydx_v4_client  # noqa: F401

        _ok("dydx-v4-client SDK installed")
        return True
    except ImportError:
        _fail("dydx-v4-client not installed (pip install dydx-v4-client>=1.1.5)")
        return False


def check_env() -> bool:
    """Check required environment variables."""
    mnemonic = os.environ.get("DYDX_MNEMONIC", "").strip()
    address = os.environ.get("DYDX_ADDRESS", "").strip()
    if not mnemonic:
        _fail("DYDX_MNEMONIC not set")
        return False
    if not address:
        _fail("DYDX_ADDRESS not set")
        return False
    # Mask mnemonic: show first 2 and last 2 words
    words = mnemonic.split()
    if len(words) >= 4:
        masked = f"{words[0]} {words[1]} ... {words[-2]} {words[-1]}"
    else:
        masked = "***"
    # Mask address: show first 6 and last 4 chars
    addr_masked = f"{address[:6]}...{address[-4:]}" if len(address) > 10 else "***"
    _ok(f"DYDX_MNEMONIC: {masked} ({len(words)} words)")
    _ok(f"DYDX_ADDRESS: {addr_masked}")
    return True


def check_public_scan(base: str) -> bool:
    """Test public funding rate scan (no credentials needed)."""
    try:
        from venues.dydx_funding import DydxFundingProvider

        fp = DydxFundingProvider()
        cur = fp.fetch_current(f"{base.upper()}USDT")
        rate = float(cur.get("rate_pct", 0) or 0)
        mark = float(cur.get("mark_price", 0) or 0)
        interval = float(cur.get("interval_ms", 3600000) or 3600000) / 1000 / 3600
        if mark <= 0:
            _fail(f"public scan {base}: no mark price returned")
            return False
        _ok(
            f"public scan {base}: rate={rate:.6f}% mark={mark:.2f} "
            f"interval={interval:.0f}h"
        )
        return True
    except Exception as e:
        _fail(f"public scan {base}: {e}")
        return False


def check_account_reads(base: str) -> bool:
    """Read account balance and positions via DydxVenue."""
    os.environ["DYDX_ENABLE_LIVE"] = "1"
    try:
        from venues.dydx import DydxVenue

        venue = DydxVenue()

        # Balance
        bal = venue.fetch_usdt_account_balances()
        fut = float(bal.get("futures", 0) or 0)
        _ok(f"account balance: {fut:.4f} USDC (free collateral)")

        # Positions
        positions = venue.fetch_futures_positions()
        _ok(f"open positions: {len(positions)}")
        for p in positions[:3]:
            _ok(
                f"  {p.get('symbol')} {p.get('side')} "
                f"qty={p.get('qty')} "
                f"entry={p.get('entry_price', 0):.2f} "
                f"pnl={p.get('unrealized_pnl', 0):.4f}"
            )

        # Meta
        meta = venue.contract_meta_for_base(base.upper())
        if meta:
            _ok(
                f"market meta {base}: "
                f"step={meta.get('step_size')} "
                f"tick={meta.get('tick_size')} "
                f"clobPairId={meta.get('clob_pair_id')}"
            )

        return True
    except Exception as e:
        _fail(f"account read: {e}")
        return False


def check_dry_trade(base: str) -> bool:
    """Execute a dry-run trade."""
    try:
        from venues.dydx import DydxVenue

        venue = DydxVenue()
        pair = f"{base.upper()}USDT"
        price = venue.get_futures_ticker(pair)
        if price <= 0:
            _fail(f"dry-trade: no price for {pair}")
            return False
        trade = {
            "symbol": pair,
            "type": "open_long",
            "amount_base": 0.001,
        }
        market = {pair: {"price": price}}
        results = venue.execute_trades([trade], market, dry_run=True)
        if not results:
            _fail("execute_trades returned empty")
            return False
        r = results[0]
        status = r.get("status")
        if status != "simulated":
            _fail(f"dry-trade status={status!r} error={r.get('error')}")
            return False
        _ok(
            f"dry-run open_long {base}: status={status} "
            f"price={r.get('exec_price', 0):.2f}"
        )
        return True
    except Exception as e:
        _fail(f"dry-trade: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify dYdX v4 live trading integration"
    )
    parser.add_argument("--base", default="BTC", help="Base asset (default BTC)")
    parser.add_argument(
        "--read-account",
        action="store_true",
        help="Read account balance/positions",
    )
    parser.add_argument(
        "--dry-trade",
        action="store_true",
        help="Dry-run order execution",
    )
    parser.add_argument(
        "--network",
        default="testnet",
        choices=["mainnet", "testnet"],
        help="Network (default testnet)",
    )
    parser.add_argument("--mnemonic", help="Override DYDX_MNEMONIC")
    parser.add_argument("--address", help="Override DYDX_ADDRESS")
    parser.add_argument("--json", action="store_true", help="JSON summary output")
    args = parser.parse_args()

    # Apply overrides
    if args.mnemonic:
        os.environ["DYDX_MNEMONIC"] = args.mnemonic
    if args.address:
        os.environ["DYDX_ADDRESS"] = args.address
    os.environ["DYDX_NETWORK"] = args.network

    base = args.base.upper()
    summary: dict = {"base": base, "network": args.network, "checks": {}}
    failed = 0

    print(f"dYdX v4 verification (network={args.network})")
    print()

    # Step 1: SDK check
    if not check_sdk():
        summary["checks"]["sdk"] = False
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1
    summary["checks"]["sdk"] = True

    # Step 2: Public scan (always)
    if not check_public_scan(base):
        failed += 1
    summary["checks"]["public_scan"] = failed == 0

    # Step 3: Account reads (needs creds)
    if args.read_account:
        if not check_env():
            failed += 1
            summary["checks"]["credentials"] = False
        else:
            summary["checks"]["credentials"] = True
            if not check_account_reads(base):
                failed += 1
            summary["checks"]["read_account"] = True

    # Step 4: Dry trade
    if args.dry_trade:
        if not check_dry_trade(base):
            failed += 1
        summary["checks"]["dry_trade"] = True

    summary["success"] = failed == 0
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"\n{'All checks passed ✓' if failed == 0 else f'{failed} check(s) failed ✗'}"
        )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
