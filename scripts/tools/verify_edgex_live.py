#!/usr/bin/env python3
"""Verify EdgeX live integration — public scan + optional authenticated reads.

Usage:
  python3 scripts/tools/verify_edgex_live.py
  python3 scripts/tools/verify_edgex_live.py --base ETH --read-account
  python3 scripts/tools/verify_edgex_live.py --dry-trade

Exits 0 when all requested checks pass; non-zero on failure.
Does not submit live orders unless --live-trade is set (use with caution).
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

from venues.edgex import EdgexVenue  # noqa: E402
from venues.edgex_funding import EdgexFundingProvider  # noqa: E402


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _fail(msg: str) -> None:
    print(f" FAIL {msg}", file=sys.stderr)


def _has_creds() -> bool:
    return bool(
        os.environ.get("EDGEX_ACCOUNT_ID", "").strip()
        and os.environ.get("EDGEX_TRADING_PRIVATE_KEY", "").strip()
    )


def check_public_scan(base: str) -> bool:
    fp = EdgexFundingProvider()
    cur = fp.fetch_current(f"{base.upper()}USDT")
    rate = float(cur.get("rate_pct", 0) or 0)
    mark = float(cur.get("mark_price", 0) or 0)
    if mark <= 0:
        _fail(f"public ticker for {base}: mark_price missing")
        return False
    _ok(f"public scan {base}: rate={rate:.6f}% mark={mark:.2f}")
    return True


def check_account_reads(venue: EdgexVenue) -> bool:
    bal = venue.fetch_usdt_account_balances()
    fut = float(bal.get("futures", 0) or 0)
    _ok(f"account asset: futures_available≈{fut:.4f} USD")
    positions = venue.fetch_futures_positions()
    _ok(f"positions: {len(positions)} open leg(s)")
    if positions:
        sample = positions[0]
        _ok(
            f"  sample {sample.get('symbol')} {sample.get('side')} "
            f"qty={sample.get('qty')} entry≈{sample.get('entry_price', 0):.4f}"
        )
    return True


def check_dry_trade(venue: EdgexVenue, base: str, *, live: bool) -> bool:
    pair = f"{base.upper()}USDT"
    price = venue.get_futures_ticker(pair)
    if price <= 0:
        _fail(f"cannot dry-trade: no mark for {pair}")
        return False
    trade = {
        "symbol": base.upper(),
        "type": "open_long",
        "amount_base": 0.01,
        "amount_usdt": round(0.01 * price, 2),
    }
    market = {base.upper(): {"price": price}}
    results = venue.execute_trades([trade], market, dry_run=not live)
    if not results:
        _fail("execute_trades returned empty")
        return False
    r = results[0]
    status = r.get("status")
    if status not in ("simulated", "filled"):
        _fail(f"trade status={status!r} error={r.get('error')}")
        return False
    mode = "LIVE" if live else "dry-run"
    _ok(f"{mode} open_long {base}: status={status}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify EdgeX live integration")
    parser.add_argument("--base", default="BTC", help="Base asset to probe (default BTC)")
    parser.add_argument(
        "--read-account",
        action="store_true",
        help="Fetch balance/positions via V2 SDK (requires creds)",
    )
    parser.add_argument(
        "--dry-trade",
        action="store_true",
        help="Run execute_trades in dry-run mode",
    )
    parser.add_argument(
        "--live-trade",
        action="store_true",
        help="Submit a tiny live order (requires creds; dangerous)",
    )
    parser.add_argument("--json", action="store_true", help="JSON summary on stdout")
    args = parser.parse_args()

    base = str(args.base).upper()
    summary: dict[str, object] = {"base": base, "checks": {}}
    failed = 0

    print("EdgeX verification")
    if not check_public_scan(base):
        failed += 1
    summary["checks"]["public_scan"] = failed == 0

    need_creds = args.read_account or args.live_trade
    if need_creds and not _has_creds():
        _fail("EDGEX_ACCOUNT_ID and EDGEX_TRADING_PRIVATE_KEY required for account checks")
        failed += 1
        summary["checks"]["credentials"] = False
    elif need_creds:
        summary["checks"]["credentials"] = True
        venue = EdgexVenue()
        if args.read_account:
            try:
                if not check_account_reads(venue):
                    failed += 1
                summary["checks"]["read_account"] = failed == 0
            except Exception as e:
                _fail(f"account read: {e}")
                failed += 1
                summary["checks"]["read_account"] = False

    if args.dry_trade or args.live_trade:
        if args.live_trade and not _has_creds():
            _fail("live trade requires EdgeX credentials")
            failed += 1
        else:
            venue = EdgexVenue()
            try:
                if not check_dry_trade(venue, base, live=args.live_trade):
                    failed += 1
                summary["checks"]["trade"] = failed == 0
            except Exception as e:
                _fail(f"trade check: {e}")
                failed += 1
                summary["checks"]["trade"] = False

    summary["success"] = failed == 0
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'All checks passed' if failed == 0 else f'{failed} check(s) failed'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
