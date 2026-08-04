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

When GM turns autodeploy **back on**, a catch-up flag is written so the
next watcher poll does a full ``git reset --hard origin/main`` (protected
live files stashed/restored as usual). That picks up every commit missed
while overlays were paused — not just the tip's Fix-bug file list. Any
``Fix bug #N`` commits in that gap are also queued for in-game resolve
(reporter credit, no Veil countdown — code is already on disk). Ordinary
advance-only polls stay strict (no silent re-overlay when the tracked SHA
already matches); catch-up is only the re-enable path.

State lives in .auto_deploy_state.json (gitignored) so a container restart
does not re-deploy old commits.
"""

from __future__ import annotations

import importlib
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
# Written by GM `autodeploy on` so the next poll syncs the full working tree
# to origin/main (commits missed while the override was off).
CATCHUP_NAME = ".auto_deploy_catchup"

# Defaults; override via environment (see docker-compose.yml).
DEFAULT_POLL_EVERY = 30
DEFAULT_COUNTDOWN = 20
DEFAULT_READY_TIMEOUT = 120
# Cap hung `git fetch` / git-remote-https so a stuck HTTPS helper cannot
# freeze watch_and_run's 1s loop (gateway + game keep running, but the
# supervisor would otherwise stop reaping / hot-reloading / deploying).
# Mid two-repo split: same path will poll SUPERS origin/main later --
# timeout must exist before Phase 5 remotes.
DEFAULT_FETCH_TIMEOUT = 60

# Intentional fix subjects only -- must look like a ship, not a mention.
# Examples that MATCH: "Fix bug #25: list commands alphabetically."
#                      "Fix bugs #79-82: ethereal gear…"
#                      "Fix bugs #57-#60: sit/stand…"
#                      "Fix bugs #79, #80, #82: …"
#                      "Fixes bug_reports.log #12 -- sparring echo text"
# Examples that do NOT: "Merge origin/main: ... with bug #25."
#                       "Enhance auto-deploy (#5)"  (PR number, not bug id)
#                       "Fix overnight Cadence… (#63-#67)" (parenthetical only)
_FIX_SUBJECT_RE = re.compile(
    r"^(?:fix(?:es|ed)?)\s+"
    r"(?:(?:in-game\s+)?bugs?|bug_reports\.log)\s*#?"
    r"(\d+)"
    r"(?:\s*[-–—]\s*#?(\d+))?"
    r"((?:\s*,\s*#?\d+)*)"
    r"\b",
    re.IGNORECASE,
)
# When a Fix subject closes bug #N, also close these duplicate filings.
_BUG_RESOLVE_ALIASES: dict[int, tuple[int, ...]] = {
    # Gary: demesne hub ``down`` + ``beasts`` crash filed twice same session.
    239: (238,),
}
# Non-Fix squash subjects that still shipped a player-visible fix.
_DEPLOY_RESOLVE_SUBJECT_HOOKS: tuple[tuple[re.Pattern[str], tuple[int, ...]], ...] = (
    (re.compile(r"nest dens.*flood", re.IGNORECASE), (243, 244)),
)
# Ship suggestion subjects mirror Fix bug subjects:
#   "Ship suggestion #92: pit auto-look"
#   "Ship suggestions #92-#96: packet"
#   "Shipped ideas #92, #94: …"
_SHIP_SUGGESTION_SUBJECT_RE = re.compile(
    r"^(?:ship(?:ped)?)\s+"
    r"(?:suggestions?|ideas?|suggestions\.log)\s*#"
    r"(\d+)"
    r"(?:\s*[-–—]\s*#?(\d+))?"
    r"((?:\s*,\s*#?\d+)*)"
    r"\b",
    re.IGNORECASE,
)
# Cap range expansion so a typo like #1-9999 cannot flood resolve.
_MAX_BUG_ID_RANGE = 50


def expand_bug_ids_with_aliases(bug_ids) -> list[int]:
    """Return ``bug_ids`` plus any configured duplicate tickets to close."""
    out: list[int] = []
    for raw in bug_ids or []:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n not in out:
            out.append(n)
        for alias in _BUG_RESOLVE_ALIASES.get(n, ()):
            if alias not in out:
                out.append(alias)
    return out


def _expand_fix_subject_bug_ids(start: int, end: int | None, extras: str) -> list[int]:
    """Build an ordered, de-duplicated bug-id list from a Fix subject match.

    ``start`` is the first ``#N``. ``end`` is the optional range end
    (``#79-82`` / ``#57-#60``). ``extras`` is the comma-tail
    (``, #80, #82``). Ranges wider than ``_MAX_BUG_ID_RANGE`` collapse to
    just the two endpoints so a typo cannot mark dozens of tickets.
    """
    ids: list[int] = []

    def _add(n: int) -> None:
        if n not in ids:
            ids.append(n)

    if end is None:
        _add(start)
    else:
        lo, hi = (start, end) if start <= end else (end, start)
        if hi - lo > _MAX_BUG_ID_RANGE:
            _add(start)
            _add(end)
        else:
            for n in range(lo, hi + 1):
                _add(n)
    for chunk in re.findall(r"\d+", extras or ""):
        _add(int(chunk))
    return ids

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


def catchup_path(root=None):
    """Absolute path to the re-enable catch-up request flag."""
    return os.path.join(root or _repo_root(), CATCHUP_NAME)


def catchup_requested(root=None):
    """True when GM `autodeploy on` asked for a full origin/main sync."""
    return os.path.isfile(catchup_path(root))


def request_catchup(root=None):
    """Queue a full working-tree sync on the next successful deploy poll.

    The watcher (not the game child) performs the sync — this only drops a
    flag file the parent reads inside ``try_auto_deploy``.
    """
    path = catchup_path(root)
    with open(path, "w", encoding="utf-8") as f:
        # Timestamp helps ops logs; presence alone triggers the sync.
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
    return path


def clear_catchup(root=None):
    """Remove a pending catch-up flag (after sync, or when turning off)."""
    path = catchup_path(root)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    return path


def set_override(value, root=None, *, queue_catchup=True):
    """Write the GM override file to 'on' or 'off'. Returns the path written.

    Turning **on** also queues a catch-up sync (``request_catchup``) so
    commits that landed while overlays were paused are applied on the next
    watcher poll. Turning **off** clears any pending catch-up flag.

    Pass ``queue_catchup=False`` when restoring a prior override in tests
    so the staging/live tree does not get a spurious catch-up flag.

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
    # Re-enable → full tree catch-up; pause → cancel a pending catch-up.
    if normalized == _OVERRIDE_ON:
        if queue_catchup:
            request_catchup(root)
    else:
        clear_catchup(root)
    return path


def clear_override(root=None):
    """Remove the override file so AUTO_DEPLOY env is the only gate again."""
    path = override_path(root)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    # Dropping the override is not an explicit "on" — leave catch-up alone
    # only if env still enables; if a catch-up was queued from a prior `on`,
    # keep it so the next enabled poll still syncs. (No clear here.)
    return path


def env_enabled():
    """True when AUTO_DEPLOY env says enabled (ignores the override file)."""
    return os.environ.get("AUTO_DEPLOY", "1").strip().lower() not in _FALSEY_ENV


def _enabled():
    """Whether try_auto_deploy should poll this tick.

    GM override file wins when present; otherwise AUTO_DEPLOY env (default on).
    Revert hold (crash recovery) forces off so hotfix / manual patches are not
    wiped by reset --hard while staff clears the hold.
    """
    from engine import crash_recovery

    if crash_recovery.hold_active():
        return False
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
    catchup_line = (
        "Catch-up queued: yes (next poll syncs working tree to origin/main)"
        if catchup_requested()
        else "Catch-up queued: no"
    )
    return (
        f"Auto-deploy effective: {effective}\n"
        f"{override_line}\n"
        f"AUTO_DEPLOY env: {'on' if env_on else 'off'}\n"
        f"{catchup_line}"
    )


def _countdown_seconds():
    try:
        return max(5, int(os.environ.get("AUTO_DEPLOY_COUNTDOWN", DEFAULT_COUNTDOWN)))
    except ValueError:
        return DEFAULT_COUNTDOWN


def fetch_timeout_seconds():
    """Seconds before a hung `git fetch` is killed (AUTO_DEPLOY_FETCH_TIMEOUT).

    Floor at 15 so a slow but healthy pack never races the kill; default 60
    matches DEFAULT_FETCH_TIMEOUT. Set 0 only in tests that want no cap.
    """
    raw = os.environ.get("AUTO_DEPLOY_FETCH_TIMEOUT", str(DEFAULT_FETCH_TIMEOUT))
    try:
        value = int(str(raw).strip())
    except ValueError:
        return DEFAULT_FETCH_TIMEOUT
    if value <= 0:
        return 0
    return max(15, value)


def _kill_process_tree(proc):
    """Kill `proc` and (on Unix) its process group -- git + git-remote-https.

    `subprocess.run(..., timeout=…)` only SIGKILLs the direct child; a hung
    `git-remote-https` sibling/grandchild can linger and fill the PID
    cgroup. We start fetches in a new session so killpg reaches the tree.
    """
    if proc is None or proc.poll() is not None:
        return
    # Unix: process group == session when start_new_session=True.
    if os.name != "nt" and hasattr(os, "killpg"):
        try:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _git_ssh_command_for_key(root, key_name):
    """SSH wrapper for a deploy key under ``root/.secrets`` (container or host path)."""
    key_path = os.path.join(root, ".secrets", key_name).replace("\\", "/")
    return (
        f"ssh -i {key_path} -o IdentitiesOnly=yes "
        f"-o StrictHostKeyChecking=accept-new -o BatchMode=yes"
    )


def _git_env_for(root):
    """Subprocess env for git: deploy key via GIT_SSH_COMMAND, not core.sshCommand.

    ``core.sshCommand`` in the bind-mounted ``.git/config`` is shared with the
    Windows host. A container-only path (``/app/.secrets/…``) breaks host
    ``git push`` / agent worktrees. Auto-deploy and manual in-container fetch
    use ``GIT_SSH_COMMAND`` instead; see ``tools/wire_staging_github_ssh.py``.
    """
    env = os.environ.copy()
    ssh_cmd = (env.get("GIT_SSH_COMMAND") or "").strip()
    if not ssh_cmd:
        key_name, _key_path = _find_deploy_key(root)
        if key_name:
            ssh_cmd = _git_ssh_command_for_key(root, key_name)
    if ssh_cmd:
        env["GIT_SSH_COMMAND"] = ssh_cmd
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def _git(*args, cwd=None):
    root = cwd or os.getcwd()
    return subprocess.check_output(
        ["git", *args],
        cwd=cwd,
        text=True,
        stderr=subprocess.DEVNULL,
        env=_git_env_for(root),
    ).strip()


def _run_git(*args, cwd=None, timeout=None):
    """Run a git subprocess; raise CalledProcessError on non-zero exit.

    Stdout/stderr are captured (not inherited) so a failed fetch can be
    logged with the real git reason -- DEVNULL made live outages look like
    a mysterious exit 128.

    When `timeout` is a positive number of seconds, the child runs in a new
    session (Unix) so a hung `git-remote-https` can be killpg'd. Raises
    ``subprocess.TimeoutExpired`` after killing the tree -- callers that
    must stay best-effort (fetch) catch it; reset --hard usually omits
    timeout because it is local and fast.
    """
    print(f"+ git {' '.join(args)}", flush=True)
    root = cwd or os.getcwd()
    popen_kwargs = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "env": _git_env_for(root),
    }
    # New session only when we need a killable process group (timed fetch).
    if timeout and timeout > 0 and os.name != "nt":
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(["git", *args], **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(
            timeout=timeout if timeout and timeout > 0 else None
        )
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(proc)
        # Drain so the Popen does not leak pipes / zombies.
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout = getattr(exc, "stdout", None) or ""
            stderr = getattr(exc, "stderr", None) or ""
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        # Re-raise with captured output for the fetch logger.
        raise subprocess.TimeoutExpired(
            exc.cmd, exc.timeout, output=stdout, stderr=stderr,
        ) from None

    result = subprocess.CompletedProcess(
        args=["git", *args],
        returncode=proc.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
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


def _iter_protected_live_files(root):
    """Repo-relative paths under protect lists that currently exist on disk.

    Used so ``git reset --hard`` does not wipe live dig / Studio Live Edit
    / GM catalog JSON. Same prefixes as Fix-bug overlays
    (``AUTO_DEPLOY_PROTECT_PREFIXES`` / defaults in ``tools.apply_pr_fix``).
    """
    # Import here so engine boot stays light when auto_deploy is unused.
    from tools.apply_pr_fix import (
        is_protected_path,
        protected_paths,
        protected_prefixes,
    )

    found = []
    for rel in protected_paths():
        norm = rel.replace("\\", "/")
        abs_path = os.path.join(root, *norm.split("/"))
        if os.path.isfile(abs_path):
            found.append(norm)
    for prefix in protected_prefixes():
        norm_prefix = prefix.replace("\\", "/")
        if not norm_prefix:
            continue
        if norm_prefix.endswith("/"):
            abs_dir = os.path.join(root, *norm_prefix.rstrip("/").split("/"))
            if not os.path.isdir(abs_dir):
                continue
            for dirpath, _dirnames, filenames in os.walk(abs_dir):
                for name in filenames:
                    abs_file = os.path.join(dirpath, name)
                    rel = os.path.relpath(abs_file, root).replace("\\", "/")
                    if is_protected_path(rel):
                        found.append(rel)
        else:
            abs_path = os.path.join(root, *norm_prefix.split("/"))
            if os.path.isfile(abs_path):
                found.append(norm_prefix)
    # Stable unique list (walk order can vary).
    return sorted(set(found))


def _stash_protected_live_files(root):
    """Copy protected live files aside before ``git reset --hard``.

    Returns ``(tmpdir, rel_paths)`` or ``None`` when nothing to preserve.
    """
    import shutil
    import tempfile

    files = _iter_protected_live_files(root)
    # Always log count so silent empty protect lists are visible in docker logs.
    print(
        f"[auto_deploy] protect stash: {len(files)} live-authored file(s)",
        flush=True,
    )
    if not files:
        return None
    tmp = tempfile.mkdtemp(prefix="riftforge_protect_")
    for rel in files:
        src = os.path.join(root, *rel.split("/"))
        dst = os.path.join(tmp, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except OSError as exc:
            print(
                f"[auto_deploy] protect stash skipped {rel}: {exc}",
                flush=True,
            )
    return tmp, files


# Protected paths that follow origin/main even under content/maps protect.
# When main deletes these, restore must NOT resurrect the live copy
# (wastes purge: live kept loading Grave Plots because protect restored
# the old wastes.json after every reset --hard).
_MAIN_WINS_PROTECTED_PATHS = frozenset({
    "content/maps/wastes.json",
    "content/npcs/wastes.json",
})


def _restore_protected_live_files(root, stash):
    """Write stashed protected files back after reset; remove the temp dir.

    Catalog JSON (jobs / personas / items) is **additive-merged** with the
    post-reset (origin) file on disk: new keys from ``origin/main`` land,
    live-only GM keys and live leaf edits are kept. Other protected paths
    (npcs, map_backups) still restore with a blind copy. Pipeline
    ``engine/*.py`` modules are not on the default protect list -- they
    stay at the post-reset ``origin/main`` tip.

    Paths in ``_MAIN_WINS_PROTECTED_PATHS`` are never restored: if
    ``origin/main`` deleted them, they stay deleted on live.
    """
    import json
    import shutil

    if not stash:
        return
    tmp, files = stash
    restored = 0
    merged = 0
    dropped = 0
    try:
        from tools.apply_pr_fix import (
            is_additive_catalog_path,
            write_merged_catalog_json,
        )

        for rel in files:
            src = os.path.join(tmp, *rel.split("/"))
            if not os.path.isfile(src):
                continue
            dst = os.path.join(root, *rel.split("/"))
            # Purged maps/rosters: never resurrect from the live stash when
            # origin/main no longer has the path (wastes.json deletion).
            if rel in _MAIN_WINS_PROTECTED_PATHS:
                head_has = False
                try:
                    _run_git(
                        "cat-file", "-e", f"HEAD:{rel}", cwd=root,
                    )
                    head_has = True
                except (subprocess.CalledProcessError, FileNotFoundError):
                    head_has = False
                if not head_has:
                    if os.path.isfile(dst):
                        try:
                            os.remove(dst)
                        except OSError as exc:
                            print(
                                f"[auto_deploy] protect drop failed "
                                f"{rel}: {exc}",
                                flush=True,
                            )
                    dropped += 1
                    print(
                        f"[auto_deploy] protect drop (main wins): {rel}",
                        flush=True,
                    )
                    continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                if is_additive_catalog_path(rel) and os.path.isfile(dst):
                    # dst is origin's version after reset --hard; src is live.
                    with open(dst, encoding="utf-8") as handle:
                        origin_data = json.load(handle)
                    with open(src, encoding="utf-8") as handle:
                        live_data = json.load(handle)
                    write_merged_catalog_json(dst, origin_data, live_data)
                    merged += 1
                    restored += 1
                    print(
                        f"[auto_deploy] additive-merged protected catalog {rel}",
                        flush=True,
                    )
                else:
                    shutil.copy2(src, dst)
                    restored += 1
            except OSError as exc:
                print(
                    f"[auto_deploy] protect restore failed {rel}: {exc}",
                    flush=True,
                )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                # Bad JSON on either side -- fall back to live stash bytes
                # so we never leave a half-written catalog.
                print(
                    f"[auto_deploy] catalog merge failed {rel} ({exc}); "
                    "restoring live copy",
                    flush=True,
                )
                try:
                    shutil.copy2(src, dst)
                    restored += 1
                except OSError as copy_exc:
                    print(
                        f"[auto_deploy] protect restore failed {rel}: "
                        f"{copy_exc}",
                        flush=True,
                    )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    drop_bit = f", dropped {dropped} main-wins" if dropped else ""
    print(
        f"[auto_deploy] restored {restored} protected live-authored "
        f"file(s) after reset --hard ({merged} catalog merge(s)"
        f"{drop_bit})",
        flush=True,
    )
    try:
        from engine import hooks

        heal_stats = hooks.boot_content_heal()
        if heal_stats:
            print(
                f"[auto_deploy] boot_content_heal after protect restore: "
                f"{heal_stats}",
                flush=True,
            )
    except Exception as exc:
        print(
            f"[auto_deploy] boot_content_heal after protect restore failed: "
            f"{exc!r}",
            flush=True,
        )


def _reset_hard_to(root, sha):
    """Move the working tree to sha (feature pushes on Azure / any host).

    Fix-bug deploys still use the narrower overlay path. Non-fix advances
    call this so a push to main actually updates live files, then the
    mtime watcher / copyover picks up the change.

    Live-authored trees under ``AUTO_DEPLOY_PROTECT_PREFIXES`` (npcs,
    catalog JSON, ``content/map_backups/``, …) are snapped aside before
    reset and written back afterward so GM catalog edits and staff map
    snapshots survive silent ``origin/main`` advances. Map/zone JSON
    itself follows ``origin/main`` (use ``gm maps backup`` first).

    When HEAD is already ``sha``, skip the stash/reset/restore cycle —
    re-running protect restore rewrites hundreds of mtimes and can
    copyover-loop the game for no code change (same trap as catch-up).
    """
    ensure_git_safe_directory(root)
    head = _head_sha(root)
    if head and head == sha:
        print(
            f"[auto_deploy] working tree already at {sha[:12]} "
            "(skipping reset --hard + protect restore)",
            flush=True,
        )
        return
    print(f"[auto_deploy] syncing working tree to {sha[:12]}", flush=True)
    stash = _stash_protected_live_files(root)
    try:
        _run_git("reset", "--hard", sha, cwd=root)
    finally:
        _restore_protected_live_files(root, stash)
    # Live populate/dig rooms may exist only in protected map_backups after
    # reset --hard. Additive heal merges missing keys back into zone/map
    # JSON without overwriting git-authored rooms.
    #
    # Important: reload(hooks) clears ``_auto_deploy_map_heal``, and the
    # watcher process never runs supers.bootstrap — so heal used to
    # silently return []. ``auto_deploy_map_heal`` late-binds map_heal
    # after that wipe (see engine.hooks.ensure_auto_deploy_map_heal).
    try:
        import engine.hooks as hooks

        importlib.reload(hooks)
        heal_lines = hooks.auto_deploy_map_heal(root)
        if heal_lines:
            for line in heal_lines:
                print(f"[auto_deploy] {line}", flush=True)
        else:
            print(
                "[auto_deploy] map heal: nothing to merge "
                "(backups already match live, or no map_backups)",
                flush=True,
            )
    except Exception as exc:
        print(f"[auto_deploy] map heal skipped: {exc}", flush=True)


_GITHUB_SSH_ORIGIN = "git@github.com:capnknives/RiftForge.git"
_DEPLOY_KEY_NAMES = (
    "id_ed25519_github_staging",
    "id_ed25519_github",
)


def sanitize_openssh_private_key(material):
    """Return a single LF-only OpenSSH private key block, or None."""
    text = (material or "").replace("\r\n", "\n").replace("\r", "\n")
    match = re.search(
        r"-----BEGIN OPENSSH PRIVATE KEY-----"
        r".*?"
        r"-----END OPENSSH PRIVATE KEY-----",
        text,
        re.DOTALL,
    )
    if not match:
        return None
    return match.group(0).strip() + "\n"


def _normalize_deploy_key_file(path):
    """Strip CR bytes from a bind-mounted deploy key (Windows Docker CRLF).

    OpenSSH inside Linux rejects keys with ``\\r`` in the PEM block
    (``error in libcrypto``). Idempotent when the file is already LF-only.
    Returns True when the file was rewritten.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return False
    if b"\r" not in raw:
        return False
    fixed = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    try:
        with open(path, "wb") as handle:
            handle.write(fixed)
        os.chmod(path, 0o600)
    except OSError:
        return False
    return True


def _find_deploy_key(root):
    """Return ``(key_name, abs_path)`` for the first usable deploy key."""
    for name in _DEPLOY_KEY_NAMES:
        path = os.path.join(root, ".secrets", name)
        if not os.path.isfile(path):
            continue
        _normalize_deploy_key_file(path)
        try:
            with open(path, "rb") as handle:
                head = handle.read(96)
        except OSError:
            continue
        if b"BEGIN OPENSSH PRIVATE KEY" in head:
            return name, path
    return None, None


def ensure_github_ssh_fetch(root):
    """Point ``origin`` at SSH when a deploy key exists; clear stale sshCommand.

    Fetch auth uses ``GIT_SSH_COMMAND`` (``_git_env_for``) so the bind-mounted
    ``.git/config`` is not poisoned with a container-only ``/app/.secrets/…``
    path that breaks Windows host ``git push``. Staging Docker often has deploy
    keys on the bind-mount but ``origin`` still on HTTPS (``gh`` /
    PLAY_CHECKOUT recovery). Auto-heal before fetch so ``gm autodeploy on``
    and ``AUTO_DEPLOY=1`` work without re-running the wire script every time
    the remote URL flips back.
    """
    key_name, _key_path = _find_deploy_key(root)
    if not key_name:
        return False
    try:
        origin = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        current = (origin.stdout or "").strip()
        if not current.startswith("git@"):
            subprocess.run(
                ["git", "remote", "set-url", "origin", _GITHUB_SSH_ORIGIN],
                cwd=root,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(
                f"[auto_deploy] origin -> {_GITHUB_SSH_ORIGIN} "
                f"(deploy key {key_name})",
                flush=True,
            )
        # Legacy wire script / live repair wrote container paths here — unset so
        # host git (worktrees, agents) is not forced through /app/.secrets/….
        cfg = subprocess.run(
            ["git", "config", "--get", "core.sshCommand"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if (cfg.stdout or "").strip():
            subprocess.run(
                ["git", "config", "--unset", "core.sshCommand"],
                cwd=root,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return True


def _is_permanent_fetch_auth_failure(detail):
    """True when git fetch will not self-heal without credential / URL fix."""
    detail_l = (detail or "").lower()
    return (
        "could not read username" in detail_l
        or "permission denied (publickey)" in detail_l
        or "authentication failed" in detail_l
        or "invalid username or password" in detail_l
        or "terminal prompts disabled" in detail_l
    )


def _clear_catchup_on_permanent_fetch_failure(root, detail):
    """Drop a queued catch-up when fetch cannot auth (staging Docker)."""
    if not catchup_requested(root):
        return
    clear_catchup(root)
    print(
        "[auto_deploy] cleared catch-up flag: git fetch cannot authenticate "
        f"in this environment ({detail}). Staging: `git pull --ff-only` on "
        "the host and keep AUTO_DEPLOY=0; live uses deploy keys in "
        ".secrets/id_ed25519_github.",
        flush=True,
    )


def _fetch_origin(root):
    """Best-effort fetch -- offline / corrupt git should not crash the watcher.

    On failure, print git's own stderr (empty object, auth, dubious ownership,
    network). Silent exit-128 skips are how a corrupted live `.git` can stall
    origin/main for hours while AUTO_DEPLOY still looks "on".

    Hung HTTPS (live `git-remote-https` with no exit) used to block
    `watch_and_run` forever because `_run_git` had no timeout -- that freezes
    orphan reaping and further deploys while the gateway/game keep running.
    Timed fetch + killpg returns False so the next poll can try again.
    """
    ensure_git_safe_directory(root)
    ensure_github_ssh_fetch(root)
    timeout = fetch_timeout_seconds()
    try:
        _run_git("fetch", "origin", "main", cwd=root, timeout=timeout or None)
        return True
    except subprocess.TimeoutExpired as exc:
        secs = getattr(exc, "timeout", timeout) or timeout
        print(
            f"[auto_deploy] git fetch timed out after {secs}s "
            "(killed hung git / git-remote-https) -- will retry next poll; "
            "see docs/LIVE_DEPLOY.md (hung git fetch)",
            flush=True,
        )
        return False
    except subprocess.CalledProcessError as exc:
        detail = getattr(exc, "_riftforge_git_detail", None) or str(exc)
        print(f"[auto_deploy] git fetch skipped: {detail}", flush=True)
        detail_l = detail.lower()
        if _is_permanent_fetch_auth_failure(detail):
            _clear_catchup_on_permanent_fetch_failure(root, detail)
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


def _head_sha(root):
    """Checked-out commit (may lag ``origin/main`` after Fix overlays)."""
    try:
        return _git("rev-parse", "HEAD", cwd=root).strip()
    except subprocess.CalledProcessError:
        return ""


def _sync_head_to_tip_if_behind(root, remote_sha, *, reason=""):
    """Move HEAD to the shipped tip when Fix overlays left the ref behind.

    Fix-bug deploys overlay only the changed paths; ``.auto_deploy_state``
    still advances ``origin_main`` to the remote tip. Without this, live
    diagnostics show ``HEAD`` behind ``origin/main`` even though the last
    Fix landed, and any files outside the overlay list stay stale until
    someone runs a manual ``reset --hard``.
    """
    head_sha = _head_sha(root)
    if not head_sha or head_sha == remote_sha:
        return True
    label = f" ({reason})" if reason else ""
    print(
        f"[auto_deploy] HEAD {head_sha[:12]} behind tip {remote_sha[:12]}"
        f" -- full sync{label}",
        flush=True,
    )
    try:
        _reset_hard_to(root, remote_sha)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"[auto_deploy] HEAD sync failed: {exc}", flush=True)
        return False
    return True


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


def _parse_subject_ticket_tail(summary: str, match) -> str:
    """Return the human summary after a Fix/Ship subject prefix match."""
    rest = summary[match.end():].lstrip()
    if rest.startswith(":"):
        rest = rest[1:].lstrip()
    elif rest.startswith(("--", "—", "–")):
        rest = rest.lstrip("-—–").lstrip()
    return rest


def parse_deploy_metadata(subject: str) -> tuple[list[int], list[int], str]:
    """Extract bug/suggestion ids + short summary from a deploy subject.

    Only subjects that START like ``Fix bug #N:`` / ``Ship suggestion #N:``
    yield ids. Mid-sentence mentions and bare PR refs deliberately return
    empty id lists so auto-deploy does not announce a false world reset.

    Returns ``(bug_ids, suggestion_ids, summary)``.
    """
    summary = subject.strip()
    # Drop trailing "(#123)" PR reference from squash-merge subjects BEFORE
    # any id scan, so a PR number can never become a ticket id.
    summary = re.sub(r"\s*\(#\d+\)\s*$", "", summary).strip()

    bug_ids: list[int] = []
    suggestion_ids: list[int] = []
    match = _FIX_SUBJECT_RE.match(summary)
    if match:
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        bug_ids = _expand_fix_subject_bug_ids(start, end, match.group(3) or "")
        rest = _parse_subject_ticket_tail(summary, match)
        summary = rest or "A bug fix has been deployed."
    else:
        match = _SHIP_SUGGESTION_SUBJECT_RE.match(summary)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else None
            suggestion_ids = _expand_fix_subject_bug_ids(
                start, end, match.group(3) or "",
            )
            rest = _parse_subject_ticket_tail(summary, match)
            summary = rest or "A player suggestion has been shipped."

    if len(summary) > 120:
        summary = summary[:117] + "..."
    if bug_ids:
        bug_ids = expand_bug_ids_with_aliases(bug_ids)
        default = "A bug fix has been deployed."
    elif suggestion_ids:
        default = "A player suggestion has been shipped."
    else:
        default = "A bug fix has been deployed."
    return bug_ids, suggestion_ids, summary or default


def should_ship_bug_fix(subject: str, *, parent_count: int, file_count: int):
    """Decide whether this tip commit should announce + overlay.

    Pure helper (easy to smoke-test). Returns (ship: bool, reason: str).

    Ship only when ALL of:
      - not a merge commit (parent_count <= 1)
      - subject parses to at least one Fix bug #N id
      - the commit actually touches at least one file to overlay
    Otherwise the caller should advance origin/main silently.
    """
    if parent_count > 1:
        return False, "merge commit -- advance silently"
    if subject.strip().lower().startswith("merge "):
        return False, "merge subject -- advance silently"
    bug_ids, suggestion_ids, _summary = parse_deploy_metadata(subject)
    if not bug_ids and not suggestion_ids:
        return False, "not a Fix/Ship ticket subject -- advance silently"
    if file_count <= 0:
        return False, "no files to overlay -- advance silently"
    if bug_ids:
        if len(bug_ids) == 1:
            return True, f"ship bug #{bug_ids[0]}"
        return True, f"ship bugs #{bug_ids[0]}-#{bug_ids[-1]}"
    if len(suggestion_ids) == 1:
        return True, f"ship suggestion #{suggestion_ids[0]}"
    return True, (
        f"ship suggestions #{suggestion_ids[0]}-#{suggestion_ids[-1]}"
    )


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


def _run_deploy_pipeline(
    root, *, commit_sha, bug_ids, suggestion_ids, summary, countdown, files,
):
    """Announce in-game, wait, overlay this commit's files only.

    `files` is precomputed by the caller so empty commits never reach
    queue_deploy (announce-before-overlay was the false-positive path for
    merge tips with no file payload). ``bug_ids`` / ``suggestion_ids`` may
    list several tickets for a batch subject so on_resume marks every one
    resolved.
    """
    from tools.apply_pr_fix import overlay_files_from_ref

    deploy_notify = _fresh_deploy_notify()

    primary_bug = bug_ids[0] if bug_ids else None
    primary_suggest = suggestion_ids[0] if suggestion_ids else None
    signal = deploy_notify.queue_deploy(
        root,
        pr=commit_sha[:12],
        bug_id=primary_bug,
        bug_ids=bug_ids,
        suggestion_id=primary_suggest,
        suggestion_ids=suggestion_ids,
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

    label = deploy_notify.describe_ticket_ref(bug_ids, suggestion_ids)
    print(
        f"[auto_deploy] countdown {countdown}s for {label}: {summary}",
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


def _commits_between(from_sha, to_sha, root):
    """Return commit SHAs from ``from_sha`` exclusive through ``to_sha`` inclusive.

    When ``from_sha`` is empty, only ``to_sha`` is considered (first sync).
    """
    if not to_sha:
        return []
    if not from_sha:
        return [to_sha]
    if from_sha == to_sha:
        return []
    out = _git("rev-list", "--reverse", f"{from_sha}..{to_sha}", cwd=root)
    return [line.strip() for line in out.splitlines() if line.strip()]


def _fix_commits_from_shas(root, shas):
    """Parse Fix/Ship subjects from an ordered SHA list."""
    from tools.apply_pr_fix import files_in_commit

    fixes = []
    for sha in shas:
        try:
            subject = _commit_subject(sha, root)
            parent_count = _commit_parent_count(sha, root)
        except subprocess.CalledProcessError:
            continue
        files = files_in_commit(sha, cwd=root)
        ship, _reason = should_ship_bug_fix(
            subject, parent_count=parent_count, file_count=len(files),
        )
        if not ship:
            continue
        bug_ids, suggestion_ids, summary = parse_deploy_metadata(subject)
        if not bug_ids and not suggestion_ids:
            continue
        fixes.append({
            "sha": sha,
            "bug_ids": expand_bug_ids_with_aliases(bug_ids),
            "suggestion_ids": list(suggestion_ids),
            "summary": summary,
        })
    return fixes


def collect_missed_fix_commits(root, from_sha, to_sha):
    """List Fix-bug commits in ``from_sha..to_sha`` not yet announced.

    Each entry is ``{"sha", "bug_ids", "summary"}``. Pure git + parser
    helpers — easy to smoke-test with a real repo history.
    """
    return _fix_commits_from_shas(root, _commits_between(from_sha, to_sha, root))


def _deployed_tip_sha(root):
    """Best SHA for 'what is deployed' when scanning Fix subjects in git history."""
    state_path = os.path.join(root, STATE_NAME)
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        state = {}
    origin = (state.get("origin_main") or "").strip()
    if origin:
        return origin
    last = ((state.get("last_deploy") or {}).get("sha") or "").strip()
    if last:
        return last
    for ref in ("origin/main", "HEAD"):
        try:
            return _git("rev-parse", ref, cwd=root).strip()
        except subprocess.CalledProcessError:
            continue
    return ""


BUG_RESOLVE_CACHE_NAME = ".bug_resolve_cache.json"

# ``git log --grep`` terms for fast Fix/Ship scans (not full ``rev-list``).
_FIX_SHIP_GREP_TERMS = (
    "Fix bug",
    "Fix bugs",
    "Fix in-game",
    "Fixes bug",
    "Ship suggestion",
    "Ship suggestions",
    "Stop nest dens",
)


def git_root_for(directory):
    """Repo checkout that holds ``.git`` for deploy-subject scans.

    On live, ``report_dir`` (beside ``riftforge.db``) is the bind-mounted
    repo root — same path for reports and git. Tests may use a temp report
    dir while git lives in the real checkout.
    """
    if directory and os.path.isdir(os.path.join(directory, ".git")):
        return directory
    return _repo_root()


def _bug_resolve_cache_path(directory):
    return os.path.join(directory, BUG_RESOLVE_CACHE_NAME)


def _load_bug_resolve_cache(directory):
    path = _bug_resolve_cache_path(directory)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"tip_sha": "", "bug_ids": []}
    bug_ids = data.get("bug_ids") or []
    return {
        "tip_sha": (data.get("tip_sha") or "").strip(),
        "bug_ids": [int(x) for x in bug_ids if str(x).isdigit()],
    }


def _save_bug_resolve_cache(directory, *, tip_sha, bug_ids):
    path = _bug_resolve_cache_path(directory)
    payload = {
        "tip_sha": tip_sha,
        "bug_ids": sorted({int(x) for x in bug_ids}),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def _merge_bug_id_list(existing, new_ids):
    out = list(existing or [])
    for raw in new_ids or []:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n not in out:
            out.append(n)
    return out


def _collect_fix_ship_shas(git_root, to_sha, *, from_sha=None):
    """Return SHAs whose subjects may be Fix/Ship deploys (grep, not full history)."""
    if not to_sha:
        return []
    rev_range = to_sha
    if from_sha and from_sha != to_sha:
        rev_range = f"{from_sha}..{to_sha}"
    seen: set[str] = set()
    ordered: list[str] = []
    for term in _FIX_SHIP_GREP_TERMS:
        try:
            out = _git(
                "log",
                "--reverse",
                "--format=%H",
                rev_range,
                f"--grep={term}",
                "--regexp-ignore-case",
                cwd=git_root,
            )
        except subprocess.CalledProcessError:
            continue
        for line in out.splitlines():
            sha = line.strip()
            if sha and sha not in seen:
                seen.add(sha)
                ordered.append(sha)
    return ordered


def _hook_bug_ids_from_shas(git_root, shas):
    ids: list[int] = []
    for sha in shas:
        try:
            subject = _commit_subject(sha, git_root)
        except subprocess.CalledProcessError:
            continue
        for pattern, hook_ids in _DEPLOY_RESOLVE_SUBJECT_HOOKS:
            if not pattern.search(subject):
                continue
            ids = _merge_bug_id_list(ids, hook_ids)
    return ids


def refresh_deployed_bug_id_cache(
    git_root,
    report_directory,
    *,
    full_rebuild=False,
):
    """Update cached deployed Fix-bug ids incrementally (fast boot path)."""
    tip = _deployed_tip_sha(git_root)
    cache = _load_bug_resolve_cache(report_directory)
    bug_ids = list(cache.get("bug_ids") or [])
    cached_tip = cache.get("tip_sha") or ""

    if not tip:
        return bug_ids

    if cached_tip == tip and bug_ids and not full_rebuild:
        return bug_ids

    if full_rebuild or not cached_tip:
        scan_from = None
        if full_rebuild:
            bug_ids = []
    else:
        scan_from = cached_tip

    shas = _collect_fix_ship_shas(git_root, tip, from_sha=scan_from)
    for fix in _fix_commits_from_shas(git_root, shas):
        bug_ids = _merge_bug_id_list(bug_ids, fix.get("bug_ids"))
    bug_ids = _merge_bug_id_list(bug_ids, _hook_bug_ids_from_shas(git_root, shas))
    _save_bug_resolve_cache(report_directory, tip_sha=tip, bug_ids=bug_ids)
    return bug_ids


def collect_all_deployed_fix_commits(root, to_sha=None):
    """List every Fix/Ship commit on ``to_sha`` (grep scan — ops/tooling only)."""
    if to_sha is None:
        to_sha = _deployed_tip_sha(root)
    if not to_sha:
        return []
    shas = _collect_fix_ship_shas(root, to_sha, from_sha=None)
    return _fix_commits_from_shas(root, shas)


def open_bug_ids(directory="."):
    """Return sorted ids of still-open rows in ``bug_reports.log``."""
    from engine import reports

    return sorted(
        entry["id"]
        for entry in reports.recent(reports.BUG, None, directory=directory)
        if entry.get("status", "open") == "open"
    )


def deployed_fix_bug_ids(git_root, report_directory=None, *, full_rebuild=False):
    """De-duplicated bug ids from deployed Fix subjects (cached, incremental)."""
    report_directory = report_directory or git_root
    return refresh_deployed_bug_id_cache(
        git_root,
        report_directory,
        full_rebuild=full_rebuild,
    )


def _catchup_from_sha(state):
    """Oldest tracked tip to scan for missed Fix commits during catch-up."""
    return (
        state.get("origin_main")
        or (state.get("last_deploy") or {}).get("sha")
        or ""
    )


def _fresh_deploy_notify():
    """Reload ``engine.deploy_notify`` from disk before calling it.

    ``watch_and_run`` reloads this module every poll, but an older watcher
    that only reloads ``auto_deploy`` can still hold a stale
    ``deploy_notify`` in memory. Reloading here keeps ``queue_deploy`` /
    ``queue_catchup_resolves`` signatures aligned with the bind-mount even
    when ``watch_and_run.reload_auto_deploy`` itself has not been updated
    yet (chicken-and-egg after a Fix that only patches this file).
    """
    import engine.deploy_notify as deploy_notify

    return importlib.reload(deploy_notify)


def _apply_catchup_fix_resolves(root, state, missed_fixes):
    """Queue in-game resolve for Fix commits already on disk after catch-up."""
    deploy_notify = _fresh_deploy_notify()

    deploy_notify.queue_catchup_resolves(root, missed_fixes)
    for fix in missed_fixes:
        deploy_notify.mark_deploy_completed(root, fix["sha"])

    latest = missed_fixes[-1]
    try:
        subject = _commit_subject(latest["sha"], root)
    except subprocess.CalledProcessError:
        subject = "(catch-up fix)"
    state["last_deploy"] = {
        "sha": latest["sha"],
        "subject": subject,
        "bug_id": latest["bug_ids"][0] if latest.get("bug_ids") else None,
        "bug_ids": list(latest.get("bug_ids") or []),
        "suggestion_id": (
            latest["suggestion_ids"][0]
            if latest.get("suggestion_ids")
            else None
        ),
        "suggestion_ids": list(latest.get("suggestion_ids") or []),
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    all_bug_ids = []
    all_suggest_ids = []
    for fix in missed_fixes:
        for bug_id in fix.get("bug_ids") or []:
            if bug_id not in all_bug_ids:
                all_bug_ids.append(bug_id)
        for suggestion_id in fix.get("suggestion_ids") or []:
            if suggestion_id not in all_suggest_ids:
                all_suggest_ids.append(suggestion_id)
    label = deploy_notify.describe_ticket_ref(all_bug_ids, all_suggest_ids)
    print(
        f"[auto_deploy] catch-up queued resolve for {label} "
        f"({len(missed_fixes)} Fix commit(s))",
        flush=True,
    )


def record_catchup_last_deploy(root, fix, *, subject=None):
    """Persist ``last_deploy`` after in-game catch-up resolve (copyover)."""
    state = _load_state(root)
    if subject is None:
        try:
            subject = _commit_subject(fix["sha"], root)
        except subprocess.CalledProcessError:
            subject = "(catch-up fix)"
    state["last_deploy"] = {
        "sha": fix["sha"],
        "subject": subject,
        "bug_id": fix["bug_ids"][0] if fix.get("bug_ids") else None,
        "bug_ids": list(fix.get("bug_ids") or []),
        "suggestion_id": (
            fix["suggestion_ids"][0] if fix.get("suggestion_ids") else None
        ),
        "suggestion_ids": list(fix.get("suggestion_ids") or []),
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _save_state(root, state)


def _run_reenable_catchup(root, state, remote_sha):
    """Full working-tree sync after GM ``autodeploy on`` (missed commits).

    Uses the same ``reset --hard`` + protect stash path as a silent feature
    advance — not a tip-only Fix overlay — so multi-commit gaps while the
    toggle was off do not leave intermediate files behind. Missed ``Fix bug
    #N`` subjects in that range are handed to deploy_notify for resolve +
    reporter credit (no Veil countdown — the tree is already current).
    """
    from_sha = _catchup_from_sha(state)
    subject = "(unknown)"
    try:
        subject = _commit_subject(remote_sha, root)
    except subprocess.CalledProcessError:
        pass
    print(
        f"[auto_deploy] catch-up after re-enable: syncing working tree to "
        f"{remote_sha[:12]} ({subject})",
        flush=True,
    )
    try:
        head_sha = _git("rev-parse", "HEAD", cwd=root).strip()
    except subprocess.CalledProcessError:
        head_sha = ""
    # Already at tip: skip reset --hard. Re-running it still rewrites
    # protected restore mtimes and copyovers the game for no code change.
    if head_sha == remote_sha:
        print(
            f"[auto_deploy] catch-up tree already at {remote_sha[:12]} "
            "(skipping reset --hard)",
            flush=True,
        )
    else:
        try:
            _reset_hard_to(root, remote_sha)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"[auto_deploy] catch-up sync failed: {exc}", flush=True)
            # Leave the flag so the next poll retries.
            return False

    # Tree is at tip. Always clear the catch-up flag + advance tracked SHA
    # even if resolve queueing fails -- otherwise every poll re-runs
    # reset --hard + protect restore, which rewrites mtimes and copyover-
    # loops the game forever under a stale deploy_notify (AttributeError
    # on queue_catchup_resolves / unexpected kwarg bug_ids).
    missed_fixes = collect_missed_fix_commits(root, from_sha, remote_sha)
    if missed_fixes:
        try:
            _apply_catchup_fix_resolves(root, state, missed_fixes)
        except Exception as exc:
            print(
                f"[auto_deploy] catch-up resolve queue failed "
                f"(tree already synced; clearing catch-up flag): {exc}",
                flush=True,
            )

    state["origin_main"] = remote_sha
    _save_state(root, state)
    clear_catchup(root)
    print(
        f"[auto_deploy] catch-up complete at {remote_sha[:12]}",
        flush=True,
    )
    return True


def try_auto_deploy():
    """Poll origin/main once; deploy only when the remote SHA advances.

    Called from watch_and_run.py every AUTO_DEPLOY_POLL_SECONDS (default 30).
    Returns True if a deploy ran.

    Advance-only: never re-overlay because the local bind-mount drifted.
    That "catch-up" path rewrote commands.py and wiped webhook/fixbugs
    wiring. Manual recovery: tools/deploy_bug_fix.py --merged.

    Exception: GM ``autodeploy on`` queues ``.auto_deploy_catchup`` so the
    next poll does one full ``reset --hard`` to origin/main (commits missed
    while overlays were off). That is intentional and flag-gated — not the
    old "files differ from tracked SHA" auto path.
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

    # Re-enable catch-up runs before advance-only gates so a paused host
    # that fell behind by many commits always gets a full tree sync.
    if catchup_requested(root):
        return _run_reenable_catchup(root, state, remote_sha)

    # Never re-run the full deploy pipeline for a commit we already shipped.
    last_deploy_sha = (state.get("last_deploy") or {}).get("sha")
    if last_deploy_sha == remote_sha:
        return False

    prev_sha = state.get("origin_main") or ""
    # Strict advance-only: remote must be a NEW commit vs tracked origin_main.
    if remote_sha == prev_sha:
        # Fix overlays advance state without moving HEAD -- self-heal on the
        # next poll so agents are not fooled by HEAD != origin/main.
        head_sha = _head_sha(root)
        if head_sha and head_sha != remote_sha:
            return _sync_head_to_tip_if_behind(
                root,
                remote_sha,
                reason="tracked tip matches origin/main",
            )
        # Local files may still lag -- hint once-ish via poll log, never deploy.
        if _working_tree_behind_commit(remote_sha, root):
            print(
                "[auto_deploy] local files differ from origin/main; "
                "run tools/deploy_bug_fix.py --merged to catch up manually",
                flush=True,
            )
        return False

    subject = _commit_subject(remote_sha, root)
    bug_ids, suggestion_ids, summary = parse_deploy_metadata(subject)
    countdown = _countdown_seconds()
    missed_fixes = collect_missed_fix_commits(root, prev_sha, remote_sha)

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
        # A feature tip can land in the same poll batch as Fix commits
        # underneath it -- resolve those tickets even without a Veil countdown.
        if missed_fixes:
            try:
                _apply_catchup_fix_resolves(root, state, missed_fixes)
            except Exception as exc:
                print(
                    f"[auto_deploy] missed-fix catch-up failed: {exc}",
                    flush=True,
                )
        return _advance_origin_only(root, state, remote_sha, subject, reason)

    # Multi-commit gaps: only the tip Fix gets the countdown; earlier Fix
    # subjects in the same batch still need reporter credit + resolved status.
    earlier_fixes = [fix for fix in missed_fixes if fix["sha"] != remote_sha]
    if earlier_fixes:
        try:
            _apply_catchup_fix_resolves(root, state, earlier_fixes)
        except Exception as exc:
            print(
                f"[auto_deploy] earlier-fix catch-up failed: {exc}",
                flush=True,
            )

    queued = _run_deploy_pipeline(
        root,
        commit_sha=remote_sha,
        bug_ids=bug_ids,
        suggestion_ids=suggestion_ids,
        summary=summary,
        countdown=countdown,
        files=files,
    )
    if not queued:
        return False

    if not _sync_head_to_tip_if_behind(
        root, remote_sha, reason="after Fix overlay",
    ):
        return False

    state["origin_main"] = remote_sha
    state["last_deploy"] = {
        "sha": remote_sha,
        "subject": subject,
        "bug_id": bug_ids[0] if bug_ids else None,
        "bug_ids": list(bug_ids),
        "suggestion_id": suggestion_ids[0] if suggestion_ids else None,
        "suggestion_ids": list(suggestion_ids),
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
