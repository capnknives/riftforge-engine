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


def _identity_tokens(room):
    """All stable tokens that identify one rooms[] row (key / vnum / legacy)."""
    if not isinstance(room, dict):
        return set()
    tokens = set()
    for field in ("key", "legacy_key"):
        text = str(room.get(field) or "").strip()
        if text:
            tokens.add(text)
    vnum = str(room.get("vnum") or "").strip().upper()
    if vnum:
        tokens.add(vnum)
    return tokens


def _build_identity_map(room_index):
    """Map any identity token -> canonical rooms[] key in ``room_index``."""
    identity = {}
    for key, room in room_index.items():
        for token in _identity_tokens(room):
            identity.setdefault(token, key)
    return identity


def _backup_remodel_index(backup_index):
    """Map identity token -> backup row when that row carries a remodel stamp."""
    by_identity = {}
    for room in backup_index.values():
        if not _backup_has_remodel_stamp(room):
            continue
        for token in _identity_tokens(room):
            by_identity.setdefault(token, room)
    return by_identity


def _live_key_for_backup_ref(
    backup_key,
    backup_room,
    live_index,
    live_identity,
    *,
    skipped_backup_keys=None,
):
    """Resolve a backup room reference to the live rooms[] key.

    Phase 3 keeps VNUMs on ``Room.key`` while zone JSON may still list
    legacy dig keys until the next save. Hot backups can therefore carry
    either shape after ``dig_room`` / ``apply_remodel`` (bug reports 1051,
    1073).

  When a backup row was skipped due to a true vnum collision, never remap
    its exits onto the unrelated live owner of that vnum.
    """
    skipped_backup_keys = skipped_backup_keys or set()
    if backup_key in skipped_backup_keys:
        return None
    if backup_key in live_index:
        return backup_key
    for token in _identity_tokens(backup_room):
        live_key = live_identity.get(token)
        if live_key and live_key in live_index:
            return live_key
    return None


def _backup_by_vnum(backup_index):
    """Map vnum -> backup rooms[] row (first wins)."""
    by_vnum = {}
    for room in backup_index.values():
        by_vnum.setdefault(_vnum_for_room(room), room)
    return by_vnum


def _find_backup_row_for_live(live_room, backup_index, backup_identity):
    """Return the backup rooms[] row for a live row, matching any identity."""
    key = live_room.get("key")
    if key and key in backup_index:
        return backup_index[key]
    for token in _identity_tokens(live_room):
        backup_key = backup_identity.get(token)
        if backup_key and backup_key in backup_index:
            return backup_index[backup_key]
    return None


def _find_backup_room_for_live(live_room, backup_index, backup_remodel_index):
    """Return the backup row that should re-stamp this live room."""
    key = live_room.get("key")
    if key and key in backup_index:
        backup_room = backup_index[key]
        if _backup_has_remodel_stamp(backup_room):
            return backup_room
    for token in _identity_tokens(live_room):
        backup_room = backup_remodel_index.get(token)
        if backup_room is not None:
            return backup_room
    return None


def _resolve_backup_exit_dest(
    dest,
    backup_index,
    live_keys,
    live_identity,
    *,
    skipped_backup_keys=None,
):
    """Map a backup exit destination key onto a live graph key when safe."""
    if dest in live_keys:
        return dest
    canonical = live_identity.get(dest)
    if canonical and canonical in live_keys:
        return canonical
    backup_dest = backup_index.get(dest)
    if backup_dest is None:
        return None
    return _live_key_for_backup_ref(
        dest,
        backup_dest,
        {key: {} for key in live_keys},
        live_identity,
        skipped_backup_keys=skipped_backup_keys,
    )


def _normalize_merged_exit_dests(doc):
    """Rewrite exit targets to canonical rooms[] keys after a backup merge.

    Hot backups written by ``apply_remodel`` often store Phase-3 VNUMs in
    ``exits`` (``LG00103``) while git zone JSON still lists legacy dig keys
    (``Hickory Parkway 12103 Living``). ``document_hand_exit_graph_errors``
    rejects the merge unless every destination resolves to a live graph key
    (bug report 1094: garage bays restored in backup but heal skipped).
    """
    live_index = _room_index(doc)
    live_keys = set(live_index.keys())
    live_identity = _build_identity_map(live_index)
    patches = 0
    for room in live_index.values():
        exits = dict(room.get("exits") or {})
        changed = False
        normalized = {}
        for direction, dest in exits.items():
            if not dest:
                continue
            if dest in live_keys:
                normalized[direction] = dest
                continue
            canonical = live_identity.get(dest)
            if canonical and canonical in live_keys:
                normalized[direction] = canonical
                changed = True
                patches += 1
                continue
            normalized[direction] = dest
        if changed:
            room["exits"] = normalized
    return patches


def _merge_remodel_inbound_exits(
    live_index,
    backup_index,
    live_keys,
    live_identity,
    *,
    skipped_backup_keys=None,
):
    """Restore named inbound exits (Garage, …) after git reset."""
    patches = 0
    skipped_backup_keys = skipped_backup_keys or set()
    for target_key, backup_room in backup_index.items():
        label = str(backup_room.get("remodel_inbound_exit") or "").strip().lower()
        if not label:
            continue
        live_target_key = _live_key_for_backup_ref(
            target_key,
            backup_room,
            live_index,
            live_identity,
            skipped_backup_keys=skipped_backup_keys,
        )
        if live_target_key is None:
            continue
        for nkey, nbackup in backup_index.items():
            live_neighbor_key = _live_key_for_backup_ref(
                nkey,
                nbackup,
                live_index,
                live_identity,
                skipped_backup_keys=skipped_backup_keys,
            )
            nlive = live_index.get(live_neighbor_key) if live_neighbor_key else None
            if nlive is None:
                continue
            bexits = nbackup.get("exits") or {}
            dest = bexits.get(label)
            if dest not in (target_key, live_target_key):
                continue
            live_exits = dict(nlive.get("exits") or {})
            prior = dict(live_exits)
            for direction, dest_key in list(live_exits.items()):
                if dest_key == live_target_key and direction != label:
                    if direction in _REMODEL_RELABELABLE_EXITS:
                        live_exits.pop(direction, None)
            live_exits[label] = live_target_key
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
    live_index = _room_index(live)
    live_identity = _build_identity_map(live_index)
    backup_index = _room_index(backup)
    backup_remodel_index = _backup_remodel_index(backup_index)
    backup_identity = _build_identity_map(backup_index)

    added = []
    skipped_vnum = []
    remodel_patches = 0
    for key, room in backup_index.items():
        if key in live_keys:
            continue
        correlated_live_key = None
        for token in _identity_tokens(room):
            owner = live_identity.get(token)
            if owner and owner in live_index:
                correlated_live_key = owner
                break
        if correlated_live_key is not None:
            live_owner = live_index[correlated_live_key]
            if _patch_room_remodel_fields(live_owner, room):
                remodel_patches += 1
            skipped_vnum.append(key)
            continue
        vnum = _vnum_for_room(room)
        owner = live_vnums.get(vnum)
        if owner is not None and owner != key:
            live_owner = live_index.get(owner)
            if live_owner is not None and _patch_room_remodel_fields(live_owner, room):
                remodel_patches += 1
            skipped_vnum.append(key)
            continue
        live.setdefault("rooms", []).append(copy.deepcopy(room))
        live_keys.add(key)
        live_vnums[vnum] = key
        added.append(key)

    live_index = _room_index(live)
    live_identity = _build_identity_map(live_index)
    live_vnums = _vnum_index(live)
    skipped_backup_keys = set(skipped_vnum)

    for _key, live_room in live_index.items():
        backup_room = _find_backup_room_for_live(
            live_room, backup_index, backup_remodel_index,
        )
        if backup_room is None:
            continue
        if _patch_room_remodel_fields(live_room, backup_room):
            remodel_patches += 1

    exit_patches = _merge_remodel_inbound_exits(
        live_index,
        backup_index,
        live_keys,
        live_identity,
        skipped_backup_keys=skipped_backup_keys,
    )
    for key, live_room in live_index.items():
        backup_room = _find_backup_row_for_live(
            live_room, backup_index, backup_identity,
        )
        if backup_room is None:
            continue
        live_exits = dict(live_room.get("exits") or {})
        changed = False
        for direction, dest in (backup_room.get("exits") or {}).items():
            if direction in live_exits:
                continue
            dest_live = _resolve_backup_exit_dest(
                dest,
                backup_index,
                live_keys,
                live_identity,
                skipped_backup_keys=skipped_backup_keys,
            )
            if dest_live not in live_keys:
                continue
            live_exits[direction] = dest_live
            changed = True
            exit_patches += 1
        if changed:
            live_room["exits"] = live_exits

    exit_patches += _normalize_merged_exit_dests(live)

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
