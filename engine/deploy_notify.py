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
     each bug in bug_ids (or the legacy single bug_id) resolved in
     bug_reports.log — which credits reporters via the after-mark hook.

GM ``autodeploy on`` catch-up (engine/auto_deploy.py) writes
``.catchup_bug_resolve.json`` instead: copyover ``on_resume()`` (and the
game tick) marks every missed Fix ticket resolved without a Veil countdown
because the working tree was already synced via ``git reset --hard``.

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
CATCHUP_RESOLVE_PATH = ".catchup_bug_resolve.json"

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


def catchup_resolve_path(directory="."):
    """Absolute path to the autodeploy catch-up bug-resolve hand-off file."""
    return os.path.join(directory, CATCHUP_RESOLVE_PATH)


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


def mark_deploy_completed(directory, deploy_key):
    """Public wrapper — auto_deploy catch-up marks Fix SHAs without countdown."""
    _mark_completed(directory, deploy_key)


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


def _normalize_bug_ids(bug_id=None, bug_ids=None):
    """Return a de-duplicated list of int bug ids from queue_deploy args.

    ``bug_ids`` (batch Fix subjects) wins when provided; ``bug_id`` is folded
    in for callers that still pass a single primary. Empty / None → [].
    """
    ids = []
    if bug_ids:
        for raw in bug_ids:
            try:
                n = int(raw)
            except (TypeError, ValueError):
                continue
            if n not in ids:
                ids.append(n)
    if bug_id is not None:
        try:
            n = int(bug_id)
        except (TypeError, ValueError):
            n = None
        if n is not None and n not in ids:
            # Primary announce id goes first when it was not already listed.
            ids.insert(0, n)
    return ids


def _normalize_suggestion_ids(suggestion_id=None, suggestion_ids=None):
    """Return a de-duplicated list of int suggestion ids from queue_deploy."""
    ids = []
    if suggestion_ids:
        for raw in suggestion_ids:
            try:
                n = int(raw)
            except (TypeError, ValueError):
                continue
            if n not in ids:
                ids.append(n)
    if suggestion_id is not None:
        try:
            n = int(suggestion_id)
        except (TypeError, ValueError):
            n = None
        if n is not None and n not in ids:
            ids.insert(0, n)
    return ids


def describe_ticket_ref(bug_ids=None, suggestion_ids=None):
    """Short in-game label for one or more bug/suggestion ticket ids."""
    bug_ids = list(bug_ids or [])
    suggestion_ids = list(suggestion_ids or [])
    parts = []
    if bug_ids:
        if len(bug_ids) == 1:
            parts.append(f"Bug #{bug_ids[0]}")
        else:
            parts.append(f"Bugs #{bug_ids[0]}–#{bug_ids[-1]}")
    if suggestion_ids:
        if len(suggestion_ids) == 1:
            parts.append(f"Suggestion #{suggestion_ids[0]}")
        else:
            parts.append(
                f"Suggestions #{suggestion_ids[0]}–#{suggestion_ids[-1]}"
            )
    if not parts:
        return "A tear in the script"
    return " and ".join(parts)


def describe_ticket_live_clause(bug_ids=None, suggestion_ids=None):
    """Post-copyover announce clause: 'Bug #N fix is live', etc.

    Bug-only deploys say *fix* so players do not read 'Bug #N is live' as the
    bug itself shipping. Suggestion-only deploys say *ship*; mixed batches keep
    the neutral 'is live' wording.
    """
    bug_ids = list(bug_ids or [])
    suggestion_ids = list(suggestion_ids or [])
    ref = describe_ticket_ref(bug_ids, suggestion_ids)
    if ref == "A tear in the script":
        return "The mend is live"
    if bug_ids and not suggestion_ids:
        return f"{ref} fix is live"
    if suggestion_ids and not bug_ids:
        return f"{ref} ship is live"
    return f"{ref} is live"


def describe_ticket_countdown_verb(bug_ids=None, suggestion_ids=None):
    """Veil countdown verb: ``has/have been mended/shipped``."""
    bug_ids = list(bug_ids or [])
    suggestion_ids = list(suggestion_ids or [])
    if bug_ids and not suggestion_ids:
        base = "mended"
    else:
        base = "shipped"
    count = len(bug_ids) + len(suggestion_ids)
    if count > 1:
        return f"have been {base}"
    return f"has been {base}"


def queue_deploy(directory, *, pr, bug_id=None, bug_ids=None,
                 suggestion_id=None, suggestion_ids=None, summary="",
                 countdown_seconds=30, triggered_by="unknown", commit_sha=None):
    """Write a new deploy signal -- tick() will pick it up on the next heartbeat.

    pr is stored for logging only (the host script already knows it). bug_id /
    bug_ids and summary drive the in-game announcements; when any bug id is
    set, on_resume() marks each bug resolved after copyover (so batch Fix
    subjects credit every reporter).

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

    ids = _normalize_bug_ids(bug_id=bug_id, bug_ids=bug_ids)
    suggest_ids = _normalize_suggestion_ids(
        suggestion_id=suggestion_id, suggestion_ids=suggestion_ids,
    )
    if not summary.strip():
        if ids and suggest_ids:
            summary = "Reported bugs and suggestions have been shipped."
        elif suggest_ids:
            summary = "A player suggestion has been shipped."
        else:
            summary = "A reported bug has been fixed."
    payload = {
        "pr": str(pr),
        # Primary ids kept for older signal readers / announce chrome.
        "bug_id": ids[0] if ids else None,
        "bug_ids": ids,
        "suggestion_id": suggest_ids[0] if suggest_ids else None,
        "suggestion_ids": suggest_ids,
        "summary": summary.strip(),
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


def queue_catchup_resolves(directory, missed_fixes):
    """Hand off missed Fix commits for the game tick to mark resolved.

    ``missed_fixes`` is a list of dicts with ``sha``, ``bug_ids``, and
    ``summary`` (from auto_deploy.collect_missed_fix_commits). No Veil
    countdown — code is already on disk after catch-up ``reset --hard``.
    """
    if not missed_fixes:
        return
    payload = {
        "phase": "pending",
        "fixes": [
            {
                "commit_sha": fix["sha"],
                "bug_ids": list(fix.get("bug_ids") or []),
                "suggestion_ids": list(fix.get("suggestion_ids") or []),
                "summary": (fix.get("summary") or "").strip()
                or "A reported bug has been fixed.",
            }
            for fix in missed_fixes
        ],
    }
    path = catchup_resolve_path(directory)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    print(
        f"[deploy_notify] queued catch-up resolve for "
        f"{len(missed_fixes)} Fix commit(s)",
        flush=True,
    )


def _apply_catchup_fixes(game, fixes):
    """Mark bugs/suggestions resolved for missed ship commits (no countdown)."""
    directory = game.report_dir
    all_bug_ids = []
    all_suggestion_ids = []
    for fix in fixes:
        for bug_id in _normalize_bug_ids(bug_ids=fix.get("bug_ids")):
            if bug_id not in all_bug_ids:
                all_bug_ids.append(bug_id)
        for suggestion_id in _normalize_suggestion_ids(
            suggestion_ids=fix.get("suggestion_ids"),
        ):
            if suggestion_id not in all_suggestion_ids:
                all_suggestion_ids.append(suggestion_id)

    if all_bug_ids or all_suggestion_ids:
        last_summary = (fixes[-1].get("summary") or "").strip()
        if not last_summary:
            if all_bug_ids and all_suggestion_ids:
                last_summary = "Reported bugs and suggestions have been shipped."
            elif all_suggestion_ids:
                last_summary = "Player suggestions have been shipped."
            else:
                last_summary = "Reported bugs have been fixed."
        live_clause = describe_ticket_live_clause(
            all_bug_ids, all_suggestion_ids,
        )
        game.broadcast_all(
            f"*** The Veil holds. {live_clause} (catch-up): "
            f"{last_summary} ***"
        )

    for fix in fixes:
        commit_sha = fix.get("commit_sha") or fix.get("sha") or ""
        for bug_id in _normalize_bug_ids(bug_ids=fix.get("bug_ids")):
            try:
                reports.mark(
                    reports.BUG, int(bug_id), "resolved", directory=directory,
                    game=game,
                )
                print(
                    f"[deploy_notify] catch-up marked bug #{bug_id} resolved",
                    flush=True,
                )
            except (ValueError, IndexError) as exc:
                print(
                    f"[deploy_notify] catch-up could not mark bug "
                    f"#{bug_id} resolved: {exc}",
                    flush=True,
                )
        for suggestion_id in _normalize_suggestion_ids(
            suggestion_ids=fix.get("suggestion_ids"),
        ):
            try:
                reports.mark(
                    reports.SUGGEST, int(suggestion_id), "resolved",
                    directory=directory, game=game,
                )
                print(
                    f"[deploy_notify] catch-up marked suggestion "
                    f"#{suggestion_id} resolved",
                    flush=True,
                )
            except (ValueError, IndexError) as exc:
                print(
                    f"[deploy_notify] catch-up could not mark suggestion "
                    f"#{suggestion_id} resolved: {exc}",
                    flush=True,
                )
        if commit_sha:
            _mark_completed(directory, commit_sha)
    return bool(all_bug_ids or all_suggestion_ids)


def _process_catchup_resolves(game):
    """Mark bugs resolved after autodeploy catch-up (no countdown)."""
    directory = game.report_dir
    path = catchup_resolve_path(directory)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        try:
            os.remove(path)
        except OSError:
            pass
        return False

    if payload.get("phase") != "pending":
        try:
            os.remove(path)
        except OSError:
            pass
        return False

    fixes = payload.get("fixes") or []
    applied = _apply_catchup_fixes(game, fixes)

    try:
        os.remove(path)
    except OSError:
        pass
    if applied:
        from engine import auto_deploy
        latest = fixes[-1]
        auto_deploy.record_catchup_last_deploy(
            directory,
            {
                "sha": latest.get("commit_sha") or latest.get("sha"),
                "bug_ids": latest.get("bug_ids") or [],
            },
        )
        print("[deploy_notify] catch-up bug resolve complete", flush=True)
    return applied


def _reconcile_missed_fix_resolves_from_auto_deploy_state(game):
    """On copyover, resolve Fix commits between last_deploy and origin_main.

    Covers catch-up that synced code before the resolve hand-off shipped, or
  any gap where ``last_deploy`` lags ``origin_main`` with Fix subjects still
    open in ``bug_reports.log``.
    """
    directory = game.report_dir
    state_path = os.path.join(directory, ".auto_deploy_state.json")
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return reconcile_open_bugs_from_deployed_fixes(game)

    last_sha = (state.get("last_deploy") or {}).get("sha") or ""
    origin_sha = state.get("origin_main") or ""
    if not last_sha or not origin_sha or last_sha == origin_sha:
        return reconcile_open_bugs_from_deployed_fixes(game)

    from engine import auto_deploy

    missed = auto_deploy.collect_missed_fix_commits(
        directory, last_sha, origin_sha,
    )
    if not missed:
        return reconcile_open_bugs_from_deployed_fixes(game)

    completed = _load_completed_keys(directory)
    pending = [fix for fix in missed if fix["sha"] not in completed]
    if not pending:
        return reconcile_open_bugs_from_deployed_fixes(game)

    # Normalize keys for _apply_catchup_fixes (file hand-off uses commit_sha).
    fixes = [
        {
            "commit_sha": fix["sha"],
            "bug_ids": list(fix.get("bug_ids") or []),
            "suggestion_ids": list(fix.get("suggestion_ids") or []),
            "summary": fix.get("summary") or "",
        }
        for fix in pending
    ]
    applied = _apply_catchup_fixes(game, fixes)
    if applied:
        auto_deploy.record_catchup_last_deploy(directory, pending[-1])
        print(
            f"[deploy_notify] copyover reconciled {len(pending)} missed Fix "
            f"commit(s) from auto_deploy state",
            flush=True,
        )
    return applied or reconcile_open_bugs_from_deployed_fixes(game)


def reconcile_open_bugs_from_deployed_fixes(game):
    """Close open tickets whose Fix subjects are already on the deployed tree.

    Idempotent boot/copyover heal for tickets that stayed ``open`` because
    resolve ran once at deploy time but ``reports.mark`` failed, the catch-up
    hand-off was lost, or the squash subject used a duplicate id (#239 vs
    #238) covered by ``auto_deploy._BUG_RESOLVE_ALIASES``.
    """
    directory = game.report_dir
    from engine import auto_deploy

    git_root = auto_deploy.git_root_for(directory)
    open_ids = set(auto_deploy.open_bug_ids(directory))
    if not open_ids:
        return False

    deployed_ids = set(
        auto_deploy.deployed_fix_bug_ids(git_root, directory),
    )
    to_close = sorted(open_ids & deployed_ids)
    if not to_close:
        return False

    for bug_id in to_close:
        try:
            reports.mark(
                reports.BUG, int(bug_id), "resolved", directory=directory,
                game=game,
            )
            print(
                f"[deploy_notify] deployed-fix heal marked bug "
                f"#{bug_id} resolved",
                flush=True,
            )
        except (ValueError, IndexError) as exc:
            print(
                f"[deploy_notify] deployed-fix heal could not mark bug "
                f"#{bug_id} resolved: {exc}",
                flush=True,
            )
    return bool(to_close)


def tick(game):
    """Called from Game.on_tick() -- start a countdown when a signal appears.

    Synchronous on purpose: only schedules asyncio.create_task, never blocks
    the tick loop on sleep(). Catch-up resolves run first (fast, no sleep).
    """
    global _deploy_task, _started_mtime

    directory = game.report_dir
    if _process_catchup_resolves(game):
        return

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
    bug_ids = _normalize_bug_ids(
        bug_id=signal.get("bug_id"), bug_ids=signal.get("bug_ids"),
    )
    suggestion_ids = _normalize_suggestion_ids(
        suggestion_id=signal.get("suggestion_id"),
        suggestion_ids=signal.get("suggestion_ids"),
    )
    summary = signal.get("summary") or "A reported bug has been fixed."
    total = int(signal.get("countdown_seconds", 30))

    ticket_ref = describe_ticket_ref(bug_ids, suggestion_ids)
    verb = describe_ticket_countdown_verb(bug_ids, suggestion_ids)
    game.broadcast_all(
        f"*** {ticket_ref} {verb}: {summary} ***\r\n"
        f"*** The Veil will reseal in {total} seconds. Stay put -- "
        f"you will remain. ***"
    )
    print(
        f"[deploy_notify] countdown started for {ticket_ref} "
        f"(PR {signal.get('pr', '?')}, {total}s)",
        flush=True,
    )

    # Walk second-by-second so we can hit WARN_AT milestones cleanly.
    for remaining in range(total, 0, -1):
        if remaining in _COUNTDOWN_WARN_AT and remaining < total:
            game.broadcast_all(f"*** The Veil reseals in {remaining}... ***")
        await asyncio.sleep(1)

    signal["phase"] = "awaiting_copyover"
    _write_signal(directory, signal)

    # Host tools/deploy_bug_fix.py polls for this file before git checkout.
    with open(ready_path(directory), "w", encoding="utf-8") as f:
        json.dump({"ok": True, "pr": signal.get("pr")}, f)
        f.write("\n")

    game.broadcast_all(
        "*** The rewrite takes hold. The Veil folds -- hold on. ***"
    )
    print("[deploy_notify] deploy_ready written -- host may pull the fix now",
          flush=True)


async def on_resume(game):
    """After copyover, finish deploy hand-offs and reconcile missed Fix bugs."""
    directory = game.report_dir

    # Catch-up file (watcher) or last_deploy lag (retro after early catch-up).
    _process_catchup_resolves(game)
    _reconcile_missed_fix_resolves_from_auto_deploy_state(game)

    signal = _read_signal(directory)
    if not signal or signal.get("phase") != "awaiting_copyover":
        return

    bug_ids = _normalize_bug_ids(
        bug_id=signal.get("bug_id"), bug_ids=signal.get("bug_ids"),
    )
    suggestion_ids = _normalize_suggestion_ids(
        suggestion_id=signal.get("suggestion_id"),
        suggestion_ids=signal.get("suggestion_ids"),
    )
    summary = signal.get("summary") or "A reported bug has been fixed."

    live_clause = describe_ticket_live_clause(bug_ids, suggestion_ids)
    game.broadcast_all(
        f"*** The Veil holds. {live_clause}: {summary} ***"
    )

    for bug_id in bug_ids:
        try:
            reports.mark(
                reports.BUG, int(bug_id), "resolved", directory=directory,
                game=game,
            )
            print(f"[deploy_notify] marked bug #{bug_id} resolved", flush=True)
        except (ValueError, IndexError) as exc:
            print(
                f"[deploy_notify] could not mark bug #{bug_id} resolved: {exc}",
                flush=True,
            )

    for suggestion_id in suggestion_ids:
        try:
            reports.mark(
                reports.SUGGEST, int(suggestion_id), "resolved",
                directory=directory, game=game,
            )
            print(
                f"[deploy_notify] marked suggestion #{suggestion_id} resolved",
                flush=True,
            )
        except (ValueError, IndexError) as exc:
            print(
                f"[deploy_notify] could not mark suggestion "
                f"#{suggestion_id} resolved: {exc}",
                flush=True,
            )

    _cleanup(directory)
    deploy_key = signal.get("deploy_key") or signal.get("commit_sha") or str(
        signal.get("pr", ""),
    )
    _mark_completed(directory, deploy_key)
    print("[deploy_notify] deploy complete", flush=True)
