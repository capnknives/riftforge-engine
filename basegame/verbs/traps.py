"""traps.py -- basegame devil's trap chalk hub (engine occult_marks kernel)."""

from __future__ import annotations

from command_support import broadcast_here
from engine.command_support import _display_name
from engine.systems import occult_marks as occult_marks_mod

TRAP_REAGENT_IDS = ("ritual_chalk", "devils_trap_paint")


def _pick_trap_reagent(character):
    """Return the first trap chalk consumable in inventory, if any."""
    from world import Item

    if character is None:
        return None
    for obj in getattr(character, "inventory", None) or []:
        if not isinstance(obj, Item):
            continue
        cat = getattr(obj, "catalog_id", None)
        if cat in TRAP_REAGENT_IDS:
            return cat
    return None


def _consume_trap_reagent(character, catalog_id):
    """Remove one trap chalk item from inventory."""
    from world import Item

    inv = getattr(character, "inventory", None) or []
    for idx, obj in enumerate(inv):
        if (
            isinstance(obj, Item)
            and getattr(obj, "catalog_id", None) == catalog_id
        ):
            inv.pop(idx)
            return True
    return False


def _refresh_zone_hud(room):
    """Push Occupants / Zone HUD after a trap mark changes."""
    if room is None:
        return
    from engine import gmcp as gmcp_mod

    gmcp_mod.push_occupants_for_room(room)


def _trap_status_lines(room, game):
    """Short readout for bare ``traps``."""
    ticks = int(getattr(game, "game_time_ticks", 0) or 0) if game else 0
    if occult_marks_mod.room_is_devils_trap(room, ticks):
        return [
            "This room has a devil's trap chalked across the floor.",
            "Type traps clear to wipe it, or traps paint to refresh it.",
        ]
    return [
        "No devil's trap is chalked here.",
        "Buy ritual chalk at the General Store, then traps paint.",
    ]


def _paint_trap(character, game, room):
    """Consume chalk and stamp a permanent devil's trap."""
    if getattr(room, "devils_trap", False):
        character.session.send("This room already has a permanent devil's trap.")
        return
    reagent = _pick_trap_reagent(character)
    if not reagent:
        character.session.send(
            "You need ritual chalk or devil's trap paint in your inventory. "
            "Buy chalk at the General Store (see 'help traps')."
        )
        return
    if not _consume_trap_reagent(character, reagent):
        character.session.send("You fumble for chalk and find nothing.")
        return
    occult_marks_mod.stamp_devils_trap(room)
    character.session.send("You chalk a devil's trap across the floor.")
    label = _display_name(character, viewer=None)
    broadcast_here(
        character,
        f"{label} chalks a devil's trap across the floor.",
        exclude=character,
    )
    _refresh_zone_hud(room)


def _clear_trap(character, game, room):
    """Wipe the permanent authored devil's trap flag."""
    ticks = int(getattr(game, "game_time_ticks", 0) or 0) if game else 0
    if not getattr(room, "devils_trap", False):
        if occult_marks_mod.room_is_devils_trap(room, ticks):
            character.session.send(
                "Only a permanent chalked trap can be cleared here -- "
                "live circles fade on their own."
            )
        else:
            character.session.send("There is no devil's trap to clear here.")
        return
    occult_marks_mod.clear_devils_trap(room)
    character.session.send("You scrub the devil's trap chalk from the floor.")
    label = _display_name(character, viewer=None)
    broadcast_here(
        character,
        f"{label} scrubs a devil's trap from the floor.",
        exclude=character,
    )
    _refresh_zone_hud(room)


def cmd_traps(character, args, game):
    """Chalk or clear a devil's trap in this room (help traps)."""
    room = character.location
    if room is None:
        character.session.send("You aren't anywhere.")
        return
    parts = (args or "").strip().split()
    sub = parts[0].lower() if parts else ""
    if sub in ("paint", "here", "chalk", "trap"):
        _paint_trap(character, game, room)
        return
    if sub in ("clear", "wipe", "erase", "scrub"):
        _clear_trap(character, game, room)
        return
    if sub in ("status", "read", "?"):
        lines = _trap_status_lines(room, game)
        character.session.send("\r\n".join(lines))
        return
    if sub:
        character.session.send(
            "Try traps paint, traps here, traps clear, or bare traps for "
            "status (see 'help traps')."
        )
        return
    lines = _trap_status_lines(room, game)
    character.session.send("\r\n".join(lines))
