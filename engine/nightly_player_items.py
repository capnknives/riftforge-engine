"""nightly_player_items.py -- per-player gear JSON from nightly backup DB.

After ``world_backup.run_backup()`` copies ``riftforge.db`` into
``backups/YYYY-MM-DD/``, this module reads that **read-only** copy and
writes ``backups/YYYY-MM-DD/player-items/<Character>.json`` payloads
compatible with ``restore_player_checkpoint_items`` (items + room + name;
no stats blob).

Fail-soft: extract errors never fail the nightly backup itself.
"""

from __future__ import annotations

import json
import os
import sqlite3
from urllib.parse import unquote

from engine.player_save_backup import (
    CHECKPOINT_VERSION,
    _checkpoint_dir_name,
    _item_row_dict,
    checkpoints_root,
)
from engine.world_backup import backups_root

NIGHTLY_ITEMS_SUBDIR = "player-items"


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def nightly_items_dir(backup_date, *, root=None):
    """``backups/<date>/player-items/`` absolute path."""
    return os.path.join(backups_root(root or _repo_root()), backup_date, NIGHTLY_ITEMS_SUBDIR)


def _nightly_items_filename(character_name):
    """One JSON file per character under the dated player-items folder."""
    return f"{_checkpoint_dir_name(character_name)}.json"


def _open_sqlite_ro(db_path):
    uri = os.path.abspath(db_path).replace("\\", "/")
    return sqlite3.connect(f"file:{uri}?mode=ro", uri=True, timeout=30)


def _parse_stats_blob(blob_text):
    if not blob_text:
        return {}
    try:
        data = json.loads(blob_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _known_player_checkpoint_names(root):
    """Character names that have at least one manual ``save`` checkpoint."""
    names = set()
    cp_root = checkpoints_root(root)
    if not os.path.isdir(cp_root):
        return names
    for dirname in os.listdir(cp_root):
        path = os.path.join(cp_root, dirname)
        if not os.path.isdir(path):
            continue
        try:
            names.add(unquote(dirname))
        except Exception:
            names.add(dirname)
    return names


def is_player_extract_candidate(name, stats_dict, *, checkpoint_names=None):
    """True when a backup DB row looks like a player body worth extracting."""
    key_low = (name or "").lower()
    if not key_low:
        return False
    if key_low.startswith(("gmspirit:", "husk:", "riftcrash:")):
        return False
    if stats_dict.get("is_npc"):
        return False
    if stats_dict.get("is_guest"):
        return False
    if stats_dict.get("gm_mode"):
        return False
    if stats_dict.get("transient_soul"):
        return False
    if stats_dict.get("pit_merchant") or stats_dict.get("pit_run_tag"):
        return False
    if stats_dict.get("tutorial_mentor_for"):
        return False
    if stats_dict.get("character_kind") == "riftcrash_avatar":
        return False
    account = (stats_dict.get("account") or "").strip()
    if account:
        return True
    if stats_dict.get("immersion") or stats_dict.get("essential"):
        return False
    if checkpoint_names and name in checkpoint_names:
        return True
    return False


def build_nightly_items_payload(
    char_row,
    item_rows,
    *,
    backup_date,
    at_utc=None,
):
    """Checkpoint-shaped JSON without a stats blob (items-only restore safe)."""
    name, description, room_key, _blob = char_row
    return {
        "version": CHECKPOINT_VERSION,
        "kind": "nightly-items",
        "time": at_utc or f"{backup_date}T00:00:00Z",
        "backup_date": backup_date,
        "character": name,
        "description": description,
        "room_key": room_key,
        "items": [_item_row_dict(row) for row in (item_rows or ())],
    }


def _item_rows_for_character(con, name):
    return list(
        con.execute(
            "SELECT key, description, holder_type, holder_key, container "
            "FROM items WHERE holder_key = ? COLLATE NOCASE "
            "AND holder_type IN ('character', 'gear')",
            (name,),
        ).fetchall()
        or ()
    )


def extract_nightly_player_items(
    backup_db_path,
    backup_date,
    *,
    root=None,
    at_utc=None,
):
    """Write ``player-items/*.json`` from a nightly backup DB copy.

    Returns a summary dict ``{written, skipped, errors}``. Never raises.
    """
    root = root or _repo_root()
    summary = {"written": 0, "skipped": 0, "errors": []}
    if not backup_date or not os.path.isfile(backup_db_path):
        summary["errors"].append("backup db missing")
        return summary

    dest_dir = nightly_items_dir(backup_date, root=root)
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except OSError as exc:
        summary["errors"].append(f"mkdir failed: {exc!r}")
        return summary

    checkpoint_names = _known_player_checkpoint_names(root)

    try:
        con = _open_sqlite_ro(backup_db_path)
    except sqlite3.Error as exc:
        summary["errors"].append(f"open db: {exc!r}")
        return summary

    try:
        char_rows = list(
            con.execute(
                "SELECT name, description, room_key, stats FROM characters"
            ).fetchall()
            or ()
        )
        for name, description, room_key, blob in char_rows:
            stats = _parse_stats_blob(blob)
            if not is_player_extract_candidate(
                name, stats, checkpoint_names=checkpoint_names,
            ):
                summary["skipped"] += 1
                continue
            item_rows = _item_rows_for_character(con, name)
            if not item_rows:
                summary["skipped"] += 1
                continue
            payload = build_nightly_items_payload(
                (name, description, room_key, blob),
                item_rows,
                backup_date=backup_date,
                at_utc=at_utc,
            )
            path = os.path.join(dest_dir, _nightly_items_filename(name))
            try:
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2, sort_keys=True)
                    handle.write("\n")
            except OSError as exc:
                summary["errors"].append(f"write {name!r}: {exc!r}")
                continue
            summary["written"] += 1
    except sqlite3.Error as exc:
        summary["errors"].append(f"query failed: {exc!r}")
    finally:
        con.close()

    if summary["written"]:
        rel = os.path.relpath(dest_dir, root).replace("\\", "/")
        print(
            f"[backup] nightly player-items: {summary['written']} file(s) -> {rel}",
            flush=True,
        )
    if summary["errors"]:
        print(
            f"[backup] nightly player-items errors: {summary['errors']}",
            flush=True,
        )
    return summary
