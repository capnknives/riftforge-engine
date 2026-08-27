"""
maps_audit.py -- offline hygiene checks for loaded map/zone rooms.

Generic engine helper for builder platform audit (Wave 10). Assumes
``load_all_maps()`` already succeeded; reports soft WARN lines staff
can triage without failing boot.
"""

from __future__ import annotations

import json
import os

# Cardinal + diagonal + vertical pairs for one-way exit warnings.
_EXIT_OPPOSITE = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
    "northeast": "southwest",
    "southwest": "northeast",
    "northwest": "southeast",
    "southeast": "northwest",
    "up": "down",
    "down": "up",
    "in": "out",
    "out": "in",
}


def _room_label(room):
    """Stable display key for audit lines."""
    return getattr(room, "key", None) or "?"


def builder_audit_lines(*, rooms, start_room=None):
    """Return sorted WARN lines for a loaded room graph."""
    lines = []
    if start_room is None:
        lines.append("WARN no is_start room resolved at load")

    for key, room in sorted(rooms.items(), key=lambda item: str(item[0])):
        exits = getattr(room, "exits", None) or {}
        for direction, dest in exits.items():
            reverse = _EXIT_OPPOSITE.get(direction)
            if not reverse:
                continue
            dest_exits = getattr(dest, "exits", None) or {}
            back = dest_exits.get(reverse)
            if back is not room:
                dest_key = _room_label(dest)
                lines.append(
                    f"WARN {key!r} {direction}->{dest_key!r} lacks reverse "
                    f"{reverse}"
                )

    return lines


def zone_file_audit_lines(content_root):
    """Soft checks on raw zone JSON (runtime hub metadata)."""
    lines = []
    zones_dir = os.path.join(content_root, "zones")
    if not os.path.isdir(zones_dir):
        return lines
    for name in sorted(os.listdir(zones_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(zones_dir, name)
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("autoload") is False:
            continue
        rooms = data.get("rooms") or []
        if not rooms:
            continue
        if not data.get("runtime_hub") and not data.get("entry_room"):
            lines.append(
                f"WARN {name}: zone has {len(rooms)} room(s) but no "
                "runtime_hub or entry_room"
            )
    return lines


def builder_audit(*, rooms, content_root, start_room=None):
    """Merge loaded-room and on-disk zone metadata audit lines."""
    lines = list(builder_audit_lines(rooms=rooms, start_room=start_room))
    lines.extend(zone_file_audit_lines(content_root))
    return lines
