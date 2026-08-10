"""
content_room_scan.py -- scan room fields from content/maps + content/zones JSON.

Generic disk scan for offline builder audits (no live Game required).
"""

from __future__ import annotations

import json
import os


def iter_room_snapshots(content_root, *, fields=("jobs", "resources")):
    """Yield per-room dicts with ``key`` plus requested list fields.

    ``content_root`` is the repo ``content/`` directory (maps + zones).
    """
    wanted = tuple(fields)
    for sub in ("zones", "maps"):
        folder = os.path.join(content_root, sub)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            for room in data.get("rooms") or []:
                snap = {"key": room.get("key") or "?"}
                for field in wanted:
                    snap[field] = list(room.get(field) or [])
                yield snap


def iter_seed_item_refs(content_root):
    """Yield (map_file, room_key, item_id) for catalog refs in seed_items."""
    for sub in ("zones", "maps"):
        folder = os.path.join(content_root, sub)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            for room in data.get("rooms") or []:
                room_key = room.get("key") or "?"
                for ref in room.get("seed_items") or []:
                    if isinstance(ref, dict):
                        item_id = ref.get("item")
                        if item_id:
                            yield name, room_key, str(item_id)


def _yield_spawn_nest_from_mapping(map_name, label, mapping):
    """Yield one spawn_nest ref from a room dict or grid cell override."""
    if not isinstance(mapping, dict):
        return
    nest = mapping.get("spawn_nest")
    if nest:
        key = str(nest).strip().lower()
        if key:
            yield map_name, label, key
        return
    if mapping.get("vampire_nest"):
        yield map_name, label, "vampire"


def iter_spawn_nest_refs(content_root):
    """Yield (map_file, location_label, nest_type) for spawn_nest stamps."""
    for sub in ("zones", "maps"):
        folder = os.path.join(content_root, sub)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            grid = data.get("grid") or {}
            if isinstance(grid, dict):
                for coord, cell in (grid.get("cell_overrides") or {}).items():
                    yield from _yield_spawn_nest_from_mapping(
                        name, f"grid:{coord}", cell,
                    )
            for room in data.get("rooms") or []:
                room_key = room.get("key") or "?"
                yield from _yield_spawn_nest_from_mapping(
                    name, room_key, room,
                )
