"""
build_lock.py -- GM build lock for live world editing.

While active, auto-deploy must not run ``git reset --hard`` / catch-up tree
syncs that would clobber live map/zone work. Narrow ``Fix bug #N`` file
overlays still ship. The watcher reads ``.build_lock`` each poll (same
pattern as ``.auto_deploy_override``).
"""

from __future__ import annotations

import json
import os
import time

LOCK_NAME = ".build_lock"

_OVERRIDE_ON = "on"
_OVERRIDE_OFF = "off"


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lock_path(root=None):
    """Absolute path to the GM build-lock sidecar file."""
    return os.path.join(root or _repo_root(), LOCK_NAME)


def read_lock(root=None):
    """Return lock metadata dict, or None when unlocked / unreadable."""
    path = lock_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("active") not in (True, _OVERRIDE_ON, "1", 1):
        return None
    return data


def is_active(root=None):
    """True when GM build lock is on."""
    return read_lock(root) is not None


def set_lock(value, *, actor="", root=None):
    """Write lock on/off. Returns the lock file path.

    ``on`` stores who engaged and when (UTC). ``off`` removes the file.
    """
    normalized = (value or "").strip().lower()
    if normalized in ("1", "true", "yes"):
        normalized = _OVERRIDE_ON
    if normalized in ("0", "false", "no"):
        normalized = _OVERRIDE_OFF
    if normalized not in (_OVERRIDE_ON, _OVERRIDE_OFF):
        raise ValueError(f"build lock must be on or off, got {value!r}")
    path = lock_path(root)
    if normalized == _OVERRIDE_OFF:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return path
    payload = {
        "active": True,
        "actor": (actor or "").strip() or "(unknown)",
        "engaged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    return path


def status_lines(root=None):
    """Short status lines for GM ``buildlock`` / ``autodeploy``."""
    data = read_lock(root)
    if data is None:
        return ["Build lock: off"]
    actor = data.get("actor") or "(unknown)"
    engaged = data.get("engaged_at") or "(unknown time)"
    return [
        "Build lock: on",
        f"  engaged by: {actor}",
        f"  engaged at: {engaged}",
        "  tree syncs (reset --hard / catch-up) are deferred",
        "  Fix bug #N overlays still ship",
    ]
