"""
map_backups.py -- staff snapshots of map/zone JSON (dig / Studio Live Edit).

Live dig and Area Studio Live Edit rewrite ``content/maps/*.json`` and
``content/zones/*.json`` on the live checkout. Those trees follow
``origin/main`` (not auto-deploy protected) so map ships land on live.
Staff should ``gm maps backup`` / ``gm maps backup all`` before risky
manual digs; restore with ``gm maps restore <id>``.

Backups live under ``content/map_backups/<map_id>.json`` (gitignored).
They survive ``git reset --hard`` as untracked files, and auto-deploy
also prefix-protects ``content/map_backups/`` so snapshots are stashed
and restored across feature-push resets. Protect does **not** block the
in-game backup verbs from writing.

Staff verbs (``gm on``):
  gm maps backup <id>   -- replace the backup slot with the current live file
  gm maps backup all    -- snapshot every content/maps + content/zones JSON
  gm maps restore <id>  -- copy the backup onto the live file + hot-reload

Dig / validated saves seed a backup **once** from the pre-write bytes when
no slot exists yet (safety net for the first edit). Studio Live Edit seeds
the same way on the remote box before SCP when the slot is empty.

``remodel`` / ``populate`` (``append_hand_rooms``) call
``refresh_backup_from_live`` after each successful save so the protected
backup slot always matches the latest stamped house / garage JSON -- not
a stale first-seed snapshot. That way ``gm maps restore`` can unstick
players even when auto-deploy overlays live ``content/zones/*.json``.

All validated map writes through ``map_store.save_doc_validated`` also
refresh the hot backup and mark daily archive state (see
``engine/map_archive.py``).
"""

from __future__ import annotations

import os
import shutil

import maps as maps_mod

from engine import hooks as hooks_mod


class MapBackupError(ValueError):
    """Raised for backup/restore failures (message is GM-facing)."""


def backups_dir(root=None):
    """Absolute path to ``content/map_backups`` under the repo root."""
    if root is None:
        root = os.getcwd()
    return os.path.join(os.path.abspath(root), "content", "map_backups")


def _safe_map_id(map_id):
    """Filename-safe map_id (no path separators)."""
    text = (map_id or "").strip()
    if not text:
        raise MapBackupError("empty map id")
    cleaned = text.replace("/", "_").replace("\\", "_").replace("..", "_")
    if cleaned != text or cleaned in (".", ".."):
        raise MapBackupError(f"refusing unsafe map id {map_id!r}")
    return cleaned


def backup_path_for(map_id, root=None):
    """Return the absolute backup file path for ``map_id``."""
    safe = _safe_map_id(map_id)
    return os.path.join(backups_dir(root), f"{safe}.json")


def has_backup(map_id, root=None):
    """True when a backup file exists for this map_id."""
    try:
        return os.path.isfile(backup_path_for(map_id, root=root))
    except MapBackupError:
        return False


def _ensure_backup_dir(root=None):
    """Create the backup directory if missing."""
    path = backups_dir(root)
    os.makedirs(path, exist_ok=True)
    return path


def seed_backup_if_missing(live_path, map_id, *, root=None):
    """Copy ``live_path`` into the backup slot only when no backup exists.

    Returns True if a new backup was written, False if a slot already
    existed or the live file was missing. Never raises for I/O -- dig /
    Studio must not fail because a safety snapshot failed.
    """
    try:
        if has_backup(map_id, root=root):
            return False
        if not live_path or not os.path.isfile(live_path):
            return False
        _ensure_backup_dir(root)
        dest = backup_path_for(map_id, root=root)
        shutil.copy2(live_path, dest)
        return True
    except (OSError, MapBackupError):
        return False


def seed_backup_bytes_if_missing(raw_bytes, map_id, *, root=None):
    """Like ``seed_backup_if_missing`` but from in-memory pre-write bytes."""
    try:
        if has_backup(map_id, root=root):
            return False
        if raw_bytes is None:
            return False
        _ensure_backup_dir(root)
        dest = backup_path_for(map_id, root=root)
        tmp = dest + ".tmp"
        with open(tmp, "wb") as handle:
            handle.write(raw_bytes)
        os.replace(tmp, dest)
        return True
    except (OSError, MapBackupError):
        return False


def refresh_backup_from_live(live_path, map_id, *, root=None):
    """Overwrite the backup slot with the current live map/zone file.

    Used after ``remodel`` / ``populate`` so ``content/map_backups/`` (auto-
    deploy protected) always holds the latest stamped JSON -- not a stale
    first-seed snapshot. Never raises for I/O; returns True on success.
    """
    try:
        if not live_path or not os.path.isfile(live_path):
            return False
        if not map_id:
            return False
        _ensure_backup_dir(root)
        dest = backup_path_for(map_id, root=root)
        shutil.copy2(live_path, dest)
        return True
    except (OSError, MapBackupError):
        return False


def resolve_live_map(name):
    """Resolve a map/zone name to (abspath, filename, map_id, kind, data)."""
    try:
        path, filename, data, kind = maps_mod.resolve_map_file(name)
    except FileNotFoundError as exc:
        raise MapBackupError(str(exc)) from exc
    map_id = maps_mod._map_id_for(filename, data)
    return path, filename, map_id, kind, data


def write_backup(name, *, root=None):
    """Replace the backup slot with the current live map/zone JSON.

    Returns a short GM-facing status string.
    """
    path, filename, map_id, kind, _data = resolve_live_map(name)
    _ensure_backup_dir(root)
    dest = backup_path_for(map_id, root=root)
    try:
        shutil.copy2(path, dest)
    except OSError as exc:
        raise MapBackupError(f"backup write failed: {exc}") from exc
    size = os.path.getsize(dest)
    return (
        f"Backed up {map_id} ({kind} file {filename}) → "
        f"content/map_backups/{_safe_map_id(map_id)}.json "
        f"({size} bytes)."
    )


def write_backup_all(*, root=None):
    """Snapshot every on-disk map and zone JSON into ``content/map_backups/``.

    Walks ``content/maps/*.json`` and ``content/zones/*.json`` (same set
    as boot). Each file replaces its backup slot. One bad file does not
    stop the rest -- failures are collected into the summary.

    Returns a multi-line GM-facing status string.
    """
    ok_ids = []
    errors = []
    for path in maps_mod.iter_map_json_paths():
        filename = os.path.basename(path)
        try:
            _path, _fn, map_id, kind, _data = resolve_live_map(filename)
            write_backup(filename, root=root)
            ok_ids.append(f"{map_id} ({kind})")
        except MapBackupError as exc:
            errors.append(f"{filename}: {exc}")
        except (OSError, ValueError, TypeError) as exc:
            # Corrupt JSON / path glitches -- keep going.
            errors.append(f"{filename}: {exc}")
    lines = [
        f"[MAP BACKUP ALL] {len(ok_ids)} snapshot(s) written "
        f"under content/map_backups/."
    ]
    if ok_ids:
        # Keep the list readable; staff can scroll.
        lines.append("  ok: " + ", ".join(ok_ids))
    if errors:
        lines.append(f"  failed ({len(errors)}):")
        for err in errors:
            lines.append(f"    - {err}")
    if not ok_ids and not errors:
        lines.append("  (no map/zone JSON found on disk)")
    return "\n".join(lines)


def restore_backup(name, game=None, *, root=None):
    """Copy the backup onto the live map/zone file and hot-reload if loaded.

    Returns a short GM-facing status string.
    """
    path, filename, map_id, kind, _data = resolve_live_map(name)
    src = backup_path_for(map_id, root=root)
    if not os.path.isfile(src):
        raise MapBackupError(
            f"No backup for {map_id!r}. "
            f"Run: gm maps backup {map_id}"
        )
    try:
        shutil.copy2(src, path)
    except OSError as exc:
        raise MapBackupError(f"restore write failed: {exc}") from exc

    reload_note = ""
    if game is not None:
        try:
            hot = hooks_mod.map_restore_hot_reload(game, map_id)
            if hot is not None:
                reload_note = hot
            else:
                reload_note = ""
        except Exception as exc:
            # Disk is restored; staff can gm maps unload/load by hand.
            reload_note = (
                f" Disk restored; hot-reload deferred ({exc}). "
                f"Try: gm maps unload {map_id} then gm maps load {map_id}"
            )
    else:
        reload_note = " Disk restored (no Game — reload on next load)."

    return (
        f"Restored {map_id} ({kind} file {filename}) from "
        f"content/map_backups/{_safe_map_id(map_id)}.json."
        f"{reload_note}"
    )
