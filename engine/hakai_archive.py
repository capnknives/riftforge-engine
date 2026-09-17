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
# Newest N pre-deploy + newest N nightly DBs the restore path will open.
# Live 2026-09-11: 24 x 330MB backups, opened per character, crossed the
# 300s crash window. Cap independently of disk retention so deploy
# frequency cannot grow boot time again.
DEFAULT_BACKUP_SCAN_KEEP = 5

# Per-boot memo: backup DBs are 330MB each. Opening them once per
# character (414 x 24 ~= 10k times) is what hung copyover. Reset via
# reset_scan_caches() between smokes.
_sqlite_open_count = 0
_BACKUP_INDEX = None  # (fingerprint, {name_lower: [(when, db_path, label, row)]})
_HAKAI_INDEX = None  # (fingerprint, {name_lower: [summary dicts]})


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


def sqlite_open_count():
    """How many read-only backup DB connections this process has opened."""
    return int(_sqlite_open_count)


def reset_scan_caches():
    """Drop per-boot backup / hakai indexes (smokes + after a new snapshot).

    The 2026-09-11 outage was O(characters x backup_DBs) SQLite opens.
    These caches exist so a second restore in the same boot is free.
    """
    global _BACKUP_INDEX, _HAKAI_INDEX, _sqlite_open_count
    _BACKUP_INDEX = None
    _HAKAI_INDEX = None
    _sqlite_open_count = 0
    try:
        from engine.player_save_backup import reset_checkpoint_scan_caches

        reset_checkpoint_scan_caches()
    except Exception:
        pass


def backup_scan_keep():
    """Newest backup snapshots the restore path will consider (default 5)."""
    raw = (os.environ.get("RIFTFORGE_PFILE_BACKUP_SCAN_KEEP") or "").strip()
    if not raw:
        return DEFAULT_BACKUP_SCAN_KEEP
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_BACKUP_SCAN_KEEP


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
    # New JSON would otherwise hide behind the per-boot hakai index.
    global _HAKAI_INDEX
    _HAKAI_INDEX = None
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


def _listing_fingerprint(path):
    """Cheap cache key: child names + mtime + size (no file reads)."""
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


def _hakai_index(root=None):
    """Per-boot map of storage-key -> hakai JSON paths (no json.load).

    ``list_hakai_archives`` used to ``os.listdir`` + ``json.load`` every
    archive for every character. 414 bodies x a handful of files was
    thousands of extra reads on top of the backup-DB storm.
    """
    global _HAKAI_INDEX
    root = root or _repo_root()
    base = archive_root(root)
    fp = _listing_fingerprint(base)
    cached = _HAKAI_INDEX
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
            name = decode_archive_dir_name(folder).strip().lower()
            if not name:
                continue
            entries = []
            try:
                files = os.listdir(char_dir)
            except OSError:
                files = []
            for filename in files:
                if not filename.endswith(".json"):
                    continue
                path = os.path.join(char_dir, filename)
                if not os.path.isfile(path):
                    continue
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    mtime = 0.0
                entries.append(
                    {
                        "path": path,
                        "file": filename,
                        # Real ISO time is on the payload; mtime is enough
                        # to order until the caller loads the winner.
                        "time": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime),
                        ),
                        "room_key": "",
                        "source": "hakai-archive",
                        "_mtime": mtime,
                    }
                )
            entries.sort(key=lambda row: row.get("_mtime") or 0, reverse=True)
            by_name[name] = entries
    _HAKAI_INDEX = (fp, by_name)
    return by_name


def list_hakai_archives(character_name, *, root=None):
    """Newest-first archive metadata for one storage key."""
    name = (character_name or "").strip().lower()
    if not name:
        return []
    entries = _hakai_index(root).get(name) or []
    return [dict(row) for row in entries]


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
    global _sqlite_open_count
    _sqlite_open_count += 1
    uri = os.path.abspath(path).replace("\\", "/")
    return sqlite3.connect(f"file:{uri}?mode=ro", uri=True)


def _select_row_exact_then_nocase(con, sql_exact, sql_nocase, name):
    """Exact lookup first so a BINARY index can be used; NOCASE only on miss.

    ``COLLATE NOCASE`` on ``items.holder_key`` made ``idx_items_holder_key``
    unusable (EXPLAIN QUERY PLAN: ``SCAN items``) — a 52k-row table scan
    per candidate on live. Character keys are already canonical; the
    fallback is only for odd mixed-case leftovers.
    """
    row = con.execute(sql_exact, (name,)).fetchone()
    if row is not None:
        return row
    return con.execute(sql_nocase, (name,)).fetchone()


def _character_row_in_db(db_path, name):
    """Return ``(name, description, room_key, stats)`` or None."""
    exact = (
        "SELECT name, description, room_key, stats "
        "FROM characters WHERE name = ?"
    )
    nocase = exact + " COLLATE NOCASE"
    try:
        con = _open_sqlite_ro(db_path)
        try:
            row = _select_row_exact_then_nocase(con, exact, nocase, name)
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return row


def _item_select_sql(con, *, nocase=False):
    """SELECT held inventory/gear rows; exact ``holder_key`` uses the index."""
    cols = {row[1] for row in con.execute("PRAGMA table_info(items)")}
    collate = " COLLATE NOCASE" if nocase else ""
    if "holder_cnum" in cols:
        return (
            "SELECT key, description, holder_type, holder_key, "
            "container, holder_cnum FROM items "
            f"WHERE holder_key = ?{collate} "
            "AND holder_type IN ('character', 'gear')"
        )
    return (
        "SELECT key, description, holder_type, holder_key, "
        "container FROM items "
        f"WHERE holder_key = ?{collate} "
        "AND holder_type IN ('character', 'gear')"
    )


def _item_rows_in_db(db_path, name):
    """Held inventory/gear rows for ``name`` from a backup DB."""
    try:
        con = _open_sqlite_ro(db_path)
        try:
            sql_exact = _item_select_sql(con, nocase=False)
            rows = con.execute(sql_exact, (name,)).fetchall()
            if not rows:
                sql_nocase = _item_select_sql(con, nocase=True)
                rows = con.execute(sql_nocase, (name,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return list(rows or [])


def explain_item_holder_lookup(db_path, name, *, nocase=False):
    """EXPLAIN QUERY PLAN text for the hot items lookup (smoke / ops)."""
    try:
        con = _open_sqlite_ro(db_path)
        try:
            sql = _item_select_sql(con, nocase=bool(nocase))
            plan = con.execute("EXPLAIN QUERY PLAN " + sql, (name,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return f"error: {exc!r}"
    return " | ".join(str(row) for row in plan)


def _payload_from_db_rows(char_row, item_rows, *, when, source):
    """Build a checkpoint-shaped payload from a backup SQLite row set."""
    from engine.player_save_backup import build_checkpoint_payload

    payload = build_checkpoint_payload(char_row, item_rows)
    payload["kind"] = "hakai-restore"
    payload["time"] = when or payload.get("time") or _utc_now()
    payload["source"] = source
    return payload


def _backup_db_listing(root, *, unlimited=False):
    """Newest-first ``(when, db_path, label)`` for pre-deploy + nightly DBs."""
    backups = backups_root(root or _repo_root())
    if not os.path.isdir(backups):
        return []
    cap = None if unlimited else backup_scan_keep()
    found = []

    pre = os.path.join(backups, "pre-deploy")
    if os.path.isdir(pre):
        folders = [
            name for name in os.listdir(pre)
            if os.path.isdir(os.path.join(pre, name))
        ]
        folders.sort(reverse=True)
        if cap is not None:
            folders = folders[:cap]
        for folder in folders:
            db_path = os.path.join(pre, folder, "riftforge.db")
            if not os.path.isfile(db_path):
                continue
            when = _predeploy_stamp_iso(folder) or _utc_now()
            found.append((when, db_path, f"pre-deploy/{folder}"))

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
    if cap is not None:
        nights = nights[:cap]
    for folder in nights:
        db_path = os.path.join(backups, folder, "riftforge.db")
        when = _nightly_stamp_iso(folder)
        found.append((when, db_path, f"nightly/{folder}"))
    return found


def _backup_listing_fingerprint(root, *, unlimited=False):
    rows = []
    for when, db_path, label in _backup_db_listing(root, unlimited=unlimited):
        try:
            st = os.stat(db_path)
            rows.append((label, int(st.st_mtime), int(st.st_size)))
        except OSError:
            rows.append((label, 0, 0))
    return (os.path.abspath(root or _repo_root()), unlimited, tuple(rows))


def _backup_roster_index(root=None, *, unlimited=False):
    """Open each capped backup DB once; map name -> character rows.

    Live 2026-09-11 opened every backup once per character (and again in
    the duplicate boot sweep). One pass per DB collecting the 414-row
    ``characters`` table is enough for the whole roster.
    """
    global _BACKUP_INDEX
    root = root or _repo_root()
    fp = _backup_listing_fingerprint(root, unlimited=unlimited)
    cached = _BACKUP_INDEX
    if cached is not None and cached[0] == fp:
        return cached[1]
    by_name = {}
    for when, db_path, label in _backup_db_listing(root, unlimited=unlimited):
        try:
            con = _open_sqlite_ro(db_path)
            try:
                rows = con.execute(
                    "SELECT name, description, room_key, stats FROM characters"
                ).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        for row in rows or ():
            name = (row[0] or "").strip().lower()
            if not name:
                continue
            by_name.setdefault(name, []).append((when, db_path, label, row))
    _BACKUP_INDEX = (fp, by_name)
    return by_name


def iter_backup_db_hits(character_name, *, root=None, unlimited=False):
    """Newest-first backup DBs that still contain ``character_name``.

    Yields ``(iso_time, db_path, label, char_row)``. Does not load items
    until the caller picks a winner. Consults the per-boot roster index
    so each backup DB is opened once per boot, not once per character.
    """
    name = (character_name or "").strip().lower()
    if not name:
        return
    index = _backup_roster_index(root, unlimited=unlimited)
    for when, db_path, label, row in index.get(name) or ():
        yield when, db_path, label, row


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

    for when, db_path, label, char_row in iter_backup_db_hits(
        name, root=root, unlimited=True,
    ):
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
    # Prefer a world-backup row over a stale player checkpoint from the
    # same second (bug report 1437 -- August ``save`` copies must not beat
    # a September pre-deploy DB that still has the body).
    if len(candidates) > 1:
        best_when = candidates[0][0] or ""
        same = [row for row in candidates if (row[0] or "") == best_when]
        backup = [row for row in same if row[1] == "backup-db"]
        if backup:
            candidates = backup + [
                row for row in candidates if row not in backup
            ]
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


def perform_unhakai(game, name):
    """Restore a missing body from hakai archive, checkpoint, or backup DB.

    Returns ``(character, source_label, error)``. On success *error* is
    None. Shared by ``gm unhakai`` and the Discord staff-ops inbox so a
    wiped login PC can be brought back without a second ``Game()`` on
    the live SQLite file (bug report 1437).
    """
    want = (name or "").strip()
    if not want:
        return None, None, "Unhakai whom? Usage: gm unhakai <name>"
    existing = live_body_by_storage_key(game, want)
    if existing is not None:
        key = getattr(existing, "key", None) or want
        return None, None, (
            f"{key} is already in the world. "
            f"Unhakai only brings back a wiped body. "
            f"To roll an Echo back to a save tank, type "
            f"gm restore checkpoint {key}."
        )
    root = getattr(game, "report_dir", None)
    payload, label = resolve_unhakai_payload(want, root=root)
    if payload is None:
        return None, None, label
    restored, msg = recreate_character_from_payload(game, payload)
    if restored is None:
        return None, label, f"Unhakai failed: {msg}"
    return restored, label, None


def decode_archive_dir_name(folder):
    """Reverse ``quote()`` folder names for staff listing."""
    try:
        return unquote(folder or "")
    except Exception:
        return folder or ""
