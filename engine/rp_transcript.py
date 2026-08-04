"""
rp_transcript.py -- D69 player RP transcript capture (rplog verb).

Buffers room-visible lines the player actually sees (say/emote/tell in).
Not the GM ``log`` / ``echolog`` kit soak tools.
"""

from __future__ import annotations

_MAX_LINES = 500
_MAX_LINE_LEN = 400


def ensure_defaults(character):
    """Attach transcript fields if missing."""
    if not hasattr(character, "rplog_active"):
        character.rplog_active = False
    if not hasattr(character, "rplog_buffer") or character.rplog_buffer is None:
        character.rplog_buffer = []


def start(character):
    """Begin capturing lines for this Session."""
    ensure_defaults(character)
    character.rplog_active = True
    character.rplog_buffer = []
    return "RP transcript capture started. Room say, emote, and tell lines will buffer until you 'rplog stop' or 'rplog save'."


def stop(character):
    """Stop capture without clearing the buffer."""
    ensure_defaults(character)
    character.rplog_active = False
    count = len(character.rplog_buffer or [])
    return f"RP transcript capture stopped ({count} line(s) in buffer). Use 'rplog save' to print or 'rplog clear' to discard."


def clear(character):
    """Discard the buffer."""
    ensure_defaults(character)
    character.rplog_buffer = []
    character.rplog_active = False
    return "RP transcript buffer cleared."


def capture(character, line):
    """Append one line when logging is active."""
    ensure_defaults(character)
    if not character.rplog_active:
        return
    text = (line or "").strip()
    if not text:
        return
    if len(text) > _MAX_LINE_LEN:
        text = text[: _MAX_LINE_LEN - 3] + "..."
    buf = character.rplog_buffer
    buf.append(text)
    if len(buf) > _MAX_LINES:
        del buf[: len(buf) - _MAX_LINES]


def save_block(character):
    """Return paste-friendly block or an error string."""
    ensure_defaults(character)
    buf = character.rplog_buffer or []
    if not buf:
        return "RP transcript buffer is empty."
    character.rplog_active = False
    header = f"--- RP transcript ({len(buf)} lines) ---"
    footer = "--- end transcript ---"
    return "\r\n".join([header, *buf, footer])
