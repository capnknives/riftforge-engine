"""
watch_and_run.py -- tiny stdlib-only auto-reload wrapper, Docker's entry point.

Problem this solves: docker-compose.yml volume-mounts the project source
into the container (`.:/app`), so a code edit on the host is visible inside
the container INSTANTLY -- but `server.py` is a long-running asyncio process
that already loaded its Python modules into memory. Editing the file on
disk doesn't make the running interpreter re-read it; only starting a NEW
`python server.py` process does. Without this wrapper, the container can
run happily for days on stale code with no visible sign anything is wrong.

What this does:

  - With RIFTFORGE_GATEWAY=1 (Docker default): start `engine.gateway` once
    (owns public :4000), then run `server.py` as a child with IPC to the
    gateway. On .py / content change, SIGUSR1 the game so it announces
    (Veil / hold on) then exits; this wrapper respawns it — clients stay
    on the gateway and get the settle line on reattach. SIGUSR1 waits for
    ``.copyover_ready`` (veil open) so a merge storm cannot terminate a
    child still inside ``Game()``. At most one copyover is queued. Crashes
    still hard-respawn (no time to announce). GM ``autodeploy off`` also
    suppresses auto-resume / announced-deploy SIGUSR1 (live 2026-09-11
    loop: override off did not stop origin-advance copyover). After N
    consecutive exits with no ``.copyover_ready`` (default 3,
    ``GAME_CRASH_LOOP_LIMIT``), copyover signaling stays off until a spawn
    stamps ready -- the game child still respawns; the gateway stays up.

  - Hung game (PID alive, asyncio thread stuck): ``server.py`` touches
    ``.game_heartbeat`` each tick; if that stamp goes stale the watcher
    SIGTERM/SIGKILL's the child and respawns (same as a crash). Disable
    with ``GAME_HANG_CHECK=0``; tune via ``GAME_HANG_TIMEOUT`` /
    ``GAME_HANG_BOOT_GRACE`` (see ``engine/game_heartbeat.py``).

  - Exception: edits under ``engine/gateway*.py`` (or
    ``engine/gateway_protocol.py``), or to this watcher module itself,
    require killing the gateway child — the long-lived holder does not
    re-import. Those changes restart gateway + game (clients drop).
    Without that, peer-IP forwarding and similar gateway fixes stay on
    disk while the process still runs old code.

  - ``engine/auto_deploy.py`` (and ``tools/apply_pr_fix.py``) are
    ``importlib.reload``'d on every deploy poll for the same reason: a
    one-shot import at watcher boot left live map protect on disk while
    ``reset --hard`` still wiped Studio / dig JSON until the container
    restarted.

  - With RIFTFORGE_GATEWAY=0: legacy path — run `server.py` alone; on
    code change send SIGUSR1 for in-process copyover (client fds survive
    on Linux; listening socket does not).

Auto-deploy (engine/auto_deploy.py): on a slower timer (default every 30s),
git-fetch origin/main and, when it advanced since the last successful deploy,
run the full in-game countdown + file overlay pipeline so squash-merged bug-
fix PRs reach the live bind-mounted game without a manual host script.

Docker entrypoint note: this process is often PID 1. Each tick calls
``_reap_orphans`` so exited ``git fetch`` helpers do not pile up as zombies
and exhaust the container PID cgroup (see docs/LIVE_DEPLOY.md).

Not meant for a real production deployment (polling is a blunt instrument)
-- this is a local, single-developer convenience.
"""

import fnmatch
import importlib
import os
import signal
import subprocess
import sys
import time

# ``python engine/watch_and_run.py`` puts ``engine/`` on sys.path[0], not
# the repo root -- so ``import engine`` fails unless we put the repo root
# first. (``python server.py`` from /app is fine; this only bites the
# watcher entrypoint.)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from engine import game_heartbeat
from engine import boot_stability
from engine import crash_recovery
from engine import world_backup
from engine import watcher_request

# Default 5s -- 1s recursive glob+stat on Windows Docker bind-mounts pegs
# a core (tools/ alone is ~1500 .py files). Copyover already waits
# COPYOVER_SETTLE_SECONDS (default 30). Override with
# RIFTFORGE_WATCH_POLL_SECONDS=1 for the old cadence.
_POLL_SECONDS_DEFAULT = 5.0

# Populated on every ``_snapshot()`` call (wall/cpu ms + path counts).
_LAST_SNAPSHOT_METRICS = {}

# Directory names never descended (pruned in os.walk, not skip-after-glob).
# tools/ is smoke/dev scripts -- not imported by server.py; watching them
# was ~41% of the old **/*.py snapshot.
_SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    "tools",
    "backups",
    ".cursor",
    ".pytest_cache",
    ".ruff_cache",
    ".junie",
    ".claude",
    ".vscode",
    ".secrets",
    "node_modules",
}

# Under content/, these trees are live-edited (dig / Studio) or snapshots.
_CONTENT_SKIP_DIRS = {
    "maps",
    "zones",
    "map_backups",
    "map_archives",
}

# Relative path prefixes still skipped if a walk leaks a match.
_COPYOVER_SKIP_PREFIXES = (
    "content/maps",
    "content/zones",
    "content/map_backups",
    "content/map_archives",
    "backups",
    "tools",
)

# Individual files (beyond the prefix list above) that should never trigger
# a copyover on their own — same class as map_backups: generated artifact,
# not a reason to SIGUSR1 the game. The changelog ledger
# (``content/changelog.db``) never lived here: it is a ``.db`` file, and
# this watcher only globs ``*.py`` plus JSON under ``content/`` /
# ``supers/content/`` in the first place.
#
# Live-save JSON that game ticks mutate (Faith flock rows, …) must live
# here or they SIGUSR1 the MUD with no Ash Heads-up (bug report 1471).
_COPYOVER_SKIP_FILES = (
    "supers/content/god_follower_sheets.json",
)
_CHANGED_PATH_LOG_LIMIT = 8
_PENDING_CHANGE_PATHS_CAP = 40


def _repo_root():
    """Repo root (watch_and_run.py lives in engine/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def poll_seconds():
    """Seconds between watcher snapshots (env ``RIFTFORGE_WATCH_POLL_SECONDS``)."""
    raw = (os.environ.get("RIFTFORGE_WATCH_POLL_SECONDS") or "").strip()
    if not raw:
        return _POLL_SECONDS_DEFAULT
    try:
        return max(0.25, float(raw))
    except ValueError:
        return _POLL_SECONDS_DEFAULT


def _copyover_boot_grace_seconds():
    """Seconds after game spawn where mtime churn defers copyover (not hang-kill)."""
    raw = os.environ.get("COPYOVER_BOOT_GRACE_SECONDS", "45").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 45.0


def copyover_child_ready(
    spawn_wall,
    *,
    boot_grace=None,
    now=None,
    root=None,
):
    """True when SIGUSR1 is safe: this spawn wrote ``.copyover_ready``.

    Sending SIGUSR1 during ``Game()`` (handler not installed) is the
    default terminate signal -- that is how merge storms killed a child
    still loading SQLite. The child stamps ``.copyover_ready`` when look
    works (copyover-recover skip included). Boot grace is kept on the
    signature for callers but is not an extra wait after the stamp.
    """
    if spawn_wall is None:
        return True
    from engine import copyover as copyover_mod

    # ``boot_grace`` / ``now`` are unused: the stamp is the gate. A 45s
    # AND with grace would still delay SIGUSR1 after players can type.
    _ = (boot_grace, now)
    return copyover_mod.copyover_ready_since(spawn_wall, root=root)


# Consecutive game exits that never reached ``.copyover_ready`` before
# the watcher stops piling SIGUSR1 on a child that cannot stay playable.
# Live 2026-09-11: Game() took 4-5 minutes, crashed, watcher announced
# another copyover (files=none) in a loop. Hard-respawn still runs.
_CRASH_LOOP_LIMIT_DEFAULT = 3


def crash_loop_limit():
    """How many unready exits open the copyover circuit breaker.

    Default 3. ``GAME_CRASH_LOOP_LIMIT`` overrides (minimum 1 so ``0``
    cannot silently disable the guard). Junk values fall back to 3.
    """
    raw = (os.environ.get("GAME_CRASH_LOOP_LIMIT") or "").strip()
    if not raw:
        return _CRASH_LOOP_LIMIT_DEFAULT
    try:
        return max(1, int(raw))
    except ValueError:
        return _CRASH_LOOP_LIMIT_DEFAULT


def note_copyover_ready_reset(unready_exits, spawn_wall, *, root=None):
    """Return 0 once this spawn stamps ``.copyover_ready``, else the count.

    Uses ``copyover_ready_since`` -- do not reimplement the stamp check.
    Reset happens as soon as the child is playable so file-mtime copyover
    and announced deploys work again after a boot-crash streak.
    """
    from engine import copyover as copyover_mod

    if copyover_mod.copyover_ready_since(spawn_wall, root=root):
        return 0
    return int(unready_exits or 0)


def note_unready_exit(unready_exits, spawn_wall, *, root=None):
    """Count this child exit if it never stamped ``.copyover_ready``.

    Call **before** ``_spawn_game`` -- spawn clears the stamp, so a check
    after spawn would always look unready. A spawn that *did* stamp
    ready returns 0 (planned copyover / healthy crash-after-play).
    """
    from engine import copyover as copyover_mod

    if copyover_mod.copyover_ready_since(spawn_wall, root=root):
        return 0
    return int(unready_exits or 0) + 1


def copyover_signal_allowed(unready_exits):
    """False when the crash-loop breaker is open (do not SIGUSR1)."""
    return int(unready_exits or 0) < crash_loop_limit()


def auto_resume_suppress_reason(root=None, *, unready_exits=0):
    """Why auto-resume / origin-advance reload must not run, or None.

    Reuses ``auto_deploy.override_is_off`` (same ``read_override`` parser
    as the poll path). Cannot use ``poll_skip_reason`` here: that returns
    ``revert hold active`` first, and auto-resume is meant to run *while*
    held -- unless GM explicitly paused overlays.

    Hold-written off is **not** staff off: ``set_revert_hold`` always
    writes override off and stamps ``auto_override_off`` in hold meta.
    Suppress only when override is off **and** that flag is clear
    (staff ``autodeploy off``, including off typed *after* a hold).

    Live 2026-09-11: ``.auto_deploy_override`` was ``off`` at 17:51 UTC;
    at 17:52 the watcher still logged ``auto-resume: origin advanced``
    then ``announced deploy sync`` (``files=none``).
    ``resume_after_crash_hold`` writes override ``on``, so the gate must
    sit *before* that call.
    """
    from engine import auto_deploy as auto_deploy_mod
    from engine import crash_recovery as crash_recovery_mod

    if auto_deploy_mod.override_is_off(root):
        if not crash_recovery_mod.hold_set_override_off(root=root):
            return "autodeploy override off"
    if not copyover_signal_allowed(unready_exits):
        return (
            f"crash loop ({int(unready_exits)} exits without "
            f".copyover_ready, limit {crash_loop_limit()})"
        )
    return None


def copyover_suppress_reason(reason_label, *, unready_exits=0, root=None):
    """Why this watcher must not SIGUSR1 / queue copyover, or None.

    Crash-loop blocks every reason -- SIGUSR1 during ``Game()`` is the
    default terminate signal, which is how merge storms killed a booting
    child. GM autodeploy off blocks **only** announced-deploy (local
    file-mtime copyover must still work while overlays are paused).
    """
    label = (reason_label or "").strip()
    if not copyover_signal_allowed(unready_exits):
        return (
            f"crash loop ({int(unready_exits)} exits without "
            f".copyover_ready, limit {crash_loop_limit()})"
        )
    if label == "announced deploy sync":
        from engine import auto_deploy as auto_deploy_mod

        if auto_deploy_mod.override_is_off(root):
            return "autodeploy override off"
    return None


def _copyover_settle_seconds():
    """Quiet period after the last watched mtime change before SIGUSR1.

    Batches rapid auto-deploy ``reset --hard`` churn (several merges landing
    within a minute) into one copyover instead of a Veil storm per commit.
    Set ``COPYOVER_SETTLE_SECONDS=0`` to restore immediate reload on change.
    """
    raw = os.environ.get("COPYOVER_SETTLE_SECONDS", "30").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 30.0


def reload_auto_deploy():
    """Reload apply_pr_fix, notify, ticket map, hooks, changelog, auto_deploy.

    The watcher is long-lived (often PID 1). A one-shot ``from engine.auto_deploy
    import try_auto_deploy`` at boot keeps the pre-patch function forever after
    ``git reset --hard`` updates the files — live map protect (#367) sat on
    disk while feature syncs still wiped Studio / dig JSON.

    Reloads ``tools.apply_pr_fix`` first (protect lists), then
    ``engine.deploy_notify`` and ``engine.hooks`` (batch ``bug_ids`` /
    ``queue_catchup_resolves`` / map heal must match disk), then
    ``engine.changelog_ledger`` (mint any new ``#N``), then
    ``engine.discord_patch_notes`` (posts from the ledger's unposted rows),
    then ``engine.deploy_pr_ticket_map`` (``lookup(..., commit_sha=)`` must
    match ``auto_deploy``), then ``engine.auto_deploy``. Call on every
    deploy poll.

    Without reloading ``deploy_notify``, a tip-only ``auto_deploy`` reload
    can call ``queue_deploy(..., bug_ids=...)`` against a stale module and
    error-loop every poll (reset --hard + protect restore → copyover churn).
    Same for ``deploy_pr_ticket_map``: new auto_deploy passing
    ``commit_sha=`` against an old ``lookup()`` TypeErrors the whole poll
    and shipped tickets stay open.
    """
    import engine.auto_deploy as auto_deploy
    import engine.changelog_ledger as changelog_ledger
    import engine.deploy_notify as deploy_notify
    import engine.deploy_pr_ticket_map as deploy_pr_ticket_map
    import engine.discord_patch_notes as discord_patch_notes
    import engine.hooks as hooks
    import tools.apply_pr_fix as apply_pr_fix

    importlib.reload(apply_pr_fix)
    importlib.reload(deploy_notify)
    importlib.reload(hooks)
    importlib.reload(changelog_ledger)
    importlib.reload(discord_patch_notes)
    importlib.reload(deploy_pr_ticket_map)
    # Watcher is not the game child — bootstrap never re-registers map
    # heal after this hooks reload. Late-bind so the next reset --hard
    # actually merges content/map_backups into zone/map JSON.
    try:
        hooks.ensure_auto_deploy_map_heal(reload_impl=True)
    except Exception as exc:
        print(
            f"[watch] ensure_auto_deploy_map_heal failed: {exc!r}",
            flush=True,
        )
    mod = importlib.reload(auto_deploy)
    return mod


def _gateway_mode():
    """True when Docker/live should hold clients across game restarts."""
    # Default ON for this entrypoint (Docker CMD). Explicit 0 disables.
    raw = os.environ.get("RIFTFORGE_GATEWAY", "1").strip()
    return raw not in ("0", "false", "False", "no", "NO")


def _snap_log_enabled():
    """True when snapshot cost lines go to docker logs (default on)."""
    raw = os.environ.get("RIFTFORGE_WATCH_SNAP_LOG", "1").strip()
    return raw not in ("0", "false", "False", "no", "NO")


def _snap_warn_ms():
    """Log when snapshot wall time meets or exceeds this (milliseconds)."""
    raw = os.environ.get("RIFTFORGE_WATCH_SNAP_WARN_MS", "200").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 200.0


def _snap_log_every():
    """Log every Nth main poll tick even when wall time is below warn."""
    raw = os.environ.get("RIFTFORGE_WATCH_SNAP_EVERY", "30").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 30


def _path_git_hit(path):
    """True when a glob hit touches ``.git`` (bind-mount noise diagnostic)."""
    if path.startswith(".git") or path.startswith(".git" + os.sep):
        return True
    norm = path.replace("\\", "/")
    if norm.startswith(".git/"):
        return True
    if "/.git/" in norm:
        return True
    if "\\.git\\" in path:
        return True
    return False


def _maybe_log_snapshot_metrics(*, poll_tick, periodic_ok):
    """Print one grep-friendly line when snapshot cost warrants it."""
    if not _snap_log_enabled():
        return
    metrics = _LAST_SNAPSHOT_METRICS
    if not metrics:
        return
    wall_ms = metrics.get("wall_ms", 0.0)
    warn_ms = _snap_warn_ms()
    every = _snap_log_every()
    if wall_ms < warn_ms and not (
        periodic_ok and poll_tick > 0 and (poll_tick % every) == 0
    ):
        return
    print(
        "[watch_and_run] snapshot "
        f"wall_ms={wall_ms:.1f} "
        f"cpu_ms={metrics.get('cpu_ms', 0.0):.1f} "
        f"globbed={metrics.get('globbed', 0)} "
        f"skipped={metrics.get('skipped', 0)} "
        f"stated={metrics.get('stated', 0)} "
        f"git_hits={metrics.get('git_hits', 0)} "
        f"poll_s={poll_seconds()}",
        flush=True,
    )


def _skip_copyover_path(path):
    """True when ``path`` must not trigger copyover.

    Skips:

    - ``content/maps`` / ``content/zones`` (dig + Studio live-edit)
    - ``content/map_backups`` / ``content/map_archives`` snapshot churn from
      validated map saves (backups + daily archive nag state)
    - Repo-root ad-hoc ``_*.py`` probes (``live_ssh.run_remote_script`` uses
      ``/tmp`` instead -- dropping ``_remote_probe*.py`` in the bind-mount
      used to SIGUSR1 mid-chargen in a tight loop)
    - Agent debug NDJSON at repo root (``debug-*.log``)
    - Live-save JSON under ``supers/content/`` that ticks rewrite
      (``god_follower_sheets.json`` -- congregation mint, not a code ship)

    Paths are compared with ``os.path.normpath`` so Windows backslashes from
    ``glob`` still match the skip list.
    """
    norm = os.path.normpath(path).replace("\\", "/")
    if norm in _COPYOVER_SKIP_FILES:
        return True
    for prefix in _COPYOVER_SKIP_PREFIXES:
        if norm == prefix or norm.startswith(prefix + "/"):
            return True
    basename = os.path.basename(norm)
    # Only skip underscore scripts at repo root -- ``supers/_foo.py`` is real code.
    if "/" not in norm and basename.startswith("_") and basename.endswith(".py"):
        return True
    if fnmatch.fnmatch(basename, "debug-*.log"):
        return True
    return False


def _rel_posix(path):
    """Forward-slash relative path for skip/watch checks."""
    return os.path.normpath(path).replace("\\", "/")


def _snapshot_changed_paths(before, after):
    """Sorted relative paths whose mtime (or presence) changed between snaps."""
    changed = []
    keys = set(before) | set(after)
    for path in keys:
        if before.get(path) != after.get(path):
            changed.append(_rel_posix(path))
    changed.sort()
    return changed


def _format_changed_paths(paths, *, limit=None):
    """One grep-friendly clause for Docker logs (capped list)."""
    if limit is None:
        limit = _CHANGED_PATH_LOG_LIMIT
    paths = list(paths or ())
    if not paths:
        return "files=none"
    shown = paths[:limit]
    extra = len(paths) - len(shown)
    text = ",".join(shown)
    if extra > 0:
        text = f"{text} +{extra} more"
    return f"n={len(paths)} {text}"


def _file_is_watched(rel):
    """True when ``rel`` (from repo root) should be in the mtime snapshot."""
    norm = _rel_posix(rel)
    if _skip_copyover_path(norm):
        return False
    base = os.path.basename(norm)
    if base.endswith(".py"):
        return True
    if base.endswith(".json") and (
        norm.startswith("content/") or norm.startswith("supers/content/")
    ):
        return True
    return False


def _snapshot():
    """{path: mtime} for watched runtime files below cwd.

    Uses ``os.walk`` and prunes ``tools/``, ``.git``, ``backups/``,
    ``content/maps``, ``content/zones``, and snapshot trees *before*
    listing files -- skip-after-glob still paid the bind-mount walk.

    Watches ``*.py`` (except pruned trees / root ``_*.py`` probes) plus
    JSON under ``content/`` and ``supers/content/``. Map/zone JSON stays
    off the copyover list (dig + Studio live-edit). The changelog ledger
    (``content/changelog.db``) is a ``.db`` file, so it was never in this
    JSON glob to begin with.

    Updates ``_LAST_SNAPSHOT_METRICS`` (see ``RIFTFORGE_WATCH_SNAP_*``).
    """
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    globbed = 0
    skipped = 0
    stated = 0
    git_hits = 0
    snapshot = {}
    root = os.getcwd()
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir_posix = ""
        else:
            rel_dir_posix = _rel_posix(rel_dir)
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIR_NAMES]
        if rel_dir_posix == "content":
            dirnames[:] = [
                name for name in dirnames if name not in _CONTENT_SKIP_DIRS
            ]
        for name in filenames:
            globbed += 1
            rel = name if not rel_dir_posix else f"{rel_dir_posix}/{name}"
            rel = _rel_posix(rel)
            if _path_git_hit(rel):
                git_hits += 1
            if not _file_is_watched(rel):
                skipped += 1
                continue
            abs_path = os.path.join(dirpath, name)
            try:
                snapshot[rel] = os.path.getmtime(abs_path)
                stated += 1
            except OSError:
                # Vanishes between walk and stat (editor atomic save).
                pass
    wall_ms = (time.perf_counter() - wall_start) * 1000.0
    cpu_ms = (time.process_time() - cpu_start) * 1000.0
    _LAST_SNAPSHOT_METRICS.clear()
    _LAST_SNAPSHOT_METRICS.update(
        {
            "wall_ms": wall_ms,
            "cpu_ms": cpu_ms,
            "globbed": globbed,
            "skipped": skipped,
            "stated": stated,
            "git_hits": git_hits,
        }
    )
    return snapshot


def _reap_orphans(proc):
    """Non-blocking wait for zombie children (Docker PID-1 hygiene).

    This script is often PID 1 inside the container. Auto-deploy's
    ``git fetch`` spawns helpers (``git-remote-https``, etc.); when those
    helpers exit after being reparented here, they stay zombies until
    something calls ``wait``. Without that, the cgroup PID count climbs
    (~one zombie per 30s poll) until ``fork()`` fails with "Resource
    temporarily unavailable" and auto-deploy stalls forever.

    If we reap the tracked ``server.py`` child ourselves, stash its exit
    status on ``Popen.returncode`` so the existing crash-restart path
    (``proc.poll()``) still sees the exit -- otherwise poll would keep
    thinking the child is alive after we already collected it.

    No-op on Windows (no ``WNOHANG`` / Docker entrypoint path).
    """
    if not hasattr(os, "WNOHANG"):
        return
    while True:
        try:
            # -1 = any child; WNOHANG = don't block if none are ready.
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            # No children left at all (server not started yet, or already
            # fully reaped).
            break
        if pid == 0:
            # Children exist, but none are zombies right now.
            break
        if pid == proc.pid and proc.returncode is None:
            # We stole the exit that Popen would have collected -- mirror
            # it onto returncode so poll() returns non-None next tick.
            try:
                proc.returncode = os.waitstatus_to_exitcode(status)
            except ValueError:
                # Odd wait status (rare); still mark as exited.
                proc.returncode = -1


def _spawn_game(env=None, *, cold=False):
    """Start server.py; return ``(Popen, spawn_wall_time)``.

    Clears any leftover ``.game_heartbeat`` so a previous child's last
    stamp cannot look like this new process is already healthy.
    Inherits env so ``RIFTFORGE_GATEWAY`` reaches the child.

    ``cold=True`` is a compose / gateway bounce (full content ensures).
    Default is a game-only reload: gateway holds clients; the child skips
    historical boot heals (Evennia Server reload / Diku copyover_recover).
    Direct telnet (gateway off) is always cold — deferred seed runs in
    ``Game()`` and there is no portal to distinguish the two.
    """
    game_heartbeat.clear_heartbeat()
    from engine import boot_kind as boot_kind_mod
    from engine import boot_stability
    from engine import copyover as copyover_mod
    from engine import crash_recovery
    from engine import env_file
    import importlib

    root = _repo_root()
    crash_recovery.clear_gateway_ipc_down(root=root)
    copyover_mod.clear_copyover_ready(root=root)
    # Reload from disk so new DEFAULT_REPO_ENV_KEYS (viewport, lag knobs)
    # apply even if this watcher process imported env_file days ago.
    # The game child *also* applies in server.py main() — that is what
    # actually saves a stale watcher without a compose recreate.
    importlib.reload(env_file)
    env_file.apply_repo_env(root)
    boot_stability.reset_post_tick_counter()
    if cold or not _gateway_mode():
        kind = boot_kind_mod.COLD
    else:
        kind = boot_kind_mod.RELOAD
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    child_env[boot_kind_mod.ENV_KEY] = kind
    print(f"[watch] spawning game boot_kind={kind}", flush=True)
    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        env=child_env,
        cwd=root,
    )
    return proc, time.time()


def _spawn_gateway():
    """Start the long-lived telnet holder (public :4000 + IPC :4001)."""
    return subprocess.Popen(
        [sys.executable, "-m", "engine.gateway"],
        cwd=_repo_root(),
        env=os.environ.copy(),
    )


def _stop_game(proc):
    """Ask the game child to exit so we can respawn it (gateway keeps clients)."""
    if proc.poll() is not None:
        return
    try:
        # Prefer SIGTERM so asyncio can run finally/save on Unix.
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait(timeout=5)


def _stop_gateway(proc):
    """Stop the telnet holder (clients on :4000 drop).

    Used when gateway source itself changed -- game-only restart cannot
    reload ``engine.gateway``'s in-memory process.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait(timeout=5)


def _watcher_self_changed(before, after):
    """True when this watcher module changed on disk (needs re-exec)."""
    self_path = os.path.normpath("engine/watch_and_run.py")
    keys = set(before) | set(after)
    for path in keys:
        if os.path.normpath(path) != self_path:
            continue
        if before.get(path) != after.get(path):
            return True
    return False


def _disconnect_warn_seconds():
    """Brief pause so deploy_notify can warn before the gateway is killed."""
    raw = os.environ.get("DEPLOY_DISCONNECT_WARN_SECONDS", "5").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 5.0


def _grace_disconnect_warning(root, proc):
    """Touch markers + wait so game tick or gateway hold loop can warn players."""
    from engine import auto_deploy

    auto_deploy.touch_gateway_disconnect_imminent(root)
    if proc is None or proc.poll() is not None:
        # Game already down ([WAIT]) — only the gateway can still speak.
        time.sleep(_disconnect_warn_seconds())
        auto_deploy.clear_gateway_disconnect_imminent(root)
        return
    auto_deploy.touch_disconnect_imminent(root)
    deadline = time.time() + _disconnect_warn_seconds()
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    auto_deploy.clear_disconnect_imminent(root)
    auto_deploy.clear_gateway_disconnect_imminent(root)


def _reexec_watcher(proc, gateway_proc):
    """Replace this long-lived PID-1 process so new skip/grace logic loads."""
    print(
        "[watch] watch_and_run.py changed -- re-execing watcher",
        flush=True,
    )
    _grace_disconnect_warning(_repo_root(), proc)
    _stop_game(proc)
    if gateway_proc is not None:
        _stop_gateway(gateway_proc)
    watcher = os.path.join(_repo_root(), "engine", "watch_and_run.py")
    os.execv(sys.executable, [sys.executable, watcher])


def _gateway_paths_changed(before, after):
    """True when a gateway-process module appeared, vanished, or mtime-shifted.

    Paths are relative to the repo root (same as ``_snapshot`` keys).
    Only these files require killing the long-lived gateway child; other
    ``.py`` edits keep clients held across a game-only restart.
    """
    from engine import gateway_watch

    return gateway_watch.snapshot_touches_gateway_restart(before, after)


def _maybe_drop_clients_for_gateway_modules(
    before,
    after,
    *,
    proc,
    gateway_proc,
    use_gateway,
    reason_label,
):
    """Restart holder modules when a snapshot delta hits gateway/watcher files.

    Returns ``(proc, game_spawn_wall, gateway_proc)`` after a gateway+game
    restart, or None when this delta is a stay-connected copyover.

    Watcher self-change ``execv``s and does not return. Auto-deploy used to
    snapshot *after* ``reset --hard`` and then fire announced game-only
    copyover, so a Veil that warned "full shutdown" never dropped anyone.
    """
    if _watcher_self_changed(before, after):
        _reexec_watcher(proc, gateway_proc)
    if not (
        use_gateway
        and gateway_proc is not None
        and _gateway_paths_changed(before, after)
    ):
        return None
    from engine import auto_deploy as auto_deploy_mod

    print(
        f"[watch] {reason_label} -- "
        "restarting gateway + game (clients drop)",
        flush=True,
    )
    auto_deploy_mod.clear_announced_copyover(_repo_root())
    _grace_disconnect_warning(_repo_root(), proc)
    _stop_game(proc)
    _stop_gateway(gateway_proc)
    new_gateway = _spawn_gateway()
    time.sleep(0.4)
    new_proc, game_spawn_wall = _spawn_game(cold=True)
    return new_proc, game_spawn_wall, new_gateway


def _respawn_game(_proc, _game_spawn_wall, *, spawn_failures):
    """Apply crash budget / backoff, then spawn a fresh game child."""
    root = _repo_root()
    if not boot_stability.load_stable(root):
        delay = crash_recovery.spawn_backoff_seconds(
            failure_count=spawn_failures,
            root=root,
        )
        if delay > 0:
            print(
                f"[watch] no stable boot stamp yet -- "
                f"backing off {delay:.0f}s before respawn",
                flush=True,
            )
            time.sleep(delay)
    return _spawn_game()


def _maybe_auto_revert(*, reason_prefix="", game_spawn_wall=None):
    """Revert bind-mount when crash budget trips. Returns True if reverted.

    Never raises — git / FS failures must not kill this long-lived watcher
    (often Docker PID 1). Failed or skipped reverts set the crash hold and
    clear ``.gateway_outage.json`` so the next tick does not spin.

    ``game_spawn_wall`` lets the gateway-outage check ignore downtime the
    previous child accrued (a fresh child must not inherit a blown window).
    """
    trip, reason = crash_recovery.should_revert(game_spawn_wall=game_spawn_wall)
    if not trip:
        return False
    try:
        ok, detail = crash_recovery.revert_to_last_stable(
            reason=f"{reason_prefix}{reason}".strip(),
        )
    except Exception as exc:
        # Belt-and-suspenders: revert_to_last_stable already traps git
        # errors, but any unexpected blow-up still must not take down PID 1.
        print(f"[watch] auto-revert raised: {exc!r}", flush=True)
        try:
            crash_recovery.set_revert_hold(
                reason=f"{reason_prefix}revert raised: {exc!r}"[:500],
            )
            crash_recovery.clear_gateway_outage()
        except Exception as hold_exc:
            print(
                f"[watch] set_revert_hold failed: {hold_exc!r}",
                flush=True,
            )
        return False
    if ok:
        print(
            f"[watch] auto-reverted to stable {str(detail)[:12]}; "
            "auto-deploy held (gm recover clearhold)",
            flush=True,
        )
    else:
        print(
            f"[watch] auto-revert did not apply ({detail})",
            flush=True,
        )
    return ok


def _maybe_reload_storm_revert(*, spawn_failures, game_spawn_wall):
    """Revert when copyover / respawn churn outruns stable-boot stamps."""
    trip, reason = crash_recovery.should_revert_reload_storm(
        spawn_failures=spawn_failures,
        game_spawn_wall=game_spawn_wall,
    )
    if not trip:
        return False
    print(
        f"[watch] reload storm detected ({reason}) -- evaluating auto-revert",
        flush=True,
    )
    # Deliberately does NOT pass game_spawn_wall: the storm breaker is the
    # net for a genuinely broken tip (repeated respawns with no stable boot
    # stamp), so it keeps the pre-2026-09-11 trip conditions.
    return _maybe_auto_revert(reason_prefix=f"reload storm: {reason}; ")


def _after_game_exit(proc, *, hang_kill=False, root=None, game_spawn_wall=None):
    """Record exit, maybe trip DB/boot hold, maybe code-revert.

    ``game_spawn_wall`` is the spawn time of the child that just exited, so
    the gateway-outage check can tell its downtime apart from downtime a
    still-booting successor inherited.
    """
    root = root or _repo_root()
    crash_recovery.record_exit(
        returncode=proc.returncode,
        hang_kill=hang_kill,
        root=root,
    )
    action, _detail = crash_recovery.evaluate_db_corruption(root=root)
    if action == "auto_restored":
        return
    if crash_recovery.db_hold_active(root=root):
        return
    crash_recovery.evaluate_boot_failure(root=root)
    if crash_recovery.boot_hold_active(root=root):
        return
    _maybe_auto_revert(
        reason_prefix="exit: ", game_spawn_wall=game_spawn_wall
    )


def _signal_planned_copyover(proc):
    """SIGUSR1 for a deliberate reload; do not count as a crash."""
    crash_recovery.mark_planned_restart()
    try:
        proc.send_signal(signal.SIGUSR1)
        return True
    except (AttributeError, OSError) as exc:
        print(
            f"[watch] SIGUSR1 unavailable ({exc}); "
            "restarting server.py",
            flush=True,
        )
        return False


def _maybe_watcher_request(
    proc,
    game_spawn_wall=None,
    *,
    gateway_proc=None,
    use_gateway=False,
):
    """Head-GM recovery queue: restart and/or code revert, then respawn."""
    noop = (proc, None, False, gateway_proc)
    req = watcher_request.take_pending()
    if not req:
        return noop
    op = req.get("op")
    by = (req.get("by") or "staff").strip() or "staff"
    root = _repo_root()
    if op == "restart_game":
        if req.get("backup"):
            try:
                world_backup.run_backup(force=True, triggered_by=by)
            except Exception as exc:
                print(
                    f"[watch] backup before restart failed: {exc!r}",
                    flush=True,
                )
        crash_recovery.mark_planned_restart()
        if proc is not None and proc.poll() is None:
            print(
                f"[watch] watcher_request restart_game by {by!r} "
                f"(backup={bool(req.get('backup'))}) -- stopping game child",
                flush=True,
            )
            _stop_game(proc)
        new_proc, wall = _spawn_game()
        return new_proc, wall, True, gateway_proc
    if op == "restart_gateway":
        print(
            f"[watch] watcher_request restart_gateway by {by!r} "
            "-- stopping game + gateway (clients drop briefly)",
            flush=True,
        )
        crash_recovery.mark_planned_restart()
        if proc is not None and proc.poll() is None:
            _stop_game(proc)
            _after_game_exit(
                proc, root=root, game_spawn_wall=game_spawn_wall
            )
        new_gateway = gateway_proc
        if use_gateway:
            if gateway_proc is not None:
                _stop_gateway(gateway_proc)
            new_gateway = _spawn_gateway()
            time.sleep(0.4)
        new_proc, wall = _spawn_game(cold=True)
        return new_proc, wall, True, new_gateway
    if op == "revert_stable":
        ok, detail = crash_recovery.revert_to_last_stable(
            reason=f"gm recover revert by {by}",
        )
        if not ok:
            print(
                f"[watch] revert_stable failed ({detail!r}) "
                f"requested by {by!r}",
                flush=True,
            )
            return noop
        print(
            f"[watch] revert_stable to {detail[:12]} by {by!r} "
            "-- respawning game child",
            flush=True,
        )
        crash_recovery.mark_planned_restart()
        if proc is not None and proc.poll() is None:
            _stop_game(proc)
        new_proc, wall = _spawn_game()
        return new_proc, wall, True, gateway_proc
    if op == "clear_revert_hold":
        crash_recovery.resume_after_crash_hold(root=root)
        print(
            f"[watch] clear_revert_hold by {by!r} — "
            "auto-deploy catch-up queued",
            flush=True,
        )
        if proc is not None and proc.poll() is None:
            return proc, game_spawn_wall, True, gateway_proc
        crash_recovery.mark_planned_restart()
        new_proc, wall = _spawn_game()
        return new_proc, wall, True, gateway_proc
    if op == "restore_db":
        date = (req.get("date") or "").strip() or None
        if proc is not None and proc.poll() is None:
            print(
                f"[watch] restore_db {date or 'latest'} by {by!r} "
                "-- stopping game child",
                flush=True,
            )
            _stop_game(proc)
        ok, detail = world_backup.restore_live_db(
            date,
            root=root,
            triggered_by=by,
        )
        if not ok:
            print(
                f"[watch] restore_db failed ({detail!r}) "
                f"requested by {by!r}",
                flush=True,
            )
            return noop
        crash_recovery.clear_db_hold(root=root)
        state = crash_recovery.load_state(root=root)
        state["recent_exits"] = []
        state["last_db_restore"] = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "detail": detail,
            "by": by,
            "date": date or "(latest)",
        }
        crash_recovery.save_state(state, root=root)
        print(
            f"[watch] restore_db OK ({detail}) by {by!r} "
            "-- respawning game child",
            flush=True,
        )
        crash_recovery.mark_planned_restart()
        new_proc, wall = _spawn_game(cold=True)
        return new_proc, wall, True, gateway_proc
    return noop


def main():
    root = _repo_root()
    os.chdir(root)
    if root not in sys.path:
        sys.path.insert(0, root)

    use_gateway = _gateway_mode()
    gateway_proc = None
    if use_gateway:
        # Ensure the game child also sees gateway mode (compose may set it).
        os.environ.setdefault("RIFTFORGE_GATEWAY", "1")
        print("[watch] starting gateway (RIFTFORGE_GATEWAY=1)", flush=True)
        gateway_proc = _spawn_gateway()
        time.sleep(0.4)  # brief head-start so IPC accept is ready
        print("[watch] starting server.py behind gateway", flush=True)
    else:
        print("[watch] starting server.py (direct telnet, no gateway)", flush=True)

    proc, game_spawn_wall = _spawn_game(cold=True)
    before = _snapshot()
    pending_copyover = False
    copyover_wait_logged = False
    # Unique-order paths that woke the current settle window (for Docker
    # logs + .copyover_journal.jsonl). Cleared when SIGUSR1 actually fires.
    pending_change_paths = []
    copyover_boot_grace = _copyover_boot_grace_seconds()
    copyover_settle_seconds = _copyover_settle_seconds()
    settle_deadline = None
    spawn_failures = 0
    # Consecutive exits with no ``.copyover_ready`` (crash-loop breaker).
    unready_exits = 0
    backup_running = False
    # Log boot/db hold once when respawn pauses — not every 1s poll tick.
    hold_pause_announced = False
    poll_tick = 0

    def _count_unready_exit():
        """Count a game-child death before ``_spawn_game`` clears the stamp.

        Live 2026-09-11: SIGUSR1 during a never-ready boot was terminate,
        then auto-resume announced another copyover. After the limit we
        still respawn (availability) but stop piling copyover signals.
        Gateway stays up.
        """
        nonlocal unready_exits
        unready_exits = note_unready_exit(
            unready_exits, game_spawn_wall, root=root
        )
        if not copyover_signal_allowed(unready_exits):
            print(
                f"[watch] crash loop: {unready_exits} exits without "
                f".copyover_ready (limit {crash_loop_limit()}) -- "
                "copyover signaling and auto-resume suppressed "
                "(game still respawns; gateway stays up)",
                flush=True,
            )

    # First load (and later deploy polls reload) so auto_deploy patches that
    # arrive via reset --hard actually run inside this long-lived watcher.
    auto_deploy = reload_auto_deploy()

    # Bind-mounted checkouts are often owned by a non-root host user; mark
    # the repo safe so auto-deploy's git fetch is not rejected every poll.
    auto_deploy.ensure_git_safe_directory(root)

    deploy_every = auto_deploy.poll_interval_seconds()
    seconds_until_deploy = 0.0
    poll_s = poll_seconds()
    print(
        f"[watch] file poll every {poll_s:.1f}s "
        f"(RIFTFORGE_WATCH_POLL_SECONDS, default {_POLL_SECONDS_DEFAULT:.0f})",
        flush=True,
    )
    print(
        f"[watch] auto-deploy polling every {deploy_every}s "
        "(AUTO_DEPLOY=0 to disable; modules reload only when deploy is on)",
        flush=True,
    )
    if game_heartbeat.hang_check_enabled():
        print(
            f"[watch] hang check on "
            f"(timeout={game_heartbeat.hang_timeout_seconds():.0f}s, "
            f"boot_grace={game_heartbeat.boot_grace_seconds():.0f}s; "
            "GAME_HANG_CHECK=0 to disable)",
            flush=True,
        )
    else:
        print("[watch] hang check off (GAME_HANG_CHECK=0)", flush=True)
    print(
        f"[watch] crash-loop breaker on "
        f"(limit={crash_loop_limit()} unready exits; "
        "GAME_CRASH_LOOP_LIMIT to override)",
        flush=True,
    )
    if copyover_settle_seconds > 0:
        print(
            f"[watch] copyover settle on "
            f"({copyover_settle_seconds:.0f}s quiet after last file change; "
            "COPYOVER_SETTLE_SECONDS=0 for immediate reload)",
            flush=True,
        )

    while True:
        poll_s = poll_seconds()
        time.sleep(poll_s)
        poll_tick += 1

        proc, new_wall, restarted, gateway_proc = _maybe_watcher_request(
            proc,
            game_spawn_wall,
            gateway_proc=gateway_proc,
            use_gateway=use_gateway,
        )
        if restarted:
            if new_wall is not None:
                game_spawn_wall = new_wall
            before = _snapshot()
            _maybe_log_snapshot_metrics(poll_tick=poll_tick, periodic_ok=False)
            pending_copyover = False
            settle_deadline = None
            continue

        stable = boot_stability.load_stable()
        if stable:
            try:
                stable_mtime = os.path.getmtime(boot_stability.stable_path())
            except OSError:
                stable_mtime = 0
            if (
                game_spawn_wall is not None
                and stable_mtime >= (game_spawn_wall - 1.0)
            ):
                spawn_failures = 0
                crash_recovery.clear_gateway_outage()

        # Playable stamp is earlier than write_stable -- reset the
        # crash-loop breaker as soon as look works (2026-09-11 loop).
        was_crash_loop = not copyover_signal_allowed(unready_exits)
        unready_exits = note_copyover_ready_reset(
            unready_exits, game_spawn_wall, root=root
        )
        if was_crash_loop and copyover_signal_allowed(unready_exits):
            print(
                "[watch] crash loop cleared -- this spawn stamped "
                ".copyover_ready; copyover signaling resumed",
                flush=True,
            )

        # Also requires a stale heartbeat (2026-09-11: 284s/325s boots
        # with advancing phases must not look like a dead game).
        if crash_recovery.gateway_outage_tripped(
            game_spawn_wall=game_spawn_wall
        ):
            print(
                "[watch] gateway outage past crash window -- "
                "evaluating auto-revert",
                flush=True,
            )
            # Hold already on (prior failed/skipped revert, or the thrash
            # guard): clear the stale outage flag but do NOT `continue` --
            # the exit-check / hang-check / auto-deploy poll below must
            # still run every tick even while held, otherwise a dead game
            # child that never opens gateway IPC (a boot-crash loop) is
            # never respawned again once this file trips once. This
            # `continue` used to skip that (2026-08-04 hang postmortem):
            # the watcher stayed "alive" (still PID 1, still looping) but
            # never checked `proc.poll()` again, so a crashed child was
            # never respawned and the game looked hung from the outside.
            if crash_recovery.hold_active(root=root):
                print(
                    "[watch] revert hold active -- clearing stale "
                    "gateway outage (game respawn / auto-deploy checks "
                    "still run this tick)",
                    flush=True,
                )
                crash_recovery.clear_gateway_outage()
            else:
                # Attempt tree revert FIRST. Only stop+respawn when the reset
                # succeeds — otherwise a git failure used to leave the game
                # dead (or kill PID 1) while ``.gateway_outage.json`` survived
                # and re-tripped every restart.
                reverted = _maybe_auto_revert(
                    reason_prefix="gateway: ",
                    game_spawn_wall=game_spawn_wall,
                )
                if reverted:
                    if proc.poll() is None:
                        _stop_game(proc)
                    proc, game_spawn_wall = _spawn_game()
                    before = _snapshot()
                    pending_copyover = False
                    continue
                # Failed/skipped: hold + outage clear already done inside
                # revert path (or the thrash guard just armed a fresh
                # hold). Fall through -- a dead child still needs the
                # normal poll()/hang-check handling below, every tick.

        if not backup_running and world_backup.backup_due():
            backup_running = True
            try:
                world_backup.run_backup(triggered_by="scheduler")
            except Exception as exc:
                print(f"[watch] backup error (will retry): {exc}", flush=True)
            finally:
                backup_running = False

        # Reap git zombies (and any other orphaned children) before we
        # look at the server child -- keeps the PID cgroup from filling up.
        _reap_orphans(proc)
        if gateway_proc is not None:
            _reap_orphans(gateway_proc)
            if gateway_proc.poll() is not None:
                print(
                    f"[watch] gateway exited ({gateway_proc.returncode}) "
                    "-- restarting gateway + game",
                    flush=True,
                )
                _stop_game(proc)
                gateway_proc = _spawn_gateway()
                time.sleep(0.4)
                proc, game_spawn_wall = _spawn_game(cold=True)
                before = _snapshot()
                pending_copyover = False
                settle_deadline = None
                continue

        if proc.poll() is not None:   # None means "still running"
            _after_game_exit(
                proc, root=root, game_spawn_wall=game_spawn_wall
            )
            if crash_recovery.respawn_paused(root=root):
                if not hold_pause_announced:
                    hold_pause_announced = True
                    reason = (
                        crash_recovery.read_boot_hold_reason(root=root)
                        or "crash hold active"
                    )
                    print(
                        f"[watch] server.py exited ({proc.returncode}) "
                        f"-- respawn PAUSED ({reason[:240]}). "
                        "Fix content, then gm recover clearhold.",
                        flush=True,
                    )
                continue
            # Count once per death, immediately before respawn -- not
            # every poll tick while a hold pauses the dead child.
            _count_unready_exit()
            hold_pause_announced = False
            if use_gateway:
                print(
                    f"[watch] server.py exited ({proc.returncode}) "
                    "-- restarting game (clients held by gateway)",
                    flush=True,
                )
            else:
                print(
                    f"[watch] server.py exited ({proc.returncode}) "
                    "-- restarting (no copyover possible for a crash)",
                    flush=True,
                )
            spawn_failures += 1
            if _maybe_reload_storm_revert(
                spawn_failures=spawn_failures,
                game_spawn_wall=game_spawn_wall,
            ):
                spawn_failures = 0
                proc, game_spawn_wall = _spawn_game()
                before = _snapshot()
                pending_copyover = False
                settle_deadline = None
                continue
            proc, game_spawn_wall = _respawn_game(
                proc, game_spawn_wall, spawn_failures=spawn_failures,
            )
            before = _snapshot()
            pending_copyover = False
            settle_deadline = None
            continue

        # Alive but stuck (no exit): classic autorun never sees this.
        # Heartbeat stamp from tick_loop goes stale → force restart.
        if use_gateway and proc.poll() is None:
            kill_desync, desync_reason = crash_recovery.should_kill_for_ipc_desync(
                game_spawn_wall=game_spawn_wall,
                root=root,
            )
            if kill_desync:
                crash_recovery.record_ipc_desync_kill(root=root)
                bounce_gateway, bounce_reason = (
                    crash_recovery.should_restart_gateway_for_desync(root=root)
                )
                if bounce_gateway and gateway_proc is not None:
                    print(
                        f"[watch] gateway IPC desync ({desync_reason}; "
                        f"{bounce_reason}) -- restarting gateway + game "
                        "(clients held briefly)",
                        flush=True,
                    )
                    _stop_game(proc)
                    _after_game_exit(
                        proc, root=root, game_spawn_wall=game_spawn_wall
                    )
                    _count_unready_exit()
                    crash_recovery.record_ipc_desync_gateway_restart(root=root)
                    _stop_gateway(gateway_proc)
                    gateway_proc = _spawn_gateway()
                    time.sleep(0.4)
                    proc, game_spawn_wall = _spawn_game(cold=True)
                    before = _snapshot()
                    pending_copyover = False
                    settle_deadline = None
                    continue
                print(
                    f"[watch] gateway IPC desync ({desync_reason}) "
                    "-- killing game (clients held by gateway)",
                    flush=True,
                )
                _stop_game(proc)
                _after_game_exit(
                    proc, root=root, game_spawn_wall=game_spawn_wall
                )
                if crash_recovery.respawn_paused(root=root):
                    continue
                _count_unready_exit()
                spawn_failures += 1
                if _maybe_reload_storm_revert(
                    spawn_failures=spawn_failures,
                    game_spawn_wall=game_spawn_wall,
                ):
                    spawn_failures = 0
                    proc, game_spawn_wall = _spawn_game()
                    before = _snapshot()
                    pending_copyover = False
                    settle_deadline = None
                    continue
                proc, game_spawn_wall = _respawn_game(
                    proc, game_spawn_wall, spawn_failures=spawn_failures,
                )
                before = _snapshot()
                pending_copyover = False
                settle_deadline = None
                continue

        kill_hang, hang_reason = game_heartbeat.should_kill_for_hang(
            spawn_wall=game_spawn_wall,
        )
        if kill_hang:
            if use_gateway:
                print(
                    f"[watch] server.py hung ({hang_reason}) "
                    "-- killing game (clients held by gateway)",
                    flush=True,
                )
            else:
                print(
                    f"[watch] server.py hung ({hang_reason}) "
                    "-- killing and restarting",
                    flush=True,
                )
            _stop_game(proc)
            _after_game_exit(
                proc,
                hang_kill=True,
                root=root,
                game_spawn_wall=game_spawn_wall,
            )
            if crash_recovery.respawn_paused(root=root):
                continue
            _count_unready_exit()
            spawn_failures += 1
            if _maybe_reload_storm_revert(
                spawn_failures=spawn_failures,
                game_spawn_wall=game_spawn_wall,
            ):
                spawn_failures = 0
                proc, game_spawn_wall = _spawn_game()
                before = _snapshot()
                pending_copyover = False
                settle_deadline = None
                continue
            proc, game_spawn_wall = _respawn_game(
                proc, game_spawn_wall, spawn_failures=spawn_failures,
            )
            before = _snapshot()
            pending_copyover = False
            settle_deadline = None
            continue

        # Boot heal / auto-deploy catalog merges can touch watched JSON while
        # the child is still importing -- defer one copyover instead of
        # SIGUSR1-stacking during startup (exit -10 storms mid-chargen).
        if pending_copyover and proc.poll() is None:
            if crash_recovery.hold_active(root=root):
                print(
                    "[watch] revert hold active -- clearing deferred copyover",
                    flush=True,
                )
                pending_copyover = False
                pending_change_paths = []
                before = _snapshot()
                continue
            deferred_suppress = copyover_suppress_reason(
                "deferred copyover",
                unready_exits=unready_exits,
                root=root,
            )
            if deferred_suppress:
                print(
                    f"[watch] deferred copyover suppressed: {deferred_suppress}",
                    flush=True,
                )
                pending_copyover = False
                pending_change_paths = []
                copyover_wait_logged = False
                before = _snapshot()
                continue
            if copyover_child_ready(
                game_spawn_wall,
                boot_grace=copyover_boot_grace,
                root=root,
            ):
                files_note = _format_changed_paths(pending_change_paths)
                print(
                    "[watch] deferred copyover -- signaling game "
                    f"(copyover ready; gateway holds clients) {files_note}",
                    flush=True,
                )
                try:
                    from engine import copyover as copyover_mod

                    copyover_mod.append_journal_event(
                        "watch",
                        reason="deferred copyover",
                        files=pending_change_paths,
                        root=root,
                    )
                except Exception as exc:
                    print(
                        f"[watch] copyover journal append failed: {exc!r}",
                        flush=True,
                    )
                if not _signal_planned_copyover(proc):
                    _stop_game(proc)
                    proc, game_spawn_wall = _spawn_game()
                pending_copyover = False
                copyover_wait_logged = False
                settle_deadline = None
                pending_change_paths = []
                before = _snapshot()
                continue
            # One queued reload: absorb bind-mount churn until the stamp.
            before = _snapshot()
            continue

        after = _snapshot()
        _maybe_log_snapshot_metrics(poll_tick=poll_tick, periodic_ok=True)

        from engine import auto_deploy as auto_deploy_mod

        def _fire_copyover(*, reason_label):
            """SIGUSR1 the game child, or queue until ``.copyover_ready``."""
            nonlocal proc, game_spawn_wall, before, pending_copyover
            nonlocal settle_deadline, copyover_wait_logged
            nonlocal pending_change_paths
            suppress = copyover_suppress_reason(
                reason_label, unready_exits=unready_exits, root=root
            )
            if suppress:
                print(
                    f"[watch] {reason_label} suppressed: {suppress}",
                    flush=True,
                )
                pending_copyover = False
                pending_change_paths = []
                settle_deadline = None
                copyover_wait_logged = False
                before = after
                return
            files_note = _format_changed_paths(pending_change_paths)
            if not copyover_child_ready(
                game_spawn_wall,
                boot_grace=copyover_boot_grace,
                root=root,
            ):
                pending_copyover = True
                settle_deadline = None
                before = after
                if not copyover_wait_logged:
                    print(
                        f"[watch] {reason_label} -- queued until "
                        f"copyover ready (Game() / veil stamp) {files_note}",
                        flush=True,
                    )
                    copyover_wait_logged = True
                return
            copyover_wait_logged = False
            if use_gateway:
                print(
                    f"[watch] {reason_label} -- "
                    "signaling game (announce + exit; gateway holds clients) "
                    f"{files_note}",
                    flush=True,
                )
            else:
                print(
                    f"[watch] {reason_label} -- hot-reloading (copyover) "
                    f"{files_note}",
                    flush=True,
                )
            try:
                from engine import copyover as copyover_mod

                copyover_mod.append_journal_event(
                    "watch",
                    reason=reason_label,
                    files=pending_change_paths,
                    root=root,
                )
            except Exception as exc:
                print(
                    f"[watch] copyover journal append failed: {exc!r}",
                    flush=True,
                )
            if not _signal_planned_copyover(proc):
                _stop_game(proc)
                proc, game_spawn_wall = _spawn_game()
            pending_copyover = False
            pending_change_paths = []
            settle_deadline = None
            before = after

        # Veil/sync announced exactly one deliberate reload (even during quiesce).
        # If the sync also touched gateway-holder modules, drop clients --
        # do not treat that as a stay-connected copyover.
        if auto_deploy_mod.announced_copyover_pending(root):
            announced_suppress = copyover_suppress_reason(
                "announced deploy sync",
                unready_exits=unready_exits,
                root=root,
            )
            if announced_suppress:
                # Clear the stamp so a leftover announce cannot skip
                # file-mtime forever. Off means off -- do not SIGUSR1,
                # and do not bounce the gateway (2026-09-11: files=none
                # copyover after autodeploy off).
                print(
                    f"[watch] announced deploy sync suppressed: "
                    f"{announced_suppress}",
                    flush=True,
                )
                auto_deploy_mod.clear_announced_copyover(root)
            else:
                dropped = _maybe_drop_clients_for_gateway_modules(
                    before,
                    after,
                    proc=proc,
                    gateway_proc=gateway_proc,
                    use_gateway=use_gateway,
                    reason_label="announced deploy touched gateway source",
                )
                if dropped:
                    proc, game_spawn_wall, gateway_proc = dropped
                    before = after
                    pending_copyover = False
                    settle_deadline = None
                    continue
                auto_deploy_mod.clear_announced_copyover(root)
                _fire_copyover(reason_label="announced deploy sync")
                continue

        # Post-sync protect-restore rewrites hundreds of mtimes. Absorb them
        # until stable boot at the new HEAD (quiesce cleared in write_stable).
        if auto_deploy_mod.tree_sync_quiesce_active(root):
            before = after
            pending_copyover = False
            settle_deadline = None
            pending_change_paths = []
            continue

        # Boot disk heals (items.json canon patch, npc roster stamps, …)
        # rewrite watched JSON after Game() starts. Absorb those mtimes
        # until write_stable -- otherwise the heal's own write copyovers
        # the child it just booted.
        if boot_stability.boot_disk_quiesce_active(root):
            before = after
            pending_copyover = False
            settle_deadline = None
            pending_change_paths = []
            continue

        # Batched reload: quiet period elapsed since the last mtime churn.
        if (
            copyover_settle_seconds > 0
            and settle_deadline is not None
            and after == before
            and proc.poll() is None
            and time.time() >= settle_deadline
        ):
            if crash_recovery.hold_active(root=root):
                print(
                    "[watch] revert hold active -- clearing settled copyover",
                    flush=True,
                )
                settle_deadline = None
                pending_change_paths = []
                continue
            _fire_copyover(reason_label="settled code/content change")
            continue

        if after != before:
            if crash_recovery.hold_active(root=root):
                print(
                    "[watch] revert hold active -- skipping copyover "
                    "on file change",
                    flush=True,
                )
                pending_copyover = False
                pending_change_paths = []
                before = after
                continue
            # Gateway process modules do not hot-reload -- restart holder
            # + game (clients drop). Everything else: game-only / copyover.
            dropped = _maybe_drop_clients_for_gateway_modules(
                before,
                after,
                proc=proc,
                gateway_proc=gateway_proc,
                use_gateway=use_gateway,
                reason_label="gateway source changed",
            )
            if dropped:
                proc, game_spawn_wall, gateway_proc = dropped
                before = after
                pending_copyover = False
                continue
            if copyover_settle_seconds > 0:
                settle_deadline = time.time() + copyover_settle_seconds
                delta = _snapshot_changed_paths(before, after)
                for path in delta:
                    if path not in pending_change_paths:
                        pending_change_paths.append(path)
                del pending_change_paths[_PENDING_CHANGE_PATHS_CAP:]
                print(
                    "[watch] code/content change detected -- "
                    f"waiting {copyover_settle_seconds:.0f}s settle "
                    "(batch rapid deploy edits) "
                    f"{_format_changed_paths(delta)}",
                    flush=True,
                )
                before = after
                continue
            delta = _snapshot_changed_paths(before, after)
            for path in delta:
                if path not in pending_change_paths:
                    pending_change_paths.append(path)
            del pending_change_paths[_PENDING_CHANGE_PATHS_CAP:]
            _fire_copyover(reason_label="code/content change detected")
            continue

        seconds_until_deploy += poll_s
        # gm autodeploy now: skip the remaining poll wait (still respects
        # revert-hold / override-off via poll_skip_reason).
        force_deploy = False
        immediate_fn = getattr(auto_deploy, "immediate_requested", None)
        if callable(immediate_fn) and immediate_fn(root):
            skip_now = auto_deploy.poll_skip_reason(root)
            if skip_now is None:
                force_deploy = True
                print(
                    "[watch] immediate catch-up requested -- "
                    "polling auto-deploy now",
                    flush=True,
                )
        if force_deploy or seconds_until_deploy >= deploy_every:
            seconds_until_deploy = 0.0
            try:
                # A revert hold forces auto-deploy off (see auto_deploy._enabled)
                # so a fix pushed while held would otherwise sit unused until
                # someone remembers `gm recover clearhold`. Check on the same
                # cadence as the deploy poll so a real fix self-heals.
                if crash_recovery.hold_active(root=root):
                    resume_suppress = auto_resume_suppress_reason(
                        root, unready_exits=unready_exits
                    )
                    if resume_suppress:
                        print(
                            f"[watch] auto-resume suppressed: {resume_suppress}",
                            flush=True,
                        )
                    else:
                        resumed, detail = (
                            crash_recovery.maybe_auto_resume_hold(root=root)
                        )
                        if resumed:
                            print(
                                f"[watch] auto-resume: {detail}",
                                flush=True,
                            )
                skip = auto_deploy.poll_skip_reason(root)
                if skip is None:
                    # Reload only when deploy would actually run -- re-parsing
                    # hooks.py every 30s with AUTO_DEPLOY=0 was wasted CPU.
                    auto_deploy = reload_auto_deploy()
                    deploy_every = auto_deploy.poll_interval_seconds()
                    # Compare against the *pre-deploy* snapshot. Resetting
                    # ``before`` here used to swallow gateway.py / watcher
                    # mtimes, so a full-shutdown Veil ran as stay-connected
                    # copyover and nobody disconnected.
                    snap_before_deploy = before
                    deployed = auto_deploy.try_auto_deploy()
                    if deployed:
                        after_deploy = _snapshot()
                        dropped = _maybe_drop_clients_for_gateway_modules(
                            snap_before_deploy,
                            after_deploy,
                            proc=proc,
                            gateway_proc=gateway_proc,
                            use_gateway=use_gateway,
                            reason_label="deploy sync touched gateway source",
                        )
                        if dropped:
                            proc, game_spawn_wall, gateway_proc = dropped
                        before = after_deploy
                        pending_copyover = False
                        settle_deadline = None
            except Exception as exc:
                print(f"[watch] auto_deploy error (will retry): {exc}", flush=True)


if __name__ == "__main__":
    main()
