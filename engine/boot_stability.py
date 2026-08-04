"""boot_stability.py -- record last SHA that reached sustained tick health.

After ``STABLE_TICKS`` consecutive ``post_tick`` heartbeats (~30s at 3s/tick)
**and** ``Game.__init__`` has finished (``seed_content`` included), writes
``.boot_stable.json`` so the watcher can revert to a known-good commit when
crash loops trip the budget.

Env (optional):

- ``RIFTFORGE_STABLE_TICKS`` -- consecutive post_tick stamps required
  (default ``10``).
"""

from __future__ import annotations

import json
import os
import subprocess
import time


STABLE_FILENAME = ".boot_stable.json"
HISTORY_FILENAME = ".boot_stable_history.json"
DEFAULT_STABLE_TICKS = 10
MAX_STABLE_HISTORY = 5

# In-process counter; resets on each game child spawn.
_consecutive_post_ticks = 0
# False until ``Game.__init__`` finishes ``seed_content`` in this process.
_boot_finished = False


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def stable_ticks_required():
    """How many consecutive post_tick stamps before we stamp stable."""
    raw = (os.environ.get("RIFTFORGE_STABLE_TICKS") or "").strip()
    if not raw:
        return DEFAULT_STABLE_TICKS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_STABLE_TICKS


def stable_path(root=None):
    """Absolute path to the stable-boot JSON stamp."""
    return os.path.join(root or _repo_root(), STABLE_FILENAME)


def stable_history_path(root=None):
    """Absolute path to the rolling stable SHA history file."""
    return os.path.join(root or _repo_root(), HISTORY_FILENAME)


def current_head_sha(root=None):
    """Return full git HEAD SHA, or empty string on failure."""
    root = root or _repo_root()
    try:
        proc = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def load_stable(root=None):
    """Read ``.boot_stable.json`` or return ``None``."""
    path = stable_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_stable_history(root=None):
    """Return recent stable stamps (newest first), or ``[]``."""
    path = stable_history_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict) and row.get("sha")]


def _push_stable_history(payload, *, root=None):
    """Keep the last ``MAX_STABLE_HISTORY`` distinct stable stamps."""
    if not payload or not payload.get("sha"):
        return
    root = root or _repo_root()
    sha = payload["sha"]
    history = [row for row in load_stable_history(root) if row.get("sha") != sha]
    history.insert(0, dict(payload))
    history = history[:MAX_STABLE_HISTORY]
    path = stable_history_path(root)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(history, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        pass


def previous_stable_sha(*, root=None, skip_sha=None):
    """Return the next-older stable SHA from history, or ``None``."""
    skip = (skip_sha or "").strip()
    for row in load_stable_history(root):
        sha = (row.get("sha") or "").strip()
        if sha and sha != skip:
            return sha
    return None


def write_stable(*, root=None, ticks=None):
    """Persist the current HEAD as last stable boot."""
    root = root or _repo_root()
    sha = current_head_sha(root)
    if not sha:
        return None
    payload = {
        "sha": sha,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ticks": int(ticks or stable_ticks_required()),
    }
    path = stable_path(root)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        return None
    _push_stable_history(payload, root=root)
    print(
        f"[boot_stability] stable boot recorded at {sha[:12]} "
        f"({payload['ticks']} ticks)",
        flush=True,
    )
    return payload


def mark_boot_finished():
    """Call at end of ``Game.__init__`` after boot seed / heal completes."""
    global _boot_finished
    _boot_finished = True


def boot_finished():
    """True once this game child finished ``Game.__init__``."""
    return _boot_finished


def reset_post_tick_counter():
    """Clear tick counter and boot gate (new game child process)."""
    global _consecutive_post_ticks, _boot_finished
    _consecutive_post_ticks = 0
    _boot_finished = False


def note_post_tick(*, root=None):
    """Call after each successful ``post_tick`` heartbeat.

    Ignores ticks until ``mark_boot_finished`` so a latent boot-only bug
    cannot become the revert target after a few lucky heartbeats mid-init.

    Returns the stable payload when the gate fires this tick, else ``None``.
    """
    global _consecutive_post_ticks
    if not _boot_finished:
        return None
    _consecutive_post_ticks += 1
    required = stable_ticks_required()
    # Stamp once when the gate is met -- not every tick afterward.
    if _consecutive_post_ticks != required:
        return None
    return write_stable(root=root, ticks=_consecutive_post_ticks)
