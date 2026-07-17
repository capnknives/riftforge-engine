"""
bug_webhook.py -- optional outbound POST to the Cursor bug-fixer automation.

Why this module exists (and why it is NOT inside reports.py):
  engine/reports.py is documented as "File I/O only: no networking". The local
  JSONL log must keep working even if Cursor is down, misconfigured, or the
  URL is unset. Player `bug` only writes the local log.

  Webhook POSTs are GM-on-demand only: `squashbugs` / `fixbugs` in commands.py
  call schedule_open_bugs() / schedule_bug_report(). That way a public playtest
  can leave the URL configured without every stranger's `bug` firing Cursor.

Only *bugs* fire this webhook -- suggestions stay local (the Cursor fixer
automation is for reproducible game bugs, not feature ideas).

Networking here is deliberate and narrow: one HTTPS POST via the standard
library (urllib). No third-party HTTP client. The game is single-threaded
asyncio, so we never block the session on a slow webhook -- schedule()
creates a background task that runs the blocking urllib call in
asyncio.to_thread().

Configure with environment variables (see .env.example and
docker-compose.yml):

  CURSOR_BUG_WEBHOOK_URL   -- Cursor Automations webhook endpoint
  CURSOR_BUG_WEBHOOK_AUTH  -- Bearer token from "Generate auth header"
                              in the Automations UI (required for POSTs
                              to succeed; URL alone returns HTTP 401)

If the URL is unset/empty, schedule() is a silent no-op so production-ish
runs without a fixer stay quiet.
"""

import asyncio
import json
import os
import urllib.error
import urllib.request


# Env var names -- keep these strings in one place so callers/docs/tests agree.
ENV_VAR = "CURSOR_BUG_WEBHOOK_URL"
AUTH_ENV_VAR = "CURSOR_BUG_WEBHOOK_AUTH"

# Print the missing-auth warning at most once per process.
_warned_missing_auth = False

# How long a single POST may block the worker thread before we give up.
# Fail-soft either way: a timeout is logged and the local report stays filed.
_POST_TIMEOUT_SECONDS = 15


def webhook_url():
    """Return the configured webhook URL, or '' if unset.

    Stripping whitespace so a compose typo like ' URL ' still works, and so
    an explicitly empty value (CURSOR_BUG_WEBHOOK_URL=) disables the hook.
    """
    return os.environ.get(ENV_VAR, "").strip()


def webhook_auth_token():
    """Return the Bearer token value (no ``Bearer `` prefix), or '' if unset.

    Cursor's UI copies the full header as ``Authorization: Bearer crsr_...``.
    Accept either the raw ``crsr_...`` token or that full header line so a
    paste from the Automations dashboard still works.
    """
    raw = os.environ.get(AUTH_ENV_VAR, "").strip()
    if not raw:
        return ""
    # Case-insensitive strip of an optional "Authorization: Bearer " prefix.
    lower = raw.lower()
    if lower.startswith("authorization:"):
        raw = raw.split(":", 1)[1].strip()
        lower = raw.lower()
    if lower.startswith("bearer "):
        return raw[7:].strip()
    return raw


def request_headers():
    """HTTP headers for the outbound webhook POST."""
    headers = {"Content-Type": "application/json"}
    token = webhook_auth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _maybe_warn_missing_auth():
    """Log once when a URL is configured but auth is not."""
    global _warned_missing_auth
    if _warned_missing_auth or not webhook_url() or webhook_auth_token():
        return
    _warned_missing_auth = True
    print(
        f"[bug_webhook] {ENV_VAR} is set but {AUTH_ENV_VAR} is missing -- "
        "Cursor will reject POSTs with HTTP 401. See .env.example.",
        flush=True,
    )


def payload_from_record(record_payload):
    """Build the JSON body the Cursor automation expects.

    reports.record() already shaped the local log entry (time, reporter,
    description, history, errors, status, id). The webhook body is that
    same dict plus an explicit kind='bug' so the automation can tell bugs
    from anything else without guessing from the URL alone.
    """
    body = dict(record_payload)
    body["kind"] = "bug"
    return body


def post_sync(url, payload, *, headers=None):
    """Blocking HTTPS POST of one bug-report JSON body.

    Uses urllib (stdlib) -- no requests/httpx. Callers should prefer
    schedule_bug_report() so the play loop never waits on this. Smoke tests
    monkeypatch this function to assert without hitting the network.

    headers= defaults to request_headers() so tests can inject or capture
    the Authorization header without mutating os.environ.
    """
    data = json.dumps(payload).encode("utf-8")
    # Request(..., method='POST') needs Python 3.3+; we require 3.11+.
    req = urllib.request.Request(
        url,
        data=data,
        headers=dict(headers if headers is not None else request_headers()),
        method="POST",
    )
    # urlopen follows redirects and raises URLError/HTTPError on failure.
    with urllib.request.urlopen(req, timeout=_POST_TIMEOUT_SECONDS) as resp:
        # Drain the body so the connection can close cleanly; we don't
        # care about the response content beyond "it didn't raise".
        resp.read()
        return resp.status


async def post_async(url, payload):
    """Run post_sync off the event-loop thread; log and swallow errors.

    asyncio.to_thread (3.9+) hands the blocking urllib call to the default
    executor so the single-threaded game loop can keep serving players.
    Any failure here is printed and ignored -- filing the local log already
    succeeded before schedule_bug_report() was called.
    """
    try:
        headers = request_headers()
        status = await asyncio.to_thread(post_sync, url, payload, headers=headers)
        print(
            f"[bug_webhook] POST ok (HTTP {status}) for bug "
            f"#{payload.get('id', '?')} from {payload.get('reporter', '?')}",
            flush=True,
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        # Expected failure modes: DNS, TLS, 4xx/5xx, timeout, connection reset.
        print(f"[bug_webhook] POST failed (report still filed locally): {exc}", flush=True)
    except Exception as exc:
        # Belt-and-suspenders: never let an unexpected bug in this helper
        # become an unretrieved task exception on the event loop.
        print(f"[bug_webhook] unexpected error (report still filed locally): {exc}", flush=True)


def _log_task_exception(task):
    """Done-callback: surface a task crash that somehow escaped post_async."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        print(f"[bug_webhook] background task crashed: {exc}", flush=True)


def schedule_open_bugs(directory, *, bug_ids=None):
    """Re-POST open bugs from bug_reports.log to the fixer webhook.

    bug_ids=None sends every open entry; otherwise only the listed ids that
    are still open. Returns (scheduled_count, matched_count).
    """
    from engine import reports

    open_bugs = [
        entry for entry in reports.recent(reports.BUG, None, directory=directory)
        if entry.get("status", "open") == "open"
    ]
    if bug_ids is not None:
        wanted = set(bug_ids)
        open_bugs = [entry for entry in open_bugs if entry.get("id") in wanted]

    scheduled = 0
    for entry in open_bugs:
        if schedule_bug_report(entry):
            scheduled += 1
    return scheduled, len(open_bugs)


def schedule_bug_report(record_payload, *, url=None):
    """Fire-and-forget webhook for one bug payload (GM squashbugs path).

    Returns True if a background task was scheduled, False if skipped
    (no URL configured, or no running asyncio loop -- e.g. a sync unit
    test that never entered asyncio.run). Never raises to the caller.

    url= lets tests inject a fake endpoint without mutating os.environ.
    Player `bug` does NOT call this -- only GM squashbugs / fixbugs do.
    """
    target = (url if url is not None else webhook_url())
    if not target:
        return False

    _maybe_warn_missing_auth()
    body = payload_from_record(record_payload)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop: cannot create_task. Sync smoke paths that don't
        # go through Session.play() hit this; production play always has a
        # loop. Don't fall back to a blocking POST -- that would stall a
        # command handler if someone called us from the wrong context.
        print(
            "[bug_webhook] no running event loop -- skipping POST "
            "(report still filed locally)",
            flush=True,
        )
        return False

    task = loop.create_task(post_async(target, body))
    task.add_done_callback(_log_task_exception)
    return True
