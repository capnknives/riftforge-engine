"""
gm_debug_export.py -- persist ``gm debug`` channel evidence to files, then
push / clear like ``gm diaglog``.

Staff ``[DEBUG]`` lines used to die with the session. When capture is on,
every emit also appends one NDJSON row under ``logs/gm_debug/<channel>.ndjson``
even if nobody has that channel toggled on their session.

Flow (mirrors ``engine/diag_export.py``, different sticky hub):

  1. ``gm debuglog on`` writes ``.gm_debug_capture_override``
  2. Reproduce
  3. ``gm debuglog analyze [channel]`` -- secret Gist + comment GitHub issue
     4488 (not issue 195) + writers off + **delete the host files**
  4. ``gm debuglog clear [channel]`` deletes without a Gist

Lag NDJSON stays on ``gm diaglog`` / issue 195. Pace transcripts stay
on ``gm pacelog``. This module is the channel-file hub only.

This module reuses the same GitHub token (``RIFTFORGE_DIAG_GITHUB_TOKEN``)
so live does not need a second PAT or a compose recreate.

Networking is stdlib ``urllib`` only, off the play loop via
``asyncio.to_thread``. File append never raises into emit callers.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error


# Capture toggle file (gitignored), same idea as ``.diag_enabled_override``.
OVERRIDE_NAME = ".gm_debug_capture_override"
ENABLED_ENV = "RIFTFORGE_GM_DEBUG_CAPTURE"
LOG_DIR_ENV = "RIFTFORGE_GM_DEBUG_LOG_DIR"
ROOT_ENV = "RIFTFORGE_GM_DEBUG_ROOT"
ISSUE_ENV = "RIFTFORGE_GM_DEBUG_GITHUB_ISSUE"

# Sticky hub for channel dumps -- not the lag hub (issue 195).
DEFAULT_ISSUE = 4488
DEFAULT_LOG_DIRNAME = os.path.join("logs", "gm_debug")
DEFAULT_SESSION_ID = "gm-debug"

_OVERRIDE_ON = "on"
_OVERRIDE_OFF = "off"

# Cap one line so a runaway combat emit cannot blow the disk in one write.
_MAX_MESSAGE_CHARS = 4000
_MAX_GIST_CHARS = 900_000
_POST_TIMEOUT_SECONDS = 30
_SUMMARY_MAX_LINES = 5000
_CHANNEL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def repo_root():
    """Checkout root (parent of ``engine/``), or test override."""
    override = os.environ.get(ROOT_ENV, "").strip()
    if override:
        return override
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log_dir(root=None):
    """Directory of per-channel NDJSON evidence files."""
    override = os.environ.get(LOG_DIR_ENV, "").strip()
    if override:
        return override
    return os.path.join(root or repo_root(), DEFAULT_LOG_DIRNAME)


def override_path(root=None):
    """Absolute path to the GM capture on/off override file."""
    return os.path.join(root or repo_root(), OVERRIDE_NAME)


def read_override(root=None):
    """Return ``'on'``, ``'off'``, or ``None`` if no override / junk file."""
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
    """Write the GM override to ``on`` or ``off``. Returns the path written."""
    normalized = (value or "").strip().lower()
    if normalized in ("1", "true", "yes"):
        normalized = _OVERRIDE_ON
    if normalized in ("0", "false", "no"):
        normalized = _OVERRIDE_OFF
    if normalized not in (_OVERRIDE_ON, _OVERRIDE_OFF):
        raise ValueError(f"override must be on or off, got {value!r}")
    path = override_path(root)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(normalized + "\n")
    return path


def env_enabled():
    """True when ``ENABLED_ENV`` alone would turn capture on (no file check)."""
    raw = os.environ.get(ENABLED_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def capture_enabled(root=None):
    """True when emit paths should append NDJSON.

    Priority: GM override file (``gm debuglog on|off``) wins over
    ``RIFTFORGE_GM_DEBUG_CAPTURE``. With neither set, capture stays off.
    """
    override = read_override(root)
    if override == _OVERRIDE_ON:
        return True
    if override == _OVERRIDE_OFF:
        return False
    return env_enabled()


def normalize_channel(channel):
    """Lowercase channel id, or ``''`` if it is not a safe filename."""
    raw = (channel or "").strip().lower()
    if not raw or not _CHANNEL_NAME_RE.match(raw):
        return ""
    return raw


def channel_log_path(channel, root=None):
    """Absolute path for one channel's NDJSON file, or None if invalid."""
    name = normalize_channel(channel)
    if not name:
        return None
    return os.path.join(log_dir(root), f"{name}.ndjson")


def list_channel_files(root=None):
    """Return ``[(channel, path, bytes, lines), ...]`` for files on disk."""
    directory = log_dir(root)
    rows = []
    try:
        names = os.listdir(directory)
    except OSError:
        return rows
    for name in sorted(names):
        if not name.endswith(".ndjson"):
            continue
        channel = name[: -len(".ndjson")]
        if not normalize_channel(channel):
            continue
        path = os.path.join(directory, name)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        lines = 0
        if size:
            try:
                with open(path, encoding="utf-8") as fh:
                    lines = sum(1 for _ in fh)
            except OSError:
                lines = -1
        rows.append((channel, path, size, lines))
    return rows


def github_issue_number():
    """Sticky issue number, or None if explicitly disabled (``0`` / empty)."""
    raw = os.environ.get(ISSUE_ENV, str(DEFAULT_ISSUE)).strip()
    if not raw or raw in ("0", "none", "off"):
        return None
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_ISSUE


def status_lines(root=None):
    """Human-readable capture config -- same shape as ``gm diaglog`` status."""
    from engine import diag_export

    directory = log_dir(root)
    files = list_channel_files(root)
    override = read_override(root)
    if override == _OVERRIDE_ON:
        writers = f"ON (gm debuglog on → {OVERRIDE_NAME})"
    elif override == _OVERRIDE_OFF:
        writers = f"off (gm debuglog off → {OVERRIDE_NAME})"
    elif capture_enabled(root):
        writers = f"ON ({ENABLED_ENV}=1)"
    else:
        writers = "off (gm debuglog on)"
    issue = github_issue_number()
    total_bytes = sum(size for _ch, _path, size, _n in files)
    total_lines = 0
    unknown_lines = False
    for _ch, _path, _size, n_lines in files:
        if n_lines < 0:
            unknown_lines = True
        else:
            total_lines += n_lines
    exists = bool(files)
    lines = [
        f"Log: {directory}",
        (
            f"Exists: {'yes' if exists else 'no'}  "
            f"bytes={total_bytes}  "
            f"lines={total_lines if exists and not unknown_lines else ('?' if unknown_lines else 0)}"
        ),
        f"NDJSON writers: {writers}",
        "Analyze webhook: n/a (Gist + issue only)",
        f"Token: {'set' if diag_export.github_token() else 'MISSING (' + diag_export.TOKEN_ENV + ')'}",
        f"Repo: {diag_export.github_repo()}",
        (
            f"Issue: #{issue} (https://github.com/{diag_export.github_repo()}/issues/{issue})"
            if issue
            else "Issue: (disabled -- gist only)"
        ),
    ]
    if files:
        bits = " ".join(
            f"{ch}={size}/{n_lines if n_lines >= 0 else '?'}"
            for ch, _path, size, n_lines in files
        )
        lines.append(f"Channels: {bits}")
    return lines


def capture_event(channel, message, extra=None, *, root=None):
    """Append one NDJSON row when capture is on. Never raises."""
    if not capture_enabled(root):
        return False
    name = normalize_channel(channel)
    if not name:
        return False
    text = (message or "").strip()
    if not text:
        return False
    if len(text) > _MAX_MESSAGE_CHARS:
        text = text[:_MAX_MESSAGE_CHARS] + "…(truncated)"
    path = channel_log_path(name, root)
    if not path:
        return False
    payload = {
        "sessionId": DEFAULT_SESSION_ID,
        "channel": name,
        "message": text,
        "timestamp": int(time.time() * 1000),
    }
    if extra:
        payload["data"] = extra
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
        return True
    except Exception as exc:
        from engine import log_util

        log_util.ops("gm_debug_export", "ndjson append failed", exc=exc)
        return False


def read_channel_text(channel=None, *, root=None, max_chars=_MAX_GIST_CHARS):
    """Return (text, label, found) for one channel or every file concatenated."""
    files = list_channel_files(root)
    if channel:
        name = normalize_channel(channel)
        if not name:
            return f"(invalid channel {channel!r})", "", False
        wanted = [row for row in files if row[0] == name]
        if not wanted:
            path = channel_log_path(name, root)
            return f"(no NDJSON at {path})", path or "", False
        files = wanted
    if not files:
        return f"(no NDJSON under {log_dir(root)})", log_dir(root), False
    chunks = []
    total = 0
    for _ch, path, _size, _n in files:
        try:
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
        except OSError as exc:
            chunks.append(f"(read failed {path}: {exc})\n")
            continue
        header = f"# channel={_ch} path={path}\n"
        chunks.append(header + body)
        total += len(body)
    text = "\n".join(chunks)
    if len(text) > max_chars:
        text = (
            f"(truncated: kept last {max_chars} of {len(text)} chars)\n"
            + text[-max_chars:]
        )
    label = channel or "all"
    return text, label, True


def clear_log(channel=None, *, root=None):
    """Delete one channel file or every gm-debug NDJSON. Returns (ok, message)."""
    if channel:
        name = normalize_channel(channel)
        if not name:
            return False, f"Unknown channel '{channel}'."
        path = channel_log_path(name, root)
        if not path or not os.path.isfile(path):
            return True, f"No log file for [{name}]."
        try:
            os.remove(path)
        except OSError as exc:
            return False, f"Could not delete {path}: {exc}"
        return True, f"Cleared {path}."
    files = list_channel_files(root)
    if not files:
        return True, f"No gm-debug files under {log_dir(root)}."
    errors = []
    n = 0
    for _ch, path, _size, _n in files:
        try:
            os.remove(path)
            n += 1
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    if errors:
        return False, "Cleared some files; failed: " + "; ".join(errors)
    return True, f"Cleared {n} gm-debug file(s) under {log_dir(root)}."


def summarize_ndjson(text):
    """Cheap counts by channel / message prefix for the Gist comment."""
    from collections import defaultdict

    channels = defaultdict(int)
    prefixes = defaultdict(int)
    lines_seen = 0
    for line in (text or "").splitlines():
        if lines_seen >= _SUMMARY_MAX_LINES:
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("(") or stripped.startswith("#"):
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        lines_seen += 1
        ch = row.get("channel") or "?"
        channels[ch] += 1
        msg = (row.get("message") or "").strip()
        prefix = msg.split(" ", 1)[0] if msg else "?"
        prefixes[prefix] += 1
    top_prefix = sorted(prefixes.items(), key=lambda kv: -kv[1])[:12]
    return {
        "ndjson_lines_parsed": lines_seen,
        "channels": dict(channels),
        "top_message_prefixes": [
            {"prefix": name, "n": count} for name, count in top_prefix
        ],
    }


def format_capture_summary(stats, *, channel=None):
    """Plain-text block for the sticky-issue comment."""
    if not stats:
        return "(no stats)"
    parts = [
        f"lines={stats.get('ndjson_lines_parsed') or 0}",
    ]
    if channel:
        parts.append(f"filter={channel}")
    chans = stats.get("channels") or {}
    if chans:
        bits = ", ".join(
            f"{name}={n}"
            for name, n in sorted(chans.items(), key=lambda kv: -kv[1])
        )
        parts.append(f"channels: {bits}")
    tops = stats.get("top_message_prefixes") or []
    if tops:
        bits = ", ".join(f"{row['prefix']}={row['n']}" for row in tops[:8])
        parts.append(f"prefixes: {bits}")
    return "\n".join(parts)


def push_sync(channel=None, *, reporter="?", root=None):
    """Blocking: create Gist + issue comment. Returns result dict."""
    from engine import diag_export

    token = diag_export.github_token()
    if not token:
        return {
            "ok": False,
            "message": (
                f"No GitHub token. Set {diag_export.TOKEN_ENV} "
                "(same PAT as gm diaglog) in the live .env."
            ),
            "gist_url": None,
            "issue_url": None,
            "error": "missing_token",
        }

    log_text, label, found = read_channel_text(channel, root=root)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    filename = f"riftforge-gm-debug-{int(time.time())}.ndjson"
    if not found or not log_text.strip():
        log_text = (
            f"(no gm-debug NDJSON -- capture status only)\n"
            + "\n".join(status_lines(root))
        )
    stats = summarize_ndjson(log_text)
    summary = format_capture_summary(stats, channel=channel)
    gist_body = {
        "description": (
            f"Riftforge gm-debug capture from {reporter} @ {stamp}"
            + (f" [{channel}]" if channel else "")
        ),
        "public": False,
        "files": {
            filename: {"content": log_text},
            "capture_summary.txt": {"content": summary + "\n\n" + "\n".join(status_lines(root))},
        },
    }
    try:
        status, gist = diag_export._http_json(
            "POST", "https://api.github.com/gists", gist_body, token
        )
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        detail = str(exc)
        if isinstance(exc, urllib.error.HTTPError):
            try:
                detail = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}"
            except Exception:
                detail = f"HTTP {exc.code}"
        return {
            "ok": False,
            "message": f"Gist create failed: {detail}",
            "gist_url": None,
            "issue_url": None,
            "error": "gist_failed",
        }

    gist_url = (gist or {}).get("html_url") if status and gist else None
    if not gist_url:
        return {
            "ok": False,
            "message": f"Gist create returned HTTP {status} without html_url",
            "gist_url": None,
            "issue_url": None,
            "error": "gist_bad_response",
        }

    issue_n = github_issue_number()
    issue_url = None
    if issue_n:
        repo = diag_export.github_repo()
        filt = f" filter=`{channel}`" if channel else ""
        comment = (
            f"### gm-debug capture — `{reporter}` — {stamp}{filt}\n\n"
            f"**Gist (full NDJSON):** {gist_url}\n\n"
            f"```\n{summary}\n```\n"
        )
        api = f"https://api.github.com/repos/{repo}/issues/{issue_n}/comments"
        try:
            diag_export._http_json("POST", api, {"body": comment}, token)
            issue_url = f"https://github.com/{repo}/issues/{issue_n}"
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            return {
                "ok": True,
                "message": (
                    f"Gist ok, but issue {issue_n} comment failed: {exc}. "
                    f"Gist: {gist_url}"
                ),
                "gist_url": gist_url,
                "issue_url": f"https://github.com/{repo}/issues/{issue_n}",
                "error": "issue_comment_failed",
            }

    hub = issue_url or gist_url
    return {
        "ok": True,
        "message": (
            f"Pushed gm-debug log ({label}). Hub: {hub}"
            + (f"  Gist: {gist_url}" if issue_url else "")
        ),
        "gist_url": gist_url,
        "issue_url": issue_url,
        "error": None,
    }


def analyze_sync(channel=None, *, reporter="?", root=None):
    """Push Gist, turn capture off, clear host files after a successful Gist.

    No Cursor webhook -- unlike ``gm diaglog analyze``. Review happens on
    the sticky GitHub issue (default 4488). Files are deleted only when the
    Gist landed so a failed upload cannot wipe the only copy.
    """
    push_result = push_sync(channel, reporter=reporter, root=root)
    try:
        off_path = set_override(_OVERRIDE_OFF, root=root)
        writers_off = True
        writers_message = f"capture off ({off_path})."
    except Exception as exc:
        writers_off = False
        writers_message = f"Could not turn capture off: {exc}"

    cleared = False
    clear_message = ""
    if push_result.get("ok") and push_result.get("gist_url"):
        ok, clear_message = clear_log(channel, root=root)
        cleared = ok
        if not ok:
            clear_message = f"Gist ok but clear failed: {clear_message}"
    else:
        clear_message = "Host files kept (push did not land a Gist)."

    hub = push_result.get("issue_url") or push_result.get("gist_url")
    ok = bool(push_result.get("ok") and push_result.get("gist_url"))
    return {
        "ok": ok,
        "message": (
            f"{push_result.get('message') or ''} {writers_message} "
            f"{clear_message}"
            + (f" Hub: {hub}" if hub else "")
        ).strip(),
        "gist_url": push_result.get("gist_url"),
        "issue_url": push_result.get("issue_url"),
        "writers_off": writers_off,
        "cleared": cleared,
        "error": None if ok else (push_result.get("error") or "push_failed"),
    }


def _log_task_exception(task):
    """Done-callback: surface escaped task crashes."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        print(f"[gm_debug_export] background task crashed: {exc}", flush=True)


async def _push_async(channel, reporter, session, root):
    """Run push_sync off-loop; tell the GM the result."""
    from engine import gm_notify

    try:
        result = await asyncio.to_thread(
            push_sync, channel, reporter=reporter, root=root
        )
        msg = result.get("message") or "(no message)"
        if session is not None:
            gm_notify.send_gm_session(session, f"debuglog: {msg}")
            if result.get("gist_url"):
                gm_notify.send_gm_session(
                    session, f"debuglog: gist {result['gist_url']}",
                )
            if result.get("issue_url"):
                gm_notify.send_gm_session(
                    session, f"debuglog: issue {result['issue_url']}",
                )
        print(f"[gm_debug_export] {msg}", flush=True)
    except Exception as exc:
        print(f"[gm_debug_export] unexpected error: {exc}", flush=True)
        if session is not None:
            gm_notify.send_gm_session(
                session, f"debuglog: unexpected error: {exc}",
            )


async def _analyze_async(channel, reporter, session, root):
    """Run analyze_sync off-loop; tell the GM the result."""
    from engine import gm_notify

    try:
        result = await asyncio.to_thread(
            analyze_sync, channel, reporter=reporter, root=root
        )
        msg = result.get("message") or "(no message)"
        if session is not None:
            gm_notify.send_gm_session(session, f"debuglog: {msg}")
            if result.get("gist_url"):
                gm_notify.send_gm_session(
                    session, f"debuglog: gist {result['gist_url']}",
                )
            if result.get("issue_url"):
                gm_notify.send_gm_session(
                    session, f"debuglog: issue {result['issue_url']}",
                )
            if result.get("writers_off"):
                gm_notify.send_gm_session(
                    session,
                    "debuglog: NDJSON writers are OFF "
                    "(re-enable with `gm debuglog on`).",
                )
            if result.get("cleared"):
                gm_notify.send_gm_session(
                    session,
                    "debuglog: host evidence files deleted after the Gist.",
                )
        print(f"[gm_debug_export] analyze: {msg}", flush=True)
    except Exception as exc:
        print(f"[gm_debug_export] analyze unexpected error: {exc}", flush=True)
        if session is not None:
            gm_notify.send_gm_session(
                session, f"debuglog: analyze error: {exc}",
            )


def schedule_push(game, *, channel=None, reporter="?", session=None, root=None):
    """Fire-and-forget push. Returns True if a task was scheduled."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        result = push_sync(channel, reporter=reporter, root=root)
        if session is not None:
            from engine import gm_notify

            gm_notify.send_gm_session(
                session,
                "debuglog: " + (result.get("message") or ""),
            )
        return result.get("ok", False)

    task = loop.create_task(_push_async(channel, reporter, session, root))
    task.add_done_callback(_log_task_exception)
    return True


def schedule_analyze(game, *, channel=None, reporter="?", session=None, root=None):
    """Fire-and-forget analyze (push + capture off + clear after Gist)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        result = analyze_sync(channel, reporter=reporter, root=root)
        if session is not None:
            from engine import gm_notify

            gm_notify.send_gm_session(
                session,
                "debuglog: " + (result.get("message") or ""),
            )
        return result.get("ok", False)

    task = loop.create_task(_analyze_async(channel, reporter, session, root))
    task.add_done_callback(_log_task_exception)
    return True
