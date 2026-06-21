"""Tests for scripts/notify/telegram_push.py formatting & chunking logic.

We deliberately do NOT hit the network here — ``send_telegram_message`` is
covered by manual smoke tests. The formatting / chunking logic is where
regressions tend to creep in, so that's what we lock down.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notify.telegram_push import (  # noqa: E402
    TG_MESSAGE_LIMIT,
    _escape_html,
    _fmt_pct,
    _load_config,
    _resolve_venues,
    broadcast,
    format_digest,
    send_telegram_message,
)

# ---------------------------------------------------------------------------
# Sample fixtures
# ---------------------------------------------------------------------------


def _make_spread(**overrides) -> dict:
    """A canonical spread row, matching the scanner output schema."""
    base = {
        "base": "BTC",
        "direction": "forward",
        "long_venue": "okx",
        "short_venue": "bybit",
        "long_rate_pct": 0.0125,
        "short_rate_pct": -0.0050,
        "spread_pct": 0.0175,
        "fee_pct": 0.0080,
        "net_edge_pct": 0.0095,
        "annual_apy_pct": 83.0,
        "mark_spread_pct": 0.03,
        "settle_mismatch": False,
    }
    base.update(overrides)
    return base


def _make_result(rows="__default__", venues=None) -> dict:
    if rows == "__default__":
        rows = [_make_spread()]
    forward = [r for r in rows if r.get("direction", "forward") == "forward"]
    reverse = [r for r in rows if r.get("direction") == "reverse"]
    return {
        "venues": venues or ["binance", "bitget", "bybit", "okx"],
        "total_assets_scanned": 187,
        "total_spreads_found": len(rows),
        "forward": forward,
        "reverse": reverse,
        "venue_pair_stats": [],
        "timestamp": "2026-06-21T08:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# _fmt_pct
# ---------------------------------------------------------------------------


class TestFmtPct:
    def test_positive_with_plus(self):
        assert _fmt_pct(0.0125) == "+0.0125%"

    def test_negative(self):
        assert _fmt_pct(-0.005) == "-0.0050%"

    def test_zero(self):
        assert _fmt_pct(0.0) == "+0.0000%"

    def test_plus_false(self):
        assert _fmt_pct(0.0125, plus=False) == "0.0125%"

    def test_none(self):
        assert _fmt_pct(None) == "n/a"

    def test_nan_string(self):
        assert _fmt_pct("not a number") == "n/a"


# ---------------------------------------------------------------------------
# _escape_html
# ---------------------------------------------------------------------------


class TestEscapeHtml:
    def test_ampersand(self):
        assert _escape_html("a & b") == "a &amp; b"

    def test_angle_brackets(self):
        assert _escape_html("<script>") == "&lt;script&gt;"

    def test_quotes(self):
        out = _escape_html("""say "hi" 'yo'""")
        assert "&quot;" in out and "&#39;" in out

    def test_plain_passthrough(self):
        assert _escape_html("BTC/USDT") == "BTC/USDT"


# ---------------------------------------------------------------------------
# format_digest
# ---------------------------------------------------------------------------


class TestFormatDigest:
    def test_empty_rows_emits_placeholder(self):
        result = _make_result(rows=[])
        msgs = format_digest(result, top_n=10)
        assert len(msgs) == 1
        assert "No spreads above threshold" in msgs[0]
        assert "🌱" in msgs[0]

    def test_includes_header_metadata(self):
        result = _make_result()
        msgs = format_digest(result, top_n=10)
        assert "Funding Spread Digest" in msgs[0]
        assert "BTC" in msgs[0]
        assert "okx" in msgs[0]
        assert "bybit" in msgs[0]
        assert "candidates: 1" in msgs[0]

    def test_direction_emoji(self):
        forward = _make_result(rows=[_make_spread(direction="forward")])
        reverse = _make_result(rows=[_make_spread(direction="reverse")])
        assert "🟢" in format_digest(forward)[0]
        assert "🔴" in format_digest(reverse)[0]

    def test_settle_mismatch_flag(self):
        # Footer always mentions "⚠️ = settlement mismatch" as a legend, so we
        # assert on the in-row flag (right after the leg pair) not the whole msg.
        clean = _make_result(rows=[_make_spread(base="CLEAN", settle_mismatch=False)])
        bad = _make_result(rows=[_make_spread(base="RISKY", settle_mismatch=True)])
        clean_body = format_digest(clean)[0].split("net_edge = funding")[0]
        bad_body = format_digest(bad)[0].split("net_edge = funding")[0]
        assert "bybitS ⚠️" not in clean_body
        assert "bybitS ⚠️" in bad_body

    def test_title_prepended(self):
        result = _make_result()
        msgs = format_digest(result, title="🤖 Daily Report")
        assert msgs[0].startswith("🤖 Daily Report")

    def test_top_n_truncation(self):
        rows = [
            _make_spread(base=f"COIN{i}", net_edge_pct=0.01 * (10 - i))
            for i in range(20)
        ]
        result = _make_result(rows=rows)
        msgs = format_digest(result, top_n=5)
        body = "\n".join(msgs)
        assert "COIN0" in body  # highest edge
        assert "COIN4" in body  # 5th
        assert "COIN5" not in body  # truncated

    def test_sorting_by_net_edge_desc(self):
        rows = [
            _make_spread(base="LOW", net_edge_pct=0.001),
            _make_spread(base="HIGH", net_edge_pct=0.099),
            _make_spread(base="MID", net_edge_pct=0.050),
        ]
        result = _make_result(rows=rows)
        body = format_digest(result, top_n=3)[0]
        # HIGH must appear before MID before LOW in the rendered body
        assert body.index("HIGH") < body.index("MID") < body.index("LOW")

    def test_chunking_under_limit(self):
        """Each emitted message must respect Telegram's 4096-char limit."""
        # Generate a big digest: 50 verbose rows
        rows = [
            _make_spread(
                base=f"TOKEN{i:03d}",
                long_venue="binance",
                short_venue="okx",
                net_edge_pct=0.05 - i * 0.0001,
            )
            for i in range(50)
        ]
        result = _make_result(rows=rows)
        msgs = format_digest(result, top_n=50)
        assert len(msgs) > 1, "expected chunking for 50 rows"
        for m in msgs:
            assert len(m) <= TG_MESSAGE_LIMIT, (
                f"message of {len(m)} chars exceeds Telegram 4096 limit"
            )

    def test_html_entities_in_base(self):
        """Asset symbols with < or & must be escaped (would break parse_mode=HTML)."""
        result = _make_result(rows=[_make_spread(base="<evil>&co")])
        body = format_digest(result)[0]
        assert "<evil>" not in body  # raw < would break Telegram parser
        assert "&lt;evil&gt;" in body
        assert "&amp;co" in body

    def test_missing_rate_field_tolerant(self):
        """A row missing rate fields should not crash formatting."""
        row = _make_spread()
        for k in ("long_rate_pct", "short_rate_pct", "net_edge_pct"):
            row.pop(k, None)
        result = _make_result(rows=[row])
        body = format_digest(result)[0]
        assert "n/a" in body


# ---------------------------------------------------------------------------
# _resolve_venues
# ---------------------------------------------------------------------------


class TestResolveVenues:
    def test_explicit_arg_wins(self):
        v = _resolve_venues("binance,bybit", include_dex=True)
        assert v == ["binance", "bybit"]

    def test_default_cex_plus_hl(self):
        v = _resolve_venues(None, include_dex=False)
        assert "binance" in v and "hyperliquid" in v
        assert "aster" not in v

    def test_include_dex_full_set(self):
        v = _resolve_venues(None, include_dex=True)
        for dex in ("hyperliquid", "aster", "lighter", "edgex", "dydx"):
            assert dex in v

    def test_whitespace_tolerant(self):
        v = _resolve_venues(" binance , bybit ,OKX ", include_dex=False)
        assert v == ["binance", "bybit", "okx"]


# ---------------------------------------------------------------------------
# _load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "@channel")
        cfg = _load_config()
        assert cfg == {
            "telegram_bot_token": "tok-123",
            "telegram_chat_id": "@channel",
        }

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "  tok  ")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "\t@ch\n")
        cfg = _load_config()
        assert cfg["telegram_bot_token"] == "tok"
        assert cfg["telegram_chat_id"] == "@ch"

    def test_missing_returns_empty(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        cfg = _load_config()
        assert cfg == {"telegram_bot_token": "", "telegram_chat_id": ""}


# ---------------------------------------------------------------------------
# broadcast (dry-run path only — no network)
# ---------------------------------------------------------------------------


class TestBroadcastDryRun:
    def test_dry_run_does_not_call_network(self):
        msgs = ["hello", "world"]
        sent, failed = broadcast(msgs, config={}, dry_run=True)
        assert sent == 2
        assert failed == 0

    def test_dry_run_returns_zero_for_empty(self):
        sent, failed = broadcast([], config={}, dry_run=True)
        assert (sent, failed) == (0, 0)


# ---------------------------------------------------------------------------
# send_telegram_message (mocked HTTP)
# ---------------------------------------------------------------------------


class TestSendTelegramMessage:
    def _mock_response(self, status=200):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = status
        return mock_resp

    def test_success_returns_true(self):
        cfg = {"telegram_bot_token": "tok", "telegram_chat_id": "@ch"}
        with patch("notify.telegram_push.urllib.request.urlopen") as m:
            m.return_value = self._mock_response(200)
            assert send_telegram_message("hi", cfg) is True
            m.assert_called_once()

    def test_http_400_raises_runtime_error(self):
        import urllib.error

        cfg = {"telegram_bot_token": "tok", "telegram_chat_id": "@ch"}
        err = urllib.error.HTTPError(
            url="x",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=None,
        )
        with patch("notify.telegram_push.urllib.request.urlopen", side_effect=err):
            try:
                send_telegram_message("hi", cfg)
                assert False, "expected RuntimeError"
            except RuntimeError as e:
                assert "Telegram HTTP 400" in str(e)


# ---------------------------------------------------------------------------
# Integration: broadcast + format_digest together
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_broadcast_consumes_format_digest_output(self):
        result = _make_result()
        msgs = format_digest(result, top_n=10)
        sent, failed = broadcast(msgs, config={}, dry_run=True)
        assert sent == len(msgs)
        assert failed == 0
