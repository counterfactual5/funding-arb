#!/usr/bin/env python3
"""Manual CLI for pure futures spread pair open/close/list.

示例：
  python3 scripts/cli/pure_futures_trade.py open BTC --long-venue okx --short-venue bybit --trade-usd 500 --dry-run
  python3 scripts/cli/pure_futures_trade.py list
  python3 scripts/cli/pure_futures_trade.py close pf-BTC-okx-bybit-... --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.pure_futures_executor import (  # noqa: E402
    close_pure_futures_pair,
    load_pure_futures_positions,
    open_pure_futures_pair,
)


def _print_result(result) -> None:
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


def main() -> int:
    p = argparse.ArgumentParser(description="Pure futures spread manual trade CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    op = sub.add_parser("open", help="open long-perp + short-perp pair")
    op.add_argument("base", help="Base asset, e.g. BTC")
    op.add_argument("--long-venue", required=True, help="Venue for open_long")
    op.add_argument("--short-venue", required=True, help="Venue for open_short")
    op.add_argument("--trade-usd", type=float, required=True)
    op.add_argument("--quote", default="USDT")
    op.add_argument(
        "--direction",
        choices=("forward", "reverse"),
        default="forward",
        help="metadata from scanner classification",
    )
    op.add_argument("--max-mark-spread-pct", type=float, default=1.0)
    op.add_argument("--dry-run", action="store_true", help="simulate only")
    op.add_argument("--live", action="store_true", help="submit live orders")

    cl = sub.add_parser("close", help="close an open pure futures pair")
    cl.add_argument("position_id")
    cl.add_argument("--quote", default="USDT")
    cl.add_argument("--dry-run", action="store_true", help="simulate close")
    cl.add_argument("--live", action="store_true", help="submit live orders")

    ls = sub.add_parser("list", help="list pure futures positions")
    ls.add_argument("--all", action="store_true", help="include closed positions")
    ls.add_argument("--json", action="store_true")

    args = p.parse_args()
    if args.cmd == "open":
        if args.live and args.dry_run:
            p.error("--live and --dry-run are mutually exclusive")
        dry_run = not args.live
        res = open_pure_futures_pair(
            args.base.upper(),
            args.long_venue.lower(),
            args.short_venue.lower(),
            args.trade_usd,
            dry_run=dry_run,
            quote=args.quote.upper(),
            direction=args.direction,
            max_mark_spread_pct=args.max_mark_spread_pct,
        )
        _print_result(res)
        return 0 if res.ok else 2

    if args.cmd == "close":
        if args.live and args.dry_run:
            p.error("--live and --dry-run are mutually exclusive")
        dry_run = None if not args.live and not args.dry_run else bool(args.dry_run)
        if args.live:
            dry_run = False
        res = close_pure_futures_pair(
            args.position_id, dry_run=dry_run, quote=args.quote.upper()
        )
        _print_result(res)
        return 0 if res.ok else 2

    if args.cmd == "list":
        rows = load_pure_futures_positions()
        if not args.all:
            rows = [r for r in rows if r.get("status") == "open"]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            if not rows:
                print("No positions.")
            for r in rows:
                print(
                    f"{r.get('id')} {r.get('status')} {r.get('base')} "
                    f"long@{r.get('long_venue')} short@{r.get('short_venue')} "
                    f"qty={r.get('qty')} dry={r.get('dry_run')}"
                )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
