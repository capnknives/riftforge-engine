"""crash_recovery.py -- crash budget, auto-revert, and recovery hold.

The watcher calls into this module when the game child exits or is hang-killed.
If failures exceed the budget within a window, the bind-mount resets to the
last stable SHA from ``.boot_stable.json`` and auto-deploy is paused until
staff clears the hold.

Env (optional):

- ``RIFTFORGE_CRASH_WINDOW`` -- seconds (default ``300``)
- ``RIFTFORGE_CRASH_MAX_EXITS`` -- non-planned exits in window (default ``5``)
- ``RIFTFORGE_CRASH_REVERT_BACKOFF`` -- base spawn backoff seconds (default ``30``)
- ``RIFTFORGE_DB_CORRUPT_MAX_EXITS`` -- corrupt DB boot exits before hold (default ``3``)
- ``RIFTFORGE_BOOT_FAILURE_MAX_EXITS`` -- identical boot-error exits before hold (default ``3``)
- ``RIFTFORGE_RELOAD_STORM_MAX_EXITS`` -- game respawns without stable boot stamp before reload-storm revert (default ``5``)
- ``RIFTFORGE_AUTO_DB_RECOVER`` -- staging helper: restore latest backup on hold (``1``)
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from engine import boot_stability
from engine import boot_failure


STATE_FILENAME = ".crash_recovery_state.json"
HOLD_FILENAME = ".crash_revert_hold"
# Records the HEAD SHA at the moment a revert hold was set, so
# ``maybe_auto_resume_hold`` can tell "a fix landed on origin/main since
# then" from "nothing has changed" without staff needing to remember a
# manual `gm recover clearhold` every single time.
HOLD_META_FILENAME = ".crash_revert_hold_meta.json"
DB_HOLD_FILENAME = ".db_corruption_hold"
BOOT_HOLD_FILENAME = ".boot_failure_hold"
PLANNED_RESTART_FILENAME = ".planned_restart"
GATEWAY_OUTAGE_FILENAME = ".gateway_outage.json"
# Written as soon as gateway IPC drops; watcher uses this for fast desync
# recovery (game ticking but IPC dead) — separate from the 300s revert file.
GATEWAY_IPC_DOWN_FILENAME = ".gateway_ipc_down.json"
# Watcher counts desync kills; after N in a window, bounce gateway+game.
IPC_DESYNC_STREAK_FILENAME = ".ipc_desync_streak.json"

DEFAULT_CRASH_WINDOW = 300.0
DEFAULT_IPC_DESYNC_KILL = 30.0
DEFAULT_IPC_DESYNC_GATEWAY_RESTART = 3
DEFAULT_IPC_DESYNC_STREAK_WINDOW = 180.0
DEFAULT_MAX_EXITS = 5
DEFAULT_BACKOFF_BASE = 30.0
DEFAULT_DB_CORRUPT_MAX_EXITS = 3
DEFAULT_BOOT_FAILURE_MAX_EXITS = 3
# Planned copyover / SIGUSR1 reload exits used to bypass the crash budget
# entirely, so map-heal + auto-deploy mtime churn could spin forever without
# ever tripping ``revert_to_last_stable``.
DEFAULT_RELOAD_STORM_MAX_EXITS = 5


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _state_path(root=None):
    return os.path.join(root or _repo_root(), STATE_FILENAME)


def _hold_path(root=None):
    return os.path.join(root or _repo_root(), HOLD_FILENAME)


def _hold_meta_path(root=None):
    return os.path.join(root or _repo_root(), HOLD_META_FILENAME)


def _planned_restart_path(root=None):
    return os.path.join(root or _repo_root(), PLANNED_RESTART_FILENAME)


def _gateway_outage_path(root=None):
    return os.path.join(root or _repo_root(), GATEWAY_OUTAGE_FILENAME)


def _gateway_ipc_down_path(root=None):
    return os.path.join(root or _repo_root(), GATEWAY_IPC_DOWN_FILENAME)


def _ipc_desync_streak_path(root=None):
    return os.path.join(root or _repo_root(), IPC_DESYNC_STREAK_FILENAME)


def _db_hold_path(root=None):
    return os.path.join(root or _repo_root(), DB_HOLD_FILENAME)


def _boot_hold_path(root=None):
    return os.path.join(root or _repo_root(), BOOT_HOLD_FILENAME)


def db_corrupt_max_exits():
    raw = (os.environ.get("RIFTFORGE_DB_CORRUPT_MAX_EXITS") or "").strip()
    if not raw:
        return DEFAULT_DB_CORRUPT_MAX_EXITS
    try:
        return max(2, int(raw))
    except ValueError:
        return DEFAULT_DB_CORRUPT_MAX_EXITS


def auto_db_recover_enabled():
    raw = (os.environ.get("RIFTFORGE_AUTO_DB_RECOVER") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def boot_failure_max_exits():
    raw = (os.environ.get("RIFTFORGE_BOOT_FAILURE_MAX_EXITS") or "").strip()
    if not raw:
        return DEFAULT_BOOT_FAILURE_MAX_EXITS
    try:
        return max(2, int(raw))
    except ValueError:
        return DEFAULT_BOOT_FAILURE_MAX_EXITS


def db_hold_active(*, root=None):
    return os.path.isfile(_db_hold_path(root))


def set_db_hold(*, reason="", root=None):
    path = _db_hold_path(root)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write((reason or "database corruption suspected").strip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass


def clear_db_hold(*, root=None):
    try:
        os.remove(_db_hold_path(root))
    except OSError:
        pass


def read_db_hold_reason(*, root=None):
    path = _db_hold_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            return (handle.read() or "").strip()
    except OSError:
        return ""


def boot_hold_active(*, root=None):
    return os.path.isfile(_boot_hold_path(root))


def set_boot_hold(*, reason="", root=None):
    path = _boot_hold_path(root)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write((reason or "deterministic boot failure").strip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass


def clear_boot_hold(*, root=None):
    try:
        os.remove(_boot_hold_path(root))
    except OSError:
        pass


def read_boot_hold_reason(*, root=None):
    path = _boot_hold_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            return (handle.read() or "").strip()
    except OSError:
        return ""


def respawn_paused(*, root=None):
    """True when the watcher must not respawn the game child (DB or boot hold)."""
    root = root or _repo_root()
    return db_hold_active(root=root) or boot_hold_active(root=root)


def _recent_db_corrupt_exits(recent):
    return [row for row in recent if row.get("db_corrupt")]


def _recent_all_db_corrupt(recent):
    """True when every exit in the window is tagged corrupt-DB boot."""
    if not recent:
        return False
    return all(bool(row.get("db_corrupt")) for row in recent)


def _recent_unplanned_exits(recent):
    """Crash-budget rows only — deliberate copyover reloads are tracked separately."""
    return [row for row in recent if not row.get("planned")]


def _recent_boot_error_exits(recent):
    """Exits that carried a boot-failure stamp (not SQLite corruption)."""
    return [
        row for row in recent
        if row.get("boot_error") and not row.get("db_corrupt")
    ]


def _exit_boot_signature(row):
    """Normalize ``(exc_type, message-prefix)`` for one exit row."""
    return (
        row.get("boot_exc_type") or "",
        str(row.get("boot_error") or "")[:160],
    )


def crash_window_seconds():
    raw = (os.environ.get("RIFTFORGE_CRASH_WINDOW") or "").strip()
    if not raw:
        return DEFAULT_CRASH_WINDOW
    try:
        return max(60.0, float(raw))
    except ValueError:
        return DEFAULT_CRASH_WINDOW


def crash_max_exits():
    raw = (os.environ.get("RIFTFORGE_CRASH_MAX_EXITS") or "").strip()
    if not raw:
        return DEFAULT_MAX_EXITS
    try:
        return max(2, int(raw))
    except ValueError:
        return DEFAULT_MAX_EXITS


def reload_storm_max_exits():
    """Respawns without a fresh stable-boot stamp before auto-revert."""
    raw = (os.environ.get("RIFTFORGE_RELOAD_STORM_MAX_EXITS") or "").strip()
    if not raw:
        return DEFAULT_RELOAD_STORM_MAX_EXITS
    try:
        return max(2, int(raw))
    except ValueError:
        return DEFAULT_RELOAD_STORM_MAX_EXITS


def backoff_base_seconds():
    raw = (os.environ.get("RIFTFORGE_CRASH_REVERT_BACKOFF") or "").strip()
    if not raw:
        return DEFAULT_BACKOFF_BASE
    try:
        return max(5.0, float(raw))
    except ValueError:
        return DEFAULT_BACKOFF_BASE


def load_state(root=None):
    path = _state_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("recent_exits", [])
    data.setdefault("revert_count", 0)
    return data


def save_state(state, *, root=None):
    path = _state_path(root)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        pass


def mark_planned_restart(*, root=None):
    """Watcher / copyover sets this before a deliberate game restart."""
    path = _planned_restart_path(root)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"{time.time():.3f}\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass


def clear_planned_restart(*, root=None):
    try:
        os.remove(_planned_restart_path(root))
    except OSError:
        pass


def planned_restart_pending(*, root=None):
    return os.path.isfile(_planned_restart_path(root))


def clear_gateway_outage(*, root=None):
    try:
        os.remove(_gateway_outage_path(root))
    except OSError:
        pass


def ipc_desync_kill_seconds():
    """Watcher kills a ticking game child when IPC is down longer than this."""
    raw = (os.environ.get("RIFTFORGE_GATEWAY_IPC_DESYNC_KILL") or "").strip()
    if not raw:
        return DEFAULT_IPC_DESYNC_KILL
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_IPC_DESYNC_KILL
    # Must exceed routine game-only reload + hold grace (default 5s).
    return max(10.0, value)


def clear_gateway_ipc_down(*, root=None):
    try:
        os.remove(_gateway_ipc_down_path(root))
    except OSError:
        pass
    clear_ipc_desync_streak(root=root)


def read_gateway_ipc_down(*, root=None):
    path = _gateway_ipc_down_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def write_gateway_ipc_down(
    *, down_since_wall, planned_restart=False, root=None
):
    """Gateway writes immediately when game IPC drops (watcher fast path)."""
    payload = {
        "down_since_wall": float(down_since_wall),
        "planned_restart": bool(planned_restart),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = _gateway_ipc_down_path(root)
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


def should_kill_for_ipc_desync(
    *, game_spawn_wall, now_wall=None, root=None
):
    """True when gateway IPC is down but the game child heartbeat is fresh.

    Covers split-brain: ``server.py`` still ticks while the gateway lost the
    IPC writer — clients connect to :4000 but get a blank telnet socket.
    """
    now = time.time() if now_wall is None else now_wall
    from engine import game_heartbeat

    if game_spawn_wall is not None:
        spawn_age = now - float(game_spawn_wall)
        # After copyover / deploy sync the child may tick locally for minutes
        # before gateway IPC reconnects — do not desync-kill during boot grace.
        if spawn_age < game_heartbeat.boot_grace_seconds():
            return False, f"spawn_boot_grace_{spawn_age:.0f}s"
    data = read_gateway_ipc_down(root=root)
    if not data or not isinstance(data, dict):
        return False, "no_ipc_down_file"
    if data.get("planned_restart"):
        return False, "planned_restart"
    started = float(data.get("down_since_wall") or 0)
    if started <= 0:
        return False, "invalid_down_since"
    age = now - started
    threshold = ipc_desync_kill_seconds()
    if age < threshold:
        return False, f"ipc_down_age_{age:.0f}s"

    mtime = game_heartbeat.heartbeat_mtime()
    stamp_from_this_child = (
        mtime is not None and mtime >= (game_spawn_wall - 1.0)
    )
    if not stamp_from_this_child:
        return False, "heartbeat_not_from_this_child"
    heartbeat_age = now - mtime
    if heartbeat_age >= game_heartbeat.hang_timeout_seconds():
        return False, "heartbeat_stale_use_hang_check"
    return True, (
        f"ipc_down_{age:.0f}s>{threshold:.0f}s heartbeat_fresh_{heartbeat_age:.0f}s"
    )


def ipc_desync_gateway_restart_kills():
    """Desync game-kills in ``ipc_desync_streak_window`` before gateway bounce."""
    raw = (
        os.environ.get("RIFTFORGE_GATEWAY_IPC_DESYNC_GATEWAY_RESTART") or ""
    ).strip()
    if not raw:
        return int(DEFAULT_IPC_DESYNC_GATEWAY_RESTART)
    try:
        return max(1, int(raw))
    except ValueError:
        return int(DEFAULT_IPC_DESYNC_GATEWAY_RESTART)


def ipc_desync_streak_window_seconds():
    """Rolling window for ``record_ipc_desync_kill`` streak counting."""
    raw = (
        os.environ.get("RIFTFORGE_GATEWAY_IPC_DESYNC_STREAK_WINDOW") or ""
    ).strip()
    if not raw:
        return float(DEFAULT_IPC_DESYNC_STREAK_WINDOW)
    try:
        return max(30.0, float(raw))
    except ValueError:
        return float(DEFAULT_IPC_DESYNC_STREAK_WINDOW)


def read_ipc_desync_streak(*, root=None):
    path = _ipc_desync_streak_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {"kills": [], "gateway_restarts": 0}
    if not isinstance(data, dict):
        return {"kills": [], "gateway_restarts": 0}
    kills = data.get("kills")
    if not isinstance(kills, list):
        kills = []
    return {
        "kills": [float(x) for x in kills if x is not None],
        "gateway_restarts": int(data.get("gateway_restarts", 0) or 0),
    }


def _write_ipc_desync_streak(data, *, root=None):
    path = _ipc_desync_streak_path(root)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def record_ipc_desync_kill(*, root=None, now_wall=None):
    """Append one watcher desync-kill timestamp (rolling window)."""
    now = time.time() if now_wall is None else float(now_wall)
    window = ipc_desync_streak_window_seconds()
    data = read_ipc_desync_streak(root=root)
    kills = [t for t in data.get("kills", []) if now - t <= window]
    kills.append(now)
    data["kills"] = kills
    _write_ipc_desync_streak(data, root=root)
    return len(kills)


def record_ipc_desync_gateway_restart(*, root=None):
    """Bump gateway-bounce counter after desync escalation."""
    data = read_ipc_desync_streak(root=root)
    data["gateway_restarts"] = int(data.get("gateway_restarts", 0) or 0) + 1
    data["kills"] = []
    _write_ipc_desync_streak(data, root=root)


def clear_ipc_desync_streak(*, root=None):
    """Clear desync kill streak when game IPC is healthy again."""
    try:
        os.remove(_ipc_desync_streak_path(root))
    except OSError:
        pass


def should_restart_gateway_for_desync(*, root=None, now_wall=None):
    """True when repeated desync kills did not restore IPC — bounce gateway."""
    now = time.time() if now_wall is None else float(now_wall)
    window = ipc_desync_streak_window_seconds()
    need = ipc_desync_gateway_restart_kills()
    data = read_ipc_desync_streak(root=root)
    recent = [
        t for t in data.get("kills", [])
        if now - float(t) <= window
    ]
    if len(recent) < need:
        return False, f"desync_kills_{len(recent)}_of_{need}"
    return True, (
        f"desync_kills_{len(recent)}>={need} in {window:.0f}s "
        "-- gateway restart"
    )


def read_gateway_outage(*, root=None):
    path = _gateway_outage_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def gateway_outage_tripped(*, root=None, now=None):
    """True when gateway reports game IPC down longer than crash window."""
    data = read_gateway_outage(root=root)
    if not data or not isinstance(data, dict):
        return False
    if data.get("planned_restart"):
        return False
    started = float(data.get("down_since_wall") or 0)
    if started <= 0:
        return False
    now = time.time() if now is None else now
    return (now - started) >= crash_window_seconds()


def write_gateway_outage(*, down_since_wall, planned_restart=False, root=None):
    """Gateway calls when game IPC has been down past the revert threshold."""
    payload = {
        "down_since_wall": float(down_since_wall),
        "planned_restart": bool(planned_restart),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = _gateway_outage_path(root)
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


def hold_active(*, root=None):
    return os.path.isfile(_hold_path(root))


def _dominant_signature(recent):
    """Most common ``(exc_type, message-prefix)`` among boot-error rows.

    Returns ``None`` when no exit in ``recent`` carried a boot-failure
    signature (hang-kills, or ordinary exits with no ``.game_boot_error.json``
    to read). Used by the revert-thrash guard to recognize "the exact same
    boot failure came right back after a revert" -- a git reset cannot fix
    a bug that predates (or does not depend on) the reverted-to commit, so
    repeating it forever just burns cycles without ever recovering.
    """
    from collections import Counter

    sigs = [
        (row.get("boot_exc_type") or "", str(row.get("boot_error"))[:160])
        for row in recent
        if row.get("boot_error")
    ]
    if not sigs:
        return None
    return Counter(sigs).most_common(1)[0][0]


def read_hold_sha(*, root=None):
    """HEAD SHA recorded when the current (or most recent) hold was set."""
    path = _hold_meta_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return ""
    if isinstance(data, dict):
        return (data.get("sha") or "").strip()
    return ""


def _reload_storm_reason(reason):
    """True when a revert was triggered by copyover/reload churn, not a crash."""
    text = (reason or "").lower()
    return "reload storm" in text or "reload/copyover storm" in text


def set_revert_hold(*, reason="", root=None, hold_baseline_sha=None):
    """Pause auto-deploy after an automatic code revert.

    Also stamps a SHA into ``.crash_revert_hold_meta.json`` so
    ``maybe_auto_resume_hold`` can later tell whether a real fix has
    landed on ``origin/main`` since this hold began. Default baseline is
    post-revert HEAD; reload-storm reverts pass the failed tip so auto-
    resume does not immediately re-sync the same broken advance.
    """
    from engine import auto_deploy

    was_active = hold_active(root=root)
    path = _hold_path(root)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write((reason or "crash budget exceeded").strip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass
    auto_deploy.set_override("off", root=root, queue_catchup=False)

    sha = (hold_baseline_sha or "").strip() or boot_stability.current_head_sha(root)
    if sha:
        meta_path = _hold_meta_path(root)
        tmp = meta_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "sha": sha,
                        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    },
                    handle,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, meta_path)
        except OSError:
            pass

    if not was_active:
        try:
            from engine import ops_webhook
            ops_webhook.schedule_ops_alert(
                "crash_revert_hold",
                (reason or "crash budget exceeded").strip(),
                sha=sha or "",
            )
        except Exception as exc:
            from engine import log_util

            log_util.ops(
                "crash_recovery",
                "ops_webhook schedule failed",
                exc=exc,
            )


def clear_revert_hold(*, root=None):
    """Head GM clears hold so auto-deploy may resume."""
    try:
        os.remove(_hold_path(root))
    except OSError:
        pass
    try:
        os.remove(_hold_meta_path(root))
    except OSError:
        pass


def resume_after_crash_hold(*, root=None):
    """Clear revert / db / boot holds and queue auto-deploy catch-up.

    Deliberately does **not** forget the thrash-guard failure signature
    (``last_revert_signature``). Resuming — whether via staff
    ``gm recover clearhold`` or ``maybe_auto_resume_hold`` — only clears
    the *hold*; it is not proof the underlying bug is fixed. If the same
    failure signature comes right back after this resume, ``should_revert``
    must still recognize it as a repeat and hold again immediately instead
    of reverting a second time — that repeated "clearhold, crash again,
    clearhold again" cycle was the actual "auto recovery doesn't work"
    complaint. The signature only moves forward when a *different* revert
    actually happens (see ``revert_to_last_stable``), or ages out
    naturally once the real fix keeps the game running long enough that
    ``recent_exits`` no longer holds any matching failure.
    """
    root = root or _repo_root()
    clear_revert_hold(root=root)
    clear_db_hold(root=root)
    clear_boot_hold(root=root)
    from engine import auto_deploy

    auto_deploy.set_override("on", root=root)


def maybe_auto_resume_hold(*, root=None):
    """Auto-clear a stale revert hold once a real fix lands on origin/main.

    Without this, a revert hold (and the forced auto-deploy pause it
    implies) only ever comes back with a *manual* ``gm recover clearhold``
    -- if staff fix the underlying bug, push it, and forget that separate
    step (or a second unrelated crash re-arms the hold first), auto-deploy
    stays paused indefinitely even though a fix is sitting unused on
    ``origin/main``. That was the actual "auto recovery doesn't work"
    complaint: the safety pause was working as designed, but nothing ever
    automatically re-validated it and resumed.

    While a hold is active, fetch ``origin/main`` and compare it to the SHA
    that was checked out when the hold began (``read_hold_sha``). If a
    *newer* commit exists, treat it as a candidate fix: clear the hold and
    queue the normal catch-up sync (exactly what a manual clearhold does).
    If the new code still crashes, the crash budget / thrash guard simply
    re-arms the hold on the next failure -- no worse than a premature
    manual clearhold, but a real fix now self-heals without anyone
    remembering an extra step.

    Never touches a DB-corruption hold (needs an explicit restore, not a
    code fix) and never guesses when no baseline SHA was recorded (a hold
    file left over from before this existed) -- stay conservative and
    require a manual clearhold rather than resume against an unknown
    baseline.

    Returns ``(resumed: bool, detail: str)``.
    """
    root = root or _repo_root()
    if not hold_active(root=root):
        return False, "no hold active"
    if db_hold_active(root=root):
        return False, "db corruption hold active (needs manual restore)"
    if boot_hold_active(root=root):
        return False, "boot failure hold active (needs fix + clearhold)"
    baseline = read_hold_sha(root=root)
    if not baseline:
        return False, "no recorded hold baseline SHA (clear manually)"

    from engine import auto_deploy

    if not auto_deploy._fetch_origin(root):
        return False, "git fetch failed"
    try:
        remote_sha = auto_deploy._origin_main_sha(root)
    except subprocess.CalledProcessError:
        return False, "could not read origin/main"
    if not remote_sha or remote_sha == baseline:
        return False, "origin/main unchanged since hold began"

    print(
        f"[crash_recovery] origin/main advanced past hold baseline "
        f"{baseline[:12]} -> {remote_sha[:12]} -- auto-resuming "
        "(clearing hold, queuing catch-up sync)",
        flush=True,
    )
    resume_after_crash_hold(root=root)
    return True, f"origin advanced {baseline[:12]} -> {remote_sha[:12]}"


def read_hold_reason(*, root=None):
    path = _hold_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            return (handle.read() or "").strip()
    except OSError:
        return ""


def _prune_exits(recent, *, now, window):
    cutoff = now - window
    return [row for row in recent if float(row.get("at", 0)) >= cutoff]


def record_exit(*, returncode, hang_kill=False, root=None):
    """Append a game-child exit; return updated state dict."""
    root = root or _repo_root()
    now = time.time()
    state = load_state(root)
    planned = planned_restart_pending(root=root)
    if planned:
        clear_planned_restart(root=root)
        state["last_planned_restart_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)
        )
        # Still count deliberate reloads (copyover / announced deploy) toward
        # the reload-storm budget — they used to vanish from ``recent_exits``,
        # so auto-revert never fired during Veil/copyover cascades.
        recent = _prune_exits(
            state.get("recent_exits") or [], now=now, window=crash_window_seconds(),
        )
        row = {
            "at": now,
            "code": int(returncode) if returncode is not None else None,
            "planned": True,
        }
        boot_err = boot_failure.take_boot_failure(root=root)
        if boot_err:
            if boot_err.get("message"):
                row["boot_error"] = str(boot_err.get("message"))[:200]
            if boot_err.get("exc_type"):
                row["boot_exc_type"] = str(boot_err.get("exc_type"))[:80]
            if boot_err.get("db_corrupt"):
                row["db_corrupt"] = True
        recent.append(row)
        state["recent_exits"] = recent
        save_state(state, root=root)
        return state

    boot_err = boot_failure.take_boot_failure(root=root)
    db_corrupt = bool(boot_err and boot_err.get("db_corrupt"))
    recent = _prune_exits(state.get("recent_exits") or [], now=now, window=crash_window_seconds())
    row = {
        "at": now,
        "code": int(returncode) if returncode is not None else None,
        "hang": bool(hang_kill),
    }
    # Capture a failure signature for ANY boot error (not just DB corruption)
    # so the thrash guard in ``should_revert`` can recognize "the same crash
    # came right back after a revert" instead of reverting to the same
    # broken state over and over (see ``_dominant_signature``).
    if boot_err:
        if boot_err.get("message"):
            row["boot_error"] = str(boot_err.get("message"))[:200]
        if boot_err.get("exc_type"):
            row["boot_exc_type"] = str(boot_err.get("exc_type"))[:80]
    if db_corrupt:
        row["db_corrupt"] = True
    recent.append(row)
    state["recent_exits"] = recent
    save_state(state, root=root)
    return state


def evaluate_db_corruption(*, root=None):
    """Trip DB hold (and optional staging auto-restore) after corrupt boot exits.

    Returns ``(action, detail)`` where ``action`` is one of:
    ``none``, ``hold``, ``auto_restored``.
    """
    root = root or _repo_root()
    if db_hold_active(root=root):
        return "none", "db hold already active"

    state = load_state(root)
    now = time.time()
    recent = _prune_exits(state.get("recent_exits") or [], now=now, window=crash_window_seconds())
    db_recent = _recent_db_corrupt_exits(recent)
    if len(db_recent) < db_corrupt_max_exits():
        return "none", "below db corrupt threshold"

    detail = (db_recent[-1].get("boot_error") or "database corruption on boot").strip()
    set_db_hold(
        reason=(
            f"database corruption on boot ({len(db_recent)} exits in "
            f"{crash_window_seconds():.0f}s): {detail}"
        )[:500],
        root=root,
    )
    print(
        f"[crash_recovery] DB corruption hold - respawn paused. "
        f"Restore with gm recover restoredb [YYYY-MM-DD] or fix "
        f"riftforge.db manually. ({detail[:120]})",
        flush=True,
    )

    if auto_db_recover_enabled():
        from engine import world_backup

        ok, restore_detail = world_backup.try_auto_restore_live_db(
            root=root,
            triggered_by="auto_db_recover",
        )
        if ok:
            clear_db_hold(root=root)
            state = load_state(root)
            state["recent_exits"] = []
            state["last_db_restore"] = {
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "detail": restore_detail,
                "auto": True,
            }
            save_state(state, root=root)
            print(
                f"[crash_recovery] auto DB restore OK: {restore_detail}",
                flush=True,
            )
            return "auto_restored", restore_detail
        print(
            f"[crash_recovery] auto DB restore failed: {restore_detail}",
            flush=True,
        )

    return "hold", detail


def evaluate_boot_failure(*, root=None):
    """Trip boot hold (and pause respawn) after repeated identical boot errors.

    Returns ``(action, detail)`` where ``action`` is ``none`` or ``hold``.
    Code revert cannot fix import-time validation / content-schema crashes;
    this mirrors ``evaluate_db_corruption`` but for tagged boot failures.
    """
    root = root or _repo_root()
    if boot_hold_active(root=root):
        return "none", "boot hold already active"

    state = load_state(root)
    now = time.time()
    recent = _prune_exits(
        state.get("recent_exits") or [], now=now, window=crash_window_seconds(),
    )
    boot_recent = _recent_boot_error_exits(recent)
    needed = boot_failure_max_exits()
    if len(boot_recent) < needed:
        return "none", "below boot failure threshold"

    signature = _dominant_signature(boot_recent)
    if signature is None:
        return "none", "no boot failure signature"

    matching = [
        row for row in boot_recent
        if _exit_boot_signature(row) == signature
    ]
    if len(matching) < needed:
        return "none", "mixed boot failures"

    detail = (matching[-1].get("boot_error") or "boot failure").strip()
    exc_type = (matching[-1].get("boot_exc_type") or "Error").strip()
    set_boot_hold(
        reason=(
            f"deterministic boot failure ({needed} identical exits in "
            f"{crash_window_seconds():.0f}s): {exc_type}: {detail}"
        )[:500],
        root=root,
    )
    print(
        f"[crash_recovery] BOOT HOLD - respawn paused. "
        f"Fix the boot error ({exc_type}: {detail[:120]}), then "
        f"gm recover clearhold (or fix files on bind-mount and clearhold).",
        flush=True,
    )
    return "hold", detail


def should_revert(*, root=None, now=None):
    """Return ``(trip: bool, reason: str)`` for crash budget / gateway outage."""
    root = root or _repo_root()
    now = time.time() if now is None else now
    # Hold means staff / a prior failed revert paused auto-deploy. Do not
    # keep re-entering revert every watcher tick (gateway outage file left
    # behind used to spin forever).
    if hold_active(root=root):
        return False, "revert hold active"
    if db_hold_active(root=root):
        return False, "db corruption hold active"
    if boot_hold_active(root=root):
        return False, "boot failure hold active"
    stable = boot_stability.load_stable(root)
    if not stable or not (stable.get("sha") or "").strip():
        return False, "no stable boot stamp yet"

    if gateway_outage_tripped(root=root, now=now):
        return True, "gateway game IPC down past crash window"

    state = load_state(root)
    recent = _prune_exits(state.get("recent_exits") or [], now=now, window=crash_window_seconds())
    unplanned = _recent_unplanned_exits(recent)
    # Code revert cannot fix a corrupt SQLite file — skip when every recent
    # exit was a corrupt-DB boot failure.
    if _recent_all_db_corrupt(unplanned):
        return False, "db corruption pattern (code revert skipped)"

    if len(unplanned) >= crash_max_exits():
        # Thrash guard: if the LAST revert already tried to fix this exact
        # failure and it came right back, a second git reset cannot help —
        # this is a runtime-state-dependent / content bug, not a code
        # regression the reverted-to commit introduced (2026-08-04
        # postmortem: 52 reverts against the same hillside_sanctuary boot
        # crash, none of which could possibly have fixed it). Hold directly
        # with a loud, specific reason instead of reverting again.
        signature = _dominant_signature(unplanned)
        last_signature = state.get("last_revert_signature")
        if (
            signature is not None
            and last_signature is not None
            and list(signature) == list(last_signature)
        ):
            set_revert_hold(
                reason=(
                    f"repeated boot failure after revert ({signature[0]}: "
                    f"{signature[1]}) -- this looks like a data/boot-order "
                    "bug a code revert cannot fix; needs a real fix + "
                    "gm recover clearhold (or origin/main advance for "
                    "auto-resume)"
                )[:500],
                root=root,
            )
            print(
                f"[crash_recovery] THRASH GUARD: identical boot failure "
                f"after a revert ({signature[0]}) -- holding without "
                "further reverts.",
                flush=True,
            )
            return False, "repeated failure signature after revert (thrash guard)"
        return True, f"{len(unplanned)} exits in {crash_window_seconds():.0f}s"

    # Continuous failure: no stable stamp refresh within window while failing.
    stable_at = stable.get("at") or ""
    try:
        stable_ts = time.mktime(time.strptime(stable_at, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError, OverflowError):
        stable_ts = 0
    if unplanned and stable_ts and (now - stable_ts) >= crash_window_seconds():
        first_fail = min(float(row.get("at", now)) for row in unplanned)
        if first_fail > stable_ts and (now - first_fail) >= crash_window_seconds():
            return True, "continuous failure past crash window"

    return False, "ok"


def should_revert_reload_storm(
    *, spawn_failures, game_spawn_wall, root=None, now=None,
):
    """Return ``(trip, reason)`` when reloads outrun stable-boot stamps.

    Copyover / planned-restart exits do not always carry boot-failure
    stamps, but the watcher still respawns the game child. When that
    happens ``reload_storm_max_exits()`` times without
    ``.boot_stable.json`` refreshing for the current spawn, treat it like
    a crash loop and allow ``revert_to_last_stable``.
    """
    root = root or _repo_root()
    now = time.time() if now is None else now
    if hold_active(root=root):
        return False, "revert hold active"
    if db_hold_active(root=root):
        return False, "db corruption hold active"
    if boot_hold_active(root=root):
        return False, "boot failure hold active"
    stable = boot_stability.load_stable(root)
    if not stable or not (stable.get("sha") or "").strip():
        return False, "no stable boot stamp yet"

    needed = reload_storm_max_exits()
    if int(spawn_failures or 0) < needed:
        return False, "below reload storm threshold"

    try:
        stable_mtime = os.path.getmtime(boot_stability.stable_path(root))
    except OSError:
        stable_mtime = 0.0
    # Same freshness gate as watch_and_run uses to clear spawn_failures.
    if stable_mtime >= (float(game_spawn_wall) - 1.0):
        return False, "stable boot fresh for this spawn"

    return True, (
        f"{spawn_failures} game respawns without stable boot stamp "
        f"(reload/copyover storm)"
    )


def revert_to_last_stable(*, root=None, reason=""):
    """Reset working tree to last stable SHA; pause auto-deploy."""
    root = root or _repo_root()
    failed_head = boot_stability.current_head_sha(root)
    stable = boot_stability.load_stable(root)
    if not stable:
        return False, "no stable boot stamp"
    sha = (stable.get("sha") or "").strip()
    if not sha:
        return False, "stable stamp missing sha"

    head = boot_stability.current_head_sha(root)
    if head and head == sha:
        # Reverting to the same commit cannot fix a boot-only crash loop
        # (2026-07-30: stable SHA was the broken #1224 ship).
        alt = boot_stability.previous_stable_sha(root=root, skip_sha=sha)
        if alt:
            print(
                f"[crash_recovery] stable {sha[:12]} matches HEAD; "
                f"trying previous stable {alt[:12]}",
                flush=True,
            )
            sha = alt
        else:
            from engine import auto_deploy

            try:
                origin = auto_deploy._origin_main_sha(root)
            except Exception:
                origin = ""
            origin = (origin or "").strip()
            if origin and origin != head:
                print(
                    f"[crash_recovery] stable {sha[:12]} matches HEAD; "
                    f"trying origin/main {origin[:12]}",
                    flush=True,
                )
                sha = origin
            else:
                print(
                    f"[crash_recovery] revert skipped: stable SHA matches "
                    f"HEAD ({sha[:12]}) and no older stable / origin tip",
                    flush=True,
                )
                set_revert_hold(
                    reason=reason or "crash budget exceeded (revert skipped)",
                    root=root,
                )
                state = load_state(root)
                state["last_revert"] = {
                    "sha": sha,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "reason": f"{reason or ''} (skipped same SHA)".strip(),
                    "skipped": True,
                }
                save_state(state, root=root)
                # Clear outage so the watcher does not re-trip every second
                # after a no-op skip (same death-spiral as a failed reset).
                clear_gateway_outage(root=root)
                clear_planned_restart(root=root)
                return False, "stable matches HEAD; revert skipped"

    from engine import auto_deploy

    print(
        f"[crash_recovery] reverting bind-mount to stable {sha[:12]} "
        f"({reason or 'crash budget'})",
        flush=True,
    )
    # Never let git failure kill the long-lived watcher (PID 1). A raised
    # CalledProcessError used to restart the container while
    # ``.gateway_outage.json`` stayed on disk → immediate re-revert loop.
    try:
        auto_deploy._reset_hard_to(root, sha)
    except (subprocess.CalledProcessError, OSError, FileNotFoundError) as exc:
        detail = getattr(exc, "_riftforge_git_detail", None) or str(exc)
        stderr = (getattr(exc, "stderr", None) or "").strip()
        if stderr and detail == str(exc):
            lines = [ln for ln in stderr.splitlines() if ln.strip()]
            detail = " | ".join(lines[-3:])[:400] or detail
        print(
            f"[crash_recovery] revert failed at {sha[:12]}: {detail}",
            flush=True,
        )
        set_revert_hold(
            reason=(
                f"{reason or 'crash budget'} (git reset failed: {detail})"
            )[:500],
            root=root,
        )
        state = load_state(root)
        state["last_revert"] = {
            "sha": sha,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "reason": f"{reason or ''} (failed: {detail})".strip(),
            "skipped": False,
            "failed": True,
        }
        save_state(state, root=root)
        clear_gateway_outage(root=root)
        clear_planned_restart(root=root)
        return False, f"reset failed: {detail}"

    hold_baseline = failed_head if _reload_storm_reason(reason) else None
    set_revert_hold(
        reason=reason or "crash budget exceeded",
        root=root,
        hold_baseline_sha=hold_baseline,
    )

    state = load_state(root)
    # Remember what we were trying to fix (if the boot failures carried a
    # signature) so the thrash guard in ``should_revert`` can recognize a
    # second identical failure after THIS revert as "reverting again will
    # not help" instead of resetting to the same broken state forever.
    pre_revert_recent = _prune_exits(
        state.get("recent_exits") or [], now=time.time(), window=crash_window_seconds(),
    )
    pre_revert_signature = _dominant_signature(pre_revert_recent)
    if pre_revert_signature is not None:
        state["last_revert_signature"] = list(pre_revert_signature)
    state["revert_count"] = int(state.get("revert_count") or 0) + 1
    state["last_revert"] = {
        "sha": sha,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reason": reason or "",
        "skipped": False,
    }
    state["recent_exits"] = []
    save_state(state, root=root)
    clear_gateway_outage(root=root)
    clear_planned_restart(root=root)
    return True, sha


def spawn_backoff_seconds(*, failure_count, root=None):
    """Exponential backoff when no stable stamp exists yet."""
    base = backoff_base_seconds()
    if failure_count <= 1:
        return base
    return min(120.0, base * (2 ** min(failure_count - 1, 2)))


def status_text(*, root=None):
    """Human-readable status for ``gm recover status``."""
    root = root or _repo_root()
    stable = boot_stability.load_stable(root) or {}
    state = load_state(root)
    lines = [
        "Crash recovery status:",
        f"  last stable SHA: {(stable.get('sha') or '(none)')[:12]}",
        f"  stable recorded: {stable.get('at') or '(never)'}",
        f"  revert hold: {'ON' if hold_active(root=root) else 'off'}",
        f"  db corruption hold: {'ON' if db_hold_active(root=root) else 'off'}",
        f"  boot failure hold: {'ON' if boot_hold_active(root=root) else 'off'}",
    ]
    reason = read_hold_reason(root=root)
    if reason:
        lines.append(f"  hold reason: {reason}")
    if hold_active(root=root):
        baseline = read_hold_sha(root=root)
        lines.append(
            "  hold baseline SHA: "
            + (f"{baseline[:12]} (auto-resumes past this)" if baseline else "(none -- auto-resume needs manual clearhold)")
        )
    db_reason = read_db_hold_reason(root=root)
    if db_reason:
        lines.append(f"  db hold reason: {db_reason}")
    boot_reason = read_boot_hold_reason(root=root)
    if boot_reason:
        lines.append(f"  boot hold reason: {boot_reason}")
    if state.get("last_revert_signature"):
        sig = state["last_revert_signature"]
        lines.append(
            f"  thrash guard armed for: {sig[0]}: {str(sig[1])[:120]}"
        )
    recent = state.get("recent_exits") or []
    lines.append(f"  recent exits ({len(recent)} in window): {recent[-5:]}")
    last = state.get("last_revert") or {}
    if last:
        lines.append(
            f"  last revert: {last.get('sha', '')[:12]} at {last.get('at', '')}"
        )
    outage = read_gateway_outage(root=root)
    if outage:
        lines.append(f"  gateway outage file: {outage}")
    return "\r\n".join(lines)
