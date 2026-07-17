"""
deploy_notify.py -- world-wide bug-fix deploy announcements + countdown.

When a fix is ready to ship, a host-side helper (tools/deploy_bug_fix.py) or
the GM `deployfix` command writes `.deploy_signal.json` beside the save file.
The game tick loop calls tick() each heartbeat; that starts a background
asyncio task which:

  1. Broadcasts that a bug was fixed and a world reset is coming.
  2. Counts down (players stay connected -- copyover preserves sockets).
  3. Writes `.deploy_ready` so the host script can `gh pr checkout` the fix.
  4. After copyover resumes, on_resume() announces the fix is live and marks
     the bug resolved in bug_reports.log when a bug_id was provided.

Networking stays out of this module (file hand-off only, same spirit as
reports.py). The actual git pull runs on the host; engine/watch_and_run.py
detects the changed .py files and SIGUSR1's a copyover automatically.
"""

import asyncio
import json
import os

from engine import reports

# Transient hand-off files (gitignored). Live beside riftforge.db / report_dir.
SIGNAL_PATH = ".deploy_signal.json"
READY_PATH = ".deploy_ready"

# Seconds at which to repeat the countdown warning (plus the initial announce).
_COUNTDOWN_WARN_AT = (15, 10, 5, 3, 2, 1)

# Background task handle -- None when idle.
_deploy_task = None
# mtime of the signal file we already started a countdown for (avoid duplicates).
_started_mtime = None
# Persists deploy_keys we already announced through copyover (survives restarts).
_STATE_NAME = ".deploy_notify_state.json"


def signal_path(directory="."):
    """Absolute path to the deploy signal file under report_dir."""
    return os.path.join(directory, SIGNAL_PATH)


def ready_path(directory="."):
    """Absolute path to the deploy-ready marker under report_dir."""
    return os.path.join(directory, READY_PATH)


def _read_signal(directory):
    """Load the signal dict, or None if missing/unreadable."""
    path = signal_path(directory)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _write_signal(directory, payload):
    """Persist the signal dict (updates phase, etc.)."""
    path = signal_path(directory)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def _cleanup(directory):
    """Remove hand-off files after a deploy completes."""
    for path in (signal_path(directory), ready_path(directory)):
        try:
            os.remove(path)
        except OSError:
            pass


def _state_path(directory):
    return os.path.join(directory, _STATE_NAME)


def _load_completed_keys(directory):
    """Return the set of deploy_keys already announced through copyover."""
    path = _state_path(directory)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return set()
    keys = data.get("completed_deploy_keys", [])
    return set(keys) if isinstance(keys, list) else set()


def _mark_completed(directory, deploy_key):
    """Record that this deploy_key finished (countdown + copyover + on_resume)."""
    if not deploy_key:
        return
    keys = _load_completed_keys(directory)
    if deploy_key in keys:
        return
    keys.add(deploy_key)
    path = _state_path(directory)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"completed_deploy_keys": sorted(keys)}, f, indent=2)
        f.write("\n")


def _seed_completed_from_auto_deploy(directory):
    """If auto_deploy already shipped a commit, treat it as announced once."""
    path = os.path.join(directory, ".auto_deploy_state.json")
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return
    sha = (state.get("last_deploy") or {}).get("sha")
    if sha:
        _mark_completed(directory, sha)


def queue_deploy(directory, *, pr, bug_id=None, summary="", countdown_seconds=30,
                 triggered_by="unknown", commit_sha=None):
    """Write a new deploy signal -- tick() will pick it up on the next heartbeat.

    pr is stored for logging only (the host script already knows it). bug_id
    and summary drive the in-game announcements; when bug_id is set, on_resume()
    marks that bug resolved after copyover.

    commit_sha= (when known) is used as a stable deploy_key so auto_deploy does
    not re-announce the same squash-merge on every poll when local files drift.
    """
    _seed_completed_from_auto_deploy(directory)

    deploy_key = (commit_sha or str(pr)).strip()
    if deploy_key in _load_completed_keys(directory):
        print(
            f"[deploy_notify] skipping deploy already completed for {deploy_key[:12]}",
            flush=True,
        )
        return None

    existing = _read_signal(directory)
    if existing:
        existing_key = existing.get("deploy_key") or existing.get("commit_sha") or str(
            existing.get("pr", ""),
        )
        if existing_key == deploy_key and existing.get("phase") in (
            "pending", "awaiting_copyover",
        ):
            return existing

    payload = {
        "pr": str(pr),
        "bug_id": bug_id,
        "summary": summary.strip() or "A reported bug has been fixed.",
        "countdown_seconds": max(0, int(countdown_seconds)),
        "triggered_by": triggered_by,
        "phase": "pending",
        "deploy_key": deploy_key,
        "commit_sha": commit_sha,
    }
    _write_signal(directory, payload)
    # Drop any stale ready marker from a prior attempt.
    try:
        os.remove(ready_path(directory))
    except OSError:
        pass
    return payload


def tick(game):
    """Called from Game.on_tick() -- start a countdown when a signal appears.

    Synchronous on purpose: only schedules asyncio.create_task, never blocks
    the tick loop on sleep().
    """
    global _deploy_task, _started_mtime

    directory = game.report_dir
    path = signal_path(directory)
    if not os.path.isfile(path):
        _started_mtime = None
        return

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return

    signal = _read_signal(directory)
    if not signal:
        return

    # Already waiting for copyover -- on_resume() will finish the workflow.
    if signal.get("phase") == "awaiting_copyover":
        return

    if _deploy_task is not None and not _deploy_task.done():
        return
    if _started_mtime == mtime:
        return

    _started_mtime = mtime
    _deploy_task = asyncio.create_task(_run_countdown(game, signal))
    _deploy_task.add_done_callback(_log_task_exception)


def _log_task_exception(task):
    """Surface a countdown task crash without killing the tick loop."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        print(f"[deploy_notify] countdown task crashed: {exc}", flush=True)


async def _run_countdown(game, signal):
    """Broadcast the fix announcement, count down, then release the host."""
    directory = game.report_dir
    bug_id = signal.get("bug_id")
    summary = signal.get("summary") or "A reported bug has been fixed."
    total = int(signal.get("countdown_seconds", 30))

    bug_ref = f"Bug #{bug_id}" if bug_id else "A bug"
    game.broadcast_all(
        f"*** {bug_ref} has been fixed: {summary} ***\r\n"
        f"The world will reset in {total} seconds to apply the update. "
        "You will stay connected through the reset."
    )
    print(
        f"[deploy_notify] countdown started for {bug_ref} "
        f"(PR {signal.get('pr', '?')}, {total}s)",
        flush=True,
    )

    # Walk second-by-second so we can hit WARN_AT milestones cleanly.
    for remaining in range(total, 0, -1):
        if remaining in _COUNTDOWN_WARN_AT and remaining < total:
            game.broadcast_all(f"*** World reset in {remaining}... ***")
        await asyncio.sleep(1)

    signal["phase"] = "awaiting_copyover"
    _write_signal(directory, signal)

    # Host tools/deploy_bug_fix.py polls for this file before git checkout.
    with open(ready_path(directory), "w", encoding="utf-8") as f:
        json.dump({"ok": True, "pr": signal.get("pr")}, f)
        f.write("\n")

    game.broadcast_all(
        "*** Applying the update now -- reality will reform momentarily. "
        "Hold on. ***"
    )
    print("[deploy_notify] deploy_ready written -- host may pull the fix now",
          flush=True)


async def on_resume(game):
    """After copyover, announce the fix is live and tidy up hand-off files."""
    directory = game.report_dir
    signal = _read_signal(directory)
    if not signal or signal.get("phase") != "awaiting_copyover":
        return

    bug_id = signal.get("bug_id")
    summary = signal.get("summary") or "A reported bug has been fixed."
    bug_ref = f"Bug #{bug_id}" if bug_id else "The bug fix"

    game.broadcast_all(
        f"*** {bug_ref} is now live: {summary} ***"
    )

    if bug_id is not None:
        try:
            reports.mark(
                reports.BUG, int(bug_id), "resolved", directory=directory,
            )
            print(f"[deploy_notify] marked bug #{bug_id} resolved", flush=True)
        except (ValueError, IndexError) as exc:
            print(
                f"[deploy_notify] could not mark bug #{bug_id} resolved: {exc}",
                flush=True,
            )

    _cleanup(directory)
    deploy_key = signal.get("deploy_key") or signal.get("commit_sha") or str(
        signal.get("pr", ""),
    )
    _mark_completed(directory, deploy_key)
    print("[deploy_notify] deploy complete", flush=True)
