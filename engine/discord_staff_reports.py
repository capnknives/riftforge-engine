"""discord_staff_reports.py -- brief bug/suggestion lines to Discord #staff.

Player ``bug`` / ``suggest`` already ping online staff in-game
(``engine/bug_filing.record_and_confirm`` → ``gm_notify.ping_gms``). This
module mirrors a **short** summary to Discord #staff via ``discord_bridge`` —
id, reporter label, truncated description only. No command history, tracebacks,
or diagnostic context blob (those stay in the JSONL log for GM triage).

Configure with tag ``bug_report`` / ``suggestion`` on
``DISCORD_BRIDGE_CHANNELS`` or per-tag webhooks
(``DISCORD_BRIDGE_WEBHOOK_BUG_REPORT``, ``DISCORD_BRIDGE_WEBHOOK_SUGGESTION``).
Unset mapping = silent no-op (same as other bridge tags).
"""

from __future__ import annotations

from engine import discord_bridge
from engine import reports


_BRIEF_DESC_MAX = 200


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
        "bug_report" if kind == reports.BUG else "suggestion",
        None,
        line,
    )


def schedule_report(kind: str, payload: dict) -> bool:
    """Queue one brief staff-channel post; return True if scheduled."""
    if kind not in (reports.BUG, reports.SUGGEST):
        return False
    tag = "bug_report" if kind == reports.BUG else "suggestion"
    body = format_brief(kind, payload)
    # format_tagged_message already applied; pass body as-is (starts with [).
    return discord_bridge.schedule_discord(tag, body, kind=None)
