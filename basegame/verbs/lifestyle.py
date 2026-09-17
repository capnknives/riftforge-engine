"""lifestyle.py -- basegame fishing verb (engine kernel)."""

from __future__ import annotations

from command_support import broadcast_here
from engine.systems import fishing as fishing_engine
from engine.systems import utility_delay as delay_mod


def _status_lines(character, game, room):
    """Short readout for ``fish status`` without SUPERS week/spooky prose."""
    body_id = fishing_engine.water_body_for_room(room)
    if not body_id:
        return [
            "No fishable water here. Try a pier or lake shore (help fishing)."
        ]
    from engine import hooks

    tables = hooks.fishing_tables()
    body = (tables.get("bodies") or {}).get(body_id) or {}
    return [
        f"Water: {body.get('label', body_id)}.",
        "Type fish to cast, or fish worms when you carry bait.",
    ]


def _find_bait_in_inventory(character, bait_id):
    """Return True when a catalog bait id is in inventory."""
    from world import Item

    for obj in getattr(character, "inventory", None) or []:
        if isinstance(obj, Item) and getattr(obj, "catalog_id", None) == bait_id:
            return True
    return False


def _consume_bait(character, bait_id):
    """Remove one bait stack from inventory after a successful cast."""
    from world import Item

    inv = getattr(character, "inventory", None) or []
    for idx, obj in enumerate(inv):
        if isinstance(obj, Item) and getattr(obj, "catalog_id", None) == bait_id:
            inv.pop(idx)
            return True
    return False


def cmd_fish(character, args, game):
    """Cast at fishable water; fish status / fish worms / fish fly."""
    parts = (args or "").strip().split()
    if parts and parts[0].lower() in ("status", "read"):
        lines = _status_lines(character, game, character.location)
        character.session.send("\r\n".join(lines))
        return
    bait_id = None
    if parts:
        token = parts[0].lower()
        if token in ("worms", "worm"):
            bait_id = "fishing_worms"
        elif token in ("fly", "flies"):
            bait_id = "fishing_fly"
        if bait_id and not _find_bait_in_inventory(character, bait_id):
            character.session.send("You are not carrying that bait.")
            return
    busy = delay_mod.begin_attempt(character, game, "fish")
    if busy:
        character.session.send(busy)
        return
    ok, msg, room_line, _catch = fishing_engine.try_fish(
        character, game, bait=bait_id,
    )
    character.session.send(msg)
    if ok:
        if bait_id:
            _consume_bait(character, bait_id)
        if room_line and character.location:
            broadcast_here(character, room_line, exclude=character)
