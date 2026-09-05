"""journal.py -- private per-character note list (engine kernel).

Blob-backed notes on ``Character.journal_entries``. Games keep share /
theft / echo-diary policy in their own modules; this file is cap, list,
write, erase.

stdlib only. No game imports.
"""

from __future__ import annotations

JOURNAL_CAP = 20
JOURNAL_TEXT_MAX = 2000


def entries(character):
    """Return the journal list (creates empty if missing)."""
    box = getattr(character, "journal_entries", None)
    if box is None:
        character.journal_entries = []
        return character.journal_entries
    return box


def format_list(character):
    """Lines listing journal entries (1-indexed)."""
    box = entries(character)
    if not box:
        return [
            "Your journal is empty. "
            "Type 'journal write <text>' to begin."
        ]
    lines = [f"Journal ({len(box)}/{JOURNAL_CAP}):"]
    for i, entry in enumerate(box, start=1):
        text = entry.get("text", "")
        if len(text) > 60:
            text = text[:57] + "..."
        lines.append(f"  {i}. {text}")
    return lines


def write_entry(character, text, game):
    """Append a journal entry. Returns (ok, message)."""
    body = (text or "").strip()
    if not body:
        return False, "Usage: journal write <text>"
    if len(body) > JOURNAL_TEXT_MAX:
        return False, f"Entries are limited to {JOURNAL_TEXT_MAX} characters."
    box = entries(character)
    ticks = int(getattr(game, "game_time_ticks", 0) or 0) if game else 0
    box.append({"text": body, "tick": ticks})
    while len(box) > JOURNAL_CAP:
        box.pop(0)
    return True, f"Written. ({len(box)}/{JOURNAL_CAP} entries)"


def erase_entry(character, which):
    """Erase one entry by 1-based index, or clear all. Returns (ok, message)."""
    box = entries(character)
    flag = (which or "").strip().lower()
    if flag in ("all", "clear", "*"):
        n = len(box)
        box.clear()
        return True, f"Cleared {n} journal entry(ies)."
    try:
        n = int(flag)
    except (TypeError, ValueError):
        return False, "Usage: journal erase <number|all>"
    if n < 1 or n > len(box):
        return False, "No entry with that number."
    box.pop(n - 1)
    return True, "Entry erased."
