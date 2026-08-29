"""channel_transcript.py -- append-only OOC / questions logs for staff mining.

JSONL beside riftforge.db (Game.report_dir), same pattern as help_misses
and town_stress. Never committed (.gitignore ``ooc.log`` / ``questions.log``).

File I/O only. Callers record(); disk failures are swallowed so chat never
crashes a full disk.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

OOC_FILE = "ooc.log"
QUESTIONS_FILE = "questions.log"

_FILES = {
    "ooc": OOC_FILE,
    "questions": QUESTIONS_FILE,
}


def _path(channel: str, directory: str) -> str:
    name = _FILES.get(channel) or f"{channel}.log"
    return os.path.join(directory, name)


def _report_dir(game) -> str:
    if game is None:
        return "."
    return getattr(game, "report_dir", None) or "."


def record(channel: str, *, face: str, message: str, game=None, speaker: str = "", extra=None):
    """Append one public channel line. Returns payload or None on failure."""
    name = str(channel or "").strip().lower()
    if name not in _FILES:
        return None
    text = str(message or "").strip()
    if not text:
        return None
    payload = {
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "channel": name,
        "face": str(face or "").strip() or "?",
        "speaker": str(speaker or "").strip(),
        "message": text[:2000],
    }
    if extra and isinstance(extra, dict):
        payload["extra"] = extra
    directory = _report_dir(game)
    path = _path(name, directory)
    try:
        os.makedirs(directory, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return payload
