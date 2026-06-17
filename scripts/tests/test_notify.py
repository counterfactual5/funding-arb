"""Tests for send_notification dedup / throttle logic."""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.notify import (
    _dedup_key,
    clear_notification_cache,
    send_notification,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config():
    """Return a fake config with telegram credentials."""
    return {
        "telegram_bot_token": "fake-token",
        "telegram_chat_id": "123456",
    }


def _mock_urlopen_success():
    """Return a context-manager mock whose .status == 200."""
    mock_resp = MagicMock()
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.status = 200
    return mock_resp


# ---------------------------------------------------------------------------
# _dedup_key tests
# ---------------------------------------------------------------------------


class TestDedupKey:
    def test_extracts_position_id(self):
        msg = "Alert for pf-BTC-long-okx-1234-deadbeef something wrong"
        assert _dedup_key("TITLE", msg) == "pf-BTC-long-okx-1234-deadbeef"

    def test_extracts_base_venue(self):
        msg = "Position BTC-PERP BTCUSDT long 1.0 entry=60000, venue=okx@OKX"
        # This regex looks for: Position <word> <base> ... <venue@venue>
        # The pattern is: Position\s+\S+\s+(\w+).*?(\w+@\w+)
        # \S+ matches "BTC-PERP", (\w+) captures "BTC", .*?(\w+@\w+) captures "okx@OKX"
        key = _dedup_key("MARGIN DISTANCE LOW", msg)
        # Adjusting expectation based on actual regex behavior:
        # "Position BTC-PERP BTCUSDT ..." - \S+ = BTC-PERP, \s+(\w+) = BTCUSDT
        # But wait, let me re-check the regex...
        # Position\s+\S+\s+(\w+).*?(\w+@\w+)
        # Position BTC-PERP BTCUSDT long... venue=okx@OKX
        # \S+ = BTC-PERP, (\w+) = BTCUSDT, (\w+@\w+) = okx@OKX
        assert key == "MARGIN DISTANCE LOW:BTCUSDT:okx@OKX"

    def test_falls_back_to_title(self):
        assert _dedup_key("SOME TITLE", "random message") == "SOME TITLE"


# ---------------------------------------------------------------------------
# send_notification dedup tests
# ---------------------------------------------------------------------------


class TestSendNotificationDedup:
    def setup_method(self):
        """Clear cache before each test."""
        clear_notification_cache()

    @patch("core.notify.urllib.request.urlopen")
    def test_first_call_sends(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_success()
        config = _make_config()

        result = send_notification("ALERT", "something bad", config=config)
        assert result is True
        mock_urlopen.assert_called_once()

    @patch("core.notify.urllib.request.urlopen")
    def test_duplicate_suppressed_within_window(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_success()
        config = _make_config()

        # First call: sends
        send_notification("ALERT", "something bad", config=config, dedup_sec=60.0)
        assert mock_urlopen.call_count == 1

        # Second call immediately: suppressed
        result = send_notification(
            "ALERT", "something bad", config=config, dedup_sec=60.0
        )
        assert result is True
        assert mock_urlopen.call_count == 1  # still 1, not called again

    @patch("core.notify.urllib.request.urlopen")
    def test_sends_after_dedup_expires(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_success()
        config = _make_config()

        # First call
        send_notification("ALERT", "something bad", config=config, dedup_sec=1.0)
        assert mock_urlopen.call_count == 1

        # Wait for dedup window to expire
        time.sleep(1.1)

        # Should send again
        result = send_notification(
            "ALERT", "something bad", config=config, dedup_sec=1.0
        )
        assert result is True
        assert mock_urlopen.call_count == 2

    @patch("core.notify.urllib.request.urlopen")
    def test_clear_cache_allows_resend(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_success()
        config = _make_config()

        # First call
        send_notification("ALERT", "something bad", config=config, dedup_sec=300.0)
        assert mock_urlopen.call_count == 1

        # Clear cache
        clear_notification_cache()

        # Should send again immediately
        send_notification("ALERT", "something bad", config=config, dedup_sec=300.0)
        assert mock_urlopen.call_count == 2

    @patch("core.notify.urllib.request.urlopen")
    def test_different_keys_dont_interfere(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_success()
        config = _make_config()

        # Send alert A
        send_notification("ALERT_A", "msg a", config=config, dedup_sec=300.0)
        assert mock_urlopen.call_count == 1

        # Send alert B (different key) — should go through
        send_notification("ALERT_B", "msg b", config=config, dedup_sec=300.0)
        assert mock_urlopen.call_count == 2

        # Alert A duplicate — suppressed
        send_notification("ALERT_A", "msg a", config=config, dedup_sec=300.0)
        assert mock_urlopen.call_count == 2

    @patch("core.notify.urllib.request.urlopen")
    def test_no_config_no_telegram_no_error(self, mock_urlopen):
        """Without telegram config, returns True and never calls urlopen."""
        result = send_notification("ALERT", "msg")
        assert result is True
        mock_urlopen.assert_not_called()

    @patch("core.notify.urllib.request.urlopen")
    def test_stderr_always_prints(self, mock_urlopen, capsys):
        """stderr output is never suppressed, even when dedup blocks telegram."""
        mock_urlopen.return_value = _mock_urlopen_success()
        config = _make_config()

        send_notification("ALERT", "msg", config=config, dedup_sec=300.0)
        send_notification("ALERT", "msg", config=config, dedup_sec=300.0)

        # stderr is captured via capsys; both calls should produce output
        captured = capsys.readouterr()
        # Count how many times [URGENT NOTIFY] appears
        assert captured.err.count("[URGENT NOTIFY]") == 2
