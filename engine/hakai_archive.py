"""hakai_archive.py -- snapshot a body before GM wipe, and bring it back.

``gm hakai`` erases a character from the live world and the next save.
This module writes a JSON snapshot first (same shape as player
``save`` checkpoints) under ``backups/hakai-archive/`` so staff can
``gm unhakai <name>`` later.

When no hakai archive exists (wipes from before this ship), restore
picks the newest leftover copy: a player checkpoint, or a character
row still sitting in ``backups/pre-deploy/`` / nightly ``backups/YYYY-MM-DD/``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from urllib.parse import quote, unquote

from engine.world_backup import backups_root

ARCHIVE_VERSION = 1
ARCHIVE_SUBDIR = "hakai-archive"
DEFAULT_RETENTION = 5


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _game_root(game):
    """Directory that holds ``backups/`` (same as player checkpoints)."""
    return getattr(game, "report_dir", None) or _repo_root()


def _dir_name(character_name):
    """Filesystem-safe folder that still maps 1:1 to the storage key."""
    name = (character_name or "").strip() or "unknown"
    return quote(name, safe="")


def archive_root(root=None):
    return os.path.join(backups_root(root or _repo_root()), ARCHIVE_SUBDIR)


def character_archive_dir(character_name, *, root=None):
    return os.path.join(archive_root(root), _dir_name(character_name))


def _utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_iso(text):
    """Return a comparable UTC string, or empty when the stamp is junk."""
    raw = (text or "").strip()
    if not raw:
        return ""
    return raw.replace(" ", "T")


def _prune_old_archives(char_dir, keep):
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


def live_body_by_storage_key(game, name):
    """Exact ``Character.key`` match — never substring / Watcher-Name hits."""
    needle = (name or "").strip().lower()
    if not needle:
        return None
    for obj in getattr(game, "characters", None) or []:
        if (getattr(obj, "key", "") or "").lower() == needle:
            return obj
    return None


def build_hakai_payload(character):
    """Serialize one live body the way player checkpoints do."""
    from engine.hooks import character_to_blob
    from engine.persistence import snapshot_held_items

    room = getattr(character, "location", None)
    room_key = getattr(room, "key", None) or ""
    stats = character_to_blob(character)
    if not isinstance(stats, dict):
        stats = {}
    return {
        "version": ARCHIVE_VERSION,
        "kind": "hakai",
        "time": _utc_now(),
        "character": getattr(character, "key", "") or "",
        "description": getattr(character, "description", "") or "",
        "room_key": room_key,
        "stats": stats,
        "items": snapshot_held_items(character),
    }


def archive_hakai_snapshot(game, character, *, root=None):
    """Write a hakai undo snapshot. Returns ``(path_or_none, detail)``.

    Fail-soft: a write error must not block the wipe itself.
    """
    if character is None:
        return None, "no character"
    name = getattr(character, "key", None) or ""
    if not name:
        return None, "no character key"
    root = root or _game_root(game)
    char_dir = character_archive_dir(name, root=root)
    try:
        os.makedirs(char_dir, exist_ok=True)
    except OSError as exc:
        return None, f"mkdir failed: {exc!r}"

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    micro = int((time.time() % 1) * 1_000_000)
    filename = f"{stamp}-{micro:06d}.json"
    path = os.path.join(char_dir, filename)
    try:
        payload = build_hakai_payload(character)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except Exception as exc:
        return None, f"write failed: {exc!r}"

    _prune_old_archives(char_dir, DEFAULT_RETENTION)
    rel = os.path.relpath(path, root).replace("\\", "/")
    print(f"[hakai_archive] saved {name!r} -> {rel}", flush=True)
    return path, rel


def _load_payload_file(path):
    """Return a payload dict or None."""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def list_hakai_archives(character_name, *, root=None):
    """Newest-first archive metadata for one storage key."""
    char_dir = character_archive_dir(character_name, root=root)
    if not os.path.isdir(char_dir):
        return []
    entries = []
    for filename in os.listdir(char_dir):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(char_dir, filename)
        if not os.path.isfile(path):
            continue
        payload = _load_payload_file(path) or {}
        entries.append(
            {
                "path": path,
                "file": filename,
                "time": payload.get("time") or "?",
                "room_key": payload.get("room_key") or "",
                "source": "hakai-archive",
            }
        )
    entries.sort(key=lambda row: os.path.getmtime(row["path"]), reverse=True)
    return entries


def _predeploy_stamp_iso(folder_name):
    """``20260831-165840`` -> ``2026-08-31T16:58:40Z``."""
    raw = (folder_name or "").strip()
    if len(raw) < 15 or raw[8] != "-":
        return ""
    date, clock = raw[:8], raw[9:]
    if not (date.isdigit() and clock.isdigit() and len(clock) >= 6):
        return ""
    return (
        f"{date[0:4]}-{date[4:6]}-{date[6:8]}T"
        f"{clock[0:2]}:{clock[2:4]}:{clock[4:6]}Z"
    )


def _nightly_stamp_iso(folder_name):
    """``2026-08-31`` -> ``2026-08-31T00:00:00Z`` (morning snapshot)."""
    raw = (folder_name or "").strip()
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        return ""
    return f"{raw}T00:00:00Z"


def _open_sqlite_ro(path):
    """Read-only SQLite connection (never the live writer)."""
    uri = os.path.abspath(path).replace("\\", "/")
    return sqlite3.connect(f"file:{uri}?mode=ro", uri=True)


def _character_row_in_db(db_path, name):
    """Return ``(name, description, room_key, stats)`` or None."""
    try:
        con = _open_sqlite_ro(db_path)
        try:
            row = con.execute(
                "SELECT name, description, room_key, stats "
                "FROM characters WHERE name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return row


def _item_rows_in_db(db_path, name):
    """Held inventory/gear rows for ``name`` from a backup DB."""
    try:
        con = _open_sqlite_ro(db_path)
        try:
            rows = con.execute(
                "SELECT key, description, holder_type, holder_key, container "
                "FROM items WHERE holder_key = ? COLLATE NOCASE "
                "AND holder_type IN ('character', 'gear')",
                (name,),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return list(rows or [])


def _payload_from_db_rows(char_row, item_rows, *, when, source):
    """Build a checkpoint-shaped payload from a backup SQLite row set."""
    from engine.player_save_backup import build_checkpoint_payload

    payload = build_checkpoint_payload(char_row, item_rows)
    payload["kind"] = "hakai-restore"
    payload["time"] = when or payload.get("time") or _utc_now()
    payload["source"] = source
    return payload


def iter_backup_db_hits(character_name, *, root=None):
    """Newest-first backup DBs that still contain ``character_name``.

    Yields ``(iso_time, db_path, label)``. Does not load items until
    the caller picks a winner.
    """
    backups = backups_root(root or _repo_root())
    if not os.path.isdir(backups):
        return

    pre = os.path.join(backups, "pre-deploy")
    if os.path.isdir(pre):
        folders = [
            name for name in os.listdir(pre)
            if os.path.isdir(os.path.join(pre, name))
        ]
        folders.sort(reverse=True)
        for folder in folders:
            db_path = os.path.join(pre, folder, "riftforge.db")
            if not os.path.isfile(db_path):
                continue
            row = _character_row_in_db(db_path, character_name)
            if row is None:
                continue
            when = _predeploy_stamp_iso(folder) or _utc_now()
            yield when, db_path, f"pre-deploy/{folder}", row

    nights = []
    try:
        names = os.listdir(backups)
    except OSError:
        names = []
    for name in names:
        if _nightly_stamp_iso(name) == "":
            continue
        db_path = os.path.join(backups, name, "riftforge.db")
        if not os.path.isfile(db_path):
            continue
        nights.append(name)
    nights.sort(reverse=True)
    for folder in nights:
        db_path = os.path.join(backups, folder, "riftforge.db")
        row = _character_row_in_db(db_path, character_name)
        if row is None:
            continue
        when = _nightly_stamp_iso(folder)
        yield when, db_path, f"nightly/{folder}", row


def _payload_from_checkpoint_file(path, source):
    payload = _load_payload_file(path)
    if payload is None:
        return None
    payload["source"] = source
    return payload


def resolve_unhakai_payload(character_name, *, root=None, pick=None):
    """Pick the newest restore snapshot for ``character_name``.

    Preference:
      1. Newest hakai-archive file (true undo of a wipe this ship archived)
      2. Else the newest of (player checkpoint, backup DB that still has them)

    Returns ``(payload, label)`` or ``(None, reason)``.
    """
    name = (character_name or "").strip()
    if not name:
        return None, "No character name."
    root = root or _repo_root()

    archives = list_hakai_archives(name, root=root)
    if archives:
        if pick:
            for row in archives:
                if pick in row["file"] or pick == row["file"]:
                    payload = _payload_from_checkpoint_file(
                        row["path"], "hakai-archive",
                    )
                    if payload is None:
                        return None, f"Could not read archive {row['file']}."
                    return payload, f"hakai-archive {row['file']}"
        payload = _payload_from_checkpoint_file(
            archives[0]["path"], "hakai-archive",
        )
        if payload is None:
            return None, "Hakai archive on file but unreadable."
        return payload, f"hakai-archive {archives[0]['file']}"

    candidates = []

    from engine.player_save_backup import list_checkpoint_summaries

    for row in list_checkpoint_summaries(name, root=root):
        candidates.append(
            (
                _parse_iso(row.get("time")),
                "checkpoint",
                row["path"],
                f"player-checkpoint {row['file']}",
                None,
            )
        )

    for when, db_path, label, char_row in iter_backup_db_hits(name, root=root):
        candidates.append(
            (
                _parse_iso(when),
                "backup-db",
                db_path,
                label,
                char_row,
            )
        )

    if not candidates:
        return None, (
            f"No hakai archive, player checkpoint, or backup DB copy "
            f"of {name!r}."
        )

    candidates.sort(key=lambda row: row[0] or "", reverse=True)
    when, kind, path, label, char_row = candidates[0]
    if kind == "checkpoint":
        payload = _payload_from_checkpoint_file(path, "player-checkpoint")
        if payload is None:
            return None, f"Could not read checkpoint ({label})."
        return payload, label

    item_rows = _item_rows_in_db(path, name)
    payload = _payload_from_db_rows(
        char_row, item_rows, when=when, source="backup-db",
    )
    # Keep the storage key the live world used (row name), not the query.
    payload["character"] = char_row[0]
    return payload, label


def recreate_character_from_payload(game, payload):
    """Hydrate a missing body from a checkpoint-shaped payload.

    The storage key must not already be live (exact key). Returns
    ``(character_or_none, message)``.
    """
    if game is None or not isinstance(payload, dict):
        return None, "Nothing to restore."
    name = (payload.get("character") or "").strip()
    if not name:
        return None, "Snapshot has no character key."
    existing = live_body_by_storage_key(game, name)
    if existing is not None:
        return None, (
            f"{name} is already in the world. Unhakai only brings back "
            f"a wiped body — use gm restore checkpoint {name} to roll "
            f"an existing Echo back."
        )

    from engine.hooks import apply_character_blob
    from engine.persistence import (
        _resolve_saved_room,
        persist_save_character,
        resolve_pending_body_link,
    )
    from engine.player_save_backup import _place_checkpoint_items
    from engine.world import Character

    stats = payload.get("stats") or {}
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, "Snapshot stats blob is invalid."
    if not isinstance(stats, dict):
        return None, "Snapshot stats blob is invalid."

    char = Character(name, payload.get("description") or "")
    pending_link = apply_character_blob(char, stats)
    _place_checkpoint_items(char, payload.get("items") or [], game)

    room_key = payload.get("room_key") or ""
    room = _resolve_saved_room(game, room_key, name)
    char.move_to(room)
    char.session = None

    if pending_link is not None:
        body_room_key, body_key = pending_link
        resolve_pending_body_link(game, char, body_room_key, body_key)

    conn = getattr(game, "db", None)
    if conn is None:
        return None, "No database connection."

    claimed = (getattr(char, "account", None) or "").strip()
    if claimed and not getattr(char, "is_npc", False):
        from engine import accounts as accounts_mod
        from engine.persistence import flush_accounts_now

        acct = accounts_mod.find_account(game, claimed)
        if acct is not None:
            accounts_mod.link_character(game, acct, char)
            flush_accounts_now(conn, game, reason="unhakai")

    ok, msg = persist_save_character(
        conn, game, char, player_checkpoint=False,
    )
    if not ok:
        return None, msg
    return char, "ok"


def decode_archive_dir_name(folder):
    """Reverse ``quote()`` folder names for staff listing."""
    try:
        return unquote(folder or "")
    except Exception:
        return folder or ""
