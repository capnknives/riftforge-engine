"""discord_staff_reports.py -- brief bug/suggestion lines to Discord #staff.

Player ``bug`` / ``suggest`` already ping online staff in-game
(``engine/bug_filing.record_and_confirm`` → ``gm_notify.ping_gms``). This
module mirrors a **short** summary to Discord #staff via ``discord_bridge`` —
id, reporter label, truncated description only. No command history, tracebacks,
or diagnostic context blob (those stay in the JSONL log for GM triage).

When the ``bug_report`` tag is mapped on the **bot** channel list (not a
webhook), the posted message id is remembered so a checkmark reaction is
added when the ticket is marked ``resolved`` in ``bug_reports.log`` (manual
``gm reports resolve`` or auto-deploy Fix ships).

Configure with tag ``bug_report`` / ``suggestion`` on
``DISCORD_BRIDGE_CHANNELS`` or per-tag webhooks
(``DISCORD_BRIDGE_WEBHOOK_BUG_REPORT``, ``DISCORD_BRIDGE_WEBHOOK_SUGGESTION``).
Webhook-only ``bug_report`` posts still work but cannot receive resolve
reactions — use the bot channel map for checkmarks.
"""

from __future__ import annotations

import json
import os

from engine import discord_bridge
from engine import reports

BUG_REPORT_TAG = "bug_report"
SUGGESTION_TAG = "suggestion"
STORAGE_NAME = ".discord_bug_posts.json"
RESOLVED_REACTION = "\u2705"  # checkmark — plain Unicode for Discord REST

_BRIEF_DESC_MAX = 200
_hooks_registered = False


def _reporter_label(payload: dict) -> str:
    """One-line who filed the report (account suffix when present)."""
    reporter = str(payload.get("reporter") or "?").strip()
    account = str(payload.get("account") or "").strip()
    if not account:
        ctx = payload.get("context")
        if isinstance(ctx, dict):
            account = str(ctx.get("account") or "").strip()
    if account:
        return f"{reporter}({account})"
    return reporter


def _truncate(text: str, limit: int = _BRIEF_DESC_MAX) -> str:
    """Single-line, client-wrappable brief body."""
    clean = (text or "").replace("\n", " ").replace("\r", " ").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def format_brief(kind: str, payload: dict) -> str:
    """Plain Discord body for one filed report (no debug attachment)."""
    entry_id = payload.get("id", "?")
    desc = _truncate(str(payload.get("description") or ""))
    who = _reporter_label(payload)
    subject_key = str(payload.get("subject") or "").strip()
    about = f" about {subject_key}" if subject_key else ""

    if kind == reports.BUG:
        header = f"[BUG REPORT #{entry_id}]"
    elif kind == reports.SUGGEST:
        header = f"[SUGGESTION #{entry_id}]"
    elif kind == reports.HELP:
        header = f"[HELP IDEA #{entry_id}]"
    elif kind == reports.TYPO:
        header = f"[TYPO #{entry_id}]"
    else:
        header = f"[REPORT #{entry_id}]"

    line = f"{header}\n{who} filed{about}: {desc}"
    return discord_bridge.format_tagged_message(
        BUG_REPORT_TAG if kind == reports.BUG else SUGGESTION_TAG,
        None,
        line,
    )


def storage_path(directory: str = ".") -> str:
    """Path to the bug-id → Discord message map beside the report logs."""
    return os.path.join(directory or ".", STORAGE_NAME)


def load_post_map(directory: str = ".") -> dict:
    """Return ``{bug_id_str: {channel_id, message_id}}`` (empty when missing)."""
    path = storage_path(directory)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_post_ref(
    directory: str, bug_id, channel_id: str, message_id: str,
) -> None:
    """Remember which Discord message mirrors one in-game bug report."""
    try:
        key = str(int(bug_id))
    except (TypeError, ValueError):
        return
    data = load_post_map(directory)
    data[key] = {
        "channel_id": str(channel_id),
        "message_id": str(message_id),
    }
    path = storage_path(directory)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
    except OSError as exc:
        print(
            f"[discord_staff_reports] could not save {STORAGE_NAME}: {exc!r}",
            flush=True,
        )


def post_ref_for_bug(directory: str, bug_id) -> dict | None:
    """Lookup stored Discord coordinates for one bug id, or None."""
    try:
        key = str(int(bug_id))
    except (TypeError, ValueError):
        return None
    ref = load_post_map(directory).get(key)
    if not isinstance(ref, dict):
        return None
    channel_id = str(ref.get("channel_id") or "").strip()
    message_id = str(ref.get("message_id") or "").strip()
    if not channel_id or not message_id:
        return None
    return {"channel_id": channel_id, "message_id": message_id}


def bug_posts_trackable() -> bool:
    """True when bug_report can use the bot API (token + channel, no webhook)."""
    if discord_bridge.webhook_url_for_tag(BUG_REPORT_TAG):
        return False
    token = discord_bridge.bot_token()
    channel_id = discord_bridge.channel_id_for_tag(BUG_REPORT_TAG)
    return bool(token and channel_id)


def schedule_report(kind: str, payload: dict, *, directory: str = ".") -> bool:
    """Queue one brief staff-channel post; return True if scheduled."""
    if kind not in (reports.BUG, reports.SUGGEST):
        return False
    tag = BUG_REPORT_TAG if kind == reports.BUG else SUGGESTION_TAG
    body = format_brief(kind, payload)
    if kind == reports.BUG and bug_posts_trackable():
        bug_id = payload.get("id")

        def _remember(channel_id: str, message_id: str) -> None:
            if bug_id is not None:
                save_post_ref(directory, bug_id, channel_id, message_id)

        return discord_bridge.schedule_bot_post_with_id(
            tag, body, on_posted=_remember,
        )
    return discord_bridge.schedule_discord(tag, body, kind=None)


def schedule_resolved_reaction(directory: str, bug_id) -> bool:
    """Add a checkmark to the Discord post for a resolved bug report."""
    ref = post_ref_for_bug(directory, bug_id)
    if not ref:
        return False
    return discord_bridge.schedule_message_reaction(
        ref["channel_id"],
        ref["message_id"],
        RESOLVED_REACTION,
    )


def _on_report_marked(kind, payload, *, old_status, directory, game=None):
    """After-mark hook: react on Discord when a bug becomes resolved."""
    del game  # unused — hook signature matches reports.register_after_mark
    if kind != reports.BUG:
        return
    if payload.get("status") != "resolved":
        return
    if old_status == "resolved":
        return
    bug_id = payload.get("id")
    if bug_id is None:
        return
    schedule_resolved_reaction(directory, bug_id)


def register_hooks() -> None:
    """Wire resolve reactions once at process boot (``server.py``)."""
    global _hooks_registered
    if _hooks_registered:
        return
    _hooks_registered = True
    reports.register_after_mark(_on_report_marked)
