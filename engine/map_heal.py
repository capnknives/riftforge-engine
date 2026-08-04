"""
map_heal.py -- additive merge of live map/zone JSON from hot backups.

After auto-deploy ``git reset --hard``, ``content/zones/*.json`` and
``content/maps/*.json`` follow origin/main. Protected
``content/map_backups/<id>.json`` slots may still hold live populate /
dig rooms. This module merges **missing room keys** (and missing exits
whose destinations exist) back into the live file without overwriting
rooms that git already ships.

Used from ``engine.auto_deploy`` after protect-restore on silent main
advances so live-built neighborhoods survive unrelated PR merges.

Engine-pure: no ``supers`` imports. Backup directory path is the standard
``content/map_backups`` tree (same as staff ``gm maps backup``).
"""

from __future__ import annotations

import copy
import json
import os

import maps as maps_mod


def _backups_dir(root=None):
    """Absolute path to ``content/map_backups`` under the repo root."""
    if root is None:
        root = os.getcwd()
    return os.path.join(os.path.abspath(root), "content", "map_backups")


def _room_keys(doc):
    """Return the set of room keys in a map/zone document."""
    return {
        room.get("key")
        for room in (doc.get("rooms") or [])
        if room.get("key")
    }


def _room_index(doc):
    """Map room key -> rooms[] dict (mutates doc if needed)."""
    index = {}
    for room in doc.get("rooms") or []:
        key = room.get("key")
        if key:
            index[key] = room
    return index


def _vnum_for_room(room):
    """Stable vnum identity for heal collision checks (matches maps loader)."""
    return room.get("vnum") or room.get("key")


def _vnum_index(doc):
    """Map vnum -> room key for every room in a map/zone document."""
    index = {}
    for room in doc.get("rooms") or []:
        key = room.get("key")
        if not key:
            continue
        index[_vnum_for_room(room)] = key
    return index


def merge_missing_from_backup(live_doc, backup_doc):
    """Return (merged_doc, added_room_keys, patched_exit_count, skipped_vnum_keys).

    Additive only: never removes or replaces an existing room entry.
    For rooms present in both, copy missing exit directions from backup
    when the destination key exists in the merged graph.

    Backup rooms whose ``vnum`` (or key when vnum is absent) is already
    used by a different live room are **skipped** so auto-deploy heal
    cannot recreate boot-time duplicate vnum crashes.
    """
    live = copy.deepcopy(live_doc)
    backup = backup_doc if isinstance(backup_doc, dict) else {}
    live_keys = _room_keys(live)
    live_vnums = _vnum_index(live)
    backup_index = _room_index(backup)

    added = []
    skipped_vnum = []
    for key, room in backup_index.items():
        if key in live_keys:
            continue
        vnum = _vnum_for_room(room)
        owner = live_vnums.get(vnum)
        if owner is not None and owner != key:
            skipped_vnum.append(key)
            continue
        live.setdefault("rooms", []).append(copy.deepcopy(room))
        live_keys.add(key)
        live_vnums[vnum] = key
        added.append(key)

    exit_patches = 0
    live_index = _room_index(live)
    for key, live_room in live_index.items():
        backup_room = backup_index.get(key)
        if backup_room is None:
            continue
        live_exits = dict(live_room.get("exits") or {})
        changed = False
        for direction, dest in (backup_room.get("exits") or {}).items():
            if direction in live_exits:
                continue
            if dest not in live_keys:
                continue
            live_exits[direction] = dest
            changed = True
            exit_patches += 1
        if changed:
            live_room["exits"] = live_exits

    return live, sorted(added), exit_patches, sorted(skipped_vnum)


def heal_file_from_backup(live_path, backup_path, *, dry_run=False):
    """Merge backup into live_path when backup has extra rooms.

    Returns a short status string, or None when nothing to do.
    Raises on I/O / JSON errors.
    """
    if not os.path.isfile(backup_path):
        return None
    if not os.path.isfile(live_path):
        return None

    with open(live_path, encoding="utf-8") as handle:
        live_doc = json.load(handle)
    with open(backup_path, encoding="utf-8") as handle:
        backup_doc = json.load(handle)

    merged, added, exit_patches, skipped_vnum = merge_missing_from_backup(
        live_doc, backup_doc,
    )
    if not added and not exit_patches and not skipped_vnum:
        return None

    map_id = backup_doc.get("id") or live_doc.get("id") or os.path.basename(live_path)

    def _status_prefix():
        parts = [f"map heal {map_id}: +{len(added)} room(s)"]
        if exit_patches:
            parts.append(f"+{exit_patches} exit(s)")
        if skipped_vnum:
            sample = ", ".join(skipped_vnum[:3])
            if len(skipped_vnum) > 3:
                sample += ", …"
            parts.append(
                f"skipped {len(skipped_vnum)} vnum collision(s) ({sample})",
            )
        if added:
            sample = ", ".join(added[:3])
            if len(added) > 3:
                sample += ", …"
            parts.append(f"({sample})")
        return " ".join(parts)

    if dry_run:
        msg = _status_prefix().replace("map heal", "would heal", 1)
        return msg

    if added or exit_patches:
        with open(live_path, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=4, ensure_ascii=False)
            handle.write("\n")

    return _status_prefix()


def heal_all_from_hot_backups(root=None, *, dry_run=False):
    """Walk ``content/map_backups/*.json`` and heal matching live files.

    Returns a list of log lines (empty when nothing healed).
    """
    if root is None:
        root = os.getcwd()
    bak_dir = _backups_dir(root)
    if not os.path.isdir(bak_dir):
        return []

    # map_id -> live absolute path
    live_by_id = {}
    for path in maps_mod.iter_map_json_paths():
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            map_id = maps_mod._map_id_for(os.path.basename(path), data)
            live_by_id[map_id] = path
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue

    lines = []
    for name in sorted(os.listdir(bak_dir)):
        if not name.endswith(".json"):
            continue
        map_id = name[:-5]
        live_path = live_by_id.get(map_id)
        if not live_path:
            continue
        backup_path = os.path.join(bak_dir, name)
        msg = heal_file_from_backup(live_path, backup_path, dry_run=dry_run)
        if msg:
            lines.append(msg)
    return lines
