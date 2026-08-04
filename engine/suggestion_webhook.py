"""
suggestion_webhook.py -- optional outbound POST to the Cursor suggestion
implementer automation.

Mirrors engine/bug_webhook.py but for suggestions.log entries only. Player
`suggest` only writes the local log; GM `sendsuggest` / `squashsuggest`
call schedule_suggestion_report() / schedule_open_suggestions().

Configure with:

  CURSOR_SUGGESTION_WEBHOOK_URL   -- Cursor Automations webhook endpoint
  CURSOR_SUGGESTION_WEBHOOK_AUTH  -- Bearer token from Generate auth header

See .cursor/automations/in-game-suggestion-implementer.SETUP.md
"""

import asyncio
import json
import os
import urllib.error
import urllib.request


ENV_VAR = "CURSOR_SUGGESTION_WEBHOOK_URL"
AUTH_ENV_VAR = "CURSOR_SUGGESTION_WEBHOOK_AUTH"

_warned_missing_auth = False
_POST_TIMEOUT_SECONDS = 15


def webhook_url():
    """Return the configured webhook URL, or '' if unset."""
    return os.environ.get(ENV_VAR, "").strip()


def webhook_auth_token():
    """Return the Bearer token value (no ``Bearer `` prefix), or '' if unset."""
    raw = os.environ.get(AUTH_ENV_VAR, "").strip()
    if not raw:
        return ""
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
        f"[suggestion_webhook] {ENV_VAR} is set but {AUTH_ENV_VAR} is missing -- "
        "Cursor will reject POSTs with HTTP 401. See .env.example.",
        flush=True,
    )


def payload_from_record(record_payload):
    """Build the JSON body the suggestion automation expects."""
    body = dict(record_payload)
    body["kind"] = "suggest"
    return body


def post_sync(url, payload, *, headers=None):
    """Blocking HTTPS POST of one suggestion JSON body."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=dict(headers if headers is not None else request_headers()),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_POST_TIMEOUT_SECONDS) as resp:
        resp.read()
        return resp.status


async def post_async(url, payload):
    """Run post_sync off the event-loop thread; log and swallow errors."""
    try:
        headers = request_headers()
        status = await asyncio.to_thread(post_sync, url, payload, headers=headers)
        print(
            f"[suggestion_webhook] POST ok (HTTP {status}) for suggestion "
            f"#{payload.get('id', '?')} from {payload.get('reporter', '?')}",
            flush=True,
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        print(
            f"[suggestion_webhook] POST failed (report still filed locally): {exc}",
            flush=True,
        )
    except Exception as exc:
        print(
            f"[suggestion_webhook] unexpected error (report still filed locally): {exc}",
            flush=True,
        )


def _log_task_exception(task):
    """Done-callback: surface a task crash that escaped post_async."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        print(f"[suggestion_webhook] background task crashed: {exc}", flush=True)


def schedule_open_suggestions(directory, *, suggestion_ids=None):
    """Re-POST open suggestions from suggestions.log to the implementer webhook.

    suggestion_ids=None sends every open entry; otherwise only listed ids that
    are still open. Returns ``(scheduled_count, matched_count, scheduled_ids)``.
    """
    from engine import reports

    open_suggestions = [
        entry for entry in reports.recent(reports.SUGGEST, None, directory=directory)
        if entry.get("status", "open") == "open"
    ]
    if suggestion_ids is not None:
        wanted = set(suggestion_ids)
        open_suggestions = [
            entry for entry in open_suggestions if entry.get("id") in wanted
        ]

    scheduled = 0
    scheduled_ids = []
    for entry in open_suggestions:
        if schedule_suggestion_report(entry):
            scheduled += 1
            sid = entry.get("id")
            if sid is not None:
                try:
                    from engine import kokid_notify

                    sid = int(sid)
                    scheduled_ids.append(sid)
                    kokid_notify.watch_suggestion_ids([sid])
                except Exception as exc:
                    print(f"[suggestion_webhook] kokid watch skipped: {exc}", flush=True)
                    sid = entry.get("id")
                    if sid is not None:
                        try:
                            scheduled_ids.append(int(sid))
                        except (TypeError, ValueError):
                            pass
    return scheduled, len(open_suggestions), scheduled_ids


def schedule_suggestion_report(record_payload, *, url=None):
    """Fire-and-forget webhook for one suggestion payload (GM sendsuggest path).

    Returns True if a background task was scheduled, False if skipped.
    Player `suggest` does NOT call this -- only GM sendsuggest / squashsuggest.
    """
    target = (url if url is not None else webhook_url())
    if not target:
        return False

    _maybe_warn_missing_auth()
    body = payload_from_record(record_payload)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        print(
            "[suggestion_webhook] no running event loop -- skipping POST "
            "(report still filed locally)",
            flush=True,
        )
        return False

    task = loop.create_task(post_async(target, body))
    task.add_done_callback(_log_task_exception)
    return True
