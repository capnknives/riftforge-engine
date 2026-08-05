"""dig.py -- runtime room carving via engine.map_store (H6 demo)."""

from engine.command_support import DIRECTIONS
from engine import map_store


def _normalize_direction(token):
    """Canonical exit direction from a dig token, or None."""
    raw = (token or "").strip().lower()
    if not raw:
        return None
    if raw in ("in", "out", "leave"):
        return raw
    return DIRECTIONS.get(raw)


def cmd_dig(character, args, game):
    """Carve a new room off the current one (persists zone JSON)."""
    here = getattr(character, "location", None)
    if here is None:
        character.session.send("You are nowhere.")
        return
    parts = (args or "").strip().split(None, 1)
    if len(parts) < 2:
        character.session.send("Usage: dig <direction> <ROOM NAME…>")
        return
    direction = _normalize_direction(parts[0])
    if direction is None:
        character.session.send(
            f"Unknown direction {parts[0]!r}. Try n/s/e/w/up/down/in/out."
        )
        return
    new_key = parts[1].strip()
    try:
        msg = map_store.dig_room(game, here, direction, new_key)
    except ValueError as err:
        character.session.send(str(err))
        return
    character.session.send(msg)
