#!/usr/bin/env python3
"""Verify Hyperliquid live trading integration — public scan + optional account reads.

Usage:
  python3 scripts/tools/verify_hyperliquid_live.py
  python3 scripts/tools/verify_hyperliquid_live.py --base ETH --read-account
  python3 scripts/tools/verify_hyperliquid_live.py --dry-trade

Exits 0 when all requested checks pass; non-zero on failure.
Does not submit live orders unless --live-trade is set (use with caution).

Note: account reads and live trading require the sibling `hyperliquid` repo
      cloned next to funding-arb (../hyperliquid/scripts/hyperliquid_order.py).
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


# ── checks ─────────────────────────────────────────────────────────────────


def check_sdk() -> bool:
    """Verify venue adapter imports work (always succeeds for read paths)."""
    try:
        from venues.hyperliquid import HyperliquidVenue  # noqa: F401
        from venues.hyperliquid_funding import HyperliquidFundingProvider  # noqa: F401

        _ok("venue adapter import (venues.hyperliquid)")
        return True
    except ImportError as e:
        _fail(f"venue adapter import: {e}")
        return False


def check_sibling_repo() -> bool:
    """Check if the sibling hyperliquid repo exists for write-path support."""
    sibling = (
        Path(__file__).resolve().parent.parent.parent.parent / "hyperliquid" / "scripts"
    )
    order_script = sibling / "hyperliquid_order.py"
    if sibling.is_dir() and order_script.exists():
        _ok(f"sibling repo found: {sibling}")
        return True
    _warn(
        f"sibling repo not found at {sibling} (account reads / live trades unavailable)"
    )
    return False


def check_env() -> bool:
    """Verify Hyperliquid credential environment variables are set."""
    key = os.environ.get("HYPERLIQUID_API_KEY", "").strip()
    secret = os.environ.get("HYPERLIQUID_API_SECRET", "").strip()
    if key and secret:
        # Mask the ETH address for display
        display = f"{key[:6]}...{key[-4:]}" if len(key) > 10 else "***"
        _ok(f"env HYPERLIQUID_API_KEY={display}, HYPERLIQUID_API_SECRET=***set***")
        return True
    missing = []
    if not key:
        missing.append("HYPERLIQUID_API_KEY")
    if not secret:
        missing.append("HYPERLIQUID_API_SECRET")
    _warn(f"env vars not set: {', '.join(missing)}")
    return False


def check_public_scan(base: str) -> bool:
    """Fetch funding rate and mark price via public HTTP (no creds needed)."""
    from venues.hyperliquid_funding import HyperliquidFundingProvider

    try:
        fp = HyperliquidFundingProvider()
        cur = fp.fetch_current(f"{base.upper()}USDT")
    except Exception as e:
        _fail(f"public scan {base}: API error — {e}")
        return False

    rate = float(cur.get("rate_pct", 0) or 0)
    mark = float(cur.get("mark_price", 0) or 0)
    if mark <= 0:
        _fail(f"public scan {base}: mark_price missing (API returned 0)")
        return False
    _ok(f"public scan {base}: rate={rate:.6f}%  mark={mark:.2f}")
    return True


def check_account_reads(base: str) -> bool:
    """Fetch account balances and positions (requires sibling repo + creds)."""
    from venues.hyperliquid import HyperliquidVenue

    venue = HyperliquidVenue()
    try:
        bal = venue.fetch_usdt_account_balances()
    except Exception as e:
        _fail(f"account balances: {e}")
        return False

    fut = float(bal.get("futures", 0) or 0)
    _ok(f"account balance: futures≈{fut:.4f} USDC")

    try:
        positions = venue.fetch_futures_positions()
    except Exception as e:
        _fail(f"positions: {e}")
        return False

    _ok(f"positions: {len(positions)} open leg(s)")
    if positions:
        sample = positions[0]
        _ok(
            f"  sample {sample.get('symbol')} {sample.get('side')} "
            f"qty={sample.get('qty')} entry≈{sample.get('entry_price', 0):.4f}"
        )
    return True


def check_dry_trade(base: str, *, live: bool = False) -> bool:
    """Execute a tiny trade (dry-run by default, live if requested)."""
    from venues.hyperliquid import HyperliquidVenue

    venue = HyperliquidVenue()
    pair = f"{base.upper()}USDT"
    price = venue.get_futures_ticker(pair)
    if price <= 0:
        _fail(f"cannot trade: no mark price for {pair}")
        return False

    trade = {
        "symbol": pair,
        "type": "open_long",
        "amount_base": 0.001,
        "amount_usdt": round(0.001 * price, 2),
    }
    market = {pair: {"price": price}}

    try:
        results = venue.execute_trades([trade], market, dry_run=not live)
    except Exception as e:
        _fail(f"execute_trades raised: {e}")
        return False

    if not results:
        _fail("execute_trades returned empty")
        return False

    r = results[0]
    status = r.get("status")
    if status not in ("simulated", "filled"):
        _fail(f"trade status={status!r}  error={r.get('error')}")
        return False

    mode = "LIVE" if live else "dry-run"
    _ok(f"{mode} open_long {base}: status={status}")
    return True


# ── main ───────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify Hyperliquid live trading integration"
    )
    parser.add_argument(
        "--base", default="BTC", help="Base asset to probe (default BTC)"
    )
    parser.add_argument(
        "--read-account",
        action="store_true",
        help="Fetch balance/positions (requires creds + sibling repo)",
    )
    parser.add_argument(
        "--dry-trade",
        action="store_true",
        help="Run execute_trades in dry-run mode (requires sibling repo)",
    )
    parser.add_argument(
        "--live-trade",
        action="store_true",
        help="Submit a tiny live order (requires creds + sibling repo; dangerous)",
    )
    parser.add_argument("--json", action="store_true", help="JSON summary on stdout")
    args = parser.parse_args()

    base = str(args.base).upper()
    summary: dict = {"base": base, "checks": {}}
    failed = 0

    print("Hyperliquid verification")
    print("=" * 40)

    # ── SDK ─────────────────────────────────────────────────────────────
    if not check_sdk():
        failed += 1
    summary["checks"]["sdk"] = failed == 0

    # ── sibling repo (informational) ────────────────────────────────────
    has_sibling = check_sibling_repo()
    summary["checks"]["sibling_repo"] = has_sibling

    # ── public scan (always runs) ───────────────────────────────────────
    if not check_public_scan(base):
        failed += 1
    summary["checks"]["public_scan"] = failed == 0

    # ── credentials check (if needed) ───────────────────────────────────
    need_creds = args.read_account or args.live_trade or args.dry_trade
    has_creds = check_env() if need_creds else False
    if need_creds:
        summary["checks"]["credentials"] = has_creds

    # ── account reads ───────────────────────────────────────────────────
    if args.read_account:
        if not has_creds:
            _fail(
                "--read-account requires HYPERLIQUID_API_KEY and HYPERLIQUID_API_SECRET"
            )
            failed += 1
            summary["checks"]["read_account"] = False
        elif not has_sibling:
            _fail(
                "--read-account requires the sibling hyperliquid repo (../hyperliquid/scripts)"
            )
            failed += 1
            summary["checks"]["read_account"] = False
        else:
            try:
                if not check_account_reads(base):
                    failed += 1
                summary["checks"]["read_account"] = True
            except Exception as e:
                _fail(f"account read: {e}")
                failed += 1
                summary["checks"]["read_account"] = False

    # ── trade (dry or live) ─────────────────────────────────────────────
    if args.dry_trade or args.live_trade:
        if args.live_trade and not has_creds:
            _fail(
                "--live-trade requires HYPERLIQUID_API_KEY and HYPERLIQUID_API_SECRET"
            )
            failed += 1
            summary["checks"]["trade"] = False
        elif not has_sibling:
            _fail(
                "trade check requires the sibling hyperliquid repo (../hyperliquid/scripts)"
            )
            failed += 1
            summary["checks"]["trade"] = False
        else:
            try:
                if not check_dry_trade(base, live=args.live_trade):
                    failed += 1
                summary["checks"]["trade"] = True
            except Exception as e:
                _fail(f"trade check: {e}")
                failed += 1
                summary["checks"]["trade"] = False

    # ── summary ─────────────────────────────────────────────────────────
    summary["success"] = failed == 0
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print()
        if failed == 0:
            print("All checks passed ✓")
        else:
            print(f"{failed} check(s) failed ✗")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
