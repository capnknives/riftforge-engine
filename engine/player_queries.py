"""player_queries.py -- threaded player help tickets (JSONL beside report logs).

Separate from ``bug_reports.log`` / ``suggestions.log`` -- helpers tutor
players; staff still use bug/suggest for code and design work.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

LOG_NAME = "player_queries.log"
STATUSES = ("open", "closed")
_PENDING_NOTIFY_NAME = ".query_reply_pending.json"


class PlayerQueriesIOError(OSError):
    """Query log read/write failed."""


def log_path(directory="."):
    return os.path.join(directory or ".", LOG_NAME)


def pending_notify_path(directory="."):
    return os.path.join(directory or ".", _PENDING_NOTIFY_NAME)


def ensure_log(directory="."):
    """Create an empty query log when missing (Docker bind-mount safe)."""
    from engine import thread_log

    thread_log.ensure_file(log_path(directory), log_label="player_queries")


def _read_lines(directory):
    from engine import thread_log

    try:
        return thread_log.read_raw_lines(log_path(directory))
    except thread_log.ThreadLogIOError as exc:
        raise PlayerQueriesIOError(str(exc)) from exc


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _parse_line(raw, line_no):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError:
        return None
    entry["id"] = line_no
    entry.setdefault("status", "open")
    entry.setdefault("messages", [])
    return entry


def recent(directory=".", *, open_only=False):
    """All queries oldest-first; optional open-only filter."""
    entries = []
    for line_no, raw in enumerate(_read_lines(directory), start=1):
        entry = _parse_line(raw, line_no)
        if entry is None:
            continue
        if open_only and entry.get("status") != "open":
            continue
        entries.append(entry)
    return entries


def get_by_id(query_id, directory="."):
    if query_id < 1:
        return None
    lines = _read_lines(directory)
    if query_id > len(lines):
        return None
    return _parse_line(lines[query_id - 1], query_id)


def by_reporter(reporter_key, directory=".", *, open_only=True):
    needle = (reporter_key or "").strip()
    if not needle:
        return []
    out = []
    for entry in recent(directory, open_only=open_only):
        if entry.get("reporter_key") != needle:
            continue
        out.append(entry)
    return out


def _rewrite(directory, query_id, payload):
    from engine import thread_log

    try:
        return thread_log.rewrite_line(
            log_path(directory),
            query_id,
            payload,
            compact=True,
            missing="no query",
        )
    except thread_log.ThreadLogIOError as exc:
        raise PlayerQueriesIOError(str(exc)) from exc


def open_query(
    reporter_key,
    description,
    directory=".",
    *,
    account="",
):
    """Append one open query; returns the stored payload with id."""
    text = (description or "").strip()
    if not text:
        raise ValueError("query text required")
    now = _now_iso()
    msg = {
        "time": now,
        "author": reporter_key,
        "text": text,
        "role": "player",
    }
    payload = {
        "time": now,
        "created": now,
        "updated": now,
        "reporter_key": reporter_key,
        "account": (account or reporter_key or "").strip(),
        "description": text,
        "status": "open",
        "messages": [msg],
    }
    from engine import thread_log

    try:
        payload = thread_log.append_line(
            log_path(directory), payload, compact=True,
        )
    except thread_log.ThreadLogIOError as exc:
        raise PlayerQueriesIOError(
            f"cannot append query log {log_path(directory)!r}: {exc}"
        ) from exc
    return payload


def append_comment(
    query_id,
    author_key,
    text,
    directory=".",
    *,
    role="player",
):
    """Add one message to an existing query thread."""
    body = (text or "").strip()
    if not body:
        raise ValueError("comment text required")
    entry = get_by_id(query_id, directory)
    if entry is None:
        raise IndexError(f"no query #{query_id}")
    if entry.get("status") != "open":
        raise ValueError(f"query #{query_id} is {entry.get('status')}")
    now = _now_iso()
    messages = list(entry.get("messages") or [])
    messages.append({
        "time": now,
        "author": author_key,
        "text": body,
        "role": role,
    })
    entry["messages"] = messages
    entry["updated"] = now
    return _rewrite(directory, query_id, entry)


def close_query(query_id, directory=".", *, closer_key=""):
    entry = get_by_id(query_id, directory)
    if entry is None:
        raise IndexError(f"no query #{query_id}")
    if entry.get("status") == "closed":
        return entry
    now = _now_iso()
    entry["status"] = "closed"
    entry["updated"] = now
    if closer_key:
        entry["closed_by"] = closer_key
    return _rewrite(directory, query_id, entry)


def format_thread(entry, *, game=None):
    """Plain-text thread body for ``queryread``."""
    from engine.char_identity import reporter_display_name

    qid = entry.get("id", "?")
    status = entry.get("status", "open")
    reporter = entry.get("reporter_key", "?")
    if game is not None:
        reporter = reporter_display_name(game, reporter)
    lines = [
        f"Query #{qid} ({status})",
        f"opened: {entry.get('created', '?')}",
        f"reporter: {reporter}",
        "",
    ]
    for msg in entry.get("messages") or []:
        author = msg.get("author", "?")
        if game is not None:
            author = reporter_display_name(game, author)
        role = msg.get("role") or "player"
        stamp = msg.get("time", "?")
        text = (msg.get("text") or "").strip()
        lines.append(f"[{stamp}] {author} ({role}):")
        lines.append(text)
        lines.append("")
    return "\r\n".join(lines).rstrip()


def format_list_line(entry, *, game=None):
    from engine.char_identity import reporter_display_name

    qid = entry.get("id", "?")
    reporter = entry.get("reporter_key", "?")
    if game is not None:
        reporter = reporter_display_name(game, reporter)
    desc = (entry.get("description") or "").strip()
    if len(desc) > 72:
        desc = desc[:69] + "..."
    return f"#{qid} {reporter}: {desc} (updated {entry.get('updated', '?')})"
