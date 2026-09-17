"""pfile_restore.py -- recover roster PCs from checkpoints / backup DBs.

When a procedural origin hostile steals a player storage key and
overwrites the SQLite row, staff restore the **actual archived pfile**
(newest clean checkpoint or pre-deploy / nightly backup DB) — not a
hardcoded Origin/path stamp.

Shared by boot heal, ``gm restore pfile``, and ``tools/restore_roster_pfile.py``.
"""

from __future__ import annotations

import json
import os
import sqlite3

_ARCHIVE_WALK_COUNT = 0


def archive_walk_count():
    """How many times ``iter_restore_candidates`` actually walked disk."""
    return int(_ARCHIVE_WALK_COUNT)


def reset_restore_scan_stats():
    """Zero walk + SQLite-open counters (smokes)."""
    global _ARCHIVE_WALK_COUNT
    _ARCHIVE_WALK_COUNT = 0
    from engine.hakai_archive import reset_scan_caches

    reset_scan_caches()


def _parse_stats(blob_text):
    if not blob_text:
        return {}
    try:
        data = json.loads(blob_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _disc_count(stats):
    """How many discipline ranks a blob carries (dict or list)."""
    if not isinstance(stats, dict):
        return 0
    discs = stats.get("disciplines") or stats.get("known_disciplines") or {}
    if isinstance(discs, dict):
        return len(discs)
    if isinstance(discs, list):
        return len(discs)
    return 0


def stats_look_collapsed(live, archive):
    """True when *live* looks like a chargen kit vs a stronger archive copy.

    Bug reports 1505 / 1509: a skipped copyover force-full wipe dropped the
    SQLite row, login rebuilt a Hunter with 510 HP and two discs, and the
    NPC-only pfile heal never fired. Compare tier, disc count, and HP.
    """
    if not isinstance(live, dict) or not isinstance(archive, dict):
        return False
    live_acct = (live.get("account") or "").strip().lower()
    arch_acct = (archive.get("account") or "").strip().lower()
    if live_acct and arch_acct and live_acct != arch_acct:
        return False
    try:
        live_tier = int(live.get("tier") or 0)
    except (TypeError, ValueError):
        live_tier = 0
    try:
        arch_tier = int(archive.get("tier") or 0)
    except (TypeError, ValueError):
        arch_tier = 0
    try:
        live_hp = float(live.get("hp") or 0)
    except (TypeError, ValueError):
        live_hp = 0.0
    try:
        arch_hp = float(archive.get("hp") or 0)
    except (TypeError, ValueError):
        arch_hp = 0.0
    live_n = _disc_count(live)
    arch_n = _disc_count(archive)
    if arch_tier > live_tier:
        return True
    if arch_n >= 8 and live_n <= 3:
        return True
    if arch_hp >= 1500 and live_hp <= 600:
        return True
    return False


def is_roster_pfile_corrupt_stats(stats):
    """True when a passworded account PC blob looks like a hostile overwrite."""
    if not isinstance(stats, dict):
        return False
    account = (stats.get("account") or "").strip()
    if not account:
        return False
    if not (stats.get("password_hash") or "").strip():
        return False
    if stats.get("_origin_hostile"):
        return True
    if stats.get("is_npc"):
        return True
    return False


def is_roster_pfile_corrupt(character):
    """Live Character wrapper for :func:`is_roster_pfile_corrupt_stats`."""
    if character is None:
        return False
    if getattr(character, "immersion", False):
        return False
    if getattr(character, "tutorial_mentor_for", None):
        return False
    key_low = (getattr(character, "key", None) or "").lower()
    if key_low.startswith("husk:") or key_low.startswith("gmspirit:"):
        return False
    if getattr(character, "is_guest", False) or getattr(
        character, "transient_soul", False,
    ):
        return False
    if not (getattr(character, "password_hash", None) or "").strip():
        return False
    if not (getattr(character, "account", None) or "").strip():
        return False
    if getattr(character, "_origin_hostile", False):
        return True
    if getattr(character, "is_npc", False):
        return True
    return False


def _payload_stats(payload):
    stats = (payload or {}).get("stats") or {}
    if isinstance(stats, str):
        return _parse_stats(stats)
    return stats if isinstance(stats, dict) else {}


def _payload_is_clean(payload):
    return not is_roster_pfile_corrupt_stats(_payload_stats(payload))


def _checkpoint_payload(path, source):
    from engine.hakai_archive import _load_payload_file

    payload = _load_payload_file(path)
    if payload is None:
        return None
    payload["source"] = source
    return payload


def iter_restore_candidates(character_name, *, root=None, unlimited=False):
    """Yield ``(iso_time, label, payload)`` newest-first for one storage key.

    Backup-DB candidates are yielded **without** item rows. The caller
    hydrates ``_item_rows_in_db`` only for the winning payload (the
    ``iter_backup_db_hits`` docstring already promised this; the old
    caller loaded items for every candidate and full-scanned 52k rows).
    """
    global _ARCHIVE_WALK_COUNT
    from engine.hakai_archive import (
        _payload_from_db_rows,
        iter_backup_db_hits,
        list_hakai_archives,
    )
    from engine.player_save_backup import list_checkpoint_summaries

    name = (character_name or "").strip()
    if not name:
        return
    _ARCHIVE_WALK_COUNT += 1

    for row in list_hakai_archives(name, root=root):
        payload = _checkpoint_payload(row["path"], "hakai-archive")
        if payload is not None:
            yield _parse_iso(payload.get("time")), row["file"], payload

    for row in list_checkpoint_summaries(name, root=root):
        payload = _checkpoint_payload(row["path"], row.get("source_label") or "save")
        if payload is not None:
            yield _parse_iso(payload.get("time")), row.get("source_label") or row["file"], payload

    for when, db_path, label, char_row in iter_backup_db_hits(
        name, root=root, unlimited=unlimited,
    ):
        # Lazy items: empty list here, hydrate only if this payload wins.
        payload = _payload_from_db_rows(
            char_row, [], when=when, source="backup-db",
        )
        payload["_backup_db_path"] = db_path
        payload["_lazy_items"] = True
        yield _parse_iso(when), label, payload


def _parse_iso(text):
    from engine.hakai_archive import _parse_iso as _hakai_parse_iso

    return _hakai_parse_iso(text)


def _hydrate_backup_items(payload, character_name):
    """Fill ``items`` on a lazy backup-DB payload (winner only)."""
    if not isinstance(payload, dict) or not payload.get("_lazy_items"):
        return payload
    from engine.hakai_archive import _item_rows_in_db
    from engine.player_save_backup import _item_row_dict

    db_path = payload.get("_backup_db_path")
    payload["_lazy_items"] = False
    if not db_path or not os.path.isfile(db_path):
        payload["items"] = payload.get("items") or []
        return payload
    holder = (payload.get("character") or character_name or "").strip()
    item_rows = _item_rows_in_db(db_path, holder)
    payload["items"] = [_item_row_dict(row) for row in (item_rows or ())]
    return payload


def pick_clean_restore_payload(character_name, *, root=None, pick=None):
    """Return ``(payload, label)`` for the newest non-corrupt archive copy.

    Candidates from hakai, player ``save`` checkpoints, and backup DBs are
    merge-sorted by timestamp so a three-week-old checkpoint cannot beat a
    same-morning pre-deploy row (Daniel's 1455 heal picked 2026-08-17).
    Staff ``pick=`` searches every snapshot; boot heals cap to the newest
    N backups so deploy frequency cannot grow copyover time again.
    """
    name = (character_name or "").strip()
    if not name:
        return None, "No character name."
    pick_l = (pick or "").strip().lower()
    ranked = []
    for when, label, payload in iter_restore_candidates(
        name, root=root, unlimited=bool(pick_l),
    ):
        ranked.append((when or "", label, payload))
    ranked.sort(key=lambda row: row[0] or "", reverse=True)
    for _when, label, payload in ranked:
        if pick_l:
            if pick_l not in label.lower() and pick_l not in (label.split("/")[-1]).lower():
                continue
        if not _payload_is_clean(payload):
            continue
        if (payload.get("character") or "").strip().lower() != name.lower():
            continue
        _hydrate_backup_items(payload, name)
        return payload, label
    if pick_l:
        return None, f"No clean archive matching {pick!r} for {name!r}."
    return None, (
        f"No clean checkpoint or backup DB copy of {name!r} "
        f"(newest archives are still polluted)."
    )


def apply_restore_payload_to_character(target, payload, game, *, persist=True):
    """Roll a live body back to a checkpoint-shaped payload. Returns ``(ok, msg)``."""
    if game is None or target is None or not isinstance(payload, dict):
        return False, "Nothing to restore."
    name = getattr(target, "key", None) or ""
    if (payload.get("character") or "") != name:
        return False, (
            f"Snapshot is for {payload.get('character')!r}, not {name!r}."
        )
    if payload.get("kind") == "nightly-items" or "stats" not in payload:
        return False, "That copy is gear-only — use restore checkpoint items."

    # Reuse the player checkpoint applier by writing payload to a temp file
    # is overkill — inline the same steps as restore_player_checkpoint.
    from engine.hooks import apply_character_blob
    from engine.persistence import _resolve_saved_room, persist_save_character
    from engine.player_save_backup import (
        _clear_held_items,
        _place_checkpoint_items,
    )

    stats = _payload_stats(payload)
    if not stats:
        return False, "Snapshot stats blob is invalid."

    target.description = payload.get("description") or target.description
    apply_character_blob(target, stats)
    _clear_held_items(target)
    _place_checkpoint_items(target, payload.get("items") or [], game)
    try:
        from engine.item_inum import remint_inum_occupants_for_items

        remint_inum_occupants_for_items(game, target)
    except Exception as exc:
        print(f"[pfile_restore] inum clash heal skipped: {exc!r}", flush=True)

    room_key = payload.get("room_key") or ""
    room = _resolve_saved_room(game, room_key, name)
    if room is not None:
        target.move_to(room)

    from engine.char_identity import remint_cnum_clash_for_restore

    new_cnum, clash_notes = remint_cnum_clash_for_restore(
        getattr(game, "db", None),
        keep_name=name,
        keep_cnum=getattr(target, "cnum", None),
        keep_stats=stats,
        game=game,
    )
    if new_cnum:
        target.cnum = new_cnum

    persist_ok = True
    persist_msg = ""
    if persist:
        conn = getattr(game, "db", None)
        if conn is None:
            return False, "No database connection."
        persist_ok, persist_msg = persist_save_character(
            conn, game, target, player_checkpoint=False,
        )
        if not persist_ok:
            return False, persist_msg
    label = payload.get("source") or "archive"
    when = payload.get("time") or label
    print(
        f"[pfile_restore] restored {name!r} from {label} ({when})",
        flush=True,
    )
    detail = f"Restored {name} from {label} ({when})."
    if clash_notes:
        detail = f"{detail} CNUM heal: {'; '.join(clash_notes)}."
    return True, detail


def _live_stats_for_collapse(character):
    """Minimal live snapshot for :func:`stats_look_collapsed`."""
    discs = getattr(character, "disciplines", None)
    if not isinstance(discs, dict):
        discs = getattr(character, "known_disciplines", None) or {}
    return {
        "account": getattr(character, "account", None),
        "tier": getattr(character, "tier", 0),
        "hp": getattr(character, "hp", 0),
        "disciplines": discs if isinstance(discs, dict) else {},
    }


def roster_body_collapsed_vs_archive(character, *, root=None):
    """True when *character* is a chargen-shaped collapse vs a newer archive."""
    if character is None:
        return False
    if getattr(character, "immersion", False):
        return False
    if not (getattr(character, "account", None) or "").strip():
        return False
    name = getattr(character, "key", None) or ""
    payload, _label = pick_clean_restore_payload(name, root=root)
    if payload is None:
        return False
    return stats_look_collapsed(
        _live_stats_for_collapse(character), _payload_stats(payload),
    )


def rematerialize_missing_roster_body(game, name, *, account_name=None, root=None):
    """Rebuild a roster PC that vanished from ``game.characters``.

    Copyover force-full ``DELETE FROM characters`` plus a skipped login
    body left Andri's menu with a name and no Echo -- login then minted a
    fresh Hunter kit. Pull the newest clean archive into a new Echo.
    """
    from engine.char_identity import find_character_exact_key
    from engine.world import Character

    key = (name or "").strip()
    if not key or game is None:
        return None
    existing = find_character_exact_key(game, key)
    if existing is not None:
        restore_character_from_clean_archive(game, existing, root=root)
        return existing
    payload, label = pick_clean_restore_payload(key, root=root)
    if payload is None:
        print(
            f"[pfile_restore] cannot rematerialize {key!r}: {label}",
            flush=True,
        )
        return None
    char = Character(key, payload.get("description") or key)
    char.game = game
    from engine.char_index import register_character

    register_character(game, char)
    if account_name and not getattr(char, "account", None):
        char.account = account_name
    ok, msg = apply_restore_payload_to_character(
        char, payload, game, persist=getattr(game, "db", None) is not None,
    )
    if not ok:
        print(f"[pfile_restore] rematerialize failed for {key!r}: {msg}", flush=True)
        return None
    print(
        f"[pfile_restore] rematerialized {key!r} from {label}",
        flush=True,
    )
    return char


def live_looks_chargen_collapsed(character):
    """Cheap gate: T1 kit with almost no discs or tiny HP (Ayla wipe shape).

    Account-linked login bodies only. Town NPCs at T1 with a small disc
    kit used to pass this gate, then ``pick_clean_restore_payload`` scanned
    every backup DB per name on ``load_world`` -- copyover boot hung past
    the gateway crash window, crash recovery reverted 1509, then auto-resume
    redeployed it in a loop.

    This drifted wide again: every newbie and low-tier alt matched
    forever, which is how live copyover hit ~270s on 2026-09-11 (414
    bodies x 24 x 330MB backups). Boot must NOT use this gate to scan
    (``restore_character_from_clean_archive(..., check_collapse=False)``).
    Login still may. A body already checked this process is never
    rescanned.
    """
    if character is None:
        return False
    if getattr(character, "pfile_archive_checked", False):
        return False
    if getattr(character, "immersion", False):
        return False
    if not (getattr(character, "account", None) or "").strip():
        return False
    if not (getattr(character, "password_hash", None) or "").strip():
        return False
    try:
        tier = int(getattr(character, "tier", 0) or 0)
    except (TypeError, ValueError):
        tier = 0
    try:
        hp = float(getattr(character, "hp", 0) or 0)
    except (TypeError, ValueError):
        hp = 0.0
    n = _disc_count(_live_stats_for_collapse(character))
    return tier <= 1 and (n <= 3 or hp <= 600)


def _stamp_archive_checked(character):
    """Mark a clean body so later login/boot in this process skip the scan."""
    if character is None:
        return
    try:
        character.pfile_archive_checked = True
    except Exception:
        pass


def restore_character_from_clean_archive(
    game, target, *, root=None, pick=None, check_collapse=False,
):
    """Pick the newest clean archive and roll ``target`` back. Returns bool.

    ``check_collapse`` defaults to False: boot ``load_world`` must not
    treat every T1 kit as a wipe (2026-09-11 outage). Corrupt roster
    overwrites still restore. Login passes ``check_collapse=True`` so
    an Ayla-shaped remint still heals from the newest clean archive.
    """
    if target is None:
        return False
    corrupt = is_roster_pfile_corrupt(target)
    collapsed = bool(check_collapse) and live_looks_chargen_collapsed(target)
    if not corrupt and not collapsed:
        return False
    name = getattr(target, "key", None) or ""
    payload, label = pick_clean_restore_payload(name, root=root, pick=pick)
    if payload is None:
        if corrupt:
            print(
                f"[pfile_restore] no clean archive for {name!r}: {label}",
                flush=True,
            )
        else:
            _stamp_archive_checked(target)
        return False
    if not corrupt and not stats_look_collapsed(
        _live_stats_for_collapse(target), _payload_stats(payload),
    ):
        _stamp_archive_checked(target)
        return False
    ok, msg = apply_restore_payload_to_character(
        target, payload, game,
        persist=getattr(game, "db", None) is not None,
    )
    if not ok:
        print(f"[pfile_restore] restore failed for {name!r}: {msg}", flush=True)
        return False
    from engine import accounts as accounts_mod

    accounts_mod.heal_account_linked_playable_flags(
        target, account_name=getattr(target, "account", None),
    )
    return True


def heal_all_roster_pfile_overwrites(game, *, root=None, check_collapse=True):
    """Roster sweep: corrupt (and optionally collapsed) PCs with a clean archive.

    Boot no longer calls this — ``normalize_character_after_load`` already
    ran the per-body restore, and a second full-roster walk was the other
    half of the 2026-09-11 ~270s copyover. Kept for ``gm restore pfile``
    tooling and ``tools/restore_roster_pfile.py``.
    """
    if game is None:
        return 0
    healed = 0
    root = root or getattr(game, "report_dir", None)
    for char in list(getattr(game, "characters", None) or []):
        if restore_character_from_clean_archive(
            game, char, root=root, check_collapse=check_collapse,
        ):
            healed += 1
    return healed


def sqlite_restore_character_from_backup(
    live_db_path,
    backup_db_path,
    character_name,
    *,
    dry_run=False,
):
    """Surgical SQLite restore: character row + held items from a backup DB.

    Inserts a missing name (wiped roster PC). CNUM UNIQUE clashes remint
    the NPC occupant so the restored PC keeps the archived tag. Use when
    the game is down or before a game-only restart. Returns ``(ok, detail)``.
    """
    from engine.hakai_archive import _character_row_in_db, _item_rows_in_db

    name = (character_name or "").strip()
    if not name:
        return False, "No character name."
    if not os.path.isfile(live_db_path):
        return False, f"Live DB missing: {live_db_path}"
    if not os.path.isfile(backup_db_path):
        return False, f"Backup DB missing: {backup_db_path}"

    char_row = _character_row_in_db(backup_db_path, name)
    if char_row is None:
        return False, f"{name!r} not in backup DB."
    stats = _parse_stats(char_row[3])
    if is_roster_pfile_corrupt_stats(stats):
        return False, "Backup copy is still polluted — pick an older snapshot."

    item_rows = _item_rows_in_db(backup_db_path, name)
    if dry_run:
        return True, (
            f"would restore {name!r} from {backup_db_path} "
            f"(origin={stats.get('origin')}/{stats.get('path')}, "
            f"items={len(item_rows)})"
        )

    live = sqlite3.connect(live_db_path)
    clash_notes = []
    try:
        live.execute("BEGIN IMMEDIATE")
        existing = live.execute(
            "SELECT 1 FROM characters WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        cnum_val = None
        raw_cnum = stats.get("cnum") if isinstance(stats, dict) else None
        if isinstance(raw_cnum, str) and raw_cnum.strip():
            try:
                from engine.char_cnum import validate_cnum
                cnum_val = validate_cnum(raw_cnum)
            except ValueError:
                cnum_val = None
        live_cols = {
            row[1] for row in live.execute("PRAGMA table_info(characters)")
        }
        clash_notes = []
        if "cnum" in live_cols and cnum_val:
            from engine.char_identity import (
                remint_cnum_clash_for_restore,
                rewrite_stats_blob_cnum,
            )

            cnum_val, clash_notes = remint_cnum_clash_for_restore(
                live,
                keep_name=name,
                keep_cnum=cnum_val,
                keep_stats=stats,
            )
            stats_blob = char_row[3]
            if cnum_val:
                stats_blob = rewrite_stats_blob_cnum(stats_blob, cnum_val)
        else:
            stats_blob = char_row[3]
        if not existing:
            if "cnum" in live_cols:
                live.execute(
                    "INSERT INTO characters (name, description, room_key, "
                    "stats, cnum) VALUES (?, ?, ?, ?, ?)",
                    (name, char_row[1], char_row[2], stats_blob, cnum_val),
                )
            else:
                live.execute(
                    "INSERT INTO characters (name, description, room_key, "
                    "stats) VALUES (?, ?, ?, ?)",
                    (name, char_row[1], char_row[2], stats_blob),
                )
        elif "cnum" in live_cols:
            live.execute(
                "UPDATE characters SET description = ?, room_key = ?, "
                "stats = ?, cnum = ? WHERE name = ? COLLATE NOCASE",
                (char_row[1], char_row[2], stats_blob, cnum_val, name),
            )
        else:
            live.execute(
                "UPDATE characters SET description = ?, room_key = ?, stats = ? "
                "WHERE name = ? COLLATE NOCASE",
                (char_row[1], char_row[2], stats_blob, name),
            )
        live.execute(
            "DELETE FROM items WHERE holder_key = ? COLLATE NOCASE "
            "AND holder_type IN ('character', 'gear')",
            (name,),
        )
        item_cols = {
            row[1] for row in live.execute("PRAGMA table_info(items)")
        }
        for row in item_rows:
            if "holder_cnum" in item_cols:
                if len(row) >= 6:
                    live.execute(
                        "INSERT INTO items (key, description, holder_type, "
                        "holder_key, container, holder_cnum) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        tuple(row[:6]),
                    )
                else:
                    live.execute(
                        "INSERT INTO items (key, description, holder_type, "
                        "holder_key, container, holder_cnum) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        tuple(row[:5]) + (cnum_val,),
                    )
            else:
                live.execute(
                    "INSERT INTO items (key, description, holder_type, "
                    "holder_key, container) VALUES (?, ?, ?, ?, ?)",
                    tuple(row[:5]),
                )
        live.execute("COMMIT")
    except sqlite3.Error as exc:
        try:
            live.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return False, f"SQLite restore failed: {exc!r}"
    finally:
        live.close()

    clash_bit = ""
    if clash_notes:
        clash_bit = f", cnum heal: {'; '.join(clash_notes)}"
    detail = (
        f"restored {name!r} from {backup_db_path} "
        f"(origin={stats.get('origin')}/{stats.get('path')}, "
        f"items={len(item_rows)}{clash_bit})"
    )
    print(f"[pfile_restore] {detail}", flush=True)
    return True, detail
