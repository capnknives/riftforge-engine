"""
ops_webhook.py -- optional outbound POST for ops alerts (crash hold, save streak).

Separate from bug/suggestion/diag webhooks. Fire-and-forget when
``RIFTFORGE_OPS_WEBHOOK_URL`` is set -- Discord incoming webhook,
Cursor automation, or any JSON POST endpoint that accepts our payload.

Networking stays out of game logic except this narrow helper (same pattern
as ``engine/bug_webhook.py``).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request

ENV_VAR = "RIFTFORGE_OPS_WEBHOOK_URL"
AUTH_ENV_VAR = "RIFTFORGE_OPS_WEBHOOK_AUTH"
_POST_TIMEOUT_SECONDS = 15
_warned_missing_auth = False


def webhook_url():
    """Configured webhook URL, or '' when disabled."""
    return os.environ.get(ENV_VAR, "").strip()


def webhook_auth_token():
    """Bearer token (no ``Bearer `` prefix), or ''."""
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
    headers = {"Content-Type": "application/json"}
    token = webhook_auth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _maybe_warn_missing_auth():
    global _warned_missing_auth
    if _warned_missing_auth or not webhook_url() or webhook_auth_token():
        return
    _warned_missing_auth = True
    print(
        f"[ops_webhook] {ENV_VAR} is set but {AUTH_ENV_VAR} is missing -- "
        "POSTs may return HTTP 401. See .env.example.",
        flush=True,
    )


def build_payload(kind, summary, **fields):
    """JSON body for one ops alert."""
    body = {
        "kind": (kind or "ops").strip() or "ops",
        "summary": (summary or "").strip(),
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    for key, val in fields.items():
        if val is not None:
            body[key] = val
    return body


def post_sync(url, payload, *, headers=None):
    """Blocking POST -- tests monkeypatch this."""
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
    try:
        status = await asyncio.to_thread(
            post_sync, url, payload, headers=request_headers(),
        )
        print(
            f"[ops_webhook] POST ok (HTTP {status}) kind={payload.get('kind', '?')}",
            flush=True,
        )
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        OSError,
    ) as exc:
        print(f"[ops_webhook] POST failed: {exc}", flush=True)
    except Exception as exc:
        print(f"[ops_webhook] unexpected error: {exc}", flush=True)


def _log_task_exception(task):
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        print(f"[ops_webhook] background task crashed: {exc}", flush=True)


def schedule_ops_alert(kind, summary, **fields):
    """Queue one ops alert POST. Returns True when scheduled."""
    target = webhook_url()
    if not target:
        return False
    _maybe_warn_missing_auth()
    body = build_payload(kind, summary, **fields)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        print(
            "[ops_webhook] no running event loop -- skipping POST",
            flush=True,
        )
        return False
    task = loop.create_task(post_async(target, body))
    task.add_done_callback(_log_task_exception)
    return True
