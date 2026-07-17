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
    gateway. On .py / content change, terminate the **game only** and
    respawn it — clients stay connected on the gateway. Crashes do the
    same (gateway holds sockets; new game reattaches).

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

import glob
import os
import signal
import subprocess
import sys
import time

POLL_SECONDS = 1.0        # how often to check for changed files


# Every .py file below this directory, plus world JSON (content/) and game
# catalogs (supers/content/) -- either kind of change should trigger the
# same hot-reload (map editor saves, item/origin/persona edits, etc.).
_WATCHED_GLOBS = (
    "**/*.py",
    "content/**/*.json",
    "supers/content/**/*.json",
)


def _repo_root():
    """Repo root (watch_and_run.py lives in engine/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _gateway_mode():
    """True when Docker/live should hold clients across game restarts."""
    # Default ON for this entrypoint (Docker CMD). Explicit 0 disables.
    raw = os.environ.get("RIFTFORGE_GATEWAY", "1").strip()
    return raw not in ("0", "false", "False", "no", "NO")


def _snapshot():
    """{path: mtime} for every watched file below this directory.

    glob's `**` with recursive=True walks subdirectories too. `__pycache__`
    is skipped -- .pyc files there change on every import and would cause a
    restart LOOP (reload -> import -> .pyc changes -> reload -> ...).
    """
    snapshot = {}
    for pattern in _WATCHED_GLOBS:
        for path in glob.glob(pattern, recursive=True):
            if "__pycache__" in path:
                continue
            try:
                snapshot[path] = os.path.getmtime(path)
            except OSError:
                # A file can vanish between glob() listing it and us stat-ing
                # it (e.g. an editor's atomic save briefly renames it) --
                # harmless, just skip it this round.
                pass
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


def _spawn_game(env=None):
    """Start server.py as a child; inherit env so RIFTFORGE_GATEWAY reaches it."""
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    return subprocess.Popen(
        [sys.executable, "server.py"],
        env=child_env,
        cwd=_repo_root(),
    )


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

    proc = _spawn_game()
    before = _snapshot()

    from engine.auto_deploy import (
        ensure_git_safe_directory,
        poll_interval_seconds,
        try_auto_deploy,
    )

    # Bind-mounted checkouts are often owned by a non-root host user; mark
    # the repo safe so auto-deploy's git fetch is not rejected every poll.
    ensure_git_safe_directory(root)

    deploy_every = poll_interval_seconds()
    ticks_until_deploy = 0
    print(
        f"[watch] auto-deploy polling every {deploy_every}s "
        "(AUTO_DEPLOY=0 to disable)",
        flush=True,
    )

    while True:
        time.sleep(POLL_SECONDS)

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
                proc = _spawn_game()
                before = _snapshot()
                continue

        if proc.poll() is not None:   # None means "still running"
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
            proc = _spawn_game()
            before = _snapshot()
            continue

        after = _snapshot()
        if after != before:
            if use_gateway:
                print(
                    "[watch] code/content change detected -- "
                    "restarting game behind gateway",
                    flush=True,
                )
                _stop_game(proc)
                proc = _spawn_game()
            else:
                print(
                    "[watch] code/content change detected -- "
                    "hot-reloading (copyover)",
                    flush=True,
                )
                try:
                    proc.send_signal(signal.SIGUSR1)
                except (AttributeError, OSError) as exc:
                    # Windows / odd signal set: fall back to full restart.
                    print(
                        f"[watch] SIGUSR1 unavailable ({exc}); "
                        "restarting server.py",
                        flush=True,
                    )
                    _stop_game(proc)
                    proc = _spawn_game()
            before = after   # don't re-trigger next second on the same edit

        ticks_until_deploy += 1
        if ticks_until_deploy >= deploy_every:
            ticks_until_deploy = 0
            try:
                try_auto_deploy()
            except Exception as exc:
                print(f"[watch] auto_deploy error (will retry): {exc}", flush=True)


if __name__ == "__main__":
    main()
