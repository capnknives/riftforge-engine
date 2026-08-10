"""player_save_backup.py -- extra checkpoint tank for player ``save``.

When a player types ``save``, persistence already flushes their character
row to SQLite. This module archives an additional JSON snapshot under
``backups/player-checkpoints/`` so staff (or a future restore tool) can
recover one body without rolling back the whole world DB.

Fail-soft: backup errors never block a successful player save.
"""

from __future__ import annotations

import json
import os
import time
from urllib.parse import quote

from engine.world_backup import backups_root

CHECKPOINT_VERSION = 1
CHECKPOINT_SUBDIR = "player-checkpoints"
DEFAULT_RETENTION = 10


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def checkpoints_enabled():
    """True unless ``RIFTFORGE_PLAYER_CHECKPOINTS=0``."""
    raw = (os.environ.get("RIFTFORGE_PLAYER_CHECKPOINTS") or "").strip().lower()
    return raw not in ("0", "off", "false", "no")


def retention_count():
    """How many checkpoints to keep per character (default 10)."""
    raw = (os.environ.get("RIFTFORGE_PLAYER_CHECKPOINT_RETENTION") or "").strip()
    if not raw:
        return DEFAULT_RETENTION
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_RETENTION


def _checkpoint_dir_name(character_name):
    """Filesystem-safe folder name that still maps 1:1 to the login body."""
    name = (character_name or "").strip() or "unknown"
    # quote() keeps alnum; encodes :, /, spaces, etc.
    return quote(name, safe="")


def checkpoints_root(root=None):
    return os.path.join(backups_root(root or _repo_root()), CHECKPOINT_SUBDIR)


def character_checkpoint_dir(character_name, *, root=None):
    return os.path.join(
        checkpoints_root(root),
        _checkpoint_dir_name(character_name),
    )


def _parse_blob(blob_text):
    if not blob_text:
        return {}
    try:
        return json.loads(blob_text)
    except (TypeError, ValueError):
        return {"_raw": blob_text}


def _item_row_dict(row):
    # (key, description, holder_type, holder_key, container_blob)
    return {
        "key": row[0],
        "description": row[1],
        "holder_type": row[2],
        "holder_key": row[3],
        "container": _parse_blob(row[4] if len(row) > 4 else "{}"),
    }


def build_checkpoint_payload(char_row, item_rows):
    """Serialize one character save row set for the checkpoint tank."""
    name, description, room_key, blob_text = char_row
    return {
        "version": CHECKPOINT_VERSION,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "character": name,
        "description": description,
        "room_key": room_key,
        "stats": _parse_blob(blob_text),
        "items": [_item_row_dict(row) for row in (item_rows or ())],
    }


def _prune_old_checkpoints(char_dir, keep):
    """Drop oldest JSON files when a character exceeds the retention cap."""
    try:
        names = [
            n for n in os.listdir(char_dir)
            if n.endswith(".json") and os.path.isfile(os.path.join(char_dir, n))
        ]
    except OSError:
        return
    if len(names) <= keep:
        return
    paths = [os.path.join(char_dir, n) for n in names]
    paths.sort(key=lambda p: os.path.getmtime(p))
    for path in paths[: len(paths) - keep]:
        try:
            os.remove(path)
        except OSError:
            pass


def archive_player_checkpoint(char_row, item_rows, *, root=None):
    """Write a player-initiated save to the checkpoint tank.

    Returns ``(path_or_none, detail)`` where *detail* is human-readable for
    logs. Never raises.
    """
    if not checkpoints_enabled():
        return None, "disabled"
    if not char_row:
        return None, "empty row"
    name = char_row[0]
    if not name:
        return None, "no character name"

    char_dir = character_checkpoint_dir(name, root=root)
    try:
        os.makedirs(char_dir, exist_ok=True)
    except OSError as exc:
        return None, f"mkdir failed: {exc!r}"

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    # Subsecond suffix: rapid tests (or clock skew) must not clobber same stamp.
    micro = int((time.time() % 1) * 1_000_000)
    filename = f"{stamp}-{micro:06d}.json"
    path = os.path.join(char_dir, filename)
    payload = build_checkpoint_payload(char_row, item_rows)

    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as exc:
        return None, f"write failed: {exc!r}"

    _prune_old_checkpoints(char_dir, retention_count())
    rel = os.path.relpath(path, root or _repo_root()).replace("\\", "/")
    print(
        f"[player_checkpoint] archived {name!r} -> {rel}",
        flush=True,
    )
    return path, rel


def list_checkpoint_summaries(character_name, *, root=None):
    """Newest-first checkpoint metadata for staff listing."""
    char_dir = character_checkpoint_dir(character_name, root=root)
    if not os.path.isdir(char_dir):
        return []
    entries = []
    for filename in os.listdir(char_dir):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(char_dir, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            payload = {}
        entries.append(
            {
                "path": path,
                "file": filename,
                "time": payload.get("time") or "?",
                "room_key": payload.get("room_key") or "",
            }
        )
    entries.sort(key=lambda row: os.path.getmtime(row["path"]), reverse=True)
    return entries


def resolve_checkpoint_path(character_name, pick=None, *, root=None):
    """Return ``(path, label)`` for latest or a filename stem match."""
    entries = list_checkpoint_summaries(character_name, root=root)
    if not entries:
        return None, "no player checkpoints on file"
    if not pick:
        row = entries[0]
        return row["path"], row["file"]
    pick_l = pick.lower().strip()
    for row in entries:
        stem = row["file"][:-5] if row["file"].endswith(".json") else row["file"]
        if (
            row["file"].lower() == pick_l
            or stem.lower() == pick_l
            or stem.lower().startswith(pick_l)
        ):
            return row["path"], row["file"]
    return None, f"no checkpoint matching {pick!r}"


def _checkpoint_root(game):
    return getattr(game, "report_dir", None) or _repo_root()


def _clear_held_items(character):
    """Drop inventory + gear_bag refs before rebuilding from checkpoint."""
    if getattr(character, "inventory", None) is not None:
        character.inventory.clear()
    bag = getattr(character, "gear_bag", None)
    if isinstance(bag, list):
        bag.clear()


def _place_checkpoint_items(character, item_rows, game):
    """Rebuild inventory / gear_bag from checkpoint JSON rows."""
    from engine import hooks
    from engine import persistence as persistence_mod

    for row in item_rows or []:
        if not isinstance(row, dict):
            continue
        holder = (row.get("holder_type") or "character").lower()
        item = persistence_mod.item_from_saved_container(
            row.get("key") or "item",
            row.get("description") or row.get("key") or "item",
            row.get("container") or {},
        )
        if holder == "gear":
            bag = getattr(character, "gear_bag", None)
            if bag is None or not isinstance(bag, list):
                character.gear_bag = []
                bag = character.gear_bag
            bag.append(item)
        else:
            character.inventory.append(item)
        hooks.enrich_loaded_item(item)
    hooks.rebind_character_equipment(character)
    from engine.systems import containers as containers_mod

    containers_mod.heal_character_kit_bag(character)
    containers_mod.rebind_containers_from_inventory(character)


def restore_player_checkpoint(game, target, pick=None, *, root=None):
    """Restore one live body from a player ``save`` checkpoint tank file.

    Returns ``(ok, message)``. The target Character must already exist in
    the world (online or Echo). Writes through to SQLite without archiving
    another checkpoint.
    """
    if game is None or target is None:
        return False, "Nothing to restore."
    name = getattr(target, "key", None) or ""
    if not name:
        return False, "Target has no character key."

    root = root or _checkpoint_root(game)
    path, label = resolve_checkpoint_path(name, pick, root=root)
    if path is None:
        return False, label

    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"Could not read checkpoint ({exc!r})."

    if (payload.get("character") or "") != name:
        return False, (
            f"Checkpoint is for {payload.get('character')!r}, not {name!r}."
        )

    from engine.hooks import apply_character_blob
    from engine.persistence import (
        _resolve_saved_room,
        persist_save_character,
    )

    stats = payload.get("stats") or {}
    if not isinstance(stats, dict):
        return False, "Checkpoint stats blob is invalid."

    target.description = payload.get("description") or target.description
    apply_character_blob(target, stats)
    _clear_held_items(target)
    _place_checkpoint_items(target, payload.get("items") or [], game)

    room_key = payload.get("room_key") or ""
    room = _resolve_saved_room(game, room_key, name)
    if room is not None:
        target.move_to(room)

    conn = getattr(game, "db", None)
    if conn is None:
        return False, "No database connection."
    ok, msg = persist_save_character(conn, game, target, player_checkpoint=False)
    if not ok:
        return False, msg
    when = payload.get("time") or label
    print(
        f"[player_checkpoint] restored {name!r} from {label} ({when})",
        flush=True,
    )
    return True, (
        f"Restored {name} from player checkpoint {label} "
        f"({when})."
    )
