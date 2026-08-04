"""
bug_report_payload.py -- enrich stored bug rows before the fixer webhook POST.

Player ``bug`` snapshots context at file time. GM ``squashbug`` / ``squashbugs``
re-reads the JSONL line and may refresh that snapshot when the reporter is still
in the world roster (live player or Echo), so the Cursor automation receives
loadout, combat_style, cadence, and fight topology as they are *now*, not
only as they were when the ticket was opened.

Networking stays in bug_webhook.py; this module only shapes the JSON body.
"""

from __future__ import annotations

import copy
from datetime import datetime


def enrich_bug_payload(entry, game=None):
    """Return a webhook-ready bug dict with the fullest context we can attach.

    Always starts from the stored log entry (description, history, errors).
    When ``game`` is provided and ``find_character`` resolves the reporter,
    rebuilds ``context`` from ``report_context.build`` and keeps the filed
    snapshot under ``context_filed`` for comparison.
    """
    out = copy.deepcopy(entry)
    refresh = {"status": "stored_only"}

    if game is None:
        out["context_refresh"] = refresh
        return out

    reporter_key = (out.get("reporter") or "").strip()
    if not reporter_key:
        refresh["status"] = "no_reporter"
        out["context_refresh"] = refresh
        return out

    # When filed about another character, refresh *their* snapshot on squash.
    context_key = (out.get("subject") or "").strip() or reporter_key

    find = getattr(game, "find_character", None)
    character = find(context_key) if callable(find) else None
    if character is None:
        refresh["status"] = "subject_offline" if out.get("subject") else "reporter_offline"
        refresh["context_key"] = context_key
        out["context_refresh"] = refresh
        return out

    from engine import accounts as accounts_mod
    from engine import report_context

    fresh = report_context.build(character, game)
    account = accounts_mod.account_for_character(game, character)
    if account is not None:
        fresh = dict(fresh)
        fresh["account"] = account.display_name
        out["account"] = account.display_name

    if out.get("context"):
        out["context_filed"] = out["context"]
    out["context"] = fresh
    refresh = {
        "status": "refreshed",
        "at": datetime.now().isoformat(timespec="seconds"),
        "reporter": reporter_key,
        "context_key": context_key,
        "presence": (fresh.get("character") or {}).get("presence"),
    }
    out["context_refresh"] = refresh
    return out
