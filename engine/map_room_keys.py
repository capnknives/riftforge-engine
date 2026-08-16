"""
map_room_keys.py -- global hand-room key uniqueness across map/zone JSON.

``game.rooms`` is one flat dict: a storage key like ``MN00009`` must refer to
exactly one town. Townforge and live builds must not ship zones that reuse
keys already claimed by Lebanon, Lawrence, or any other loaded map.
"""

from __future__ import annotations

import json
import os

from engine.world_maps import iter_map_json_paths


def _zone_label(data, rel_path: str) -> str:
    """Short provenance string for error lines."""
    zone = data.get("zone_id") or data.get("zone") or data.get("id") or ""
    city = data.get("city_name") or ""
    bits = [rel_path]
    if zone:
        bits.append(f"zone={zone}")
    if city:
        bits.append(city)
    return " ".join(bits)


def iter_hand_room_keys(path, data=None):
    """Yield ``(room_key, zone, map_id)`` for hand rooms in one map file."""
    if data is None:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    map_id = data.get("id") or os.path.splitext(os.path.basename(path))[0]
    for row in data.get("rooms") or []:
        key = row.get("key")
        if not key:
            continue
        yield str(key), row.get("zone") or data.get("zone_id") or "", map_id


def build_global_room_key_index(*, root=None, exclude_paths=()):
    """Map storage key -> first file that claimed it."""
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    exclude = {os.path.normpath(p) for p in exclude_paths}
    index: dict[str, dict] = {}
    for path in iter_map_json_paths():
        norm = os.path.normpath(path)
        if norm in exclude:
            continue
        rel = os.path.relpath(path, root).replace("\\", "/")
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        label = _zone_label(data, rel)
        for key, zone, map_id in iter_hand_room_keys(path, data):
            if key not in index:
                index[key] = {
                    "path": rel,
                    "label": label,
                    "zone": zone or "",
                    "map_id": map_id or "",
                }
    return index


def find_room_key_collisions_for_doc(
    zone_doc,
    zone_path,
    *,
    root=None,
    catalog=None,
):
    """Return collision rows for keys in ``zone_doc`` vs other map files."""
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    zone_norm = os.path.normpath(zone_path)
    if catalog is None:
        catalog = build_global_room_key_index(
            root=root, exclude_paths=[zone_norm],
        )
    rel_self = os.path.relpath(zone_norm, root).replace("\\", "/")
    collisions = []
    for key, zone, map_id in iter_hand_room_keys(zone_norm, zone_doc):
        hit = catalog.get(key)
        if hit is None:
            continue
        collisions.append({
            "key": key,
            "other_path": hit["path"],
            "other_label": hit["label"],
            "other_zone": hit.get("zone") or "",
            "other_map_id": hit.get("map_id") or "",
            "self_path": rel_self,
            "self_zone": zone or zone_doc.get("zone_id") or "",
            "self_map_id": map_id or zone_doc.get("id") or "",
        })
    return collisions


def scan_global_room_key_collisions(*, root=None):
    """Return every duplicate hand-room key across all map JSON files."""
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seen: dict[str, dict] = {}
    dupes = []
    for path in iter_map_json_paths():
        rel = os.path.relpath(path, root).replace("\\", "/")
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            dupes.append({
                "key": "",
                "error": f"{rel}: cannot read ({exc})",
            })
            continue
        label = _zone_label(data, rel)
        for key, zone, map_id in iter_hand_room_keys(path, data):
            if key in seen:
                dupes.append({
                    "key": key,
                    "first_path": seen[key]["path"],
                    "first_label": seen[key]["label"],
                    "second_path": rel,
                    "second_label": label,
                    "first_zone": seen[key].get("zone") or "",
                    "second_zone": zone or "",
                    "first_map_id": seen[key].get("map_id") or "",
                    "second_map_id": map_id or "",
                })
            else:
                seen[key] = {
                    "path": rel,
                    "label": label,
                    "zone": zone or "",
                    "map_id": map_id or "",
                }
    return dupes


def format_collision_failures(collisions, *, prefix="FAIL"):
    """Turn collision dicts into validate_* style lines."""
    lines = []
    for row in collisions:
        if row.get("error"):
            lines.append(f"{prefix} {row['error']}")
            continue
        key = row.get("key") or "?"
        if "other_path" in row:
            lines.append(
                f"{prefix} room key {key!r} in {row.get('self_path')} "
                f"(zone={row.get('self_zone')!r} map={row.get('self_map_id')!r}) "
                f"collides with {row.get('other_path')} "
                f"({row.get('other_label')})"
            )
        else:
            lines.append(
                f"{prefix} room key {key!r} duplicated: "
                f"{row.get('first_path')} ({row.get('first_label')}) vs "
                f"{row.get('second_path')} ({row.get('second_label')})"
            )
    return lines
