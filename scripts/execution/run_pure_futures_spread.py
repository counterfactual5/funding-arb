#!/usr/bin/env python3
"""Pure Futures Spread runner — scan → decide → execute → journal.

MVP runner，默认 dry-run：
  python3 scripts/execution/run_pure_futures_spread.py \
    --config templates/config.pure_futures.spread.json --once --verbose

Live 需配置 dry_run=false 或 DCA_LIVE=1。建议先用 scanner/watch + report 确认机会持续性。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli.scan_pure_futures_spreads import scan_pure_futures_spreads  # noqa: E402
from execution.pure_futures_executor import (  # noqa: E402
    close_pure_futures_pair,
    load_pure_futures_positions,
    open_pure_futures_pair,
)
from execution.settle_mismatch_planner import (  # noqa: E402
    effective_trade_usd,
    filter_candidates_with_mismatch,
)

DATA_DIR = SCRIPTS_DIR / "data" / "pure-futures"
JOURNAL_PATH = DATA_DIR / "journal.jsonl"


def _load_json(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dry_run(cfg: dict[str, Any]) -> bool:
    if os.environ.get("DCA_LIVE") == "1":
        return False
    if os.environ.get("DCA_DRY_RUN") == "1":
        return True
    return bool(cfg.get("dry_run", True))


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _candidate_key(row: dict[str, Any]) -> str:
    return ":".join(
        [
            str(row.get("base", "")).upper(),
            str(row.get("direction", "")),
            str(row.get("long_venue", "")),
            str(row.get("short_venue", "")),
        ]
    )


def _position_key(pos: dict[str, Any]) -> str:
    return ":".join(
        [
            str(pos.get("base", "")).upper(),
            str(pos.get("direction", "forward")),
            str(pos.get("long_venue", "")),
            str(pos.get("short_venue", "")),
        ]
    )


def _open_positions() -> list[dict[str, Any]]:
    return [p for p in load_pure_futures_positions() if p.get("status") == "open"]


def run_once(cfg: dict[str, Any], *, verbose: bool = False) -> dict[str, Any]:
    pfa = cfg.get("pureFuturesArbitrage") or {}
    venues = [
        str(v).lower() for v in pfa.get("venues", ["binance", "bitget", "bybit", "okx"])
    ]
    min_spread = float(pfa.get("minSpreadPct", 0.05))
    min_edge = float(pfa.get("minNetEdgePct", 0.01))
    exit_edge = float(pfa.get("exitThresholdPct", 0.01))
    max_pairs = int(pfa.get("maxConcurrentPairs", 3))
    trade_usd = float(pfa.get("tradeUsdPerPair", 500.0))
    max_mark_spread = float(pfa.get("maxMarkSpreadPct", 1.0))
    allow_mismatch = bool(pfa.get("allowSettleMismatch", False))
    workers = int(pfa.get("workers", 4))
    dry_run = _dry_run(cfg)

    # 用宽松阈值扫描，保证 held position 即使跌破入场阈值也能被看见并用于退出判断。
    scan = scan_pure_futures_spreads(
        venues=venues, min_spread=0.0, min_edge=-999.0, workers=workers
    )
    all_rows = list(scan.get("forward", [])) + list(scan.get("reverse", []))
    row_by_key = {_candidate_key(r): r for r in all_rows}

    actions: list[dict[str, Any]] = []

    # 1) Exit first.
    for pos in _open_positions():
        key = _position_key(pos)
        row = row_by_key.get(key)
        edge = float(row.get("net_edge_pct", -999.0)) if row else -999.0
        should_close = row is None or edge <= exit_edge
        if should_close:
            res = close_pure_futures_pair(str(pos["id"]), dry_run=dry_run, config=cfg)
            actions.append(
                {
                    "action": "close",
                    "position_id": pos.get("id"),
                    "edge": edge,
                    "result": res.to_dict(),
                }
            )
            if verbose:
                print(f"close {pos.get('id')} edge={edge:.4f}% state={res.state}")

    # 2) Open new slots.
    active = _open_positions()
    active_keys = {_position_key(p) for p in active}
    slots = max(0, max_pairs - len(active))

    # Pre-filter by basic thresholds
    candidates = []
    for row in all_rows:
        if float(row.get("spread_pct", 0.0) or 0.0) < min_spread:
            continue
        if float(row.get("net_edge_pct", 0.0) or 0.0) < min_edge:
            continue
        key = _candidate_key(row)
        if key in active_keys:
            continue
        candidates.append(row)

    # Apply settle_mismatch planner
    candidates = filter_candidates_with_mismatch(
        candidates,
        allow_mismatch=allow_mismatch,
        max_cumulative_outflow_pct=0.5,
        min_adjusted_edge_pct=min_edge,
    )

    candidates.sort(key=lambda x: -float(x.get("adjusted_net_edge_pct", 0.0) or x.get("net_edge_pct", 0.0)))

    for row in candidates[:slots]:
        row_trade_usd = effective_trade_usd(trade_usd, row)
        res = open_pure_futures_pair(
            str(row["base"]),
            str(row["long_venue"]),
            str(row["short_venue"]),
            row_trade_usd,
            dry_run=dry_run,
            direction=str(row.get("direction", "forward")),
            max_mark_spread_pct=max_mark_spread,
            config=cfg,
            capital_buffer_pct=float(row.get("capital_buffer_pct", 0) or 0),
        )
        actions.append({"action": "open", "candidate": row, "result": res.to_dict()})
        if verbose:
            print(
                f"open {row['base']} long@{row['long_venue']} short@{row['short_venue']} "
                f"edge={row.get('adjusted_net_edge_pct', row.get('net_edge_pct', 0)):.4f}% state={res.state}"
            )

    summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "strategy": "pure_futures_spread",
        "dry_run": dry_run,
        "venues": venues,
        "scan_total": len(all_rows),
        "candidates_after_filter": len(candidates),
        "actions": actions,
        "open_positions": len(_open_positions()),
        "thresholds": {
            "minSpreadPct": min_spread,
            "minNetEdgePct": min_edge,
            "exitThresholdPct": exit_edge,
            "allowSettleMismatch": allow_mismatch,
        },
    }
    _append_jsonl(JOURNAL_PATH, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Pure futures spread runner")
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    parser.add_argument(
        "--watch",
        type=float,
        metavar="MINUTES",
        help="run continuously every N minutes",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    cfg = _load_json(args.config)
    if not args.once and not args.watch:
        args.once = True

    if args.once:
        out = run_once(cfg, verbose=args.verbose)
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        elif args.verbose:
            print(
                f"done actions={len(out['actions'])} open_positions={out['open_positions']}"
            )
        return 0

    interval = float(args.watch) * 60.0
    while True:
        t0 = time.time()
        out = run_once(cfg, verbose=args.verbose)
        if args.json:
            print(json.dumps(out, ensure_ascii=False))
        elapsed = time.time() - t0
        time.sleep(max(0.0, interval - elapsed))


if __name__ == "__main__":
    raise SystemExit(main())
