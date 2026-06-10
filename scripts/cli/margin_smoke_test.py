#!/usr/bin/env python3
"""各所 cross margin 能力探测（只读 + 配置探测，默认不下单）。

用法:
  python3 scripts/cli/margin_smoke_test.py
  python3 scripts/cli/margin_smoke_test.py --venue okx --asset ETH
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VENUES = {
    "bitget": "venues.bitget.BitgetSpotVenue",
    "bybit": "venues.bybit.BybitSpotVenue",
    "okx": "venues.okx.OkxSpotVenue",
    "binance": "venues.binance.BinanceSpotVenue",
}


def _load_venue(name: str):
    mod_path, cls_name = VENUES[name].rsplit(".", 1)
    mod = __import__(mod_path, fromlist=[cls_name])
    return getattr(mod, cls_name)()


def _probe(venue_id: str, asset: str) -> dict:
    v = _load_venue(venue_id)
    out: dict = {"venue": venue_id, "supports_reverse": False, "debt": {}, "extra": {}}
    if hasattr(v, "supports_reverse_arbitrage"):
        out["supports_reverse"] = bool(v.supports_reverse_arbitrage())
    if hasattr(v, "fetch_margin_debt"):
        out["debt"] = v.fetch_margin_debt([asset.upper()])
    if venue_id == "okx" and hasattr(v, "_get_account_config"):
        cfg = v._get_account_config()
        out["extra"] = {
            "acctLv": cfg.get("acctLv"),
            "autoLoan": cfg.get("autoLoan"),
            "enableSpotBorrow": cfg.get("enableSpotBorrow"),
            "spotBorrowAutoRepay": cfg.get("spotBorrowAutoRepay"),
        }
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Cross margin 能力 smoke test（不下单）")
    p.add_argument("--venue", choices=list(VENUES), help="只测单所")
    p.add_argument("--asset", default="ETH", help="探测负债的币种")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    targets = [args.venue] if args.venue else list(VENUES)
    results = [_probe(v, args.asset) for v in targets]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            flag = "✓" if r["supports_reverse"] else "✗"
            print(f"{flag} {r['venue']:8} reverse={r['supports_reverse']}")
            if r.get("debt"):
                print(f"    debt[{args.asset.upper()}]={r['debt'].get(args.asset.upper(), 0)}")
            if r.get("extra"):
                print(f"    config={r['extra']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
