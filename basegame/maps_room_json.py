"""
maps_room_json.py -- basegame half of map-JSON room stamping.

``maps._add_room`` passes the raw hand-room dict through
``engine.hooks.stamp_map_room``; basegame registers this stamper from
``bootstrap.register_core_hooks`` so ``rift_gate`` and per-room elemental
``plane`` overrides load from zone JSON.
"""

from __future__ import annotations

from maps import PLANES, REALM_FOR_PLANE


def stamp_basegame_map_room(room, data, *, filename=None):
    """Apply basegame-authored room flags from map / zone JSON."""
    rift_gate = data.get("rift_gate")
    if rift_gate is not None:
        room.rift_gate = bool(rift_gate)

    # Per-room plane override (hub stays earth; mouths tag fire/water/air/stone).
    plane = data.get("plane")
    if plane is not None:
        if plane not in PLANES:
            where = f"{filename}: " if filename else ""
            raise ValueError(
                f"{where}room {room.key!r}: unknown plane {plane!r} -- "
                f"must be one of {sorted(PLANES)}"
            )
        room.plane = plane
        room.realm = REALM_FOR_PLANE.get(plane, "prime")

    spawn_nest = data.get("spawn_nest")
    if spawn_nest is not None and str(spawn_nest).strip():
        room.spawn_nest = str(spawn_nest).strip().lower()
