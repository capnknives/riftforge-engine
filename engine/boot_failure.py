"""boot_failure.py -- record fatal boot errors for the watcher.

When ``Game()`` or early ``main()`` dies before the tick loop runs, the
game child writes ``.game_boot_error.json`` so ``engine.crash_recovery``
can distinguish SQLite corruption from ordinary code crashes. The watcher
reads and clears the file on each game-child exit.
"""

from __future__ import annotations

import json
import os
import time

BOOT_ERROR_FILENAME = ".game_boot_error.json"

# Substrings that mean ``git reset`` / code revert will not help.
_DB_CORRUPT_MARKERS = (
    "database disk image is malformed",
    "file is not a database",
    "database corruption",
    "malformed database schema",
    "btree",
)


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _path(root=None):
    return os.path.join(root or _repo_root(), BOOT_ERROR_FILENAME)


def _exc_message(exc):
    parts = [str(exc).strip()]
    for attr in ("message", "detail"):
        val = getattr(exc, attr, None)
        if val and str(val).strip() not in parts:
            parts.append(str(val).strip())
    return " | ".join(p for p in parts if p)


def is_db_corruption_message(text):
    """True when ``text`` looks like SQLite on-disk corruption."""
    lower = (text or "").lower()
    return any(marker in lower for marker in _DB_CORRUPT_MARKERS)


def is_db_corruption_error(exc):
    """True when ``exc`` is (or wraps) a corrupt-database boot failure."""
    import sqlite3

    if isinstance(exc, sqlite3.DatabaseError):
        return is_db_corruption_message(_exc_message(exc))
    # WAL/bind-mount corruption sometimes surfaces as generic Error/OSError.
    return is_db_corruption_message(_exc_message(exc))


def record_boot_failure(exc, *, root=None, phase="boot"):
    """Write a boot-failure stamp before the game child exits."""
    message = _exc_message(exc)
    payload = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": (phase or "boot").strip() or "boot",
        "exc_type": type(exc).__name__,
        "message": message[:800],
        "db_corrupt": bool(is_db_corruption_error(exc)),
    }
    path = _path(root)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        pass
    return payload


def take_boot_failure(*, root=None):
    """Read and remove the boot-failure stamp (if any)."""
    path = _path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    try:
        os.remove(path)
    except OSError:
        pass
    return data if isinstance(data, dict) else None
