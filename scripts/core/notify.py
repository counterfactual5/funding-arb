import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Module-level dedup cache
# ---------------------------------------------------------------------------
_notification_cache: dict[str, float] = {}
_cache_lock = threading.Lock()
_DEFAULT_DEDUP_SEC = 300.0  # 5 minutes
_EXPIRY_SEC = 3600.0  # stale entries cleaned after 1 hour


def _dedup_key(title: str, message: str) -> str:
    """Extract a dedup key: prefer position_id, then base+venue, else title."""
    # Try to extract position_id (pf-XXX format)
    m = re.search(r"(pf-[A-Z]+-[a-z]+-[a-z]+-\d+-[0-9a-f]+)", message)
    if m:
        return m.group(1)
    # Try to extract base+venue
    m = re.search(r"Position\s+\S+\s+(\w+).*?(\w+@\w+)", message)
    if m:
        return f"{title}:{m.group(1)}:{m.group(2)}"
    # Fall back to title
    return title


def clear_notification_cache() -> None:
    """Clear the entire notification dedup cache."""
    with _cache_lock:
        _notification_cache.clear()


def send_notification(
    title: str,
    message: str,
    config: dict | None = None,
    dedup_sec: float = _DEFAULT_DEDUP_SEC,
) -> bool:
    """Send an urgent notification to the system/user.

    By default this prints an extremely visible warning to stderr.
    If 'telegram_bot_token' and 'telegram_chat_id' are present in config,
    it will attempt to send a message via Telegram.

    ``dedup_sec`` controls the minimum interval (in seconds) between
    Telegram messages that share the same dedup key.  stderr printing is
    never throttled.
    """
    # --- Always print to stderr (unthrottled) ---
    formatted_msg = (
        f"\n{'=' * 60}\n[URGENT NOTIFY] {title}\n{'-' * 60}\n{message}\n{'=' * 60}\n"
    )
    print(formatted_msg, file=sys.stderr)

    # --- Telegram path (with dedup) ---
    if config and config.get("telegram_bot_token") and config.get("telegram_chat_id"):
        key = _dedup_key(title, message)
        now = time.monotonic()

        with _cache_lock:
            # Purge expired entries
            stale = [
                k for k, v in _notification_cache.items() if now - v >= _EXPIRY_SEC
            ]
            for k in stale:
                del _notification_cache[k]

            # Check dedup
            last_sent = _notification_cache.get(key)
            if last_sent is not None and (now - last_sent) < dedup_sec:
                print(
                    f"[Notify] Suppressed duplicate Telegram notification "
                    f"(key={key}, {dedup_sec - (now - last_sent):.0f}s remaining)",
                    file=sys.stderr,
                )
                return True  # suppressed, but not an error

        # Send outside the lock
        bot_token = config["telegram_bot_token"]
        chat_id = config["telegram_chat_id"]
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        payload = json.dumps(
            {
                "chat_id": chat_id,
                "text": f"🚨 *{title}*\n\n{message}",
                "parse_mode": "Markdown",
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                success = response.status == 200
        except Exception as e:
            print(f"[Notify] Failed to send Telegram message: {e}", file=sys.stderr)
            success = False

        # Update cache on success
        if success:
            with _cache_lock:
                _notification_cache[key] = time.monotonic()

        return success

    return True
