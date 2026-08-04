"""
diag_export.py -- push lag / tick diagnosis logs to GitHub + Cursor.

Live hosts are awkward to SSH into from every agent session. GM
``gm diaglog push`` POSTs the NDJSON dump (plus a tick summary) to:

  1. A secret GitHub Gist (full body; returns a shareable URL)
  2. A sticky GitHub Issue comment with the Gist link + a short summary
     (default hub: RiftForge issue #195)

``gm diaglog analyze`` does push, turns NDJSON writers **off**, then POSTs
a dedicated Cursor automation webhook (``RIFTFORGE_DIAG_WEBHOOK_*``) so an
agent can diagnose the dump. Separate from the bug-fixer webhook
(``CURSOR_BUG_WEBHOOK_*`` / ``squashbugs``).

Networking is deliberate and narrow: stdlib ``urllib`` only, same spirit
as ``engine/bug_webhook.py``. The play loop never blocks --
``schedule_push`` / ``schedule_analyze`` use ``asyncio.to_thread``.

Configure (live ``.env`` -- never commit tokens):

  RIFTFORGE_DIAG_GITHUB_TOKEN   -- PAT with gist + issues:write (or classic
                                   repo + gist). Also accepts GITHUB_TOKEN.
  RIFTFORGE_DIAG_GITHUB_REPO    -- default ``capnknives/RiftForge``
  RIFTFORGE_DIAG_GITHUB_ISSUE   -- sticky issue number (default ``195``)
  RIFTFORGE_DIAG_LOG            -- optional override path for the NDJSON file
  RIFTFORGE_DIAG_WEBHOOK_URL    -- Cursor Automations webhook (lag analyzer)
  RIFTFORGE_DIAG_WEBHOOK_AUTH   -- Bearer token from that automation
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
import urllib.error
import urllib.request


# Env names -- single source so .env.example / help / tests agree.
TOKEN_ENV = "RIFTFORGE_DIAG_GITHUB_TOKEN"
TOKEN_FALLBACK_ENV = "GITHUB_TOKEN"
REPO_ENV = "RIFTFORGE_DIAG_GITHUB_REPO"
ISSUE_ENV = "RIFTFORGE_DIAG_GITHUB_ISSUE"
LOG_ENV = "RIFTFORGE_DIAG_LOG"
WEBHOOK_URL_ENV = "RIFTFORGE_DIAG_WEBHOOK_URL"
WEBHOOK_AUTH_ENV = "RIFTFORGE_DIAG_WEBHOOK_AUTH"
# Opt-in NDJSON writers (cadence / tick / …). Default OFF so a verified
# live host does not grow multi-MB debug logs every heartbeat. Toggle
# in-game with ``gm diaglog on|off`` (writes OVERRIDE_NAME) or set
# ENABLED_ENV=1 in .env, then ``gm diaglog push``.
ENABLED_ENV = "RIFTFORGE_DIAG_ENABLED"
# GM toggle file (gitignored), same idea as ``.auto_deploy_override``.
OVERRIDE_NAME = ".diag_enabled_override"

DEFAULT_REPO = "capnknives/RiftForge"
# Sticky hub created for live lag dumps (agents: gh issue view 195 --comments).
DEFAULT_ISSUE = 195
# NDJSON filename written by lag instrumentation (repo root).
DEFAULT_LOG_NAME = "debug-e4b2fd.log"
# Session id stamped into every append_event line (Gist / issue hub).
DEFAULT_SESSION_ID = "e4b2fd"
# Webhook payload kind -- Cursor automation filters on this.
WEBHOOK_KIND = "lag_diag"

# Values accepted in the override file / GM command.
_OVERRIDE_ON = "on"
_OVERRIDE_OFF = "off"

# GitHub issue comments cap at 65536; keep the sticky comment short and
# put the bulk in the Gist. Truncate the Gist body so a runaway log cannot
# blow the request.
_MAX_GIST_CHARS = 900_000
_POST_TIMEOUT_SECONDS = 30
_WEBHOOK_TIMEOUT_SECONDS = 15
_API_VERSION = "2022-11-28"
# Cap how many NDJSON lines we parse for the inline stats summary.
_SUMMARY_MAX_LINES = 5000

# Print the missing-auth warning at most once per process.
_warned_missing_webhook_auth = False


def repo_root():
    """Checkout root (parent of ``engine/``)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log_path():
    """Absolute path to the NDJSON diag log (env override or default name)."""
    override = os.environ.get(LOG_ENV, "").strip()
    if override:
        return override
    return os.path.join(repo_root(), DEFAULT_LOG_NAME)


def override_path(root=None):
    """Absolute path to the GM diag on/off override file."""
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


def clear_override(root=None):
    """Delete the GM override file if present. Returns True when removed."""
    path = override_path(root)
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def env_enabled():
    """True when ``ENABLED_ENV`` alone would turn writers on (no file check)."""
    raw = os.environ.get(ENABLED_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def diag_enabled(root=None):
    """True when in-process NDJSON tick writers should append.

    Priority: GM override file (``gm diaglog on|off``) wins over
    ``RIFTFORGE_DIAG_ENABLED``. With neither set, writers stay off.
    """
    override = read_override(root)
    if override == _OVERRIDE_ON:
        return True
    if override == _OVERRIDE_OFF:
        return False
    return env_enabled()


def append_event(
    hypothesis_id,
    location,
    message,
    data,
    *,
    run_id="post-fix",
    session_id=None,
):
    """Append one NDJSON line when ``diag_enabled()``; otherwise no-op.

    Shared by cadence / tick_registry / fuel / dominion / autosave so
    agents do not scatter open(debug-e4b2fd.log) blocks.
    """
    if not diag_enabled():
        return False
    try:
        payload = {
            "sessionId": session_id or DEFAULT_SESSION_ID,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
        return True
    except Exception:
        return False


def github_token():
    """Return the PAT, or '' if unset."""
    raw = (
        os.environ.get(TOKEN_ENV, "").strip()
        or os.environ.get(TOKEN_FALLBACK_ENV, "").strip()
    )
    if not raw:
        return ""
    # Accept a pasted ``Authorization: Bearer …`` line.
    lower = raw.lower()
    if lower.startswith("authorization:"):
        raw = raw.split(":", 1)[1].strip()
        lower = raw.lower()
    if lower.startswith("bearer "):
        return raw[7:].strip()
    return raw


def github_repo():
    """``owner/name`` for the sticky issue / API paths."""
    return os.environ.get(REPO_ENV, DEFAULT_REPO).strip() or DEFAULT_REPO


def github_issue_number():
    """Sticky issue number, or None if explicitly disabled (``0`` / empty)."""
    raw = os.environ.get(ISSUE_ENV, str(DEFAULT_ISSUE)).strip()
    if not raw or raw in ("0", "none", "off"):
        return None
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_ISSUE


def status_lines():
    """Human-readable config + log size for ``gm diaglog`` / ``status``."""
    path = log_path()
    exists = os.path.isfile(path)
    size = os.path.getsize(path) if exists else 0
    lines = 0
    if exists and size:
        try:
            with open(path, encoding="utf-8") as fh:
                lines = sum(1 for _ in fh)
        except OSError:
            lines = -1
    issue = github_issue_number()
    override = read_override()
    if override == _OVERRIDE_ON:
        writers = f"ON (gm diaglog on → {OVERRIDE_NAME})"
    elif override == _OVERRIDE_OFF:
        writers = f"off (gm diaglog off → {OVERRIDE_NAME})"
    elif diag_enabled():
        writers = f"ON ({ENABLED_ENV}=1)"
    else:
        writers = (
            f"off (gm diaglog on, or set {ENABLED_ENV}=1)"
        )
    return [
        f"Log: {path}",
        (
            f"Exists: {'yes' if exists else 'no'}  "
            f"bytes={size}  lines={lines if lines >= 0 else '?'}"
        ),
        f"NDJSON writers: {writers}",
        (
            f"Analyze webhook: "
            f"{'set' if webhook_url() else 'MISSING (' + WEBHOOK_URL_ENV + ')'}"
            + (
                ""
                if webhook_auth_token() or not webhook_url()
                else f"  auth=MISSING ({WEBHOOK_AUTH_ENV})"
            )
        ),
        f"Token: {'set' if github_token() else 'MISSING (' + TOKEN_ENV + ')'}",
        f"Repo: {github_repo()}",
        (
            f"Issue: #{issue} (https://github.com/{github_repo()}/issues/{issue})"
            if issue
            else "Issue: (disabled -- gist only)"
        ),
    ]


def read_log_text(*, max_chars=_MAX_GIST_CHARS):
    """Return log contents, truncated from the start if oversized."""
    path = log_path()
    if not os.path.isfile(path):
        return "", path, False
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return f"(read failed: {exc})", path, False
    if len(text) > max_chars:
        text = (
            f"(truncated: kept last {max_chars} of {len(text)} chars)\n"
            + text[-max_chars:]
        )
    return text, path, True


def clear_log():
    """Delete the NDJSON file. Returns (ok, message)."""
    path = log_path()
    if not os.path.isfile(path):
        return True, f"No log file at {path}."
    try:
        os.remove(path)
    except OSError as exc:
        return False, f"Could not delete {path}: {exc}"
    return True, f"Cleared {path}."


def _api_headers(token):
    """GitHub REST headers for Gist + Issues."""
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": _API_VERSION,
        "Content-Type": "application/json",
        "User-Agent": "riftforge-diag-export",
    }


def _http_json(method, url, payload, token):
    """Blocking JSON request; returns (status, parsed_body_or_None)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=_api_headers(token),
        method=method,
    )
    with urllib.request.urlopen(req, timeout=_POST_TIMEOUT_SECONDS) as resp:
        raw = resp.read()
        body = json.loads(raw.decode("utf-8")) if raw else None
        return resp.status, body


def build_tick_summary(game):
    """Compact text from ``game._tick_stats`` + Cadence budget meta."""
    ring = list(getattr(game, "_tick_stats", ()) or ())
    meta = getattr(game, "_cadence_budget_meta", None) or {}
    lines = [
        f"game_time_ticks={getattr(game, 'game_time_ticks', None)}",
        f"sessions={len(getattr(game, 'sessions', None) or [])}",
        f"characters={len(getattr(game, 'characters', ()) or ())}",
        f"rooms={len(getattr(game, 'rooms', {}) or {})}",
    ]
    if meta:
        lines.append(
            f"cadence budget used={meta.get('used')}/{meta.get('limit')} "
            f"cast={meta.get('population')} "
            f"(town={meta.get('town_npcs')} echoes={meta.get('echoes')})"
        )
    if not ring:
        lines.append("tick samples: (none yet)")
        return "\n".join(lines)
    totals = [s.get("total_ms", 0) for s in ring]
    last = ring[-1]
    slow = last.get("slow") or []
    slow_txt = ", ".join(f"{n}={ms:.1f}ms" for n, ms in slow[:8]) or "(none)"
    lines.append(
        f"tick last={last.get('total_ms', 0):.1f}ms "
        f"avg={sum(totals) / len(totals):.1f} "
        f"worst={max(totals):.1f} (n={len(ring)})"
    )
    lines.append(f"last slow=[{slow_txt}]")
    return "\n".join(lines)


def push_sync(game=None, *, reporter="?"):
    """Blocking: create Gist (+ optional issue comment). Returns result dict.

    result keys: ok, message, gist_url, issue_url, error
    """
    token = github_token()
    if not token:
        return {
            "ok": False,
            "message": (
                f"No GitHub token. Set {TOKEN_ENV} (or {TOKEN_FALLBACK_ENV}) "
                "in the live .env and restart / copyover."
            ),
            "gist_url": None,
            "issue_url": None,
            "error": "missing_token",
        }

    log_text, path, found = read_log_text()
    tick_summary = build_tick_summary(game) if game is not None else "(no game)"
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    filename = f"riftforge-diag-{int(time.time())}.ndjson"
    if not found or not log_text.strip():
        # Still push a stub so ops get the tick summary + config status.
        log_text = (
            f"(no NDJSON at {path} -- tick summary only)\n"
            + "\n".join(status_lines())
        )

    gist_body = {
        "description": (
            f"Riftforge lag diag from {reporter} @ {stamp}"
        ),
        "public": False,
        "files": {
            filename: {"content": log_text},
            "tick_summary.txt": {"content": tick_summary},
        },
    }
    try:
        status, gist = _http_json(
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
        repo = github_repo()
        comment = (
            f"### Lag diag push — `{reporter}` — {stamp}\n\n"
            f"**Gist (full NDJSON):** {gist_url}\n\n"
            f"```\n{tick_summary}\n```\n\n"
            f"Log path on host: `{path}`\n"
        )
        api = f"https://api.github.com/repos/{repo}/issues/{issue_n}/comments"
        try:
            _http_json("POST", api, {"body": comment}, token)
            issue_url = f"https://github.com/{repo}/issues/{issue_n}"
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            # Gist already landed -- report partial success.
            return {
                "ok": True,
                "message": (
                    f"Gist ok, but issue #{issue_n} comment failed: {exc}. "
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
            f"Pushed diag log. Hub: {hub}"
            + (f"  Gist: {gist_url}" if issue_url else "")
        ),
        "gist_url": gist_url,
        "issue_url": issue_url,
        "error": None,
    }


def _log_task_exception(task):
    """Done-callback: surface escaped task crashes."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        print(f"[diag_export] background task crashed: {exc}", flush=True)


async def _push_async(game, reporter, session):
    """Run push_sync off-loop; tell the GM the result."""
    try:
        result = await asyncio.to_thread(push_sync, game, reporter=reporter)
        msg = result.get("message") or "(no message)"
        prefix = "[GM] diaglog: "
        if session is not None and getattr(session, "send", None):
            session.send(prefix + msg)
            if result.get("gist_url"):
                session.send(prefix + f"gist {result['gist_url']}")
            if result.get("issue_url"):
                session.send(prefix + f"issue {result['issue_url']}")
        print(f"[diag_export] {msg}", flush=True)
    except Exception as exc:
        print(f"[diag_export] unexpected error: {exc}", flush=True)
        if session is not None and getattr(session, "send", None):
            session.send(f"[GM] diaglog: unexpected error: {exc}")


def schedule_push(game, *, reporter="?", session=None):
    """Fire-and-forget push. Returns True if a task was scheduled."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Sync smoke / no loop: run blocking so tests can assert.
        result = push_sync(game, reporter=reporter)
        if session is not None and getattr(session, "send", None):
            session.send("[GM] diaglog: " + (result.get("message") or ""))
        return result.get("ok", False)

    task = loop.create_task(_push_async(game, reporter, session))
    task.add_done_callback(_log_task_exception)
    return True


# --- Cursor lag-analyzer webhook (gm diaglog analyze) ----------------------


def webhook_url():
    """Return the configured Cursor lag-diag webhook URL, or '' if unset."""
    return os.environ.get(WEBHOOK_URL_ENV, "").strip()


def webhook_auth_token():
    """Bearer token for the lag-diag webhook (no ``Bearer `` prefix)."""
    raw = os.environ.get(WEBHOOK_AUTH_ENV, "").strip()
    if not raw:
        return ""
    lower = raw.lower()
    if lower.startswith("authorization:"):
        raw = raw.split(":", 1)[1].strip()
        lower = raw.lower()
    if lower.startswith("bearer "):
        return raw[7:].strip()
    return raw


def webhook_request_headers():
    """HTTP headers for the outbound Cursor automation POST."""
    headers = {"Content-Type": "application/json"}
    token = webhook_auth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _maybe_warn_missing_webhook_auth():
    """Log once when a URL is configured but auth is not."""
    global _warned_missing_webhook_auth
    if _warned_missing_webhook_auth or not webhook_url() or webhook_auth_token():
        return
    _warned_missing_webhook_auth = True
    print(
        f"[diag_export] {WEBHOOK_URL_ENV} is set but {WEBHOOK_AUTH_ENV} "
        "is missing -- Cursor will reject POSTs with HTTP 401. "
        "See .env.example / .cursor/automations/lag-diag-analyzer.",
        flush=True,
    )


def summarize_ndjson(text):
    """Cheap stats from NDJSON for the webhook body (no Gist fetch needed).

    Returns a dict with counts and ms averages for cadence / slow ticks.
    """
    from collections import defaultdict

    messages = defaultdict(int)
    echo_ms = []
    town_ms = []
    cadence_total = []
    slow_total = []
    handler_ms = defaultdict(list)
    lines_seen = 0
    for line in (text or "").splitlines():
        if lines_seen >= _SUMMARY_MAX_LINES:
            break
        line = line.strip()
        if not line or line.startswith("("):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        lines_seen += 1
        msg = row.get("message") or "?"
        messages[msg] += 1
        data = row.get("data") or {}
        if msg == "cadence_phase_breakdown":
            cadence_total.append(float(data.get("total_ms") or 0))
            phases = data.get("phases") or {}
            echo_ms.append(float(phases.get("echo_ms") or 0))
            town_ms.append(float(phases.get("town_ms") or 0))
        elif msg == "slow_or_spike_tick":
            slow_total.append(float(data.get("total_ms") or 0))
            for h in data.get("top_handlers") or []:
                name = h.get("name")
                if name:
                    handler_ms[name].append(float(h.get("ms") or 0))

    def _stat(xs):
        if not xs:
            return None
        return {
            "n": len(xs),
            "avg": round(statistics.mean(xs), 1),
            "p50": round(statistics.median(xs), 1),
            "max": round(max(xs), 1),
        }

    top_handlers = sorted(
        (
            {"name": n, "avg_ms": round(statistics.mean(xs), 1), "n": len(xs)}
            for n, xs in handler_ms.items()
        ),
        key=lambda row: -row["avg_ms"],
    )[:8]
    return {
        "ndjson_lines_parsed": lines_seen,
        "messages": dict(messages),
        "cadence_total_ms": _stat(cadence_total),
        "echo_ms": _stat(echo_ms),
        "town_ms": _stat(town_ms),
        "slow_tick_total_ms": _stat(slow_total),
        "top_handlers_by_avg": top_handlers,
    }


def build_analyze_payload(push_result, game=None, *, reporter="?"):
    """JSON body for the Cursor lag-diag automation."""
    log_text, path, _found = read_log_text()
    tick_summary = build_tick_summary(game) if game is not None else "(no game)"
    return {
        "kind": WEBHOOK_KIND,
        "reporter": reporter,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gist_url": (push_result or {}).get("gist_url"),
        "issue_url": (push_result or {}).get("issue_url"),
        "push_ok": bool((push_result or {}).get("ok")),
        "push_message": (push_result or {}).get("message"),
        "log_path": path,
        "tick_summary": tick_summary,
        "status_lines": status_lines(),
        "stats": summarize_ndjson(log_text),
        "hub_issue": (
            f"https://github.com/{github_repo()}/issues/{github_issue_number()}"
            if github_issue_number()
            else None
        ),
        "instructions_hint": (
            "Diagnose live tick lag from this dump. Prefer reading the "
            "gist_url NDJSON + stats. Open a PR only if you have a clear "
            "fix; otherwise comment findings on hub_issue / the PR body."
        ),
    }


def post_webhook_sync(url, payload, *, headers=None):
    """Blocking HTTPS POST of one lag-diag JSON body."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=dict(
            headers if headers is not None else webhook_request_headers()
        ),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_WEBHOOK_TIMEOUT_SECONDS) as resp:
        resp.read()
        return resp.status


def analyze_sync(game=None, *, reporter="?"):
    """Push Gist, turn writers off, POST Cursor webhook. Returns result dict.

    Always attempts ``set_override('off')`` after the push so a successful
    analyze cannot leave the host writing forever. Webhook skip (no URL)
    still turns writers off and returns ok=False with a clear message.
    """
    push_result = push_sync(game, reporter=reporter)
    # Stop NDJSON writers regardless of push success -- analyze ends capture.
    try:
        off_path = set_override(_OVERRIDE_OFF)
        writers_off = True
        writers_message = f"NDJSON writers off ({off_path})."
    except Exception as exc:
        writers_off = False
        writers_message = f"Could not turn writers off: {exc}"

    target = webhook_url()
    if not target:
        push_bit = (
            "Push ok"
            if push_result.get("ok")
            else f"Push incomplete ({push_result.get('error') or 'failed'})"
        )
        return {
            "ok": False,
            "message": (
                f"{push_bit}; writers off. No {WEBHOOK_URL_ENV} configured "
                "-- cannot queue Cursor analyzer. "
                + (push_result.get("message") or "")
            ),
            "gist_url": push_result.get("gist_url"),
            "issue_url": push_result.get("issue_url"),
            "webhook_status": None,
            "writers_off": writers_off,
            "error": "missing_webhook",
        }

    _maybe_warn_missing_webhook_auth()
    payload = build_analyze_payload(
        push_result, game, reporter=reporter
    )
    try:
        status = post_webhook_sync(target, payload)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "message": (
                f"Push ok-ish; writers off; webhook POST failed: {exc}. "
                f"Gist: {push_result.get('gist_url')}"
            ),
            "gist_url": push_result.get("gist_url"),
            "issue_url": push_result.get("issue_url"),
            "webhook_status": None,
            "writers_off": writers_off,
            "error": "webhook_failed",
        }

    hub = push_result.get("issue_url") or push_result.get("gist_url")
    return {
        "ok": True,
        "message": (
            f"Analyze queued (HTTP {status}). {writers_message} "
            f"Hub: {hub}"
        ),
        "gist_url": push_result.get("gist_url"),
        "issue_url": push_result.get("issue_url"),
        "webhook_status": status,
        "writers_off": writers_off,
        "error": None,
    }


def _should_announce_lag_queued(result):
    """Wiznet when we have a hub link, even if the Cursor webhook POST failed."""
    if not result:
        return False
    return bool(result.get("gist_url") or result.get("issue_url"))


def _announce_lag_queued_if_ready(game, result, reporter):
    if not _should_announce_lag_queued(result):
        return
    try:
        from engine import kokid_notify

        kokid_notify.announce_lag_queued(
            game,
            result.get("gist_url"),
            result.get("issue_url"),
            reporter,
        )
    except Exception as exc:
        print(
            f"[diag_export] kokid lag queued announce skipped: {exc}",
            flush=True,
        )


async def _analyze_async(game, reporter, session):
    """Run analyze_sync off-loop; tell the GM the result."""
    try:
        result = await asyncio.to_thread(
            analyze_sync, game, reporter=reporter
        )
        msg = result.get("message") or "(no message)"
        prefix = "[GM] diaglog: "
        if session is not None and getattr(session, "send", None):
            session.send(prefix + msg)
            if result.get("gist_url"):
                session.send(prefix + f"gist {result['gist_url']}")
            if result.get("issue_url"):
                session.send(prefix + f"issue {result['issue_url']}")
            if result.get("writers_off"):
                session.send(
                    prefix + "NDJSON writers are OFF "
                    "(re-enable with `gm diaglog on` to capture again)."
                )
        print(f"[diag_export] analyze: {msg}", flush=True)
        _announce_lag_queued_if_ready(game, result, reporter)
    except Exception as exc:
        print(f"[diag_export] analyze unexpected error: {exc}", flush=True)
        if session is not None and getattr(session, "send", None):
            session.send(f"[GM] diaglog: analyze error: {exc}")


def schedule_analyze(game, *, reporter="?", session=None):
    """Fire-and-forget analyze (push + webhook + writers off)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        result = analyze_sync(game, reporter=reporter)
        if session is not None and getattr(session, "send", None):
            session.send("[GM] diaglog: " + (result.get("message") or ""))
        _announce_lag_queued_if_ready(game, result, reporter)
        return result.get("ok", False)

    task = loop.create_task(_analyze_async(game, reporter, session))
    task.add_done_callback(_log_task_exception)
    return True
