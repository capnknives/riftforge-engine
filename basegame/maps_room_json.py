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

    hospital = data.get("hospital")
    if hospital is not None:
        room.hospital = bool(hospital)

    is_cell = data.get("is_cell")
    if is_cell is not None:
        room.is_cell = bool(is_cell)

    shop_stock = data.get("shop_stock")
    if shop_stock is not None:
        if not isinstance(shop_stock, list):
            where = f"{filename}: " if filename else ""
            raise ValueError(
                f"{where}room {room.key!r}: shop_stock must be a list"
            )
        room.shop_stock = [dict(entry) for entry in shop_stock]

    is_hotel_room = data.get("is_hotel_room")
    if is_hotel_room is not None:
        room.is_hotel_room = bool(is_hotel_room)

    resources = data.get("resources")
    if resources is not None:
        room.resources = list(resources)

    # Twitch / active combat arena flag -- Fight.combat_mode locks to
    # "active" when engagement starts here (docs/plans/fast_paced_combat_engine.md).
    active_combat = data.get("active_combat")
    if active_combat is not None:
        room.active_combat = bool(active_combat)
