"""
map_heal.py -- additive merge of live map/zone JSON from hot backups.

After auto-deploy ``git reset --hard``, ``content/zones/*.json`` and
``content/maps/*.json`` follow origin/main. Protected
``content/map_backups/<id>.json`` slots may still hold live populate /
dig rooms and player **remodel** stamps. This module merges **missing
room keys** (and missing exits whose destinations exist) back into the
live file, and restores remodel prose / ``remodel_type`` / garage inbound
exit labels on rooms that git already ships (bug report 765).

Used from ``engine.auto_deploy`` after protect-restore on silent main
advances so live-built neighborhoods survive unrelated PR merges.

Engine-pure: no ``supers`` imports. Backup directory path is the standard
``content/map_backups`` tree (same as staff ``gm maps backup``).
"""

from __future__ import annotations

import copy
import json
import os
import stat as stat_mod

from engine import world_maps as maps_mod


def _restore_path_meta(path, info):
    """Keep bind-mount owner/mode after a root-in-Docker rewrite.

    Container map heal used to leave zone JSON ``root:root`` mode 600, so
    the host user could not ``git hash`` the file and later checkouts
    fought the bind-mount.
    """
    if info is None:
        return
    mode, uid, gid = info
    try:
        os.chmod(path, mode)
    except OSError:
        pass
    if hasattr(os, "chown") and uid is not None and gid is not None:
        try:
            os.chown(path, uid, gid)
        except OSError:
            pass


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


# Fields ``map_store.apply_remodel`` writes that must survive git reset.
_LIVE_EDIT_ROOM_FIELDS = (
    "title",
    "description",
    "remodel_type",
    "remodel_inbound_exit",
    "resources",
    "vehicle_berth",
    "seed_items",
    "doors",
)

# Compass exits remodel may rename to a named label (garage, …).
_REMODEL_RELABELABLE_EXITS = frozenset({
    "north", "south", "east", "west",
    "northeast", "northwest", "southeast", "southwest",
})


def _backup_has_remodel_stamp(backup_room):
    """True when backup row carries a player/staff remodel stamp."""
    if not isinstance(backup_room, dict):
        return False
    return bool(str(backup_room.get("remodel_type") or "").strip())


def _patch_room_remodel_fields(live_room, backup_room):
    """Copy remodel prose/stamps from backup onto an existing live room."""
    if not _backup_has_remodel_stamp(backup_room):
        return False
    changed = False
    for field in _LIVE_EDIT_ROOM_FIELDS:
        if field not in backup_room:
            continue
        new_val = copy.deepcopy(backup_room[field])
        if live_room.get(field) != new_val:
            live_room[field] = new_val
            changed = True
    return changed


def _merge_remodel_inbound_exits(live_index, backup_index, live_keys):
    """Restore named inbound exits (Garage, …) after git reset."""
    patches = 0
    for target_key, backup_room in backup_index.items():
        label = str(backup_room.get("remodel_inbound_exit") or "").strip().lower()
        if not label:
            continue
        if target_key not in live_index:
            continue
        for nkey, nbackup in backup_index.items():
            nlive = live_index.get(nkey)
            if nlive is None:
                continue
            bexits = nbackup.get("exits") or {}
            if bexits.get(label) != target_key:
                continue
            live_exits = dict(nlive.get("exits") or {})
            prior = dict(live_exits)
            for direction, dest in list(live_exits.items()):
                if dest == target_key and direction != label:
                    if direction in _REMODEL_RELABELABLE_EXITS:
                        live_exits.pop(direction, None)
            live_exits[label] = target_key
            if live_exits != prior:
                nlive["exits"] = live_exits
                patches += 1
    return patches


def merge_missing_from_backup(live_doc, backup_doc):
    """Return (merged_doc, added_room_keys, patched_exit_count, skipped_vnum_keys, remodel_patches).

    Additive only: never removes or replaces an existing room entry.
    For rooms present in both, copy missing exit directions from backup
    when the destination key exists in the merged graph. When backup
    carries ``remodel_type``, also restore remodel prose/stamps and
    garage-style inbound exit labels on existing rooms.

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

    live_index = _room_index(live)

    remodel_patches = 0
    for key, live_room in live_index.items():
        backup_room = backup_index.get(key)
        if backup_room is None:
            continue
        if _patch_room_remodel_fields(live_room, backup_room):
            remodel_patches += 1

    exit_patches = _merge_remodel_inbound_exits(
        live_index, backup_index, live_keys,
    )
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

    return live, sorted(added), exit_patches, sorted(skipped_vnum), remodel_patches


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

    merged, added, exit_patches, skipped_vnum, remodel_patches = (
        merge_missing_from_backup(live_doc, backup_doc)
    )
    if not added and not exit_patches and not skipped_vnum and not remodel_patches:
        return None

    map_id = backup_doc.get("id") or live_doc.get("id") or os.path.basename(live_path)

    def _status_prefix():
        parts = [f"map heal {map_id}: +{len(added)} room(s)"]
        if exit_patches:
            parts.append(f"+{exit_patches} exit(s)")
        if remodel_patches:
            parts.append(f"+{remodel_patches} remodel(s)")
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

    if added or exit_patches or remodel_patches:
        extra_keys = maps_mod.collect_map_room_keys(exclude_path=live_path)
        exit_errors = maps_mod.document_hand_exit_graph_errors(
            os.path.basename(live_path), merged, known_keys=extra_keys,
        )
        if exit_errors:
            return (
                f"map heal {map_id}: skipped write — would break boot "
                f"({exit_errors[0]})"
            )
        prior = None
        try:
            st = os.stat(live_path)
            prior = (stat_mod.S_IMODE(st.st_mode), st.st_uid, st.st_gid)
        except OSError:
            prior = None
        with open(live_path, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=4, ensure_ascii=False)
            handle.write("\n")
        _restore_path_meta(live_path, prior)

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
