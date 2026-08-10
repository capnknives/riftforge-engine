"""
gm_audit.py -- append-only JSONL for staff world/account mutations.

Lives beside ``riftforge.db`` (``Game.report_dir``) with the same treatment
as ``bug_reports.log`` -- host-side runtime data, never committed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

FILENAME = "gm_audit.jsonl"


def _path(directory):
    """Absolute path for the audit log under ``directory``."""
    return os.path.join(directory or ".", FILENAME)


def actor_label(character):
    """Stable staff-facing actor key for audit rows."""
    from engine.command_support import strip_ephemeral_storage_prefix

    return strip_ephemeral_storage_prefix(
        getattr(character, "key", None) or "?"
    )


def record(actor, verb, summary, *, directory=".", fields=None):
    """Append one staff mutation row. Returns the payload written."""
    payload = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "actor": (actor or "?").strip() or "?",
        "verb": (verb or "?").strip() or "?",
        "summary": (summary or "").strip(),
    }
    if fields:
        for key, val in fields.items():
            if val is not None:
                payload[key] = val
    path = _path(directory)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")
    return payload
