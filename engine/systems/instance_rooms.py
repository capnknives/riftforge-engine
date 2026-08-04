"""
instance_rooms.py -- generic ephemeral-instance room collapse.

Any per-player/per-party dungeon pocket (a mission stronghold, a portal
run, a procedurally-stacked floor) eventually needs to vanish: pop its
rooms out of the world, send living occupants somewhere safe, and quietly
drop anything that was only ever instance trash. That "collapse" step
turned out identical across four independent SUPERS implementations
(``supers/missions/runtime.py::_teardown_portal``,
``supers/purgatory_dungeon/floor.py::teardown``,
``supers/rowena_portal/run.py::teardown_run``, and a close cousin in
``supers/marches_expedition/rim.py::teardown_all_rim_rooms``) before this
was peeled out under docs/plans/riftforge_core_expansion.md's missions
loose end -- a real repeated mechanism, not a speculative one.

*Finding* which rooms belong to an instance stays a caller concern (each
game tags its own rooms its own way -- ``mission_id``, ``pit_run_tag``,
``rowena_portal_run_id`` -- and that tagging is content-specific). This
module only owns the shared back half: given a set of room keys, evict
them from the world and route their contents somewhere sane.

stdlib only.
"""

from __future__ import annotations


def _default_is_trash(obj):
    """An NPC with no live session and no immersion flag -- the shape
    every one of the four SUPERS teardowns started from before adding
    their own special cases (a boss tag, a hostile flag, ...)."""
    if not getattr(obj, "is_npc", False):
        return False
    if getattr(obj, "session", None) is not None:
        return False
    return not getattr(obj, "immersion", False)


def collapse_instance_rooms(
    game, room_keys, *, evac=None, is_trash=None, sink_orphan_item=None,
):
    """Pop each of ``room_keys`` out of ``game.rooms`` and route contents.

    For every occupant of a collapsing room:
      * no ``move_to`` (a static Item, not a mover) -- handed to
        ``sink_orphan_item(game, obj, from_room=room)`` when given, else
        left to vanish with the room (matches every caller's own
        "floor items sink to a vault" hook -- SUPERS-specific, so this
        stays a caller-supplied callback rather than a hard import).
      * ``is_trash(obj)`` true (default: NPC, no session, no immersion)
        -- despawned (removed from its location), never moved.
      * anything else (players, immersion-cast NPCs) -- moved to
        ``evac`` when one is given, otherwise left in the room as it's
        popped (caller's problem if that happens -- every real caller
        passes an evac room).

    Returns the list of ``Room`` objects actually removed, in
    ``room_keys`` order, skipping any key already absent from
    ``game.rooms``.
    """
    trash_check = is_trash if is_trash is not None else _default_is_trash
    rooms = getattr(game, "rooms", None)
    if not isinstance(rooms, dict):
        return []

    removed = []
    for key in list(room_keys):
        room = rooms.pop(key, None)
        if room is None:
            continue
        removed.append(room)
        for obj in list(getattr(room, "contents", None) or []):
            if not hasattr(obj, "move_to"):
                if sink_orphan_item is not None:
                    sink_orphan_item(game, obj, from_room=room)
                continue
            if trash_check(obj):
                if getattr(obj, "location", None) is not None:
                    obj.location.remove(obj)
                continue
            if evac is not None:
                obj.move_to(evac)
    return removed
