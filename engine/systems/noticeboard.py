"""noticeboard.py -- generic room-attached community notice list.

A room tagged with ``"noticeboard"`` in ``resources`` (or any room with a
``notices`` list) can hold a capped stack of short public posts:
``{author, text, posted_tick}``. Games wire player verbs through this module;
nothing here touches networking.

SUPERS may add its own policy layer later; basegame proves the kit via
``basegame/verbs/noticeboard.py`` and a tagged room in Notbigville.

stdlib only.
"""

from __future__ import annotations

from engine.systems.player_text import PLAYER_TEXT_WALL_MAX

# Soft cap so one room cannot grow an unbounded scroll.
NOTICE_CAP = 20
NOTICE_TEXT_MAX = PLAYER_TEXT_WALL_MAX


def is_noticeboard_room(room):
    """True when this room accepts public posts."""
    if room is None:
        return False
    resources = getattr(room, "resources", None) or []
    if "noticeboard" in resources:
        return True
    # Also honor an explicit notices list (tests / hand-built rooms).
    notices = getattr(room, "notices", None)
    return isinstance(notices, list)


def notices(room):
    """Return the room's ``notices`` list (creates empty if missing)."""
    if room is None:
        return []
    board = getattr(room, "notices", None)
    if board is None:
        room.notices = []
        return room.notices
    return board


def post(room, author, text, *, now_tick=0):
    """Append one notice. Returns ``(ok, message)``."""
    if not is_noticeboard_room(room):
        return False, "There is no noticeboard here."
    body = (text or "").strip()
    if not body:
        return False, "Usage: notice <text>"
    if len(body) > NOTICE_TEXT_MAX:
        return False, f"Notices are limited to {NOTICE_TEXT_MAX} characters."
    author_name = getattr(author, "key", None) or str(author or "?")
    board = notices(room)
    board.append({
        "author": author_name,
        "text": body,
        "posted_tick": int(now_tick or 0),
    })
    while len(board) > NOTICE_CAP:
        board.pop(0)
    return True, "You tack your notice to the board."


def read(room):
    """Return display lines for every notice (newest last)."""
    board = notices(room)
    if not board:
        return ["The noticeboard is bare."]
    lines = [f"Noticeboard ({len(board)}/{NOTICE_CAP}):"]
    for index, entry in enumerate(board, start=1):
        preview = entry.get("text", "")
        if len(preview) > 60:
            preview = preview[:57] + "..."
        lines.append(
            f"  {index}. {entry.get('author', '?')}: {preview}"
        )
    return lines


def read_one(room, index):
    """Return ``(ok, message)`` for a 1-based notice index."""
    board = notices(room)
    try:
        n = int(index)
    except (TypeError, ValueError):
        return False, "Usage: notice read <number>"
    if n < 1 or n > len(board):
        return False, "No notice with that number."
    entry = board[n - 1]
    return True, (
        f"Posted by {entry.get('author', '?')}:\r\n"
        f"{entry.get('text', '')}"
    )


def remove(room, index):
    """Remove one notice by 1-based index. Returns ``(ok, message)``."""
    board = notices(room)
    try:
        n = int(index)
    except (TypeError, ValueError):
        return False, "Usage: notice remove <number>"
    if n < 1 or n > len(board):
        return False, "No notice with that number."
    board.pop(n - 1)
    return True, "You pull the notice down."
