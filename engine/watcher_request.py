"""watcher_request.py -- file queue from game/gateway to watch_and_run.

Head GM recovery verbs write ``.watcher_request.json``; the watcher polls
each second and acts without dropping the gateway.

Ops:
  restart_game  -- kill + respawn (optional backup; default off)
  revert_stable -- git reset to ``.boot_stable.json`` SHA, then respawn
                   (never writes to ``backups/``)
  clear_revert_hold -- clear crash revert hold + queue auto-deploy catch-up
  restore_db      -- restore riftforge.db from backups/YYYY-MM-DD/ (game stopped)
"""

from __future__ import annotations

import json
import os
import time

REQUEST_FILENAME = ".watcher_request.json"


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def request_path(root=None):
    return os.path.join(root or _repo_root(), REQUEST_FILENAME)


def _write_request(payload, *, root=None):
    path = request_path(root)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def queue_restart_game(*, by="", backup=False, root=None):
    """Ask the watcher to respawn the game child (backup only when requested)."""
    payload = {
        "op": "restart_game",
        "backup": bool(backup),
        "by": (by or "staff").strip() or "staff",
        "at": time.time(),
    }
    if not _write_request(payload, root=root):
        return False
    print(
        f"[watcher_request] queued restart_game backup={backup} "
        f"by={payload['by']!r}",
        flush=True,
    )
    return True


def queue_revert_stable(*, by="", root=None):
    """Ask the watcher to reset code to last stable SHA, then respawn."""
    payload = {
        "op": "revert_stable",
        "by": (by or "staff").strip() or "staff",
        "at": time.time(),
    }
    if not _write_request(payload, root=root):
        return False
    print(
        f"[watcher_request] queued revert_stable by={payload['by']!r}",
        flush=True,
    )
    return True


def queue_clear_revert_hold(*, by="", root=None):
    """Ask the watcher to clear crash revert hold and resume auto-deploy."""
    payload = {
        "op": "clear_revert_hold",
        "by": (by or "staff").strip() or "staff",
        "at": time.time(),
    }
    if not _write_request(payload, root=root):
        return False
    print(
        f"[watcher_request] queued clear_revert_hold by={payload['by']!r}",
        flush=True,
    )
    return True


def queue_restore_db(*, date="", by="", root=None):
    """Ask the watcher to restore live riftforge.db from a dated backup."""
    payload = {
        "op": "restore_db",
        "date": (date or "").strip(),
        "by": (by or "staff").strip() or "staff",
        "at": time.time(),
    }
    if not _write_request(payload, root=root):
        return False
    when = payload["date"] or "latest restorable"
    print(
        f"[watcher_request] queued restore_db date={when!r} "
        f"by={payload['by']!r}",
        flush=True,
    )
    return True


def take_pending(*, root=None):
    """Read and remove a pending request, or return None."""
    path = request_path(root)
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
