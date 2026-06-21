#!/usr/bin/env python3
"""Telegram channel broadcaster for Pure-Futures funding spreads.

A thin "scan → format → push" wrapper around the existing
:func:`scripts.cli.scan_pure_futures_spreads.scan_pure_futures_spreads`.
Designed for cron / serverless invocation (GitHub Actions, systemd timers,
cron, Airflow, …) — runs one scan, pushes a Markdown digest to a Telegram
chat, and exits. No long-running state, no WebSocket, no order execution.

Environment variables
---------------------
``TELEGRAM_BOT_TOKEN``
    Bot token from ``@BotFather``.
``TELEGRAM_CHAT_ID``
    Target chat / channel id (use ``@channelname`` or numeric id).

Both must be set; otherwise the script prints a warning and exits 0 so a
forgotten secret in CI does not turn the workflow red.

Usage
-----
::

    python3 scripts/notify/telegram_push.py
    python3 scripts/notify/telegram_push.py --venues binance,bybit,okx,bitget,hyperliquid
    python3 scripts/notify/telegram_push.py --top 5 --min-edge 0.05
    python3 scripts/notify/telegram_push.py --include-dex
    python3 scripts/notify/telegram_push.py --dry-run    # skip the HTTP POST

Exit codes
----------
``0``
    Success, or skipped (missing secrets, no spreads, ``--dry-run``).
``2``
    Scan raised an exception — workflow should surface the logs.
``3``
    Telegram API rejected the message (auth / chat_id / network).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli.scan_pure_futures_spreads import scan_pure_futures_spreads  # noqa: E402

# Telegram hard limit for ``sendMessage`` — we leave headroom for the header.
TG_MESSAGE_LIMIT = 4096
TG_CHUNK_LIMIT = 3800

DEFAULT_CEX_VENUES = ["binance", "bitget", "bybit", "okx"]
DEFAULT_DEX_VENUES = ["hyperliquid", "aster", "lighter", "edgex", "dydx"]

DEFAULT_TOP_N = 10
# Push anything with a positive net edge (spread − open-leg taker fee >= 0).
# Tighten via --min-edge when the channel gets noisy in calm markets.
DEFAULT_MIN_EDGE = 0.0
# A top-N opportunity counts as "moved" when its real_edge shifts by at least
# this many percentage points vs the previous snapshot — used for 📈/📉 markers
# and the --skip-if-unchanged anti-spam gate.
DEFAULT_CHANGE_THRESHOLD = 0.01


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _load_config() -> dict[str, str]:
    """Read Telegram credentials from environment.

    Matches the key names used by :mod:`scripts.core.notify` so users only
    need to configure secrets once.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return {"telegram_bot_token": token, "telegram_chat_id": chat_id}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _fmt_pct(x: float | None, plus: bool = True) -> str:
    """Format a percentage value, tolerant of None / NaN."""
    if x is None:
        return "n/a"
    try:
        if plus and x >= 0:
            return f"+{x:.4f}%"
        return f"{x:.4f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_interval(h: Any) -> str:
    """Funding interval in hours → compact label (8.0 → '8h', 0.5 → '0.5h')."""
    try:
        hv = float(h)
    except (TypeError, ValueError):
        return "?"
    return f"{int(hv)}h" if hv == int(hv) else f"{hv:g}h"


def _escape_html(text: str) -> str:
    """Telegram sendMessage parse_mode=HTML needs the five XML entities."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _opportunity_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    """Stable identity of an opportunity: asset + leg pair + direction."""
    return (
        str(row.get("base", "")),
        str(row.get("long_venue", "")),
        str(row.get("short_venue", "")),
        str(row.get("direction", "forward")),
    )


def _change_marker(
    row: dict[str, Any],
    prev_index: dict[tuple[str, str, str, str], float] | None,
    threshold: float,
) -> tuple[str, bool]:
    """Compare a row against the previous snapshot.

    Returns ``(marker, is_new_or_changed)`` where marker is 🆕 for an
    opportunity absent last cycle, 📈/📉 when its real_edge moved by at least
    ``threshold``, and "" when essentially unchanged. With no prior snapshot
    everything is treated as unchanged (no markers, nothing to dedup against).
    """
    if not prev_index:
        return "", False
    key = _opportunity_key(row)
    if key not in prev_index:
        return "🆕", True
    delta = _rank_value(row) - prev_index[key]
    if delta >= threshold:
        return "📈", True
    if delta <= -threshold:
        return "📉", True
    return "", False


def _format_one(
    spread: dict[str, Any],
    prev_index: dict[tuple[str, str, str, str], float] | None = None,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
) -> str:
    """One spread entry → one Telegram line (HTML)."""
    base = _escape_html(str(spread.get("base", "?")))
    long_venue = _escape_html(str(spread.get("long_venue", "?")))
    short_venue = _escape_html(str(spread.get("short_venue", "?")))
    direction = spread.get("direction", "forward")
    arrow = "🟢" if direction == "forward" else "🔴"
    marker, _ = _change_marker(spread, prev_index, change_threshold)
    marker_str = f" {marker}" if marker else ""

    long_rate = _fmt_pct(spread.get("long_rate_pct"))
    short_rate = _fmt_pct(spread.get("short_rate_pct"))
    spread_pct = _fmt_pct(spread.get("spread_pct"), plus=False)
    net_edge = _fmt_pct(spread.get("net_edge_pct"))

    # Settlement-interval mismatch: surface the *actual* intervals (e.g. 1h/8h)
    # so the reader can judge how lopsided the funding accrual is.
    if spread.get("settle_mismatch"):
        flag = (
            f" ⚠️ {_fmt_interval(spread.get('long_interval_h'))}"
            f"/{_fmt_interval(spread.get('short_interval_h'))}"
        )
    else:
        flag = ""

    # real_edge = net_edge − cross-venue mark divergence (basis risk). This is
    # the scanner's primary ranking metric; net_edge alone overstates a perp
    # that is dislocated from its peer venue.
    real_edge_val = spread.get("real_edge_pct")
    mark_spread = spread.get("mark_spread_pct")
    if real_edge_val is not None:
        mark_str = (
            f" after markΔ {mark_spread:.3f}%"
            if isinstance(mark_spread, (int, float))
            else ""
        )
        real_line = f"\n   real_edge <b>{_fmt_pct(real_edge_val)}</b>{mark_str}"
    else:
        real_line = ""

    # One-cycle net: profit if you enter and exit after a single settlement —
    # this must clear the *round-trip* fee (open + close, both legs), not just
    # the open leg. A transient funding spike is only worth eating once if this
    # is positive; otherwise it only pays as a multi-cycle carry.
    spread_val = spread.get("spread_pct")
    rt_fee = spread.get("round_trip_fee_pct")
    if isinstance(spread_val, (int, float)) and isinstance(rt_fee, (int, float)):
        one_cycle_line = (
            f"\n   1-cycle net <b>{_fmt_pct(spread_val - rt_fee)}</b> "
            f"(eat once, after round-trip fee)"
        )
    else:
        one_cycle_line = ""

    # Gross vs net APR — net (net_apy_pct) deducts round-trip fees + entry basis.
    gross = spread.get("annual_apy_pct")
    net_apy = spread.get("net_apy_pct")
    gross_str = f"{gross:.0f}%" if isinstance(gross, (int, float)) else "n/a"
    net_str = f"{net_apy:.0f}%" if isinstance(net_apy, (int, float)) else "n/a"
    fee_str = f" · fee rt {rt_fee:.3f}%" if isinstance(rt_fee, (int, float)) else ""

    return (
        f"{arrow} <b>{base}</b>  {long_venue}L / {short_venue}S{flag}{marker_str}\n"
        f"   rate {long_rate} vs {short_rate}  →  spread {spread_pct}\n"
        f"   net_edge <b>{net_edge}</b>{real_line}{one_cycle_line}\n"
        f"   APR {gross_str} gross / {net_str} net{fee_str}"
    )


def _rank_value(row: dict[str, Any]) -> float:
    """Ranking metric: basis-adjusted real edge, falling back to net edge.

    Mirrors the scanner's own ordering (``scan_pure_futures_spreads`` sorts by
    ``real_edge_pct``) so the digest top-N matches what the scanner considers
    the cleanest opportunities — not just the largest raw funding edge.
    """
    v = row.get("real_edge_pct")
    if v is None:
        v = row.get("net_edge_pct", -1e9)
    try:
        return float(v)
    except (TypeError, ValueError):
        return -1e9


def _load_prev_index(
    path: str | None,
) -> dict[tuple[str, str, str, str], float]:
    """Load the previous snapshot's opportunities → {key: real_edge}.

    Tolerant of a missing / malformed / empty file (returns {}), so a
    first-ever run or a failed fetch simply disables dedup rather than
    crashing the push.
    """
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    opp = data.get("scanner_opportunities", data)
    index: dict[tuple[str, str, str, str], float] = {}
    for direction in ("forward", "reverse"):
        for row in opp.get(direction, []) or []:
            r = dict(row)
            r.setdefault("direction", direction)
            index[_opportunity_key(r)] = _rank_value(r)
    return index


def rank_rows(
    result: dict[str, Any], top_n: int
) -> tuple[list[dict[str, Any]], int]:
    """Flatten forward+reverse, rank by real_edge, return (top_n rows, total)."""
    all_rows: list[dict[str, Any]] = []
    for x in result.get("forward", []):
        x = dict(x)
        x.setdefault("direction", "forward")
        all_rows.append(x)
    for x in result.get("reverse", []):
        x = dict(x)
        x.setdefault("direction", "reverse")
        all_rows.append(x)
    all_rows.sort(key=lambda x: -_rank_value(x))
    return all_rows[:top_n], len(all_rows)


def count_new_or_changed(
    top_rows: list[dict[str, Any]],
    prev_index: dict[tuple[str, str, str, str], float] | None,
    threshold: float = DEFAULT_CHANGE_THRESHOLD,
) -> int:
    """How many of ``top_rows`` are new or moved vs the previous snapshot."""
    return sum(1 for r in top_rows if _change_marker(r, prev_index, threshold)[1])


def format_digest(
    result: dict[str, Any],
    top_n: int = DEFAULT_TOP_N,
    title: str | None = None,
    prev_index: dict[tuple[str, str, str, str], float] | None = None,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
) -> list[str]:
    """Build one or more Telegram HTML messages.

    Splits the digest into multiple messages if it would exceed Telegram's
    4096-char limit. Returns a list so callers can post sequentially.

    When ``prev_index`` is supplied, rows are tagged 🆕 / 📈 / 📉 relative to
    the previous snapshot. Always returns at least one message (an empty-result
    notice when no spreads crossed the threshold).
    """
    venues = result.get("venues", [])
    ts = result.get("timestamp", "")
    try:
        parsed = datetime.fromisoformat(ts).astimezone(timezone.utc)
        ts_human = parsed.strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        ts_human = ts or datetime.now(timezone.utc).isoformat()

    top, total = rank_rows(result, top_n)
    assets = result.get("total_assets_scanned", "?")
    header_venues = ", ".join(venues) if venues else "n/a"

    head = (
        f"📊 <b>Funding Spread Digest</b>\n"
        f"<i>{_escape_html(ts_human)}</i>\n"
        f"venues: {_escape_html(header_venues)} · assets: {assets} · "
        f"candidates: {total}\n"
        f"{'─' * 24}"
    )
    if title:
        head = f"{title}\n{head}"

    if not top:
        return [f"{head}\n\nNo spreads above threshold this cycle. 🌱"]

    chunks: list[str] = []
    current = head + "\n\n"
    for i, row in enumerate(top, start=1):
        block = (
            f"<b>{i}.</b>  "
            + _format_one(row, prev_index, change_threshold)
            + "\n\n"
        )
        if len(current) + len(block) > TG_CHUNK_LIMIT:
            chunks.append(current.rstrip())
            current = ""
        current += block

    footer = (
        "\n<i>net_edge = spread − open-leg fee (carry, fee amortized) · "
        "real_edge = net_edge − markΔ (basis risk)\n"
        "1-cycle net = spread − round-trip fee (profit if you eat one cycle) · "
        "fees = standard taker tier (VIP lower) · "
        "⚠️ = settlement-interval mismatch."
    )
    if prev_index:
        footer += " 🆕 new · 📈/📉 = edge moved vs last cycle."
    footer += "</i>"
    if len(current) + len(footer) > TG_MESSAGE_LIMIT:
        chunks.append(current.rstrip())
        chunks.append(footer.strip())
    else:
        chunks.append((current + footer).rstrip())

    return chunks


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def send_telegram_message(
    text: str,
    config: dict[str, str],
    *,
    timeout: float = 10.0,
) -> bool:
    """POST a single ``sendMessage`` to Telegram.

    Returns True on HTTP 200. Raises on network errors so callers can
    decide between retry / abort.
    """
    token = config["telegram_bot_token"]
    chat_id = config["telegram_chat_id"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Telegram HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Telegram network error: {e.reason}") from e


def broadcast(
    messages: Iterable[str],
    config: dict[str, str],
    *,
    dry_run: bool = False,
    sleep_between: float = 0.5,
) -> tuple[int, int]:
    """Send a sequence of messages. Returns (sent, failed)."""
    sent, failed = 0, 0
    for msg in messages:
        if dry_run:
            print(f"[dry-run] Would post {len(msg)} chars:\n{msg}\n")
            sent += 1
            continue
        try:
            ok = send_telegram_message(msg, config)
        except RuntimeError as e:
            print(f"[push] failed: {e}", file=sys.stderr)
            failed += 1
            continue
        if ok:
            sent += 1
        else:
            failed += 1
        time.sleep(sleep_between)
    return sent, failed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _resolve_venues(venues_arg: str | None, include_dex: bool) -> list[str]:
    if venues_arg:
        return [v.strip().lower() for v in venues_arg.split(",") if v.strip()]
    if include_dex:
        return DEFAULT_CEX_VENUES + DEFAULT_DEX_VENUES
    return DEFAULT_CEX_VENUES + ["hyperliquid"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Push funding-spread digest to a Telegram chat (cron-friendly)."
    )
    parser.add_argument(
        "--venues",
        default=None,
        help="Comma-separated venues (default: CEX + hyperliquid)",
    )
    parser.add_argument(
        "--include-dex",
        action="store_true",
        help="Include all perp DEX venues (hyperliquid,aster,lighter,edgex,dydx)",
    )
    parser.add_argument(
        "--min-edge",
        type=float,
        default=DEFAULT_MIN_EDGE,
        help=f"Minimum net edge %% after fees (default {DEFAULT_MIN_EDGE}%%)",
    )
    parser.add_argument(
        "--min-spread",
        type=float,
        default=0.03,
        help="Minimum raw funding spread %% (default 0.03%%)",
    )
    parser.add_argument(
        "--max-mark-spread",
        type=float,
        default=1.0,
        help="Max mark price spread %% between venues (default 1.0%%)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Top-N rows to publish (default {DEFAULT_TOP_N})",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Optional extra title line at the top of the digest",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=16,
        help="Parallel I/O workers (passed to scanner)",
    )
    parser.add_argument(
        "--prev-snapshot",
        default=None,
        help="Path to the previous run's snapshot JSON; enables 🆕/📈/📉 markers "
        "and --skip-if-unchanged dedup",
    )
    parser.add_argument(
        "--change-threshold",
        type=float,
        default=DEFAULT_CHANGE_THRESHOLD,
        help="real_edge move %% that counts as 'changed' "
        f"(default {DEFAULT_CHANGE_THRESHOLD}%%)",
    )
    parser.add_argument(
        "--skip-if-unchanged",
        action="store_true",
        help="Do not post when no top-N opportunity is new or moved vs "
        "--prev-snapshot (anti-spam for hourly cron)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Format the digest and print it, but do not POST to Telegram",
    )
    args = parser.parse_args()

    config = _load_config()
    if not args.dry_run and not (
        config["telegram_bot_token"] and config["telegram_chat_id"]
    ):
        print(
            "[push] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — "
            "nothing to do (exit 0).",
            file=sys.stderr,
        )
        return 0

    venues = _resolve_venues(args.venues, args.include_dex)
    print(f"[push] scanning venues={venues} min_edge={args.min_edge}", file=sys.stderr)

    t0 = time.time()
    try:
        result = scan_pure_futures_spreads(
            venues=venues,
            min_spread=args.min_spread,
            min_edge=args.min_edge,
            max_mark_spread_pct=args.max_mark_spread,
            workers=args.workers,
        )
    except Exception as e:
        print(f"[push] scanner raised: {e}", file=sys.stderr)
        return 2
    elapsed = time.time() - t0
    print(
        f"[push] scan done in {elapsed:.1f}s — "
        f"{result.get('total_spreads_found', 0)} candidates",
        file=sys.stderr,
    )

    prev_index = _load_prev_index(args.prev_snapshot)
    top, _ = rank_rows(result, args.top)
    changed = count_new_or_changed(top, prev_index, args.change_threshold)
    if prev_index:
        print(
            f"[push] {changed}/{len(top)} top opportunities new or moved "
            f"vs previous snapshot",
            file=sys.stderr,
        )
    if args.skip_if_unchanged and prev_index and changed == 0:
        print(
            "[push] nothing new or changed vs last cycle — skipping post (exit 0).",
            file=sys.stderr,
        )
        return 0

    messages = format_digest(
        result,
        top_n=args.top,
        title=args.title,
        prev_index=prev_index,
        change_threshold=args.change_threshold,
    )
    print(
        f"[push] formatted {len(messages)} message(s) for top {args.top} candidates",
        file=sys.stderr,
    )

    sent, failed = broadcast(messages, config, dry_run=args.dry_run)
    print(f"[push] sent={sent} failed={failed}", file=sys.stderr)
    if failed > 0 and not args.dry_run:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
