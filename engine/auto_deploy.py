"""
auto_deploy.py -- poll GitHub and auto-ship merged bug fixes to the live game.

Docker's entry point (engine/watch_and_run.py) calls try_auto_deploy() on a
timer. When origin/main advances (e.g. after you squash-merge a Cursor fixer
PR), this module:

  1. Parses the new commit for bug id + summary text
  2. Queues deploy_notify's in-game countdown (global announcement)
  3. Waits for .deploy_ready
  4. Overlays only that commit's files onto the bind-mounted checkout
  5. watch_and_run copyovers the running server

Announce policy (player-facing countdown): ONLY intentional fix subjects
like "Fix bug #N: ..." trigger a deploy announcement. Merge commits,
feature commits, and subjects that merely mention "bug #N" mid-sentence
advance origin/main silently so incidental history (changelog merges,
PR numbers) never fake a second "Bug #N has been fixed" world reset.

No manual `tools/deploy_bug_fix.py` step. Disable with AUTO_DEPLOY=0, or
toggle live with GM `autodeploy on|off` (writes `.auto_deploy_override`).

State lives in .auto_deploy_state.json (gitignored) so a container restart
does not re-deploy old commits.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

STATE_NAME = ".auto_deploy_state.json"
READY_NAME = ".deploy_ready"
# GM `autodeploy on|off` writes this so watch_and_run (parent process) sees the
# toggle -- mutating os.environ inside server.py would not affect the watcher.
OVERRIDE_NAME = ".auto_deploy_override"

# Defaults; override via environment (see docker-compose.yml).
DEFAULT_POLL_EVERY = 30
DEFAULT_COUNTDOWN = 20
DEFAULT_READY_TIMEOUT = 120

# Intentional fix subjects only -- must look like a ship, not a mention.
# Examples that MATCH: "Fix bug #25: list commands alphabetically."
#                      "Fixes bug_reports.log #12 -- sparring echo text"
# Examples that do NOT: "Merge origin/main: ... with bug #25."
#                       "Enhance auto-deploy (#5)"  (PR number, not bug id)
_FIX_SUBJECT_RE = re.compile(
    r"^(?:fix(?:es|ed)?)\s+"
    r"(?:bug\s*#|bug_reports\.log\s*#)"
    r"(\d+)\b",
    re.IGNORECASE,
)

# Values accepted in the override file / GM command (normalized to these).
_OVERRIDE_ON = "on"
_OVERRIDE_OFF = "off"
_FALSEY_ENV = ("0", "false", "no", "off")


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _state_path(root):
    return os.path.join(root, STATE_NAME)


def _ready_path(root):
    return os.path.join(root, READY_NAME)


def override_path(root=None):
    """Absolute path to the GM autodeploy override file."""
    return os.path.join(root or _repo_root(), OVERRIDE_NAME)


def read_override(root=None):
    """Return 'on', 'off', or None if no override file / unreadable junk."""
    path = override_path(root)
    try:
        raw = open(path, encoding="utf-8").read().strip().lower()
    except OSError:
        return None
    if raw in (_OVERRIDE_ON, "1", "true", "yes"):
        return _OVERRIDE_ON
    if raw in (_OVERRIDE_OFF, "0", "false", "no"):
        return _OVERRIDE_OFF
    return None


def set_override(value, root=None):
    """Write the GM override file to 'on' or 'off'. Returns the path written.

    Raises ValueError if value is not on/off.
    """
    normalized = (value or "").strip().lower()
    if normalized in ("1", "true", "yes"):
        normalized = _OVERRIDE_ON
    if normalized in ("0", "false", "no"):
        normalized = _OVERRIDE_OFF
    if normalized not in (_OVERRIDE_ON, _OVERRIDE_OFF):
        raise ValueError(f"override must be on or off, got {value!r}")
    path = override_path(root)
    with open(path, "w", encoding="utf-8") as f:
        f.write(normalized + "\n")
    return path


def clear_override(root=None):
    """Remove the override file so AUTO_DEPLOY env is the only gate again."""
    path = override_path(root)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    return path


def env_enabled():
    """True when AUTO_DEPLOY env says enabled (ignores the override file)."""
    return os.environ.get("AUTO_DEPLOY", "1").strip().lower() not in _FALSEY_ENV


def _enabled():
    """Whether try_auto_deploy should poll this tick.

    GM override file wins when present; otherwise AUTO_DEPLOY env (default on).
    """
    override = read_override()
    if override is not None:
        return override == _OVERRIDE_ON
    return env_enabled()


def is_enabled():
    """Public wrapper for `_enabled()` -- used by GM status and smoke tests."""
    return _enabled()


def status_text():
    """One short multi-line status string for the GM autodeploy command."""
    override = read_override()
    env_on = env_enabled()
    effective = "on" if is_enabled() else "off"
    if override is None:
        override_line = "Override file: (none -- using AUTO_DEPLOY env)"
    else:
        override_line = f"Override file: {override}"
    return (
        f"Auto-deploy effective: {effective}\n"
        f"{override_line}\n"
        f"AUTO_DEPLOY env: {'on' if env_on else 'off'}"
    )


def _countdown_seconds():
    try:
        return max(5, int(os.environ.get("AUTO_DEPLOY_COUNTDOWN", DEFAULT_COUNTDOWN)))
    except ValueError:
        return DEFAULT_COUNTDOWN


def _git(*args, cwd=None):
    return subprocess.check_output(
        ["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL,
    ).strip()


def _run_git(*args, cwd=None):
    """Run a git subprocess; raise CalledProcessError on non-zero exit.

    Stdout/stderr are captured (not inherited) so a failed fetch can be
    logged with the real git reason -- DEVNULL made live outages look like
    a mysterious exit 128.
    """
    print(f"+ git {' '.join(args)}", flush=True)
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        # Keep the familiar CalledProcessError for callers' except clauses.
        exc = subprocess.CalledProcessError(
            result.returncode, result.args, output=result.stdout, stderr=result.stderr,
        )
        if detail:
            # Attach a short human line without dumping huge pack progress.
            lines = [ln for ln in detail.splitlines() if ln.strip()]
            short = " | ".join(lines[-3:])[:400]
            exc._riftforge_git_detail = short  # noqa: SLF001 -- read in _fetch_origin
        raise exc
    return result


def _load_state(root):
    path = _state_path(root)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _save_state(root, state):
    with open(_state_path(root), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def ensure_git_safe_directory(root=None):
    """Mark the repo safe for git when bind-mounted under a different uid.

    Docker runs as root while the host checkout is owned by the VM user
    (e.g. riftadmin uid 1000). Without this, every `git fetch` fails with
    "detected dubious ownership" (exit 128) and auto-deploy never advances.

    Idempotent -- only `--add` when the path is not already listed. Calling
    `--add` every 30s poll used to flood `~/.gitconfig` with duplicate
    `safe.directory` lines (harmless but noisy on the live host).
    """
    root = root or _repo_root()
    try:
        listed = subprocess.run(
            ["git", "config", "--global", "--get-all", "safe.directory"],
            check=False,
            capture_output=True,
            text=True,
        )
        existing = {
            ln.strip() for ln in (listed.stdout or "").splitlines() if ln.strip()
        }
        # Match both the absolute path and a trailing-slash variant.
        if root in existing or root.rstrip("/") in existing:
            return
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", root],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def _reset_hard_to(root, sha):
    """Move the working tree to sha (feature pushes on Azure / any host).

    Fix-bug deploys still use the narrower overlay path. Non-fix advances
    call this so a push to main actually updates live files, then the
    mtime watcher / copyover picks up the change.
    """
    ensure_git_safe_directory(root)
    print(f"[auto_deploy] syncing working tree to {sha[:12]}", flush=True)
    _run_git("reset", "--hard", sha, cwd=root)


def _fetch_origin(root):
    """Best-effort fetch -- offline / corrupt git should not crash the watcher.

    On failure, print git's own stderr (empty object, auth, dubious ownership,
    network). Silent exit-128 skips are how a corrupted live `.git` can stall
    origin/main for hours while AUTO_DEPLOY still looks "on".
    """
    ensure_git_safe_directory(root)
    try:
        _run_git("fetch", "origin", "main", cwd=root)
        return True
    except subprocess.CalledProcessError as exc:
        detail = getattr(exc, "_riftforge_git_detail", None) or str(exc)
        print(f"[auto_deploy] git fetch skipped: {detail}", flush=True)
        detail_l = detail.lower()
        # One-line recovery hint for the empty-object failure we hit on Azure.
        if "empty" in detail_l or "bad object" in detail_l:
            print(
                "[auto_deploy] hint: live .git may have empty loose objects -- "
                "see docs/LIVE_DEPLOY.md (Repair corrupted .git)",
                flush=True,
            )
        # PID-1 never reaping git helpers fills the cgroup until fork fails.
        if "cannot fork" in detail_l or "resource temporarily unavailable" in detail_l:
            print(
                "[auto_deploy] hint: container PID limit / git zombies -- "
                "docker compose restart; see docs/LIVE_DEPLOY.md "
                "(cannot fork / high PIDS)",
                flush=True,
            )
        return False
    except FileNotFoundError as exc:
        print(f"[auto_deploy] git fetch skipped: {exc}", flush=True)
        return False


def _origin_main_sha(root):
    return _git("rev-parse", "origin/main", cwd=root)


def _commit_subject(sha, root):
    return _git("log", "-1", "--format=%s", sha, cwd=root)


def _commit_parent_count(sha, root):
    """How many parents a commit has (1 = normal, 2+ = merge).

    Used to skip announce/overlay for merge tips -- git diff-tree without
    -m often returns no files for merges, and merge subjects frequently
    mention bug ids without being the fix itself.
    """
    parents = _git("rev-list", "--parents", "-n", "1", sha, cwd=root)
    # Format: "<sha> <parent1> [parent2 ...]" -- first token is the commit.
    return max(0, len(parents.split()) - 1)


def parse_deploy_metadata(subject: str) -> tuple[int | None, str]:
    """Extract bug # and a short summary from an intentional fix subject.

    Only subjects that START like "Fix bug #N:" / "Fixes bug_reports.log #N"
    yield a bug_id. Mid-sentence mentions ("... changelog with bug #25") and
    bare PR refs ("... (#5)") deliberately return bug_id=None so auto-deploy
    does not announce a false "Bug #N has been fixed" world reset.
    """
    summary = subject.strip()
    # Drop trailing "(#123)" PR reference from squash-merge subjects BEFORE
    # any id scan, so a PR number can never become a bug id.
    summary = re.sub(r"\s*\(#\d+\)\s*$", "", summary).strip()

    bug_id = None
    match = _FIX_SUBJECT_RE.match(summary)
    if match:
        bug_id = int(match.group(1))

    if len(summary) > 120:
        summary = summary[:117] + "..."
    return bug_id, summary or "A bug fix has been deployed."


def should_ship_bug_fix(subject: str, *, parent_count: int, file_count: int):
    """Decide whether this tip commit should announce + overlay.

    Pure helper (easy to smoke-test). Returns (ship: bool, reason: str).

    Ship only when ALL of:
      - not a merge commit (parent_count <= 1)
      - subject parses to a real Fix bug #N id
      - the commit actually touches at least one file to overlay
    Otherwise the caller should advance origin/main silently.
    """
    if parent_count > 1:
        return False, "merge commit -- advance silently"
    if subject.strip().lower().startswith("merge "):
        return False, "merge subject -- advance silently"
    bug_id, _summary = parse_deploy_metadata(subject)
    if bug_id is None:
        return False, "not a Fix bug #N subject -- advance silently"
    if file_count <= 0:
        return False, "no files to overlay -- advance silently"
    return True, f"ship bug #{bug_id}"


def _advance_origin_only(root, state, remote_sha, subject, reason):
    """Record the new tip without announcing or overlaying."""
    print(
        f"[auto_deploy] skipping announce for {remote_sha[:12]} "
        f"({reason}): {subject}",
        flush=True,
    )
    state["origin_main"] = remote_sha
    # Do NOT write last_deploy -- that would imply we shipped a fix.
    # Tracking origin_main alone is enough for advance-only gating.
    _save_state(root, state)
    return False


def _wait_for_ready(root, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    ready = _ready_path(root)
    while time.monotonic() < deadline:
        if os.path.isfile(ready):
            try:
                os.remove(ready)
            except OSError:
                pass
            return True
        time.sleep(0.5)
    return False


def _run_deploy_pipeline(root, *, commit_sha, bug_id, summary, countdown, files):
    """Announce in-game, wait, overlay this commit's files only.

    `files` is precomputed by the caller so empty commits never reach
    queue_deploy (announce-before-overlay was the false-positive path for
    merge tips with no file payload).
    """
    from engine import deploy_notify
    from tools.apply_pr_fix import overlay_files_from_ref

    signal = deploy_notify.queue_deploy(
        root,
        pr=commit_sha[:12],
        bug_id=bug_id,
        summary=summary,
        countdown_seconds=countdown,
        triggered_by="engine/auto_deploy.py",
        commit_sha=commit_sha,
    )
    if signal is None:
        print(
            f"[auto_deploy] deploy skipped for {commit_sha[:12]} "
            "(countdown already completed for this commit)",
            flush=True,
        )
        return True

    print(
        f"[auto_deploy] countdown {countdown}s for "
        f"{f'bug #{bug_id}' if bug_id else 'fix'}: {summary}",
        flush=True,
    )
    timeout = DEFAULT_READY_TIMEOUT + countdown
    if not _wait_for_ready(root, timeout):
        print(
            "[auto_deploy] timed out waiting for .deploy_ready -- "
            "is deploy_notify wired in server.py?",
            flush=True,
        )
        return False

    overlay_files_from_ref(commit_sha, files, cwd=root)
    print(
        f"[auto_deploy] overlaid {len(files)} file(s) from {commit_sha[:12]}",
        flush=True,
    )
    from engine.deploy_guard import run_post_overlay_checks
    run_post_overlay_checks()
    return True


def _bootstrap_if_needed(root, state):
    """Record the *checked-out* tip without deploying (first run after upgrade).

    Tracking HEAD (not origin/main) matters: if we stamped the remote tip
    while the working tree still lagged, the next poll would see "already
    current" and never sync. Stamping HEAD lets a later remote advance run
    the normal update path.
    """
    if state.get("origin_main"):
        return state
    try:
        sha = _git("rev-parse", "HEAD", cwd=root)
    except subprocess.CalledProcessError:
        try:
            sha = _origin_main_sha(root)
        except subprocess.CalledProcessError:
            return state
    state["origin_main"] = sha
    _save_state(root, state)
    print(
        f"[auto_deploy] bootstrapped at HEAD {sha[:12]} "
        "(no deploy on first sight; next origin/main advance will sync)",
        flush=True,
    )
    return state


def _working_tree_behind_commit(commit_sha, root):
    """True when checked-out files differ from commit_sha for paths it touched.

    Used only for a one-line manual-recovery hint -- never triggers a deploy.
    Catch-up overlays clobbered local pipeline wiring; recovery is explicit
    via tools/deploy_bug_fix.py --merged.
    """
    from tools.apply_pr_fix import files_in_commit

    files = files_in_commit(commit_sha, cwd=root)
    if not files:
        return False
    try:
        subprocess.run(
            ["git", "diff", "--quiet", commit_sha, "--", *files],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return False
    except subprocess.CalledProcessError:
        return True


def try_auto_deploy():
    """Poll origin/main once; deploy only when the remote SHA advances.

    Called from watch_and_run.py every AUTO_DEPLOY_POLL_SECONDS (default 30).
    Returns True if a deploy ran.

    Advance-only: never re-overlay because the local bind-mount drifted.
    That "catch-up" path rewrote commands.py and wiped webhook/fixbugs
    wiring. Manual recovery: tools/deploy_bug_fix.py --merged.
    """
    if not _enabled():
        return False

    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)

    if not _fetch_origin(root):
        return False

    state = _bootstrap_if_needed(root, _load_state(root))
    try:
        remote_sha = _origin_main_sha(root)
    except subprocess.CalledProcessError:
        return False

    # Never re-run the full deploy pipeline for a commit we already shipped.
    last_deploy_sha = (state.get("last_deploy") or {}).get("sha")
    if last_deploy_sha == remote_sha:
        return False

    prev_sha = state.get("origin_main") or ""
    # Strict advance-only: remote must be a NEW commit vs tracked origin_main.
    if remote_sha == prev_sha:
        # Local files may still lag -- hint once-ish via poll log, never deploy.
        if _working_tree_behind_commit(remote_sha, root):
            print(
                "[auto_deploy] local files differ from origin/main; "
                "run tools/deploy_bug_fix.py --merged to catch up manually",
                flush=True,
            )
        return False

    subject = _commit_subject(remote_sha, root)
    bug_id, summary = parse_deploy_metadata(subject)
    countdown = _countdown_seconds()

    print(
        f"[auto_deploy] origin/main advanced {prev_sha[:12]} "
        f"-> {remote_sha[:12]}: {subject}",
        flush=True,
    )

    # Gate announce/overlay BEFORE queue_deploy so merge subjects that
    # merely mention "bug #N" never broadcast a false world-reset.
    from tools.apply_pr_fix import files_in_commit
    try:
        parent_count = _commit_parent_count(remote_sha, root)
    except subprocess.CalledProcessError:
        parent_count = 1
    files = files_in_commit(remote_sha, cwd=root)
    ship, reason = should_ship_bug_fix(
        subject, parent_count=parent_count, file_count=len(files),
    )
    if not ship:
        # Feature / non-Fix pushes: still update live files, just no countdown.
        try:
            _reset_hard_to(root, remote_sha)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"[auto_deploy] working-tree sync failed: {exc}", flush=True)
            return False
        return _advance_origin_only(root, state, remote_sha, subject, reason)

    queued = _run_deploy_pipeline(
        root,
        commit_sha=remote_sha,
        bug_id=bug_id,
        summary=summary,
        countdown=countdown,
        files=files,
    )
    if not queued:
        return False

    state["origin_main"] = remote_sha
    state["last_deploy"] = {
        "sha": remote_sha,
        "subject": subject,
        "bug_id": bug_id,
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _save_state(root, state)
    return True


def poll_interval_seconds():
    """How often watch_and_run should call try_auto_deploy()."""
    try:
        return max(10, int(os.environ.get("AUTO_DEPLOY_POLL_SECONDS",
                                          DEFAULT_POLL_EVERY)))
    except ValueError:
        return DEFAULT_POLL_EVERY
