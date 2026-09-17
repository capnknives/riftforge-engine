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
from urllib.parse import quote, unquote

from engine.world_backup import backups_root

CHECKPOINT_VERSION = 2
CHECKPOINT_SUBDIR = "player-checkpoints"
DEFAULT_RETENTION = 10

# Per-boot memo: listing every save-tank / nightly JSON per character
# (414 x os.listdir + json.load) was the third disk leg of the
# 2026-09-11 copyover hang. Reset via reset_checkpoint_scan_caches().
_SAVE_INDEX = None  # (fingerprint, {name_lower: [paths]})
_NIGHTLY_INDEX = None  # (fingerprint, {name_lower: [summary shells]})


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def reset_checkpoint_scan_caches():
    """Drop per-boot save-tank / nightly JSON indexes (smokes + new backups)."""
    global _SAVE_INDEX, _NIGHTLY_INDEX
    _SAVE_INDEX = None
    _NIGHTLY_INDEX = None


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
    # (key, description, holder_type, holder_key, container_blob[, holder_cnum])
    out = {
        "key": row[0],
        "description": row[1],
        "holder_type": row[2],
        "holder_key": row[3],
        "container": _parse_blob(row[4] if len(row) > 4 else "{}"),
    }
    if len(row) > 5 and row[5]:
        out["holder_cnum"] = row[5]
    return out


def build_checkpoint_payload(char_row, item_rows, built_sites=None):
    """Serialize one character save row set for the checkpoint tank."""
    name, description, room_key, blob_text = char_row[:4]
    payload = {
        "version": CHECKPOINT_VERSION,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "character": name,
        "description": description,
        "room_key": room_key,
        "stats": _parse_blob(blob_text),
        "items": [_item_row_dict(row) for row in (item_rows or ())],
    }
    if isinstance(built_sites, dict) and built_sites:
        payload["built_sites"] = built_sites
    return payload


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


def archive_player_checkpoint(char_row, item_rows, *, root=None, built_sites=None):
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
    payload = build_checkpoint_payload(
        char_row, item_rows, built_sites=built_sites,
    )

    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as exc:
        return None, f"write failed: {exc!r}"

    _prune_old_checkpoints(char_dir, retention_count())
    reset_checkpoint_scan_caches()
    rel = os.path.relpath(path, root or _repo_root()).replace("\\", "/")
    print(
        f"[player_checkpoint] archived {name!r} -> {rel}",
        flush=True,
    )
    return path, rel


def _load_checkpoint_payload(path):
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _site_kinds_from_built(built):
    """Human labels for site snapshots stored on a checkpoint."""
    if not isinstance(built, dict):
        return []
    labels = []
    if built.get("homestead"):
        labels.append("homestead")
    if built.get("demesne"):
        labels.append("demesne")
    if built.get("personal_realm"):
        labels.append("personal realm")
    shops = built.get("player_shops") or []
    if shops:
        labels.append("shop" if len(shops) == 1 else "shops")
    if built.get("township"):
        labels.append("township")
    return labels


def _summarize_checkpoint_file(path, filename, *, source_label, backup_date=None):
    payload = _load_checkpoint_payload(path)
    items = payload.get("items") or []
    return {
        "path": path,
        "file": filename,
        "time": payload.get("time") or "?",
        "room_key": payload.get("room_key") or "",
        "source": "save" if source_label == "save" else "nightly",
        "source_label": source_label,
        "backup_date": backup_date or payload.get("backup_date") or "",
        "item_count": len(items) if isinstance(items, list) else 0,
        "site_kinds": _site_kinds_from_built(payload.get("built_sites")),
    }


def _listing_fingerprint(path):
    """Child names + mtime + size — same idea as hakai_archive's backup index."""
    if not path or not os.path.isdir(path):
        return (path or "", ())
    rows = []
    try:
        names = os.listdir(path)
    except OSError:
        return (path, ())
    for name in names:
        child = os.path.join(path, name)
        try:
            st = os.stat(child)
        except OSError:
            continue
        rows.append((name, int(st.st_mtime), int(st.st_size)))
    rows.sort()
    return (path, tuple(rows))


def _save_checkpoint_index(root=None):
    """One pass over ``player-checkpoints/``: name -> json paths."""
    global _SAVE_INDEX
    root = root or _repo_root()
    base = checkpoints_root(root)
    fp = _listing_fingerprint(base)
    cached = _SAVE_INDEX
    if cached is not None and cached[0] == fp:
        return cached[1]
    by_name = {}
    if os.path.isdir(base):
        try:
            folders = os.listdir(base)
        except OSError:
            folders = []
        for folder in folders:
            char_dir = os.path.join(base, folder)
            if not os.path.isdir(char_dir):
                continue
            try:
                name = unquote(folder).strip().lower()
            except Exception:
                name = (folder or "").strip().lower()
            if not name:
                continue
            paths = []
            try:
                files = os.listdir(char_dir)
            except OSError:
                files = []
            for filename in files:
                if not filename.endswith(".json"):
                    continue
                path = os.path.join(char_dir, filename)
                if os.path.isfile(path):
                    paths.append((path, filename))
            by_name[name] = paths
    _SAVE_INDEX = (fp, by_name)
    return by_name


def _list_save_checkpoint_summaries(character_name, *, root=None):
    """Manual ``save`` tank entries for one character."""
    name = (character_name or "").strip().lower()
    if not name:
        return []
    entries = []
    for path, filename in _save_checkpoint_index(root).get(name) or ():
        row = _summarize_checkpoint_file(path, filename, source_label="save")
        entries.append(row)
    return entries


def _nightly_items_index(root=None):
    """One pass over dated ``player-items/`` folders keyed by filename.

    Used to open every nightly JSON for every character just to match a
    name. The filename is already the quoted storage key.
    """
    global _NIGHTLY_INDEX
    from engine.nightly_player_items import NIGHTLY_ITEMS_SUBDIR

    root = root or _repo_root()
    base = backups_root(root)
    fp = _listing_fingerprint(base)
    cached = _NIGHTLY_INDEX
    if cached is not None and cached[0] == fp:
        return cached[1]
    by_name = {}
    if os.path.isdir(base):
        try:
            names = os.listdir(base)
        except OSError:
            names = []
        for entry in names:
            if len(entry) != 10 or entry[4] != "-" or entry[7] != "-":
                continue
            items_dir = os.path.join(base, entry, NIGHTLY_ITEMS_SUBDIR)
            if not os.path.isdir(items_dir):
                continue
            try:
                files = os.listdir(items_dir)
            except OSError:
                files = []
            for filename in files:
                if not filename.endswith(".json"):
                    continue
                path = os.path.join(items_dir, filename)
                if not os.path.isfile(path):
                    continue
                stem = filename[:-5]
                try:
                    char_name = unquote(stem).strip().lower()
                except Exception:
                    char_name = stem.strip().lower()
                if not char_name:
                    continue
                by_name.setdefault(char_name, []).append(
                    {
                        "path": path,
                        "file": filename,
                        "backup_date": entry,
                        "source_label": f"nightly {entry}",
                    }
                )
    _NIGHTLY_INDEX = (fp, by_name)
    return by_name


def _list_nightly_item_summaries(character_name, *, root=None):
    """Nightly ``backups/YYYY-MM-DD/player-items/`` copies for one character."""
    name_lower = (character_name or "").strip().lower()
    if not name_lower:
        return []
    entries = []
    for shell in _nightly_items_index(root).get(name_lower) or ():
        row = _summarize_checkpoint_file(
            shell["path"],
            shell["file"],
            source_label=shell["source_label"],
            backup_date=shell.get("backup_date") or "",
        )
        entries.append(row)
    return entries


def list_checkpoint_summaries(character_name, *, root=None):
    """Newest-first recovery metadata: manual save tank + nightly player-items."""
    entries = _list_save_checkpoint_summaries(character_name, root=root)
    entries.extend(_list_nightly_item_summaries(character_name, root=root))
    entries.sort(key=lambda row: os.path.getmtime(row["path"]), reverse=True)
    return entries


def resolve_checkpoint_path(character_name, pick=None, *, root=None):
    """Return ``(path, label)`` for latest or a filename / date stem match."""
    entries = list_checkpoint_summaries(character_name, root=root)
    if not entries:
        return None, "no player checkpoints on file"
    if not pick:
        row = entries[0]
        label = row.get("source_label") or row["file"]
        return row["path"], label
    pick_l = pick.lower().strip()
    # Exact nightly date: ``2026-09-07``
    if len(pick_l) == 10 and pick_l[4] == "-" and pick_l[7] == "-":
        for row in entries:
            if (row.get("backup_date") or "").lower() == pick_l:
                label = row.get("source_label") or row["file"]
                return row["path"], label
    for row in entries:
        stem = row["file"][:-5] if row["file"].endswith(".json") else row["file"]
        label = row.get("source_label") or row["file"]
        if (
            row["file"].lower() == pick_l
            or stem.lower() == pick_l
            or stem.lower().startswith(pick_l)
            or (row.get("backup_date") or "").lower() == pick_l
            or (row.get("backup_date") or "").lower().startswith(pick_l)
        ):
            return row["path"], label
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


def _checkpoint_row_container(row):
    """Return the parsed container dict from a checkpoint item row."""
    if not isinstance(row, dict):
        return {}
    container = row.get("container") or {}
    if isinstance(container, str):
        return _parse_blob(container)
    if isinstance(container, dict):
        return container
    return {}


def _item_identity_key(*, catalog_id=None, item_key=None, row=None, item=None):
    """Stable dedupe key: catalog_id first, else item key."""
    if row is not None:
        container = _checkpoint_row_container(row)
        catalog_id = container.get("catalog_id")
        item_key = row.get("key")
    elif item is not None:
        catalog_id = getattr(item, "catalog_id", None)
        item_key = getattr(item, "key", None)
    cat = (catalog_id or "").strip().lower()
    if cat:
        return f"cat:{cat}"
    key = (item_key or "").strip().lower()
    return f"key:{key}" if key else ""


def _iter_held_item_objects(character):
    """Yield every Item on the body (inventory, gear_bag, nested bags)."""
    from engine.systems.containers import bag_contents

    seen = set()

    def _walk(items):
        for piece in list(items or []):
            if piece is None:
                continue
            pid = id(piece)
            if pid in seen:
                continue
            seen.add(pid)
            yield piece
            nested = bag_contents(piece)
            if nested:
                yield from _walk(nested)

    yield from _walk(getattr(character, "inventory", None))
    yield from _walk(getattr(character, "gear_bag", None))


def _held_item_identities(character):
    """Set of identity keys for everything the character currently holds."""
    ids = set()
    for piece in _iter_held_item_objects(character):
        ident = _item_identity_key(item=piece)
        if ident:
            ids.add(ident)
    return ids


def _append_checkpoint_item(character, row, *, game=None):
    """Place one checkpoint row onto *character* (inventory or gear_bag)."""
    from engine import hooks
    from engine import persistence as persistence_mod

    if not isinstance(row, dict):
        return None
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
        inv = getattr(character, "inventory", None)
        if inv is None:
            character.inventory = []
            inv = character.inventory
        inv.append(item)
    hooks.enrich_loaded_item(item)
    return item


def _rebind_checkpoint_gear(character):
    """Wire equipment slots and worn containers after item restore."""
    from engine import hooks
    from engine.systems import containers as containers_mod

    hooks.rebind_character_equipment(character)
    containers_mod.heal_character_kit_bag(character)
    containers_mod.rebind_containers_from_inventory(character)


def _place_checkpoint_items(character, item_rows, game):
    """Rebuild inventory / gear_bag from checkpoint JSON rows."""
    for row in item_rows or []:
        _append_checkpoint_item(character, row, game=game)
    _rebind_checkpoint_gear(character)


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

    if payload.get("kind") == "nightly-items" or "stats" not in payload:
        return False, (
            "That copy is a nightly gear snapshot — use "
            "gm restore checkpoint items instead."
        )
    stats = payload.get("stats") or {}
    if not isinstance(stats, dict):
        return False, "Checkpoint stats blob is invalid."

    target.description = payload.get("description") or target.description
    apply_character_blob(target, stats)
    _clear_held_items(target)
    _place_checkpoint_items(target, payload.get("items") or [], game)

    built = payload.get("built_sites") or {}
    site_note = ""
    if built:
        try:
            from engine import hooks as hooks_mod

            _ok, kinds = hooks_mod.restore_player_built_sites(
                game, target, built,
            )
            if kinds:
                site_note = " Sites restored: " + ", ".join(kinds) + "."
        except Exception as exc:
            print(
                f"[player_checkpoint] built-site restore skipped for "
                f"{name!r}: {exc!r}",
                flush=True,
            )

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
        f"({when}).{site_note}"
    )


def restore_player_checkpoint_items(game, target, pick=None, *, root=None):
    """Merge missing gear from a player ``save`` checkpoint into *target*.

    Adds checkpoint inventory / equipment / clothing rows the body does not
    already hold (matched by ``catalog_id``, else item key). Does **not**
    change stats, room, quests, or other character blob fields. Persists via
    ``persist_save_character`` without archiving another checkpoint.

    Returns ``(ok, message)``.
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

    held = _held_item_identities(target)
    checkpoint_rows = payload.get("items") or []
    added = 0
    for row in checkpoint_rows:
        if not isinstance(row, dict):
            continue
        ident = _item_identity_key(row=row)
        if ident and ident in held:
            continue
        item = _append_checkpoint_item(target, row, game=game)
        if item is None:
            continue
        added += 1
        if ident:
            held.add(ident)

    if added:
        _rebind_checkpoint_gear(target)

    from engine.persistence import persist_save_character

    conn = getattr(game, "db", None)
    if conn is None:
        return False, "No database connection."
    ok, msg = persist_save_character(
        conn, game, target, player_checkpoint=False,
    )
    if not ok:
        return False, msg

    when = payload.get("time") or label
    print(
        f"[player_checkpoint] merged items for {name!r} from {label} "
        f"({when}); added {added}",
        flush=True,
    )
    detail = (
        f"Merged {added} missing item(s) from player checkpoint {label} "
        f"({when})."
        if added
        else (
            f"No missing items to merge from player checkpoint {label} "
            f"({when})."
        )
    )
    return True, detail
