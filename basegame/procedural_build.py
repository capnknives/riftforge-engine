"""procedural_build.py -- basegame hooks for engine procedural builders."""

from __future__ import annotations

from engine import hooks
from engine.room_naming import structured_title

_MAP_CITY = {
    "notbigville": "Notbigville",
    "rift_nexus": "Rift Nexus",
}


def register_populate_hooks():
    """Wire generic title builder + Notbigville city labels."""
    hooks.set_populate_room_namer(structured_title)
    hooks.set_populate_city_for_map_id(
        lambda map_id: _MAP_CITY.get(str(map_id or "").strip(), "Notbigville"),
    )

    def _city_label(room):
        stamped = str(getattr(room, "city_name", None) or "").strip()
        if stamped:
            return stamped
        return hooks.populate_city_for_map_id(getattr(room, "map_id", None) or "")

    hooks.set_populate_city_label(_city_label)
