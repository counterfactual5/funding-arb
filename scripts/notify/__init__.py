"""Notification & push subsystem (Telegram channel broadcast, etc.).

This package is intentionally lightweight — it reuses the existing
:mod:`scripts.core.notify` for alert-style messages (with dedup), and adds
batch / scheduled push helpers such as the Telegram channel broadcaster.
"""
