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
    python3 scripts/notify/telegram_push.py --dashboard-url ""  # no inline buttons

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
# Only annotate a row with its cross-venue mark divergence (mkΔ) when it is
# large enough to plausibly matter — below this, net_edge and real_edge are
# close enough that spelling it out is just noise.
BASIS_ANNOTATION_THRESHOLD = 0.05
# Default demo dashboard — Telegram inline "view more" buttons deep-link here
# (see README's Live Demo link). Override with --dashboard-url.
DEFAULT_DASHBOARD_URL = "https://funding-arb-drab.vercel.app"


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
    """One spread entry → one compact Telegram line (HTML).

    Deliberately terse — a phone notification needs the decision-relevant
    numbers (fee-adjusted edge, annualized return, whether it's a repeatable
    carry vs a one-off spike, and any settlement risk), not every intermediate
    figure the scanner computes. Full detail is one tap away via the
    dashboard buttons appended to the digest.
    """
    base = _escape_html(str(spread.get("base", "?")))
    long_venue = _escape_html(str(spread.get("long_venue", "?")))
    short_venue = _escape_html(str(spread.get("short_venue", "?")))
    direction = spread.get("direction", "forward")
    arrow = "🟢" if direction == "forward" else "🔴"
    marker, _ = _change_marker(spread, prev_index, change_threshold)
    marker_str = f" {marker}" if marker else ""

    # Settlement-interval mismatch: surface the *actual* intervals (e.g. 1h/8h)
    # so the reader can judge how lopsided the funding accrual is.
    if spread.get("settle_mismatch"):
        flag = (
            f" ⚠️ {_fmt_interval(spread.get('long_interval_h'))}"
            f"/{_fmt_interval(spread.get('short_interval_h'))}"
        )
    else:
        flag = ""

    net_edge = _fmt_pct(spread.get("net_edge_pct"))

    # net_edge already deducts the open-leg fee. Only call out the extra
    # cross-venue mark-price gap (mkΔ, the basis-risk discount baked into
    # real_edge) when it's large enough to plausibly change the decision.
    mark_spread = spread.get("mark_spread_pct")
    if isinstance(mark_spread, (int, float)) and mark_spread >= BASIS_ANNOTATION_THRESHOLD:
        basis = f" (mkΔ{mark_spread:.2f}%)"
    else:
        basis = ""

    # APR: net (fee + entry-basis adjusted) when available, else gross.
    apr = spread.get("net_apy_pct")
    if not isinstance(apr, (int, float)):
        apr = spread.get("annual_apy_pct")
    apr_str = f"{apr:.0f}%" if isinstance(apr, (int, float)) else "n/a"

    # Persistence: % of recent cycles the oriented spread stayed positive,
    # plus a ⚡ flag when the current spread is an outlier vs its own history
    # (i.e. a transient spike, not a repeatable carry).
    hist_cycles = spread.get("hist_cycles")
    if isinstance(hist_cycles, (int, float)) and hist_cycles:
        held_pct = spread.get("hist_held_pct", 0)
        spike = "⚡" if spread.get("is_spike") else ""
        persist = f"  P{held_pct:.0f}%{spike}"
    else:
        persist = ""

    return (
        f"{arrow} <b>{base}</b>  {long_venue}L/{short_venue}S{flag}{marker_str}  "
        f"net <b>{net_edge}</b>{basis}  APR {apr_str}{persist}"
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


def rank_rows(result: dict[str, Any], top_n: int) -> tuple[list[dict[str, Any]], int]:
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
    top_rows: list[dict[str, Any]] | None = None,
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

    ranked, total = rank_rows(result, top_n)
    # Callers may pass already-ranked rows enriched with persistence metrics
    # (rank_rows copies dicts, so re-ranking here would drop those annotations).
    top = top_rows if top_rows is not None else ranked
    assets = result.get("total_assets_scanned", "?")

    head = (
        f"📊 <b>Funding Spread Digest</b>\n"
        f"<i>{_escape_html(ts_human)}</i>\n"
        f"{len(venues)} venues · assets: {assets} · candidates: {total}\n"
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
            f"<b>{i}.</b>  " + _format_one(row, prev_index, change_threshold) + "\n\n"
        )
        if len(current) + len(block) > TG_CHUNK_LIMIT:
            chunks.append(current.rstrip())
            current = ""
        current += block

    # Only spell out legends for markers actually present this cycle — keeps
    # the footer short on quiet cycles instead of always listing everything.
    legend = ["net = fee-adjusted edge", "APR = net annualized"]
    if any(
        isinstance(r.get("mark_spread_pct"), (int, float))
        and r["mark_spread_pct"] >= BASIS_ANNOTATION_THRESHOLD
        for r in top
    ):
        legend.append("mkΔ = cross-venue mark gap (basis risk)")
    if any(r.get("hist_cycles") for r in top):
        legend.append("P = % of recent cycles held positive")
        legend.append("⚡ = spike (>3× recent median)")
    if any(r.get("settle_mismatch") for r in top):
        legend.append("⚠️ = settlement-interval mismatch")
    if prev_index:
        legend.append("🆕 new")
        legend.append("📈/📉 = edge moved vs last cycle")
    footer = f"\n<i>{' · '.join(legend)}</i>"

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
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """POST a single ``sendMessage`` to Telegram.

    Returns True on HTTP 200. Raises on network errors so callers can
    decide between retry / abort. ``reply_markup`` (e.g. an inline keyboard)
    is attached only when given — most chunks of a multi-message digest
    don't need one.
    """
    token = config["telegram_bot_token"]
    chat_id = config["telegram_chat_id"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    body: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        body["reply_markup"] = reply_markup
    payload = json.dumps(body).encode("utf-8")

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
    reply_markup_last: dict[str, Any] | None = None,
) -> tuple[int, int]:
    """Send a sequence of messages. Returns (sent, failed).

    ``reply_markup_last`` (e.g. an inline keyboard with dashboard links) is
    attached only to the final message, so it appears once after the whole
    digest rather than repeated on every chunk.
    """
    msgs = list(messages)
    sent, failed = 0, 0
    for i, msg in enumerate(msgs):
        markup = reply_markup_last if i == len(msgs) - 1 else None
        if dry_run:
            print(f"[dry-run] Would post {len(msg)} chars:\n{msg}\n")
            if markup:
                print(f"[dry-run] buttons: {markup}")
            sent += 1
            continue
        try:
            ok = send_telegram_message(msg, config, reply_markup=markup)
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
        "--persistence-days",
        type=int,
        default=3,
        help="Days of funding history to fetch for persistence/spike labels "
        "(0 disables; default 3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Format the digest and print it, but do not POST to Telegram",
    )
    parser.add_argument(
        "--dashboard-url",
        default=DEFAULT_DASHBOARD_URL,
        help="Base URL for the digest's inline 'view more' buttons "
        f"(default {DEFAULT_DASHBOARD_URL}; pass an empty string to disable)",
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

    if args.persistence_days > 0:
        try:
            from notify.persistence import annotate_persistence

            t1 = time.time()
            annotate_persistence(top, days=args.persistence_days, workers=args.workers)
            labeled = sum(1 for r in top if r.get("hist_cycles"))
            print(
                f"[push] persistence labeled {labeled}/{len(top)} rows "
                f"in {time.time() - t1:.1f}s",
                file=sys.stderr,
            )
        except Exception as e:  # best-effort — never block the push
            print(f"[push] persistence enrichment skipped: {e}", file=sys.stderr)

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
        top_rows=top,
    )
    print(
        f"[push] formatted {len(messages)} message(s) for top {args.top} candidates",
        file=sys.stderr,
    )

    reply_markup = None
    if args.dashboard_url:
        base = args.dashboard_url.rstrip("/")
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "📊 Dashboard", "url": base},
                    {"text": "📈 Carry", "url": f"{base}/?strategy=carry"},
                    {"text": "🔀 Unified", "url": f"{base}/?strategy=unified"},
                ]
            ]
        }

    sent, failed = broadcast(
        messages, config, dry_run=args.dry_run, reply_markup_last=reply_markup
    )
    print(f"[push] sent={sent} failed={failed}", file=sys.stderr)
    if failed > 0 and not args.dry_run:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
