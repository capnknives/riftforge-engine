"""game_heartbeat.py -- liveness stamp for hung-game recovery.

Classic MUD ``autorun`` only restarts when the game *process exits*. A
full hang (deadlocked / blocked forever on the asyncio thread) leaves
the PID alive, so exit-based restart never fires.

Fix: the game writes a small stamp file on each tick (and at boot).
``engine/watch_and_run.py`` polls that stamp; if it goes stale while the
child is still running, the watcher SIGTERM/SIGKILL's the game and
respawns it (gateway keeps `:4000` clients when enabled).

Env (all optional):

- ``GAME_HANG_CHECK`` -- ``0`` disables hang kill (default: on).
- ``GAME_HANG_TIMEOUT`` -- seconds since last stamp before kill
  (default ``120``). Must exceed a worst-case ``on_tick`` / autosave.
- ``GAME_HANG_BOOT_GRACE`` -- seconds after spawn with no fresh stamp
  before treating boot as hung (default ``300``).
- ``GAME_HEARTBEAT_PATH`` -- override stamp path (default
  ``<repo>/.game_heartbeat``).
"""

from __future__ import annotations

import os
import time


# Defaults sized for live: ticks are 3s, but a heavy Cadence + SQLite
# autosave can block the single asyncio thread for many seconds.
DEFAULT_HANG_TIMEOUT = 120.0
DEFAULT_BOOT_GRACE = 300.0


def _repo_root():
    """Repo root (this file lives in ``engine/``)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def hang_check_enabled():
    """True unless ``GAME_HANG_CHECK=0`` (or empty-falsey ``off``/``false``/``no``)."""
    raw = (os.environ.get("GAME_HANG_CHECK") or "1").strip().lower()
    return raw not in ("0", "off", "false", "no")


def hang_timeout_seconds():
    """Max age of the stamp before the watcher kills a live child."""
    raw = (os.environ.get("GAME_HANG_TIMEOUT") or "").strip()
    if not raw:
        return DEFAULT_HANG_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_HANG_TIMEOUT
    # Floor at a few tick periods so a one-off slow tick never trips.
    return max(15.0, value)


def boot_grace_seconds():
    """How long after spawn we tolerate a missing/stale pre-tick stamp."""
    raw = (os.environ.get("GAME_HANG_BOOT_GRACE") or "").strip()
    if not raw:
        return DEFAULT_BOOT_GRACE
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_BOOT_GRACE
    return max(30.0, value)


def heartbeat_path():
    """Absolute path of the stamp file the game touches."""
    override = (os.environ.get("GAME_HEARTBEAT_PATH") or "").strip()
    if override:
        return override
    return os.path.join(_repo_root(), ".game_heartbeat")


def touch_heartbeat(note=""):
    """Rewrite the stamp so the watcher sees a fresh mtime.

    ``note`` is a short phase label (``boot``, ``tick``, …) written as
    the file body for humans reading the file; the watcher only cares
    about mtime. Best-effort: never raise into the tick loop.
    """
    path = heartbeat_path()
    try:
        # Ensure parent exists when GAME_HEARTBEAT_PATH points elsewhere.
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # Write then replace so readers never see a half-written file.
        # (On the same filesystem, os.replace is atomic.)
        tmp = path + ".tmp"
        body = f"{time.time():.3f} {note}\n".encode("ascii", errors="replace")
        with open(tmp, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        pass


def heartbeat_mtime():
    """Wall-clock mtime of the stamp, or ``None`` if missing/unreadable."""
    path = heartbeat_path()
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def clear_heartbeat():
    """Remove a leftover stamp (watcher calls this on each spawn)."""
    path = heartbeat_path()
    try:
        os.remove(path)
    except OSError:
        pass
    try:
        os.remove(path + ".tmp")
    except OSError:
        pass


def should_kill_for_hang(*, spawn_wall, now_wall=None):
    """Return ``(kill: bool, reason: str)`` for the watcher poll.

    ``spawn_wall`` is ``time.time()`` when the current game child was
    spawned. Heartbeats with mtime *before* that spawn are ignored (left
    over from a previous process).

    Deadline logic (avoids false kills during long ``Game()`` load):

    - Always allow at least ``boot_grace`` seconds after spawn.
    - If this child has written a stamp, also allow ``hang_timeout``
      seconds after that stamp's mtime.
    - Kill only when *now* is past the later of those two deadlines.
    """
    if not hang_check_enabled():
        return False, "disabled"
    now = time.time() if now_wall is None else now_wall
    timeout = hang_timeout_seconds()
    grace = boot_grace_seconds()
    mtime = heartbeat_mtime()

    deadline = spawn_wall + grace
    stamp_from_this_child = (
        mtime is not None and mtime >= (spawn_wall - 1.0)
    )
    if stamp_from_this_child:
        deadline = max(deadline, mtime + timeout)

    if now <= deadline:
        return False, "ok"

    if not stamp_from_this_child:
        return True, (
            f"no heartbeat within boot grace "
            f"({now - spawn_wall:.0f}s > {grace:.0f}s)"
        )
    age = now - mtime
    return True, (
        f"heartbeat stale ({age:.0f}s > {timeout:.0f}s hang timeout; "
        f"past boot grace)"
    )
