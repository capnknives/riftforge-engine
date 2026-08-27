"""
engine/persistence.py -- saving and loading the world with SQLite (two-repo
purity Phase 3: docs/plans/two_repo_purity.md).

This is the milestone-2 layer: characters (and the items they carry, and items
lying in rooms) survive a server restart. It uses Python's built-in sqlite3
module -- no external database, no dependencies, just a single .db file.

Design notes:

- engine/world.py stays free of storage concerns, exactly the way it stays free
  of networking. Rooms/Characters/Items don't know a database exists; this
  module reads their attributes and writes rows, and builds objects back
  from rows.
- We save a FULL SNAPSHOT on first boot save, then incremental dirty passes:
  characters come from game.characters (see engine/char_index.py); loose
  room items walk the indexed floor-item room set when available. Autosave
  is throttled in server.Game.on_tick (every AUTOSAVE_INTERVAL_SECONDS of
  wall-clock time). After the first successful save, only bodies queued via
  ``mark_character_dirty`` / ``mark_floor_room_dirty`` are serialized and
  written; quiet autosaves with an empty dirty queue skip the characters/items
  tables entirely. A full verify pass runs every
  ``RIFTFORGE_PERSIST_FULL_EVERY`` autosaves (default 30). Dirty queues can
  be capped per save (``RIFTFORGE_PERSIST_DIRTY_*_CAP``) so one busy minute
  cannot force a multi-second SQLite transaction; leftovers stay dirty.
  After ``load_world``, ``seed_snapshot_hashes_after_load`` (default on via
  ``RIFTFORGE_PERSIST_SEED_HASHES_ON_LOAD``) fingerprints the loaded roster
  so the first post-restart autosave can stay incremental instead of forcing
  a full verify while hash caches re-warm.
- Rooms themselves are NOT stored. The map is still built in code by
  build_world(); the database records which room each character/item is IN,
  keyed by the room's name. EXTENSION POINT: move the map itself into the DB.
- "Logout is not deletion" (systems doc section 4-E): a character who logs out
  stays in the world as an invulnerable Echo, so the characters table is the
  full roster -- online players AND echoes alike.

Two SUPERS-specific spots (Evil Strikes Back's moral-balance meter, and
re-deriving max HP after un-spiriting a character whose body was lost) go
through engine.hooks (`ensure_game_defaults`, `recompute_hp`) instead of a
direct `from supers import balance/stats` -- zero SUPERS imports here, same
as engine/world.py. Root persistence.py is now a thin re-export facade over
this module.
"""

import json                # stats will be stored as a JSON blob (milestone 3)
import os
import sqlite3
import time
import zlib

# Full wipe+rewrite verify cadence (was hard-coded 10 -- too aggressive once
# the world hit ~10k rooms / busy dirty storms). Override via env.
_PERSIST_FULL_EVERY_DEFAULT = 30
# Cap how many dirty bodies / floor rooms one incremental autosave rewrites.
# Excess keys stay in the dirty sets for the next pass.
_PERSIST_DIRTY_CHAR_CAP_DEFAULT = 40
_PERSIST_OFFLINE_DIRTY_SHARDS_DEFAULT = 8
# Offline Echo priority tier: recently active bodies drain before cold roster.
_PERSIST_DIRTY_RECENT_ACTIVE_S = 7 * 24 * 3600
_PERSIST_DIRTY_ROOM_CAP_DEFAULT = 80
# How many DELETE/INSERT batches between cooperative yields on apply/collect.
# Default 8 (was 1): collect still yields, but not after every single row.
_PERSIST_SAVE_YIELD_EVERY_DEFAULT = 8
# Yield collect when this many milliseconds of CPU elapse even if the
# row count has not hit ``persist_save_yield_every`` yet. Stops a
# force_full blob storm from pinning the loop for the whole 180s
# autosave interval.
_PERSIST_COLLECT_YIELD_MS_DEFAULT = 12.0
# How many changed characters/rooms share one SQLite commit during the
# incremental async apply (lag P11.3). Each commit is a separate fsync under
# the default DELETE journal mode; committing per-character (the old
# behavior) made routine autosave apply_ms cost 3.5-6s whenever ~100+
# characters were dirty (common right after a game-only restart, before
# per-character hash caches warm back up -- see `_collect_character_save_pass`
# "never hashed" bypass). Batching cuts fsync count by ~10-25x. Independent
# of `_PERSIST_SAVE_YIELD_EVERY_DEFAULT`, which only controls how often the
# asyncio loop yields, not how often SQLite commits.
_PERSIST_APPLY_COMMIT_BATCH_DEFAULT = 25
# Max milliseconds one autosave may spend in save_async before deferring
# optional meta slices to the next scheduled pass (lag 2026-08 live freezes).
_PERSIST_SAVE_WALL_BUDGET_MS_DEFAULT = 5000
# Lag P12: log a character's blob-build time when it alone crosses this
# floor, so a slow autosave collect names the actual culprit instead of
# only reporting an aggregate collect_ms. 20ms is well above the ~1-3ms a
# normal character blob costs -- see docs/plans/lag_p12_collect_profile.md.
_PERSIST_SLOW_CHAR_LOG_MS = 20.0
# Cap how many slow-character (name, ms) pairs one autosave keeps -- this
# is diagnostic breadcrumbs, not a full profile; a handful of names is
# enough to point a GM at the right character with ``gm cadence why``.
_PERSIST_SLOW_CHAR_LOG_CAP = 8


def persist_save_wall_budget_ms():
    """Wall-clock budget for one cooperative autosave (0 = disabled)."""
    raw = (os.environ.get("RIFTFORGE_PERSIST_SAVE_WALL_BUDGET_MS") or "").strip()
    if not raw:
        return _PERSIST_SAVE_WALL_BUDGET_MS_DEFAULT
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return _PERSIST_SAVE_WALL_BUDGET_MS_DEFAULT


def _persist_save_over_wall_budget(start_mono, budget_ms):
    """True when a cooperative save has exceeded its wall budget."""
    if budget_ms <= 0:
        return False
    import time as _time

    elapsed_ms = (_time.perf_counter() - float(start_mono)) * 1000.0
    return elapsed_ms >= float(budget_ms)


def persist_save_yield_every():
    """Cooperative yield interval for world save collect + apply (lag save-coop).

    ``RIFTFORGE_PERSIST_APPLY_YIELD_EVERY`` wins; ``RIFTFORGE_PERSIST_COLLECT_YIELD_EVERY``
    is an alias when apply is unset.
    """
    for name in (
        "RIFTFORGE_PERSIST_APPLY_YIELD_EVERY",
        "RIFTFORGE_PERSIST_COLLECT_YIELD_EVERY",
    ):
        raw = (os.environ.get(name) or "").strip()
        if raw:
            return _persist_env_int(name, _PERSIST_SAVE_YIELD_EVERY_DEFAULT)
    return _PERSIST_SAVE_YIELD_EVERY_DEFAULT


def persist_collect_yield_ms():
    """Max CPU ms between collect yields (0 = row-count only)."""
    raw = (os.environ.get("RIFTFORGE_PERSIST_COLLECT_YIELD_MS") or "").strip()
    if not raw:
        return _PERSIST_COLLECT_YIELD_MS_DEFAULT
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return _PERSIST_COLLECT_YIELD_MS_DEFAULT


def persist_apply_commit_batch():
    """Changed characters/rooms per SQLite commit in the incremental async
    apply (lag P11.3). ``RIFTFORGE_PERSIST_APPLY_COMMIT_BATCH`` overrides.
    """
    return _persist_env_int(
        "RIFTFORGE_PERSIST_APPLY_COMMIT_BATCH",
        _PERSIST_APPLY_COMMIT_BATCH_DEFAULT,
    )


def _persist_env_int(name, default):
    """Parse a positive int env override; fall back to *default* on junk."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return max(1, int(raw))
    except ValueError:
        return int(default)


def _persist_env_bool(name, default):
    """Parse a truthy env flag."""
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() not in ("0", "off", "false", "no", "")


def persist_background_writer_enabled():
    """True when autosave apply runs on the dedicated writer thread.

    Unset env stays False so ``Game()`` smokes do not start a writer
    thread. Production compose / live ``.env`` default the flag on
    (``RIFTFORGE_PERSIST_BACKGROUND_WRITER=1``).
    """
    return _persist_env_bool("RIFTFORGE_PERSIST_BACKGROUND_WRITER", False)


def persist_writer_drain_timeout_s():
    """Max seconds to wait for the writer queue to drain on shutdown."""
    return _persist_env_int("RIFTFORGE_PERSIST_WRITER_DRAIN_TIMEOUT_S", 120)


def persist_background_writer_ready():
    """True only once the writer thread actually started.

    The env flag alone (``persist_background_writer_enabled``) is not
    enough to route a save onto the writer: two live sqlite3 connections
    to the same on-disk file are only safe under WAL (rollback-journal
    "DELETE" mode serializes writers at the OS file-lock level and two
    threads contending for that lock is the exact "database disk image
    is malformed" corruption class this repo has hit before). Boot
    verifies WAL before calling ``start_persistence_writer`` -- if that
    check failed, ``_writer`` stays unset and callers must fall back to
    the synchronous single-connection save path instead of raising.
    """
    if not persist_background_writer_enabled():
        return False
    try:
        from engine.persistence_writer import writer_started
        return writer_started()
    except ImportError:
        return False


def persist_save_busy(game):
    """True while autosave is collecting or applying on the asyncio loop.

    P30 writer-thread apply is intentionally excluded -- Cadence and other
    tick handlers may run while SQLite fsync is off-loop. Save scheduling
    uses ``persist_writer_in_flight()`` separately so a follow-up collect
    still waits for the writer without freezing town life.
    """
    if game is None:
        return False
    if getattr(game, "_save_collecting", False):
        return True
    if getattr(game, "_autosave_running", False):
        return True
    return False


def _collect_idle_event(game):
    """Event that is set while world-save collect is idle."""
    import asyncio

    ev = getattr(game, "_persist_collect_idle_event", None)
    if ev is None:
        ev = asyncio.Event()
        ev.set()
        game._persist_collect_idle_event = ev
    return ev


def note_collect_started(game):
    """Clear the collect-idle Event before ``_save_collecting`` is set."""
    if game is None:
        return
    try:
        _collect_idle_event(game).clear()
    except Exception:
        pass


def note_collect_finished(game):
    """Set the collect-idle Event after ``_save_collecting`` is cleared."""
    if game is None:
        return
    try:
        _collect_idle_event(game).set()
    except Exception:
        pass


async def wait_collect_idle_async(game, *, timeout=None):
    """Park until on-loop collect finishes -- no ``sleep(0)`` spin."""
    if game is None or not persist_save_busy(game):
        return
    import asyncio

    ev = _collect_idle_event(game)
    if ev.is_set():
        return
    if timeout is None:
        await ev.wait()
        return
    try:
        await asyncio.wait_for(ev.wait(), timeout=float(timeout))
    except asyncio.TimeoutError:
        return


def persist_writer_in_flight():
    """True when the P30 writer thread still has queued or applying work.

    Distinct from ``persist_save_busy`` so schedulers can coalesce a
    follow-up save without launching a waiter task that busy-polls.
    """
    if not persist_background_writer_ready():
        return False
    try:
        from engine.persistence_writer import writer_busy
        return writer_busy()
    except ImportError:
        return False


async def wait_writer_idle_async(*, timeout=None):
    """Await writer drain without spinning the asyncio loop.

    ``asyncio.sleep(0)`` while ``writer_busy()`` is the P30 flag-on CPU
    peg: the loop keeps waking itself until SQLite apply finishes.
    """
    if not persist_background_writer_ready():
        return
    from engine.persistence_writer import wait_until_idle_async
    await wait_until_idle_async(timeout=timeout)


def persist_full_every():
    """Autosaves between full-verify wipe passes."""
    return _persist_env_int(
        "RIFTFORGE_PERSIST_FULL_EVERY", _PERSIST_FULL_EVERY_DEFAULT,
    )


def _persist_schedule_world_full_if_due(game):
    """Arm a full-verify world save for the *next* autosave (lag P23.3).

    Does not set ``_persist_force_full`` in the same ``save_async`` pass as
    SUPERS meta -- that used to force every meta slice to rewrite on count-30
    ticks.
    """
    if game is None:
        return
    count = int(getattr(game, "_persist_autosave_count", 0) or 0)
    every = persist_full_every()
    if every > 0 and count % every == 0:
        game._persist_world_full_scheduled = True


def _persist_apply_scheduled_world_full(game):
    """Consume a deferred world full-verify flag at snapshot collect time."""
    if game is None:
        return
    if getattr(game, "_persist_world_full_scheduled", False):
        game._persist_force_full = True
        game._persist_world_full_scheduled = False


def persist_dirty_char_cap(game=None):
    """Max dirty characters rewritten in one incremental save.

    Keep a hard per-pass cap so one autosave cannot serialize 100+ Echo
    bodies and freeze the asyncio loop for nearly a minute (live lag 2026-08).
    Backlog drains across successive autosaves instead.
    """
    return _persist_env_int(
        "RIFTFORGE_PERSIST_DIRTY_CHAR_CAP", _PERSIST_DIRTY_CHAR_CAP_DEFAULT,
    )


def persist_dirty_room_cap(game=None):
    """Max dirty floor rooms rewritten in one incremental save."""
    return _persist_env_int(
        "RIFTFORGE_PERSIST_DIRTY_ROOM_CAP", _PERSIST_DIRTY_ROOM_CAP_DEFAULT,
    )


def persist_offline_dirty_shards():
    """Spread offline lifestyle dirty marks across N autosave generations.

    With ~250+ offline bodies and a per-pass dirty cap of ~40, queuing every
    Echo every generation keeps ``n_deferred_chars`` pegged high and inflates
    collect. Each body only joins the dirty set when
    ``crc32(key) % shards == generation % shards`` (lag P28 follow-on).
    """
    return _persist_env_int(
        "RIFTFORGE_PERSIST_OFFLINE_DIRTY_SHARDS",
        _PERSIST_OFFLINE_DIRTY_SHARDS_DEFAULT,
    )


def _offline_dirty_shard_allows(char_key, generation, shards):
    if shards <= 1:
        return True
    slot = zlib.crc32(str(char_key).encode("utf-8")) & 0xFFFFFFFF
    return (slot % shards) == (int(generation) % shards)


def meta_persist_fingerprint(payload):
    """Stable adler32 of a JSON-serializable meta-table payload."""
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str,
    )
    return zlib.adler32(raw.encode("utf-8")) & 0xFFFFFFFF


def meta_persist_should_skip(game, tag, fingerprint):
    """True when *tag* was saved warm with the same fingerprint (lag P5)."""
    if game is None:
        return False
    if not getattr(game, f"_meta_persist_warm_{tag}", False):
        return False
    return getattr(game, f"_meta_persist_fp_{tag}", None) == fingerprint


def meta_persist_note_saved(game, tag, fingerprint):
    """Remember a successful meta-table wipe so quiet autosaves can skip."""
    if game is None:
        return
    setattr(game, f"_meta_persist_fp_{tag}", fingerprint)
    setattr(game, f"_meta_persist_warm_{tag}", True)


def meta_persist_note_loaded(game, tag, fingerprint):
    """After boot load, treat current state as already on disk."""
    meta_persist_note_saved(game, tag, fingerprint)


# Blob codec + the two SUPERS side-effects below come from engine.hooks
# (SUPERS registers its implementations at boot). No direct supers import
# here -- purity gate / docs/ENGINE_CONSUMER.md.
from engine.hooks import (
    apply_character_blob,
    character_to_blob,
    ensure_game_defaults,
    recompute_hp,
    upgrade_legacy_container,
)
# ``heal_force_unspirit`` is resolved lazily inside
# ``resolve_pending_body_link`` so a mid-copyover reload of this module
# does not ImportError when the in-memory hooks module is still one
# revision behind (see ``engine.copyover.reload_world_save_modules``).
from engine.world import Character, Item, Room, note_item_created_seq


def _boot_log_verbose():
    """Per-row boot diagnostics when ``RIFTFORGE_BOOT_VERBOSE=1``."""
    raw = (os.environ.get("RIFTFORGE_BOOT_VERBOSE") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _record_map_missing_stub(game, character_name, room_key):
    """Queue a map_missing_stub boot line; flush once after character load."""
    log = getattr(game, "_map_missing_stub_boot_log", None)
    if log is None:
        log = []
        game._map_missing_stub_boot_log = log
    log.append((character_name, room_key))
    if _boot_log_verbose():
        start_key = getattr(game.start_room, "key", None)
        print(
            f"[persistence] saved room {room_key!r} missing for "
            f"{character_name!r} -- keeping a stub so they are not dumped "
            f"to {start_key!r}. Update map JSON (or restore runtime maps) "
            f"and restart.",
            flush=True,
        )


def flush_map_missing_stub_boot_log(game):
    """Emit one summary line for map_missing_stub occupants still on stubs.

    Call after boot_seed critical heals (pit / slumber resume, stub heal) so
    transient procedural room keys do not spam the log every boot.
    """
    log = getattr(game, "_map_missing_stub_boot_log", None) or []
    if not log:
        return
    game._map_missing_stub_boot_log = []
    if _boot_log_verbose():
        return
    # Drop rows boot heal already moved off stubs (pit hub, generic stub heal).
    remaining = []
    for name, key in log:
        character = game.find_character(name) if game is not None else None
        if character is None:
            remaining.append((name, key))
            continue
        loc = getattr(character, "location", None)
        if loc is None or getattr(loc, "map_missing_stub", False):
            remaining.append((name, key))
    if not remaining:
        return
    log = remaining
    start_key = getattr(game.start_room, "key", None)
    n_chars = len(log)
    n_keys = len({rk for _name, rk in log})
    examples = []
    for name, key in log[:5]:
        if len(key) <= 72:
            examples.append(f"{name!r} @ {key!r}")
        else:
            examples.append(f"{name!r} @ {key[:69]!r}…")
    extra = f" (+{n_chars - 5} more)" if n_chars > 5 else ""
    print(
        f"[persistence] map_missing_stub: {n_chars} character(s) on "
        f"{n_keys} missing room key(s) -- keeping stubs so they are not "
        f"dumped to {start_key!r}. Examples: "
        f"{'; '.join(examples)}{extra}. Update map JSON (or restore "
        f"runtime maps) and restart. "
        f"(Set RIFTFORGE_BOOT_VERBOSE=1 for per-character lines.)",
        flush=True,
    )


def _resolve_saved_room(game, room_key, character_name):
    """Return the Room for a saved character ``room_key``.

    The map is rebuilt from JSON every boot -- rooms themselves are not in
    SQLite. When the saved key is missing (stale checkout, deferred map not
    restored, protect-skipped overlay, …) the old code dumped the character
    on ``game.start_room`` (Central Plaza). Plaza is ``no_loiter``, so the
    next tick spilled sessionless Echoes onto North Avenue -- which looked
    like "copyover teleported me to town" even though the DB still had the
    real ``room_key``.

    Fix: keep the saved key. Register a stub Room under that key so the
    character stays put until the real map content is on disk. Loud log so
    staff see the map lag.

    **Prevention:** persistable runtime rooms (vehicle interiors, charter
    cabins, …) must register a pre-load ensure in
    ``engine/runtime_rooms.py`` via ``supers/runtime_rooms.py`` so the
    real room exists before this runs. See CONTENT_AUTHORING.md.
    """
    if not room_key:
        print(
            f"[persistence] {character_name!r} has empty room_key -- "
            f"using start room {getattr(game.start_room, 'key', None)!r}",
            flush=True,
        )
        return game.start_room
    room = game.rooms.get(room_key)
    if room is not None:
        return room
    from engine import hooks
    demesne_room = hooks.demesne_resolve_room_key(game, room_key)
    if demesne_room is not None:
        return demesne_room
    from engine.systems.overland import resolve_virtual_overland_room_key

    wild_room = resolve_virtual_overland_room_key(game, room_key)
    if wild_room is not None:
        return wild_room
    from engine.systems.mine_rooms import resolve_mine_saved_room_key

    mine_room = resolve_mine_saved_room_key(game, room_key)
    if mine_room is not None:
        return mine_room
    _record_map_missing_stub(game, character_name, room_key)
    stub = Room(
        room_key,
        "The space you remember is thin here -- the map that held this "
        "place is not loaded. You have not moved; the world around you "
        "has not finished reforming. Staff: restore the missing map "
        f"file that defines {room_key!r}.",
    )
    # Mark so look / GM where can tell authored rooms from recovery stubs.
    stub.map_missing_stub = True
    stub.no_combat = True
    stub.wilderness = False
    game.rooms[room_key] = stub
    return stub


def _safe_relocation_room(game, character):
    """Pick an authored room for boot-heal relocation off a persistence stub."""
    from engine.room_vnum import lookup_room

    rooms = getattr(game, "rooms", None) or {}
    start = getattr(game, "start_room", None)

    def _usable(key):
        if not key:
            return None
        room = lookup_room(game, key)
        if room is None or getattr(room, "map_missing_stub", False):
            return None
        return room

    for key in (
        getattr(character, "home_room_key", None),
        getattr(character, "body_room_key", None),
    ):
        room = _usable(key)
        if room is not None:
            return room
    return start


def heal_map_missing_stub_occupants(game):
    """Boot heal: move bodies off persistence ``map_missing_stub`` rooms.

    ``_resolve_saved_room`` can register a thin stub when the saved
    ``room_key`` is absent from map JSON at load. Wilderness foot keys
    (``Wilderness (mx,my)/ux,uy``) are re-bound via ``place_on_overland``
    when possible before falling back to home / start relocation.
    """
    if game is None:
        return 0
    rooms = getattr(game, "rooms", None) or {}
    moved = 0
    emptied_stub_keys = []
    roster = getattr(game, "characters", None) or []
    if isinstance(roster, dict):
        roster = roster.values()
    from engine.systems.overland import parse_wilderness_room_key, place_on_overland

    from engine import hooks

    for character in list(roster):
        loc = getattr(character, "location", None)
        if loc is None or not getattr(loc, "map_missing_stub", False):
            continue
        if hooks.should_skip_map_missing_stub_heal(character):
            continue
        stub_key = getattr(loc, "key", None) or ""
        parsed = parse_wilderness_room_key(stub_key)
        if parsed is not None and place_on_overland(
            character, game, parsed[0], parsed[1],
        ):
            moved += 1
            if stub_key and stub_key not in emptied_stub_keys:
                emptied_stub_keys.append(stub_key)
            continue
        dest = _safe_relocation_room(game, character)
        if dest is None:
            continue
        try:
            character.move_to(dest)
        except Exception:
            character.location = dest
        moved += 1
        if stub_key and stub_key not in emptied_stub_keys:
            emptied_stub_keys.append(stub_key)
    for stub_key in emptied_stub_keys:
        room = rooms.get(stub_key)
        if room is None or not getattr(room, "map_missing_stub", False):
            continue
        if list(room.characters()):
            continue
        rooms.pop(stub_key, None)
    if moved:
        print(
            f"[persistence] healed {moved} occupant(s) off map_missing_stub "
            f"rooms",
            flush=True,
        )
    return moved


# Everything the database needs to exist. "IF NOT EXISTS" makes this safe to
# run every startup: it creates the tables on first boot and does nothing after.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    name        TEXT PRIMARY KEY,          -- character names are unique
    description TEXT NOT NULL,
    room_key    TEXT NOT NULL,             -- the Room.key they were last in
    stats       TEXT NOT NULL DEFAULT '{}' -- JSON blob; the stat spine lands here
);
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY,       -- SQLite auto-assigns rowids
    key         TEXT NOT NULL,
    description TEXT NOT NULL,
    -- 'gear' = job kit bag (Character.gear_bag), not surface inventory.
    holder_type TEXT NOT NULL CHECK (holder_type IN ('room', 'character', 'gear')),
    holder_key  TEXT NOT NULL,             -- room key or character name
    container   TEXT NOT NULL DEFAULT '{}' -- JSON: {"locked": bool, "loot": [...]}
);
-- Lag P11.4: every autosave issues "DELETE FROM items WHERE holder_key=?"
-- once per changed character/room. Without this index that is a full
-- table scan of the whole items table per changed holder -- on live
-- (~16,700 item rows, ~110+ changed characters per autosave right after a
-- restart) that is what turned the already-batched-commit apply step into
-- a multi-second stall (see engine/persistence.py commit-batch docs above).
CREATE INDEX IF NOT EXISTS idx_items_holder_key ON items(holder_key);
CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,          -- tiny key/value store for flags
    value       TEXT NOT NULL
);
-- D41 overland gather nodes + wilds homestead pockets (survive reboot).
CREATE TABLE IF NOT EXISTS homestead_plots (
    plot_id       TEXT PRIMARY KEY,
    owner_name    TEXT NOT NULL UNIQUE,
    cell_room_key TEXT NOT NULL UNIQUE,
    enter_name    TEXT,
    hub_room_key  TEXT,
    -- Homestead v2: JSON blob (micro coords, ledger, tier, residents, …).
    meta_json     TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS homestead_rooms (
    room_key     TEXT PRIMARY KEY,
    plot_id      TEXT NOT NULL,
    description  TEXT NOT NULL,
    flags_json   TEXT NOT NULL,
    exits_json   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gather_nodes (
    room_key         TEXT NOT NULL,
    resource         TEXT NOT NULL,
    remaining        INTEGER NOT NULL,
    capacity         INTEGER NOT NULL,
    respawn_at_tick  INTEGER,
    PRIMARY KEY (room_key, resource)
);
-- Personal Heaven / Hell pockets (docs/plans/personal_afterlife.md).
CREATE TABLE IF NOT EXISTS personal_realms (
    realm_id     TEXT PRIMARY KEY,
    owner_name   TEXT NOT NULL UNIQUE,
    aspect       TEXT NOT NULL,
    hub_room_key TEXT,
    seed_json    TEXT NOT NULL,
    editors_json TEXT NOT NULL,
    rules_json   TEXT NOT NULL,
    guests_json  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS personal_realm_rooms (
    room_key     TEXT PRIMARY KEY,
    realm_id     TEXT NOT NULL,
    description  TEXT NOT NULL,
    flags_json   TEXT NOT NULL,
    exits_json   TEXT NOT NULL
);
-- God demesnes (docs/plans/god_demesne_creation.md) -- SQLite, not git maps.
CREATE TABLE IF NOT EXISTS demesnes (
    demesne_id     TEXT PRIMARY KEY,
    owner_name     TEXT NOT NULL UNIQUE,
    host_plane     TEXT NOT NULL,
    host_hub_key   TEXT NOT NULL,
    hub_room_key   TEXT,
    macro_size     INTEGER NOT NULL DEFAULT 3,
    sealed         INTEGER NOT NULL DEFAULT 0,
    unmade         INTEGER NOT NULL DEFAULT 0,
    meta_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS demesne_rooms (
    room_key     TEXT PRIMARY KEY,
    demesne_id   TEXT NOT NULL,
    description  TEXT NOT NULL,
    flags_json   TEXT NOT NULL,
    exits_json   TEXT NOT NULL
);
-- Hard gm fold vault: zlib-compressed player Echo payload (not in live world).
CREATE TABLE IF NOT EXISTS character_vault (
    name       TEXT PRIMARY KEY,
    room_key   TEXT NOT NULL,
    folded_at  REAL NOT NULL,
    folded_by  TEXT,
    payload    BLOB NOT NULL
);
-- Hot-editable help overlay (engine/help_db.py; docs/plans/
-- helpfile_editing_system.md). primary_keyword is a natural key, same
-- style as characters.name -- SQLite still keeps an implicit rowid for it
-- (not WITHOUT ROWID), which the help_fts triggers below rely on.
CREATE TABLE IF NOT EXISTS helpfiles (
    primary_keyword TEXT PRIMARY KEY,
    category        TEXT NOT NULL DEFAULT '',
    gm_only         INTEGER NOT NULL DEFAULT 0,
    is_ic           INTEGER NOT NULL DEFAULT 0,
    syntax_block    TEXT NOT NULL DEFAULT '',
    body_text       TEXT NOT NULL DEFAULT '',
    author          TEXT NOT NULL,
    last_modified   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS help_aliases (
    alias           TEXT PRIMARY KEY,
    primary_keyword TEXT NOT NULL REFERENCES helpfiles(primary_keyword)
);
-- Engine-level player accounts (above Characters). name is the normalized
-- login key; password_hash is account-auth (characters keep their own);
-- data is a JSON blob of character_keys / gm_rank / prefs / totals.
CREATE TABLE IF NOT EXISTS accounts (
    name           TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    password_hash  TEXT NOT NULL DEFAULT '',
    data           TEXT NOT NULL DEFAULT '{}'
);
-- Player-owned civic shop fixtures (street enter mouths; P0 player_shops).
CREATE TABLE IF NOT EXISTS player_shops (
    shop_id        TEXT PRIMARY KEY,
    owner_key      TEXT NOT NULL,
    host_room_key  TEXT NOT NULL,
    enter_alias    TEXT NOT NULL,
    display_name   TEXT NOT NULL,
    amenity_type   TEXT NOT NULL DEFAULT 'retail',
    hp             INTEGER NOT NULL DEFAULT 100,
    hp_max         INTEGER NOT NULL DEFAULT 100,
    wrecked        INTEGER NOT NULL DEFAULT 0,
    meta_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS player_shop_rooms (
    room_key       TEXT PRIMARY KEY,
    shop_id        TEXT NOT NULL,
    description    TEXT NOT NULL,
    flags_json     TEXT NOT NULL,
    exits_json     TEXT NOT NULL
);
-- Player-founded townships (docs/plans/player_towns.md).
CREATE TABLE IF NOT EXISTS township_plots (
    town_id        TEXT PRIMARY KEY,
    town_name      TEXT NOT NULL,
    founder_name   TEXT NOT NULL UNIQUE,
    mouth_room_key TEXT NOT NULL,
    macro_x        INTEGER NOT NULL,
    macro_y        INTEGER NOT NULL,
    enter_name     TEXT,
    hub_room_key   TEXT,
    meta_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS township_rooms (
    room_key       TEXT PRIMARY KEY,
    town_id        TEXT NOT NULL,
    description    TEXT NOT NULL,
    flags_json     TEXT NOT NULL,
    exits_json     TEXT NOT NULL
);
-- Staff-scaffolded lucid dream pockets (docs/plans/dream_realm_expansion.md).
CREATE TABLE IF NOT EXISTS dream_pockets (
    pocket_id        TEXT PRIMARY KEY,
    owner_name       TEXT NOT NULL,
    anchor_room_key  TEXT NOT NULL,
    hub_room_key     TEXT,
    sealed           INTEGER NOT NULL DEFAULT 0,
    guests_json      TEXT NOT NULL DEFAULT '[]',
    meta_json        TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS dream_pocket_rooms (
    room_key       TEXT PRIMARY KEY,
    pocket_id      TEXT NOT NULL,
    description    TEXT NOT NULL,
    flags_json     TEXT NOT NULL,
    exits_json     TEXT NOT NULL
);
-- External-content FTS5 index (keyword + body only -- aliases already get
-- exact-match coverage via help_aliases, see help_db.get_entry). Kept in
-- sync by the three triggers below rather than the app re-indexing itself.
CREATE VIRTUAL TABLE IF NOT EXISTS help_fts USING fts5(
    primary_keyword, body_text,
    content='helpfiles', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS helpfiles_ai AFTER INSERT ON helpfiles BEGIN
    INSERT INTO help_fts(rowid, primary_keyword, body_text)
    VALUES (new.rowid, new.primary_keyword, new.body_text);
END;
CREATE TRIGGER IF NOT EXISTS helpfiles_ad AFTER DELETE ON helpfiles BEGIN
    INSERT INTO help_fts(help_fts, rowid, primary_keyword, body_text)
    VALUES('delete', old.rowid, old.primary_keyword, old.body_text);
END;
CREATE TRIGGER IF NOT EXISTS helpfiles_au AFTER UPDATE ON helpfiles BEGIN
    INSERT INTO help_fts(help_fts, rowid, primary_keyword, body_text)
    VALUES('delete', old.rowid, old.primary_keyword, old.body_text);
    INSERT INTO help_fts(rowid, primary_keyword, body_text)
    VALUES (new.rowid, new.primary_keyword, new.body_text);
END;
"""


def _sqlite_journal_mode():
    """Journal mode for on-disk DBs.

    Default is **DELETE** (not WAL). This MUD is single-writer; WAL's
    concurrent-reader benefit is unused, and Docker Desktop bind-mounts of
    ``riftforge.db`` on Windows have repeatedly corrupted under WAL
    (``database disk image is malformed`` / btree errors during
    ``DELETE FROM characters`` world saves). That left sessions
    outbound-only (combat spam, dead commands).

    Override with ``RIFTFORGE_SQLITE_JOURNAL`` (``WAL`` / ``DELETE`` /
    ``TRUNCATE`` / ``MEMORY`` / ``OFF``). Live Linux can set ``WAL`` if
    desired; DELETE remains correct and safer as the default.
    """
    import os

    override = (os.environ.get("RIFTFORGE_SQLITE_JOURNAL") or "").strip().upper()
    if override:
        return override
    return "DELETE"


_SHARED_MEMORY_URI = "file:riftforge_persist_shared?mode=memory&cache=shared"


def _sqlite_connect_target(path):
    """Return (connect_target, uri_mode) for sqlite3.connect."""
    if path == ":memory:" and persist_background_writer_enabled():
        return _SHARED_MEMORY_URI, True
    return path, False


def connect(path):
    """Open (or create) the database at `path` and ensure the tables exist.

    `path` may also be ":memory:" -- SQLite's built-in throwaway mode, which
    the smoke test uses so test runs never touch a real file. When the
    background writer is enabled, ``:memory:`` uses a shared-cache URI so
    the writer thread's second connection sees the same schema.
    """
    connect_target, uri = _sqlite_connect_target(path)
    if uri:
        conn = sqlite3.connect(connect_target, uri=True)
    else:
        conn = sqlite3.connect(connect_target)
    if path != ":memory:":
        mode = _sqlite_journal_mode()
        try:
            conn.execute(f"PRAGMA journal_mode={mode}")
        except sqlite3.Error as exc:
            # Bad override / exotic build — fall back rather than refuse boot.
            print(
                f"[persistence] PRAGMA journal_mode={mode} failed ({exc!r}); "
                "trying WAL",
                flush=True,
            )
            conn.execute("PRAGMA journal_mode=WAL")
        from engine import sqlite_wal as sqlite_wal_mod

        sqlite_wal_mod.configure_on_connect(conn, path)
        sqlite_wal_mod.maybe_checkpoint_on_boot(conn, path)
    conn.executescript(_SCHEMA)   # executescript runs several statements at once
    _migrate(conn)
    # Writer thread already sets this; the loop connection did not, so
    # login / help FTS / player ``save`` hit SQLITE_BUSY immediately
    # during WAL TRUNCATE or a long writer transaction.
    try:
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.Error:
        pass
    return conn


def maybe_wal_checkpoint_after_save(conn, db_path, *, reason="save"):
    """Post-save WAL drain hook (lag P13). Fail-soft — never raises."""
    try:
        from engine import sqlite_wal as sqlite_wal_mod

        return sqlite_wal_mod.maybe_checkpoint_after_save(
            conn, db_path, reason=reason,
        )
    except Exception:
        return {}


# Columns added to a table after its original CREATE TABLE already shipped
# ('IF NOT EXISTS' in _SCHEMA above only covers whole tables, not new columns
# on an existing one). Each entry is (version, sql) -- version numbers must
# be sequential starting at 1, since _migrate below applies every entry
# greater than the database's current schema_version, in order.
#
# `items.container` (lockbox locked/loot state, added alongside 'open') was
# the first column added to an existing table after the fact; it used to be
# its own hardcoded try/except ALTER TABLE. Now it's just migration #1 --
# the next real column addition (whenever one lands) is a new tuple appended
# here instead of new bespoke boilerplate.
_MIGRATIONS = [
    (1, "ALTER TABLE items ADD COLUMN container TEXT NOT NULL DEFAULT '{}'"),
    # D41: CREATE IF NOT EXISTS so existing DBs (schema_version=1) catch up;
    # fresh DBs already have these from _SCHEMA.
    (2, """CREATE TABLE IF NOT EXISTS homestead_plots (
    plot_id TEXT PRIMARY KEY,
    owner_name TEXT NOT NULL UNIQUE,
    cell_room_key TEXT NOT NULL UNIQUE,
    enter_name TEXT,
    hub_room_key TEXT
)"""),
    # Homestead v2 meta blob (ledger, micro coords, tier, residents, …).
    # Fresh DBs also get meta_json from _SCHEMA; ALTER covers older trees.
    (3, """CREATE TABLE IF NOT EXISTS homestead_rooms (
    room_key TEXT PRIMARY KEY,
    plot_id TEXT NOT NULL,
    description TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    exits_json TEXT NOT NULL
)"""),
    (4, """CREATE TABLE IF NOT EXISTS gather_nodes (
    room_key TEXT NOT NULL,
    resource TEXT NOT NULL,
    remaining INTEGER NOT NULL,
    capacity INTEGER NOT NULL,
    respawn_at_tick INTEGER,
    PRIMARY KEY (room_key, resource)
)"""),
    # Job gear_bag rows use holder_type 'gear'. Older DBs only allowed
    # room/character -- save_world then IntegrityError'd on login (killing
    # Session.run before play()) and on tick autosave. SQLite cannot ALTER
    # a CHECK; rebuild the table (callable migration -- see _migrate).
    (5, "_migrate_items_holder_gear"),
    # Personal Heaven/Hell pockets (docs/plans/personal_afterlife.md).
    (6, """CREATE TABLE IF NOT EXISTS personal_realms (
    realm_id TEXT PRIMARY KEY,
    owner_name TEXT NOT NULL UNIQUE,
    aspect TEXT NOT NULL,
    hub_room_key TEXT,
    seed_json TEXT NOT NULL,
    editors_json TEXT NOT NULL,
    rules_json TEXT NOT NULL,
    guests_json TEXT NOT NULL
)"""),
    (7, """CREATE TABLE IF NOT EXISTS personal_realm_rooms (
    room_key TEXT PRIMARY KEY,
    realm_id TEXT NOT NULL,
    description TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    exits_json TEXT NOT NULL
)"""),
    # Hard gm fold: extract offline Echo to vault (survives save_world wipe).
    (8, """CREATE TABLE IF NOT EXISTS character_vault (
    name TEXT PRIMARY KEY,
    room_key TEXT NOT NULL,
    folded_at REAL NOT NULL,
    folded_by TEXT,
    payload BLOB NOT NULL
)"""),
    # Hot-editable help overlay (engine/help_db.py). Several statements
    # (two tables, one FTS5 virtual table, three triggers) -- conn.execute()
    # only runs one statement at a time, so this is a callable migration
    # like #5, not a single SQL string.
    (9, "_migrate_add_help_tables"),
    # Engine-level accounts (login identity above Characters).
    (10, """CREATE TABLE IF NOT EXISTS accounts (
    name           TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    password_hash  TEXT NOT NULL DEFAULT '',
    data           TEXT NOT NULL DEFAULT '{}'
)"""),
    # Homestead v2: plot meta_json (ledger, micro, tier, residents, …).
    (11, "ALTER TABLE homestead_plots ADD COLUMN meta_json TEXT NOT NULL DEFAULT '{}'"),
    # Player shops P0: civic fixture mouths + pocket hubs.
    (12, """CREATE TABLE IF NOT EXISTS player_shops (
    shop_id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    host_room_key TEXT NOT NULL,
    enter_alias TEXT NOT NULL,
    display_name TEXT NOT NULL,
    amenity_type TEXT NOT NULL DEFAULT 'retail',
    hp INTEGER NOT NULL DEFAULT 100,
    hp_max INTEGER NOT NULL DEFAULT 100,
    wrecked INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT NOT NULL DEFAULT '{}'
)"""),
    (13, """CREATE TABLE IF NOT EXISTS player_shop_rooms (
    room_key TEXT PRIMARY KEY,
    shop_id TEXT NOT NULL,
    description TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    exits_json TEXT NOT NULL
)"""),
    # God demesnes skeleton (docs/plans/god_demesne_creation.md).
    (14, """CREATE TABLE IF NOT EXISTS demesnes (
    demesne_id TEXT PRIMARY KEY,
    owner_name TEXT NOT NULL UNIQUE,
    host_plane TEXT NOT NULL,
    host_hub_key TEXT NOT NULL,
    hub_room_key TEXT,
    macro_size INTEGER NOT NULL DEFAULT 3,
    sealed INTEGER NOT NULL DEFAULT 0,
    unmade INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT NOT NULL DEFAULT '{}'
)"""),
    (15, """CREATE TABLE IF NOT EXISTS demesne_rooms (
    room_key TEXT PRIMARY KEY,
    demesne_id TEXT NOT NULL,
    description TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    exits_json TEXT NOT NULL
)"""),
    (16, """CREATE TABLE IF NOT EXISTS township_plots (
    town_id TEXT PRIMARY KEY,
    town_name TEXT NOT NULL,
    founder_name TEXT NOT NULL UNIQUE,
    mouth_room_key TEXT NOT NULL,
    macro_x INTEGER NOT NULL,
    macro_y INTEGER NOT NULL,
    enter_name TEXT,
    hub_room_key TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}'
)"""),
    (17, """CREATE TABLE IF NOT EXISTS township_rooms (
    room_key TEXT PRIMARY KEY,
    town_id TEXT NOT NULL,
    description TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    exits_json TEXT NOT NULL
)"""),
    (18, """CREATE TABLE IF NOT EXISTS dream_pockets (
    pocket_id TEXT PRIMARY KEY,
    owner_name TEXT NOT NULL,
    anchor_room_key TEXT NOT NULL,
    hub_room_key TEXT,
    sealed INTEGER NOT NULL DEFAULT 0,
    guests_json TEXT NOT NULL DEFAULT '[]',
    meta_json TEXT NOT NULL DEFAULT '{}'
)"""),
    (19, """CREATE TABLE IF NOT EXISTS dream_pocket_rooms (
    room_key TEXT PRIMARY KEY,
    pocket_id TEXT NOT NULL,
    description TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    exits_json TEXT NOT NULL
)"""),
    # Lag P11.4: existing databases predate the index added to _SCHEMA
    # above -- every autosave's per-holder DELETE was a full table scan
    # of `items` (live: ~16,700 rows) for each of the ~110+ characters/
    # rooms changed per cycle, which is what the batched-commit fixes in
    # P11.3/P11.3b could not address (fewer commits, same O(rows) scans).
    (20, "CREATE INDEX IF NOT EXISTS idx_items_holder_key ON items(holder_key)"),
]


def _schema_version(conn):
    """The database's current migration level (0 for a database that
    predates schema_version entirely -- every _MIGRATIONS entry runs)."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row[0]) if row else 0


def _migrate_items_holder_gear(conn):
    """Rebuild ``items`` so CHECK allows holder_type ``gear``.

    Called as migration #5. Uses discrete ``execute`` calls (not
    ``executescript``) so we stay inside the outer ``with conn``
    transaction -- ``executescript`` would COMMIT mid-migration.
    """
    # Leftover from a crash between CREATE and RENAME -- drop and retry.
    conn.execute("DROP TABLE IF EXISTS items__gear_chk")
    conn.execute(
        """
        CREATE TABLE items__gear_chk (
            id          INTEGER PRIMARY KEY,
            key         TEXT NOT NULL,
            description TEXT NOT NULL,
            holder_type TEXT NOT NULL
                CHECK (holder_type IN ('room', 'character', 'gear')),
            holder_key  TEXT NOT NULL,
            container   TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    # Older DBs may lack container (migration 1 not applied yet should not
    # happen -- we run in order -- but SELECT * is fragile if columns drift).
    cols = [
        row[1]
        for row in conn.execute("PRAGMA table_info(items)").fetchall()
    ]
    if "container" in cols:
        conn.execute(
            """
            INSERT INTO items__gear_chk
                (id, key, description, holder_type, holder_key, container)
            SELECT id, key, description, holder_type, holder_key, container
            FROM items
            """
        )
    else:
        conn.execute(
            """
            INSERT INTO items__gear_chk
                (id, key, description, holder_type, holder_key, container)
            SELECT id, key, description, holder_type, holder_key, '{}'
            FROM items
            """
        )
    conn.execute("DROP TABLE items")
    conn.execute("ALTER TABLE items__gear_chk RENAME TO items")


def _migrate_add_help_tables(conn):
    """Add the hot-editable help overlay tables/index/triggers (migration
    #9) to a database created before they existed. Identical statements to
    the ones in ``_SCHEMA`` -- every ``IF NOT EXISTS`` makes this a no-op
    for a fresh database that already got them from ``_SCHEMA`` directly.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS helpfiles (
            primary_keyword TEXT PRIMARY KEY,
            category        TEXT NOT NULL DEFAULT '',
            gm_only         INTEGER NOT NULL DEFAULT 0,
            is_ic           INTEGER NOT NULL DEFAULT 0,
            syntax_block    TEXT NOT NULL DEFAULT '',
            body_text       TEXT NOT NULL DEFAULT '',
            author          TEXT NOT NULL,
            last_modified   REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS help_aliases (
            alias           TEXT PRIMARY KEY,
            primary_keyword TEXT NOT NULL REFERENCES helpfiles(primary_keyword)
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS help_fts USING fts5(
            primary_keyword, body_text,
            content='helpfiles', content_rowid='rowid'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS helpfiles_ai AFTER INSERT ON helpfiles BEGIN
            INSERT INTO help_fts(rowid, primary_keyword, body_text)
            VALUES (new.rowid, new.primary_keyword, new.body_text);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS helpfiles_ad AFTER DELETE ON helpfiles BEGIN
            INSERT INTO help_fts(help_fts, rowid, primary_keyword, body_text)
            VALUES('delete', old.rowid, old.primary_keyword, old.body_text);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS helpfiles_au AFTER UPDATE ON helpfiles BEGIN
            INSERT INTO help_fts(help_fts, rowid, primary_keyword, body_text)
            VALUES('delete', old.rowid, old.primary_keyword, old.body_text);
            INSERT INTO help_fts(rowid, primary_keyword, body_text)
            VALUES (new.rowid, new.primary_keyword, new.body_text);
        END
        """
    )


# Name -> callable for migrations that cannot be a single SQL statement.
_MIGRATION_CALLABLES = {
    "_migrate_items_holder_gear": _migrate_items_holder_gear,
    "_migrate_add_help_tables": _migrate_add_help_tables,
}


def _migrate(conn):
    """Bring the database up to the latest schema by applying every
    migration newer than its recorded schema_version, in order, and
    recording the new version after each -- so a boot that dies partway
    through resumes from the last completed migration instead of redoing
    (or skipping) one. Runs on every boot; a database already at the latest
    version does nothing.

    Each ``_MIGRATIONS`` entry is ``(version, sql_string_or_callable_name)``.
    Callable names resolve through ``_MIGRATION_CALLABLES`` (table rebuilds
    that need several statements inside one transaction).

    SQLite has no 'ADD COLUMN IF NOT EXISTS', so the try/except below stays
    as a safety net (not the primary mechanism) for one specific case: a
    database that already has `items.container` from the OLD, pre-versioned
    code path (a single hardcoded try/except ALTER TABLE) but has never
    recorded a schema_version -- without it, that database would hit
    "duplicate column" and crash instead of just catching up its version
    number.
    """
    current = _schema_version(conn)
    with conn:
        for version, step in _MIGRATIONS:
            if version <= current:
                continue
            try:
                if isinstance(step, str) and step in _MIGRATION_CALLABLES:
                    _MIGRATION_CALLABLES[step](conn)
                else:
                    conn.execute(step)
            except sqlite3.OperationalError:
                pass   # already applied by the old pre-versioned code path
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES "
                "('schema_version', ?)",
                (str(version),),
            )


def is_seeded(conn):
    """Has this database ever been populated with the starter world?

    First boot: False -> the caller places the starter items and calls
    mark_seeded(). Every later boot: True -> load what the players left behind
    instead of re-placing starter items (otherwise a picked-up sword would
    respawn in the plaza on every restart AND stay in the player's bag).
    """
    row = conn.execute("SELECT value FROM meta WHERE key = 'seeded'").fetchone()
    return row is not None


def mark_seeded(conn):
    """Record that the starter world has been placed (see is_seeded)."""
    with conn:   # 'with conn' wraps this in a transaction and commits on success
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('seeded', '1')")


def load_game_time(conn):
    """How many ticks of the compressed game-time clock have elapsed
    (Milestone E, section 4-E's pacing follow-up) -- reuses the same
    generic `meta` table as is_seeded, no schema change needed. 0 if this
    save predates the feature (a fresh world starts its clock at day 0)."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'game_time_ticks'"
    ).fetchone()
    return int(row[0]) if row else 0


def save_game_time(conn, ticks):
    """Persist the current game-time tick count (see load_game_time)."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('game_time_ticks', ?)",
            (str(ticks),),
        )


def load_calendar_epoch_day(conn):
    """Gregorian display epoch: absolute game-day that maps to 2015-10-15.

    Returns None when the key is missing so Game can rebase an upgraded
    world (set to current day) or leave a fresh world at 0. See
    engine.game_calendar and server.Game.__init__.
    """
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'calendar_epoch_day'"
    ).fetchone()
    if not row:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def save_calendar_epoch_day(conn, epoch_day):
    """Persist the Gregorian calendar_epoch_day offset (see load)."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('calendar_epoch_day', ?)",
            (str(int(epoch_day)),),
        )


def load_moral_state(conn):
    """Load Evil Strikes Back world meter + eclipse from meta.

    Returns a dict with moral_balance, eclipse_until_tick,
    moral_event_cooldown_until, moral_maxed_side, moral_maxed_since_tick,
    moral_last_casualty_tick, moral_scout_cooldown_until, holy_war_active,
    rank_titles_visible (defaults when keys are missing).
    """
    def _int_meta(key, default=0):
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return default

    def _str_meta(key, default=None):
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if not row or row[0] in (None, "", "None"):
            return default
        return str(row[0])

    def _float_meta(key, default=0.0):
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return float(row[0])
        except (TypeError, ValueError):
            return default

    maxed_side = _str_meta("moral_maxed_side", None)
    # Only 'evil' / 'good' are valid hold sides.
    if maxed_side not in ("evil", "good"):
        maxed_side = None

    return {
        "moral_balance": _int_meta("moral_balance", 0),
        "eclipse_until_tick": _int_meta("eclipse_until_tick", 0),
        "moral_event_cooldown_until": _int_meta(
            "moral_event_cooldown_until", 0
        ),
        "moral_maxed_side": maxed_side,
        "moral_maxed_since_tick": _int_meta("moral_maxed_since_tick", 0),
        "moral_last_casualty_tick": _int_meta(
            "moral_last_casualty_tick", 0
        ),
        "moral_scout_cooldown_until": _int_meta(
            "moral_scout_cooldown_until", 0
        ),
        "moral_last_centering_tick": _int_meta(
            "moral_last_centering_tick", 0
        ),
        "moral_good_window_start_tick": _int_meta(
            "moral_good_window_start_tick", 0
        ),
        "moral_good_steps_in_window": _int_meta(
            "moral_good_steps_in_window", 0
        ),
        # Host/Infernal war global (gm holywar); 0=off 1=on. Default off.
        "holy_war_active": bool(_int_meta("holy_war_active", 0)),
        # Rank flavor on score (gm titles); 1=on 0=off. Default off.
        "rank_titles_visible": bool(_int_meta("rank_titles_visible", 0)),
        "roadtrip_minutes": _float_meta("roadtrip_minutes", 30.0),
        "vehicle_pvp_enabled": bool(_int_meta("vehicle_pvp_enabled", 0)),
        "tow_dispatch_enabled": bool(_int_meta("tow_dispatch_enabled", 1)),
    }


def save_moral_state(conn, game):
    """Persist moral_balance / eclipse / hold timers / scout cooldown."""
    ensure_game_defaults(game)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_balance', ?)",
            (str(int(game.moral_balance)),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('eclipse_until_tick', ?)",
            (str(int(game.eclipse_until_tick or 0)),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_event_cooldown_until', ?)",
            (str(int(game.moral_event_cooldown_until or 0)),),
        )
        # Maxed-hold arming -- must survive Docker restart or the ±100
        # wall timer resets every bounce.
        side = getattr(game, "moral_maxed_side", None) or ""
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_maxed_side', ?)",
            (str(side),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_maxed_since_tick', ?)",
            (str(int(getattr(game, "moral_maxed_since_tick", 0) or 0)),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_last_casualty_tick', ?)",
            (str(int(getattr(game, "moral_last_casualty_tick", 0) or 0)),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_scout_cooldown_until', ?)",
            (str(int(getattr(game, "moral_scout_cooldown_until", 0) or 0)),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_last_centering_tick', ?)",
            (
                str(
                    int(getattr(game, "moral_last_centering_tick", 0) or 0)
                ),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_good_window_start_tick', ?)",
            (
                str(
                    int(
                        getattr(game, "moral_good_window_start_tick", 0) or 0
                    )
                ),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_good_steps_in_window', ?)",
            (
                str(
                    int(
                        getattr(game, "moral_good_steps_in_window", 0) or 0
                    )
                ),
            ),
        )
        # Holy war (Host vs Infernal) -- survives restart; default off.
        holy = 1 if bool(getattr(game, "holy_war_active", False)) else 0
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('holy_war_active', ?)",
            (str(holy),),
        )
        # Rank titles on score -- survives restart; default on.
        titles = 1 if bool(getattr(game, "rank_titles_visible", True)) else 0
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('rank_titles_visible', ?)",
            (str(titles),),
        )
        road_min = float(getattr(game, "roadtrip_minutes", 30.0) or 30.0)
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('roadtrip_minutes', ?)",
            (str(road_min),),
        )
        vpvp = 1 if bool(getattr(game, "vehicle_pvp_enabled", False)) else 0
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('vehicle_pvp_enabled', ?)",
            (str(vpvp),),
        )
        tdisp = 1 if bool(getattr(game, "tow_dispatch_enabled", True)) else 0
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('tow_dispatch_enabled', ?)",
            (str(tdisp),),
        )



def load_plane_soul_counts(conn):
    """Load heaven_soul_count / hell_soul_count from meta (default 0)."""
    def _int_meta(key, default=0):
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return default

    return {
        "heaven_soul_count": _int_meta("heaven_soul_count", 0),
        "hell_soul_count": _int_meta("hell_soul_count", 0),
        "next_phone_seq": _int_meta("next_phone_seq", 1000),
    }


def save_plane_soul_counts(conn, game):
    """Persist plane soul banks on Game (no SUPERS import)."""
    heaven = int(getattr(game, "heaven_soul_count", 0) or 0)
    hell = int(getattr(game, "hell_soul_count", 0) or 0)
    phone_seq = int(getattr(game, "next_phone_seq", 1000) or 1000)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('heaven_soul_count', ?)",
            (str(heaven),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('hell_soul_count', ?)",
            (str(hell),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('next_phone_seq', ?)",
            (str(phone_seq),),
        )


def _load_meta_dict(conn, key):
    """Load a JSON dict override blob from ``meta[key]`` (missing/bad = {}).

    Shared by every dict-shaped meta blob (tuning tables, hue courts,
    death beacons, Cadence overrides, …). Empty / blank values fail open
    to ``{}`` the same way a missing key does so boot never raises on a
    hand-edited meta row.
    """
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (key,)
    ).fetchone()
    # ``not row[0]`` also catches empty-string values left by a wipe.
    if not row or not row[0]:
        return {}
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def load_meta_json(conn, key):
    """Public opaque-KV read: JSON dict at ``meta[key]`` (missing = {}).

    T3 persistence-api: games should prefer this (or named wrappers) over
    inventing new SQL. SUPERS Game meta orchestration lives in
    ``supers.persist_meta`` via ``engine.hooks.set_game_meta_codec``.
    """
    return _load_meta_dict(conn, key)


def _save_meta_dict(conn, key, game, attr):
    """Persist ``game.<attr>`` (a dict) into ``meta[key]``.

    Non-dict / missing attributes coerce to ``{}`` so a corrupt in-memory
    blob cannot write junk JSON that later boot would have to heal.
    """
    blob = getattr(game, attr, None)
    if not isinstance(blob, dict):
        blob = {}
    save_meta_json(conn, key, blob)


def save_meta_json(conn, key, value):
    """Public opaque-KV write: store ``value`` (dict) at ``meta[key]``."""
    if not isinstance(value, dict):
        value = {}
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )


def load_hue_courts(conn):
    """Load celestial hue court seats from meta (default empty dict)."""
    return _load_meta_dict(conn, "hue_courts")


def save_hue_courts(conn, game):
    """Persist game.hue_courts JSON (Celestial Prince / Grigori seats).

    Serializes whatever dict is on the Game. SUPERS may pre-normalize via
    celestial_court.serialize_hue_courts before save; this layer stays
    engine-pure (no supers import).
    """
    _save_meta_dict(conn, "hue_courts", game, "hue_courts")


def load_death_beacons(conn):
    """Load death_beacons JSON dict from meta (default empty)."""
    return _load_meta_dict(conn, "death_beacons")


def save_death_beacons(conn, game):
    """Persist death_beacons so copyover keeps the Reaper queue."""
    _save_meta_dict(conn, "death_beacons", game, "death_beacons")


def load_author_mantle_event(conn):
    """Load Chuck Author mantle-resume event dict from meta (default {}).

    Missing / malformed key → empty dict so ``ensure_event`` can fill
    idle defaults. Engine-pure: no SUPERS imports.
    """
    return _load_meta_dict(conn, "author_mantle_event")


def save_author_mantle_event(conn, game):
    """Persist game.author_mantle_event so copyover keeps Chuck's event.

    Phase / until_tick / foe_key / stat_backup / auto_bout survive the
    process swap; SUPERS ``restore_after_boot`` re-reveals Chuck and
    respawns a missing Unmade shade quietly.
    """
    _save_meta_dict(conn, "author_mantle_event", game, "author_mantle_event")


def load_amara_pressure_state(conn):
    """Load world Unmade pressure meter from meta (default {})."""
    return _load_meta_dict(conn, "amara_pressure_state")


def save_amara_pressure_state(conn, game):
    """Persist game.amara_pressure_state across copyover."""
    _save_meta_dict(conn, "amara_pressure_state", game, "amara_pressure_state")


def load_chuck_heaven_claim(conn):
    """Load Heaven-claim Author reaction state from meta (default {})."""
    return _load_meta_dict(conn, "chuck_heaven_claim")


def save_chuck_heaven_claim(conn, game):
    """Persist ``game.chuck_heaven_claim`` across copyover."""
    _save_meta_dict(conn, "chuck_heaven_claim", game, "chuck_heaven_claim")


# Cap matches channel_history ring sizes (bare replay last 20 lines).
OOC_HISTORY_MAXLEN = 20
WIZNET_HISTORY_MAXLEN = 20


def load_ooc_history(conn):
    """Load the global OOC ring buffer from meta (see ``channel_history``)."""
    from engine import channel_history

    return channel_history.load_channel(conn, "ooc")


def save_ooc_history(conn, game):
    """Persist ``game.ooc_history`` (see ``channel_history``)."""
    from engine import channel_history

    channel_history.save_channel(conn, game, "ooc")


def load_wiznet_history(conn):
    """Load the global wiznet ring buffer from meta (see ``channel_history``)."""
    from engine import channel_history

    return channel_history.load_channel(conn, "wiznet")


def save_wiznet_history(conn, game):
    """Persist ``game.wiznet_history`` (see ``channel_history``)."""
    from engine import channel_history

    channel_history.save_channel(conn, game, "wiznet")


def mark_account_dirty(game, account_or_name):
    """Queue one account for the next incremental ``save_accounts`` pass."""
    if game is None or account_or_name is None:
        return
    if hasattr(account_or_name, "name"):
        name = (account_or_name.name or "").strip()
    else:
        name = str(account_or_name or "").strip()
    if not name:
        return
    key = name.lower()
    dirty = getattr(game, "_persist_dirty_accounts", None)
    if dirty is None:
        dirty = set()
        game._persist_dirty_accounts = dirty
    dirty.add(key)


def _sqlite_busy_error(exc):
    """True when *exc* is a transient SQLite lock/contention failure."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _persist_on_asyncio_loop():
    """True when called from the running tick/command loop.

    The persistence writer and Cadence planner threads never have a
    running asyncio loop. Login and ``changes`` flush *do* -- and those
    used to ``time.sleep`` until SQLite finished, pinning the same thread
    Cadence needs (P30.2).
    """
    try:
        import asyncio

        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _wait_for_persistence_writer_idle(*, timeout_s=2.0):
    """Brief pause so the background writer can release the DB write lock.

    Never ``time.sleep`` on the asyncio thread -- that froze town life for
    the whole writer apply. Callers retry ``SQLITE_BUSY`` instead.
    """
    if not persist_background_writer_ready():
        return
    if _persist_on_asyncio_loop():
        return
    try:
        from engine.persistence_writer import writer_thread_busy
    except ImportError:
        return
    import time

    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while writer_thread_busy() and time.monotonic() < deadline:
        time.sleep(0.01)


def flush_accounts_now(conn, game, *, reason=""):
    """Write dirty account rows immediately (changelog cursors, login menu).

    Waits briefly for the background persistence writer, then retries on
    SQLITE_BUSY. Returns True when ``save_accounts`` completes.
    """
    import time

    if conn is None or game is None:
        return False
    _wait_for_persistence_writer_idle()
    attempts = 3
    for attempt in range(attempts):
        try:
            save_accounts(conn, game)
            return True
        except sqlite3.OperationalError as exc:
            if not _sqlite_busy_error(exc) or attempt + 1 >= attempts:
                print(
                    f"[persistence] flush_accounts_now failed "
                    f"reason={reason!r} ({exc!r})",
                    flush=True,
                )
                return False
            # Do not pin Cadence with a blocking sleep; WAL retries are
            # instant on the tick loop and still succeed between writer
            # statements most of the time.
            if not _persist_on_asyncio_loop():
                time.sleep(0.05 * (attempt + 1))
        except Exception as exc:
            print(
                f"[persistence] flush_accounts_now failed "
                f"reason={reason!r} ({exc!r})",
                flush=True,
            )
            return False
    return False


def flush_character_checkpoint_now(conn, game, character, *, reason=""):
    """Checkpoint one character row; retry on SQLITE_BUSY."""
    import time

    if conn is None or game is None or character is None:
        return False
    _wait_for_persistence_writer_idle()
    attempts = 3
    char_key = getattr(character, "key", "") or ""
    for attempt in range(attempts):
        ok, msg = persist_save_character(
            conn, game, character, player_checkpoint=False,
        )
        if ok:
            return True
        low = (msg or "").lower()
        if "locked" not in low and "busy" not in low:
            print(
                f"[persistence] flush_character_checkpoint_now failed "
                f"reason={reason!r} char={char_key!r} ({msg!r})",
                flush=True,
            )
            return False
        if attempt + 1 >= attempts:
            print(
                f"[persistence] flush_character_checkpoint_now failed "
                f"reason={reason!r} char={char_key!r} ({msg!r})",
                flush=True,
            )
            return False
        if not _persist_on_asyncio_loop():
            time.sleep(0.05 * (attempt + 1))
    return False


def _account_row_digest(name, display, password_hash, blob):
    """Fingerprint one accounts-table row for incremental skip."""
    payload = f"{name}\0{display}\0{password_hash}\0{blob}".encode("utf-8")
    return zlib.adler32(payload) & 0xFFFFFFFF


def save_accounts(conn, game, *, force_full=False):
    """Write accounts; full wipe on first save, then dirty/hash incremental.

    Live autosave used to ``DELETE FROM accounts`` every minute -- that alone
    was multi-second under load. After the first successful save, only
    changed / dirty accounts are rewritten and removed names are deleted.
    """
    from engine.accounts import Account, ensure_accounts_dict

    accounts = ensure_accounts_dict(game)
    with conn:
        if not accounts:
            # Safety: never wipe persisted accounts when the in-memory dict
            # was not loaded yet (boot-order bug) or was accidentally cleared.
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM accounts"
                ).fetchone()
                existing = int(row[0]) if row else 0
            except sqlite3.OperationalError:
                existing = 0
            if existing:
                print(
                    "[persistence] save_accounts skipped: game.accounts "
                    f"empty but {existing} row(s) remain in SQLite",
                    flush=True,
                )
                return

        warm = bool(getattr(game, "_accounts_persist_warm", False))
        do_full = force_full or not warm
        prev = getattr(game, "_account_snapshot_hashes", None) or {}
        dirty = set(getattr(game, "_persist_dirty_accounts", None) or set())
        new_hashes = {}
        alive = set()
        changed = 0
        removed = 0

        if do_full:
            conn.execute("DELETE FROM accounts")

        for account in accounts.values():
            if not isinstance(account, Account):
                continue
            name = (account.name or "").strip()
            if not name:
                continue
            display = (account.display_name or name).strip() or name
            blob = json.dumps(account.to_blob())
            key = name.lower()
            alive.add(key)
            digest = _account_row_digest(
                name, display, account.password_hash or "", blob,
            )
            new_hashes[key] = digest
            if not do_full:
                if key not in dirty and prev.get(key) == digest:
                    continue
                conn.execute("DELETE FROM accounts WHERE name=?", (name,))
                # Also try lowercase key variants that might differ in case.
                if key != name:
                    conn.execute(
                        "DELETE FROM accounts WHERE lower(name)=?", (key,),
                    )
            conn.execute(
                "INSERT INTO accounts "
                "(name, display_name, password_hash, data) "
                "VALUES (?, ?, ?, ?)",
                (name, display, account.password_hash or "", blob),
            )
            changed += 1

        if not do_full:
            for old_key in prev:
                if old_key in alive:
                    continue
                conn.execute(
                    "DELETE FROM accounts WHERE lower(name)=?", (old_key,),
                )
                removed += 1

        game._account_snapshot_hashes = new_hashes
        game._accounts_persist_warm = True
        game._persist_dirty_accounts = set()
        game._last_accounts_save_stats = {
            "force_full": do_full,
            "n_changed": changed,
            "n_removed": removed,
            "n_accounts": len(alive),
        }


def load_accounts(conn, game):
    """Rebuild ``game.accounts`` from the accounts table.

    Safe on pre-feature DBs (empty table / missing table → empty dict).
    Called once at boot after ``load_world`` so character back-pointers
    can be reconciled against live Echoes.
    """
    from engine.accounts import Account, account_lookup_key, ensure_accounts_dict

    accounts = ensure_accounts_dict(game)
    accounts.clear()
    game._account_snapshot_hashes = None
    game._accounts_persist_warm = False
    game._persist_dirty_accounts = set()
    try:
        rows = conn.execute(
            "SELECT name, display_name, password_hash, data FROM accounts"
        )
    except sqlite3.OperationalError:
        # Migration not yet applied / very old DB -- leave empty.
        return accounts
    for name, display_name, password_hash, data in rows:
        cleaned = (name or "").strip()
        if not cleaned:
            continue
        account = Account(
            cleaned,
            password_hash=password_hash or "",
            display_name=(display_name or cleaned).strip() or cleaned,
        )
        try:
            blob = json.loads(data) if data else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            blob = {}
        account.apply_blob(blob)
        accounts[account_lookup_key(account.name)] = account
    return accounts


def load_rumor_boards(conn):
    """Load player rumor boards from meta (D63). Returns {room_key: [posts]}.

    Missing key → empty dict (pre-feature saves). Malformed JSON → empty
    dict rather than crashing boot.
    """
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'rumor_boards'"
    ).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for room_key, posts in data.items():
        if not isinstance(room_key, str) or not isinstance(posts, list):
            continue
        cleaned[room_key] = [
            p for p in posts
            if isinstance(p, dict) and isinstance(p.get("text"), str)
        ]
    return cleaned


def save_rumor_boards(conn, game):
    """Persist game.rumor_boards onto the meta table (D63)."""
    boards = getattr(game, "rumor_boards", None)
    if boards is None:
        boards = {}
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('rumor_boards', ?)",
            (json.dumps(boards),),
        )


def load_lifetime_stats(conn):
    """Load GM lifetime counters from meta (gmworld panel).

    Missing / malformed key → empty default-shaped dict so old saves boot
    cleanly. Sanitization lives in supers.world_stats.normalize_loaded
    when SUPERS is present; here we only JSON-decode.
    """
    return _load_meta_dict(conn, "lifetime_stats")


def save_lifetime_stats(conn, game):
    """Persist game.lifetime_stats onto the meta table."""
    _save_meta_dict(conn, "lifetime_stats", game, "lifetime_stats")


def load_cadence_chances(conn):
    """Load GM Cadence chance overrides from meta (empty = code defaults)."""
    return _load_meta_dict(conn, "cadence_chances")


def save_cadence_chances(conn, game):
    """Persist game.cadence_chances overrides onto the meta table."""
    _save_meta_dict(conn, "cadence_chances", game, "cadence_chances")


def load_taxi_mode(conn):
    """Load GM taxi pacing mode from meta (default testing)."""
    return _load_meta_dict(conn, "taxi_mode")


def save_taxi_mode(conn, game):
    """Persist game.taxi_mode onto the meta table."""
    from engine import hooks
    hooks.save_taxi_mode_meta(conn, game)


def load_pet_adoption(conn):
    """Load Lebanon Adoption Agency weekly board from meta."""
    return _load_meta_dict(conn, "pet_adoption")


def save_pet_adoption(conn, game):
    """Persist game.pet_adoption weekly board onto the meta table."""
    _save_meta_dict(conn, "pet_adoption", game, "pet_adoption")


def load_incap_tuning(conn):
    """Load GM incap stun tuning overrides from meta (empty = code defaults)."""
    return _load_meta_dict(conn, "incap_tuning")


def save_incap_tuning(conn, game):
    """Persist game.incap_tuning overrides onto the meta table."""
    _save_meta_dict(conn, "incap_tuning", game, "incap_tuning")


def load_game_clock_tuning(conn):
    """Load GM world-clock scale overrides from meta (empty = code defaults)."""
    return _load_meta_dict(conn, "game_clock_tuning")


def save_game_clock_tuning(conn, game):
    """Persist game.game_clock_tuning overrides onto the meta table."""
    _save_meta_dict(conn, "game_clock_tuning", game, "game_clock_tuning")


def load_outgoing_damage_tuning(conn):
    """Load GM outgoing soft-cap overrides from meta (empty = code defaults)."""
    return _load_meta_dict(conn, "outgoing_damage_tuning")


def save_outgoing_damage_tuning(conn, game):
    """Persist game.outgoing_damage_tuning overrides onto the meta table."""
    _save_meta_dict(conn, "outgoing_damage_tuning", game, "outgoing_damage_tuning")


def load_corpse_decay_tuning(conn):
    """Load GM body-decay TTL overrides from meta (empty = code defaults)."""
    return _load_meta_dict(conn, "corpse_decay_tuning")


def save_corpse_decay_tuning(conn, game):
    """Persist game.corpse_decay_tuning overrides onto the meta table."""
    _save_meta_dict(conn, "corpse_decay_tuning", game, "corpse_decay_tuning")


def load_hell_exile_tuning(conn):
    """Load GM Hell exile TTL overrides from meta (empty = code defaults)."""
    return _load_meta_dict(conn, "hell_exile_tuning")


def save_hell_exile_tuning(conn, game):
    """Persist game.hell_exile_tuning overrides onto the meta table."""
    _save_meta_dict(conn, "hell_exile_tuning", game, "hell_exile_tuning")


def load_empty_hold_tuning(conn):
    """Load GM The Empty hold overrides from meta (empty = code defaults)."""
    return _load_meta_dict(conn, "empty_hold_tuning")


def save_empty_hold_tuning(conn, game):
    """Persist game.empty_hold_tuning overrides onto the meta table."""
    _save_meta_dict(conn, "empty_hold_tuning", game, "empty_hold_tuning")


def load_pit_drop_tuning(conn):
    """Load GM Purgatory pit drop tuning overrides from meta."""
    return _load_meta_dict(conn, "pit_drop_tuning")


def save_pit_drop_tuning(conn, game):
    """Persist game.pit_drop_tuning overrides onto the meta table."""
    _save_meta_dict(conn, "pit_drop_tuning", game, "pit_drop_tuning")


def load_seal_key_wander(conn):
    """Load seal key wander state from meta."""
    return _load_meta_dict(conn, "seal_key_wander")


def save_seal_key_wander(conn, game):
    """Persist game.seal_key_wander onto the meta table."""
    _save_meta_dict(conn, "seal_key_wander", game, "seal_key_wander")


def load_marches_rim_tuning(conn):
    """Load GM Marches fog-rim ring cap from meta."""
    return _load_meta_dict(conn, "marches_rim_tuning")


def save_marches_rim_tuning(conn, game):
    """Persist game.marches_rim_tuning onto the meta table."""
    _save_meta_dict(conn, "marches_rim_tuning", game, "marches_rim_tuning")


def load_marches_rim_state(conn):
    """Load Marches monthly rim seed/affix state from meta."""
    return _load_meta_dict(conn, "marches_rim_state")


def save_marches_rim_state(conn, game):
    """Persist game.marches_rim_state onto the meta table."""
    _save_meta_dict(conn, "marches_rim_state", game, "marches_rim_state")


def load_portal_giver_costs(conn):
    """Load GM portal vendor ticket price overrides from meta."""
    return _load_meta_dict(conn, "portal_giver_costs")


def save_portal_giver_costs(conn, game):
    """Persist game.portal_giver_costs overrides onto the meta table."""
    _save_meta_dict(conn, "portal_giver_costs", game, "portal_giver_costs")


def load_cadence_scale(conn):
    """Load GM Cadence scale / LOD overrides from meta (empty = defaults)."""
    return _load_meta_dict(conn, "cadence_scale")


def save_cadence_scale(conn, game):
    """Persist game.cadence_scale overrides onto the meta table."""
    _save_meta_dict(conn, "cadence_scale", game, "cadence_scale")


def load_winchester_failsafe(conn):
    """Load Winchester Earth-job failsafe toggle from meta (default {})."""
    return _load_meta_dict(conn, "winchester_failsafe")


def save_winchester_failsafe(conn, game):
    """Persist game.winchester_failsafe (enabled + active_job) to meta."""
    _save_meta_dict(conn, "winchester_failsafe", game, "winchester_failsafe")


def load_winchester_pursuit(conn):
    """Load Winchester evil-monster pursuit state from meta (default {})."""
    return _load_meta_dict(conn, "winchester_pursuit")


def save_winchester_pursuit(conn, game):
    """Persist game.winchester_pursuit (active mark) to meta."""
    _save_meta_dict(conn, "winchester_pursuit", game, "winchester_pursuit")


def load_cadence_airport(conn):
    """Load GM Cadence airport travel toggle from meta (default off)."""
    return _load_meta_dict(conn, "cadence_airport")


def save_cadence_airport(conn, game):
    """Persist game.cadence_airport onto the meta table."""
    _save_meta_dict(conn, "cadence_airport", game, "cadence_airport")


def _loot_entry_for_json(entry):
    """Serialize one body/lockbox loot entry for the items.container blob.

    Lockboxes store dict rewards ({type: growth|relic, ...}). Combat death
    spills live Item objects onto body.loot (look in / get from) -- those
    must become plain dicts here or save_world crashes mid-tick.
    """
    if isinstance(entry, dict):
        return entry
    # Duck-type Item: combat._handle_drop spills inventory this way.
    if isinstance(entry, Item):
        blob = {
            "type": "carried",
            "key": entry.key,
            "description": entry.description,
        }
        need = getattr(entry, "need", None)
        if need:
            blob["need"] = need
        catalog_id = getattr(entry, "catalog_id", None)
        if catalog_id:
            blob["catalog_id"] = catalog_id
        if getattr(entry, "provides_light", False):
            blob["provides_light"] = True
        relic = getattr(entry, "relic", None)
        if relic:
            blob["relic"] = relic
        relic_tier = getattr(entry, "relic_tier", None)
        if relic_tier is not None:
            try:
                blob["relic_tier"] = int(relic_tier)
            except (TypeError, ValueError):
                pass
        return blob
    return None


def _loot_for_json(loot):
    """JSON-safe list for items.container 'loot' (dicts only)."""
    out = []
    for entry in loot or []:
        blob = _loot_entry_for_json(entry)
        if blob is not None:
            out.append(blob)
    return out


def _loot_from_json(loot):
    """Restore loot list: carried blobs become Items; other dicts stay dicts."""
    out = []
    for entry in loot or []:
        if isinstance(entry, dict) and entry.get("type") == "carried":
            item = Item(
                entry["key"],
                entry.get("description", entry["key"]),
            )
            if entry.get("need"):
                item.need = entry["need"]
            if entry.get("catalog_id"):
                item.catalog_id = entry["catalog_id"]
            if entry.get("provides_light"):
                item.provides_light = True
            if entry.get("relic"):
                item.relic = entry["relic"]
            if entry.get("relic_tier") is not None:
                try:
                    item.relic_tier = int(entry["relic_tier"])
                except (TypeError, ValueError):
                    pass
            out.append(item)
        else:
            out.append(entry)
    return out


def _restore_relic_fields(item, state):
    """Reattach Divine relic family + tier stamps from a container blob."""
    relic = state.get("relic")
    if relic:
        item.relic = relic
    if state.get("relic_tier") is not None:
        try:
            item.relic_tier = int(state["relic_tier"])
        except (TypeError, ValueError):
            pass


def _restore_pit_mimic_fields(item, state):
    """Reattach Purgatory pit mimic stamps from a container blob."""
    if state.get("pit_mimic"):
        item.pit_mimic = True
    if state.get("pit_mimic_tier") is not None:
        try:
            item.pit_mimic_tier = int(state["pit_mimic_tier"])
        except (TypeError, ValueError):
            pass
    if state.get("pit_mimic_floor") is not None:
        try:
            item.pit_mimic_floor = int(state["pit_mimic_floor"])
        except (TypeError, ValueError):
            pass
    if state.get("pit_run_tag"):
        item.pit_run_tag = str(state["pit_run_tag"])


def _restore_on_use_fields(item, state):
    """Reattach consumable ``on_use`` / pit potion stamps from a blob.

    Pit sustain potions (and any future on_use Items) keep their effect
    dict across restart. Older saves omitted these keys -- SUPERS boot
    heal (``heal_loaded_pit_potion`` via enrich) rebuilds them by key.
    """
    on_use = state.get("on_use")
    if isinstance(on_use, dict) and on_use:
        item.on_use = dict(on_use)
    if state.get("purgatory_pit_loot"):
        item.purgatory_pit_loot = True
    if state.get("pit_potion_id"):
        item.pit_potion_id = str(state["pit_potion_id"]).strip()


def _restore_herb_fields(item, state):
    """Reattach herb joint / loaded pipe stamps from a save blob."""
    if state.get("herb_id"):
        item.herb_id = str(state["herb_id"]).strip()
    if state.get("is_joint"):
        item.is_joint = True
    if state.get("is_pipe"):
        item.is_pipe = True
    if state.get("pipe_herb_id"):
        item.pipe_herb_id = str(state["pipe_herb_id"]).strip()
    if state.get("pipe_puffs") is not None:
        try:
            item.pipe_puffs = int(state["pipe_puffs"])
        except (TypeError, ValueError):
            pass


def _bag_contents_for_json(item):
    """Serialize nested bag rows for the container blob."""
    contents = getattr(item, "bag_contents", None) or []
    out = []
    for sub in contents:
        if not isinstance(sub, Item):
            continue
        out.append({
            "key": sub.key,
            "description": sub.description,
            "container": json.loads(_item_container_blob(sub)),
        })
    return out


def _wallet_contents_for_json(item):
    """Serialize nested wallet rows for the container blob."""
    contents = getattr(item, "wallet_contents", None) or []
    out = []
    for sub in contents:
        if not isinstance(sub, Item):
            continue
        out.append({
            "key": sub.key,
            "description": sub.description,
            "container": json.loads(_item_container_blob(sub)),
        })
    return out


def _restore_bag_fields(item, state):
    """Reattach wearable bag stamps from a container blob."""
    if state.get("is_bag"):
        item.is_bag = True
    if state.get("is_gear_bag"):
        item.is_gear_bag = True
    if state.get("is_wallet"):
        item.is_wallet = True
    if state.get("is_id_card"):
        item.is_id_card = True
    if state.get("wallet_capacity") is not None:
        try:
            item.wallet_capacity = int(state["wallet_capacity"])
        except (TypeError, ValueError):
            pass
    if state.get("wallet_dollars") is not None:
        try:
            item.wallet_dollars = int(state["wallet_dollars"])
        except (TypeError, ValueError):
            pass
    if state.get("wallet_cents") is not None:
        try:
            item.wallet_cents = int(state["wallet_cents"])
        except (TypeError, ValueError):
            pass
    blob = state.get("legal_id")
    if isinstance(blob, dict):
        item.legal_id = dict(blob)
    if state.get("bag_capacity") is not None:
        try:
            item.bag_capacity = int(state["bag_capacity"])
        except (TypeError, ValueError):
            pass
    worn = state.get("container_worn")
    if worn in ("back", "shoulder", "pocket"):
        item.container_worn = worn
    raw_contents = state.get("bag_contents") or []
    restored = []
    for row in raw_contents:
        if not isinstance(row, dict):
            continue
        sub = item_from_saved_container(
            row.get("key") or "item",
            row.get("description") or row.get("key") or "item",
            row.get("container") or {},
        )
        restored.append(sub)
    item.bag_contents = restored
    raw_wallet = state.get("wallet_contents") or []
    wallet_restored = []
    for row in raw_wallet:
        if not isinstance(row, dict):
            continue
        sub = item_from_saved_container(
            row.get("key") or "item",
            row.get("description") or row.get("key") or "item",
            row.get("container") or {},
        )
        wallet_restored.append(sub)
    item.wallet_contents = wallet_restored


def _restore_body_harvest_fields(item, state):
    """Re-apply corpse harvest flags + Origin stamps from a container blob."""
    if state.get("body_harvested_meat"):
        item.body_harvested_meat = True
    if state.get("body_drained"):
        item.body_drained = True
    if state.get("body_siphoned"):
        item.body_siphoned = True
    if state.get("body_dmb_drawn"):
        item.body_dmb_drawn = True
    if state.get("body_origin"):
        item.body_origin = str(state["body_origin"]).strip().lower()
    if state.get("body_path"):
        item.body_path = str(state["body_path"]).strip().lower()


def _item_container_blob(item):
    """JSON for the items.container column: an Item's locked/loot state (a
    dungeon lockbox's whole reward, world.make_lockbox), same reasoning as
    characters.stats -- one JSON blob means a plain flavor Item (locked=
    False, loot=[]) and a live lockbox round-trip through the same column
    with no schema difference between them. `is_body` (section 6) rides
    the same blob for the same reason -- a body Item is just another Item
    row, no schema change needed. Lodging adds furniture / owner_key / need
    so beds survive a restart with their sleep tag and claim stamp.
    """
    return json.dumps({
        "locked": item.locked,
        "loot": _loot_for_json(item.loot),
        "is_body": item.is_body,
        "is_buried": getattr(item, "is_buried", False),
        "relic": getattr(item, "relic", None),
        "relic_tier": (
            int(item.relic_tier)
            if getattr(item, "relic_tier", None) is not None
            else None
        ),
        "furniture": getattr(item, "furniture", False),
        "owner_key": getattr(item, "owner_key", None),
        "need": getattr(item, "need", None),
        "provides_light": bool(getattr(item, "provides_light", False)),
        "catalog_id": getattr(item, "catalog_id", None),
        "aliases": list(getattr(item, "aliases", None) or []),
        # GM where item (newest copy) -- monotonic stamp from Item.__init__.
        "created_seq": int(getattr(item, "created_seq", 0) or 0),
        # Combat gear fields -- survive logout so equip / mods round-trip.
        "slot": getattr(item, "slot", None),
        "mods": dict(getattr(item, "mods", None) or {})
        if isinstance(getattr(item, "mods", None), dict) else None,
        # God Forge domain: permanent gear-bless tier counter (caps
        # relicforge stacking at gear_bless_max_tier) -- must survive
        # logout or the cap is bypassable by relogging and re-blessing.
        "god_forge_blessed_tier": (
            int(item.god_forge_blessed_tier)
            if getattr(item, "god_forge_blessed_tier", None) else None
        ),
        "god_war_manifest": bool(getattr(item, "god_war_manifest", False)),
        "god_war_owner_key": getattr(item, "god_war_owner_key", None),
        "grip": getattr(item, "grip", None),
        # Host soul-bound angel blade growth (suggestion 215).
        "bound_angel_id": getattr(item, "bound_angel_id", None),
        "angel_blade_growth_tier": (
            int(item.angel_blade_growth_tier)
            if getattr(item, "angel_blade_growth_tier", None) else None
        ),
        "materials": list(getattr(item, "materials", None) or [])
        if getattr(item, "materials", None) else None,
        "color": getattr(item, "color", None),
        "equipped": bool(getattr(item, "equipped", False)),
        # Clothing layer (cosmetic under armor) -- survive logout.
        "layer": getattr(item, "layer", None),
        "worn": bool(getattr(item, "worn", False)),
        "worn_order": (
            int(getattr(item, "worn_order"))
            if getattr(item, "worn_order", None) is not None
            else None
        ),
        "cloth_material": getattr(item, "cloth_material", None),
        "warmth": getattr(item, "warmth", None),
        "cover": (
            [str(c).strip().lower() for c in item.cover if c]
            if isinstance(getattr(item, "cover", None), list)
            else None
        ),
        "conceal": (
            [str(c).strip().lower() for c in item.conceal if c]
            if isinstance(getattr(item, "conceal", None), list)
            else None
        ),
        "dirty": bool(getattr(item, "dirty", False))
        if hasattr(item, "dirty") else None,
        # Home grocery stock window (fridge furniture); None / absent = empty.
        "stock_until_tick": getattr(item, "stock_until_tick", None),
        # Vampire blood-pantry window (separate from mortal food stock).
        "blood_stock_until_tick": getattr(
            item, "blood_stock_until_tick", None
        ),
        # Corpse floor age (Wendigo larder stock gate); absent = unstamped.
        "body_dropped_tick": getattr(item, "body_dropped_tick", None),
        # Corpse harvest flags (supers/scavenge.py) -- one take each.
        "body_harvested_meat": bool(getattr(item, "body_harvested_meat", False)),
        "body_drained": bool(getattr(item, "body_drained", False)),
        "body_siphoned": bool(getattr(item, "body_siphoned", False)),
        "body_dmb_drawn": bool(getattr(item, "body_dmb_drawn", False)),
        "body_origin": getattr(item, "body_origin", None),
        "body_path": getattr(item, "body_path", None),
        # Abandoned floor loot grace (Cadence scavengers); absent = legacy pile.
        "floor_dropped_tick": getattr(item, "floor_dropped_tick", None),
        # Beneath Lucifer's Cage TTL; absent = stamp on next vault decay tick.
        "vault_decay_at_tick": getattr(item, "vault_decay_at_tick", None),
        # Physical phone line id (supers/phone.py); absent = not a phone.
        "phone_number": getattr(item, "phone_number", None),
        "is_phone": bool(getattr(item, "is_phone", False)),
        "is_payphone": bool(getattr(item, "is_payphone", False)),
        "is_ethereal": bool(getattr(item, "is_ethereal", False)),
        "is_spirit_mirror": bool(getattr(item, "is_spirit_mirror", False)),
        "spirit_mirror_source_key": getattr(item, "spirit_mirror_source_key", None),
        # Bela theft contract plants (supers/contracts/runtime.py).
        "_contract_id": getattr(item, "_contract_id", None),
        "_contract_prize": bool(getattr(item, "_contract_prize", False)),
        "_contract_lockbox": bool(getattr(item, "_contract_lockbox", False)),
        "body_equipment": {
            str(slot): {
                "key": getattr(piece, "key", None),
                "created_seq": int(getattr(piece, "created_seq", 0) or 0),
            }
            for slot, piece in (getattr(item, "body_equipment", None) or {}).items()
            if piece is not None
        },
        "body_clothing": {
            str(slot): [
                {
                    "key": getattr(p, "key", None),
                    "created_seq": int(getattr(p, "created_seq", 0) or 0),
                }
                for p in (stack or [])
                if p is not None
            ]
            for slot, stack in (getattr(item, "body_clothing", None) or {}).items()
            if isinstance(stack, list)
        },
        # Colt / charged weapons -- survive logout with chamber count.
        "ammo_charges": (
            int(item.ammo_charges)
            if getattr(item, "ammo_charges", None) is not None
            else None
        ),
        "max_ammo": (
            int(item.max_ammo)
            if getattr(item, "max_ammo", None) is not None
            else None
        ),
        "loaded_ammo_id": getattr(item, "loaded_ammo_id", None),
        "ammo_kind": getattr(item, "ammo_kind", None),
        "dmb_coated": bool(getattr(item, "dmb_coated", False)),
        "lambs_blood_coated": bool(getattr(item, "lambs_blood_coated", False)),
        "dmb_spiked": bool(getattr(item, "dmb_spiked", False)),
        "stack_charges": (
            int(item.stack_charges)
            if getattr(item, "stack_charges", None) is not None
            else None
        ),
        "weapon_voice": getattr(item, "weapon_voice", None),
        "artifact_lexicon": getattr(item, "artifact_lexicon", None),
        # Purgatory pit mimic strongboxes (pose as lockboxes until opened).
        "pit_mimic": bool(getattr(item, "pit_mimic", False)),
        "pit_mimic_tier": (
            int(item.pit_mimic_tier)
            if getattr(item, "pit_mimic_tier", None) is not None
            else None
        ),
        "pit_mimic_floor": (
            int(item.pit_mimic_floor)
            if getattr(item, "pit_mimic_floor", None) is not None
            else None
        ),
        "pit_run_tag": getattr(item, "pit_run_tag", None),
        # Consumable on_use (Purgatory pit sustain potions, etc.).
        "on_use": (
            dict(item.on_use)
            if isinstance(getattr(item, "on_use", None), dict)
            and item.on_use
            else None
        ),
        "purgatory_pit_loot": bool(
            getattr(item, "purgatory_pit_loot", False)
        ),
        "pit_potion_id": getattr(item, "pit_potion_id", None),
        # Herb joints / loaded pipes (supers/herbs.py).
        "herb_id": getattr(item, "herb_id", None),
        "is_joint": bool(getattr(item, "is_joint", False)),
        "is_pipe": bool(getattr(item, "is_pipe", False)),
        "pipe_herb_id": getattr(item, "pipe_herb_id", None),
        "pipe_puffs": (
            int(item.pipe_puffs)
            if getattr(item, "pipe_puffs", None) is not None
            else None
        ),
        "is_bag": bool(getattr(item, "is_bag", False)),
        "is_gear_bag": bool(getattr(item, "is_gear_bag", False)),
        "bag_capacity": (
            int(item.bag_capacity)
            if getattr(item, "bag_capacity", None) is not None
            else None
        ),
        "container_worn": getattr(item, "container_worn", None),
        "bag_contents": _bag_contents_for_json(item),
        "is_wallet": bool(getattr(item, "is_wallet", False)),
        "is_id_card": bool(getattr(item, "is_id_card", False)),
        "wallet_capacity": (
            int(item.wallet_capacity)
            if getattr(item, "wallet_capacity", None) is not None
            else None
        ),
        "wallet_dollars": (
            int(item.wallet_dollars)
            if getattr(item, "wallet_dollars", None) is not None
            else None
        ),
        "wallet_cents": (
            int(item.wallet_cents)
            if getattr(item, "wallet_cents", None) is not None
            else None
        ),
        "wallet_contents": _wallet_contents_for_json(item),
        "legal_id": (
            dict(item.legal_id)
            if isinstance(getattr(item, "legal_id", None), dict)
            else None
        ),
        "gear_condition": (
            int(item.gear_condition)
            if getattr(item, "gear_condition", None) is not None
            else None
        ),
    })


_CHAR_INSERT_SQL = (
    "INSERT INTO characters (name, description, room_key, stats) "
    "VALUES (?, ?, ?, ?)"
)
_ITEM_INSERT_SQL = (
    "INSERT INTO items "
    "(key, description, holder_type, holder_key, container) "
    "VALUES (?, ?, ?, ?, ?)"
)


def _persistable_reseat_body(obj):
    """True when an unroomed body should be parked before skip-save.

    Account-linked Echoes, immersion cast, and essential fixtures must keep
    a SQLite row across cold boot. Hostile NPCs, guests, and parked
    ``gmspirit:`` / ``husk:`` keys stay out of this path.
    """
    if obj is None:
        return False
    if getattr(obj, "is_guest", False) or getattr(obj, "gm_mode", False):
        return False
    key_low = (getattr(obj, "key", None) or "").lower()
    if key_low.startswith("gmspirit:") or key_low.startswith("husk:"):
        return False
    if (getattr(obj, "account", None) or "").strip():
        return True
    if getattr(obj, "immersion", False) or getattr(obj, "essential", False):
        return True
    return False


def _reseat_account_linked_if_unroomed(obj, game):
    """Put a persistable body back on a live map room before snapshot.

    Cold-boot hub heals can leave an Echo or catalog Cast with
    ``location is None`` (or on a Room object that was replaced in
    ``game.rooms``). The next full snapshot then ``DELETE FROM characters``.
    Home / start is enough to survive until deferred unstick runs.
    """
    loc = getattr(obj, "location", None)
    rooms = getattr(game, "rooms", None) or {}
    dest = None
    if loc is not None:
        live = rooms.get(getattr(loc, "key", None))
        if live is loc:
            return loc
        dest = live
    if not _persistable_reseat_body(obj):
        return loc
    if dest is None:
        dest = rooms.get(getattr(obj, "home_room_key", None))
    if dest is None:
        dest = getattr(game, "start_room", None)
    if dest is None or dest is loc:
        return loc
    mover = getattr(obj, "move_to", None)
    if callable(mover) and mover(dest):
        return dest
    return loc


def _should_skip_character_save(obj, game, seen_names):
    """Return True when this live Character must not be written to SQLite."""
    room = _reseat_account_linked_if_unroomed(obj, game)
    if room is None:
        return True
    if getattr(obj, "tutorial_mentor_for", None):
        return True
    if getattr(obj, "pit_merchant", False) or getattr(obj, "pit_run_tag", None):
        return True
    if getattr(obj, "is_guest", False):
        return True
    if getattr(obj, "transient_soul", False):
        return True
    key_low = (getattr(obj, "key", None) or "").lower()
    if getattr(obj, "character_kind", None) == "riftcrash_avatar":
        return True
    if key_low.startswith("riftcrash:"):
        return True
    if getattr(obj, "riftcrash_trash", False):
        return True
    # Account-linked login Echoes must hit SQLite even when a kit/heal left
    # is_npc set. Skipping them on the first cold-boot snapshot DELETE FROM
    # characters's the row. GM-form and spirit keys still skip below.
    account = (getattr(obj, "account", None) or "").strip()
    if (
        account
        and not key_low.startswith("gmspirit:")
        and not key_low.startswith("husk:")
        and not getattr(obj, "gm_mode", False)
        and not getattr(obj, "is_guest", False)
    ):
        save_name = getattr(obj, "key", None) or ""
        if save_name in seen_names:
            print(
                f"[persistence] skip duplicate character key "
                f"{save_name!r} in {getattr(room, 'key', '?')}",
                flush=True,
            )
            return True
        return False
    if key_low.startswith("gmspirit:"):
        permanent = bool(getattr(obj, "gm_spirit_permanent", False))
        if not permanent:
            try:
                from engine.accounts import ensure_accounts_dict
                for acct in ensure_accounts_dict(game).values():
                    if acct.gm_rank not in ("gm", "head_gm"):
                        continue
                    want = (
                        acct.gm_spirit_key
                        or f"gmspirit:{acct.name}"
                    )
                    if want.lower() == key_low:
                        permanent = True
                        break
            except Exception:
                permanent = False
        if not permanent:
            return True
    if (
        getattr(obj, "gm_mode", False)
        and not key_low.startswith("gmspirit:")
    ):
        return True
    # God bilocate twin (supers/god_omnipresence.py): must survive copyover
    # save/reload even though it is an is_npc shell. Key prefix covers
    # stale in-memory flags when copyover saves before overlay bytecode
    # reloads (bug report 152).
    if getattr(obj, "god_twin", False) or key_low.startswith("twin:"):
        pass
    elif (
        obj.is_npc
        and not obj.spar_only
        and not getattr(obj, "peaceful", False)
    ):
        return True
    save_name = getattr(obj, "key", None) or ""
    if save_name in seen_names:
        print(
            f"[persistence] skip duplicate character key "
            f"{save_name!r} in {getattr(room, 'key', '?')}",
            flush=True,
        )
        return True
    return False


def _character_save_rows(game, obj, seen_names):
    """Build INSERT rows for one persistable Character (or None to skip)."""
    # Reseat runs inside skip-save; read location only after that so a
    # None/stale room that just got parked is the key we write.
    if _should_skip_character_save(obj, game, seen_names):
        return None
    room = getattr(obj, "location", None)
    if room is None:
        return None
    save_name = getattr(obj, "key", None) or ""
    seen_names.add(save_name)
    if getattr(obj, "gm_staff_form", False):
        spirit_key = getattr(obj, "gm_spirit_key", None) or (
            f"gmspirit:{obj.key}"
        )
        finder = getattr(game, "find_character", None)
        spirit = finder(spirit_key) if callable(finder) else None
        if spirit is None:
            try:
                from engine import accounts as accounts_mod

                acct = accounts_mod.account_for_character(game, obj)
                if acct is not None and acct.gm_rank in ("gm", "head_gm"):
                    alt = accounts_mod.gm_spirit_key_for_account(acct)
                    if alt and callable(finder):
                        spirit = finder(alt)
            except Exception:
                spirit = None
        spirit_room = getattr(spirit, "location", None) if spirit else None
        if spirit_room is not None and getattr(spirit_room, "key", None):
            obj.gm_spirit_room_key = spirit_room.key
    blob = json.dumps(character_to_blob(obj))
    save_room = room
    if getattr(obj, "djinn_captive", False):
        real = getattr(obj, "djinn_real_room", None)
        real_key = getattr(obj, "djinn_real_room_key", None)
        if real is not None:
            save_room = real
        elif real_key and real_key in game.rooms:
            save_room = game.rooms[real_key]
    elif getattr(room, "djinn_instance_id", None):
        ret = getattr(obj, "djinn_mirage_return_room", None)
        if ret is not None:
            save_room = ret
    char_row = (obj.key, obj.description, save_room.key, blob)
    item_rows = []
    for item in obj.inventory:
        item_rows.append((
            item.key, item.description, "character", obj.key,
            _item_container_blob(item),
        ))
    for item in list(getattr(obj, "gear_bag", None) or []):
        item_rows.append((
            item.key, item.description, "gear", obj.key,
            _item_container_blob(item),
        ))
    return char_row, item_rows


def _persistable_floor_item(obj):
    """True when a loose room Item should be written on world save."""
    if not isinstance(obj, Item):
        return False
    if getattr(obj, "djinn_husk", False):
        return False
    if getattr(obj, "ephemeral_spawn_body", False):
        return False
    if getattr(obj, "decay_at_tick", None) is not None:
        return False
    return True


def rebuild_floor_item_room_index(game):
    """One O(rooms) pass after boot -- then maintain via Room.add/remove."""
    keys = set()
    for room in (getattr(game, "rooms", None) or {}).values():
        if not room.contents:
            continue
        for obj in room.contents:
            if _persistable_floor_item(obj):
                key = getattr(room, "key", None)
                if key:
                    keys.add(key)
                break
    game._floor_item_room_keys = keys


def note_floor_item_room(game, room):
    """Stamp ``room.key`` when a persistable floor Item lands."""
    if room is None or game is None:
        return
    keys = getattr(game, "_floor_item_room_keys", None)
    if keys is None:
        return
    key = getattr(room, "key", None)
    if key:
        keys.add(key)
    mark_floor_room_dirty(game, room)


def release_floor_item_room(game, room):
    """Drop ``room.key`` when no persistable floor Items remain."""
    if room is None or game is None:
        return
    keys = getattr(game, "_floor_item_room_keys", None)
    if keys is None:
        return
    key = getattr(room, "key", None)
    if not key or key not in keys:
        return
    for obj in room.contents:
        if _persistable_floor_item(obj):
            return
    keys.discard(key)
    mark_floor_room_dirty(game, room)


def _iter_floor_item_rooms(game):
    """Rooms that may hold persistable floor Items (indexed when available)."""
    rooms = getattr(game, "rooms", None) or {}
    keys = getattr(game, "_floor_item_room_keys", None)
    if keys is not None:
        for key in keys:
            room = rooms.get(key)
            if room is None:
                from engine import hooks
                room = hooks.demesne_lookup_room_for_persist(game, key)
            if room is not None:
                yield room
        return
    for room in rooms.values():
        if room.contents:
            yield room
    from engine import hooks
    for room in hooks.demesne_iter_micro_rooms(game):
        if room.contents:
            yield room


def _room_floor_item_rows(game):
    """Loose floor Items as INSERT tuples (indexed room walk when possible)."""
    rows = []
    for room in _iter_floor_item_rooms(game):
        for obj in room.contents:
            if not _persistable_floor_item(obj):
                continue
            rows.append((
                obj.key, obj.description, "room", room.key,
                _item_container_blob(obj),
            ))
    return rows


def _snapshot_hash_adler32(parts):
    """Combine adler32 hashes of serialized snapshot parts (stdlib only)."""
    acc = 0
    for part in parts:
        if isinstance(part, str):
            data = part.encode("utf-8")
        elif isinstance(part, bytes):
            data = part
        else:
            data = repr(part).encode("utf-8")
        acc = zlib.adler32(data, acc)
    return acc


def _char_snapshot_hash(char_row, owned_items):
    """Fingerprint one character row plus owned/gear items."""
    return _snapshot_hash_adler32([char_row[3], owned_items])


def _floor_room_snapshot_hash(room_rows):
    """Fingerprint loose floor items in one room (empty room -> 0)."""
    if not room_rows:
        return 0
    return _snapshot_hash_adler32(room_rows)


def _persist_stamp_dirty_write_gen(game, key, *, floor=False):
    """Record which dirty-write epoch last queued this key.

    Collect bumps the epoch, then the writer-thread ack drops a written
    key only when its stamp is still at or before the snapshot's epoch.
    Cadence re-marking the same Echo during SQLite apply gets a newer
    stamp and survives -- otherwise town life during P30 apply would
    silently lose meters and inventory.
    """
    if game is None or not key:
        return
    epoch = int(getattr(game, "_persist_dirty_write_epoch", 0) or 0)
    attr = (
        "_persist_dirty_floor_write_gen" if floor
        else "_persist_dirty_char_write_gen"
    )
    gens = getattr(game, attr, None)
    if gens is None:
        gens = {}
        setattr(game, attr, gens)
    gens[key] = epoch


def _persist_begin_dirty_write_epoch(game):
    """Snapshot the ack epoch, then bump so later marks outlive this save."""
    if game is None:
        return 0
    ack_epoch = int(getattr(game, "_persist_dirty_write_epoch", 0) or 0)
    game._persist_dirty_write_epoch = ack_epoch + 1
    return ack_epoch


def mark_character_dirty(game, character, *, force=False):
    """Queue one character for the next incremental world save.

    Offline bodies (Echoes and Cadence NPCs) used to re-enter the dirty set
    every heartbeat when needs decay moved a float meter, keeping the dirty-
    char cap saturated on quiet servers (lag P28). Coalesce their non-
    ``force`` dirties to once per autosave generation; online sessions and
    ``force=True`` hard mutations always queue immediately.
    """
    if game is None or character is None:
        return
    key = getattr(character, "key", None)
    if not key:
        return
    if not force:
        session = getattr(character, "session", None)
        is_online = session is not None and not getattr(session, "silent", False)
        if not is_online:
            generation = int(
                getattr(game, "_persist_echo_dirty_generation", 0) or 0,
            )
            last_marked = int(
                getattr(character, "_persist_dirty_generation", -1),
            )
            if last_marked == generation:
                skipped = int(
                    getattr(game, "_persist_echo_coalesce_skipped", 0) or 0,
                )
                game._persist_echo_coalesce_skipped = skipped + 1
                # Still stamp the writer-ack generation: the key is already
                # in the dirty set, and Cadence will re-touch it during P30
                # apply. Without the stamp the ack would drop the live
                # mutation with the snapshot it just wrote.
                _persist_stamp_dirty_write_gen(game, key)
                return
            shards = persist_offline_dirty_shards()
            if not _offline_dirty_shard_allows(key, generation, shards):
                character._persist_dirty_generation = generation
                skipped = int(
                    getattr(game, "_persist_echo_coalesce_skipped", 0) or 0,
                )
                game._persist_echo_coalesce_skipped = skipped + 1
                dirty_now = getattr(game, "_persist_dirty_characters", None)
                if dirty_now and key in dirty_now:
                    _persist_stamp_dirty_write_gen(game, key)
                return
            character._persist_dirty_generation = generation
    dirty = getattr(game, "_persist_dirty_characters", None)
    if dirty is None:
        dirty = set()
        game._persist_dirty_characters = dirty
    dirty.add(key)
    _persist_stamp_dirty_write_gen(game, key)


# Display / checkpoint verbs that do not change the persisted character blob.
# Games may extend via ``register_readonly_persist_verbs`` at boot.
_READONLY_PERSIST_VERBS = frozenset({
    "look", "l",
    "exits",
    "examine", "exa", "ex",
    "map", "bigmap",
    "who",
    "time", "timeformat", "date",
    "help",
    "commands",
    "changes",
    "more", "stop",
    "inventory", "inv", "i",
    "save",
})
_extra_readonly_persist_verbs = set()

# Wall-clock cooldown between player ``save`` commands (anti-spam).
PLAYER_SAVE_COOLDOWN_SEC = 45.0


def register_readonly_persist_verbs(verbs):
    """Register game-specific display verbs that should not queue autosave."""
    _extra_readonly_persist_verbs.update(
        str(v).strip().lower() for v in verbs if str(v).strip()
    )


def command_marks_character_dirty(verb, args=""):
    """True when a dispatched verb should queue the actor for autosave."""
    v = (verb or "").strip().lower()
    if not v:
        return False
    if v in _READONLY_PERSIST_VERBS or v in _extra_readonly_persist_verbs:
        return False
    return True


def persist_save_character(conn, game, character, *, player_checkpoint=False):
    """Write one live character (+ inventory/gear) immediately.

    Used by the player ``save`` verb: flushes the body now and clears it
    from the dirty queue so the next incremental autosave can skip them
    until they mutate again.

    When ``player_checkpoint`` is True (player ``save`` only), also archives
    a JSON recovery copy under ``backups/player-checkpoints/``.
    """
    if conn is None or game is None or character is None:
        return False, "Nothing to save."
    seen_names = set()
    payload = _character_save_rows(game, character, seen_names)
    if payload is None:
        return False, "Your character cannot be saved right now."
    char_row, item_rows = payload
    name = char_row[0]
    digest = _char_snapshot_hash(char_row, item_rows)
    meta = {
        "force_full": False,
        "changed_chars": [name],
        "removed_chars": [],
        "changed_floor_rooms": [],
    }
    try:
        _apply_world_save_snapshot(conn, [char_row], item_rows, meta)
    except sqlite3.Error as exc:
        return False, f"Save failed ({exc}). Try again in a moment."
    merged = dict(getattr(game, "_persist_char_snapshot_hashes", None) or {})
    merged[name] = digest
    game._persist_char_snapshot_hashes = merged
    game._persist_warm = True
    dirty = getattr(game, "_persist_dirty_characters", None)
    if dirty is not None:
        dirty.discard(name)
    if player_checkpoint:
        try:
            from engine import player_save_backup as psb

            root = getattr(game, "report_dir", None) or psb._repo_root()
            psb.archive_player_checkpoint(char_row, item_rows, root=root)
        except Exception as exc:
            print(
                f"[player_checkpoint] archive skipped for {name!r}: {exc!r}",
                flush=True,
            )
    return True, "Your progress is saved."


def mark_floor_room_dirty(game, room):
    """Queue loose floor items in ``room`` for the next incremental save."""
    if game is None or room is None:
        return
    key = getattr(room, "key", None)
    if not key:
        return
    dirty = getattr(game, "_persist_dirty_floor_rooms", None)
    if dirty is None:
        dirty = set()
        game._persist_dirty_floor_rooms = dirty
    dirty.add(key)
    _persist_stamp_dirty_write_gen(game, key, floor=True)


def _persist_clear_dirty(game):
    """Drop dirty queues after a successful *synchronous* world save.

    Writer-path ack uses ``_persist_ack_written_dirty`` instead -- a
    blind wipe here would drop keys Cadence marked while SQLite applied
    on the background thread.
    """
    if game is None:
        return
    game._persist_dirty_characters = set()
    game._persist_dirty_floor_rooms = set()
    game._persist_force_full = False


def _persist_drop_acked_dirty(dirty, written, gens, ack_epoch):
    """Drop written keys whose stamp is still at or before ``ack_epoch``.

    Keys re-dirtied after collect have a newer stamp and stay queued.
    """
    if dirty is None or not written:
        return
    drop = set()
    for key in written:
        if key not in dirty:
            continue
        gen = 0
        if gens is not None:
            try:
                gen = int(gens.get(key, 0) or 0)
            except (TypeError, ValueError):
                gen = 0
        if gen <= ack_epoch:
            drop.add(key)
    if not drop:
        return
    dirty.difference_update(drop)
    if gens:
        for key in drop:
            gens.pop(key, None)


def _persist_ack_written_dirty(game, meta, char_rows):
    """Remove only keys this batch wrote; keep dirties marked during apply.

    Collect leaves the dirty sets intact until ack. A full wipe-and-restore
    of deferred keys dropped anything Cadence/commands queued while the
    writer thread held SQLite -- the P30 "Cadence runs during apply" path
    would silently lose those saves.

    Keys that were in the snapshot *and* re-dirtied during apply keep
    their dirty bit via ``_persist_dirty_*_write_gen`` (P30.2). Rolling
    deploys whose in-flight meta has no ``ack_epoch`` fall back to the
    older difference_update (new keys survive; re-dirtied snapshot keys
    do not).
    """
    if game is None:
        return
    written_chars = set(meta.get("changed_chars") or ())
    written_chars.update(meta.get("removed_chars") or ())
    if meta.get("force_full"):
        written_chars.update(row[0] for row in (char_rows or ()) if row)
    written_rooms = set(meta.get("changed_floor_rooms") or ())
    if "ack_epoch" not in meta:
        dirty_c = getattr(game, "_persist_dirty_characters", None)
        if dirty_c is not None and written_chars:
            dirty_c.difference_update(written_chars)
        dirty_r = getattr(game, "_persist_dirty_floor_rooms", None)
        if dirty_r is not None and written_rooms:
            dirty_r.difference_update(written_rooms)
    else:
        try:
            ack_epoch = int(meta.get("ack_epoch") or 0)
        except (TypeError, ValueError):
            ack_epoch = 0
        _persist_drop_acked_dirty(
            getattr(game, "_persist_dirty_characters", None),
            written_chars,
            getattr(game, "_persist_dirty_char_write_gen", None),
            ack_epoch,
        )
        _persist_drop_acked_dirty(
            getattr(game, "_persist_dirty_floor_rooms", None),
            written_rooms,
            getattr(game, "_persist_dirty_floor_write_gen", None),
            ack_epoch,
        )
    if meta.get("force_full"):
        game._persist_force_full = False


def _persist_should_skip_world_save(game):
    """True when incremental autosave has nothing to write."""
    if not _persist_snapshot_hashes_ready(game):
        return False
    if getattr(game, "_persist_force_full", False):
        return False
    dirty_c = getattr(game, "_persist_dirty_characters", None) or set()
    dirty_f = getattr(game, "_persist_dirty_floor_rooms", None) or set()
    return not dirty_c and not dirty_f


def _persist_snapshot_hashes_ready(game):
    """True once a successful save stamped snapshot fingerprints."""
    return bool(getattr(game, "_persist_warm", False))


def persist_seed_hashes_on_load_enabled():
    """When true (default), boot stamps snapshot hashes after ``load_world``."""
    raw = (os.environ.get("RIFTFORGE_PERSIST_SEED_HASHES_ON_LOAD") or "1").strip()
    return raw.lower() not in ("0", "false", "no", "off")


def seed_snapshot_hashes_after_load(game):
    """Fingerprint the loaded world so the first autosave can stay incremental.

    ``load_world`` clears warm hashes; without this pass every game-only
    restart forced a full-verify autosave (or rewrote ~100+ bodies while
    hash caches re-warmed). One boot-time collect matches on-disk state;
    later saves only touch ``mark_character_dirty`` bodies.
    """
    if game is None or not persist_seed_hashes_on_load_enabled():
        return
    if _persist_snapshot_hashes_ready(game):
        return
    from engine.char_index import iter_characters

    seen_names = set()
    char_hashes = {}
    for obj in iter_characters(game):
        payload = _character_save_rows(game, obj, seen_names)
        if payload is None:
            continue
        char_row, owned_items = payload
        char_hashes[char_row[0]] = _char_snapshot_hash(char_row, owned_items)

    floor_hashes = {}
    for room in _iter_floor_item_rooms(game):
        room_key = getattr(room, "key", None)
        if not room_key:
            continue
        room_rows = []
        for obj in room.contents:
            if not _persistable_floor_item(obj):
                continue
            room_rows.append((
                obj.key, obj.description, "room", room_key,
                _item_container_blob(obj),
            ))
        floor_hashes[room_key] = _floor_room_snapshot_hash(room_rows)

    game._persist_char_snapshot_hashes = char_hashes
    game._persist_floor_room_hashes = floor_hashes
    game._persist_warm = True
    game._persist_force_full = False


def _persist_store_snapshot_hashes(game, meta):
    """Remember last-written snapshot fingerprints for incremental autosave."""
    if game is None or not meta:
        return
    new_char = meta.get("new_char_hashes")
    new_floor = meta.get("new_floor_hashes")
    if new_char is not None:
        merged = dict(getattr(game, "_persist_char_snapshot_hashes", None) or {})
        merged.update(new_char)
        game._persist_char_snapshot_hashes = merged
    if new_floor is not None:
        merged = dict(getattr(game, "_persist_floor_room_hashes", None) or {})
        merged.update(new_floor)
        game._persist_floor_room_hashes = merged
    game._persist_warm = True


def _collect_character_save_pass(
    game, seen_names, prev_char_hashes, force_full, dirty_chars,
):
    """Build character/item rows for dirty bodies (skip clean when warm).

    When warm and not force_full, skip ``_character_save_rows`` entirely for
    non-dirty bodies -- serializing every Echo just to discard the blob was
    the bulk of collect_ms on live (~1s).
    """
    from engine.char_index import iter_characters

    char_rows = []
    item_rows = []
    alive_names = set()
    changed_chars = []
    new_char_hashes = {}
    for obj in iter_characters(game):
        name = (getattr(obj, "key", None) or getattr(obj, "name", None) or "")
        name = str(name)
        if not name:
            continue
        # Cheap alive scan -- do not build JSON for clean bodies.
        if not force_full and name not in dirty_chars:
            prev_hash = (prev_char_hashes or {}).get(name)
            if prev_hash is not None:
                alive_names.add(name)
                new_char_hashes[name] = prev_hash
                continue
            # Never hashed and not selected this pass (e.g. deferred dirty
            # backlog). Keep alive without a row; next dirty slice or full
            # verify will write them.
            alive_names.add(name)
            continue
        payload = _character_save_rows(game, obj, seen_names)
        if payload is None:
            continue
        char_row, owned_items = payload
        name = char_row[0]
        alive_names.add(name)
        digest = _char_snapshot_hash(char_row, owned_items)
        new_char_hashes[name] = digest
        char_rows.append(char_row)
        item_rows.extend(owned_items)
        if not force_full:
            changed_chars.append(name)
    removed_chars = []
    if not force_full and prev_char_hashes:
        removed_chars = sorted(set(prev_char_hashes.keys()) - alive_names)
    return char_rows, item_rows, changed_chars, removed_chars, new_char_hashes


def _collect_floor_save_pass(game, prev_floor_hashes, force_full, dirty_rooms):
    """Build floor-item rows for rooms whose loose loot changed."""
    item_rows = []
    changed_floor_rooms = []
    new_floor_hashes = {}
    for room in _iter_floor_item_rooms(game):
        room_key = getattr(room, "key", None)
        if not room_key:
            continue
        if not force_full and room_key not in dirty_rooms:
            prev_digest = (prev_floor_hashes or {}).get(room_key)
            if prev_digest is not None:
                new_floor_hashes[room_key] = prev_digest
                continue
        room_rows = []
        for obj in room.contents:
            if not _persistable_floor_item(obj):
                continue
            room_rows.append((
                obj.key, obj.description, "room", room_key,
                _item_container_blob(obj),
            ))
        digest = _floor_room_snapshot_hash(room_rows)
        new_floor_hashes[room_key] = digest
        if force_full:
            item_rows.extend(room_rows)
            continue
        if (
            room_key in dirty_rooms
            or prev_floor_hashes.get(room_key) != digest
        ):
            changed_floor_rooms.append(room_key)
            item_rows.extend(room_rows)
    return item_rows, changed_floor_rooms, new_floor_hashes


async def _collect_floor_save_pass_async(
    game, prev_floor_hashes, force_full, dirty_rooms, *, yield_every=None,
):
    """Cooperative floor-item snapshot -- yield while scanning many rooms."""
    import asyncio

    if yield_every is None:
        yield_every = persist_save_yield_every()
    item_rows = []
    changed_floor_rooms = []
    new_floor_hashes = {}
    n = 0
    for room in _iter_floor_item_rooms(game):
        room_key = getattr(room, "key", None)
        if not room_key:
            continue
        if not force_full and room_key not in dirty_rooms:
            prev_digest = (prev_floor_hashes or {}).get(room_key)
            if prev_digest is not None:
                new_floor_hashes[room_key] = prev_digest
                n += 1
                if yield_every and n % yield_every == 0:
                    await asyncio.sleep(0)
                continue
        room_rows = []
        for obj in room.contents:
            if not _persistable_floor_item(obj):
                continue
            room_rows.append((
                obj.key, obj.description, "room", room_key,
                _item_container_blob(obj),
            ))
        digest = _floor_room_snapshot_hash(room_rows)
        new_floor_hashes[room_key] = digest
        if force_full:
            item_rows.extend(room_rows)
        elif (
            room_key in dirty_rooms
            or prev_floor_hashes.get(room_key) != digest
        ):
            changed_floor_rooms.append(room_key)
            item_rows.extend(room_rows)
        n += 1
        if yield_every and n % yield_every == 0:
            await asyncio.sleep(0)
    return item_rows, changed_floor_rooms, new_floor_hashes


def _persist_dirty_priority_key(game, char_key):
    """Sort key: online (0) < recently active (1) < everyone else (2).

    Lower sorts first so the dirty-char cap always drains live sessions
    before cold offline Echoes. Ties within a tier fall back to ``char_key``
    for the same determinism the old plain alphabetical sort gave tests.
    """
    find = getattr(game, "find_character", None)
    char = find(char_key) if callable(find) else None
    if char is None:
        return (2, char_key)
    session = getattr(char, "session", None)
    if session is not None and not getattr(session, "silent", False):
        return (0, char_key)
    last_active = float(getattr(char, "last_active_at", 0.0) or 0.0)
    if last_active and (time.time() - last_active) <= _PERSIST_DIRTY_RECENT_ACTIVE_S:
        return (1, char_key)
    return (2, char_key)


def _persist_cap_dirty_sets(game, force_full):
    """Return (dirty_chars, dirty_rooms, deferred_chars, deferred_rooms).

    When not force_full, take only the first N dirty keys (priority-sorted
    for characters, alphabetical for rooms) and leave the rest queued for
    the next autosave. Full verify passes ignore the cap -- they rewrite
    everything.
    """
    dirty_chars = set(getattr(game, "_persist_dirty_characters", None) or set())
    dirty_rooms = set(getattr(game, "_persist_dirty_floor_rooms", None) or set())
    if force_full:
        return dirty_chars, dirty_rooms, set(), set()
    char_cap = persist_dirty_char_cap(game)
    room_cap = persist_dirty_room_cap(game)
    char_sorted = sorted(
        dirty_chars, key=lambda k: _persist_dirty_priority_key(game, k),
    )
    room_sorted = sorted(dirty_rooms)
    take_chars = set(char_sorted[:char_cap])
    take_rooms = set(room_sorted[:room_cap])
    deferred_chars = set(char_sorted[char_cap:])
    deferred_rooms = set(room_sorted[room_cap:])
    return take_chars, take_rooms, deferred_chars, deferred_rooms


def _persist_bump_echo_generation(game):
    """Advance Echo coalesce generation after a successful world save."""
    if game is None:
        return
    game._persist_echo_coalesce_skipped = 0
    game._persist_echo_dirty_generation = int(
        getattr(game, "_persist_echo_dirty_generation", 0) or 0,
    ) + 1


def _persist_restore_deferred_dirty(game, deferred_chars, deferred_rooms):
    """Re-queue dirty keys that this save did not rewrite."""
    if game is None:
        return
    if deferred_chars:
        dirty = getattr(game, "_persist_dirty_characters", None)
        if dirty is None:
            dirty = set()
            game._persist_dirty_characters = dirty
        dirty.update(deferred_chars)
    if deferred_rooms:
        dirty = getattr(game, "_persist_dirty_floor_rooms", None)
        if dirty is None:
            dirty = set()
            game._persist_dirty_floor_rooms = dirty
        dirty.update(deferred_rooms)


def _collect_world_save_snapshot(game):
    """In-memory snapshot; skips unchanged characters/rooms when hashes warm."""
    _persist_apply_scheduled_world_full(game)
    prev_char = getattr(game, "_persist_char_snapshot_hashes", None)
    prev_floor = getattr(game, "_persist_floor_room_hashes", None)
    force_full = (
        not _persist_snapshot_hashes_ready(game)
        or bool(getattr(game, "_persist_force_full", False))
    )
    dirty_chars, dirty_rooms, deferred_chars, deferred_rooms = (
        _persist_cap_dirty_sets(game, force_full)
    )

    seen_names = set()
    char_rows, item_rows, changed_chars, removed_chars, new_char_hashes = (
        _collect_character_save_pass(
            game, seen_names, prev_char or {}, force_full, dirty_chars,
        )
    )
    floor_rows, changed_floor, new_floor_hashes = _collect_floor_save_pass(
        game, prev_floor or {}, force_full, dirty_rooms,
    )
    item_rows.extend(floor_rows)
    meta = {
        "force_full": force_full,
        "changed_chars": changed_chars,
        "removed_chars": removed_chars,
        "changed_floor_rooms": changed_floor,
        "new_char_hashes": new_char_hashes,
        "new_floor_hashes": new_floor_hashes,
        "deferred_chars": deferred_chars,
        "deferred_rooms": deferred_rooms,
        "n_dirty_chars_queued": len(
            getattr(game, "_persist_dirty_characters", None) or ()
        ),
        "n_dirty_rooms_queued": len(
            getattr(game, "_persist_dirty_floor_rooms", None) or ()
        ),
        "n_dirty_chars_this_pass": len(dirty_chars) if not force_full else -1,
        "n_dirty_rooms_this_pass": len(dirty_rooms) if not force_full else -1,
    }
    return char_rows, item_rows, meta


async def _collect_world_save_snapshot_async(
    game, *, yield_every=None, wall_start=None, wall_budget_ms=0,
):
    """Cooperative snapshot build -- yields so player commands can run."""
    import asyncio
    from engine.char_index import iter_characters

    if yield_every is None:
        yield_every = persist_save_yield_every()

    _persist_apply_scheduled_world_full(game)
    prev_char = getattr(game, "_persist_char_snapshot_hashes", None)
    prev_floor = getattr(game, "_persist_floor_room_hashes", None)
    force_full = (
        not _persist_snapshot_hashes_ready(game)
        or bool(getattr(game, "_persist_force_full", False))
    )
    dirty_chars, dirty_rooms, deferred_chars, deferred_rooms = (
        _persist_cap_dirty_sets(game, force_full)
    )

    # Yielding collect: walk characters in slices so a full-verify pass
    # does not pin the asyncio loop for the whole roster serialization.
    seen_names = set()
    char_rows = []
    item_rows = []
    alive_names = set()
    changed_chars = []
    new_char_hashes = {}
    n = 0
    wall_budget_hit = False
    last_yield = time.perf_counter()
    yield_ms = persist_collect_yield_ms()
    # Lag P12: name the specific character(s) whose blob build is slow
    # instead of only knowing the aggregate collect_ms (docs/plans/
    # lag_p12_collect_profile.md). Cheap -- one perf_counter() pair per
    # dirty character, only kept for the handful that actually run
    # _character_save_rows (clean bodies stay on the cheap hash-skip path
    # above and are never timed).
    slow_chars = []
    char_build_total_ms = 0.0
    char_build_count = 0
    # Incremental removed_chars is prev_hashes - alive_names. That is only
    # safe after a complete roster walk. Breaking mid-loop leaves unvisited
    # live bodies out of alive_names; treating them as deleted then wipes
    # their SQLite rows. A later crash / game-only restart loads the hole.
    scan_incomplete = False
    for obj in iter_characters(game):
        if wall_start is not None and _persist_save_over_wall_budget(
            wall_start, wall_budget_ms,
        ):
            wall_budget_hit = True
            if force_full:
                # Never apply a partial full-verify wipe.
                game._persist_force_full = True
                meta = {
                    "skipped_wall_budget": True,
                    "force_full": force_full,
                    "deferred_chars": sorted(dirty_chars | deferred_chars),
                    "deferred_rooms": sorted(dirty_rooms | deferred_rooms),
                    "scan_incomplete": True,
                }
                return [], [], meta
            deferred_chars.update(dirty_chars - set(changed_chars))
            scan_incomplete = True
            break
        name = (getattr(obj, "key", None) or getattr(obj, "name", None) or "")
        name = str(name)
        if not name:
            continue
        if not force_full and name not in dirty_chars:
            prev_hash = (prev_char or {}).get(name)
            if prev_hash is not None:
                alive_names.add(name)
                new_char_hashes[name] = prev_hash
                continue
            alive_names.add(name)
            continue
        t_char = time.perf_counter()
        payload = _character_save_rows(game, obj, seen_names)
        char_ms = (time.perf_counter() - t_char) * 1000.0
        char_build_total_ms += char_ms
        char_build_count += 1
        if char_ms >= _PERSIST_SLOW_CHAR_LOG_MS:
            slow_chars.append((name, round(char_ms, 2)))
        if payload is None:
            continue
        char_row, owned_items = payload
        name = char_row[0]
        alive_names.add(name)
        digest = _char_snapshot_hash(char_row, owned_items)
        new_char_hashes[name] = digest
        char_rows.append(char_row)
        item_rows.extend(owned_items)
        if not force_full:
            changed_chars.append(name)
        n += 1
        due_count = yield_every and n % yield_every == 0
        due_time = (
            yield_ms > 0
            and (time.perf_counter() - last_yield) * 1000.0 >= yield_ms
        )
        if due_count or due_time:
            await asyncio.sleep(0)
            last_yield = time.perf_counter()
    slow_chars.sort(key=lambda pair: pair[1], reverse=True)
    removed_chars = []
    if not force_full and prev_char and not scan_incomplete:
        removed_chars = sorted(set(prev_char.keys()) - alive_names)

    if (
        not wall_budget_hit
        and wall_start is not None
        and _persist_save_over_wall_budget(wall_start, wall_budget_ms)
    ):
        wall_budget_hit = True
        if not force_full:
            deferred_chars.update(dirty_chars - set(changed_chars))

    floor_rows, changed_floor, new_floor_hashes = (
        await _collect_floor_save_pass_async(
            game, prev_floor or {}, force_full, dirty_rooms,
            yield_every=yield_every,
        )
        if not wall_budget_hit
        else ([], [], {})
    )
    item_rows.extend(floor_rows)
    await asyncio.sleep(0)
    meta = {
        "force_full": force_full,
        "changed_chars": changed_chars,
        "removed_chars": removed_chars,
        "changed_floor_rooms": changed_floor,
        "new_char_hashes": new_char_hashes,
        "new_floor_hashes": new_floor_hashes,
        "deferred_chars": deferred_chars,
        "deferred_rooms": deferred_rooms,
        "wall_budget_hit": wall_budget_hit,
        "scan_incomplete": scan_incomplete,
        "n_dirty_chars_queued": len(
            getattr(game, "_persist_dirty_characters", None) or ()
        ),
        "n_dirty_rooms_queued": len(
            getattr(game, "_persist_dirty_floor_rooms", None) or ()
        ),
        "n_dirty_chars_this_pass": len(dirty_chars) if not force_full else -1,
        "n_dirty_rooms_this_pass": len(dirty_rooms) if not force_full else -1,
        # Lag P12 breadcrumbs -- see _PERSIST_SLOW_CHAR_LOG_MS above.
        "slow_chars": slow_chars[:_PERSIST_SLOW_CHAR_LOG_CAP],
        "char_build_total_ms": round(char_build_total_ms, 2),
        "char_build_count": char_build_count,
        "char_build_avg_ms": (
            round(char_build_total_ms / char_build_count, 2)
            if char_build_count
            else 0.0
        ),
    }
    return char_rows, item_rows, meta


def _apply_world_save_snapshot(conn, char_rows, item_rows, meta=None):
    """Bulk-insert snapshot rows (full wipe or incremental dirty pass)."""
    meta = meta or {"force_full": True}
    force_full = bool(meta.get("force_full"))
    last_err = None
    for attempt in range(2):
        try:
            with conn:
                if force_full:
                    conn.execute("DELETE FROM characters")
                    conn.execute("DELETE FROM items")
                else:
                    # Incomplete incremental walks must never DELETE
                    # unvisited bodies (see scan_incomplete in collect).
                    removed = () if meta.get("scan_incomplete") else meta.get(
                        "removed_chars", (),
                    )
                    for name in removed:
                        conn.execute(
                            "DELETE FROM characters WHERE name=?", (name,),
                        )
                        conn.execute(
                            "DELETE FROM items WHERE holder_key=? "
                            "AND holder_type IN ('character', 'gear')",
                            (name,),
                        )
                    for name in meta.get("changed_chars", ()):
                        conn.execute(
                            "DELETE FROM characters WHERE name=?", (name,),
                        )
                        conn.execute(
                            "DELETE FROM items WHERE holder_key=? "
                            "AND holder_type IN ('character', 'gear')",
                            (name,),
                        )
                    for room_key in meta.get("changed_floor_rooms", ()):
                        conn.execute(
                            "DELETE FROM items WHERE holder_key=? "
                            "AND holder_type='room'",
                            (room_key,),
                        )
                if char_rows:
                    conn.executemany(_CHAR_INSERT_SQL, char_rows)
                if item_rows:
                    conn.executemany(_ITEM_INSERT_SQL, item_rows)
            return
        except sqlite3.OperationalError as err:
            last_err = err
            msg = str(err).lower()
            if attempt == 0 and "disk i/o" in msg:
                time.sleep(0.05)
                continue
            raise
    if last_err is not None:
        raise last_err


async def _apply_force_full_snapshot_async(
    conn, char_rows, item_rows, *, yield_every,
):
    """Cooperative full-verify apply for ``save_world_async`` only.

    Fires on the *first* autosave after every game-only restart (hash
    cache is cold, so ``force_full`` is true) and every
    ``persist_full_every()`` passes after that. Yields every ``ye``
    (``yield_every``, default 2) rows for responsiveness, but -- lag
    P11.3 continued -- used to also *commit* (fsync) every ``ye`` rows,
    which for a live-sized world (~400 characters + ~5,000+ items) meant
    roughly (400+5000)/2 ~= 2,700 individual commits in one pass, clocked
    at 62s on live right after the 2026-08-20 P11.3 deploy (this path
    fires on every restart, so it wasn't covered by the incremental-path
    fix above). Commits now batch at ``persist_apply_commit_batch()``
    (default 25) independently of the yield cadence, matching the
    incremental path.
    """
    import asyncio

    ye = max(1, int(yield_every or 1))
    commit_batch = max(1, persist_apply_commit_batch())
    with conn:
        conn.execute("DELETE FROM characters")
        conn.execute("DELETE FROM items")
    await asyncio.sleep(0)

    async def _apply_rows(rows, insert_sql):
        n_since_commit = 0
        for i in range(0, len(rows), ye):
            batch = rows[i : i + ye]
            if not batch:
                continue
            conn.executemany(insert_sql, batch)
            n_since_commit += len(batch)
            if n_since_commit >= commit_batch:
                conn.commit()
                n_since_commit = 0
            await asyncio.sleep(0)
        if n_since_commit:
            conn.commit()

    try:
        await _apply_rows(char_rows, _CHAR_INSERT_SQL)
        await _apply_rows(item_rows, _ITEM_INSERT_SQL)
    finally:
        # Best-effort flush of anything left uncommitted on an exception
        # mid-pass -- mirrors the incremental path's safety net.
        conn.commit()


async def _apply_world_save_snapshot_async(
    conn, char_rows, item_rows, meta=None, *, yield_every=None,
):
    """Incremental apply with yields; full-verify batches INSERTs.

    Incremental dirty passes yield periodically so the asyncio loop can
    serve player commands between SQLite work, and commit once every
    ``persist_apply_commit_batch()`` characters/rooms rather than once per
    character (lag P11.3) -- each commit is a separate fsync under the
    default DELETE journal mode, and committing per-character made routine
    autosave ``apply_ms`` cost 3.5-6s whenever ~100+ characters were dirty
    (common right after a game-only restart, before hash caches warm back
    up). The transaction stays open across ``await asyncio.sleep(0)``
    yields between commits -- safe here because SUPERS is single-writer
    (hard rule 3) and this is the only coroutine touching ``conn`` for
    world-save writes.

    Cooperative full-verify (``save_world_async`` only): wipe tables once,
    then batch INSERT with yields. Copyover / shutdown still use sync
    ``save()`` which keeps one atomic transaction via
    ``_apply_world_save_snapshot``.
    """
    import asyncio

    meta = meta or {"force_full": True}
    force_full = bool(meta.get("force_full"))
    yield_every = (
        persist_save_yield_every() if yield_every is None else int(yield_every)
    )
    if force_full:
        await _apply_force_full_snapshot_async(
            conn, char_rows, item_rows, yield_every=yield_every,
        )
        return

    # Batch DELETE+INSERT per changed character / floor room.
    char_by_name = {row[0]: row for row in char_rows}
    # Items: character/gear vs room floor.
    items_by_holder = {}
    floor_by_room = {}
    for row in item_rows:
        # row = (key, desc, holder_type, holder_key, blob)
        holder_type = row[2]
        holder_key = row[3]
        if holder_type in ("character", "gear"):
            items_by_holder.setdefault(holder_key, []).append(row)
        elif holder_type == "room":
            floor_by_room.setdefault(holder_key, []).append(row)

    commit_batch = max(1, persist_apply_commit_batch())
    n = 0
    pending_commit = False

    try:
        removed = () if meta.get("scan_incomplete") else meta.get(
            "removed_chars", (),
        )
        for name in removed:
            conn.execute("DELETE FROM characters WHERE name=?", (name,))
            conn.execute(
                "DELETE FROM items WHERE holder_key=? "
                "AND holder_type IN ('character', 'gear')",
                (name,),
            )
            pending_commit = True
            n += 1
            if n % commit_batch == 0:
                conn.commit()
                pending_commit = False
            if yield_every and n % yield_every == 0:
                await asyncio.sleep(0)

        for name in meta.get("changed_chars", ()):
            conn.execute("DELETE FROM characters WHERE name=?", (name,))
            conn.execute(
                "DELETE FROM items WHERE holder_key=? "
                "AND holder_type IN ('character', 'gear')",
                (name,),
            )
            crow = char_by_name.get(name)
            if crow is not None:
                conn.execute(_CHAR_INSERT_SQL, crow)
            owned = items_by_holder.get(name) or []
            if owned:
                conn.executemany(_ITEM_INSERT_SQL, owned)
            pending_commit = True
            n += 1
            if n % commit_batch == 0:
                conn.commit()
                pending_commit = False
            if yield_every and n % yield_every == 0:
                await asyncio.sleep(0)

        for room_key in meta.get("changed_floor_rooms", ()):
            conn.execute(
                "DELETE FROM items WHERE holder_key=? "
                "AND holder_type='room'",
                (room_key,),
            )
            floor = floor_by_room.get(room_key) or []
            if floor:
                conn.executemany(_ITEM_INSERT_SQL, floor)
            pending_commit = True
            n += 1
            if n % commit_batch == 0:
                conn.commit()
                pending_commit = False
            if yield_every and n % yield_every == 0:
                await asyncio.sleep(0)
    finally:
        # Always flush whatever completed, even on an exception mid-batch --
        # never leave more than one row group uncommitted (same worst-case
        # loss window as the old per-character commit).
        if pending_commit:
            conn.commit()


async def save_world_async(
    conn, game, *, yield_every=None, wall_start=None, wall_budget_ms=0,
    writer_handoff=None,
):
    """Cooperative autosave: snapshot with yields, then chunked apply."""
    import asyncio

    if yield_every is None:
        yield_every = persist_save_yield_every()

    if _persist_should_skip_world_save(game):
        game._last_world_save_stats = {
            "skipped": True,
            "collect_ms": 0.0,
            "apply_ms": 0.0,
            "force_full": False,
            "n_char_rows": 0,
            "n_item_rows": 0,
        }
        return
    ack_epoch = _persist_begin_dirty_write_epoch(game)
    t_collect = time.perf_counter()
    char_rows, item_rows, meta = await _collect_world_save_snapshot_async(
        game,
        yield_every=yield_every,
        wall_start=wall_start,
        wall_budget_ms=wall_budget_ms,
    )
    collect_ms = (time.perf_counter() - t_collect) * 1000.0
    meta["ack_epoch"] = ack_epoch
    if meta.get("skipped_wall_budget"):
        _persist_restore_deferred_dirty(
            game,
            set(meta.get("deferred_chars") or ()),
            set(meta.get("deferred_rooms") or ()),
        )
        game._save_pending_after_current = True
        game._last_world_save_stats = {
            "skipped": True,
            "wall_budget": True,
            "collect_ms": round(collect_ms, 2),
            "apply_ms": 0.0,
            "force_full": bool(meta.get("force_full")),
            "n_char_rows": 0,
            "n_item_rows": 0,
        }
        return
    if not char_rows and not item_rows and not meta.get("changed_chars"):
        if meta.get("wall_budget_hit"):
            _persist_restore_deferred_dirty(
                game,
                set(meta.get("deferred_chars") or ()),
                set(meta.get("deferred_rooms") or ()),
            )
            game._save_pending_after_current = True
            game._last_world_save_stats = {
                "skipped": True,
                "wall_budget": True,
                "collect_ms": round(collect_ms, 2),
                "apply_ms": 0.0,
                "force_full": bool(meta.get("force_full")),
                "n_char_rows": 0,
                "n_item_rows": 0,
            }
            return
    # Let queued player commands run before SQLite work.
    await asyncio.sleep(0)
    deferred_chars = set(meta.get("deferred_chars") or ())
    deferred_rooms = set(meta.get("deferred_rooms") or ())
    if persist_background_writer_ready() and writer_handoff is not None:
        from engine.persistence_writer import WorldSnapshotPacket

        writer_handoff["world"] = WorldSnapshotPacket(
            char_rows=char_rows, item_rows=item_rows, meta=meta,
        )
        writer_handoff["collect_ms"] = collect_ms
        writer_handoff["meta"] = meta
        writer_handoff["char_rows"] = char_rows
        writer_handoff["item_rows"] = item_rows
        game._last_world_save_stats = _persist_provisional_world_stats(
            game,
            collect_ms=collect_ms,
            meta=meta,
            char_rows=char_rows,
            item_rows=item_rows,
            deferred_chars=deferred_chars,
            deferred_rooms=deferred_rooms,
        )
        # World snapshot already captured force_full. Clear the live flag
        # before writer meta so save_supers_meta does not rewrite every
        # slice on the GIL (P23.3 hitch, now on the writer thread).
        if meta.get("force_full"):
            game._persist_force_full = False
        return
    t_apply = time.perf_counter()
    await _apply_world_save_snapshot_async(
        conn, char_rows, item_rows, meta, yield_every=yield_every,
    )
    apply_ms = (time.perf_counter() - t_apply) * 1000.0
    _persist_ack_world_save(
        game, meta, apply_ms=apply_ms, collect_ms=collect_ms,
        char_rows=char_rows, item_rows=item_rows,
        deferred_chars=deferred_chars, deferred_rooms=deferred_rooms,
    )


def _persist_provisional_world_stats(
    game, *, collect_ms, meta, char_rows, item_rows,
    deferred_chars, deferred_rooms,
):
    """Stats visible before the writer thread acks the apply."""
    return {
        "skipped": False,
        "collect_ms": round(collect_ms, 2),
        "apply_ms": 0.0,
        "writer_pending": True,
        "force_full": bool(meta.get("force_full")),
        "n_char_rows": len(char_rows),
        "n_item_rows": len(item_rows),
        "n_changed_chars": len(meta.get("changed_chars") or ()),
        "n_changed_rooms": len(meta.get("changed_floor_rooms") or ()),
        "n_deferred_chars": len(deferred_chars),
        "n_deferred_rooms": len(deferred_rooms),
        "autosave_count": int(getattr(game, "_persist_autosave_count", 0) or 0),
        "full_every": persist_full_every(),
        "slow_chars": meta.get("slow_chars"),
        "char_build_total_ms": meta.get("char_build_total_ms"),
        "char_build_count": meta.get("char_build_count"),
        "char_build_avg_ms": meta.get("char_build_avg_ms"),
    }


def _persist_ack_world_save(
    game, meta, *, apply_ms, collect_ms, char_rows, item_rows,
    deferred_chars=None, deferred_rooms=None,
):
    """Post-apply bookkeeping on the asyncio loop (never on writer thread)."""
    if deferred_chars is None:
        deferred_chars = set(meta.get("deferred_chars") or ())
    if deferred_rooms is None:
        deferred_rooms = set(meta.get("deferred_rooms") or ())
    _persist_store_snapshot_hashes(game, meta)
    if persist_background_writer_ready():
        _persist_ack_written_dirty(game, meta, char_rows)
        _persist_restore_deferred_dirty(
            game, deferred_chars, deferred_rooms,
        )
    else:
        _persist_clear_dirty(game)
        _persist_restore_deferred_dirty(
            game, deferred_chars, deferred_rooms,
        )
    count = int(getattr(game, "_persist_autosave_count", 0) or 0) + 1
    game._persist_autosave_count = count
    _persist_schedule_world_full_if_due(game)
    _persist_bump_echo_generation(game)
    game._last_world_save_stats = {
        "skipped": False,
        "collect_ms": round(collect_ms, 2),
        "apply_ms": round(float(apply_ms or 0.0), 2),
        "force_full": bool(meta.get("force_full")),
        "n_char_rows": len(char_rows),
        "n_item_rows": len(item_rows),
        "n_changed_chars": len(meta.get("changed_chars") or ()),
        "n_changed_rooms": len(meta.get("changed_floor_rooms") or ()),
        "n_deferred_chars": len(deferred_chars),
        "n_deferred_rooms": len(deferred_rooms),
        "n_dirty_chars_queued": meta.get("n_dirty_chars_queued"),
        "n_dirty_rooms_queued": meta.get("n_dirty_rooms_queued"),
        "echo_coalesce_skipped": int(
            getattr(game, "_persist_echo_coalesce_skipped", 0) or 0,
        ),
        "autosave_count": count,
        "full_every": persist_full_every(),
        "slow_chars": meta.get("slow_chars"),
        "char_build_total_ms": meta.get("char_build_total_ms"),
        "char_build_count": meta.get("char_build_count"),
        "char_build_avg_ms": meta.get("char_build_avg_ms"),
    }


def save_world_before_process_exit(conn, game, *, reason="shutdown"):
    """Force a full world snapshot before copyover exit or gateway shutdown.

    Incremental dirty-cap passes can defer bodies that sold/fenced during
    the post-deploy autosave window; a terminating save must rewrite every
    live character or inventory and wallet drift apart after restart (bug
    report 690).
    """
    if conn is None or game is None:
        return
    if persist_background_writer_enabled():
        from engine.persistence_writer import shutdown_persistence_writer
        shutdown_persistence_writer(timeout=persist_writer_drain_timeout_s())
    game._persist_force_full = True
    save_world(conn, game)


def maybe_persist_commerce_immediately(game, character):
    """Flush one live seller during the post-deploy autosave defer window.

    Sell removes inventory and credits wallet in memory; when autosave is
    deferred after a deploy reset, a game-only restart can reload the old
    item rows while the in-memory payout already landed -- or lose the sale
    entirely. A single-character checkpoint keeps inventory and wallet in
    sync without scheduling a full-world snapshot on every fence.
    """
    if game is None or character is None:
        return
    if getattr(character, "session", None) is None:
        return
    import os
    from engine import auto_deploy

    blocked, _since, _window = auto_deploy.deploy_save_defer_status(
        getattr(game, "report_dir", None) or os.getcwd(),
    )
    if not blocked:
        return
    conn = getattr(game, "db", None)
    if conn is None:
        return
    persist_save_character(conn, game, character, player_checkpoint=False)


def save_world(conn, game):
    """Write a full snapshot of the live world into the database.

    Characters come from ``game.characters`` (engine/char_index) so we do
    not walk ~12k empty wilderness cells every save. Loose room Items use
    the floor-item room index when available. After the first successful
    save, only dirty characters and touched floor rooms are serialized and
    written (``mark_character_dirty`` / ``mark_floor_room_dirty``).

    Callers that are about to terminate the process (copyover / planned
    restart) must set ``game._persist_force_full = True`` first -- otherwise
    deferred dirty bodies past the per-save cap are never flushed. Prefer
    :func:`save_world_before_process_exit` for shutdown paths.

    Runs inside one transaction so a crash mid-save can never leave
    the file half-written -- SQLite rolls it back.

    Production autosave uses :func:`save_world_async` so JSON/blob work can
    yield on the asyncio loop before the bulk INSERT transaction.
    """
    if _persist_should_skip_world_save(game):
        game._last_world_save_stats = {
            "skipped": True,
            "collect_ms": 0.0,
            "apply_ms": 0.0,
            "force_full": False,
            "n_char_rows": 0,
            "n_item_rows": 0,
        }
        return
    t_collect = time.perf_counter()
    char_rows, item_rows, meta = _collect_world_save_snapshot(game)
    collect_ms = (time.perf_counter() - t_collect) * 1000.0
    t_apply = time.perf_counter()
    _apply_world_save_snapshot(conn, char_rows, item_rows, meta)
    apply_ms = (time.perf_counter() - t_apply) * 1000.0
    deferred_chars = set(meta.get("deferred_chars") or ())
    deferred_rooms = set(meta.get("deferred_rooms") or ())
    _persist_store_snapshot_hashes(game, meta)
    _persist_clear_dirty(game)
    _persist_restore_deferred_dirty(game, deferred_chars, deferred_rooms)
    count = int(getattr(game, "_persist_autosave_count", 0) or 0) + 1
    game._persist_autosave_count = count
    _persist_schedule_world_full_if_due(game)
    _persist_bump_echo_generation(game)
    game._last_world_save_stats = {
        "skipped": False,
        "collect_ms": round(collect_ms, 2),
        "apply_ms": round(apply_ms, 2),
        "force_full": bool(meta.get("force_full")),
        "n_char_rows": len(char_rows),
        "n_item_rows": len(item_rows),
        "n_changed_chars": len(meta.get("changed_chars") or ()),
        "n_changed_rooms": len(meta.get("changed_floor_rooms") or ()),
        "n_deferred_chars": len(deferred_chars),
        "n_deferred_rooms": len(deferred_rooms),
        "n_dirty_chars_queued": meta.get("n_dirty_chars_queued"),
        "n_dirty_rooms_queued": meta.get("n_dirty_rooms_queued"),
        "echo_coalesce_skipped": int(
            getattr(game, "_persist_echo_coalesce_skipped", 0) or 0,
        ),
        "autosave_count": count,
        "full_every": persist_full_every(),
    }


def load_world(conn, game):
    """Rebuild the saved characters and items into the (already built) rooms.

    Called once at startup, after build_world() made the map. Every character
    comes back as an Echo -- present in their room but with session=None --
    until (unless) their player reconnects and reattaches.
    """
    game._persist_char_snapshot_hashes = None
    game._persist_floor_room_hashes = None
    game._persist_dirty_characters = set()
    game._persist_dirty_floor_rooms = set()
    game._persist_force_full = True
    game._persist_warm = False
    # Section 6: a spirit's body/body_room can't be relinked until the
    # items loop below has placed every Item back into its room -- see the
    # fixup pass after that loop.
    pending_body_links = []
    for name, description, room_key, blob in conn.execute(
        "SELECT name, description, room_key, stats FROM characters"
    ):
        # Permanent account GM spirits load; orphan ephemeral leftovers skip.
        if (name or "").lower().startswith("gmspirit:"):
            keep = False
            try:
                blob_peek = json.loads(blob) if blob else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                blob_peek = {}
            if blob_peek.get("gm_spirit_permanent"):
                keep = True
            if not keep:
                try:
                    from engine.accounts import ensure_accounts_dict
                    low = (name or "").lower()
                    for acct in ensure_accounts_dict(game).values():
                        if acct.gm_rank not in ("gm", "head_gm"):
                            continue
                        want = (
                            acct.gm_spirit_key
                            or f"gmspirit:{acct.name}"
                        )
                        if want.lower() == low:
                            keep = True
                            break
                except Exception:
                    keep = False
            if not keep:
                continue
            # Folded staff spirits live only in character_vault until gm on.
            vault_row = vault_get(conn, name)
            if vault_row is not None and (vault_row[3] or "") == "gm-spirit-parked":
                continue
        char = Character(name, description)
        try:
            saved = json.loads(blob)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            from engine import log_util
            from engine import metrics as metrics_mod
            log_util.ops(
                "persistence",
                f"skip corrupt blob key={name!r} ({exc!r})",
            )
            metrics_mod.bump(game, "load_corrupt_blob")
            continue
        # Restore every SUPERS field (stat spine, Origin/Path/Disciplines,
        # Cadence needs, every Path's fuel/faith/blood/instinct/soul
        # economy, ...) from the saved blob onto the freshly-built `char`.
        # supers/persist_blob.py is what actually knows the field-by-field
        # restoration logic (and every .get()-fallback pattern that lets an
        # old save missing a field just take Character's own default) --
        # this module only owns the SQL row and the room/spirit fixups
        # that need the whole-world view apply_character_blob doesn't have.
        #
        # apply_character_blob returns (body_room_key, body_key) when this
        # character is a spirit (section 6): body/body_room are live object
        # refs that can't be relinked until the items loop below has placed
        # every Item back into its room, so that pair goes on the
        # pending-links list here and gets resolved in the fixup pass after
        # that loop -- see supers/persist_blob.py's module docstring for why
        # that hand-off is the cleanest split.
        pending_link = apply_character_blob(char, saved)
        # Keep the saved room_key even when map JSON is stale -- stub rather
        # than silently dumping onto start_room / North Avenue (no_loiter).
        room = _resolve_saved_room(game, room_key, name)
        char.move_to(room)          # session stays None: this is an Echo
        if pending_link is not None:
            body_room_key, body_key = pending_link
            pending_body_links.append((char, body_room_key, body_key))

    for key, description, holder_type, holder_key, container in conn.execute(
        "SELECT key, description, holder_type, holder_key, container FROM items"
    ):
        # json.loads(container) parses the blob _item_container_blob wrote.
        # .get(..., default) means an items row saved before the 'container'
        # column existed (container == '{}', the column's DEFAULT) loads as
        # a plain, unlocked flavor item -- exactly what it was before.
        state = json.loads(container)
        item = Item(
            key, description,
            locked=state.get("locked", False),
            loot=_loot_from_json(state.get("loot", [])),
            is_body=state.get("is_body", False),
            is_buried=state.get("is_buried", False),
            relic=state.get("relic", None),
            furniture=state.get("furniture", False),
        )
        # Restore creation stamp (or keep __init__ seq) and advance the
        # global counter so later live spawns stay "newer".
        if state.get("created_seq") is not None:
            try:
                item.created_seq = int(state["created_seq"])
            except (TypeError, ValueError):
                pass
        note_item_created_seq(getattr(item, "created_seq", 0))
        if state.get("owner_key"):
            item.owner_key = state["owner_key"]
        if state.get("need"):
            item.need = state["need"]
        if state.get("provides_light"):
            item.provides_light = True
        if state.get("catalog_id"):
            item.catalog_id = state["catalog_id"]
        aliases = state.get("aliases") or []
        if isinstance(aliases, list) and aliases:
            item.aliases = [str(a) for a in aliases if a]
        # Combat gear (classic slots + mods + folklore materials + color).
        if state.get("slot"):
            item.slot = state["slot"]
        if isinstance(state.get("mods"), dict):
            item.mods = dict(state["mods"])
        if state.get("god_forge_blessed_tier"):
            try:
                item.god_forge_blessed_tier = int(state["god_forge_blessed_tier"])
            except (TypeError, ValueError):
                pass
        if state.get("god_war_manifest"):
            item.god_war_manifest = True
        if state.get("god_war_owner_key"):
            item.god_war_owner_key = str(state["god_war_owner_key"])
        if state.get("grip"):
            item.grip = str(state["grip"]).strip().lower()
        if state.get("bound_angel_id"):
            item.bound_angel_id = str(state["bound_angel_id"])
        if state.get("angel_blade_growth_tier"):
            try:
                item.angel_blade_growth_tier = int(state["angel_blade_growth_tier"])
            except (TypeError, ValueError):
                pass
        if isinstance(state.get("materials"), list):
            item.materials = list(state["materials"])
        if state.get("color"):
            item.color = state["color"]
        if state.get("equipped"):
            item.equipped = True
        # Clothing layer (cosmetic under armor).
        if state.get("layer"):
            item.layer = str(state["layer"]).strip().lower()
        if state.get("worn"):
            item.worn = True
        if state.get("worn_order") is not None:
            try:
                item.worn_order = int(state["worn_order"])
            except (TypeError, ValueError):
                pass
        if state.get("cloth_material"):
            item.cloth_material = str(state["cloth_material"]).strip().lower()
        if state.get("warmth"):
            item.warmth = str(state["warmth"]).strip().lower()
        if isinstance(state.get("cover"), list):
            item.cover = [str(c).strip().lower() for c in state["cover"] if c]
        if isinstance(state.get("conceal"), list):
            item.conceal = [
                str(c).strip().lower() for c in state["conceal"] if c
            ]
        if state.get("dirty") is not None:
            item.dirty = bool(state["dirty"])
        if state.get("gear_condition") is not None:
            try:
                item.gear_condition = int(state["gear_condition"])
            except (TypeError, ValueError):
                pass
        # Fridge pantry timer (home grocery stock); absent on older saves.
        if state.get("stock_until_tick") is not None:
            try:
                item.stock_until_tick = int(state["stock_until_tick"])
            except (TypeError, ValueError):
                pass
        # Vampire blood-pantry timer; absent on older saves / empty fridges.
        if state.get("blood_stock_until_tick") is not None:
            try:
                item.blood_stock_until_tick = int(
                    state["blood_stock_until_tick"]
                )
            except (TypeError, ValueError):
                pass
        # Corpse floor age for Wendigo larder; absent on older saves.
        if state.get("body_dropped_tick") is not None:
            try:
                item.body_dropped_tick = int(state["body_dropped_tick"])
            except (TypeError, ValueError):
                pass
        _restore_body_harvest_fields(item, state)
        # Abandoned floor loot grace / vault TTL (supers.floor_loot).
        if state.get("floor_dropped_tick") is not None:
            try:
                item.floor_dropped_tick = int(state["floor_dropped_tick"])
            except (TypeError, ValueError):
                pass
        if state.get("vault_decay_at_tick") is not None:
            try:
                item.vault_decay_at_tick = int(state["vault_decay_at_tick"])
            except (TypeError, ValueError):
                pass
        # Physical phone line (supers/phone.py); absent on older saves.
        if state.get("phone_number"):
            item.phone_number = str(state["phone_number"]).strip()
        if state.get("is_phone"):
            item.is_phone = True
        if state.get("is_payphone"):
            item.is_payphone = True
            item.furniture = True
        if state.get("is_ethereal"):
            item.is_ethereal = True
        if state.get("is_spirit_mirror"):
            item.is_spirit_mirror = True
        if state.get("spirit_mirror_source_key"):
            item.spirit_mirror_source_key = str(
                state["spirit_mirror_source_key"]
            ).strip()
        if state.get("_contract_id"):
            item._contract_id = str(state["_contract_id"])
        if state.get("_contract_prize"):
            item._contract_prize = True
        if state.get("_contract_lockbox"):
            item._contract_lockbox = True
        body_eq = state.get("body_equipment")
        if isinstance(body_eq, dict):
            item.body_equipment = dict(body_eq)
        body_cl = state.get("body_clothing")
        if isinstance(body_cl, dict):
            item.body_clothing = {
                str(slot): list(keys or [])
                for slot, keys in body_cl.items()
            }
        # Colt / charged weapon ammo (absent on older saves → enrich later).
        if state.get("ammo_charges") is not None:
            try:
                item.ammo_charges = int(state["ammo_charges"])
            except (TypeError, ValueError):
                pass
        if state.get("max_ammo") is not None:
            try:
                item.max_ammo = int(state["max_ammo"])
            except (TypeError, ValueError):
                pass
        if state.get("loaded_ammo_id"):
            item.loaded_ammo_id = str(state["loaded_ammo_id"]).strip()
        if state.get("ammo_kind"):
            item.ammo_kind = str(state["ammo_kind"]).strip().lower()
        if state.get("dmb_coated"):
            item.dmb_coated = True
        if state.get("lambs_blood_coated"):
            item.lambs_blood_coated = True
        if state.get("dmb_spiked"):
            item.dmb_spiked = True
        if state.get("stack_charges") is not None:
            try:
                item.stack_charges = int(state["stack_charges"])
            except (TypeError, ValueError):
                pass
        if state.get("weapon_voice"):
            item.weapon_voice = str(state["weapon_voice"]).strip().lower()
        if state.get("artifact_lexicon"):
            item.artifact_lexicon = str(state["artifact_lexicon"]).strip()
        _restore_relic_fields(item, state)
        _restore_pit_mimic_fields(item, state)
        _restore_on_use_fields(item, state)
        _restore_herb_fields(item, state)
        _restore_bag_fields(item, state)
        # bug_reports.log #21: strongboxes saved before the lockbox pass (or
        # with the default '{}' container blob) reload as flavor-only Items;
        # promote them here so `open strongbox` still pays out after a
        # reboot. Goes through engine.hooks -- the reward math (and its
        # supers.faith relic-drop chance) is SUPERS content, not engine core.
        upgrade_legacy_container(item)
        if holder_type == "room":
            # Missing holder room (map rename / unload) -- game hook picks
            # the lost-item vault; bare engine falls back to start_room.
            from engine.hooks import orphan_item_room
            sink = game.rooms.get(holder_key)
            if sink is None:
                from engine import hooks
                sink = hooks.demesne_lookup_room_for_persist(game, holder_key)
            sink = sink or orphan_item_room(game)
            if sink is not None:
                sink.add(item)
            # Catalog gear + pit potion on_use heal (same as inventory).
            from engine import hooks
            hooks.enrich_loaded_item(item)
        elif holder_type == "gear":
            # Job kit bag -- not surface inventory (supers/gear_bag).
            owner = game.find_character(holder_key)
            if owner:
                bag = getattr(owner, "gear_bag", None)
                if bag is None or not isinstance(bag, list):
                    owner.gear_bag = []
                    bag = owner.gear_bag
                bag.append(item)
                from engine import hooks
                hooks.enrich_loaded_item(item)
        else:
            owner = game.find_character(holder_key)
            if owner:               # owner should always exist; guard anyway
                owner.inventory.append(item)
                # Enrich from catalog when only catalog_id survived.
                from engine import hooks
                hooks.enrich_loaded_item(item)

    # After all inventory rows land, rebuild equipment maps from equipped flags.
    from engine.char_index import iter_characters
    from engine import hooks
    for char in iter_characters(game):
        hooks.rebind_character_equipment(char)

    # Per-character lodging / vehicle board normalization (boot lifecycle
    # Phase C) -- replaces full-world boot sweeps for these fields.
    for char in iter_characters(game):
        hooks.normalize_character_after_load(char, game)

    # Section 6: relink each spirit's body/body_room object refs now that
    # every Item has been placed back into its room.
    for char, body_room_key, body_key in pending_body_links:
        resolve_pending_body_link(game, char, body_room_key, body_key)


def _corpse_worn_key(piece):
    """Normalize a persisted worn ref (str or {key, created_seq}) to a key."""
    if piece is None:
        return None
    if isinstance(piece, str):
        text = piece.strip()
        return text or None
    if isinstance(piece, Item):
        return getattr(piece, "key", None)
    if isinstance(piece, dict):
        text = str(piece.get("key") or "").strip()
        return text or None
    return None


def _corpse_worn_created_seq(piece):
    """Optional created_seq from a persisted worn ref for disambiguation."""
    if isinstance(piece, dict):
        try:
            return int(piece.get("created_seq") or 0)
        except (TypeError, ValueError):
            return 0
    if isinstance(piece, Item):
        try:
            return int(getattr(piece, "created_seq", 0) or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _find_corpse_worn_piece(room, piece_ref):
    """Resolve one worn ref to a live Item in ``room`` (or return Item as-is)."""
    if room is None or piece_ref is None:
        return None
    if isinstance(piece_ref, Item):
        return piece_ref
    key = _corpse_worn_key(piece_ref)
    if not key:
        return None
    want_seq = _corpse_worn_created_seq(piece_ref)
    matches = []
    for obj in getattr(room, "contents", None) or []:
        if not isinstance(obj, Item) or getattr(obj, "is_body", False):
            continue
        if getattr(obj, "key", None) != key:
            continue
        if want_seq and int(getattr(obj, "created_seq", 0) or 0) != want_seq:
            continue
        matches.append(obj)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    flagged = [
        m for m in matches
        if getattr(m, "equipped", False) or getattr(m, "worn", False)
    ]
    if len(flagged) == 1:
        return flagged[0]
    return matches[0]


def rehydrate_corpse_worn_refs(body, room):
    """Replace string worn keys on a corpse Item with live room Item refs."""
    if body is None or room is None:
        return
    equipment = getattr(body, "body_equipment", None) or {}
    if equipment:
        resolved = {}
        for slot, piece in equipment.items():
            live = _find_corpse_worn_piece(room, piece)
            if live is not None:
                resolved[slot] = live
        body.body_equipment = resolved
    clothing_map = getattr(body, "body_clothing", None) or {}
    if clothing_map:
        resolved_cl = {}
        for slot, stack in clothing_map.items():
            if not isinstance(stack, list):
                continue
            live_stack = []
            for entry in stack:
                live = _find_corpse_worn_piece(room, entry)
                if live is not None:
                    live_stack.append(live)
            if live_stack:
                resolved_cl[slot] = live_stack
        body.body_clothing = resolved_cl


def resolve_pending_body_link(game, char, body_room_key, body_key):
    """Relink one spirit's ``body`` / ``body_room`` object refs by key.

    Shared by the boot loader (after every Item is placed back into its
    room) and ``supers.fold_vault.restore_from_vault`` (a live world has
    everything already placed, so this can run immediately there instead
    of a second pass). Living vessel husks are Characters (not floor Item
    corpses) -- resolve them via ``find_character`` so a vessel-free
    Angel/Demon is not force-unspirited while their Jimmy-style husk keeps
    walking (dual-corporeal bug). If no body can be found at all
    (corrupted save / missing room / vaulted husk never restored), the
    safe fallback is still to un-spirit rather than leave them stuck with
    no way to self-anchor -- casual death staying non-permanent matters
    more here than strict fidelity to a broken save.
    """
    room = game.rooms.get(body_room_key) if body_room_key else None
    if room is None and body_room_key:
        from engine import hooks
        room = hooks.demesne_lookup_room_for_persist(game, body_room_key)
    body = None
    if room is not None:
        for obj in room.contents:
            if isinstance(obj, Item) and obj.is_body and obj.key == body_key:
                body = obj
                break
    # Living husk Character: saved body_key is husk:Mantle (or a
    # renamed mortal face). Prefer exact key, then vessel_husk_key.
    finder = getattr(game, "find_character", None)
    if body is None and callable(finder):
        for key in (body_key, getattr(char, "vessel_husk_key", None)):
            if not key:
                continue
            candidate = finder(key)
            if candidate is None or candidate is char:
                continue
            # Accept designed living husks (is_vessel_husk) owned by
            # this Mantle, or any Character whose key matched the
            # saved body pointer (pre-flag save drift).
            owner = getattr(candidate, "vessel_owner_key", None)
            if getattr(candidate, "is_vessel_husk", False):
                if owner is None or owner == char.key:
                    body = candidate
                    break
            elif key == body_key:
                body = candidate
                break
    if body is not None:
        char.body = body
        # Prefer the husk's live room over a stale body_room_key --
        # vacant Cadence husks wander after vacate.
        live_room = getattr(body, "location", None)
        char.body_room = live_room if live_room is not None else room
        rehydrate_corpse_worn_refs(body, char.body_room)
    else:
        from engine.hooks import relink_living_husk_body

        if relink_living_husk_body(game, char):
            return
        char.spirit = False
        char.spirit_state = None
        char.spirit_tether = 0.0
        char.spirit_untethered_ticks = 0
        # Drop vessel_free so boot heal / Cadence do not treat this
        # Mantle as a vessel spirit with a missing husk (hybrid state
        # that used to leave Castiel corporeal + Jimmy vacant).
        if hasattr(char, "vessel_free"):
            char.vessel_free = False
        # Re-derive HP after un-spiriting a character whose body was
        # lost -- the one spot in this module that used to reach into
        # supers.stats directly; now goes through engine.hooks so this
        # module has zero SUPERS imports (Phase 3 purity).
        recompute_hp(char)
        # Lazy import: mid-copyover reload of this file must not depend
        # on a top-level hooks symbol that may still be one revision behind.
        from engine.hooks import heal_force_unspirit

        heal_force_unspirit(char, game)


# ---------------------------------------------------------------------------
# Hard gm fold vault (opaque zlib payloads; survives save_world wipe-rewrite)
# ---------------------------------------------------------------------------


def item_from_saved_container(key, description, container):
    """Rebuild one Item from a save-shaped container blob (dict or JSON str).

    Same field set as ``load_world``'s items loop -- vault restore and live
    load share one reconstruction path so gear / phones / lockboxes do not
    drift.
    """
    if isinstance(container, str):
        state = json.loads(container or "{}")
    else:
        state = dict(container or {})
    item = Item(
        key,
        description,
        locked=state.get("locked", False),
        loot=_loot_from_json(state.get("loot", [])),
        is_body=state.get("is_body", False),
        is_buried=state.get("is_buried", False),
        relic=state.get("relic", None),
        furniture=state.get("furniture", False),
    )
    if state.get("created_seq") is not None:
        try:
            item.created_seq = int(state["created_seq"])
        except (TypeError, ValueError):
            pass
    note_item_created_seq(getattr(item, "created_seq", 0))
    if state.get("owner_key"):
        item.owner_key = state["owner_key"]
    if state.get("need"):
        item.need = state["need"]
    if state.get("provides_light"):
        item.provides_light = True
    if state.get("catalog_id"):
        item.catalog_id = state["catalog_id"]
    aliases = state.get("aliases") or []
    if isinstance(aliases, list) and aliases:
        item.aliases = [str(a) for a in aliases if a]
    if state.get("slot"):
        item.slot = state["slot"]
    if isinstance(state.get("mods"), dict):
        item.mods = dict(state["mods"])
    if state.get("god_forge_blessed_tier"):
        try:
            item.god_forge_blessed_tier = int(state["god_forge_blessed_tier"])
        except (TypeError, ValueError):
            pass
    if state.get("god_war_manifest"):
        item.god_war_manifest = True
    if state.get("god_war_owner_key"):
        item.god_war_owner_key = str(state["god_war_owner_key"])
    if state.get("grip"):
        item.grip = str(state["grip"]).strip().lower()
    if state.get("bound_angel_id"):
        item.bound_angel_id = str(state["bound_angel_id"])
    if state.get("angel_blade_growth_tier"):
        try:
            item.angel_blade_growth_tier = int(state["angel_blade_growth_tier"])
        except (TypeError, ValueError):
            pass
    if isinstance(state.get("materials"), list):
        item.materials = list(state["materials"])
    if state.get("color"):
        item.color = state["color"]
    if state.get("equipped"):
        item.equipped = True
    if state.get("layer"):
        item.layer = str(state["layer"]).strip().lower()
    if state.get("worn"):
        item.worn = True
    if state.get("worn_order") is not None:
        try:
            item.worn_order = int(state["worn_order"])
        except (TypeError, ValueError):
            pass
    if state.get("cloth_material"):
        item.cloth_material = str(state["cloth_material"]).strip().lower()
    if state.get("warmth"):
        item.warmth = str(state["warmth"]).strip().lower()
    if isinstance(state.get("cover"), list):
        item.cover = [str(c).strip().lower() for c in state["cover"] if c]
    if isinstance(state.get("conceal"), list):
        item.conceal = [
            str(c).strip().lower() for c in state["conceal"] if c
        ]
    if state.get("dirty") is not None:
        item.dirty = bool(state["dirty"])
    if state.get("stock_until_tick") is not None:
        try:
            item.stock_until_tick = int(state["stock_until_tick"])
        except (TypeError, ValueError):
            pass
    if state.get("blood_stock_until_tick") is not None:
        try:
            item.blood_stock_until_tick = int(state["blood_stock_until_tick"])
        except (TypeError, ValueError):
            pass
    if state.get("body_dropped_tick") is not None:
        try:
            item.body_dropped_tick = int(state["body_dropped_tick"])
        except (TypeError, ValueError):
            pass
    _restore_body_harvest_fields(item, state)
    if state.get("floor_dropped_tick") is not None:
        try:
            item.floor_dropped_tick = int(state["floor_dropped_tick"])
        except (TypeError, ValueError):
            pass
    if state.get("vault_decay_at_tick") is not None:
        try:
            item.vault_decay_at_tick = int(state["vault_decay_at_tick"])
        except (TypeError, ValueError):
            pass
    if state.get("phone_number"):
        item.phone_number = str(state["phone_number"]).strip()
    if state.get("is_phone"):
        item.is_phone = True
    if state.get("is_payphone"):
        item.is_payphone = True
        item.furniture = True
    if state.get("is_ethereal"):
        item.is_ethereal = True
    if state.get("_contract_id"):
        item._contract_id = str(state["_contract_id"])
    if state.get("_contract_prize"):
        item._contract_prize = True
    if state.get("_contract_lockbox"):
        item._contract_lockbox = True
    if state.get("ammo_charges") is not None:
        try:
            item.ammo_charges = int(state["ammo_charges"])
        except (TypeError, ValueError):
            pass
    if state.get("max_ammo") is not None:
        try:
            item.max_ammo = int(state["max_ammo"])
        except (TypeError, ValueError):
            pass
    if state.get("loaded_ammo_id"):
        item.loaded_ammo_id = str(state["loaded_ammo_id"]).strip()
    if state.get("ammo_kind"):
        item.ammo_kind = str(state["ammo_kind"]).strip().lower()
    if state.get("stack_charges") is not None:
        try:
            item.stack_charges = int(state["stack_charges"])
        except (TypeError, ValueError):
            pass
    if state.get("weapon_voice"):
        item.weapon_voice = str(state["weapon_voice"]).strip().lower()
    if state.get("artifact_lexicon"):
        item.artifact_lexicon = str(state["artifact_lexicon"]).strip()
    _restore_relic_fields(item, state)
    _restore_pit_mimic_fields(item, state)
    _restore_on_use_fields(item, state)
    _restore_herb_fields(item, state)
    _restore_bag_fields(item, state)
    upgrade_legacy_container(item)
    return item


def snapshot_held_items(character):
    """Return inventory + gear_bag rows shaped for vault / items table.

    Each entry: ``{holder_type, key, description, container}`` where
    ``container`` is a JSON-ready dict (not a string).
    """
    rows = []
    for item in list(getattr(character, "inventory", None) or []):
        rows.append({
            "holder_type": "character",
            "key": item.key,
            "description": item.description,
            "container": json.loads(_item_container_blob(item)),
        })
    for item in list(getattr(character, "gear_bag", None) or []):
        rows.append({
            "holder_type": "gear",
            "key": item.key,
            "description": item.description,
            "container": json.loads(_item_container_blob(item)),
        })
    return rows


def vault_list(conn):
    """Yield ``(name, room_key, folded_at, folded_by)`` for every vaulted mortal."""
    rows = conn.execute(
        "SELECT name, room_key, folded_at, folded_by FROM character_vault "
        "ORDER BY lower(name)"
    ).fetchall()
    return list(rows)


def vault_has(conn, name):
    """True when ``name`` (exact key) has a vault row."""
    if not name:
        return False
    row = conn.execute(
        "SELECT 1 FROM character_vault WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    return row is not None


def vault_lookup_name(conn, name):
    """Return the stored vault ``name`` matching ``name`` case-insensitively."""
    if not name:
        return None
    row = conn.execute(
        "SELECT name FROM character_vault WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    return row[0] if row else None


def vault_put(conn, name, room_key, payload_bytes, *, folded_by=None,
              folded_at=None):
    """INSERT OR REPLACE a compressed vault payload for ``name``.

    Call this **before** despawning the live body so a crash mid-fold
    cannot wipe the pfile without a vault copy.
    """
    when = float(folded_at if folded_at is not None else time.time())
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO character_vault "
            "(name, room_key, folded_at, folded_by, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, room_key or "", when, folded_by, payload_bytes),
        )


def vault_get(conn, name):
    """Return ``(name, room_key, folded_at, folded_by, payload)`` or None."""
    stored = vault_lookup_name(conn, name)
    if not stored:
        return None
    row = conn.execute(
        "SELECT name, room_key, folded_at, folded_by, payload "
        "FROM character_vault WHERE name = ?",
        (stored,),
    ).fetchone()
    return row


def vault_delete(conn, name):
    """Remove a vault row (after successful restore). Idempotent."""
    stored = vault_lookup_name(conn, name)
    if not stored:
        return False
    with conn:
        conn.execute("DELETE FROM character_vault WHERE name = ?", (stored,))
    return True


def compress_vault_envelope(envelope):
    """zlib-compress a JSON-serializable vault envelope dict -> bytes."""
    raw = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
    return zlib.compress(raw.encode("utf-8"), level=9)


def decompress_vault_envelope(payload_bytes):
    """Inverse of ``compress_vault_envelope``."""
    raw = zlib.decompress(payload_bytes)
    return json.loads(raw.decode("utf-8"))
