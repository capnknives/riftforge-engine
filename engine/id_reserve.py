"""id_reserve.py -- Persist lasting CNUM / VNUM / INUM tags so they are never recycled.

Ephemeral Cadence citizens, civic desk replacements, nest trash, and
wilderness hostiles mint numbers while they live and **drop them when they
die**. Lasting identities (passworded PCs, immersion cast, essential NPCs,
authored peaceful roster townsfolk, relics/unique items, hand rooms) stay
in ``id_reserved`` even after the body is gone, so a restore can reclaim
Andy / AD00002 instead of finding a new clerk on that tag.

Engine-pure: no ``supers`` imports. Policy uses Character/Item/Room fields
set by the game (``immersion``, ``civic_worker``, ``peaceful``, …).
"""

from __future__ import annotations

KIND_CNUM = "cnum"
KIND_INUM = "inum"
KIND_VNUM = "vnum"
_KINDS = (KIND_CNUM, KIND_INUM, KIND_VNUM)

# kind -> set of normalized tags. Process RAM; SQLite is the durable copy.
_RESERVED: dict[str, set[str]] = {
    KIND_CNUM: set(),
    KIND_INUM: set(),
    KIND_VNUM: set(),
}

# Read-only fallback so reserved_pool never returns None.
_EMPTY_POOL: frozenset[str] = frozenset()

# Remembered Game.db (connect). Not used for writes -- see _persist_one.
_PERSIST_CONN = None


def set_persist_conn(conn):
    """Remember the live SQLite connection for immediate reserve writes."""
    global _PERSIST_CONN
    _PERSIST_CONN = conn


def reset_for_tests():
    """Drop RAM reserved sets (smokes). Does not touch SQLite."""
    for kind in _KINDS:
        _RESERVED[kind] = set()
    global _PERSIST_CONN
    _PERSIST_CONN = None


def reserved_tags(kind: str) -> set[str]:
    """Copy of lasting tags for ``kind`` (cnum / inum / vnum)."""
    key = str(kind or "").strip().lower()
    pool = _RESERVED.get(key)
    if pool is None:
        return set()
    return set(pool)


def reserved_pool(kind: str):
    """Read-only live set for allocation hot paths. Do not mutate."""
    key = str(kind or "").strip().lower()
    return _RESERVED.get(key) or _EMPTY_POOL


def _normalize_tag(kind: str, tag) -> str | None:
    """Validate and uppercase a tag, or None if malformed."""
    if tag is None or str(tag).strip() == "":
        return None
    kind = str(kind or "").strip().lower()
    try:
        if kind == KIND_CNUM:
            from engine.char_cnum import validate_cnum

            return validate_cnum(tag)
        if kind == KIND_INUM:
            from engine.item_inum import validate_inum

            return validate_inum(tag)
        if kind == KIND_VNUM:
            from engine.room_vnum import validate_vnum

            return validate_vnum(tag)
    except ValueError:
        return None
    return None


def note_reserved(kind: str, tag, *, persist=True) -> str | None:
    """Remember a lasting tag. Idempotent. Returns the normalized tag."""
    code = _normalize_tag(kind, tag)
    if not code:
        return None
    kind = str(kind or "").strip().lower()
    pool = _RESERVED.get(kind)
    if pool is None:
        return None
    if code in pool:
        return code
    pool.add(code)
    # SQLite writes go through save_to_conn / flush_to_conn inside an
    # already-open persist apply. Never COMMIT here -- a loop-thread
    # commit on the writer connection mid-apply would freeze a partial
    # DELETE FROM characters (roster-wipe class).
    if persist:
        _persist_one(kind, code)
    return code


def character_should_reserve_cnum(char) -> bool:
    """True when this body's CNUM must survive despawn / wipe / restore.

    Passworded PCs (Andy even with an empty account field), immersion
    cast, essential NPCs, and authored peaceful townsfolk. Civic desk
    replacements (``civic_worker``), hostiles, guests, pit trash, and
    dream clones recycle — they live and die forever.
    """
    if char is None:
        return False
    from engine.char_identity import (
        is_account_linked_body,
        is_passworded_roster_body,
    )

    if is_account_linked_body(char) or is_passworded_roster_body(char):
        return True
    if getattr(char, "immersion", False) or getattr(char, "essential", False):
        return True
    if not getattr(char, "is_npc", False):
        return False
    if getattr(char, "civic_worker", False):
        return False
    if getattr(char, "pit_merchant", False) or getattr(char, "pit_run_tag", None):
        return False
    if getattr(char, "is_guest", False) or getattr(char, "transient_soul", False):
        return False
    if getattr(char, "is_sleep_dream_clone", False):
        return False
    if getattr(char, "ephemeral_spawn_body", False):
        return False
    if getattr(char, "origin_loop_ephemeral", False):
        return False
    if getattr(char, "plot_illusion", False):
        return False
    if getattr(char, "follower_id", None):
        return False
    # Lethal nest / wilderness trash recycles. Peaceful authored roster
    # is finite and is the body staff restore by name.
    if not getattr(char, "peaceful", False):
        return False
    return True


def stats_blob_should_reserve_cnum(stats) -> bool:
    """Same policy as :func:`character_should_reserve_cnum` for a save blob."""
    from engine.char_identity import stats_blob_is_login_pfile

    if not isinstance(stats, dict):
        return False
    if stats_blob_is_login_pfile(stats):
        return True
    if stats.get("immersion") or stats.get("essential"):
        return True
    if stats.get("civic_worker"):
        return False
    if stats.get("pit_merchant") or stats.get("pit_run_tag"):
        return False
    if stats.get("is_guest") or stats.get("transient_soul"):
        return False
    if stats.get("is_sleep_dream_clone"):
        return False
    if stats.get("ephemeral_spawn_body"):
        return False
    if stats.get("origin_loop_ephemeral"):
        return False
    if stats.get("plot_illusion"):
        return False
    if stats.get("follower_id"):
        return False
    if not stats.get("is_npc"):
        return False
    if not stats.get("peaceful"):
        return False
    return True


def item_should_reserve_inum(item, holder=None) -> bool:
    """True for relics / unique copies. Sandwiches and floor trash recycle.

    Restore already remints live occupants that clash with archived INUMs
    (``remint_inum_occupants_for_items``). Reserving every PC sandwich
    would freeze the 5-digit space as inventory churns. ``holder`` is
    accepted so call sites stay stable; it does not widen the policy.
    """
    if item is None:
        return False
    if getattr(item, "is_body", False):
        return False
    if getattr(item, "relic", False) or getattr(item, "unique", False):
        return True
    return False


def room_should_reserve_vnum(room) -> bool:
    """Hand rooms that keep a persistent VNUM identity. Pit/grid skip."""
    from engine.room_vnum import hand_room_wants_vnum

    return bool(room is not None and hand_room_wants_vnum(room))


def note_character_cnum(char, *, persist=True) -> str | None:
    """Reserve ``char.cnum`` when the body is a lasting identity."""
    if not character_should_reserve_cnum(char):
        return None
    return note_reserved(KIND_CNUM, getattr(char, "cnum", None), persist=persist)


def note_item_inum(item, holder=None, *, persist=True) -> str | None:
    """Reserve ``item.inum`` when the copy is lasting gear."""
    if not item_should_reserve_inum(item, holder):
        return None
    return note_reserved(KIND_INUM, getattr(item, "inum", None), persist=persist)


def note_room_vnum(room, *, persist=True) -> str | None:
    """Reserve a hand-room VNUM."""
    if not room_should_reserve_vnum(room):
        return None
    raw = getattr(room, "vnum", None) or getattr(room, "key", None)
    return note_reserved(KIND_VNUM, raw, persist=persist)


def _persist_one(kind, tag):
    """Intentionally a no-op.

    Immediate INSERT+COMMIT on ``_PERSIST_CONN`` is unsafe: the persist
    writer uses ``check_same_thread=False``, migration 25 used to point
    ``_PERSIST_CONN`` at that writer connection, and a loop-thread
    ``commit()`` mid-``DELETE FROM characters`` would freeze a partial
    roster. Durable writes happen in :func:`save_to_conn` inside the
    apply transaction, or :func:`flush_to_conn` on boot (owns its commit).
    """
    return


def load_from_conn(conn):
    """Replace RAM reserved sets from SQLite (boot / connect)."""
    for kind in _KINDS:
        _RESERVED[kind] = set()
    set_persist_conn(conn)
    if conn is None:
        return
    try:
        rows = conn.execute("SELECT kind, tag FROM id_reserved").fetchall()
    except Exception:
        return
    for kind, tag in rows:
        code = _normalize_tag(kind, tag)
        if not code:
            continue
        pool = _RESERVED.get(str(kind or "").strip().lower())
        if pool is not None:
            pool.add(code)


def save_to_conn(conn):
    """Upsert the RAM reserved sets (copyover / force-full must not wipe).

    Fail-soft: a missing table must never abort a world-save transaction
    (that would roll back the character INSERTs). Callers that own a
    transaction (persist apply) do not commit here.
    """
    if conn is None:
        return
    try:
        for kind in _KINDS:
            for tag in list(_RESERVED.get(kind) or ()):
                conn.execute(
                    "INSERT OR IGNORE INTO id_reserved (kind, tag) "
                    "VALUES (?, ?)",
                    (kind, tag),
                )
    except Exception:
        return


def flush_to_conn(conn):
    """Write RAM tags and commit only when this call opened the transaction.

    Boot / restore helpers use this. Persist apply must call
    :func:`save_to_conn` instead so the outer ``with conn`` / ``BEGIN``
    owns the commit.
    """
    if conn is None:
        return
    try:
        owned = not conn.in_transaction
    except Exception:
        owned = True
    try:
        save_to_conn(conn)
        if owned:
            try:
                if conn.in_transaction:
                    conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
    except Exception:
        return


def seed_from_character_rows(conn):
    """Scan live character CNUMs and held-item INUMs into the reserved table.

    Called once from migration 25. Civic workers / hostiles are skipped
    by :func:`stats_blob_should_reserve_cnum`. Only relic/unique INUMs
    are seeded -- inventory sandwiches recycle.
    """
    if conn is None:
        return
    import json

    try:
        rows = conn.execute(
            "SELECT name, cnum, stats FROM characters"
        ).fetchall()
    except Exception:
        rows = []
    for _name, cnum, blob in rows:
        stats = {}
        if isinstance(blob, str) and blob.strip():
            try:
                loaded = json.loads(blob)
            except (TypeError, ValueError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                stats = loaded
        if not stats_blob_should_reserve_cnum(stats):
            continue
        raw = cnum or stats.get("cnum")
        note_reserved(KIND_CNUM, raw, persist=False)
    try:
        item_rows = conn.execute(
            "SELECT container FROM items"
        ).fetchall()
    except Exception:
        item_rows = []
    for (container,) in item_rows:
        parsed = {}
        if isinstance(container, str) and container.strip():
            try:
                loaded = json.loads(container)
            except (TypeError, ValueError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                parsed = loaded
        if not (parsed.get("relic") or parsed.get("unique")):
            continue
        note_reserved(KIND_INUM, parsed.get("inum"), persist=False)
    save_to_conn(conn)
