#!/usr/bin/env python3
"""Cross-platform advisory exclusive file locking.

Unix uses ``fcntl.flock``; Windows uses ``msvcrt.locking``. Both back-ends
accept either an integer file descriptor (from ``os.open``) or a file object
(anything exposing ``.fileno()``), so existing call sites need no changes
beyond swapping the lock/unlock calls.

Usage:
    fd = open(lock_path, "w")
    lock_exclusive(fd)            # blocking
    try:
        ...
    finally:
        unlock(fd)
        fd.close()

    # non-blocking (returns False instead of blocking when held elsewhere):
    if lock_exclusive(fd, blocking=False):
        ...
"""

from __future__ import annotations

import sys
import time
from typing import Any

_IS_WINDOWS = sys.platform == "win32"


def _fileno(fd: Any) -> int:
    return fd if isinstance(fd, int) else fd.fileno()


if _IS_WINDOWS:  # pragma: no cover - exercised only on Windows
    import msvcrt

    def lock_exclusive(fd: Any, *, blocking: bool = True) -> bool:
        """Acquire an exclusive lock on the first byte of ``fd``.

        Returns True on success. When ``blocking`` is False and the lock is
        held elsewhere, returns False without raising.
        """
        h = _fileno(fd)
        # msvcrt has no infinite-blocking mode (LK_LOCK gives up after ~10s),
        # so emulate blocking by polling a non-blocking lock.
        while True:
            try:
                msvcrt.locking(h, msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if not blocking:
                    return False
                time.sleep(0.1)

    def unlock(fd: Any) -> None:
        try:
            msvcrt.locking(_fileno(fd), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def lock_exclusive(fd: Any, *, blocking: bool = True) -> bool:
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
            return True
        except (BlockingIOError, OSError):
            if blocking:
                raise
            return False

    def unlock(fd: Any) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
