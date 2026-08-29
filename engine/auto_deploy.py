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

Announce policy (player-facing countdown):
  - Intentional Fix / Ship suggestion subjects (``Fix bug #N: …``) get the
    ticket Veil countdown, then a tip-file overlay.
  - Feature / merge / other tip advances and GM ``autodeploy on`` catch-up
    still sync the working tree, but first queue a catch-up Veil countdown
    (default 60s) so players know a copyover is coming — gothic rewrite
    language without fake Bug #N chrome.
  - Subjects that merely *mention* ``bug #N`` mid-sentence never use the
    Fix announce path.

No manual `tools/deploy_bug_fix.py` step. Disable with AUTO_DEPLOY=0, or
toggle live with GM `autodeploy on|off` (writes `.auto_deploy_override`).

When GM turns autodeploy **back on**, a catch-up flag is written so the
next watcher poll does a full ``git reset --hard origin/main`` (protected
live files stashed/restored as usual). That picks up every commit missed
while overlays were paused — not just the tip's Fix-bug file list. Any
``Fix bug #N`` commits in that gap are also queued for in-game resolve
(reporter credit) after the catch-up countdown + tree sync. Ordinary
advance-only polls stay strict (no silent re-overlay when the tracked SHA
already matches); catch-up is only the re-enable path.

GM ``autodeploy now`` is the staff flush: enable polls, skip the Lag P13
merge-quiet debounce (2–10 min), and catch up on the next watcher tick
(~1s) with a short Veil (5s) instead of the 30s rewrite countdown.

State lives in .auto_deploy_state.json (gitignored) so a container restart
does not re-deploy old commits.

Lag P11: successful tree reset / overlay also appends one always-on
``auto_deploy_reset`` NDJSON row to the diag log (``engine.diag_export``)
so ``gm diaglog analyze`` can correlate deploy I/O with autosave stalls.

Lag P13: when ``RIFTFORGE_AUTO_DEPLOY_BATCH_QUIET_S`` and/or
``RIFTFORGE_AUTO_DEPLOY_BATCH_MAX_S`` are set (docker-compose defaults
120 / 600), merges debounce across watcher polls into one deploy instead
of one Veil pause per PR. First pickup writes a heads-up file so the
game can OOC from Ash(GM) a few minutes before the Veil countdown.
``gm autodeploy abort`` cancels a queued batch / catch-up / in-flight
countdown (``autodeploy off`` only pauses future polls). Legacy
``RIFTFORGE_AUTO_DEPLOY_COALESCE_S`` still works when batch is off.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request


# Lag P11 -- last watcher poll / deploy event (monotonic). Used to stamp
# poll_gap_s and since_prev_deploy_s on diag NDJSON rows. Reset on reload.
_last_poll_monotonic = None
_current_poll_gap_s = None
_last_deploy_event_monotonic = None
_last_fetch_skip_log = (0.0, "")
_FETCH_SKIP_LOG_INTERVAL_S = 300.0


def _log_git_fetch_skipped(detail, *, root):
    """Rate-limit identical git fetch failure spam in Docker logs."""
    global _last_fetch_skip_log
    now = time.time()
    prev_at, prev_detail = _last_fetch_skip_log
    if detail == prev_detail and (now - prev_at) < _FETCH_SKIP_LOG_INTERVAL_S:
        return
    _last_fetch_skip_log = (now, detail)
    print(f"[auto_deploy] git fetch skipped: {detail}", flush=True)
    detail_l = detail.lower()
    if _is_permanent_fetch_auth_failure(detail):
        _clear_catchup_on_permanent_fetch_failure(root, detail)
    if "empty" in detail_l or "bad object" in detail_l:
        print(
            "[auto_deploy] hint: live .git may have empty loose objects -- "
            "see docs/LIVE_DEPLOY.md (Repair corrupted .git)",
            flush=True,
        )
    if "cannot fork" in detail_l or "resource temporarily unavailable" in detail_l:
        print(
            "[auto_deploy] hint: container PID limit / git zombies -- "
            "docker compose restart; see docs/LIVE_DEPLOY.md "
            "(cannot fork / high PIDS)",
            flush=True,
        )


STATE_NAME = ".auto_deploy_state.json"
READY_NAME = ".deploy_ready"
# GM `autodeploy on|off` writes this so watch_and_run (parent process) sees the
# toggle -- mutating os.environ inside server.py would not affect the watcher.
OVERRIDE_NAME = ".auto_deploy_override"
# Written by GM `autodeploy on` so the next poll syncs the full working tree
# to origin/main (commits missed while the override was off).
CATCHUP_NAME = ".auto_deploy_catchup"
# GM ``autodeploy now`` — watcher polls on the next 1s tick instead of waiting
# for AUTO_DEPLOY_POLL_SECONDS, and catch-up uses the short Veil.
IMMEDIATE_NAME = ".auto_deploy_immediate"
# After an in-game Veil countdown, skip COPYOVER_SETTLE_SECONDS — players
# were already warned; the watcher should SIGUSR1 as soon as the tree sync
# lands (not another ~30s of playable lag).
ANNOUNCED_COPYOVER_NAME = ".deploy_announced_copyover"
# Touched right before git reset/overlay; game tick broadcasts once.
TREE_SYNCING_NAME = ".deploy_tree_syncing"
# Watcher touches this briefly before killing gateway (safety net if countdown missed).
DISCONNECT_IMMINENT_NAME = ".deploy_disconnect_imminent"
# Same moment, but read by the gateway hold loop when the game child is already down.
GATEWAY_DISCONNECT_IMMINENT_NAME = ".gateway_disconnect_imminent"
# Wall-clock stamp after a successful tree sync reset (lag P12: autosave skip).
DEPLOY_RESET_AT_NAME = ".auto_deploy_reset_at"

# Lag P12: legacy fixed sleep after first advance (0 = off; ignored when batch on).
DEPLOY_COALESCE_ENV = "RIFTFORGE_AUTO_DEPLOY_COALESCE_S"
# Lag P13: debounced batch — wait until merges go quiet, with a max cap (0 = off).
BATCH_QUIET_ENV = "RIFTFORGE_AUTO_DEPLOY_BATCH_QUIET_S"
BATCH_MAX_ENV = "RIFTFORGE_AUTO_DEPLOY_BATCH_MAX_S"
BATCH_PENDING_NAME = ".auto_deploy_batch_pending.json"
# Game tick OOCs from Ash(GM) when the watcher first queues a debounce batch.
HEADS_UP_NAME = ".auto_deploy_heads_up.json"
# GM ``autodeploy abort`` — watcher _wait_for_ready bails; same tip is held.
ABORT_NAME = ".auto_deploy_abort.json"
# Lag P12: skip scheduled autosaves for N seconds after deploy reset (0 = off).
AUTOSAVE_SKIP_AFTER_DEPLOY_ENV = "RIFTFORGE_AUTOSAVE_SKIP_AFTER_DEPLOY_S"

# Defaults; override via environment (see docker-compose.yml / .env.example).
DEFAULT_POLL_EVERY = 30
DEFAULT_COUNTDOWN = 20
# Feature / catch-up tree syncs: longer warning before the mtime copyover.
# Feature / catch-up tree syncs: warn before mtime copyover (was 60 -- felt
# like a second full hold after Fix ships; 30s is enough gothic warning).
DEFAULT_CATCHUP_COUNTDOWN = 30
# ``gm autodeploy now`` — skip the 2–10 min debounce; keep a brief Veil so
# players are not silently copyover'd. Matches the catch-up helper floor.
DEFAULT_IMMEDIATE_COUNTDOWN = 5
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
# Comma / Oxford-list tails on Fix and Ship subjects:
# ``#79, #80``, ``#154, #179, and #189``, ``179, 154, and 189``.
_TICKET_ID_COMMA_TAIL_RE = r"((?:\s*,\s*(?:and\s+)?#?\d+)*)"
_TICKET_ID_COMMA_TAIL_BARE_RE = r"((?:\s*,\s*(?:and\s+)?\d+)*)"
_FIX_SUBJECT_RE = re.compile(
    r"^(?:fix(?:es|ed)?)\s+"
    r"(?:(?:in-game\s+)?bugs?|bug_reports\.log)\s*#?"
    r"(\d+)"
    r"(?:\s*[-–—]\s*#?(\d+))?"
    rf"{_TICKET_ID_COMMA_TAIL_RE}"
    r"\b",
    re.IGNORECASE,
)
# Agent squash subjects: ``Fix bug report 398`` (no ``#`` — GitHub autolink trap on commits too).
_FIX_BUG_REPORT_SUBJECT_RE = re.compile(
    r"^(?:fix(?:es|ed)?)\s+"
    r"bug\s+reports?\s+"
    r"#?(\d+)"
    r"(?:\s*[-–—]\s*#?(\d+))?"
    rf"{_TICKET_ID_COMMA_TAIL_RE}"
    r"(?:\b|:)",
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
    rf"{_TICKET_ID_COMMA_TAIL_RE}"
    r"\b",
    re.IGNORECASE,
)
# Agent PR bodies: ``Ships suggestion report 54`` (no ``#``).
_SHIP_SUGGESTION_REPORT_SUBJECT_RE = re.compile(
    r"^(?:ship(?:ped|s)?)\s+"
    r"suggestion\s+reports?\s+"
    r"#?(\d+)"
    r"(?:\s*[-–—]\s*#?(\d+))?"
    rf"{_TICKET_ID_COMMA_TAIL_RE}"
    r"(?:\b|:)",
    re.IGNORECASE,
)
# Hash-free idea ranges / lists without ``#`` (agent squash subjects):
# ``Ship ideas 191-195: autoloot preset``
# ``Ship suggestions 179, 154, and 189 tip retune: …``
# Must run before slash subjects — ``[\d/]+`` would steal a lone leading id.
_SHIP_SUGGESTION_BARE_RANGE_RE = re.compile(
    r"^(?:ship(?:ped|s)?)\s+"
    r"(?:player\s+)?(?:suggestions?|ideas?)\s+"
    r"(\d+)"
    r"(?:\s*[-–—]\s*(\d+))?"
    rf"{_TICKET_ID_COMMA_TAIL_BARE_RE}"
    r"\b",
    re.IGNORECASE,
)
# Squash bodies that list ids with slashes (agent mistake on PR 2218):
# ``Ship player suggestions 164/169/122: …``
_SHIP_PLAYER_SUGGESTIONS_SLASH_RE = re.compile(
    r"^(?:ship(?:ped|s)?)\s+"
    r"(?:player\s+)?(?:suggestions?|ideas?)\s+"
    r"(\d+(?:/\d+)+)"
    r"(?:\b|:)",
    re.IGNORECASE,
)
# Squash-merge subjects often end with the GitHub PR number, not the bug id.
_MERGED_PR_REF_RE = re.compile(r"\(#(\d+)\)\s*$")
_GITHUB_PULL_API = "https://api.github.com/repos/capnknives/RiftForge/pulls"
_PR_TEXT_CACHE: dict[int, str] = {}
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


def _expand_slash_subject_ids(slash_blob: str) -> list[int]:
    """Build ids from ``164/169/122`` tails on Ship suggestion subjects."""
    ids: list[int] = []
    for chunk in re.split(r"[/\s]+", (slash_blob or "").strip()):
        if not chunk or not chunk.isdigit():
            continue
        n = int(chunk)
        if n not in ids:
            ids.append(n)
    return ids


def parse_ticket_id_blob(blob: str, *, max_range: int | None = None) -> list[int]:
    """Expand hash-free ticket id tokens from PR/squash prose.

    Supports ``191-195``, ``191, 192``, ``164/169/122``, and lone ``92``.
    Used by ``tools/open_pr.py`` body validation and agent helpers.
    """
    cap = _MAX_BUG_ID_RANGE if max_range is None else max_range
    text = (blob or "").strip().replace("#", "")
    text = re.sub(r"[.,;:]+$", "", text)
    if not text:
        return []
    if "/" in text:
        return _expand_slash_subject_ids(text)
    match = re.match(
        rf"^(\d+)(?:\s*[-–—]\s*(\d+))?{_TICKET_ID_COMMA_TAIL_BARE_RE}$",
        text,
    )
    if match:
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        ids = _expand_fix_subject_bug_ids(start, end, match.group(3) or "")
        if end is not None and len(ids) > cap + 1:
            return [start, end]
        return ids
    ids: list[int] = []
    for chunk in re.findall(r"\d+", text):
        n = int(chunk)
        if n not in ids:
            ids.append(n)
    return ids

# Values accepted in the override file / GM command (normalized to these).
_OVERRIDE_ON = "on"
_OVERRIDE_OFF = "off"
_FALSEY_ENV = ("0", "false", "no", "off")


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_seconds(name):
    """Parse a non-negative integer seconds knob from the environment."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return 0


def coalesce_seconds():
    """Extra seconds to wait after detecting origin/main advance (0 = off)."""
    return _env_seconds(DEPLOY_COALESCE_ENV)


def batch_quiet_seconds():
    """Seconds of quiet origin/main after the last merge before deploy (0 = off)."""
    return _env_seconds(BATCH_QUIET_ENV)


def batch_max_seconds():
    """Max seconds from the first queued merge before deploy is forced (0 = no cap)."""
    return _env_seconds(BATCH_MAX_ENV)


def batch_debounce_enabled():
    """True when debounced batching replaces immediate deploy / legacy coalesce."""
    return batch_quiet_seconds() > 0 or batch_max_seconds() > 0


def autosave_skip_after_deploy_seconds():
    """Skip scheduled autosaves for this many seconds after a tree sync reset."""
    raw = os.environ.get(AUTOSAVE_SKIP_AFTER_DEPLOY_ENV, "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return 0


def _deploy_reset_at_path(root):
    return os.path.join(root, DEPLOY_RESET_AT_NAME)


def record_deploy_reset_wall(root=None):
    """Stamp wall time when a deploy reset finishes (game reads for autosave skip)."""
    root = root or _repo_root()
    try:
        with open(_deploy_reset_at_path(root), "w", encoding="utf-8") as fh:
            json.dump({"wall": time.time()}, fh)
    except OSError as exc:
        print(f"[auto_deploy] deploy reset stamp skipped: {exc!r}", flush=True)


def seconds_since_deploy_reset(root=None):
    """Seconds since the last tree sync reset stamp, or None if unknown."""
    root = root or _repo_root()
    try:
        with open(_deploy_reset_at_path(root), encoding="utf-8") as fh:
            payload = json.load(fh)
        wall = float(payload.get("wall") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if wall <= 0:
        return None
    return time.time() - wall


def deploy_save_defer_status(root=None):
    """Return (blocked, since_s, window_s) for post-deploy save deferral.

    ``since_s`` is seconds since the last deploy stamp when known, else None.
    """
    root = root or _repo_root()
    window = autosave_skip_after_deploy_seconds()
    if window <= 0:
        return False, None, window
    since = seconds_since_deploy_reset(root)
    if since is None:
        return False, since, window
    return since < window, since, window


def autosave_blocked_by_recent_deploy(root=None):
    """True when a scheduled autosave should defer until deploy window passes."""
    blocked, _since, _window = deploy_save_defer_status(root)
    return blocked


def _coalesce_remote_sha(root, remote_sha):
    """Wait for a merge burst, then return the latest origin/main tip."""
    delay = coalesce_seconds()
    if delay <= 0 or not remote_sha:
        return remote_sha
    initial = remote_sha
    print(
        f"[auto_deploy] coalesce waiting {delay}s before sync "
        f"(tip {initial[:12]})",
        flush=True,
    )
    time.sleep(delay)
    if not _fetch_origin(root):
        print(
            "[auto_deploy] coalesce fetch failed; using pre-wait tip",
            flush=True,
        )
        return initial
    try:
        tip = _origin_main_sha(root)
    except subprocess.CalledProcessError:
        return initial
    if tip != initial:
        print(
            f"[auto_deploy] coalesce caught advance "
            f"{initial[:12]} -> {tip[:12]}",
            flush=True,
        )
    return tip


def _batch_pending_path(root):
    return os.path.join(root, BATCH_PENDING_NAME)


def _load_batch_pending(root):
    path = _batch_pending_path(root)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("synced_sha") or not data.get("tip_sha"):
        return None
    return data


def _save_batch_pending(root, data):
    path = _batch_pending_path(root)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def clear_batch_pending(root=None):
    """Drop debounce state after deploy or explicit catch-up."""
    path = _batch_pending_path(root or _repo_root())
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def heads_up_path(root=None):
    """Absolute path to the Ash(GM) OOC heads-up hand-off file."""
    return os.path.join(root or _repo_root(), HEADS_UP_NAME)


def _load_heads_up(root=None):
    """Return the pending/announced heads-up payload, or None."""
    path = heads_up_path(root)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("tip_sha"):
        return None
    return data


def _save_heads_up(root, data):
    """Persist the heads-up payload for the game tick to OOC."""
    path = heads_up_path(root)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def clear_heads_up(root=None):
    """Drop a queued or announced heads-up (after abort or deploy)."""
    path = heads_up_path(root or _repo_root())
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def queue_heads_up(root, tip_sha):
    """Ask the game to OOC from Ash(GM) that a rewrite is queued.

    Called when the debounce batch is first armed, and again if a later
    tip in the same batch flips to a gateway restart (follow-up OOC).
    Returns the payload written, or the existing one when unchanged.
    """
    sha = (tip_sha or "").strip()
    if not sha:
        return None
    gateway = False
    try:
        gateway = bool(_advance_touches_gateway(root, sha))
    except Exception:
        gateway = False
    existing = _load_heads_up(root)
    follow_up = False
    phase = "pending"
    if existing:
        same_tip = (existing.get("tip_sha") or "") == sha
        was_gateway = bool(existing.get("gateway_restart"))
        if same_tip and was_gateway == gateway:
            return existing
        # Same batch, but the new tip now drops clients — second OOC.
        if was_gateway is False and gateway:
            follow_up = True
        else:
            # Tip advanced within the same debounce batch — refresh tip_sha
            # without re-arming Ash(GM); players already heard the heads-up.
            phase = existing.get("phase") or "pending"
    payload = {
        "phase": phase,
        "tip_sha": sha,
        "gateway_restart": gateway,
        "follow_up": follow_up,
        "queued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "quiet_s": batch_quiet_seconds(),
        "max_s": batch_max_seconds(),
    }
    _save_heads_up(root, payload)
    print(
        f"[auto_deploy] heads-up queued for {sha[:12]} "
        f"gateway_restart={gateway} follow_up={follow_up}",
        flush=True,
    )
    return payload


def abort_path(root=None):
    """Absolute path to the GM abort-hold file."""
    return os.path.join(root or _repo_root(), ABORT_NAME)


def load_abort(root=None):
    """Return the abort-hold payload, or None."""
    path = abort_path(root)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def abort_requested(root=None):
    """True when GM abort is signalling an in-flight countdown to stop."""
    return load_abort(root) is not None


def abort_holds_tip(root, remote_sha):
    """True when this origin/main tip was aborted and should not re-queue."""
    data = load_abort(root)
    if not data:
        return False
    held = (data.get("tip_sha") or "").strip()
    want = (remote_sha or "").strip()
    return bool(held and want and held == want)


def clear_abort(root=None):
    """Drop the abort hold (new commit, explicit catch-up, or successful ship)."""
    path = abort_path(root or _repo_root())
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def abort_planned_deploy(root=None, *, actor="unknown"):
    """Cancel a queued batch, catch-up flag, heads-up, and Veil hand-off.

    Does **not** toggle autodeploy on/off. Writes ``.auto_deploy_abort.json``
    so ``_wait_for_ready`` bails and the next poll will not immediately
    re-queue the same tip (until a new commit or ``gm autodeploy on`` /
    ``gm deploy sync``).

    Returns ``(cancelled_labels, payload)``. Empty labels means nothing
    was planned.
    """
    root = root or _repo_root()
    cancelled = []
    pending = _load_batch_pending(root)
    heads = _load_heads_up(root)
    from engine import deploy_notify

    signal = deploy_notify._read_signal(root)
    tip = (
        (pending or {}).get("tip_sha")
        or (heads or {}).get("tip_sha")
        or (signal or {}).get("commit_sha")
        or (signal or {}).get("deploy_key")
        or ""
    )
    heads_announced = bool(heads and heads.get("phase") == "announced")
    if pending:
        clear_batch_pending(root)
        cancelled.append("batch")
    if catchup_requested(root):
        clear_catchup(root)
        cancelled.append("catch-up flag")
    if immediate_requested(root):
        clear_immediate(root)
        cancelled.append("immediate catch-up")
    if heads:
        clear_heads_up(root)
        cancelled.append("heads-up")
    if signal or os.path.isfile(deploy_notify.ready_path(root)):
        cancelled.append("veil countdown")
    deploy_notify._cleanup(root)
    if not cancelled:
        return [], {}
    payload = {
        "aborted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actor": actor,
        "tip_sha": (tip or "").strip(),
        "heads_announced": heads_announced,
    }
    path = abort_path(root)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print(
        f"[auto_deploy] planned deploy aborted by {actor}: "
        f"{', '.join(cancelled)} tip={(tip or '')[:12]}",
        flush=True,
    )
    return cancelled, payload


def batch_status_lines(root=None):
    """Human-readable debounce queue for GM ``autodeploy`` / ``deploy status``."""
    root = root or _repo_root()
    if not batch_debounce_enabled():
        return [f"Deploy batch: off (set {BATCH_QUIET_ENV} / {BATCH_MAX_ENV})"]
    pending = _load_batch_pending(root)
    quiet = batch_quiet_seconds()
    max_wait = batch_max_seconds()
    if not pending:
        return [
            "Deploy batch: idle",
            f"  quiet={quiet}s max={max_wait}s",
        ]
    now = time.time()
    quiet_left = max(0.0, quiet - (now - pending.get("last_seen_wall", now)))
    max_left = (
        max(0.0, max_wait - (now - pending.get("first_seen_wall", now)))
        if max_wait > 0
        else None
    )
    tip = (pending.get("tip_sha") or "")[:12]
    lines = [
        "Deploy batch: queued",
        f"  tip: {tip}",
        f"  quiet={quiet}s (≈{int(quiet_left)}s left)",
    ]
    if max_left is not None:
        lines.append(f"  max={max_wait}s (≈{int(max_left)}s left)")
    return lines


def _maybe_log_batch_waiting(root, pending, *, reason=""):
    """Throttle waiting logs to once per poll interval."""
    now = time.time()
    last_log = float(pending.get("last_log_wall") or 0.0)
    if now - last_log < max(10, poll_interval_seconds() - 1):
        return
    pending["last_log_wall"] = now
    _save_batch_pending(root, pending)
    quiet = batch_quiet_seconds()
    max_wait = batch_max_seconds()
    quiet_elapsed = now - float(pending.get("last_seen_wall") or now)
    age = now - float(pending.get("first_seen_wall") or now)
    suffix = f" ({reason})" if reason else ""
    print(
        f"[auto_deploy] batch waiting{suffix} tip "
        f"{(pending.get('tip_sha') or '')[:12]} "
        f"quiet {quiet_elapsed:.0f}/{quiet}s age {age:.0f}/"
        f"{max_wait or '∞'}s",
        flush=True,
    )


def _batch_deploy_gate(root, synced_sha, remote_sha):
    """Debounce merges across polls; return ``(tip_sha, ready_to_deploy)``."""
    now = time.time()
    quiet = batch_quiet_seconds()
    max_wait = batch_max_seconds()
    pending = _load_batch_pending(root)

    if pending and pending.get("synced_sha") != synced_sha:
        clear_batch_pending(root)
        pending = None

    if pending and pending.get("tip_sha") != remote_sha:
        pending["tip_sha"] = remote_sha
        pending["last_seen_wall"] = now
        _save_batch_pending(root, pending)
        _maybe_log_batch_waiting(root, pending, reason="tip advanced")
        queue_heads_up(root, remote_sha)
    elif not pending:
        try:
            subject = _commit_subject(remote_sha, root)
        except subprocess.CalledProcessError:
            subject = "(unknown)"
        pending = {
            "synced_sha": synced_sha,
            "tip_sha": remote_sha,
            "first_seen_wall": now,
            "last_seen_wall": now,
        }
        _save_batch_pending(root, pending)
        print(
            f"[auto_deploy] batch queued {synced_sha[:12]} -> "
            f"{remote_sha[:12]} ({subject}) "
            f"quiet={quiet}s max={max_wait or '∞'}s",
            flush=True,
        )
        queue_heads_up(root, remote_sha)
        return remote_sha, False

    quiet_elapsed = now - float(pending.get("last_seen_wall") or now)
    age = now - float(pending.get("first_seen_wall") or now)
    force_max = max_wait > 0 and age >= max_wait
    quiet_ok = quiet > 0 and quiet_elapsed >= quiet

    if not (force_max or quiet_ok):
        _maybe_log_batch_waiting(root, pending)
        return pending["tip_sha"], False

    tip = pending.get("tip_sha") or remote_sha
    if _fetch_origin(root):
        try:
            latest = _origin_main_sha(root)
            if latest:
                tip = latest
        except subprocess.CalledProcessError:
            pass
    clear_batch_pending(root)
    reason = "max elapsed" if force_max else "quiet elapsed"
    print(
        f"[auto_deploy] batch ready ({reason}) deploying tip {tip[:12]}",
        flush=True,
    )
    return tip, True


def _resolve_deploy_tip(root, synced_sha, remote_sha, *, bypass_batch=False):
    """Return ``(tip_sha, ready)`` — batch debounce, legacy coalesce, or immediate."""
    if not bypass_batch and batch_debounce_enabled():
        return _batch_deploy_gate(root, synced_sha, remote_sha)
    if coalesce_seconds() > 0:
        return _coalesce_remote_sha(root, remote_sha), True
    return remote_sha, True


def _announced_copyover_path(root):
    return os.path.join(root, ANNOUNCED_COPYOVER_NAME)


def _tree_syncing_path(root):
    return os.path.join(root, TREE_SYNCING_NAME)


def _disconnect_imminent_path(root):
    return os.path.join(root, DISCONNECT_IMMINENT_NAME)


def _gateway_disconnect_imminent_path(root):
    return os.path.join(root, GATEWAY_DISCONNECT_IMMINENT_NAME)


def touch_disconnect_imminent(root):
    """Watcher touched this before killing gateway — game tick may warn players."""
    try:
        with open(_disconnect_imminent_path(root), "w", encoding="utf-8") as fh:
            fh.write("\n")
    except OSError as exc:
        print(
            f"[auto_deploy] could not touch disconnect-imminent marker: {exc!r}",
            flush=True,
        )


def touch_gateway_disconnect_imminent(root):
    """Watcher touched this before killing gateway — hold loop warns held clients."""
    try:
        with open(_gateway_disconnect_imminent_path(root), "w", encoding="utf-8") as fh:
            fh.write("\n")
    except OSError as exc:
        print(
            f"[auto_deploy] could not touch gateway disconnect marker: {exc!r}",
            flush=True,
        )


def clear_disconnect_imminent(root):
    try:
        os.remove(_disconnect_imminent_path(root))
    except OSError:
        pass


def clear_gateway_disconnect_imminent(root):
    try:
        os.remove(_gateway_disconnect_imminent_path(root))
    except OSError:
        pass


def begin_announced_tree_sync(root):
    """Players were warned in-game — skip watcher settle before copyover."""
    try:
        with open(_announced_copyover_path(root), "w", encoding="utf-8") as fh:
            json.dump({"time": time.time()}, fh)
    except OSError as exc:
        print(
            f"[auto_deploy] could not mark announced copyover: {exc!r}",
            flush=True,
        )
    try:
        with open(_tree_syncing_path(root), "w", encoding="utf-8") as fh:
            fh.write("\n")
    except OSError as exc:
        print(
            f"[auto_deploy] could not touch tree-sync marker: {exc!r}",
            flush=True,
        )


def announced_copyover_pending(root) -> bool:
    """True when a Veil countdown already warned players (settle bypass)."""
    return os.path.isfile(_announced_copyover_path(root))


def clear_announced_copyover(root):
    """Drop the settle-bypass marker after copyover is signaled."""
    try:
        os.remove(_announced_copyover_path(root))
    except OSError:
        pass


def tree_sync_quiesce_active(root=None):
    """True while a deploy tree sync is absorbing protect-restore mtime churn.

    The watcher skips extra copyover triggers until the game records stable
    boot at the new HEAD (``boot_stability.write_stable`` clears this).
    """
    return os.path.isfile(_tree_syncing_path(root or _repo_root()))


def clear_tree_sync_quiesce(root=None):
    """Drop the post-sync quiesce marker (stable boot or abort)."""
    try:
        os.remove(_tree_syncing_path(root or _repo_root()))
    except OSError:
        pass


def abort_announced_tree_sync(root=None):
    """Drop both deploy-sync markers after a failed sync or boot probe."""
    root = root or _repo_root()
    clear_announced_copyover(root)
    clear_tree_sync_quiesce(root)


def _files_between_commits(root, old_sha, new_sha):
    """Git paths that differ between two commits (for gateway-restart detect)."""
    if not old_sha or not new_sha or old_sha == new_sha:
        return []
    try:
        out = _git("diff", "--name-only", old_sha, new_sha, cwd=root)
    except subprocess.CalledProcessError:
        return []
    return [
        line.strip()
        for line in (out or "").splitlines()
        if line.strip()
    ]


def _advance_touches_gateway(root, target_sha):
    """True when syncing HEAD → *target_sha* will drop client TCP (gateway or watcher).

    Compares the working tree to *target_sha* for gateway-holder paths, not
    only ``git diff HEAD target``. Overlay drift can already have tip gateway
    modules on disk while HEAD lags — commit-range diff then lies and the
    Veil warns disconnect when only a game-child copyover will run.
    """
    from engine import gateway_watch

    try:
        head = _git("rev-parse", "HEAD", cwd=root).strip()
    except subprocess.CalledProcessError:
        return False
    if head == target_sha:
        return False
    between = _files_between_commits(root, head, target_sha)
    gateway_candidates = [
        path
        for path in between
        if gateway_watch.normalize_repo_path(path) in gateway_watch.GATEWAY_RESTART_PATHS
    ]
    if not gateway_candidates:
        return False
    try:
        out = _git(
            "diff", "--name-only", target_sha, "--", *gateway_candidates, cwd=root,
        )
    except subprocess.CalledProcessError:
        # Conservative when git cannot compare — warn disconnect.
        return True
    return bool([
        line.strip()
        for line in (out or "").splitlines()
        if line.strip()
    ])


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
    flag file the parent reads inside ``try_auto_deploy``. Explicit catch-up
    (``gm autodeploy on`` / ``gm autodeploy now`` / ``gm deploy sync``) also
    clears an abort hold so the same tip can ship after staff cancelled a
    prior queue.
    """
    root = root or _repo_root()
    clear_abort(root)
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


def immediate_path(root=None):
    """Absolute path to the ``gm autodeploy now`` flush flag."""
    return os.path.join(root or _repo_root(), IMMEDIATE_NAME)


def immediate_requested(root=None):
    """True when staff asked to skip debounce and catch up on the next tick."""
    return os.path.isfile(immediate_path(root))


def clear_immediate(root=None):
    """Remove the immediate-flush flag (after sync, abort, or autodeploy off)."""
    path = immediate_path(root)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    return path


def request_immediate_catchup(root=None):
    """GM ``autodeploy now``: enable polls, skip debounce, catch up ASAP.

    Writes the usual catch-up flag **and** ``.auto_deploy_immediate`` so
    ``watch_and_run`` polls on the next 1s loop instead of waiting for
    ``AUTO_DEPLOY_POLL_SECONDS``. Clears a pending merge-quiet batch so
    the 2–10 minute debounce cannot hold the tip. The watcher still runs
    ``reset --hard`` (game child never mutates git).
    """
    root = root or _repo_root()
    set_override("on", root=root, queue_catchup=True)
    clear_batch_pending(root)
    clear_heads_up(root)
    path = immediate_path(root)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
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
    # Re-enable → full tree catch-up; pause → cancel a pending catch-up *flag*.
    # A debounce batch or Veil countdown already running is **not** stopped
    # by off — GM ``autodeploy abort`` calls ``abort_planned_deploy``.
    if normalized == _OVERRIDE_ON:
        if queue_catchup:
            request_catchup(root)
    else:
        clear_catchup(root)
        clear_immediate(root)
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


# Throttle poll-skip logs so a long ``autodeploy off`` window does not spam
# docker logs every AUTO_DEPLOY_POLL_SECONDS tick.
_SKIP_LOG_INTERVAL = 300.0
_last_skip_log_at = 0.0
_last_skip_log_reason = None
_last_build_lock_log_at = 0.0
_last_build_lock_log_key = None


def poll_skip_reason(root=None):
    """Why ``try_auto_deploy`` skips this tick, or None when polling would run.

    Order matches ``_enabled()`` — revert hold first, then GM override, then env.
    """
    from engine import crash_recovery

    if crash_recovery.hold_active(root=root):
        return "revert hold active"
    override = read_override(root)
    if override is not None:
        if override != _OVERRIDE_ON:
            return "override off"
        return None
    if not env_enabled():
        return "AUTO_DEPLOY env off"
    return None


def deploy_gate_summary(root=None):
    """One-line gate status for ``tools/live_ssh.py --deploy-log`` and ops."""
    from engine import build_lock
    from engine import deploy_queue

    override = read_override(root)
    ov_label = override if override is not None else "none"
    reason = poll_skip_reason(root)
    effective = "on" if reason is None else "off"
    skip = reason if reason is not None else "none"
    catchup = "yes" if catchup_requested(root) else "no"
    from engine import crash_recovery

    hold = "on" if crash_recovery.hold_active(root=root) else "off"
    build = "on" if build_lock.is_active(root) else "off"
    pending = "yes" if deploy_queue.has_pending(root) else "no"
    return (
        f"STATE={deploy_headline(root)} OVERRIDE={ov_label} EFFECTIVE={effective} "
        f"POLL_SKIP={skip} CATCHUP={catchup} REVERT_HOLD={hold} BUILD_LOCK={build} "
        f"DEPLOY_QUEUE={pending}"
    )


def tree_sync_blocked(root=None):
    """True when full ``reset --hard`` / catch-up must not run (build lock)."""
    from engine import build_lock

    return build_lock.is_active(root)


def _maybe_log_tree_sync_deferred(root, *, key, message):
    """Throttle build-lock deferral logs so polls do not spam docker."""
    global _last_build_lock_log_at, _last_build_lock_log_key
    now = time.monotonic()
    if key != _last_build_lock_log_key or (
        now - _last_build_lock_log_at
    ) >= _SKIP_LOG_INTERVAL:
        print(message, flush=True)
        _last_build_lock_log_at = now
        _last_build_lock_log_key = key


def _maybe_log_poll_skip(reason):
    """Emit ``[auto_deploy] poll skipped: …`` on reason change or every 5 min."""
    global _last_skip_log_at, _last_skip_log_reason
    now = time.monotonic()
    if (
        reason != _last_skip_log_reason
        or (now - _last_skip_log_at) >= _SKIP_LOG_INTERVAL
    ):
        print(f"[auto_deploy] poll skipped: {reason}", flush=True)
        _last_skip_log_at = now
        _last_skip_log_reason = reason


def _enabled():
    """Whether try_auto_deploy should poll this tick.

    GM override file wins when present; otherwise AUTO_DEPLOY env (default on).
    Revert hold (crash recovery) forces off so hotfix / manual patches are not
    wiped by reset --hard while staff clears the hold.
    """
    return poll_skip_reason() is None


def is_enabled():
    """Public wrapper for `_enabled()` -- used by GM status and smoke tests."""
    return _enabled()


def deploy_headline(root=None):
    """One-word-ish state so staff never has to read docker logs to know
    whether it is safe to touch autodeploy: ``PAUSED (hold)``,
    ``PAUSED (off)``, ``CATCHING UP``, or ``STABLE``.

    Order matters -- a crash-loop hold or an explicit override off always
    wins over "looks like it's syncing" so staff never see "CATCHING UP"
    while auto-deploy is actually paused for a reason.
    """
    from engine import build_lock
    from engine import boot_stability
    from engine import crash_recovery

    root = root or _repo_root()

    if crash_recovery.hold_active(root=root):
        reason = crash_recovery.read_hold_reason(root=root)
        return f"PAUSED (hold: {reason})" if reason else "PAUSED (hold)"

    override = read_override(root)
    if override is not None and override != _OVERRIDE_ON:
        return "PAUSED (override off)"
    if not env_enabled() and override is None:
        return "PAUSED (env off)"

    if (
        tree_sync_quiesce_active(root)
        or announced_copyover_pending(root)
        or catchup_requested(root)
        or build_lock.is_active(root)
    ):
        return "CATCHING UP"

    # No hold, no override pause, no sync in flight -- confirm the game
    # actually recorded a stable boot at the current HEAD. A blank/older
    # stamp here usually just means the child hasn't ticked long enough
    # yet (fresh spawn), which still reads as "settling in", not broken.
    stable = boot_stability.load_stable(root)
    head = boot_stability.current_head_sha(root)
    if stable and head and stable.get("sha") == head:
        return "STABLE"
    if head:
        return "CATCHING UP (settling in)"
    return "STABLE"


def status_text():
    """One short multi-line status string for the GM autodeploy command."""
    from engine import build_lock
    from engine import deploy_queue

    root = _repo_root()
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
    if immediate_requested():
        catchup_line += (
            " — immediate (skip merge-quiet wait; next watcher tick, short Veil)"
        )
    abort = load_abort(root)
    if abort and (abort.get("tip_sha") or "").strip():
        abort_line = (
            f"Abort hold: yes — skipping tip {abort['tip_sha'][:12]} "
            "(new commit, gm autodeploy on, or gm deploy sync clears this)"
        )
    elif abort:
        abort_line = "Abort hold: yes (in-flight rewrite cancelled)"
    else:
        abort_line = "Abort hold: no"
    lines = [
        f"State: {deploy_headline(root)}",
        f"Auto-deploy effective: {effective}",
        override_line,
        f"AUTO_DEPLOY env: {'on' if env_on else 'off'}",
        catchup_line,
        abort_line,
    ]
    lines.extend(build_lock.status_lines(root))
    lines.extend(batch_status_lines(root))
    synced = (_load_state(root).get("origin_main") or "")
    remote = ""
    try:
        remote = _origin_main_sha(root)
    except subprocess.CalledProcessError:
        pass
    lines.extend(
        deploy_queue.status_lines(root, synced_sha=synced, remote_sha=remote)
    )
    return "\n".join(lines)


def request_deploy_sync(root=None):
    """Queue one batched catch-up to origin/main (``gm deploy sync``)."""
    root = root or _repo_root()
    if tree_sync_blocked(root):
        raise RuntimeError("build lock is on — run gm buildlock off confirm first")
    request_catchup(root)
    return catchup_path(root)


def deploy_queue_status_lines(root=None):
    """Deploy-queue summary for GM status commands."""
    from engine import deploy_queue

    root = root or _repo_root()
    synced = (_load_state(root).get("origin_main") or "")
    remote = ""
    try:
        remote = _origin_main_sha(root)
    except subprocess.CalledProcessError:
        pass
    return deploy_queue.status_lines(root, synced_sha=synced, remote_sha=remote)


def needs_deploy_sync(root=None):
    """True when origin/main is ahead of the last synced tree SHA."""
    from engine import deploy_queue

    root = root or _repo_root()
    synced = (_load_state(root).get("origin_main") or "")
    if deploy_queue.has_pending(root):
        return True
    if not synced:
        return False
    try:
        remote = _origin_main_sha(root)
    except subprocess.CalledProcessError:
        return False
    return bool(remote and remote != synced)


def _countdown_seconds():
    try:
        return max(5, int(os.environ.get("AUTO_DEPLOY_COUNTDOWN", DEFAULT_COUNTDOWN)))
    except ValueError:
        return DEFAULT_COUNTDOWN


def _catchup_countdown_seconds():
    """Seconds of Veil warning before feature / catch-up tree sync + copyover."""
    try:
        return max(
            5,
            int(
                os.environ.get(
                    "AUTO_DEPLOY_CATCHUP_COUNTDOWN",
                    DEFAULT_CATCHUP_COUNTDOWN,
                )
            ),
        )
    except ValueError:
        return DEFAULT_CATCHUP_COUNTDOWN


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
        ops_sidecar_files,
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
    for name in ops_sidecar_files():
        abs_path = os.path.join(root, name)
        if os.path.isfile(abs_path):
            found.append(name)
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


def _snapshot_db_before_tree_sync(root, *, triggered_by="auto_deploy"):
    """Best-effort live DB copy before announced reset --hard."""
    try:
        from engine import world_backup as world_backup_mod

        snap = getattr(world_backup_mod, "snapshot_live_db_pre_deploy", None)
        if snap is None:
            raise ImportError("snapshot_live_db_pre_deploy missing on world_backup")
        path, detail = snap(root=root, triggered_by=triggered_by)
        if path:
            print(
                f"[auto_deploy] pre-catchup db snapshot -> {detail}",
                flush=True,
            )
        else:
            print(
                f"[auto_deploy] pre-catchup db snapshot skipped: {detail}",
                flush=True,
            )
    except Exception as exc:
        print(
            f"[auto_deploy] pre-catchup db snapshot failed: {exc!r}",
            flush=True,
        )


def _verify_boot_probe_or_rollback(root, previous_head):
    """Prove the synced tree boots; roll back to ``previous_head`` on failure.

    Fix overlays already use ``overlay_files_from_ref_with_boot_probe``;
    full ``reset --hard`` syncs must pass the same bar or live would pick
    up broken ``main`` and crash-loop the game child (Dallas class).

    Returns True when the tree is left at the synced tip (probe ok or
    probe disabled). Returns False after rolling back to ``previous_head``.
    """
    from engine.boot_probe import boot_probe_enabled, run_boot_probe_subprocess

    if not boot_probe_enabled():
        return True
    ok, detail = run_boot_probe_subprocess(cwd=root)
    if ok:
        print(f"[auto_deploy] boot probe ok: {detail or 'boot ok'}", flush=True)
        return True
    prev = (previous_head or "")[:12] or "?"
    print(
        f"[auto_deploy] boot probe FAILED after sync — rolling back to "
        f"{prev}: {detail}",
        flush=True,
    )
    if previous_head:
        stash = _stash_protected_live_files(root)
        try:
            _run_git("reset", "--hard", previous_head, cwd=root)
        finally:
            _restore_protected_live_files(root, stash)
    return False


def _mark_poll():
    """Stamp this watcher poll so deploy diag rows can report poll_gap_s."""
    global _last_poll_monotonic, _current_poll_gap_s
    now = time.monotonic()
    if _last_poll_monotonic is None:
        _current_poll_gap_s = None
    else:
        _current_poll_gap_s = round(now - _last_poll_monotonic, 2)
    _last_poll_monotonic = now


def _diff_file_count(root, from_sha, to_sha):
    """How many paths differ between two commits (best-effort, no raise)."""
    if not from_sha or not to_sha:
        return None
    try:
        names = _git("diff", "--name-only", from_sha, to_sha, cwd=root)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return len([ln for ln in names.splitlines() if ln.strip()])


def _emit_deploy_diag(*, kind, from_sha, to_sha, n_files_changed, extra=None):
    """Append one always-on auto_deploy_reset NDJSON row; never raise."""
    global _last_deploy_event_monotonic
    now = time.monotonic()
    since = None
    if _last_deploy_event_monotonic is not None:
        since = round(now - _last_deploy_event_monotonic, 2)
    try:
        from engine import diag_export
        diag_export.append_deploy_event(
            kind=kind,
            from_sha=from_sha,
            to_sha=to_sha,
            n_files_changed=n_files_changed,
            poll_gap_s=_current_poll_gap_s,
            since_prev_deploy_s=since,
            extra=extra,
        )
    except Exception as exc:
        print(f"[auto_deploy] diag event skipped: {exc}", flush=True)
    _last_deploy_event_monotonic = now
    # Overlay + full reset both refresh the save-defer window (lag P12.1).
    if kind in ("reset", "overlay"):
        record_deploy_reset_wall(_repo_root())


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

    Returns True when the tree ends at ``sha`` (or was already there).
    Returns False when boot probe fails and the tree was rolled back.
    """
    ensure_git_safe_directory(root)
    head = _head_sha(root)
    if head and head == sha:
        print(
            f"[auto_deploy] working tree already at {sha[:12]} "
            "(skipping reset --hard + protect restore)",
            flush=True,
        )
        _ensure_changelog_ledger_after_sync(root)
        return True
    previous_head = head
    n_files = _diff_file_count(root, previous_head, sha)
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
    _ensure_changelog_ledger_after_sync(root)
    if not _verify_boot_probe_or_rollback(root, previous_head):
        return False
    _emit_deploy_diag(
        kind="reset",
        from_sha=previous_head,
        to_sha=sha,
        n_files_changed=n_files,
    )
    return True


def _fresh_changelog_ledger():
    """Reload the changelog ledger module from disk (watcher is long-lived).

    ``watch_and_run`` reloads ``auto_deploy`` each poll, but used to keep a
    stale ``engine.changelog_index`` in ``sys.modules``, which then logged
    ``has no attribute 'stamp_pending_and_ensure_index'`` every idle poll and
    left the newest ships unnumbered. Same class as ``_fresh_deploy_notify``.
    """
    import engine.changelog_ledger as changelog_ledger_mod

    return importlib.reload(changelog_ledger_mod)


def _ensure_changelog_ledger_after_sync(root):
    """Mint any pending changelog ``#N`` ids in the ledger.

    ``assign_new_slugs`` is idempotent per slug — a fragment that already
    has a ledger row is a no-op, so calling this on every sync **and** every
    idle poll is safe. There is nothing left to "rebuild" here: ``changes``
    and Discord both query the ledger directly, so there is no compiled
    JSON cache that can fall behind.

    Copyover / ``Game()`` boot is the guaranteed mint (fresh interpreter).
    This watcher path is the Discord / idle-poll companion — it must reload
    ``changelog_ledger`` from disk or it no-ops on a stale module.
    """
    try:
        changelog_ledger_mod = _fresh_changelog_ledger()
        minted = changelog_ledger_mod.assign_new_slugs(
            root, log_prefix="[auto_deploy]",
        )
        if minted:
            print(f"[auto_deploy] changelog ledger minted {minted} id(s)", flush=True)
    except Exception as exc:
        print(f"[auto_deploy] changelog ledger mint skipped: {exc}", flush=True)


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
        _log_git_fetch_skipped(detail, root=root)
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
    if tree_sync_blocked(root):
        _maybe_log_tree_sync_deferred(
            root,
            key=f"head_sync:{remote_sha[:12]}",
            message=(
                "[auto_deploy] HEAD sync deferred (build_lock active) — "
                "Fix overlay applied; full tree sync waits for gm deploy sync"
            ),
        )
        return True
    label = f" ({reason})" if reason else ""
    print(
        f"[auto_deploy] HEAD {head_sha[:12]} behind tip {remote_sha[:12]}"
        f" -- full sync{label}",
        flush=True,
    )
    try:
        if not _reset_hard_to(root, remote_sha):
            print(
                "[auto_deploy] HEAD sync aborted (boot probe failed)",
                flush=True,
            )
            return False
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"[auto_deploy] HEAD sync failed: {exc}", flush=True)
        return False
    return True


def _commit_subject(sha, root):
    return _git("log", "-1", "--format=%s", sha, cwd=root)


def _commit_message(sha, root):
    """Full commit message (subject + body) for deploy metadata parsing."""
    return _git("log", "-1", "--format=%B", sha, cwd=root).strip()


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


def _normalize_deploy_line(line: str) -> str:
    """Strip markdown/list noise so PR-body lines match Fix/Ship parsers."""
    text = (line or "").strip()
    if not text:
        return ""
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"^[-*]\s+", "", text)
    return text.strip()


def _ids_from_fix_match(match) -> tuple[list[int], str]:
    """Expand a Fix-bug regex match into ids + trailing summary."""
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else None
    bug_ids = _expand_fix_subject_bug_ids(start, end, match.group(3) or "")
    summary = _parse_subject_ticket_tail(match.string, match)
    return bug_ids, summary


def _parse_line_deploy_ids(line: str) -> tuple[list[int], list[int], str]:
    """Try every Fix/Ship pattern on one normalized line."""
    text = _normalize_deploy_line(line)
    if not text:
        return [], [], ""

    match = _FIX_SUBJECT_RE.match(text)
    if match:
        bug_ids, summary = _ids_from_fix_match(match)
        return bug_ids, [], summary

    match = _FIX_BUG_REPORT_SUBJECT_RE.match(text)
    if match:
        bug_ids, summary = _ids_from_fix_match(match)
        return bug_ids, [], summary

    match = _SHIP_SUGGESTION_SUBJECT_RE.match(text)
    if match:
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        suggestion_ids = _expand_fix_subject_bug_ids(
            start, end, match.group(3) or "",
        )
        summary = _parse_subject_ticket_tail(text, match)
        return [], suggestion_ids, summary

    match = _SHIP_SUGGESTION_REPORT_SUBJECT_RE.match(text)
    if match:
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        suggestion_ids = _expand_fix_subject_bug_ids(
            start, end, match.group(3) or "",
        )
        summary = _parse_subject_ticket_tail(text, match)
        return [], suggestion_ids, summary

    match = _SHIP_PLAYER_SUGGESTIONS_SLASH_RE.match(text)
    if match:
        suggestion_ids = _expand_slash_subject_ids(match.group(1) or "")
        if suggestion_ids:
            summary = _parse_subject_ticket_tail(text, match)
            return [], suggestion_ids, summary

    match = _SHIP_SUGGESTION_BARE_RANGE_RE.match(text)
    if match:
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        suggestion_ids = _expand_fix_subject_bug_ids(
            start, end, match.group(3) or "",
        )
        summary = _parse_subject_ticket_tail(text, match)
        return [], suggestion_ids, summary

    return [], [], ""


def _first_subject_line(text: str) -> str:
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line:
            return line
    return ""


def _merge_ticket_ids(into: list[int], extra: list[int]) -> None:
    """Append unique ticket ids, preserving first-seen order."""
    for n in extra or []:
        if n not in into:
            into.append(n)


def _parse_deploy_text_lines(text: str) -> tuple[list[int], list[int], str]:
    """Scan commit/PR text line-by-line for Fix/Ship ticket headers.

    Mixed ships put bugs on one line and suggestions on another (squash
    subject plus PR body). Collect both instead of stopping at the first
    matching header.
    """
    all_bugs: list[int] = []
    all_suggestions: list[int] = []
    summary = ""
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        bug_ids, suggestion_ids, line_summary = _parse_line_deploy_ids(line)
        _merge_ticket_ids(all_bugs, bug_ids)
        _merge_ticket_ids(all_suggestions, suggestion_ids)
        if not summary and line_summary:
            summary = line_summary
    return all_bugs, all_suggestions, summary


def _merged_pr_number_from_subject(subject: str) -> int | None:
    match = _MERGED_PR_REF_RE.search((subject or "").strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _github_api_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "riftforge-auto-deploy",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_merged_pr_text_via_gh(pr_number: int) -> str:
    """Fallback when unauthenticated GitHub API cannot read a private PR."""
    try:
        proc = subprocess.run(
            [
                "gh",
                "api",
                f"repos/capnknives/RiftForge/pulls/{int(pr_number)}",
                "--jq",
                "{title,body}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=True,
        )
        row = json.loads(proc.stdout or "{}")
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        ValueError,
        json.JSONDecodeError,
    ):
        return ""
    if not isinstance(row, dict):
        return ""
    title = (row.get("title") or "").strip()
    body = (row.get("body") or "").strip()
    return f"{title}\n{body}".strip() if body else title


def _fetch_merged_pr_text(pr_number: int, *, allow_network: bool = True) -> str:
    """Fetch PR title + body for squash commits that omit the bug id."""
    pr_number = int(pr_number)
    cached = _PR_TEXT_CACHE.get(pr_number)
    if cached is not None:
        return cached
    # Local map first — live may lack GITHUB_TOKEN or hit API rate limits.
    from engine import deploy_pr_ticket_map

    mapped = deploy_pr_ticket_map.synthetic_pr_text(pr_number, root=_repo_root())
    if mapped:
        _PR_TEXT_CACHE[pr_number] = mapped
        return mapped
    if not allow_network:
        _PR_TEXT_CACHE[pr_number] = ""
        return ""
    # Prefer gh (maintainer auth) before unauthenticated REST (404/403 on private).
    text = _fetch_merged_pr_text_via_gh(pr_number)
    if text:
        _PR_TEXT_CACHE[pr_number] = text
        return text
    if not (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")):
        _PR_TEXT_CACHE[pr_number] = ""
        return ""
    url = f"{_GITHUB_PULL_API}/{pr_number}"
    req = urllib.request.Request(url, headers=_github_api_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        row = json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404, 429):
            print(
                f"[auto_deploy] GitHub PR #{pr_number} lookup failed: {exc} "
                f"(add ops/deploy_pr_ticket_map.json row or GITHUB_TOKEN)",
                flush=True,
            )
        else:
            print(
                f"[auto_deploy] GitHub PR #{pr_number} lookup failed: {exc}",
                flush=True,
            )
        _PR_TEXT_CACHE[pr_number] = ""
        return ""
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(
            f"[auto_deploy] GitHub PR #{pr_number} lookup failed: {exc}",
            flush=True,
        )
        _PR_TEXT_CACHE[pr_number] = ""
        return ""
    if not isinstance(row, dict):
        _PR_TEXT_CACHE[pr_number] = ""
        return ""
    title = (row.get("title") or "").strip()
    body = (row.get("body") or "").strip()
    text = f"{title}\n{body}".strip() if body else title
    _PR_TEXT_CACHE[pr_number] = text
    return text


def parse_deploy_metadata(
    text: str,
    *,
    pr_fallback: bool = True,
    pr_network: bool = True,
) -> tuple[list[int], list[int], str]:
    """Extract bug/suggestion ids + short summary from deploy text.

    ``ops/deploy_pr_ticket_map.json`` (written by ``tools/open_pr.py`` at
    PR-open time) is the **primary** source when the squash subject carries
    a merged PR number: it is a structured row an agent wrote deliberately,
    not free text regex has to guess at. Regex-parsing the commit
    subject/body/PR-title text is a **fallback** for pushes with no PR
    number (direct-to-main hotfixes) or a PR that predates the manifest —
    every time that fallback actually contributes an id the manifest did
    not already have, it prints a loud one-line warning so a missing
    manifest row is visible instead of silently rotting into a
    never-auto-closed ticket.

    Mixed Fix + Ship bundles collect both id lists. Recognized regex
    headers include ``Fix bug #N:``, ``Fix in-game bug N:``, and agent PR
    prose ``Fixes bug report N`` / ``Ships suggestion report N`` (no ``#``).

    Returns ``(bug_ids, suggestion_ids, summary)``.
    """
    subject_line = _first_subject_line(text)
    bug_ids, suggestion_ids, summary = _parse_deploy_text_lines(text)
    regex_found_any = bool(bug_ids or suggestion_ids)

    pr_number = _merged_pr_number_from_subject(subject_line) if pr_fallback else None
    if pr_number:
        from engine import deploy_pr_ticket_map

        manifest_row = deploy_pr_ticket_map.lookup(pr_number, root=_repo_root())
        if manifest_row:
            manifest_bugs = [
                int(x) for x in (manifest_row.get("bug_ids") or []) if str(x).isdigit()
            ]
            manifest_sugs = [
                int(x) for x in (manifest_row.get("suggestion_ids") or [])
                if str(x).isdigit()
            ]
            _merge_ticket_ids(bug_ids, manifest_bugs)
            _merge_ticket_ids(suggestion_ids, manifest_sugs)
            if not summary:
                summary = (manifest_row.get("summary") or "").strip()
        elif not regex_found_any:
            print(
                f"[auto_deploy] no ops/deploy_pr_ticket_map.json row for merged "
                f"PR #{pr_number} — falling back to regex/GitHub text parsing "
                f"for ticket close (this ticket may not auto-close; run "
                f"'py -3.13 tools/stale_ticket_report.py' to catch stragglers)",
                flush=True,
            )

    if pr_fallback and (not bug_ids or not suggestion_ids) and pr_number:
        pr_text = _fetch_merged_pr_text(pr_number, allow_network=pr_network)
        if pr_text:
            pr_bugs, pr_sugs, pr_summary = _parse_deploy_text_lines(
                pr_text,
            )
            if pr_bugs or pr_sugs:
                if not manifest_row:
                    print(
                        f"[auto_deploy] regex fallback found ticket ids for PR "
                        f"#{pr_number} that the manifest was missing "
                        f"(bugs={pr_bugs} suggestions={pr_sugs}) — consider "
                        f"backfilling ops/deploy_pr_ticket_map.json",
                        flush=True,
                    )
            _merge_ticket_ids(bug_ids, pr_bugs)
            _merge_ticket_ids(suggestion_ids, pr_sugs)
            if not summary:
                summary = pr_summary

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


def _advance_origin_only(root, state, remote_sha, subject, reason, *, from_sha=""):
    """Record the new tip without announcing or overlaying."""
    print(
        f"[auto_deploy] skipping announce for {remote_sha[:12]} "
        f"({reason}): {subject}",
        flush=True,
    )
    prev = (from_sha or state.get("origin_main") or "").strip()
    state["origin_main"] = remote_sha
    # Do NOT write last_deploy -- that would imply we shipped a fix.
    # Tracking origin_main alone is enough for advance-only gating.
    _save_state(root, state)
    from engine import deploy_queue

    deploy_queue.clear_pending(root)
    _maybe_schedule_deploy_patch_notes(root, prev, remote_sha)
    return False


def _maybe_schedule_deploy_patch_notes(root, from_sha, to_sha):
    """Fail-soft Discord #patch-notes mirror for CHANGELOG.d in this deploy."""
    # Stamp before posting so Discord never teaches a blank ``changes`` footer
    # while the in-game list already has #N (GitHub assign job is retired).
    _ensure_changelog_ledger_after_sync(root)
    try:
        import engine.discord_patch_notes as discord_patch_notes

        # Watcher keeps a stale discord_patch_notes the same way it kept a
        # stale changelog_ledger — reload so "wait then retry" actually runs.
        discord_patch_notes = importlib.reload(discord_patch_notes)
        discord_patch_notes.schedule_for_deploy(root, from_sha, to_sha)
    except Exception as exc:
        print(f"[auto_deploy] patch notes skipped: {exc}", flush=True)


def _wait_for_ready(root, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    ready = _ready_path(root)
    while time.monotonic() < deadline:
        if abort_requested(root):
            print(
                "[auto_deploy] deploy aborted by GM while waiting for "
                ".deploy_ready",
                flush=True,
            )
            return False
        if os.path.isfile(ready):
            try:
                os.remove(ready)
            except OSError:
                pass
            return True
        time.sleep(0.5)
    return False


def _run_catchup_countdown(
    root, *, commit_sha, summary, countdown=None, gateway_restart=None,
):
    """Announce a catch-up Veil countdown and wait for ``.deploy_ready``.

    Used before ``reset --hard`` / tree syncs that will mtime-trigger
    copyover. Returns True when the game finished the countdown (or the
    deploy was already completed for this SHA).
    """
    deploy_notify = _fresh_deploy_notify()
    seconds = (
        int(countdown)
        if countdown is not None
        else _catchup_countdown_seconds()
    )
    if gateway_restart is None:
        gateway_restart = _advance_touches_gateway(root, commit_sha)
    signal = deploy_notify.queue_catchup_copyover(
        root,
        commit_sha=commit_sha,
        countdown_seconds=seconds,
        summary=summary,
        triggered_by="engine/auto_deploy.py",
        gateway_restart=gateway_restart,
    )
    if signal is None:
        print(
            f"[auto_deploy] catch-up countdown skipped for {commit_sha[:12]} "
            "(already completed for this commit)",
            flush=True,
        )
        return True
    print(
        f"[auto_deploy] catch-up Veil countdown {seconds}s for "
        f"{commit_sha[:12]} (gateway_restart={gateway_restart}): {summary}",
        flush=True,
    )
    timeout = DEFAULT_READY_TIMEOUT + seconds
    if not _wait_for_ready(root, timeout):
        if abort_requested(root):
            print(
                "[auto_deploy] catch-up countdown aborted by GM",
                flush=True,
            )
        else:
            print(
                "[auto_deploy] timed out waiting for catch-up .deploy_ready -- "
                "is deploy_notify wired in server.py?",
                flush=True,
            )
        return False
    return True


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
    from tools.apply_pr_fix import overlay_files_from_ref_with_boot_probe

    deploy_notify = _fresh_deploy_notify()
    from engine import gateway_watch

    gateway_restart = gateway_watch.paths_touch_gateway_restart(files)

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
        gateway_restart=gateway_restart,
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
        f"[auto_deploy] countdown {countdown}s for {label} "
        f"(gateway_restart={gateway_restart}): {summary}",
        flush=True,
    )
    timeout = DEFAULT_READY_TIMEOUT + countdown
    if not _wait_for_ready(root, timeout):
        if abort_requested(root):
            print(
                "[auto_deploy] Fix countdown aborted by GM",
                flush=True,
            )
        else:
            print(
                "[auto_deploy] timed out waiting for .deploy_ready -- "
                "is deploy_notify wired in server.py?",
                flush=True,
            )
        return False

    begin_announced_tree_sync(root)
    ok, probe_detail = overlay_files_from_ref_with_boot_probe(
        commit_sha, files, cwd=root,
    )
    if not ok:
        abort_announced_tree_sync(root)
        print(
            f"[auto_deploy] overlay rolled back (boot probe): {probe_detail}",
            flush=True,
        )
        return False

    print(
        f"[auto_deploy] overlaid {len(files)} file(s) from {commit_sha[:12]} "
        f"(boot probe: {probe_detail})",
        flush=True,
    )
    from engine.deploy_guard import run_post_overlay_checks
    run_post_overlay_checks(root)
    _emit_deploy_diag(
        kind="overlay",
        from_sha=None,
        to_sha=commit_sha,
        n_files_changed=len(files or ()),
    )
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


def _fix_commits_from_shas(root, shas, *, pr_network: bool = True):
    """Parse Fix/Ship subjects from an ordered SHA list."""
    fixes = []
    for sha in shas:
        try:
            subject = _commit_subject(sha, root)
            message = _commit_message(sha, root)
        except subprocess.CalledProcessError:
            continue
        bug_ids, suggestion_ids, summary = parse_deploy_metadata(
            message or subject,
            pr_network=pr_network,
        )
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
    "Fixes bug report",
    "Ship suggestion",
    "Ship suggestions",
    "Ship player suggestions",
    "Ships suggestion report",
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
        return {"tip_sha": "", "bug_ids": [], "suggestion_ids": []}
    bug_ids = data.get("bug_ids") or []
    suggestion_ids = data.get("suggestion_ids") or []
    return {
        "tip_sha": (data.get("tip_sha") or "").strip(),
        "bug_ids": [int(x) for x in bug_ids if str(x).isdigit()],
        "suggestion_ids": [
            int(x) for x in suggestion_ids if str(x).isdigit()
        ],
    }


def _save_bug_resolve_cache(
    directory, *, tip_sha, bug_ids, suggestion_ids=None,
):
    path = _bug_resolve_cache_path(directory)
    payload = {
        "tip_sha": tip_sha,
        "bug_ids": sorted({int(x) for x in bug_ids}),
        "suggestion_ids": sorted(
            {int(x) for x in (suggestion_ids or [])},
        ),
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


def _collect_merged_pr_ref_shas(git_root, to_sha, *, from_sha=None):
    """Commits whose squash subject ends with ``(#NNNN)`` (PR title may hold bug id)."""
    if not to_sha:
        return []
    rev_range = to_sha
    if from_sha and from_sha != to_sha:
        rev_range = f"{from_sha}..{to_sha}"
    try:
        out = _git(
            "log",
            "--reverse",
            "--format=%H %s",
            rev_range,
            cwd=git_root,
        )
    except subprocess.CalledProcessError:
        return []
    shas: list[str] = []
    for line in out.splitlines():
        parts = line.strip().split(" ", 1)
        if len(parts) != 2:
            continue
        sha, subject = parts[0], parts[1]
        if _merged_pr_number_from_subject(subject):
            shas.append(sha)
    return shas


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
    for sha in _collect_merged_pr_ref_shas(git_root, to_sha, from_sha=from_sha):
        if sha not in seen:
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
    bugs, _suggestions = refresh_deployed_ticket_id_cache(
        git_root,
        report_directory,
        full_rebuild=full_rebuild,
    )
    return bugs


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


def open_suggestion_ids(directory="."):
    """Return sorted ids of still-open rows in ``suggestions.log``."""
    from engine import reports

    return sorted(
        entry["id"]
        for entry in reports.recent(reports.SUGGEST, None, directory=directory)
        if entry.get("status", "open") == "open"
    )


def deployed_fix_bug_ids(git_root, report_directory=None, *, full_rebuild=False):
    """De-duplicated bug ids from deployed Fix subjects (cached, incremental)."""
    bugs, _suggestions = refresh_deployed_ticket_id_cache(
        git_root,
        report_directory,
        full_rebuild=full_rebuild,
    )
    return bugs


def deployed_fix_suggestion_ids(
    git_root, report_directory=None, *, full_rebuild=False,
):
    """De-duplicated suggestion ids from deployed Ship subjects (cached)."""
    _bugs, suggestions = refresh_deployed_ticket_id_cache(
        git_root,
        report_directory,
        full_rebuild=full_rebuild,
    )
    return suggestions


def refresh_deployed_ticket_id_cache(
    git_root,
    report_directory,
    *,
    full_rebuild=False,
):
    """Update cached deployed Fix/Ship ticket ids incrementally (boot path)."""
    report_directory = report_directory or git_root
    tip = _deployed_tip_sha(git_root)
    cache = _load_bug_resolve_cache(report_directory)
    bug_ids = list(cache.get("bug_ids") or [])
    suggestion_ids = list(cache.get("suggestion_ids") or [])
    cached_tip = cache.get("tip_sha") or ""

    if not tip:
        return bug_ids, suggestion_ids

    if cached_tip == tip and bug_ids and not full_rebuild:
        try:
            with open(
                _bug_resolve_cache_path(report_directory),
                encoding="utf-8",
            ) as f:
                raw = json.load(f)
            if "suggestion_ids" in raw:
                return bug_ids, suggestion_ids
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    if full_rebuild or not cached_tip:
        scan_from = None
        if full_rebuild:
            bug_ids = []
            suggestion_ids = []
    else:
        scan_from = cached_tip

    shas = _collect_fix_ship_shas(git_root, tip, from_sha=scan_from)
    for fix in _fix_commits_from_shas(git_root, shas, pr_network=False):
        bug_ids = _merge_bug_id_list(bug_ids, fix.get("bug_ids"))
        suggestion_ids = _merge_bug_id_list(
            suggestion_ids, fix.get("suggestion_ids"),
        )
    bug_ids = _merge_bug_id_list(bug_ids, _hook_bug_ids_from_shas(git_root, shas))
    _save_bug_resolve_cache(
        report_directory,
        tip_sha=tip,
        bug_ids=bug_ids,
        suggestion_ids=suggestion_ids,
    )
    return bug_ids, suggestion_ids


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


def _run_reenable_catchup(root, state, remote_sha, *, immediate=False):
    """Full working-tree sync after GM ``autodeploy on`` (missed commits).

    Uses the same ``reset --hard`` + protect stash path as a feature
    advance — not a tip-only Fix overlay — so multi-commit gaps while the
    toggle was off do not leave intermediate files behind. When the tree
    must move, a catch-up Veil countdown (default 60s) warns players before
    the sync that will mtime-trigger copyover. Missed ``Fix bug #N``
    subjects in that range are handed to deploy_notify for resolve +
    reporter credit after the tree lands.
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
    clear_batch_pending(root)
    remote_sha, _ready = _resolve_deploy_tip(
        root, from_sha, remote_sha, bypass_batch=True,
    )
    try:
        subject = _commit_subject(remote_sha, root)
    except subprocess.CalledProcessError:
        pass
    if tree_sync_blocked(root):
        from engine import deploy_queue

        deploy_queue.record_blocked_advance(
            root, from_sha=from_sha, to_sha=remote_sha, subject=subject,
        )
        _maybe_log_tree_sync_deferred(
            root,
            key=f"catchup:{remote_sha[:12]}",
            message=(
                "[auto_deploy] catch-up deferred (build_lock active) — "
                "run gm deploy sync when ready"
            ),
        )
        return False
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
        # Warn in-game before rewriting files (mtime → copyover).
        # ``autodeploy now`` keeps a 5s Veil instead of the 30s rewrite wait
        # and the 2–10 min merge-quiet debounce.
        countdown = DEFAULT_IMMEDIATE_COUNTDOWN if immediate else None
        if not _run_catchup_countdown(
            root,
            commit_sha=remote_sha,
            countdown=countdown,
            summary=(
                "Catch-up rewrite — the Veil is about to stitch origin/main "
                "into the live bones of this world."
            ),
        ):
            # Leave the flag so the next poll retries.
            return False
        try:
            begin_announced_tree_sync(root)
            _snapshot_db_before_tree_sync(root)
            if not _reset_hard_to(root, remote_sha):
                abort_announced_tree_sync(root)
                print(
                    "[auto_deploy] catch-up sync aborted (boot probe failed)",
                    flush=True,
                )
                return False
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            abort_announced_tree_sync(root)
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
    clear_immediate(root)
    from engine import deploy_queue

    deploy_queue.clear_pending(root)
    _maybe_schedule_deploy_patch_notes(root, from_sha, remote_sha)
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

    Idle polls (no SHA advance) still fill-stamp changelog ``#N`` so
    timestamp-only ships do not stay unnumbered until the next merge.
    """
    skip_reason = poll_skip_reason()
    if skip_reason is not None:
        _maybe_log_poll_skip(skip_reason)
        return False

    _mark_poll()

    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)

    # Stray ``.auto_deploy_immediate`` with no catch-up flag: consume so
    # the watcher does not poll every second forever.
    if immediate_requested(root) and not catchup_requested(root):
        clear_immediate(root)

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
        immediate = immediate_requested(root)
        # Consume the 1s watcher poke before the blocking Veil wait so a
        # deferred/failed catch-up cannot git-fetch every tick.
        if immediate:
            clear_immediate(root)
        return _run_reenable_catchup(
            root, state, remote_sha, immediate=immediate,
        )

    if abort_holds_tip(root, remote_sha):
        _maybe_log_poll_skip("aborted tip held")
        return False
    # A newer origin/main SHA (or no hold) — drop a stale abort stamp.
    if load_abort(root) is not None:
        clear_abort(root)

    # Never re-run the full deploy pipeline for a commit we already shipped.
    last_deploy_sha = (state.get("last_deploy") or {}).get("sha")
    if last_deploy_sha == remote_sha:
        # Stamp then retry Discord: copyover may have minted #N after the
        # first pass deferred an unnumbered patch-notes post.
        _maybe_schedule_deploy_patch_notes(
            root,
            state.get("origin_main") or remote_sha,
            remote_sha,
        )
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
        # Feature deploys advance origin_main without last_deploy. Idle
        # polls used to skip the changelog stamper here, so timestamp-only
        # ships stayed unnumbered until the next origin SHA. Discord must
        # retry on the same poll or the footer stays "type changes" with
        # no lookup number.
        _maybe_schedule_deploy_patch_notes(root, prev_sha or remote_sha, remote_sha)
        return False

    tip, ready = _resolve_deploy_tip(root, prev_sha, remote_sha)
    if not ready:
        return False
    remote_sha = tip

    subject = _commit_subject(remote_sha, root)
    message = _commit_message(remote_sha, root)
    bug_ids, suggestion_ids, summary = parse_deploy_metadata(message or subject)
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
        message or subject,
        parent_count=parent_count,
        file_count=len(files),
    )
    if not ship:
        # Feature / non-Fix pushes: Veil countdown, then sync the tree.
        # (Never use Fix Bug #N chrome for incidental / merge subjects.)
        if tree_sync_blocked(root):
            from engine import deploy_queue

            deploy_queue.record_blocked_advance(
                root,
                from_sha=prev_sha,
                to_sha=remote_sha,
                subject=subject,
            )
            _maybe_log_tree_sync_deferred(
                root,
                key=f"feature:{remote_sha[:12]}",
                message=(
                    f"[auto_deploy] tree sync deferred (build_lock active) "
                    f"for {remote_sha[:12]}: {subject} — "
                    "run gm deploy sync when ready"
                ),
            )
            return False
        if not _run_catchup_countdown(
            root,
            commit_sha=remote_sha,
            summary=(
                "A world rewrite is landing — stay put while the Veil "
                "stitches the new bones into place."
            ),
        ):
            return False
        try:
            begin_announced_tree_sync(root)
            _snapshot_db_before_tree_sync(root)
            if not _reset_hard_to(root, remote_sha):
                abort_announced_tree_sync(root)
                print(
                    "[auto_deploy] working-tree sync aborted "
                    "(boot probe failed)",
                    flush=True,
                )
                return False
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            abort_announced_tree_sync(root)
            print(f"[auto_deploy] working-tree sync failed: {exc}", flush=True)
            return False
        # A feature tip can land in the same poll batch as Fix commits
        # underneath it -- resolve those tickets after the tree sync.
        if missed_fixes:
            try:
                _apply_catchup_fix_resolves(root, state, missed_fixes)
            except Exception as exc:
                print(
                    f"[auto_deploy] missed-fix catch-up failed: {exc}",
                    flush=True,
                )
        return _advance_origin_only(
            root, state, remote_sha, subject, reason, from_sha=prev_sha,
        )

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
    clear_batch_pending(root)
    _maybe_schedule_deploy_patch_notes(root, prev_sha, remote_sha)
    return True


def poll_interval_seconds():
    """How often watch_and_run should call try_auto_deploy()."""
    try:
        return max(10, int(os.environ.get("AUTO_DEPLOY_POLL_SECONDS",
                                          DEFAULT_POLL_EVERY)))
    except ValueError:
        return DEFAULT_POLL_EVERY
