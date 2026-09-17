"""rumor_board.py -- anonymous room-tagged public posts (engine kernel).

Rooms tagged ``rumor_board`` in ``resources`` hold a capped scrap list on
``game.rumor_boards[room.key]``. Persistence is the engine meta table
(``load_rumor_boards`` / ``save_rumor_boards``). Games own verbs and GM
wipe wrappers.

Distinct from ``noticeboard.py`` (in-room ``notices`` list, no SQLite).
stdlib only. No game imports.
"""

from __future__ import annotations

from engine.systems.player_text import PLAYER_TEXT_WALL_MAX

BOARD_CAP = 10
POST_TEXT_MAX = PLAYER_TEXT_WALL_MAX
RESOURCE_TAG = "rumor_board"


def ensure_boards(game):
    """Make sure ``game.rumor_boards`` exists."""
    if not hasattr(game, "rumor_boards") or game.rumor_boards is None:
        game.rumor_boards = {}
    return game.rumor_boards


def is_board_room(room):
    """True if this room has a player rumor board."""
    if room is None:
        return False
    return RESOURCE_TAG in (getattr(room, "resources", None) or [])


def _board_ident(room):
    """Mapper id for a rumor board room, else ``room.key``."""
    from engine.room_vnum import internal_room_key

    if room is None:
        return None
    return internal_room_key(room) or getattr(room, "key", None)


def _posts_bucket(boards, room):
    """Posts for ``room``, including leftover dig-name keys."""
    ident = _board_ident(room)
    if ident and ident in boards:
        return ident, boards[ident]
    leftover = getattr(room, "legacy_key", None) if room is not None else None
    if leftover and leftover in boards:
        return leftover, boards[leftover]
    raw = getattr(room, "key", None) if room is not None else None
    if raw and raw in boards:
        return raw, boards[raw]
    return ident, []


def posts_for(game, room):
    """Return the post list for this room (may be empty)."""
    boards = ensure_boards(game)
    if room is None:
        return []
    _ident, posts = _posts_bucket(boards, room)
    return posts


def format_board(game, room):
    """Lines listing posts on this room's board."""
    if not is_board_room(room):
        return ["There is no rumor board here."]
    posts = posts_for(game, room)
    if not posts:
        return [
            "The rumor board is blank. "
            "Type 'rumor post <text>' to leave a scrap."
        ]
    lines = [f"Rumor board ({len(posts)}/{BOARD_CAP}):"]
    for i, post in enumerate(posts, start=1):
        lines.append(f"  {i}. {post.get('text', '')}")
    return lines


def post_rumor(character, text, game):
    """Append an anonymous post. Returns (ok, message)."""
    room = getattr(character, "location", None)
    if not is_board_room(room):
        return False, "There is no rumor board here."
    body = (text or "").strip()
    if not body:
        return False, "Usage: rumor post <text>"
    if len(body) > POST_TEXT_MAX:
        return False, f"Rumors are limited to {POST_TEXT_MAX} characters."
    boards = ensure_boards(game)
    ident, posts = _posts_bucket(boards, room)
    posts = list(posts)
    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    posts.append({"text": body, "tick": ticks})
    while len(posts) > BOARD_CAP:
        posts.pop(0)
    if ident:
        boards[ident] = posts
        leftover = getattr(room, "legacy_key", None)
        if leftover and leftover != ident:
            boards.pop(leftover, None)
        raw = getattr(room, "key", None)
        if raw and raw != ident:
            boards.pop(raw, None)
    return True, "You pin a scrap to the board."


def clear_rumors(game, room=None):
    """GM wipe: one room, or every board. Returns a status string."""
    boards = ensure_boards(game)
    if room is None:
        n = sum(len(v) for v in boards.values())
        boards.clear()
        return f"Cleared all rumor boards ({n} post(s))."
    ident = _board_ident(room)
    leftover = getattr(room, "legacy_key", None)
    n = 0
    if ident:
        n += len(boards.get(ident, []))
        boards[ident] = []
    if leftover and leftover != ident:
        n += len(boards.get(leftover, []))
        boards.pop(leftover, None)
    raw = getattr(room, "key", None)
    if raw and raw != ident:
        n += len(boards.get(raw, []))
        boards.pop(raw, None)
    return f"Cleared the board in {ident or raw} ({n} post(s))."
