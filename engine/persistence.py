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
_PERSIST_DIRTY_CHAR_CAP_DEFAULT = 12
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
# Refuse a force_full apply whose snapshot is a partial roster versus
# live SQLite (2026-09-16 wipe: 108 INSERT rows replaced 1475). Tiny
# test DBs stay under the floor so smokes still rewrite.
_FORCE_FULL_SHORT_FRACTION = 0.80
_FORCE_FULL_SHORT_MIN_LIVE = 50
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
_PERSIST_SAVE_WALL_BUDGET_MS_DEFAULT = 1500
# Lag P12: log a character's blob-build time when it alone crosses this
# floor, so a slow autosave collect names the actual culprit instead of
# only reporting an aggregate collect_ms. 20ms is well above the ~1-3ms a
# normal character blob costs -- see docs/plans/lag_p12_collect_profile.md.
_PERSIST_SLOW_CHAR_LOG_MS = 20.0
# Cap how many slow-character (name, ms) pairs one autosave keeps -- this
# is diagnostic breadcrumbs, not a full profile; a handful of names is
# enough to point a GM at the right character with ``gm cadence why``.
_PERSIST_SLOW_CHAR_LOG_CAP = 8
# Lag U2: floor-room collect profiling (P12 sibling for ~10k room walk).
_PERSIST_SLOW_FLOOR_LOG_MS = 10.0
_PERSIST_SLOW_FLOOR_LOG_CAP = 8
# When blob build + json.dumps alone cross this floor, attach top blob keys
# to slow_char_detail (lag P23 -- name the fragment, not just the character).
_PERSIST_SLOW_CHAR_FRAGMENT_PROFILE_MS = 50.0
# One character this slow during collect gets an immediate ops line.
_PERSIST_MEGACHAR_COLLECT_OPS_LOG_MS = 200.0


def _approx_json_fragment_bytes(value):
    """Rough serialized size of one blob dict value (diagnostic only)."""
    try:
        return len(json.dumps(value, separators=(",", ":"), default=str))
    except (TypeError, ValueError):
        try:
            return len(repr(value))
        except Exception:
            return 0


def _profile_blob_top_keys(blob_dict, top_n=5):
    """Largest top-level blob keys by approximate JSON size."""
    if not isinstance(blob_dict, dict) or not blob_dict:
        return []
    ranked = []
    for key, value in blob_dict.items():
        ranked.append((str(key), _approx_json_fragment_bytes(value)))
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return [
        {"key": key, "approx_bytes": size}
        for key, size in ranked[: max(1, int(top_n))]
    ]


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
    except Exception as exc:
        from engine import log_util

        log_util.ops("persistence", "collect_idle start failed", exc=exc)


def note_collect_finished(game):
    """Set the collect-idle Event after ``_save_collecting`` is cleared."""
    if game is None:
        return
    try:
        _collect_idle_event(game).set()
    except Exception as exc:
        from engine import log_util

        log_util.ops("persistence", "collect_idle finish failed", exc=exc)


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
    if getattr(game, "_persist_force_full", False):
        # A chunked chars-only verify may span several autosaves (CPU
        # followon 2026-09) -- never stack a second scheduled arm on top
        # of one already in progress, or the next collect would reset
        # ``_persist_force_full_verified_chars`` and throw away progress.
        return
    count = int(getattr(game, "_persist_autosave_count", 0) or 0)
    every = persist_full_every()
    if every > 0 and count % every == 0:
        game._persist_world_full_scheduled = True


def _persist_apply_scheduled_world_full(game):
    """Consume a deferred world full-verify flag at snapshot collect time."""
    if game is None:
        return
    # Terminating copyover/shutdown saves already force a full roster rewrite;
    # consuming the scheduled tick here would re-arm chars-only verify and
    # drop floor loot past the dirty cap (bug report 1735).
    if getattr(game, "_persist_copyover_save", False):
        game._persist_world_full_scheduled = False
        return
    if getattr(game, "_persist_world_full_scheduled", False):
        game._persist_force_full = True
        # Lag U2: periodic full-verify rewrites the character roster only.
        # Floor loot stays on dirty/fingerprint incremental collect so
        # autosave does not walk every indexed room (~10k on live).
        game._persist_force_full_chars_only = True
        game._persist_world_full_scheduled = False
        _persist_reset_force_full_verify_progress(game)


def _persist_force_full_collect_flags(game):
    """Split full-verify into character vs floor collect (lag U2).

    Cold boot (hash cache not warm) still rewrites every character and
    every indexed floor room. Scheduled ``persist_full_every()`` ticks
    only rewrite characters -- floor loot stays on the dirty fast path.
    """
    cold = not _persist_snapshot_hashes_ready(game)
    armed = bool(getattr(game, "_persist_force_full", False))
    chars_only = bool(getattr(game, "_persist_force_full_chars_only", False))
    force_full_chars = cold or armed
    force_full_floor = cold or (armed and not chars_only)
    return force_full_chars, force_full_floor


def _persist_chars_only_chunked_verify(game, force_full_chars, force_full_floor):
    """Scheduled char-only verify can apply incrementally across autosaves.

    Cold-boot full verify still needs one atomic wipe pass; only the warm
    ``persist_full_every()`` chars-only path may chunk on wall budget. Without
    this, a slow roster (~700+ live characters) hitting the 5s collect wall
    budget discards the entire pass and re-arms ``force_full`` -- CPU
    followon 2026-09: this looped forever, ~5s collect back-to-back,
    pegging one core at ~100% because ``_save_pending_after_current``
    bypasses the normal 180s autosave gate on a wall-budget skip.
    """
    if not force_full_chars or force_full_floor:
        return False
    if not _persist_snapshot_hashes_ready(game):
        return False
    return bool(getattr(game, "_persist_force_full_chars_only", False))


def _persist_force_full_verified_chars(game):
    """Names already rewritten in the current chars-only verify cycle."""
    verified = getattr(game, "_persist_force_full_verified_chars", None)
    if verified is None:
        verified = set()
        game._persist_force_full_verified_chars = verified
    return verified


def _persist_reset_force_full_verify_progress(game):
    if game is None:
        return
    game._persist_force_full_verified_chars = set()


def _persist_count_live_characters(game):
    from engine.char_index import iter_characters

    n = 0
    for obj in iter_characters(game):
        name = (getattr(obj, "key", None) or getattr(obj, "name", None) or "")
        if str(name):
            n += 1
    return n


def _persist_finish_force_full_if_due(game, meta):
    """Clear armed full-verify once a chunked chars-only pass completes."""
    if game is None or not meta:
        return
    if not meta.get("force_full_complete"):
        return
    game._persist_force_full = False
    game._persist_force_full_chars_only = False
    _persist_reset_force_full_verify_progress(game)


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


def _existing_frontierland_cell(game, room_key):
    """Return an already-materialized 1861 virtual cell, or None.

    Does not create a twin. Looking up a Prime wilderness title must not
    spawn the sealed-age clone (that used to rewire homestead doors).
    """
    if game is None:
        return None
    key = str(room_key or "").strip()
    if not key:
        return None
    for stash in (
        getattr(game, "frontierland_rooms", None) or {},
    ):
        for room in stash.values():
            if (getattr(room, "key", None) or "") == key:
                return room
    return None


def _resolve_frontierland_saved_room_key(game, room_key, character=None):
    """Materialize 1861 Frontierland mouths / foot cells (bug report 1399).

    Virtual 1861 cells live in ``game.frontierland_rooms``, not
    ``game.rooms``. Without an atlas-aware lookup, copyover reload falls
    back to ``home_room_key`` (often the Men of Letters bunker).

    Generic ``Wilderness (mx,my)/ux,uy`` titles are shared with Prime
    Earth. Only materialize the sealed-age twin when the save belongs
    on that atlas (``overland_plane``, stranded soul, Mine:/Frontierland
    keys). Otherwise a homestead mouth on today's map reloads into 1861.
    """
    key = str(room_key or "").strip()
    if not key or game is None:
        return None
    from engine.systems.overland import (
        FRONTIERLAND_MAP_ID,
        FRONTIERLAND_PLANE,
        FRONTIERLAND_PREFIX,
        ensure_game_frontierland,
        get_virtual_room,
        parse_wilderness_room_key,
        resolve_virtual_overland_room_key,
    )
    from engine import map_ui
    from engine.room_vnum import lookup_room

    ensure_game_frontierland(game)
    rooms = getattr(game, "rooms", None) or {}

    def _is_frontierland_room(room):
        if room is None:
            return False
        plane = str(getattr(room, "plane", "") or "").strip().lower()
        map_id = str(getattr(room, "map_id", "") or "").strip().lower()
        return plane == FRONTIERLAND_PLANE or map_id == FRONTIERLAND_MAP_ID

    live = lookup_room(game, key)
    if live is not None and _is_frontierland_room(live):
        return live

    parsed_grid = map_ui.parse_grid_key(key)
    if parsed_grid and parsed_grid[0] == FRONTIERLAND_PREFIX:
        mouth = rooms.get(key)
        if mouth is not None:
            return mouth

    parsed_wild = parse_wilderness_room_key(key)
    if parsed_wild is not None:
        # Same Wilderness title exists on both atlases. Only return or
        # create the sealed-age twin when this save belongs there -- a
        # live 1861 miner at the same coords must not steal a Prime
        # homestead mouth on copyover (bug reports 1450 / 1459).
        if not _saved_key_is_frontierland(key, character):
            return None
        live_fl = _existing_frontierland_cell(game, key)
        if live_fl is not None:
            return live_fl
        macro, micro = parsed_wild
        try:
            cell = get_virtual_room(
                game, macro, micro, plane=FRONTIERLAND_PLANE,
            )
            if cell is not None:
                return cell
        except Exception:
            pass
        return None

    virt = resolve_virtual_overland_room_key(game, key)
    if virt is not None and _is_frontierland_room(virt):
        return virt
    return None


def _looks_like_mine_saved_key(room_key):
    """True when a persist key is a mine drift (canonical or legacy title).

    New saves use ``Mine:<mouth>:<room_id>``. Older copyovers stored the
    look title ``Mine drift (depth N)``, which is not in ``game.rooms``.
    """
    key = str(room_key or "")
    if not key:
        return False
    from engine.systems.mine_rooms import parse_mine_room_key

    if parse_mine_room_key(key) is not None:
        return True
    return key.lower().startswith("mine drift")


def _saved_key_is_frontierland(room_key, character=None):
    """True when this save belongs on the 1861 atlas, not Prime Earth.

    Copyover must not dump a miner onto ``home_room_key``. Infer from the
    persisted ``overland_plane``, the stranded-soul stamp, the live room
    plane, or Mine:/Frontierland keys.

    An explicit Prime ``overland_plane`` wins over a leftover stranded
    flag -- Choir already folded them home; copyover must not throw them
    back.
    """
    from engine.systems.mine_graph import plane_for_mouth_key
    from engine.systems.mine_rooms import parse_mine_room_key
    from engine.systems.overland import (
        FRONTIERLAND_MAP_ID,
        FRONTIERLAND_PLANE,
        FRONTIERLAND_PREFIX,
    )

    saved_plane = ""
    if character is not None:
        saved_plane = str(
            getattr(character, "overland_plane", None) or "",
        ).strip().lower()
    if saved_plane == FRONTIERLAND_PLANE:
        return True
    if saved_plane == "earth":
        return False
    if character is not None and getattr(character, "eve_stranded_soul", False):
        return True
    loc = getattr(character, "location", None) if character is not None else None
    if loc is not None:
        plane = str(getattr(loc, "plane", "") or "").strip().lower()
        map_id = str(getattr(loc, "map_id", "") or "").strip().lower()
        if plane == FRONTIERLAND_PLANE or map_id == FRONTIERLAND_MAP_ID:
            return True
        mouth = getattr(loc, "mouth_key", None) or ""
        if plane_for_mouth_key(mouth) == FRONTIERLAND_PLANE:
            return True
    key = str(room_key or "")
    if FRONTIERLAND_MAP_ID in key or key.startswith(FRONTIERLAND_PREFIX):
        return True
    parsed = parse_mine_room_key(key)
    if parsed is not None:
        return plane_for_mouth_key(parsed[0]) == FRONTIERLAND_PLANE
    # Legacy title keys cannot name the atlas. A stranded-soul walker
    # already returned True above; remaining Mine drift keys stay off
    # Prime home via heal/reseat, not this helper.
    return False


def _rematerialize_mine_from_character_stamps(game, character, stub_key=None):
    """Rebuild a mine drift from canonical key, mouth stamps, or foot coords.

    Legacy saves stored the look title ``Mine drift (depth N)`` instead of
    ``Mine:<mouth>:<room_id>``. Recover from the graph + the walker's
    last wilderness cell rather than dumping them home.
    """
    from engine.systems.mine_graph import (
        format_mouth_key,
        get_mouth,
    )
    from engine.systems.mine_rooms import get_mine_room, parse_mine_room_key
    from engine.systems.overland import FRONTIERLAND_MAP_ID

    loc = getattr(character, "location", None) if character is not None else None
    mouth = getattr(loc, "mouth_key", None) if loc is not None else None
    room_id = getattr(loc, "mine_room_id", None) if loc is not None else None
    if mouth and room_id:
        rebuilt = get_mine_room(game, mouth, room_id)
        if rebuilt is not None:
            return rebuilt
    parsed = parse_mine_room_key(stub_key)
    if parsed is not None:
        rebuilt = get_mine_room(game, parsed[0], parsed[1])
        if rebuilt is not None:
            return rebuilt
    macro = getattr(character, "macro_pos", None) if character is not None else None
    micro = getattr(character, "micro_pos", None) if character is not None else None
    if not (isinstance(macro, (tuple, list)) and len(macro) == 2):
        return None
    ux, uy = (5, 5)
    if isinstance(micro, (tuple, list)) and len(micro) == 2:
        ux, uy = int(micro[0]), int(micro[1])
    map_id = "earth_america"
    if _saved_key_is_frontierland(stub_key, character):
        map_id = FRONTIERLAND_MAP_ID
    mouth_key = format_mouth_key(
        map_id, int(macro[0]), int(macro[1]), ux, uy,
    )
    mouth_state = get_mouth(game, mouth_key, create=False)
    if mouth_state is None:
        return None
    rid = mouth_state.get("shaft_room_id")
    if not rid:
        rooms = mouth_state.get("rooms") or {}
        rid = next(iter(rooms), None)
    if not rid:
        return None
    return get_mine_room(game, mouth_key, rid)


def _rematerialize_mine_or_mouth(game, stub_key, character=None):
    """Rebuild a Mine: virtual room, or the wilderness mouth under it."""
    from engine.systems.mine_rooms import (
        parse_mine_room_key,
        resolve_mine_saved_room_key,
        wilderness_mouth_room,
    )

    mine = resolve_mine_saved_room_key(game, stub_key)
    if mine is not None:
        return mine
    loc = getattr(character, "location", None) if character is not None else None
    if _looks_like_mine_saved_key(stub_key) or getattr(
        loc, "virtual_mine", False,
    ):
        stamped = _rematerialize_mine_from_character_stamps(
            game, character, stub_key,
        )
        if stamped is not None:
            return stamped
    parsed = parse_mine_room_key(stub_key)
    if parsed is None:
        return None
    return wilderness_mouth_room(game, parsed[0])


def _place_off_stub(character, dest):
    """Move off a persistence stub onto ``dest``. Returns True on success."""
    if character is None or dest is None:
        return False
    if getattr(dest, "map_missing_stub", False):
        return False
    try:
        from engine.world import safe_place

        safe_place(character, dest)
    except Exception:
        try:
            character.move_to(dest)
        except Exception:
            return False
    loc = getattr(character, "location", None)
    if loc is dest:
        return True
    if loc is None or getattr(loc, "map_missing_stub", False):
        return False
    return getattr(loc, "key", None) == getattr(dest, "key", None)


def _resolve_saved_room(game, room_key, character_name, character=None):
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
    from engine import room_vnum as room_vnum_mod

    room = room_vnum_mod.lookup_room(game, room_key)
    if room is not None and not getattr(room, "map_missing_stub", False):
        return room
    # Dual-read: vehicle/logout persist may already store a VNUM while
    # game.rooms is still keyed by the legacy internal key.

    if room_vnum_mod.parse_vnum(room_key):
        found = room_vnum_mod.find_room_by_vnum(game, room_key)
        if found is not None:
            return found
    if room is not None and not _looks_like_mine_saved_key(room_key):
        return room
    from engine import hooks
    demesne_room = hooks.demesne_resolve_room_key(game, room_key)
    if demesne_room is not None:
        return demesne_room
    frontier_room = _resolve_frontierland_saved_room_key(
        game, room_key, character=character,
    )
    if frontier_room is not None:
        return frontier_room
    from engine.systems.overland import resolve_virtual_overland_room_key

    wild_room = resolve_virtual_overland_room_key(game, room_key)
    if wild_room is not None:
        return wild_room
    # Stable synthetic mouth keys (HomesteadSite:mx,my:ux,uy) — materialize
    # the live virtual cell; never map_missing_stub (homestead hygiene).
    # 1861-hosted plots clone a Prime mouth at the same coords -- prefer
    # the sealed-age cell when the body is still in that century.
    if str(room_key).startswith("HomesteadSite:"):
        try:
            rest = str(room_key)[len("HomesteadSite:"):]
            macro_part, micro_part = rest.split(":", 1)
            mx_s, my_s = macro_part.split(",", 1)
            ux_s, uy_s = micro_part.split(",", 1)
            from engine.systems.overland import FRONTIERLAND_PLANE, get_virtual_room

            macro = (int(mx_s), int(my_s))
            micro = (int(ux_s), int(uy_s))
            prefer_fl = _saved_key_is_frontierland(room_key, character)
            planes = (
                (FRONTIERLAND_PLANE, "earth")
                if prefer_fl
                else ("earth", FRONTIERLAND_PLANE)
            )
            for plane in planes:
                site_cell = get_virtual_room(
                    game, macro, micro, plane=plane,
                )
                if site_cell is not None:
                    return site_cell
        except (ValueError, TypeError):
            pass
    mine_room = _rematerialize_mine_or_mouth(game, room_key, character)
    if mine_room is not None:
        return mine_room
    if str(room_key).startswith("partyrun:"):
        party_room = hooks.resolve_party_run_saved_room(
            game, room_key, character=character,
        )
        if party_room is not None:
            return party_room
    _record_map_missing_stub(game, character_name, room_key)
    stub = Room(
        room_key,
        "The space you remember is thin here -- the map that held this "
        "place is not loaded. You have not moved; the world around you "
        "has not finished reforming.",
    )
    # Raw storage keys stay off player look (HB-26). Staff see the key
    # on gm where / staff look via map_missing_stub + staff_room_label.
    stub.staff_missing_map_key = room_key
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
    when possible. Mine drifts rematerialize from the graph. Sealed-age
    bodies never fall back to Prime ``home_room_key`` -- returning to
    the present is a typed hop, not a copyover accident.
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
    from engine.systems.overland import FRONTIERLAND_PLANE
    from engine.systems.vehicles import reseat_aboard_from_map_missing_stub

    from engine import hooks

    def _mark_emptied(stub_key):
        if stub_key and stub_key not in emptied_stub_keys:
            emptied_stub_keys.append(stub_key)

    for character in list(roster):
        loc = getattr(character, "location", None)
        if loc is None or not getattr(loc, "map_missing_stub", False):
            continue
        if hooks.should_skip_map_missing_stub_heal(character):
            continue
        stub_key = getattr(loc, "key", None) or ""
        stay_in_century = _saved_key_is_frontierland(stub_key, character)
        parsed = parse_wilderness_room_key(stub_key)
        if parsed is not None:
            planes = (
                (FRONTIERLAND_PLANE,)
                if stay_in_century
                else ("earth",)
            )
            placed = False
            for plane in planes:
                if place_on_overland(
                    character, game, parsed[0], parsed[1], plane=plane,
                ):
                    placed = True
                    break
            if placed:
                moved += 1
                _mark_emptied(stub_key)
                continue
        mine_dest = _rematerialize_mine_or_mouth(game, stub_key, character)
        if mine_dest is not None and _place_off_stub(character, mine_dest):
            moved += 1
            _mark_emptied(stub_key)
            continue
        frontier = _resolve_frontierland_saved_room_key(
            game, stub_key, character=character,
        )
        if frontier is not None and _place_off_stub(character, frontier):
            moved += 1
            _mark_emptied(stub_key)
            continue
        # Sealed-age / mine stubs stay put rather than waking in a
        # modern homestead. Staff hatch is the typed extract.
        if stay_in_century or _looks_like_mine_saved_key(stub_key):
            continue
        if reseat_aboard_from_map_missing_stub(character, game):
            moved += 1
            _mark_emptied(stub_key)
            continue
        # Boarded bodies wait for cabin restamp -- never dump them home.
        if getattr(character, "in_vehicle", None):
            continue
        dest = _safe_relocation_room(game, character)
        if dest is None:
            continue
        if _place_off_stub(character, dest):
            moved += 1
            _mark_emptied(stub_key)
            hooks.on_map_missing_stub_healed(character, stub_key)
    for stub_key in emptied_stub_keys:
        from engine.room_vnum import lookup_room

        room = lookup_room(game, stub_key)
        if room is None or not getattr(room, "map_missing_stub", False):
            continue
        if list(room.characters()):
            continue
        live_key = getattr(room, "key", None)
        rooms.pop(live_key, None)
        if stub_key and stub_key != live_key:
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
    name        TEXT PRIMARY KEY,          -- storage key; display names are not identity
    description TEXT NOT NULL,
    room_key    TEXT NOT NULL,             -- the Room.key they were last in
    stats       TEXT NOT NULL DEFAULT '{}', -- JSON blob; the stat spine lands here
    cnum        TEXT                       -- unique staff identity (DN00001)
    -- Migration 23 flips PRIMARY KEY from name to cnum. CREATE TABLE IF NOT
    -- EXISTS leaves old name-PK tables; do not declare cnum PK here.
);
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY,       -- SQLite auto-assigns rowids
    key         TEXT NOT NULL,
    description TEXT NOT NULL,
    -- 'gear' = job kit bag (Character.gear_bag), not surface inventory.
    holder_type TEXT NOT NULL CHECK (holder_type IN ('room', 'character', 'gear')),
    holder_key  TEXT NOT NULL,             -- room key or character storage name
    container   TEXT NOT NULL DEFAULT '{}' -- JSON: {"locked": bool, "loot": [...]}
    -- holder_cnum is added by migration 22 (not here): CREATE TABLE IF NOT
    -- EXISTS leaves old 6-col tables, and migration 5 rebuilds items without
    -- extra columns. Dual-write: character/gear rows stamp the owner CNUM;
    -- room rows stay NULL. Incremental DELETE still uses holder_key.
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
-- Lag S2: megachar life_log + property stash off the hot stats JSON column.
CREATE TABLE IF NOT EXISTS character_heavy_blobs (
    cnum    TEXT NOT NULL,
    shard   TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (cnum, shard)
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
    except Exception as exc:
        from engine import log_util

        log_util.ops("persistence", "wal_checkpoint failed", exc=exc)
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
    # Phase B: UNIQUE cnum column (storage name stays PRIMARY KEY).
    (21, "_migrate_characters_cnum_column"),
    # Holder remap: dual-write items.holder_cnum + homestead owner_cnum.
    (22, "_migrate_holder_cnum_columns"),
    # Phase B PK flip: cnum is the row identity; name stays UNIQUE login token.
    (23, "_migrate_characters_cnum_pk"),
    (24, """CREATE TABLE IF NOT EXISTS character_heavy_blobs (
    cnum    TEXT NOT NULL,
    shard   TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (cnum, shard)
)"""),
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


def _character_column_names(conn):
    """SQLite column names on ``characters`` (for dual-write / old DBs)."""
    return {row[1] for row in conn.execute("PRAGMA table_info(characters)")}


def _cnum_from_stats_blob(blob):
    """Validated CNUM from a persist stats JSON string, or None."""
    from engine.char_cnum import validate_cnum

    if not blob:
        return None
    try:
        saved = json.loads(blob) if isinstance(blob, str) else blob
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(saved, dict):
        return None
    raw = saved.get("cnum", None)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return validate_cnum(raw)
    except ValueError:
        return None


def _iter_saved_character_rows(conn):
    """Yield ``(name, description, room_key, stats, cnum)`` persist rows."""
    if "cnum" in _character_column_names(conn):
        sql = (
            "SELECT name, description, room_key, stats, cnum "
            "FROM characters"
        )
        for row in conn.execute(sql):
            yield row[0], row[1], row[2], row[3], row[4]
        return
    for row in conn.execute(
        "SELECT name, description, room_key, stats FROM characters"
    ):
        yield row[0], row[1], row[2], row[3], None


def _apply_persist_cnum_column(char, row_cnum):
    """Column wins over the stats blob when the UNIQUE tag is valid."""
    from engine.char_cnum import validate_cnum

    if not isinstance(row_cnum, str) or not str(row_cnum).strip():
        return
    try:
        char.cnum = validate_cnum(row_cnum)
    except ValueError:
        pass


def _migrate_characters_cnum_column(conn):
    """Add UNIQUE ``cnum`` on ``characters`` and backfill from the stats blob.

    Called as migration #21. Discrete ``execute`` calls stay inside the
    outer ``with conn`` transaction. Duplicate CNUMs in old blobs are
    nulled (account-linked row kept) so the unique index can land;
    ``heal_unique_character_cnums`` restamps the losers on load.
    """
    cols = _character_column_names(conn)
    if "cnum" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN cnum TEXT")
    rows = conn.execute(
        "SELECT name, stats, cnum FROM characters"
    ).fetchall()
    # cnum -> [(name, has_account), ...]
    groups = {}
    parsed_by_name = {}
    for name, blob, existing in rows:
        current = None
        if isinstance(existing, str) and existing.strip():
            try:
                from engine.char_cnum import validate_cnum
                current = validate_cnum(existing)
            except ValueError:
                current = None
        if current is None:
            current = _cnum_from_stats_blob(blob)
        parsed_by_name[name] = current
        if not current:
            continue
        has_account = False
        try:
            saved = json.loads(blob) if blob else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            saved = {}
        if isinstance(saved, dict) and (saved.get("account") or "").strip():
            has_account = True
        groups.setdefault(current, []).append((name, has_account))

    keep = {}
    for cnum, owners in groups.items():
        linked = [n for n, acct in owners if acct]
        if linked:
            winner = sorted(linked)[0]
        else:
            winner = sorted(n for n, _acct in owners)[0]
        keep[cnum] = winner

    for name, parsed in parsed_by_name.items():
        if parsed and keep.get(parsed) == name:
            conn.execute(
                "UPDATE characters SET cnum = ? WHERE name = ?",
                (parsed, name),
            )
        else:
            conn.execute(
                "UPDATE characters SET cnum = NULL WHERE name = ?",
                (name,),
            )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_characters_cnum "
        "ON characters(cnum) WHERE cnum IS NOT NULL AND cnum != ''"
    )


def _item_column_names(conn):
    """SQLite column names on ``items`` (holder_cnum dual-write / old DBs)."""
    return {row[1] for row in conn.execute("PRAGMA table_info(items)")}


def _homestead_column_names(conn):
    """SQLite column names on ``homestead_plots``, or empty if no table."""
    try:
        return {
            row[1] for row in conn.execute("PRAGMA table_info(homestead_plots)")
        }
    except sqlite3.OperationalError:
        return set()


def _iter_saved_item_rows(conn):
    """Yield item persist rows including optional ``holder_cnum``."""
    if "holder_cnum" in _item_column_names(conn):
        sql = (
            "SELECT key, description, holder_type, holder_key, container, "
            "holder_cnum FROM items"
        )
        for row in conn.execute(sql):
            yield row[0], row[1], row[2], row[3], row[4], row[5]
        return
    for row in conn.execute(
        "SELECT key, description, holder_type, holder_key, container "
        "FROM items"
    ):
        yield row[0], row[1], row[2], row[3], row[4], None


def _migrate_holder_cnum_columns(conn):
    """Dual-write owner CNUMs on items and homestead plots (migration #22).

    ``holder_key`` / ``owner_name`` stay the storage-name match keys so
    incremental DELETE-by-name still works. Character/gear rows stamp
    ``holder_cnum``; room floor rows stay NULL. Incremental apply also
    DELETE-by-``holder_cnum`` so a stale display name in ``holder_key``
    cannot orphan kit. One plot per CNUM via a
    partial unique index (not in ``_SCHEMA`` -- old tables would fail
    CREATE INDEX on a missing column before this migrate runs).
    """
    item_cols = _item_column_names(conn)
    if "holder_cnum" not in item_cols:
        conn.execute("ALTER TABLE items ADD COLUMN holder_cnum TEXT")
    char_cols = _character_column_names(conn)
    if "cnum" in char_cols:
        conn.execute(
            """
            UPDATE items SET holder_cnum = (
                SELECT cnum FROM characters
                WHERE characters.name = items.holder_key
            )
            WHERE holder_type IN ('character', 'gear')
            """
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_items_holder_cnum "
        "ON items(holder_cnum)"
    )

    homestead_cols = _homestead_column_names(conn)
    if not homestead_cols:
        return
    if "owner_cnum" not in homestead_cols:
        conn.execute(
            "ALTER TABLE homestead_plots ADD COLUMN owner_cnum TEXT"
        )
    if "cnum" in char_cols:
        conn.execute(
            """
            UPDATE homestead_plots SET owner_cnum = (
                SELECT cnum FROM characters
                WHERE characters.name = homestead_plots.owner_name
            )
            """
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_homestead_plots_owner_cnum "
        "ON homestead_plots(owner_cnum) "
        "WHERE owner_cnum IS NOT NULL AND owner_cnum != ''"
    )


def _character_pk_column(conn):
    """SQLite PRIMARY KEY column on ``characters``, or None."""
    for row in conn.execute("PRAGMA table_info(characters)"):
        if row[5]:
            return row[1]
    return None


def _fill_missing_row_cnums(conn):
    """Give every characters row a unique CNUM (needed before cnum PK)."""
    from engine.char_cnum import allocate_cnum, validate_cnum

    rows = conn.execute(
        "SELECT name, stats, cnum FROM characters"
    ).fetchall()
    taken = set()
    parsed_by_name = {}
    for name, blob, existing in rows:
        current = None
        if isinstance(existing, str) and existing.strip():
            try:
                current = validate_cnum(existing)
            except ValueError:
                current = None
        if current is None:
            blob_cnum = _cnum_from_stats_blob(blob)
            if blob_cnum and blob_cnum not in taken:
                current = blob_cnum
        parsed_by_name[name] = current
        if current:
            taken.add(current)
    for name, blob, existing in rows:
        current = parsed_by_name.get(name)
        if current:
            if existing != current:
                conn.execute(
                    "UPDATE characters SET cnum = ? WHERE name = ?",
                    (current, name),
                )
            continue
        minted = allocate_cnum(name or "xx", taken=taken)
        taken.add(minted)
        conn.execute(
            "UPDATE characters SET cnum = ? WHERE name = ?",
            (minted, name),
        )


def _migrate_characters_cnum_pk(conn):
    """Rebuild ``characters`` so ``cnum`` is the PRIMARY KEY (migration #23).

    ``name`` stays UNIQUE -- login still uses the storage key / given name.
    Idempotent when the PK is already ``cnum``. Discrete executes stay in
    the outer ``with conn`` transaction.
    """
    if _character_pk_column(conn) == "cnum":
        return
    cols = _character_column_names(conn)
    if "cnum" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN cnum TEXT")
    _fill_missing_row_cnums(conn)
    conn.execute("DROP TABLE IF EXISTS characters__cnum_pk")
    conn.execute(
        """
        CREATE TABLE characters__cnum_pk (
            name        TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            room_key    TEXT NOT NULL,
            stats       TEXT NOT NULL DEFAULT '{}',
            cnum        TEXT PRIMARY KEY
        )
        """
    )
    conn.execute(
        """
        INSERT INTO characters__cnum_pk
            (name, description, room_key, stats, cnum)
        SELECT name, description, room_key, stats, cnum FROM characters
        """
    )
    conn.execute("DROP TABLE characters")
    conn.execute("ALTER TABLE characters__cnum_pk RENAME TO characters")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_characters_cnum "
        "ON characters(cnum) WHERE cnum IS NOT NULL AND cnum != ''"
    )


# Name -> callable for migrations that cannot be a single SQL statement.
_MIGRATION_CALLABLES = {
    "_migrate_items_holder_gear": _migrate_items_holder_gear,
    "_migrate_add_help_tables": _migrate_add_help_tables,
    "_migrate_characters_cnum_column": _migrate_characters_cnum_column,
    "_migrate_holder_cnum_columns": _migrate_holder_cnum_columns,
    "_migrate_characters_cnum_pk": _migrate_characters_cnum_pk,
}


def _migrate(conn):
    """Bring the database up to the latest schema by applying every
    migration newer than its recorded schema_version, in order, and
    recording the new version after each -- so a boot that dies partway
    through resumes from the last completed migration instead of redoing
    (or skipping) one. Runs on every boot *and* immediately before a
    world-save apply (see ``_ensure_persist_schema``) so copyover can
    INSERT newly added columns without waiting for the next Game()
    connect. A database already at the latest version does nothing.

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


def _ensure_persist_schema(conn):
    """Apply pending migrations on this connection before INSERT/UPDATE.

    Auto-deploy overlays new ``persistence.py`` (new INSERT columns) onto
    a still-running Game whose SQLite schema_version is from the *previous*
    boot. Copyover reloads the module and calls ``save_world``; without a
    migrate here the INSERT hits ``no column named …`` and the Veil
    rewrite cancels (live 2026-09-10: ``characters.cnum``, migration 21).
    ``connect()`` already migrates on a fresh process; this covers the
    overlay-then-copyover window on the writer thread and the loop conn.
    """
    before = _schema_version(conn)
    _migrate(conn)
    after = _schema_version(conn)
    if after > before:
        print(
            f"[persistence] schema migrated {before} -> {after} before save",
            flush=True,
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
    roadtrip_mode = str(_str_meta("roadtrip_mode", "wall") or "wall").strip().lower()
    if roadtrip_mode not in ("wall", "real", "half"):
        roadtrip_mode = "wall"

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
        "roadtrip_mode": roadtrip_mode,
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
        road_mode = str(getattr(game, "roadtrip_mode", "wall") or "wall").strip().lower()
        if road_mode not in ("wall", "real", "half"):
            road_mode = "wall"
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('roadtrip_mode', ?)",
            (road_mode,),
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
    """Load heaven/hell soul banks + Heaven Stability from meta."""
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

    return {
        "heaven_soul_count": _int_meta("heaven_soul_count", 0),
        "hell_soul_count": _int_meta("hell_soul_count", 0),
        "next_phone_seq": _int_meta("next_phone_seq", 1000),
        "heaven_stability": _float_meta("heaven_stability", 100.0),
    }


def save_plane_soul_counts(conn, game):
    """Persist plane soul banks + Heaven Stability on Game (no SUPERS import)."""
    heaven = int(getattr(game, "heaven_soul_count", 0) or 0)
    hell = int(getattr(game, "hell_soul_count", 0) or 0)
    phone_seq = int(getattr(game, "next_phone_seq", 1000) or 1000)
    stability = float(getattr(game, "heaven_stability", 100.0) or 100.0)
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
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('heaven_stability', ?)",
            (str(stability),),
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


def collect_accounts_persist_packet(game, *, force_full=False):
    """Build immutable account SQL rows on the asyncio loop (Class C).

    Returns a packet the writer can apply without walking live ``Account``
    objects, or None when the in-memory dict is empty while SQLite still
    has rows (boot-order safety -- same skip as the old inline save).
    """
    from engine.accounts import Account, ensure_accounts_dict

    accounts = ensure_accounts_dict(game)
    if not accounts:
        return {
            "skip_empty_guard": True,
            "force_full": False,
            "rows": (),
            "removed": (),
            "new_hashes": {},
            "n_changed": 0,
            "n_removed": 0,
            "n_accounts": 0,
        }

    warm = bool(getattr(game, "_accounts_persist_warm", False))
    do_full = force_full or not warm
    prev = getattr(game, "_account_snapshot_hashes", None) or {}
    dirty = set(getattr(game, "_persist_dirty_accounts", None) or set())
    new_hashes = {}
    alive = set()
    rows = []
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
        if not do_full and key not in dirty and prev.get(key) == digest:
            continue
        rows.append((name, display, account.password_hash or "", blob, key))

    removed = []
    if not do_full:
        for old_key in prev:
            if old_key not in alive:
                removed.append(old_key)

    return {
        "skip_empty_guard": False,
        "force_full": do_full,
        "rows": tuple(rows),
        "removed": tuple(removed),
        "new_hashes": new_hashes,
        "n_changed": len(rows),
        "n_removed": len(removed),
        "n_accounts": len(alive),
    }


def apply_accounts_persist_packet(conn, packet):
    """SQL-only account rewrite from a frozen packet."""
    if not packet or packet.get("skip_empty_guard"):
        if packet and packet.get("skip_empty_guard"):
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
    do_full = bool(packet.get("force_full"))
    with conn:
        if do_full:
            conn.execute("DELETE FROM accounts")
        for name, display, password_hash, blob, key in packet.get("rows") or ():
            if not do_full:
                conn.execute("DELETE FROM accounts WHERE name=?", (name,))
                if key != name:
                    conn.execute(
                        "DELETE FROM accounts WHERE lower(name)=?", (key,),
                    )
            conn.execute(
                "INSERT INTO accounts "
                "(name, display_name, password_hash, data) "
                "VALUES (?, ?, ?, ?)",
                (name, display, password_hash, blob),
            )
        if not do_full:
            for old_key in packet.get("removed") or ():
                conn.execute(
                    "DELETE FROM accounts WHERE lower(name)=?", (old_key,),
                )


def ack_accounts_persist_packet(game, packet):
    """Stamp account hashes after SQL committed (loop, not writer)."""
    if game is None or not packet or packet.get("skip_empty_guard"):
        return
    game._account_snapshot_hashes = dict(packet.get("new_hashes") or {})
    game._accounts_persist_warm = True
    game._persist_dirty_accounts = set()
    game._last_accounts_save_stats = {
        "force_full": bool(packet.get("force_full")),
        "n_changed": int(packet.get("n_changed") or 0),
        "n_removed": int(packet.get("n_removed") or 0),
        "n_accounts": int(packet.get("n_accounts") or 0),
    }


def save_accounts(conn, game, *, force_full=False):
    """Write accounts; full wipe on first save, then dirty/hash incremental.

    Live autosave used to ``DELETE FROM accounts`` every minute -- that alone
    was multi-second under load. After the first successful save, only
    changed / dirty accounts are rewritten and removed names are deleted.
    Background-writer autosave collects the packet on the loop.
    """
    packet = collect_accounts_persist_packet(game, force_full=force_full)
    apply_accounts_persist_packet(conn, packet)
    ack_accounts_persist_packet(game, packet)


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


def load_help_overlay_mode(conn):
    """Load GM help overlay resolution mode from meta (default hedit)."""
    return _load_meta_dict(conn, "help_overlay_mode")


def save_help_overlay_mode(conn, game):
    """Persist game.help_overlay_mode onto the meta table."""
    from engine import hooks
    hooks.save_help_overlay_mode_meta(conn, game)


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


def load_ground_item_decay_tuning(conn):
    """Load GM ground-item decay TTL overrides from meta (empty = code default)."""
    return _load_meta_dict(conn, "ground_item_decay_tuning")


def save_ground_item_decay_tuning(conn, game):
    """Persist game.ground_item_decay_tuning overrides onto the meta table."""
    _save_meta_dict(
        conn, "ground_item_decay_tuning", game, "ground_item_decay_tuning",
    )


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


def load_winchester_road(conn):
    """Load Winchester board-tour circuit toggle from meta (default {})."""
    return _load_meta_dict(conn, "winchester_road")


def save_winchester_road(conn, game):
    """Persist game.winchester_road (enabled) to meta."""
    _save_meta_dict(conn, "winchester_road", game, "winchester_road")


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
        from engine.item_inum import persist_inum_text, ensure_item_inum

        ensure_item_inum(entry)
        inum = persist_inum_text(entry)
        if inum:
            blob["inum"] = inum
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
            from engine.item_inum import apply_saved_inum

            apply_saved_inum(item, entry.get("inum"))
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


def _restore_god_weapon_fields(item, state):
    """Reattach bound / worthy / recall / elemental stamps on mythic arms."""
    if state.get("true_artifact"):
        item.true_artifact = True
    if state.get("eve_colt_loan"):
        item.eve_colt_loan = True
        item.true_artifact = False
    owner = state.get("bound_owner_key")
    if isinstance(owner, str) and owner.strip():
        item.bound_owner_key = owner.strip()
    if state.get("worthy_wield"):
        item.worthy_wield = True
    if state.get("recall_bound"):
        item.recall_bound = True
    if state.get("never_miss"):
        item.never_miss = True
    if state.get("cuts_warded"):
        item.cuts_warded = True
    if state.get("breaks_wards"):
        item.breaks_wards = True
    elem = state.get("elemental_damage")
    if isinstance(elem, str) and elem.strip():
        item.elemental_damage = elem.strip().lower()


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


def _restore_loot_payloads(item, state):
    """Per-unit strongbox loot tables (stacked sealed boxes, bug report 1288)."""
    raw = state.get("loot_payloads")
    if not isinstance(raw, list) or not raw:
        return
    item.loot_payloads = [_loot_from_json(p) for p in raw]


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
    if state.get("is_joint_pack"):
        item.is_joint_pack = True
    if state.get("is_cigarette_pack"):
        item.is_cigarette_pack = True
    if state.get("smoke_herb_id"):
        item.smoke_herb_id = str(state["smoke_herb_id"]).strip()
    if state.get("is_pipe"):
        item.is_pipe = True
    if state.get("is_bong"):
        item.is_bong = True
        item.is_pipe = True
    if state.get("glass_crafted"):
        item.glass_crafted = True
    if state.get("pipe_max_puffs") is not None:
        try:
            item.pipe_max_puffs = int(state["pipe_max_puffs"])
        except (TypeError, ValueError):
            pass
    if state.get("pipe_herb_id"):
        item.pipe_herb_id = str(state["pipe_herb_id"]).strip()
    if state.get("herb_quality"):
        item.herb_quality = str(state["herb_quality"]).strip()
    if state.get("bag_charges") is not None:
        try:
            item.bag_charges = int(state["bag_charges"])
        except (TypeError, ValueError):
            pass
    if state.get("last_cure_week") is not None:
        try:
            item.last_cure_week = int(state["last_cure_week"])
        except (TypeError, ValueError):
            pass
    if state.get("pipe_puffs") is not None:
        try:
            item.pipe_puffs = int(state["pipe_puffs"])
        except (TypeError, ValueError):
            pass
    for key in (
        "strain_name",
        "strain_slug",
        "splice_herb_id",
        "strain_gen",
        "pipe_strain_name",
        "pipe_strain_slug",
        "pipe_splice_herb_id",
        "pipe_strain_gen",
    ):
        if state.get(key):
            setattr(item, key, str(state[key]).strip())


def _persist_text_is_corrupt_repr(text):
    if not isinstance(text, str):
        return True
    value = text.strip()
    if not value:
        return True
    return value.startswith("<") and " object at 0x" in value


def _persist_item_field(item, attr, *, fallback="item"):
    """SQLite-safe plain key/description for held-item rows."""
    from engine import hooks

    raw = getattr(item, attr, None)
    if isinstance(raw, str):
        text = raw.strip()
        if text and not _persist_text_is_corrupt_repr(text):
            return text
    if attr == "key":
        painted = hooks.item_display_key(item, None)
        from engine.style import strip_ansi

        text = strip_ansi(painted).strip() if painted else ""
        if text and not _persist_text_is_corrupt_repr(text):
            return text
    if attr == "description":
        desc = getattr(item, "description", None)
        if isinstance(desc, str):
            text = desc.strip()
            if text and not _persist_text_is_corrupt_repr(text):
                return text
        key_text = _persist_item_field(item, "key", fallback=fallback)
        if key_text and not _persist_text_is_corrupt_repr(key_text):
            return key_text
    return fallback


def _bag_contents_for_json(item):
    """Serialize nested bag rows for the container blob."""
    contents = getattr(item, "bag_contents", None) or []
    out = []
    for sub in contents:
        if not isinstance(sub, Item):
            continue
        out.append({
            "key": _persist_item_field(sub, "key"),
            "description": _persist_item_field(
                sub, "description", fallback=_persist_item_field(sub, "key"),
            ),
            "container": _item_container_dict(sub),
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
            "key": _persist_item_field(sub, "key"),
            "description": _persist_item_field(
                sub, "description", fallback=_persist_item_field(sub, "key"),
            ),
            "container": _item_container_dict(sub),
        })
    return out


def _clothing_contents_for_json(item):
    """Serialize items tucked in a garment's pockets."""
    contents = getattr(item, "clothing_contents", None) or []
    out = []
    for sub in contents:
        if not isinstance(sub, Item):
            continue
        out.append({
            "key": _persist_item_field(sub, "key"),
            "description": _persist_item_field(
                sub, "description", fallback=_persist_item_field(sub, "key"),
            ),
            "container": _item_container_dict(sub),
        })
    return out


def _restore_bag_fields(item, state):
    """Reattach wearable bag stamps from a container blob."""
    if state.get("is_bag"):
        item.is_bag = True
    if state.get("is_gear_bag"):
        item.is_gear_bag = True
    if state.get("is_profession_bag"):
        item.is_profession_bag = True
    if state.get("is_generalist_bag"):
        item.is_generalist_bag = True
    if state.get("is_extradim"):
        item.is_extradim = True
    if state.get("is_pocket_weave"):
        item.is_pocket_weave = True
    if state.get("outfit_bundle"):
        item.outfit_bundle = True
    for outfit_field in ("outfit_set_id", "outfit_set_name", "outfit_set_tag"):
        raw_outfit = state.get(outfit_field)
        if isinstance(raw_outfit, str) and raw_outfit.strip():
            setattr(item, outfit_field, raw_outfit.strip())
    profession = state.get("profession")
    if isinstance(profession, str) and profession.strip():
        item.profession = profession.strip().lower()
    pref = state.get("preferred_slot")
    if pref in ("back", "shoulder", "hip"):
        item.preferred_slot = pref
    pstow = state.get("profession_stow")
    if isinstance(pstow, str) and pstow.strip():
        item.profession_stow = pstow.strip().lower()
    if state.get("important"):
        item.important = True
    if state.get("keep_kit"):
        item.keep_kit = True
    if state.get("keep_kit_exempt"):
        item.keep_kit_exempt = True
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
    if worn in ("back", "shoulder", "hip", "pocket"):
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
    if state.get("pocket_capacity") is not None:
        try:
            item.pocket_capacity = int(state["pocket_capacity"])
        except (TypeError, ValueError):
            pass
    raw_pockets = state.get("clothing_contents") or []
    pocket_restored = []
    for row in raw_pockets:
        if not isinstance(row, dict):
            continue
        sub = item_from_saved_container(
            row.get("key") or "item",
            row.get("description") or row.get("key") or "item",
            row.get("container") or {},
        )
        pocket_restored.append(sub)
    item.clothing_contents = pocket_restored


def _restore_body_harvest_fields(item, state):
    """Re-apply corpse harvest flags + Origin stamps from a container blob."""
    if state.get("body_harvested_meat"):
        item.body_harvested_meat = True
    if state.get("body_harvested_hide"):
        item.body_harvested_hide = True
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
    if state.get("body_type"):
        item.body_type = str(state["body_type"]).strip().lower()
    if state.get("body_alignment"):
        item.body_alignment = str(state["body_alignment"]).strip().lower()
    if state.get("body_tier") is not None:
        try:
            item.body_tier = int(state["body_tier"])
        except (TypeError, ValueError):
            pass
    if state.get("body_celestial_allegiance"):
        item.body_celestial_allegiance = str(
            state["body_celestial_allegiance"]
        ).strip().lower()
    if state.get("body_celestial_title"):
        item.body_celestial_title = str(
            state["body_celestial_title"]
        ).strip().lower()
    if state.get("body_no_blood"):
        item.body_no_blood = True
    if "body_yields_meat" in state and state.get("body_yields_meat") is not None:
        item.body_yields_meat = bool(state["body_yields_meat"])
    if "body_yields_hide" in state and state.get("body_yields_hide") is not None:
        item.body_yields_hide = bool(state["body_yields_hide"])
    if state.get("body_creature_id"):
        item.body_creature_id = str(state["body_creature_id"]).strip()
    listed = state.get("body_butcher_yields")
    if isinstance(listed, list):
        item.body_butcher_yields = [
            str(x).strip() for x in listed if str(x).strip()
        ]
    custody = state.get("custody")
    if custody:
        item.custody = str(custody).strip().lower()
    if state.get("morgue_drawer") is not None:
        try:
            item.morgue_drawer = int(state["morgue_drawer"])
        except (TypeError, ValueError):
            pass
    if state.get("morgue_tray_in_at_tick") is not None:
        try:
            item.morgue_tray_in_at_tick = int(state["morgue_tray_in_at_tick"])
        except (TypeError, ValueError):
            pass
    stored_in = state.get("stored_in_receptacle_key")
    if stored_in:
        item.stored_in_receptacle_key = str(stored_in).strip()
    if state.get("body_receptacle"):
        item.body_receptacle = True
    fam = state.get("receptacle_family")
    if fam:
        item.receptacle_family = str(fam).strip().lower()
    if state.get("receptacle_slot") is not None:
        try:
            item.receptacle_slot = int(state["receptacle_slot"])
        except (TypeError, ValueError):
            pass
    stored_body_key = state.get("stored_body_key")
    if stored_body_key:
        item.stored_body_key = str(stored_body_key).strip()
    stone_label = state.get("plot_stone_label")
    if stone_label:
        item.plot_stone_label = str(stone_label).strip()
    stone_identity = state.get("plot_stone_identity")
    if stone_identity:
        item.plot_stone_identity = str(stone_identity).strip()
    for field_name in ("custom_look_in_empty_lines", "custom_look_in_lines"):
        custom_lines = state.get(field_name)
        if isinstance(custom_lines, list) and custom_lines:
            setattr(
                item,
                field_name,
                [str(line).strip() for line in custom_lines if str(line).strip()],
            )
    death_record = state.get("death_record")
    if isinstance(death_record, dict):
        item.death_record = dict(death_record)
    if state.get("forensic_autopsied"):
        item.forensic_autopsied = True
    if state.get("evidence_tampered"):
        item.evidence_tampered = True


def _phone_media_fields_for_json(item):
    """Tech-media handset fields (PR1+) beside phone_number / is_phone."""
    return {
        "uid": getattr(item, "uid", None),
        "nickname": getattr(item, "nickname", None),
        "is_smartphone": bool(getattr(item, "is_smartphone", False)),
        "is_pager": bool(getattr(item, "is_pager", False)),
        "is_landline": bool(getattr(item, "is_landline", False)),
        "is_ham": bool(getattr(item, "is_ham", False)),
        "is_dashcam": bool(getattr(item, "is_dashcam", False)),
        "sms_threads": list(getattr(item, "sms_threads", None) or []),
        "voicemail": list(getattr(item, "voicemail", None) or []),
        "protect_rating": getattr(item, "protect_rating", None),
        "protect_signature": getattr(item, "protect_signature", None),
        "protect_until": getattr(item, "protect_until", None),
        "warren_nick_until": getattr(item, "warren_nick_until", None),
        "protect_quality": getattr(item, "protect_quality", None),
        "installed_apps": list(getattr(item, "installed_apps", None) or []),
        "ringtone": getattr(item, "ringtone", None),
        "text_tone": getattr(item, "text_tone", None),
        "wallpaper": getattr(item, "wallpaper", None),
        "backup_on": bool(getattr(item, "backup_on", False)),
        "battery_ticks": getattr(item, "battery_ticks", None),
        "bug_planter_key": getattr(item, "bug_planter_key", None),
        "dashcam_buffer": getattr(item, "dashcam_buffer", None),
        "answering_tape": list(getattr(item, "answering_tape", None) or []),
        "answering_machine": bool(getattr(item, "answering_machine", False)),
    }


def _restore_phone_media_fields(item, state):
    """Restore tech-media handset fields from a container blob."""
    uid = state.get("uid") or state.get("item_uid")
    if uid:
        item.uid = str(uid).strip()
    nick = state.get("nickname")
    if nick:
        item.nickname = str(nick).strip()
    if state.get("is_smartphone"):
        item.is_smartphone = True
    if state.get("is_pager"):
        item.is_pager = True
    if state.get("is_landline"):
        item.is_landline = True
        item.furniture = True
    if state.get("is_ham"):
        item.is_ham = True
    if state.get("is_dashcam"):
        item.is_dashcam = True
    if state.get("answering_machine"):
        item.answering_machine = True
    for list_field in (
        "sms_threads", "voicemail", "installed_apps", "answering_tape",
    ):
        raw = state.get(list_field)
        if isinstance(raw, list):
            setattr(item, list_field, list(raw))
    for field in (
        "protect_rating",
        "protect_until",
        "protect_quality",
        "battery_ticks",
        "warren_nick_until",
    ):
        if state.get(field) is not None:
            setattr(item, field, state[field])
    if state.get("protect_signature") is not None:
        item.protect_signature = state["protect_signature"]
    if state.get("bug_planter_key"):
        item.bug_planter_key = str(state["bug_planter_key"]).strip()
    if state.get("dashcam_buffer") is not None:
        item.dashcam_buffer = state["dashcam_buffer"]
    if state.get("backup_on"):
        item.backup_on = True
    for cosmetic in ("ringtone", "text_tone", "wallpaper"):
        val = state.get(cosmetic)
        if isinstance(val, str) and val.strip():
            setattr(item, cosmetic, val.strip())


def _item_container_blob(item):
    """JSON for the items.container column: an Item's locked/loot state (a
    dungeon lockbox's whole reward, world.make_lockbox), same reasoning as
    characters.stats -- one JSON blob means a plain flavor Item (locked=
    False, loot=[]) and a live lockbox round-trip through the same column
    with no schema difference between them. `is_body` (section 6) rides
    the same blob for the same reason -- a body Item is just another Item
    row, no schema change needed. Lodging adds furniture / owner_key / need
    so beds survive a restart with their sleep tag and claim stamp.
    Staff item instance INUM dual-writes here (no new items column).
    """
    from engine.item_inum import ensure_item_inum, persist_inum_text

    ensure_item_inum(item)
    return json.dumps({
        "locked": item.locked,
        "loot": _loot_for_json(item.loot),
        "loot_payloads": (
            [_loot_for_json(p) for p in item.loot_payloads]
            if isinstance(getattr(item, "loot_payloads", None), list)
            and item.loot_payloads
            else None
        ),
        "is_body": item.is_body,
        "is_buried": getattr(item, "is_buried", False),
        "relic": getattr(item, "relic", None),
        "relic_tier": (
            int(item.relic_tier)
            if getattr(item, "relic_tier", None) is not None
            else None
        ),
        "furniture": getattr(item, "furniture", False),
        # Map-seeded public props (diner chairs, library desks) -- not
        # homestead install kits. Must round-trip or uninstall can farm them.
        "world_fixture": bool(getattr(item, "world_fixture", False)),
        # Civic shop curb fixtures (supers/player_shops.py) -- must round-trip
        # or save/load drops civic_fixture and boot stacks duplicates.
        "civic_fixture": bool(getattr(item, "civic_fixture", False)),
        "fixture_id": getattr(item, "fixture_id", None),
        "shop_id": getattr(item, "shop_id", None),
        "player_shop_fixture": bool(getattr(item, "player_shop_fixture", False)),
        # Player glass-shop torch bench (supers/glass_craft.py) -- heal-only
        # fixture. Flag round-trips if a row is saved; _persistable_floor_item
        # skips floor writes so copyover cannot stack (bug report 1725).
        "glassbench_prop": bool(getattr(item, "glassbench_prop", False)),
        "owner_key": getattr(item, "owner_key", None),
        "need": getattr(item, "need", None),
        "provides_light": bool(getattr(item, "provides_light", False)),
        "catalog_id": getattr(item, "catalog_id", None),
        # Staff instance tag (AD00001). Dual-write only -- not the PK.
        "inum": persist_inum_text(item),
        "magic_focus_id": getattr(item, "magic_focus_id", "") or "",
        "magic_focus_kind": getattr(item, "magic_focus_kind", "") or "",
        "magic_focus_target_key": getattr(item, "magic_focus_target_key", "") or "",
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
        "god_forge_infused_until": int(
            getattr(item, "god_forge_infused_until", 0) or 0
        ),
        "god_forge_infuse_damage_bonus": getattr(
            item, "god_forge_infuse_damage_bonus", None,
        ),
        "god_forge_infuse_accuracy_bonus": getattr(
            item, "god_forge_infuse_accuracy_bonus", None,
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
        "angel_blade_growth_mods": list(
            getattr(item, "angel_blade_growth_mods", None) or []
        )
        if isinstance(getattr(item, "angel_blade_growth_mods", None), list)
        else None,
        "angel_blade_budget_earned": (
            int(item.angel_blade_budget_earned)
            if getattr(item, "angel_blade_budget_earned", None) is not None
            else None
        ),
        "angel_blade_prototype_seed": getattr(
            item, "angel_blade_prototype_seed", None,
        ),
        "bound_angel_twin": bool(getattr(item, "bound_angel_twin", False)),
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
        "wet": bool(getattr(item, "wet", False))
        if hasattr(item, "wet") else None,
        # Home grocery stock window (fridge furniture); None / absent = empty.
        "stock_until_tick": getattr(item, "stock_until_tick", None),
        # Pet bowl servings (explicit 0 = empty; None falls back to stock window).
        "pet_servings": getattr(item, "pet_servings", None),
        # Vampire blood-pantry window (separate from mortal food stock).
        "blood_stock_until_tick": getattr(
            item, "blood_stock_until_tick", None
        ),
        # Corpse floor age (Wendigo larder stock gate); absent = unstamped.
        "body_dropped_tick": getattr(item, "body_dropped_tick", None),
        # Corpse harvest flags (supers/scavenge.py) -- one take each.
        "body_harvested_meat": bool(getattr(item, "body_harvested_meat", False)),
        "body_harvested_hide": bool(getattr(item, "body_harvested_hide", False)),
        "body_drained": bool(getattr(item, "body_drained", False)),
        "body_siphoned": bool(getattr(item, "body_siphoned", False)),
        "body_dmb_drawn": bool(getattr(item, "body_dmb_drawn", False)),
        "body_origin": getattr(item, "body_origin", None),
        "body_path": getattr(item, "body_path", None),
        "body_type": getattr(item, "body_type", None),
        "body_alignment": getattr(item, "body_alignment", None),
        "body_tier": getattr(item, "body_tier", None),
        "body_celestial_allegiance": getattr(
            item, "body_celestial_allegiance", None,
        ),
        "body_celestial_title": getattr(
            item, "body_celestial_title", None,
        ),
        "body_no_blood": bool(getattr(item, "body_no_blood", False)),
        "body_yields_meat": getattr(item, "body_yields_meat", None),
        "body_yields_hide": getattr(item, "body_yields_hide", None),
        "body_creature_id": getattr(item, "body_creature_id", None),
        "body_butcher_yields": getattr(item, "body_butcher_yields", None),
        # Death forensics custody (supers/death_forensics.py) -- scene -> tray -> plot.
        "custody": getattr(item, "custody", None),
        "morgue_drawer": getattr(item, "morgue_drawer", None),
        "morgue_tray_in_at_tick": getattr(item, "morgue_tray_in_at_tick", None),
        "stored_in_receptacle_key": getattr(item, "stored_in_receptacle_key", None),
        "death_record": getattr(item, "death_record", None),
        "forensic_autopsied": bool(getattr(item, "forensic_autopsied", False)),
        "evidence_tampered": bool(getattr(item, "evidence_tampered", False)),
        "body_receptacle": bool(getattr(item, "body_receptacle", False)),
        "receptacle_family": getattr(item, "receptacle_family", None),
        "receptacle_slot": getattr(item, "receptacle_slot", None),
        "stored_body_key": getattr(item, "stored_body_key", None),
        "plot_stone_label": getattr(item, "plot_stone_label", None),
        "plot_stone_identity": getattr(item, "plot_stone_identity", None),
        "custom_look_in_empty_lines": getattr(item, "custom_look_in_empty_lines", None),
        "custom_look_in_lines": getattr(item, "custom_look_in_lines", None),
        # Abandoned floor loot grace (Cadence scavengers); absent = legacy pile.
        "floor_dropped_tick": getattr(item, "floor_dropped_tick", None),
        # Beneath Lucifer's Cage TTL; absent = stamp on next vault decay tick.
        "vault_decay_at_tick": getattr(item, "vault_decay_at_tick", None),
        # Physical phone line id (supers/phone.py); absent = not a phone.
        "phone_number": getattr(item, "phone_number", None),
        "is_phone": bool(getattr(item, "is_phone", False)),
        "is_payphone": bool(getattr(item, "is_payphone", False)),
        **_phone_media_fields_for_json(item),
        "is_ethereal": bool(getattr(item, "is_ethereal", False)),
        "is_spirit_mirror": bool(getattr(item, "is_spirit_mirror", False)),
        "spirit_mirror_source_key": getattr(item, "spirit_mirror_source_key", None),
        "is_god_twin_visual": bool(getattr(item, "is_god_twin_visual", False)),
        # Rowena tower keys -- flags must survive Echo logout or a leftover
        # flavor stack eats the next drop (no portal_tower_key on merge).
        "nostack": bool(getattr(item, "nostack", False)),
        "portal_tower_key": bool(getattr(item, "portal_tower_key", False)),
        "portal_key_floor": (
            int(item.portal_key_floor)
            if getattr(item, "portal_key_floor", None) else None
        ),
        "rowena_portal_run_id": getattr(item, "rowena_portal_run_id", None),
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
        "three_bloods_washed": bool(getattr(item, "three_bloods_washed", False)),
        "dmb_spiked": bool(getattr(item, "dmb_spiked", False)),
        "stack_charges": (
            int(item.stack_charges)
            if getattr(item, "stack_charges", None) is not None
            else None
        ),
        "weapon_voice": getattr(item, "weapon_voice", None),
        "artifact_lexicon": getattr(item, "artifact_lexicon", None),
        "true_artifact": bool(getattr(item, "true_artifact", False)),
        "eve_colt_loan": bool(getattr(item, "eve_colt_loan", False)),
        "bound_owner_key": getattr(item, "bound_owner_key", None),
        "worthy_wield": bool(getattr(item, "worthy_wield", False)),
        "recall_bound": bool(getattr(item, "recall_bound", False)),
        "never_miss": bool(getattr(item, "never_miss", False)),
        "cuts_warded": bool(getattr(item, "cuts_warded", False)),
        "breaks_wards": bool(getattr(item, "breaks_wards", False)),
        "elemental_damage": getattr(item, "elemental_damage", None),
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
        "herb_quality": getattr(item, "herb_quality", None),
        "bag_charges": getattr(item, "bag_charges", None),
        "last_cure_week": getattr(item, "last_cure_week", None),
        "is_joint": bool(getattr(item, "is_joint", False)),
        "is_joint_pack": bool(getattr(item, "is_joint_pack", False)),
        "is_cigarette_pack": bool(getattr(item, "is_cigarette_pack", False)),
        "smoke_herb_id": getattr(item, "smoke_herb_id", None),
        "is_pipe": bool(getattr(item, "is_pipe", False)),
        "is_bong": bool(getattr(item, "is_bong", False)),
        "glass_crafted": bool(getattr(item, "glass_crafted", False)),
        "pipe_max_puffs": (
            int(item.pipe_max_puffs)
            if getattr(item, "pipe_max_puffs", None) is not None
            else None
        ),
        "pipe_herb_id": getattr(item, "pipe_herb_id", None),
        "pipe_puffs": (
            int(item.pipe_puffs)
            if getattr(item, "pipe_puffs", None) is not None
            else None
        ),
        "strain_name": getattr(item, "strain_name", None),
        "strain_slug": getattr(item, "strain_slug", None),
        "splice_herb_id": getattr(item, "splice_herb_id", None),
        "strain_gen": getattr(item, "strain_gen", None),
        "pipe_strain_name": getattr(item, "pipe_strain_name", None),
        "pipe_strain_slug": getattr(item, "pipe_strain_slug", None),
        "pipe_splice_herb_id": getattr(item, "pipe_splice_herb_id", None),
        "pipe_strain_gen": getattr(item, "pipe_strain_gen", None),
        "is_bag": bool(getattr(item, "is_bag", False)),
        "is_gear_bag": bool(getattr(item, "is_gear_bag", False)),
        "is_profession_bag": bool(getattr(item, "is_profession_bag", False)),
        "is_generalist_bag": bool(getattr(item, "is_generalist_bag", False)),
        "is_extradim": bool(getattr(item, "is_extradim", False)),
        "is_pocket_weave": bool(getattr(item, "is_pocket_weave", False)),
        "outfit_bundle": bool(getattr(item, "outfit_bundle", False)),
        "outfit_set_id": getattr(item, "outfit_set_id", None),
        "outfit_set_name": getattr(item, "outfit_set_name", None),
        "outfit_set_tag": getattr(item, "outfit_set_tag", None),
        "profession": getattr(item, "profession", None),
        "preferred_slot": getattr(item, "preferred_slot", None),
        "profession_stow": getattr(item, "profession_stow", None),
        "important": bool(getattr(item, "important", False)),
        "keep_kit": bool(getattr(item, "keep_kit", False)),
        "keep_kit_exempt": bool(getattr(item, "keep_kit_exempt", False)),
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
        "pocket_capacity": (
            int(item.pocket_capacity)
            if getattr(item, "pocket_capacity", None) is not None
            else None
        ),
        "clothing_contents": _clothing_contents_for_json(item),
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
        "laptop_battery_ticks": (
            int(item.laptop_battery_ticks)
            if getattr(item, "laptop_battery_ticks", None) is not None
            else None
        ),
        "virus_bricked_until": (
            int(item.virus_bricked_until)
            if getattr(item, "virus_bricked_until", None) is not None
            else None
        ),
    })


def _item_container_dict(item):
    """JSON-ready container dict for save rows.

    Callers used to ``json.loads(_item_container_blob(item))``. One nested
    item that cannot dump (or that dumps as junk) must not abort a whole
    character snapshot -- fail-soft to ``{}`` and log, matching load-world
    skip of a corrupt items row.
    """
    try:
        return json.loads(_item_container_blob(item))
    except (TypeError, ValueError, OverflowError) as exc:
        from engine import log_util
        log_util.ops(
            "persistence",
            "skip corrupt item container at save "
            f"key={getattr(item, 'key', None)!r} ({exc!r})",
        )
        return {}


def _restore_portal_key_fields(item, state):
    """Apply saved Rowena tower-key flags (absent on older flavor stacks)."""
    if not isinstance(state, dict) or item is None:
        return
    if state.get("nostack"):
        item.nostack = True
    if state.get("portal_tower_key"):
        item.portal_tower_key = True
        item.nostack = True
    if state.get("portal_key_floor") is not None:
        try:
            item.portal_key_floor = int(state["portal_key_floor"])
        except (TypeError, ValueError):
            pass
    run_id = state.get("rowena_portal_run_id")
    if run_id:
        item.rowena_portal_run_id = str(run_id)


_CHAR_INSERT_SQL = (
    "INSERT INTO characters (name, description, room_key, stats, cnum) "
    "VALUES (?, ?, ?, ?, ?)"
)
_ITEM_INSERT_SQL = (
    "INSERT INTO items "
    "(key, description, holder_type, holder_key, container, holder_cnum) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)


def _persist_room_identity(room):
    """VNUM identity for hand rooms, else ``room.key`` (grid / unstamped)."""
    from engine.room_vnum import internal_room_key

    key = internal_room_key(room)
    return key or (getattr(room, "key", None) or "")


def _login_body_must_save(obj):
    """True for an account-linked play body that must keep a SQLite row.

    Skip-save plus a force-full ``DELETE FROM characters`` (or incremental
    ``removed_chars``) is how a live PC can vanish from the world
    (bug report 1437 -- Daniel). Pit run tags, GM-form flags on the Echo,
    and missing rooms must not drop that row. Spirits, husks, and guests
    still skip.
    """
    from engine.char_identity import is_account_linked_body

    return is_account_linked_body(obj)


def _iter_persist_characters(game):
    """Yield live bodies with account-linked PCs first.

    Duplicate storage keys must never let an NPC snapshot win the
    SQLite PRIMARY KEY. Login bodies write first; NPCs with the same
    key skip in ``_should_skip_character_save``.
    """
    from engine.char_index import iter_characters

    bodies = list(iter_characters(game))
    bodies.sort(
        key=lambda obj: (
            0 if _login_body_must_save(obj) else 1,
            (getattr(obj, "key", None) or ""),
        )
    )
    return bodies


def _login_storage_key_set(game):
    """Lowercased persist keys owned by account-linked bodies (one collect)."""
    cached = getattr(game, "_persist_login_storage_keys", None)
    if isinstance(cached, set):
        return cached
    from engine.char_index import iter_characters

    keys = set()
    for other in iter_characters(game):
        if not _login_body_must_save(other):
            continue
        name = (getattr(other, "key", None) or "").lower()
        if name:
            keys.add(name)
    if game is not None:
        game._persist_login_storage_keys = keys
    return keys


def _login_body_owns_storage_key(game, save_name, self_obj=None):
    """True when an account-linked body already holds this persist key."""
    want = (save_name or "").lower()
    if not want:
        return False
    # NPC skip-save used to walk the whole roster per body (O(n^2) when
    # CNUM heal dirtied hundreds of extras in one collect).
    return want in _login_storage_key_set(game)


def _bonded_companion_must_save(obj):
    """True when a living bonded pet or horse must keep a SQLite row.

    Shop beasts and mounts are ``is_npc`` + ``peaceful``. Skip-save plus
    ``DELETE FROM characters`` used to wipe them when a homestead rebuild
    dropped their room (bug reports 1490 / 1494).
    """
    if obj is None:
        return False
    if int(getattr(obj, "hp", 0) or 0) <= 0:
        return False
    return bool(getattr(obj, "is_pet", False) or getattr(obj, "is_horse", False))


def _persistable_reseat_body(obj):
    """True when an unroomed body should be parked before skip-save.

    Account-linked Echoes, immersion cast, and essential fixtures must keep
    a SQLite row across cold boot. Hostile NPCs, guests, and parked
    ``gmspirit:`` / ``husk:`` keys stay out of this path. A play body that
    still wears ``gm_mode`` must reseat too -- otherwise skip-save plus
    ``DELETE FROM characters`` wipes them on the next full snapshot
    (bug report 1437). Living bonded pets and horses reseat the same way
    (bug reports 1490 / 1494).
    """
    if obj is None:
        return False
    if getattr(obj, "is_guest", False):
        return False
    if getattr(obj, "gm_mode", False) and not _login_body_must_save(obj):
        return False
    key_low = (getattr(obj, "key", None) or "").lower()
    if key_low.startswith("gmspirit:") or key_low.startswith("husk:"):
        return False
    if _login_body_must_save(obj):
        return True
    if getattr(obj, "immersion", False) or getattr(obj, "essential", False):
        return True
    if _bonded_companion_must_save(obj):
        return True
    return False


def _reseat_account_linked_if_unroomed(obj, game):
    """Put a persistable body back on a live map room before snapshot.

    Cold-boot hub heals can leave an Echo or catalog Cast with
    ``location is None`` (or on a Room object that was replaced in
    ``game.rooms``). The next full snapshot then ``DELETE FROM characters``.
    Home / start is enough to survive until deferred unstick runs.

    Virtual overland cells live in ``game.overland_rooms``, not
    ``game.rooms`` (bug report 907). A live client already sitting on a
    valid cell (including overland) returns early above and is never
    moved. Unroomed / stale-room persistable bodies -- even with a live
    Session -- must reseat; skip-save plus DELETE FROM characters is how
    a logged-in PC can vanish (bug report 1437).
    """
    from engine.systems.overland import resolve_virtual_overland_room_key
    from engine.room_vnum import lookup_room

    loc = getattr(obj, "location", None)
    rooms = getattr(game, "rooms", None) or {}
    dest = None
    if loc is not None:
        key = getattr(loc, "key", None)
        live = lookup_room(game, key) if key else None
        if live is loc and not getattr(loc, "map_missing_stub", False):
            return loc
        # Depth-mine rooms live in ``game.mine_rooms``, not ``game.rooms``.
        if getattr(loc, "virtual_mine", False) and not getattr(
            loc, "map_missing_stub", False,
        ):
            return loc
        if dest is None:
            frontier = _resolve_frontierland_saved_room_key(
                game, key, character=obj,
            )
            if frontier is loc:
                return loc
            virt = resolve_virtual_overland_room_key(game, key)
            if virt is loc:
                return loc
            dest = live if live is not None and not getattr(
                live, "map_missing_stub", False,
            ) else frontier if frontier is not None else virt
    if not _persistable_reseat_body(obj):
        return loc
    if dest is None:
        dest = _rematerialize_mine_or_mouth(
            game,
            getattr(loc, "key", None) if loc is not None else None,
            obj,
        )
    # Sealed-age / mine bodies never fall back to Prime home.
    loc_key = getattr(loc, "key", None) if loc is not None else None
    stay_in_century = _saved_key_is_frontierland(loc_key, obj)
    stay_in_mine = _looks_like_mine_saved_key(loc_key)
    if dest is None and not stay_in_century and not stay_in_mine:
        dest = lookup_room(game, getattr(obj, "home_room_key", None))
    if dest is None and not stay_in_century and not stay_in_mine:
        dest = getattr(game, "start_room", None)
    if dest is None or dest is loc:
        return loc
    if stay_in_century and dest is not None:
        from engine.systems.overland import FRONTIERLAND_MAP_ID, FRONTIERLAND_PLANE

        dest_plane = str(getattr(dest, "plane", "") or "").strip().lower()
        dest_map = str(getattr(dest, "map_id", "") or "").strip().lower()
        if dest_plane != FRONTIERLAND_PLANE and dest_map != FRONTIERLAND_MAP_ID:
            return loc
    mover = getattr(obj, "move_to", None)
    if callable(mover) and mover(dest):
        return dest
    return loc


def _keep_skipped_login_body_alive(
    obj, name, alive_names, new_char_hashes, prev_char_hashes,
):
    """Keep a skip-saved login PC in ``alive_names`` so SQLite is not wiped.

    Incremental ``removed_chars`` is ``prev - alive``. A body that is still
    in ``game.characters`` but skipped (no room, pit tag, …) used to look
    despawned and lose its row (bug report 1437).
    """
    if not name:
        return
    if not (
        _login_body_must_save(obj) or _bonded_companion_must_save(obj)
    ):
        return
    alive_names.add(name)
    prev_hash = (prev_char_hashes or {}).get(name)
    if prev_hash is not None:
        new_char_hashes[name] = prev_hash
    print(
        f"[persistence] keep sqlite row for skipped login body {name!r}",
        flush=True,
    )


def _should_skip_character_save(obj, game, seen_names):
    """Return True when this live Character must not be written to SQLite."""
    room = _reseat_account_linked_if_unroomed(obj, game)
    # Login PCs still need a cell so the INSERT has a room_key. Reseat
    # should have parked them; last-ditch start_room beats a skipped save.
    if room is None and (
        _login_body_must_save(obj) or _bonded_companion_must_save(obj)
    ):
        room = getattr(game, "start_room", None)
        if room is not None:
            mover = getattr(obj, "move_to", None)
            if callable(mover):
                mover(room)
            if getattr(obj, "location", None) is None:
                obj.location = room
    if room is None:
        return True
    # Login PCs always snapshot (except a duplicate key). Force-full
    # ``DELETE FROM characters`` then INSERT only the collected rows --
    # skip-saved login names are recorded on the snapshot so apply keeps
    # those SQLite rows instead of wiping them (bug reports 1437 / 1509).
    # Pit tags / gm_mode on the Echo must not skip.
    if _login_body_must_save(obj):
        save_name = getattr(obj, "key", None) or ""
        if save_name in seen_names:
            print(
                f"[persistence] skip duplicate character key "
                f"{save_name!r} in {getattr(room, 'key', '?')}",
                flush=True,
            )
            return True
        return False
    save_name = getattr(obj, "key", None) or ""
    # An NPC must never occupy a login body's persist PRIMARY KEY.
    if _login_body_owns_storage_key(game, save_name, self_obj=obj):
        print(
            f"[persistence] skip NPC duplicate of login key "
            f"{save_name!r} in {getattr(room, 'key', '?')}",
            flush=True,
        )
        return True
    if getattr(obj, "tutorial_mentor_for", None):
        return True
    # Pit merchants / tagged trash are ephemeral. Login bodies already
    # returned above, so these flags only skip NPC kit.
    if getattr(obj, "pit_merchant", False):
        return True
    if getattr(obj, "pit_run_tag", None):
        return True
    if getattr(obj, "is_guest", False):
        return True
    if getattr(obj, "transient_soul", False):
        return True
    # Sleep-astral copies are ephemeral scenery (respawned from the
    # Earth's sleep_dream_tour stamp). Never a second pfile.
    if getattr(obj, "is_sleep_dream_clone", False):
        return True
    key_low = (getattr(obj, "key", None) or "").lower()
    if getattr(obj, "character_kind", None) == "riftcrash_avatar":
        return True
    if key_low.startswith("riftcrash:"):
        return True
    if key_low.startswith("dreamself:"):
        return True
    if getattr(obj, "riftcrash_trash", False):
        return True
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
            except Exception as exc:
                from engine import log_util

                log_util.ops_once_per_tick(
                    None,
                    f"persistence:gm_spirit:{key_low}",
                    "persistence",
                    f"gm-spirit stamp failed key={key_low}",
                    exc=exc,
                )
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
    elif getattr(obj, "_town_moral_smoke", False) or getattr(
        obj, "_town_moral_possessed", False,
    ):
        # Lockdown occupation is tagged hostile (peaceful=False) so the
        # street-hostile skip below would drop it on copyover. Keep it.
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


def _begin_persist_cnum_set(game):
    """Return ``(taken, extra_dirty_keys)`` after at-most-once uniqueness heal.

    Collision repair must run before INSERT. Incremental autosave used to
    re-heal the whole roster every collect and then merge ``extra_keys``
    onto this pass *after* the dirty cap -- that serialized hundreds of
    bodies while ``chars=`` still showed 2-3 writes (skip-save). Heal once
    per boot (and again on force-full verify); ``_ensure_save_cnum`` still
    stamps newcomers on the bodies that actually write.
    """
    from engine.char_cnum import collect_taken_cnums
    from engine.char_identity import heal_unique_character_cnums
    from engine.char_index import iter_characters

    extra_keys = []
    force_heal = bool(getattr(game, "_persist_force_full", False))
    already = bool(getattr(game, "_persist_cnums_healed", False))
    if not already or force_heal:
        result = heal_unique_character_cnums(game)
        for char in result.get("changed") or ():
            mark_character_dirty(game, char, force=True)
            key = getattr(char, "key", None)
            if key:
                extra_keys.append(key)
        if game is not None:
            game._persist_cnums_healed = True
            game._persist_taken_cnums = None
    cached = getattr(game, "_persist_taken_cnums", None)
    if already and not extra_keys and isinstance(cached, set):
        return cached, extra_keys
    taken = collect_taken_cnums(iter_characters(game))
    if game is not None:
        game._persist_taken_cnums = taken
    return taken, extra_keys


def _persist_merge_cnum_extra_dirty(
    dirty_chars, extra_keys, deferred_chars, *, force_full, game,
):
    """Fold CNUM-heal extras into this pass without bypassing the dirty cap."""
    if not extra_keys:
        return set(dirty_chars), set(deferred_chars)
    extra_list = [k for k in extra_keys if k]
    dirty_chars = set(dirty_chars)
    deferred_chars = set(deferred_chars)
    if force_full:
        dirty_chars.update(extra_list)
        return dirty_chars, deferred_chars
    cap = persist_dirty_char_cap(game)
    keep = list(dirty_chars)
    seen = set(keep)
    for key in extra_list:
        if key in seen:
            continue
        if len(keep) < cap:
            keep.append(key)
            seen.add(key)
        else:
            deferred_chars.add(key)
    return set(keep), deferred_chars


def _ensure_save_cnum(obj, game, seen_cnums):
    """Validated CNUM on ``obj`` for the SQLite column (mutates if missing)."""
    from engine.char_cnum import allocate_cnum, validate_cnum
    from engine.char_identity import character_given_name

    raw = getattr(obj, "cnum", None)
    current = None
    if isinstance(raw, str) and raw.strip():
        try:
            current = validate_cnum(raw)
        except ValueError:
            current = None
    if current:
        obj.cnum = current
        if seen_cnums is not None:
            seen_cnums.add(current)
        cached = getattr(game, "_persist_taken_cnums", None)
        if isinstance(cached, set):
            cached.add(current)
        return current
    taken = seen_cnums if seen_cnums is not None else set()
    new = allocate_cnum(character_given_name(obj), taken=taken)
    obj.cnum = new
    taken.add(new)
    cached = getattr(game, "_persist_taken_cnums", None)
    if isinstance(cached, set):
        cached.add(new)
    return new


def _character_save_rows(game, obj, seen_names, seen_cnums=None):
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
            except Exception as exc:
                from engine import log_util

                log_util.ops(
                    "persistence",
                    f"gm-spirit stamp failed key={save_name}",
                    exc=exc,
                )
                spirit = None
        spirit_room = getattr(spirit, "location", None) if spirit else None
        if spirit_room is not None and getattr(spirit_room, "key", None):
            obj.gm_spirit_room_key = spirit_room.key
    # Ordinary save_world used to write "{}" when the blob codec was
    # unregistered (partial reload) and wipe every character (HB-02).
    # Copyover already aborts; match that bar here.
    from engine import hooks as hooks_mod
    if not hooks_mod.blob_codec_registered():
        raise RuntimeError(
            "blob codec not registered; refusing character save "
            "(would write empty blobs)"
        )
    hooks_mod.sync_equipped_flags_from_equipment(obj)
    cnum = _ensure_save_cnum(obj, game, seen_cnums)
    t_blob = time.perf_counter()
    blob_dict = character_to_blob(obj, include_heavy=False)
    blob_build_ms = (time.perf_counter() - t_blob) * 1000.0
    heavy_sidecar_rows = []
    try:
        prev_heavy = getattr(game, "_persist_heavy_shard_hashes", None) or {}
        name_key = getattr(obj, "key", None) or ""
        heavy_sidecar_rows, _shard_hashes = hooks_mod.collect_heavy_sidecar_rows(
            game,
            obj,
            cnum,
            prev_shard_hashes=prev_heavy.get(name_key),
        )
        if _shard_hashes and name_key:
            merged_heavy = dict(prev_heavy)
            merged_heavy[name_key] = _shard_hashes
            game._persist_heavy_shard_hashes = merged_heavy
    except Exception as exc:
        from engine import log_util

        log_util.ops(
            "persistence",
            f"heavy sidecar collect skipped key={getattr(obj, 'key', '?')}",
            exc=exc,
        )
    t_dumps = time.perf_counter()
    blob = json.dumps(blob_dict)
    dumps_ms = (time.perf_counter() - t_dumps) * 1000.0
    save_room = room
    if getattr(obj, "djinn_captive", False):
        real = getattr(obj, "djinn_real_room", None)
        real_key = getattr(obj, "djinn_real_room_key", None)
        if real is not None:
            save_room = real
        elif real_key:
            from engine.room_vnum import lookup_room

            found = lookup_room(game, real_key)
            if found is not None:
                save_room = found
    elif getattr(room, "djinn_instance_id", None):
        ret = getattr(obj, "djinn_mirage_return_room", None)
        if ret is not None:
            save_room = ret
    char_row = (
        obj.key, obj.description, _persist_room_identity(save_room), blob, cnum,
    )
    t_items = time.perf_counter()
    item_rows = []
    for item in obj.inventory:
        item_rows.append((
            _persist_item_field(item, "key"),
            _persist_item_field(
                item, "description", fallback=_persist_item_field(item, "key"),
            ),
            "character", obj.key,
            _item_container_blob(item),
            cnum,
        ))
    for item in list(getattr(obj, "gear_bag", None) or []):
        item_rows.append((
            _persist_item_field(item, "key"),
            _persist_item_field(
                item, "description", fallback=_persist_item_field(item, "key"),
            ),
            "gear", obj.key,
            _item_container_blob(item),
            cnum,
        ))
    items_ms = (time.perf_counter() - t_items) * 1000.0
    blob_bytes = len(blob.encode("utf-8")) if isinstance(blob, str) else len(blob)
    build_stats = {
        "blob_ms": round(blob_build_ms, 2),
        "dumps_ms": round(dumps_ms, 2),
        "items_ms": round(items_ms, 2),
        "blob_bytes": blob_bytes,
        "total_ms": round(blob_build_ms + dumps_ms + items_ms, 2),
        "heavy_sidecar_rows": heavy_sidecar_rows,
    }
    if blob_build_ms + dumps_ms >= _PERSIST_SLOW_CHAR_FRAGMENT_PROFILE_MS:
        build_stats["blob_top_keys"] = _profile_blob_top_keys(blob_dict)
    return char_row, item_rows, build_stats


def _floor_room_persist_key(room):
    """Identity string used for floor-item holder_key / dirty set."""
    key = _persist_room_identity(room)
    return key or None


def _floor_item_save_tuple(obj, room_key):
    """One loose floor Item as an items INSERT tuple (holder_cnum NULL)."""
    return (
        obj.key, obj.description, "room", room_key,
        _item_container_blob(obj), None,
    )


def _is_heal_only_glassbench_item(obj):
    """True for player-shop torch-bench furniture rebuilt on shop heal.

    Bug report 1717 planted these as floor Items. ``glassbench_prop`` was
    not in the container blob, so ``load_world`` restacked a copy every
    rewrite (bug report 1725). Same skip as civic street fixtures:
    ``load_player_shops`` / ``ensure_glassbench_look`` rebuilds one.
    Identity is the flag OR the 1717 plant name/aliases (flag-only
    misses legacy rows).
    """
    if obj is None or not isinstance(obj, Item):
        return False
    if getattr(obj, "glassbench_prop", False):
        return True
    key = str(getattr(obj, "key", "") or "").strip().lower()
    if key == "a torch bench":
        return True
    if not getattr(obj, "furniture", False):
        return False
    aliases = getattr(obj, "aliases", None) or []
    lowered = {str(a).strip().lower() for a in aliases if a}
    return "torch bench" in lowered or "glass bench" in lowered


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
    # Street shop curb fixtures are rebuilt from ``player_shops`` each boot
    # (``load_player_shops``). Persisting them stacks duplicates on every
    # restart because that heal runs before ``load_world`` reloads floor rows.
    from engine.systems import civic_fixture as civic_fixture_mod

    if civic_fixture_mod.is_fixture_item(obj):
        return False
    # Glass-shop torch benches (supers/glass_craft.ensure_glassbench_look)
    # are the same class of heal-only fixture inside the pocket hub.
    if _is_heal_only_glassbench_item(obj):
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
                key = _floor_room_persist_key(room)
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
    key = _floor_room_persist_key(room)
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
    key = _floor_room_persist_key(room)
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
            from engine.room_vnum import lookup_room

            room = lookup_room(game, key)
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


def _floor_item_room_key_set(game):
    """Indexed floor-loot room keys, or None when collect must fall back."""
    keys = getattr(game, "_floor_item_room_keys", None)
    if keys is None:
        return None
    return set(keys)


def _resolve_floor_item_room(game, room_key):
    """Lookup one indexed floor room (main graph + demesne micro)."""
    if not room_key:
        return None
    from engine.room_vnum import lookup_room

    room = lookup_room(game, room_key)
    if room is not None:
        return room
    from engine import hooks
    return hooks.demesne_lookup_room_for_persist(game, room_key)


def _floor_collect_visit_keys(game, prev_floor_hashes, force_full, dirty_rooms):
    """Room keys that still need a live snapshot this collect pass.

    Lag U2: when the floor index exists and every indexed room already has
    a fingerprint and none are dirty, return an empty set so callers can
    copy ``prev_floor_hashes`` without walking thousands of rooms.

    Missing fingerprints (new homestead rooms, post-copyover index growth)
    used to join the visit set *uncapped*. Live 2026-09-13 billed 7-9s
    collect with only 2-3 dirty characters -- that walk, not the blobs.
    Cap missing the same way dirty rooms already cap.
    """
    keys = _floor_item_room_key_set(game)
    if keys is None:
        return None
    if force_full:
        return keys
    prev = prev_floor_hashes or {}
    dirty = set(dirty_rooms or ())
    missing = keys - set(prev.keys())
    if not dirty and not missing:
        return set()
    dirty_hit = sorted(dirty & keys)
    miss_sorted = sorted(missing - set(dirty_hit))
    cap = persist_dirty_room_cap(game)
    take = dirty_hit[:cap]
    if len(take) < cap:
        take.extend(miss_sorted[: cap - len(take)])
    return set(take)


def _floor_collect_preamble_hashes(keys, prev_floor_hashes, dirty_rooms):
    """Copy clean fingerprints for rooms we are not visiting this pass."""
    prev = prev_floor_hashes or {}
    dirty = set(dirty_rooms or ())
    out = {}
    for key in keys or ():
        if key in dirty:
            continue
        digest = prev.get(key)
        if digest is not None:
            out[key] = digest
    return out


def _room_floor_item_rows(game):
    """Loose floor Items as INSERT tuples (indexed room walk when possible)."""
    rows = []
    for room in _iter_floor_item_rooms(game):
        for obj in room.contents:
            if not _persistable_floor_item(obj):
                continue
            rows.append(_floor_item_save_tuple(obj, _floor_room_persist_key(room)))
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


def _mark_online_session_characters_dirty(game):
    """Queue every connected PC before a terminating world save.

    Incremental autosave can skip bodies whose dirty bit was cleared after
    an ``unchanged_dirty_chars`` hash match while RAM still diverged from
    SQLite. Copyover must flush live session state, not the last quiet
    autosave slice (bug report 1735).
    """
    if game is None:
        return 0
    marked = 0
    for sess in list(getattr(game, "sessions", None) or ()):
        if getattr(sess, "silent", False):
            continue
        char = getattr(sess, "character", None)
        if char is None:
            continue
        mark_character_dirty(game, char, force=True)
        marked += 1
    return marked


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


def persist_save_character(
    conn, game, character, *, player_checkpoint=False, flush_built_sites=True,
):
    """Write one live character (+ inventory/gear) immediately.

    Used by the player ``save`` verb: flushes the body now and clears it
    from the dirty queue so the next incremental autosave can skip them
    until they mutate again.

    When ``player_checkpoint`` is True (player ``save`` only), also upserts
    this owner's built sites and archives a JSON recovery copy under
    ``backups/player-checkpoints/`` (including a ``built_sites`` blob).

    Copyover's online-tank archive sets ``flush_built_sites=False`` -- the
    terminating snapshot already force-writes homestead/shop/realm tables
    right after, and N × site upserts on the asyncio thread is the Veil freeze.
    """
    if conn is None or game is None or character is None:
        return False, "Nothing to save."
    seen_names = set()
    payload = _character_save_rows(
        game, character, seen_names, _begin_persist_cnum_set(game)[0],
    )
    if payload is None:
        return False, "Your character cannot be saved right now."
    char_row, item_rows, build_stats = payload
    name = char_row[0]
    digest = _char_snapshot_hash(char_row, item_rows)
    heavy_sidecar_rows = (build_stats or {}).get("heavy_sidecar_rows") or []
    meta = {
        "force_full": False,
        "changed_chars": [name],
        "removed_chars": [],
        "changed_floor_rooms": [],
        "heavy_sidecar_rows": heavy_sidecar_rows,
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
    site_kinds = []
    built_sites = {}
    if player_checkpoint:
        if flush_built_sites:
            try:
                from engine import hooks as hooks_mod

                site_kinds = list(
                    hooks_mod.save_player_built_sites(game, conn, character) or []
                )
                built_sites = hooks_mod.collect_player_built_checkpoint(
                    game, character,
                ) or {}
            except Exception as exc:
                print(
                    f"[player_checkpoint] built-site flush skipped for "
                    f"{name!r}: {exc!r}",
                    flush=True,
                )
        try:
            from engine import player_save_backup as psb

            root = getattr(game, "report_dir", None) or psb._repo_root()
            psb.archive_player_checkpoint(
                char_row, item_rows, root=root, built_sites=built_sites,
            )
        except Exception as exc:
            print(
                f"[player_checkpoint] archive skipped for {name!r}: {exc!r}",
                flush=True,
            )
    msg = "Your progress is saved."
    if site_kinds:
        pretty = ", ".join(site_kinds)
        msg = f"Your progress is saved — including your {pretty}."
    return True, msg


def mark_floor_room_dirty(game, room):
    """Queue loose floor items in ``room`` for the next incremental save."""
    if game is None or room is None:
        return
    key = _persist_room_identity(room)
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
    game._persist_force_full_chars_only = False


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
    written_chars.update(meta.get("unchanged_dirty_chars") or ())
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
    if meta.get("force_full_complete"):
        _persist_finish_force_full_if_due(game, meta)
    elif meta.get("force_full") and not meta.get("force_full_chunked_apply"):
        game._persist_force_full = False
        game._persist_force_full_chars_only = False


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

    seen_names = set()
    char_hashes = {}
    seen_cnums, _extra = _begin_persist_cnum_set(game)
    for obj in _iter_persist_characters(game):
        payload = _character_save_rows(game, obj, seen_names, seen_cnums)
        if payload is None:
            continue
        char_row, owned_items, _build_stats = payload
        char_hashes[char_row[0]] = _char_snapshot_hash(char_row, owned_items)

    floor_hashes = {}
    for room in _iter_floor_item_rooms(game):
        room_key = _floor_room_persist_key(room)
        if not room_key:
            continue
        room_rows = []
        for obj in room.contents:
            if not _persistable_floor_item(obj):
                continue
            room_rows.append(_floor_item_save_tuple(obj, room_key))
        floor_hashes[room_key] = _floor_room_snapshot_hash(room_rows)

    game._persist_char_snapshot_hashes = char_hashes
    game._persist_floor_room_hashes = floor_hashes
    game._persist_warm = True
    game._persist_force_full = False
    game._persist_force_full_chars_only = False


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
    char_rows = []
    item_rows = []
    alive_names = set()
    changed_chars = []
    new_char_hashes = {}
    skipped_login_names = []
    heavy_sidecar_rows = []
    seen_cnums, extra_keys = _begin_persist_cnum_set(game)
    dirty_chars, _extra_deferred = _persist_merge_cnum_extra_dirty(
        dirty_chars, extra_keys, set(), force_full=force_full, game=game,
    )
    _login_storage_key_set(game)
    for obj in _iter_persist_characters(game):
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
        payload = _character_save_rows(game, obj, seen_names, seen_cnums)
        if payload is None:
            _keep_skipped_login_body_alive(
                obj, name, alive_names, new_char_hashes, prev_char_hashes,
            )
            if _login_body_must_save(obj) and name:
                skipped_login_names.append(name)
            continue
        char_row, owned_items, build_stats = payload
        name = char_row[0]
        alive_names.add(name)
        digest = _char_snapshot_hash(char_row, owned_items)
        heavy_rows = (build_stats or {}).get("heavy_sidecar_rows") or []
        if heavy_rows:
            heavy_sidecar_rows.extend(heavy_rows)
        new_char_hashes[name] = digest
        char_rows.append(char_row)
        item_rows.extend(owned_items)
        if not force_full:
            changed_chars.append(name)
    written_names = {row[0] for row in char_rows}
    skipped_login_names = [
        n for n in skipped_login_names if n not in written_names
    ]
    removed_chars = []
    if not force_full and prev_char_hashes:
        removed_chars = sorted(set(prev_char_hashes.keys()) - alive_names)
    if game is not None:
        game._persist_login_storage_keys = None
    return (
        char_rows, item_rows, changed_chars, removed_chars, new_char_hashes,
        skipped_login_names, heavy_sidecar_rows,
    )


def _collect_floor_save_pass(game, prev_floor_hashes, force_full, dirty_rooms):
    """Build floor-item rows for rooms whose loose loot changed."""
    item_rows = []
    changed_floor_rooms = []
    visit_keys = _floor_collect_visit_keys(
        game, prev_floor_hashes, force_full, dirty_rooms,
    )
    if visit_keys is not None:
        indexed = _floor_item_room_key_set(game) or set()
        new_floor_hashes = _floor_collect_preamble_hashes(
            indexed, prev_floor_hashes, dirty_rooms,
        )
        rooms_hash_skip = len(new_floor_hashes)
        if not visit_keys:
            return item_rows, changed_floor_rooms, new_floor_hashes
        room_iter = (
            _resolve_floor_item_room(game, key)
            for key in visit_keys
        )
    else:
        new_floor_hashes = {}
        rooms_hash_skip = 0
        room_iter = _iter_floor_item_rooms(game)
    for room in room_iter:
        if room is None:
            continue
        room_key = _floor_room_persist_key(room)
        if not room_key:
            continue
        if (
            visit_keys is None
            and not force_full
            and room_key not in dirty_rooms
        ):
            prev_digest = (prev_floor_hashes or {}).get(room_key)
            if prev_digest is not None:
                new_floor_hashes[room_key] = prev_digest
                rooms_hash_skip += 1
                continue
        room_rows = []
        for obj in room.contents:
            if not _persistable_floor_item(obj):
                continue
            room_rows.append(_floor_item_save_tuple(obj, room_key))
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
    wall_start=None, wall_budget_ms=0,
):
    """Cooperative floor-item snapshot -- yield while scanning many rooms."""
    import asyncio
    import time as _time

    if yield_every is None:
        yield_every = persist_save_yield_every()
    item_rows = []
    changed_floor_rooms = []
    visit_keys = _floor_collect_visit_keys(
        game, prev_floor_hashes, force_full, dirty_rooms,
    )
    indexed = _floor_item_room_key_set(game)
    if visit_keys is not None and indexed is not None:
        new_floor_hashes = _floor_collect_preamble_hashes(
            indexed, prev_floor_hashes, dirty_rooms,
        )
        rooms_hash_skip = len(new_floor_hashes)
        floor_collect_fast_skip = 1 if not visit_keys else 0
        if not visit_keys:
            floor_stats = {
                "floor_rooms_scanned": 0,
                "floor_rooms_hash_skip": rooms_hash_skip,
                "floor_collect_fast_skip": floor_collect_fast_skip,
                "floor_yield_count": 0,
                "floor_build_total_ms": 0.0,
                "floor_build_count": 0,
                "floor_build_avg_ms": 0.0,
                "slow_floor_rooms": [],
            }
            return item_rows, changed_floor_rooms, new_floor_hashes, floor_stats
        visit_list = list(visit_keys)
        room_iter = (
            _resolve_floor_item_room(game, key)
            for key in visit_list
        )
    else:
        new_floor_hashes = {}
        rooms_hash_skip = 0
        floor_collect_fast_skip = 0
        visit_list = None
        room_iter = _iter_floor_item_rooms(game)
    n = 0
    rooms_scanned = 0
    yield_count = 0
    floor_build_total_ms = 0.0
    floor_build_count = 0
    slow_rooms = []
    floor_deferred = []
    for room in room_iter:
        if (
            wall_start is not None
            and _persist_save_over_wall_budget(wall_start, wall_budget_ms)
        ):
            if visit_list is not None:
                seen = set(new_floor_hashes) | set(changed_floor_rooms)
                floor_deferred = [k for k in visit_list if k not in seen]
            break
        if room is None:
            continue
        room_key = _floor_room_persist_key(room)
        if not room_key:
            continue
        rooms_scanned += 1
        if (
            visit_keys is None
            and not force_full
            and room_key not in dirty_rooms
        ):
            prev_digest = (prev_floor_hashes or {}).get(room_key)
            if prev_digest is not None:
                new_floor_hashes[room_key] = prev_digest
                rooms_hash_skip += 1
                n += 1
                if yield_every and n % yield_every == 0:
                    await asyncio.sleep(0)
                    yield_count += 1
                continue
        t_room = _time.perf_counter()
        room_rows = []
        for obj in room.contents:
            if not _persistable_floor_item(obj):
                continue
            room_rows.append(_floor_item_save_tuple(obj, room_key))
        digest = _floor_room_snapshot_hash(room_rows)
        new_floor_hashes[room_key] = digest
        room_ms = (_time.perf_counter() - t_room) * 1000.0
        floor_build_total_ms += room_ms
        floor_build_count += 1
        if room_ms >= _PERSIST_SLOW_FLOOR_LOG_MS:
            slow_rooms.append((room_key, round(room_ms, 2)))
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
            yield_count += 1
    slow_rooms.sort(key=lambda pair: pair[1], reverse=True)
    floor_stats = {
        "floor_rooms_scanned": rooms_scanned,
        "floor_rooms_hash_skip": rooms_hash_skip,
        "floor_collect_fast_skip": floor_collect_fast_skip,
        "floor_yield_count": yield_count,
        "floor_build_total_ms": round(floor_build_total_ms, 2),
        "floor_build_count": floor_build_count,
        "floor_build_avg_ms": (
            round(floor_build_total_ms / floor_build_count, 2)
            if floor_build_count
            else 0.0
        ),
        "slow_floor_rooms": slow_rooms[:_PERSIST_SLOW_FLOOR_LOG_CAP],
        "floor_deferred_rooms": floor_deferred,
    }
    if floor_deferred:
        _persist_restore_deferred_dirty(game, set(), set(floor_deferred))
    return item_rows, changed_floor_rooms, new_floor_hashes, floor_stats


def _persist_dirty_priority_key(game, char_key, by_key=None):
    """Sort key: online (0) < recently active (1) < everyone else (2).

    Lower sorts first so the dirty-char cap always drains live sessions
    before cold offline Echoes. Ties within a tier fall back to ``char_key``
    for the same determinism the old plain alphabetical sort gave tests.

    ``game.find_character`` is the player-facing fuzzy resolver (ordinals,
    faces, CNUM). Cap-sorting thousands of dirty keys through that was a
    multi-second collect all by itself. Prefer an exact-key map.
    """
    char = None
    if isinstance(by_key, dict):
        char = by_key.get(char_key)
    if char is None:
        from engine.char_index import find_character_by_key

        char = find_character_by_key(game, char_key)
    if char is None:
        return (2, char_key)
    session = getattr(char, "session", None)
    if session is not None and not getattr(session, "silent", False):
        return (0, char_key)
    last_active = float(getattr(char, "last_active_at", 0.0) or 0.0)
    if last_active and (time.time() - last_active) <= _PERSIST_DIRTY_RECENT_ACTIVE_S:
        return (1, char_key)
    return (2, char_key)


def _persist_cap_dirty_sets(game, force_full, *, force_full_floor=None):
    """Return (dirty_chars, dirty_rooms, deferred_chars, deferred_rooms).

    When not force_full, take only the first N dirty keys (priority-sorted
    for characters, alphabetical for rooms) and leave the rest queued for
    the next autosave. Full verify passes ignore the cap -- they rewrite
    everything (characters only when ``force_full_floor`` is false).
    """
    dirty_chars = set(getattr(game, "_persist_dirty_characters", None) or set())
    dirty_rooms = set(getattr(game, "_persist_dirty_floor_rooms", None) or set())
    if force_full_floor is None:
        force_full_floor = force_full
    if force_full:
        return dirty_chars, dirty_rooms, set(), set()
    char_cap = persist_dirty_char_cap(game)
    room_cap = persist_dirty_room_cap(game)
    from engine.char_index import iter_characters

    by_key = {}
    for obj in iter_characters(game):
        key = getattr(obj, "key", None)
        if key:
            by_key[key] = obj
    char_sorted = sorted(
        dirty_chars,
        key=lambda k: _persist_dirty_priority_key(game, k, by_key),
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
    force_full_chars, force_full_floor = _persist_force_full_collect_flags(game)
    dirty_chars, dirty_rooms, deferred_chars, deferred_rooms = (
        _persist_cap_dirty_sets(
            game, force_full_chars, force_full_floor=force_full_floor,
        )
    )

    seen_names = set()
    (
        char_rows, item_rows, changed_chars, removed_chars, new_char_hashes,
        skipped_login, heavy_sidecar_rows,
    ) = _collect_character_save_pass(
        game, seen_names, prev_char or {}, force_full_chars, dirty_chars,
    )
    floor_rows, changed_floor, new_floor_hashes = _collect_floor_save_pass(
        game, prev_floor or {}, force_full_floor, dirty_rooms,
    )
    item_rows.extend(floor_rows)
    meta = {
        "force_full": force_full_chars,
        "force_full_chars_only": force_full_chars and not force_full_floor,
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
        "n_dirty_chars_this_pass": len(dirty_chars) if not force_full_chars else -1,
        "n_dirty_rooms_this_pass": len(dirty_rooms) if not force_full_floor else -1,
        "skipped_login_names": list(skipped_login),
        "heavy_sidecar_rows": heavy_sidecar_rows,
    }
    return char_rows, item_rows, meta


async def _collect_world_save_snapshot_async(
    game, *, yield_every=None, wall_start=None, wall_budget_ms=0,
):
    """Cooperative snapshot build -- yields so player commands can run."""
    import asyncio

    if yield_every is None:
        yield_every = persist_save_yield_every()

    _persist_apply_scheduled_world_full(game)
    prev_char = getattr(game, "_persist_char_snapshot_hashes", None)
    prev_floor = getattr(game, "_persist_floor_room_hashes", None)
    force_full_chars, force_full_floor = _persist_force_full_collect_flags(game)
    dirty_chars, dirty_rooms, deferred_chars, deferred_rooms = (
        _persist_cap_dirty_sets(
            game, force_full_chars, force_full_floor=force_full_floor,
        )
    )
    chunked_chars_only = _persist_chars_only_chunked_verify(
        game, force_full_chars, force_full_floor,
    )
    verified_chars = (
        _persist_force_full_verified_chars(game) if chunked_chars_only else set()
    )

    # Yielding collect: walk characters in slices so a full-verify pass
    # does not pin the asyncio loop for the whole roster serialization.
    seen_names = set()
    char_rows = []
    item_rows = []
    alive_names = set()
    changed_chars = []
    new_char_hashes = {}
    skipped_login_names = []
    n = 0
    seen_cnums, extra_keys = _begin_persist_cnum_set(game)
    dirty_chars, deferred_chars = _persist_merge_cnum_extra_dirty(
        dirty_chars, extra_keys, deferred_chars,
        force_full=force_full_chars, game=game,
    )
    _login_storage_key_set(game)
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
    slow_char_detail = []
    char_build_total_ms = 0.0
    char_build_count = 0
    heavy_sidecar_rows = []
    unchanged_dirty_chars = []
    char_collect_wall_start = wall_start
    if wall_start is not None and wall_budget_ms > 0:
        char_collect_wall_start = time.perf_counter()
    for obj in _iter_persist_characters(game):
        if char_collect_wall_start is not None and _persist_save_over_wall_budget(
            char_collect_wall_start, wall_budget_ms,
        ):
            wall_budget_hit = True
            if force_full_chars and not chunked_chars_only:
                # Never apply a partial full-verify wipe.
                game._persist_force_full = True
                meta = {
                    "skipped_wall_budget": True,
                    "force_full": force_full_chars,
                    "force_full_chars_only": (
                        force_full_chars and not force_full_floor
                    ),
                    "deferred_chars": sorted(dirty_chars | deferred_chars),
                    "deferred_rooms": sorted(dirty_rooms | deferred_rooms),
                }
                return [], [], meta
            if not force_full_chars:
                deferred_chars.update(dirty_chars - set(changed_chars))
            break
        name = (getattr(obj, "key", None) or getattr(obj, "name", None) or "")
        name = str(name)
        if not name:
            continue
        if chunked_chars_only and name in verified_chars:
            # Already rewritten in this multi-pass verify cycle -- cheap
            # hash carry-forward, same shape as the warm dirty-skip path.
            alive_names.add(name)
            prev_hash = (prev_char or {}).get(name)
            if prev_hash is not None:
                new_char_hashes[name] = prev_hash
            continue
        if not force_full_chars and name not in dirty_chars:
            prev_hash = (prev_char or {}).get(name)
            if prev_hash is not None:
                alive_names.add(name)
                new_char_hashes[name] = prev_hash
                continue
            alive_names.add(name)
            continue
        t_char = time.perf_counter()
        payload = _character_save_rows(game, obj, seen_names, seen_cnums)
        outer_ms = (time.perf_counter() - t_char) * 1000.0
        if payload is None:
            _keep_skipped_login_body_alive(
                obj, name, alive_names, new_char_hashes, prev_char,
            )
            if _login_body_must_save(obj) and name:
                skipped_login_names.append(name)
            continue
        char_row, owned_items, build_stats = payload
        name = char_row[0]
        inner_ms = float(build_stats.get("total_ms") or 0.0)
        # Outer wall includes monkeypatches / skip-save work around the
        # inner blob+dumps+items split so a 9s collect still names the char.
        char_ms = max(outer_ms, inner_ms)
        char_build_total_ms += char_ms
        char_build_count += 1
        if char_ms >= _PERSIST_SLOW_CHAR_LOG_MS:
            slow_chars.append((name, round(char_ms, 2)))
            detail = {
                "name": name,
                "blob_ms": build_stats.get("blob_ms"),
                "dumps_ms": build_stats.get("dumps_ms"),
                "items_ms": build_stats.get("items_ms"),
                "blob_bytes": build_stats.get("blob_bytes"),
                "total_ms": round(char_ms, 2),
            }
            top_keys = build_stats.get("blob_top_keys")
            if top_keys:
                detail["blob_top_keys"] = top_keys
            slow_char_detail.append(detail)
            if char_ms >= _PERSIST_MEGACHAR_COLLECT_OPS_LOG_MS:
                from engine import lag_watch

                lag_watch.note_persist_collect_megachar(game, detail)
        alive_names.add(name)
        digest = _char_snapshot_hash(char_row, owned_items)
        heavy_rows = build_stats.get("heavy_sidecar_rows") or []
        if heavy_rows:
            heavy_sidecar_rows.extend(heavy_rows)
        prev_hash = (prev_char or {}).get(name)
        if (
            not force_full_chars
            and name in dirty_chars
            and prev_hash is not None
            and digest == prev_hash
            and not heavy_rows
        ):
            unchanged_dirty_chars.append(name)
            new_char_hashes[name] = digest
            continue
        new_char_hashes[name] = digest
        char_rows.append(char_row)
        item_rows.extend(owned_items)
        if not force_full_chars or chunked_chars_only:
            changed_chars.append(name)
        n += 1
        char_exceeded_yield = yield_ms > 0 and char_ms >= yield_ms
        due_count = yield_every and n % yield_every == 0
        due_time = (
            yield_ms > 0
            and (time.perf_counter() - last_yield) * 1000.0 >= yield_ms
        )
        if char_exceeded_yield or due_count or due_time:
            await asyncio.sleep(0)
            last_yield = time.perf_counter()
        if char_collect_wall_start is not None and _persist_save_over_wall_budget(
            char_collect_wall_start, wall_budget_ms,
        ):
            wall_budget_hit = True
            if force_full_chars and not chunked_chars_only:
                # Same bar as the pre-character gate: never apply a
                # partial full-verify wipe after a megachar blew the wall.
                game._persist_force_full = True
                meta = {
                    "skipped_wall_budget": True,
                    "wall_budget_hit": True,
                    "force_full": force_full_chars,
                    "force_full_chars_only": (
                        force_full_chars and not force_full_floor
                    ),
                    "deferred_chars": sorted(dirty_chars | deferred_chars),
                    "deferred_rooms": sorted(dirty_rooms | deferred_rooms),
                    "slow_chars": slow_chars[:_PERSIST_SLOW_CHAR_LOG_CAP],
                    "slow_char_detail": slow_char_detail[:_PERSIST_SLOW_CHAR_LOG_CAP],
                }
                return [], [], meta
            if not force_full_chars:
                deferred_chars.update(dirty_chars - set(changed_chars))
            break
    slow_chars.sort(key=lambda pair: pair[1], reverse=True)
    slow_char_detail.sort(
        key=lambda row: float(row.get("total_ms") or 0.0), reverse=True,
    )
    removed_chars = []
    if chunked_chars_only and changed_chars:
        verified_chars.update(changed_chars)
    force_full_complete = False
    if chunked_chars_only and force_full_chars:
        # Only trust removed-character detection once one uninterrupted
        # pass has walked every live body -- a wall-budget cutoff mid-pass
        # means alive_names is incomplete and would falsely "remove" the
        # tail of the roster it never reached.
        live_count = _persist_count_live_characters(game)
        force_full_complete = (
            not wall_budget_hit
            and live_count > 0
            and len(verified_chars) >= live_count
        )
        if force_full_complete and prev_char:
            removed_chars = sorted(set(prev_char.keys()) - alive_names)
    elif not force_full_chars and prev_char:
        removed_chars = sorted(set(prev_char.keys()) - alive_names)

    if (
        not wall_budget_hit
        and wall_start is not None
        and _persist_save_over_wall_budget(wall_start, wall_budget_ms)
    ):
        wall_budget_hit = True
        if not force_full_chars:
            deferred_chars.update(dirty_chars - set(changed_chars))

    floor_rows, changed_floor, new_floor_hashes, floor_stats = (
        await _collect_floor_save_pass_async(
            game, prev_floor or {}, force_full_floor, dirty_rooms,
            yield_every=yield_every,
            wall_start=wall_start,
            wall_budget_ms=wall_budget_ms,
        )
        if not wall_budget_hit
        else ([], [], {}, {})
    )
    item_rows.extend(floor_rows)
    await asyncio.sleep(0)
    written_names = {row[0] for row in char_rows}
    skipped_login_names = [
        n for n in skipped_login_names if n not in written_names
    ]
    meta = {
        "force_full": force_full_chars,
        "force_full_chars_only": force_full_chars and not force_full_floor,
        "force_full_chunked_apply": chunked_chars_only,
        "force_full_complete": force_full_complete,
        "changed_chars": changed_chars,
        "removed_chars": removed_chars,
        "unchanged_dirty_chars": unchanged_dirty_chars,
        "heavy_sidecar_rows": heavy_sidecar_rows,
        "changed_floor_rooms": changed_floor,
        "new_char_hashes": new_char_hashes,
        "new_floor_hashes": new_floor_hashes,
        "deferred_chars": deferred_chars,
        "deferred_rooms": deferred_rooms,
        "wall_budget_hit": wall_budget_hit,
        "n_dirty_chars_queued": len(
            getattr(game, "_persist_dirty_characters", None) or ()
        ),
        "n_dirty_rooms_queued": len(
            getattr(game, "_persist_dirty_floor_rooms", None) or ()
        ),
        "n_dirty_chars_this_pass": len(dirty_chars) if not force_full_chars else -1,
        "n_dirty_rooms_this_pass": len(dirty_rooms) if not force_full_floor else -1,
        "skipped_login_names": list(skipped_login_names),
        # Lag P12 breadcrumbs -- see _PERSIST_SLOW_CHAR_LOG_MS above.
        "slow_chars": slow_chars[:_PERSIST_SLOW_CHAR_LOG_CAP],
        "slow_char_detail": slow_char_detail[:_PERSIST_SLOW_CHAR_LOG_CAP],
        "char_build_total_ms": round(char_build_total_ms, 2),
        "char_build_count": char_build_count,
        "char_build_avg_ms": (
            round(char_build_total_ms / char_build_count, 2)
            if char_build_count
            else 0.0
        ),
        **(floor_stats or {}),
    }
    if game is not None:
        game._persist_login_storage_keys = None
    return char_rows, item_rows, meta


def _persist_cnum_for_storage_name(conn, name, char_row=None):
    """CNUM for this storage name: snapshot row, else the live SQLite row."""
    if char_row is not None and len(char_row) > 4:
        tagged = char_row[4]
        if isinstance(tagged, str) and tagged.strip():
            return tagged
    if not name or "cnum" not in _character_column_names(conn):
        return None
    row = conn.execute(
        "SELECT cnum FROM characters WHERE name=?", (name,),
    ).fetchone()
    if row and isinstance(row[0], str) and row[0].strip():
        return row[0]
    return None


def _delete_owned_items_for_character(conn, name, holder_cnum=None):
    """Wipe character/gear item rows by storage name and by CNUM.

    ``holder_key`` stays the storage name because that column also stores
    room keys, so incremental DELETE cannot switch to CNUM-only. Dual-write
    ``holder_cnum`` is the owner tag; DELETE must follow it too or a stale
    ``holder_key`` display name would leave orphaned kit rows.
    """
    conn.execute(
        "DELETE FROM items WHERE holder_key=? "
        "AND holder_type IN ('character', 'gear')",
        (name,),
    )
    if not holder_cnum or "holder_cnum" not in _item_column_names(conn):
        return
    conn.execute(
        "DELETE FROM items WHERE holder_cnum=? "
        "AND holder_type IN ('character', 'gear')",
        (holder_cnum,),
    )


def _skipped_login_keep_names(meta):
    """Deduped storage keys of login PCs collect could not snapshot."""
    names = []
    seen = set()
    for raw in (meta or {}).get("skipped_login_names") or ():
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _upsert_heavy_sidecar_rows(conn, rows):
    """INSERT OR REPLACE sidecar shards; skip empty collects (do not wipe)."""
    from engine import hooks

    for cnum, shard, payload in rows or ():
        if str(shard) == "property_stash":
            parsed_new = hooks.parse_heavy_shard_payload(payload)
            if parsed_new is not None and hooks.property_stash_payload_empty(parsed_new):
                try:
                    row = conn.execute(
                        "SELECT payload FROM character_heavy_blobs "
                        "WHERE cnum=? AND shard=?",
                        (str(cnum), str(shard)),
                    ).fetchone()
                except sqlite3.Error:
                    row = None
                if row:
                    parsed_old = hooks.parse_heavy_shard_payload(row[0])
                    if parsed_old is not None and not hooks.property_stash_payload_empty(
                        parsed_old,
                    ):
                        continue
        conn.execute(
            "INSERT OR REPLACE INTO character_heavy_blobs "
            "(cnum, shard, payload) VALUES (?,?,?)",
            (str(cnum), str(shard), payload),
        )


def _prune_orphan_heavy_blobs(conn):
    """Drop sidecar rows whose character cnum is gone."""
    conn.execute(
        "DELETE FROM character_heavy_blobs WHERE cnum NOT IN "
        "(SELECT cnum FROM characters WHERE cnum IS NOT NULL AND cnum != '')"
    )


def _force_full_delete_tables(conn, *, chars_only, keep_login_names):
    """Wipe characters/items for a force-full apply, keeping skip-saved PCs.

    Copyover ``DELETE FROM characters`` then INSERT only collected rows used
    to erase a skip-saved login body (bug report 1509 -- Ayla). Keep those
    names (and their held items) when collect could not snapshot them.
    """
    keep = [n for n in (keep_login_names or ()) if str(n).strip()]
    if keep:
        qmarks = ",".join("?" * len(keep))
        conn.execute(
            f"DELETE FROM characters WHERE name NOT IN ({qmarks})",
            keep,
        )
        item_owned = (
            f"DELETE FROM items WHERE holder_type IN ('character', 'gear') "
            f"AND holder_key NOT IN ({qmarks})"
        )
        if chars_only:
            conn.execute(item_owned, keep)
        else:
            conn.execute("DELETE FROM items WHERE holder_type = 'room'")
            conn.execute(item_owned, keep)
        _prune_orphan_heavy_blobs(conn)
        return
    conn.execute("DELETE FROM characters")
    if chars_only:
        conn.execute(
            "DELETE FROM items WHERE holder_type IN ('character', 'gear')",
        )
    else:
        conn.execute("DELETE FROM items")


def _refuse_empty_force_full_wipe(char_rows, meta):
    """True when a force-full apply would wipe the roster with nothing to INSERT."""
    if not (meta or {}).get("force_full"):
        return False
    if char_rows:
        return False
    print(
        "[persistence] refuse empty force_full wipe of characters table",
        flush=True,
    )
    return True


def _refuse_short_force_full_wipe(conn, char_rows, meta):
    """True when force_full would replace a large SQLite roster with a short snapshot.

    Empty-wipe refuse does not catch a non-empty partial snapshot (108 of
    1475). ``skipped_login_names`` count toward the snapshot so a keep-list
    copyover is not treated as a wipe.
    """
    if not (meta or {}).get("force_full"):
        return False
    if conn is None:
        return False
    snap_n = len(char_rows or ())
    keep_n = len(_skipped_login_keep_names(meta) or ())
    effective = snap_n + keep_n
    try:
        live_n = int(
            conn.execute("SELECT COUNT(*) FROM characters").fetchone()[0] or 0
        )
    except sqlite3.Error:
        return False
    if live_n < _FORCE_FULL_SHORT_MIN_LIVE:
        return False
    floor = max(1, int(live_n * _FORCE_FULL_SHORT_FRACTION))
    if effective >= floor:
        return False
    print(
        "[persistence] refuse short force_full wipe "
        f"(snapshot {effective} vs live {live_n}, floor {floor})",
        flush=True,
    )
    return True


def _refuse_unsafe_force_full_wipe(conn, char_rows, meta):
    """Empty or short force_full snapshot -- do not DELETE the live roster."""
    if _refuse_empty_force_full_wipe(char_rows, meta):
        return True
    return _refuse_short_force_full_wipe(conn, char_rows, meta)


def _heal_char_rows_cnum_clashes(conn, char_rows):
    """Remint NPC occupants so incoming PC rows can keep their archived CNUM."""
    if not char_rows or conn is None:
        return char_rows
    from engine.char_identity import (
        remint_cnum_clash_for_restore,
        rewrite_stats_blob_cnum,
    )

    healed = []
    for row in char_rows:
        name, desc, room, stats = row[0], row[1], row[2], row[3]
        cnum = row[4] if len(row) > 4 else None
        parsed = {}
        if isinstance(stats, str) and stats.strip():
            try:
                loaded = json.loads(stats)
            except (TypeError, ValueError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                parsed = loaded
        new_cnum, _notes = remint_cnum_clash_for_restore(
            conn,
            keep_name=name,
            keep_cnum=cnum,
            keep_stats=parsed,
        )
        if new_cnum:
            if new_cnum != cnum:
                stats = rewrite_stats_blob_cnum(stats, new_cnum)
            cnum = new_cnum
        healed.append((name, desc, room, stats, cnum))
    return healed


def _abort_copyover_incomplete_snapshot(game, char_rows, meta):
    """Raise when a terminating copyover save must not rewrite SQLite."""
    if not getattr(game, "_persist_copyover_save", False):
        return
    skipped = _skipped_login_keep_names(meta)
    if skipped:
        raise RuntimeError(
            "copyover save skipped login bodies %s -- aborting so SQLite "
            "is not wiped" % skipped
        )
    if meta.get("force_full") and not char_rows:
        raise RuntimeError(
            "copyover force_full collect produced 0 character rows -- "
            "aborting so SQLite is not wiped"
        )
    if meta.get("force_full"):
        try:
            roster_n = len(list(getattr(game, "characters", None) or []))
        except TypeError:
            roster_n = 0
        snap_n = len(char_rows or ()) + len(_skipped_login_keep_names(meta) or ())
        floor = max(1, int(roster_n * _FORCE_FULL_SHORT_FRACTION))
        if roster_n >= _FORCE_FULL_SHORT_MIN_LIVE and snap_n < floor:
            raise RuntimeError(
                "copyover force_full snapshot too short (%s of %s bodies) "
                "-- aborting so SQLite is not wiped" % (snap_n, roster_n)
            )
    if meta.get("skipped_wall_budget") or meta.get("wall_budget_hit"):
        raise RuntimeError(
            "copyover save hit persist wall budget -- aborting so SQLite "
            "is not partially rolled back"
        )


def _abort_copyover_skipped_save(game):
    """Raise when copyover save returned without applying a full snapshot."""
    if not getattr(game, "_persist_copyover_save", False):
        return
    stats = getattr(game, "_last_world_save_stats", None) or {}
    if stats.get("skipped"):
        detail = (
            stats.get("empty_force_full") and "empty_force_full"
            or stats.get("short_force_full") and "short_force_full"
            or "skipped"
        )
        raise RuntimeError(
            f"copyover save did not apply world snapshot ({detail}) -- "
            "aborting so players are not rolled back"
        )
    if stats.get("writer_pending"):
        raise RuntimeError(
            "copyover save left persist writer pending -- aborting"
        )


def _apply_world_save_snapshot(conn, char_rows, item_rows, meta=None):
    """Bulk-insert snapshot rows (full wipe or incremental dirty pass).

    Runs on the P30 background writer thread (default live path -- see
    ``RIFTFORGE_PERSIST_BACKGROUND_WRITER``) as well as the sync fallback.
    """
    _ensure_persist_schema(conn)
    meta = meta or {"force_full": True}
    force_full = bool(meta.get("force_full"))
    if force_full and meta.get("force_full_chunked_apply"):
        # CPU followon 2026-09: scheduled chars-only verify commits each
        # wall-budget chunk as per-name DELETE+INSERT instead of an atomic
        # wipe of the whole ``characters`` table -- the atomic wipe only
        # ever fires once collect finishes the *entire* live roster, which
        # a slow/large roster (~700+ characters) never did inside the 5s
        # budget. That discarded every chunk's work every autosave and
        # re-armed force_full forever, pegging a core at ~100% CPU.
        meta = dict(meta)
        meta["force_full"] = False
        force_full = False
    if force_full and _refuse_unsafe_force_full_wipe(conn, char_rows, meta):
        return
    last_err = None
    for attempt in range(2):
        try:
            with conn:
                if force_full:
                    _force_full_delete_tables(
                        conn,
                        chars_only=bool(meta.get("force_full_chars_only")),
                        keep_login_names=_skipped_login_keep_names(meta),
                    )
                else:
                    char_by_name = {row[0]: row for row in (char_rows or ())}
                    for name in meta.get("removed_chars", ()):
                        holder_cnum = _persist_cnum_for_storage_name(
                            conn, name,
                        )
                        conn.execute(
                            "DELETE FROM characters WHERE name=?", (name,),
                        )
                        _delete_owned_items_for_character(
                            conn, name, holder_cnum,
                        )
                    for name in meta.get("changed_chars", ()):
                        holder_cnum = _persist_cnum_for_storage_name(
                            conn, name, char_by_name.get(name),
                        )
                        conn.execute(
                            "DELETE FROM characters WHERE name=?", (name,),
                        )
                        _delete_owned_items_for_character(
                            conn, name, holder_cnum,
                        )
                    for room_key in meta.get("changed_floor_rooms", ()):
                        conn.execute(
                            "DELETE FROM items WHERE holder_key=? "
                            "AND holder_type='room'",
                            (room_key,),
                        )
                if char_rows:
                    char_rows = _heal_char_rows_cnum_clashes(conn, char_rows)
                    conn.executemany(_CHAR_INSERT_SQL, char_rows)
                if item_rows:
                    conn.executemany(_ITEM_INSERT_SQL, item_rows)
                _upsert_heavy_sidecar_rows(
                    conn, meta.get("heavy_sidecar_rows"),
                )
                if force_full:
                    _prune_orphan_heavy_blobs(conn)
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
    conn, char_rows, item_rows, *, yield_every, chars_only=False,
    keep_login_names=None, heavy_sidecar_rows=None,
):
    """Cooperative full-verify apply for ``save_world_async`` only.

    Fires on the *first* autosave after every game-only restart (hash
    cache is cold, so ``force_full`` is true) and every
    ``persist_full_every()`` passes after that. Scheduled full-verify
    passes set ``chars_only`` so floor loot rows are not wiped wholesale
    (lag U2). Yields every ``ye`` (``yield_every``, default 2) rows for
    responsiveness, but -- lag P11.3 continued -- used to also *commit*
    (fsync) every ``ye`` rows, which for a live-sized world (~400
    characters + ~5,000+ items) meant roughly (400+5000)/2 ~= 2,700
    individual commits in one pass, clocked at 62s on live right after
    the 2026-08-20 P11.3 deploy (this path fires on every restart, so
    it wasn't covered by the incremental-path fix above). Commits now
    batch at ``persist_apply_commit_batch()`` (default 25) independently
    of the yield cadence, matching the incremental path.
    """
    import asyncio

    _ensure_persist_schema(conn)
    ye = max(1, int(yield_every or 1))
    # Lag E1: never COMMIT a table wipe before INSERTs land. The old
    # ``with conn: DELETE`` block committed deletes, then yielded, so a
    # kill mid-pass could leave empty characters/items. One transaction
    # for the whole force-full apply; yields only serve the asyncio loop.
    conn.execute("BEGIN IMMEDIATE")
    try:
        _force_full_delete_tables(
            conn,
            chars_only=chars_only,
            keep_login_names=keep_login_names,
        )

        async def _apply_rows(rows, insert_sql):
            for i in range(0, len(rows), ye):
                batch = rows[i : i + ye]
                if not batch:
                    continue
                conn.executemany(insert_sql, batch)
                await asyncio.sleep(0)

        await _apply_rows(char_rows, _CHAR_INSERT_SQL)
        await _apply_rows(item_rows, _ITEM_INSERT_SQL)
        _upsert_heavy_sidecar_rows(conn, heavy_sidecar_rows)
        _prune_orphan_heavy_blobs(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


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

    _ensure_persist_schema(conn)
    meta = meta or {"force_full": True}
    force_full = bool(meta.get("force_full"))
    if force_full and meta.get("force_full_chunked_apply"):
        # CPU followon 2026-09: a scheduled chars-only verify applies each
        # chunk as per-body DELETE+INSERT (like the incremental path)
        # instead of one atomic wipe-then-reinsert. This lets a wall-budget
        # cutoff commit real progress -- the old atomic apply only ran once
        # the *entire* roster had been collected, so a slow/large roster
        # that never finished collecting inside the budget never applied
        # anything and the next autosave started the same walk over again.
        meta = dict(meta)
        meta["force_full"] = False
        force_full = False
    yield_every = (
        persist_save_yield_every() if yield_every is None else int(yield_every)
    )
    if force_full:
        if _refuse_unsafe_force_full_wipe(conn, char_rows, meta):
            return
        await _apply_force_full_snapshot_async(
            conn, char_rows, item_rows, yield_every=yield_every,
            chars_only=bool(meta.get("force_full_chars_only")),
            keep_login_names=_skipped_login_keep_names(meta),
            heavy_sidecar_rows=meta.get("heavy_sidecar_rows"),
        )
        return

    # Batch DELETE+INSERT per changed character / floor room.
    char_by_name = {row[0]: row for row in char_rows}
    # Items: character/gear vs room floor.
    items_by_holder = {}
    floor_by_room = {}
    for row in item_rows:
        # row = (key, desc, holder_type, holder_key, blob, holder_cnum)
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
        for name in meta.get("removed_chars", ()):
            holder_cnum = _persist_cnum_for_storage_name(conn, name)
            conn.execute("DELETE FROM characters WHERE name=?", (name,))
            _delete_owned_items_for_character(conn, name, holder_cnum)
            if holder_cnum:
                conn.execute(
                    "DELETE FROM character_heavy_blobs WHERE cnum=?",
                    (str(holder_cnum),),
                )
            pending_commit = True
            n += 1
            if n % commit_batch == 0:
                conn.commit()
                pending_commit = False
            if yield_every and n % yield_every == 0:
                await asyncio.sleep(0)

        for name in meta.get("changed_chars", ()):
            crow = char_by_name.get(name)
            holder_cnum = _persist_cnum_for_storage_name(
                conn, name, crow,
            )
            conn.execute("DELETE FROM characters WHERE name=?", (name,))
            _delete_owned_items_for_character(conn, name, holder_cnum)
            if crow is not None:
                crow = _heal_char_rows_cnum_clashes(conn, [crow])[0]
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

        for cnum, shard, payload in meta.get("heavy_sidecar_rows") or ():
            conn.execute(
                "INSERT OR REPLACE INTO character_heavy_blobs "
                "(cnum, shard, payload) VALUES (?,?,?)",
                (str(cnum), str(shard), payload),
            )
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
    if _refuse_unsafe_force_full_wipe(conn, char_rows, meta):
        _persist_restore_deferred_dirty(
            game,
            set(meta.get("deferred_chars") or ()),
            set(meta.get("deferred_rooms") or ()),
        )
        empty = not char_rows
        game._last_world_save_stats = {
            "skipped": True,
            "empty_force_full": empty,
            "short_force_full": (not empty),
            "collect_ms": round(collect_ms, 2),
            "apply_ms": 0.0,
            "force_full": bool(meta.get("force_full")),
            "n_char_rows": len(char_rows or ()),
            "n_item_rows": 0,
            "skipped_login_names": list(meta.get("skipped_login_names") or ()),
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
        if meta.get("force_full_complete"):
            _persist_finish_force_full_if_due(game, meta)
        elif meta.get("force_full") and not meta.get("force_full_chunked_apply"):
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
        "force_full_chars_only": bool(meta.get("force_full_chars_only")),
        "n_char_rows": len(char_rows),
        "n_item_rows": len(item_rows),
        "n_changed_chars": len(meta.get("changed_chars") or ()),
        "n_changed_rooms": len(meta.get("changed_floor_rooms") or ()),
        "n_deferred_chars": len(deferred_chars),
        "n_deferred_rooms": len(deferred_rooms),
        "autosave_count": int(getattr(game, "_persist_autosave_count", 0) or 0),
        "full_every": persist_full_every(),
        "slow_chars": meta.get("slow_chars"),
        "slow_char_detail": meta.get("slow_char_detail"),
        "wall_budget_hit": bool(meta.get("wall_budget_hit")),
        "char_build_total_ms": meta.get("char_build_total_ms"),
        "char_build_count": meta.get("char_build_count"),
        "char_build_avg_ms": meta.get("char_build_avg_ms"),
        "floor_rooms_scanned": meta.get("floor_rooms_scanned"),
        "floor_rooms_hash_skip": meta.get("floor_rooms_hash_skip"),
        "floor_yield_count": meta.get("floor_yield_count"),
        "floor_build_total_ms": meta.get("floor_build_total_ms"),
        "floor_build_count": meta.get("floor_build_count"),
        "floor_build_avg_ms": meta.get("floor_build_avg_ms"),
        "slow_floor_rooms": meta.get("slow_floor_rooms"),
        "floor_collect_fast_skip": meta.get("floor_collect_fast_skip"),
        "n_dirty_chars_this_pass": meta.get("n_dirty_chars_this_pass"),
        "n_dirty_rooms_this_pass": meta.get("n_dirty_rooms_this_pass"),
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
        "force_full_chars_only": bool(meta.get("force_full_chars_only")),
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
        "slow_char_detail": meta.get("slow_char_detail"),
        "wall_budget_hit": bool(meta.get("wall_budget_hit")),
        "char_build_total_ms": meta.get("char_build_total_ms"),
        "char_build_count": meta.get("char_build_count"),
        "char_build_avg_ms": meta.get("char_build_avg_ms"),
        "floor_rooms_scanned": meta.get("floor_rooms_scanned"),
        "floor_rooms_hash_skip": meta.get("floor_rooms_hash_skip"),
        "floor_yield_count": meta.get("floor_yield_count"),
        "floor_build_total_ms": meta.get("floor_build_total_ms"),
        "floor_build_count": meta.get("floor_build_count"),
        "floor_build_avg_ms": meta.get("floor_build_avg_ms"),
        "slow_floor_rooms": meta.get("slow_floor_rooms"),
        "floor_collect_fast_skip": meta.get("floor_collect_fast_skip"),
        "n_dirty_chars_this_pass": meta.get("n_dirty_chars_this_pass"),
        "n_dirty_rooms_this_pass": meta.get("n_dirty_rooms_this_pass"),
    }


def save_world_before_process_exit(conn, game, *, reason="shutdown"):
    """Force a full world snapshot before copyover exit or gateway shutdown.

    Incremental dirty-cap passes can defer bodies that sold/fenced during
    the post-deploy autosave window; a terminating save must rewrite every
    live character or inventory and wallet drift apart after restart (bug
    report 690).

    Must clear a stale ``_persist_force_full_chars_only`` before calling
    ``save_world`` -- a scheduled chars-only verify (lag U2 / CPU followon
    2026-09) can leave that flag set for several autosaves while it chunks
    across the wall budget. Leaving it set here would make
    ``_persist_force_full_collect_flags`` skip the floor-room full-verify
    on a terminating save (``force_full_floor`` computes False), silently
    dropping any floor items past the incremental dirty cap right before
    the process exits.
    """
    if conn is None or game is None:
        return
    if persist_background_writer_enabled():
        from engine.persistence_writer import shutdown_persistence_writer
        shutdown_persistence_writer(timeout=persist_writer_drain_timeout_s())
    game._persist_world_full_scheduled = False
    _persist_reset_force_full_verify_progress(game)
    _mark_online_session_characters_dirty(game)
    game._persist_force_full = True
    game._persist_force_full_chars_only = False
    save_world(conn, game)
    _abort_copyover_skipped_save(game)


def archive_online_login_checkpoints(conn, game):
    """Fail-soft player-save tank for logged-in account PCs after copyover.

    Manual ``save`` is the only other writer of ``player-checkpoints``. A
    skipped copyover wipe left Ayla's tank 22h stale (bug report 1509).
    """
    if conn is None or game is None:
        return 0
    from engine.char_identity import is_account_linked_body

    archived = 0
    for sess in list(getattr(game, "sessions", None) or ()):
        char = getattr(sess, "character", None)
        if char is None or not is_account_linked_body(char):
            continue
        try:
            ok, _msg = persist_save_character(
                conn, game, char, player_checkpoint=True, flush_built_sites=False,
            )
            if ok:
                archived += 1
        except Exception as exc:
            name = getattr(char, "key", None) or "?"
            print(
                f"[player_checkpoint] copyover archive skipped for "
                f"{name!r}: {exc!r}",
                flush=True,
            )
    if archived:
        print(
            f"[player_checkpoint] copyover archived {archived} online "
            f"login body(ies)",
            flush=True,
        )
    return archived


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
    _abort_copyover_incomplete_snapshot(game, char_rows, meta)
    if _refuse_unsafe_force_full_wipe(conn, char_rows, meta):
        empty = not char_rows
        game._last_world_save_stats = {
            "skipped": True,
            "empty_force_full": empty,
            "short_force_full": (not empty),
            "collect_ms": round(collect_ms, 2),
            "apply_ms": 0.0,
            "force_full": True,
            "n_char_rows": len(char_rows or ()),
            "n_item_rows": 0,
            "skipped_login_names": list(meta.get("skipped_login_names") or ()),
        }
        return
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
        "force_full_chars_only": bool(meta.get("force_full_chars_only")),
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
    from engine import boot_profile as boot_profile_mod
    from engine import game_heartbeat as game_heartbeat_mod
    from engine import hooks as hooks_mod

    heavy_sidecar_index = hooks_mod.load_heavy_sidecars_index(conn)

    char_i = 0
    for name, description, room_key, blob, row_cnum in _iter_saved_character_rows(
        conn
    ):
        char_i += 1
        if char_i % 200 == 0:
            game_heartbeat_mod.touch_heartbeat("load_world_characters")
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
        char.game = game
        try:
            saved = json.loads(blob)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            from engine import log_util
            from engine import metrics as metrics_mod
            log_util.ops(
                "persistence",
                f"quarantine corrupt blob key={name!r} ({exc!r})",
            )
            metrics_mod.bump(game, "load_corrupt_blob")
            # Keep a stub Echo so the name is not silently gone (HB-25).
            # Do not apply the corrupt JSON. Place on the start / safe room
            # after the loop via pending list.
            char.corrupt_blob_quarantine = True
            char.description = (
                "This body is here, but the memory that held it would "
                "not reform. Staff: a corrupt save blob was quarantined."
            )
            pending_link = None
            # Fall through to room placement -- still an Echo, no blob.
            try:
                room = _safe_relocation_room(game, char) or _resolve_saved_room(
                    game, room_key, name
                )
            except RecursionError:
                import traceback
                print(
                    f"[persistence] RecursionError rematerializing "
                    f"quarantined {name!r} room_key={room_key!r} -- "
                    f"using start room",
                    flush=True,
                )
                traceback.print_exc()
                room = game.start_room
            try:
                from engine.world import safe_place
                safe_place(char, room)
            except Exception:
                char.move_to(room)
            _apply_persist_cnum_column(char, row_cnum)
            continue
        if row_cnum and heavy_sidecar_index:
            side = heavy_sidecar_index.get(str(row_cnum))
            if side:
                saved = hooks_mod.merge_saved_with_sidecars(saved, side)
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
        try:
            pending_link = apply_character_blob(char, saved)
        except Exception as exc:
            from engine import log_util
            from engine import metrics as metrics_mod
            log_util.ops(
                "persistence",
                f"quarantine apply_character_blob key={name!r} ({exc!r})",
            )
            metrics_mod.bump(game, "load_corrupt_blob")
            char.corrupt_blob_quarantine = True
            char.description = (
                "This body is here, but the memory that held it would "
                "not reform. Staff: a corrupt save blob was quarantined."
            )
            pending_link = None
        _apply_persist_cnum_column(char, row_cnum)
        # Keep the saved room_key even when map JSON is stale -- stub rather
        # than silently dumping onto start_room / North Avenue (no_loiter).
        # RecursionError here used to abort the whole Game() boot (live
        # 2026-09-16 mine dest cycle). Cage one character so the rest of
        # the world still comes up.
        try:
            room = _resolve_saved_room(game, room_key, name, character=char)
        except RecursionError:
            import traceback
            print(
                f"[persistence] RecursionError rematerializing "
                f"room_key={room_key!r} for {name!r} -- using start room",
                flush=True,
            )
            traceback.print_exc()
            room = game.start_room
        char.move_to(room)          # session stays None: this is an Echo
        if pending_link is not None:
            body_room_key, body_key = pending_link
            pending_body_links.append((char, body_room_key, body_key))

    boot_profile_mod.mark("load_world_characters")
    from engine.char_identity import heal_unique_character_cnums

    unique = heal_unique_character_cnums(game)
    for char in unique.get("changed") or ():
        mark_character_dirty(game, char, force=True)

    # Hoisted out of the item loop below (fast copyover boot plan 8):
    # every one of these ``from ... import ...`` statements used to run
    # once *per item row* -- cheap on their own (the module is already in
    # ``sys.modules``), but 52,499 repeats of six-plus import statements
    # adds real wall-clock on live-sized boots. Import once here instead;
    # behavior is identical, these are the same module-level singletons
    # the old per-item imports resolved to.
    from engine.systems import civic_fixture as civic_fixture_mod
    from engine.item_inum import apply_saved_inum
    from engine import hooks as hooks_mod
    from engine.hooks import orphan_item_room
    from engine.room_vnum import lookup_room

    # O(1) holder lookup for the item loop below (fast copyover boot
    # plan 8). ``resolve_held_character`` -> ``find_character_by_cnum`` /
    # ``find_character_exact_key`` each do a *linear scan* of every live
    # Character (``engine.char_identity``, by design -- that scan is fine
    # for a single command-path lookup). Calling it once per gear/
    # inventory item row turned item load O(items x characters): on live,
    # 52,499 items x 414 characters is ~21.7M comparisons just to find
    # who holds what. All characters are already loaded at this point (the
    # characters loop above just finished), so build a plain dict index
    # once and reuse it for every item instead. Same match semantics as
    # ``resolve_held_character``: exact CNUM first, then exact
    # case-insensitive ``key``.
    from engine.char_cnum import validate_cnum
    from engine.char_index import iter_characters

    _by_cnum = {}
    _by_key = {}
    for _char in iter_characters(game):
        raw_cnum = getattr(_char, "cnum", None)
        if raw_cnum:
            try:
                _by_cnum[validate_cnum(raw_cnum)] = _char
            except ValueError:
                pass
        char_key = (getattr(_char, "key", None) or "").strip().lower()
        if char_key:
            _by_key[char_key] = _char

    def _resolve_holder(holder_cnum, holder_key):
        """Same result as ``resolve_held_character`` -- O(1), not O(chars)."""
        if holder_cnum:
            try:
                found = _by_cnum.get(validate_cnum(holder_cnum))
            except ValueError:
                found = None
            if found is not None:
                return found
        if holder_key:
            return _by_key.get((holder_key or "").strip().lower())
        return None

    for (
        key, description, holder_type, holder_key, container, holder_cnum
    ) in _iter_saved_item_rows(conn):
        # json.loads(container) parses the blob _item_container_blob wrote.
        # .get(..., default) means an items row saved before the 'container'
        # column existed (container == '{}', the column's DEFAULT) loads as
        # a plain, unlocked flavor item -- exactly what it was before.
        # One corrupt row must not abort load_world (HB-01) -- character
        # blobs already fail-soft; items must match.
        try:
            state = json.loads(container)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            from engine import log_util
            from engine import metrics as metrics_mod
            log_util.ops(
                "persistence",
                f"skip corrupt item container key={key!r} ({exc!r})",
            )
            metrics_mod.bump(game, "load_corrupt_item")
            continue
        item = Item(
            key, description,
            locked=state.get("locked", False),
            loot=_loot_from_json(state.get("loot", [])),
            is_body=state.get("is_body", False),
            is_buried=state.get("is_buried", False),
            relic=state.get("relic", None),
            furniture=state.get("furniture", False),
        )
        if state.get("world_fixture"):
            item.world_fixture = True
        if state.get("glassbench_prop"):
            item.glassbench_prop = True
        # Restore creation stamp (or keep __init__ seq) and advance the
        # global counter so later live spawns stay "newer".
        if state.get("created_seq") is not None:
            try:
                item.created_seq = int(state["created_seq"])
            except (TypeError, ValueError):
                pass
        note_item_created_seq(getattr(item, "created_seq", 0))
        if state.get("civic_fixture") or state.get("fixture_id") or state.get("shop_id"):
            fid = state.get("fixture_id") or state.get("shop_id")
            civic_fixture_mod.restore_fixture_item(
                item, str(fid).strip() if fid else None
            )
            if state.get("player_shop_fixture"):
                item.player_shop_fixture = True
        elif str(key or "").startswith(civic_fixture_mod.FIXTURE_KEY_PREFIX):
            civic_fixture_mod.restore_fixture_item(item)
        elif str(key or "").startswith(civic_fixture_mod.LEGACY_FIXTURE_KEY_PREFIX):
            civic_fixture_mod.restore_fixture_item(item)
        if state.get("owner_key"):
            item.owner_key = state["owner_key"]
        if state.get("need"):
            item.need = state["need"]
        if state.get("provides_light"):
            item.provides_light = True
        if state.get("catalog_id"):
            item.catalog_id = state["catalog_id"]
        apply_saved_inum(item, state.get("inum"))
        if state.get("magic_focus_id"):
            item.magic_focus_id = state["magic_focus_id"]
        if state.get("magic_focus_kind"):
            item.magic_focus_kind = state["magic_focus_kind"]
        if state.get("magic_focus_target_key"):
            item.magic_focus_target_key = state["magic_focus_target_key"]
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
        if state.get("god_forge_infused_until"):
            try:
                item.god_forge_infused_until = int(state["god_forge_infused_until"])
            except (TypeError, ValueError):
                pass
        if state.get("god_forge_infuse_damage_bonus") is not None:
            item.god_forge_infuse_damage_bonus = state[
                "god_forge_infuse_damage_bonus"
            ]
        if state.get("god_forge_infuse_accuracy_bonus") is not None:
            item.god_forge_infuse_accuracy_bonus = state[
                "god_forge_infuse_accuracy_bonus"
            ]
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
        if isinstance(state.get("angel_blade_growth_mods"), list):
            item.angel_blade_growth_mods = [
                str(m) for m in state["angel_blade_growth_mods"] if str(m).strip()
            ]
        if state.get("angel_blade_budget_earned") is not None:
            try:
                item.angel_blade_budget_earned = int(state["angel_blade_budget_earned"])
            except (TypeError, ValueError):
                pass
        if state.get("angel_blade_prototype_seed"):
            item.angel_blade_prototype_seed = str(state["angel_blade_prototype_seed"])
        if state.get("bound_angel_twin"):
            item.bound_angel_twin = True
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
        if state.get("wet") is not None:
            item.wet = bool(state["wet"])
        if state.get("gear_condition") is not None:
            try:
                item.gear_condition = int(state["gear_condition"])
            except (TypeError, ValueError):
                pass
        if state.get("laptop_battery_ticks") is not None:
            try:
                item.laptop_battery_ticks = int(state["laptop_battery_ticks"])
            except (TypeError, ValueError):
                pass
        if state.get("virus_bricked_until") is not None:
            try:
                item.virus_bricked_until = int(state["virus_bricked_until"])
            except (TypeError, ValueError):
                pass
        # Fridge pantry timer (home grocery stock); absent on older saves.
        if state.get("stock_until_tick") is not None:
            try:
                item.stock_until_tick = int(state["stock_until_tick"])
            except (TypeError, ValueError):
                pass
        if state.get("pet_servings") is not None:
            try:
                item.pet_servings = int(state["pet_servings"])
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
        _restore_phone_media_fields(item, state)
        if state.get("is_ethereal"):
            item.is_ethereal = True
        if state.get("is_spirit_mirror"):
            item.is_spirit_mirror = True
        if state.get("spirit_mirror_source_key"):
            item.spirit_mirror_source_key = str(
                state["spirit_mirror_source_key"]
            ).strip()
        if state.get("is_god_twin_visual"):
            item.is_god_twin_visual = True
        _restore_portal_key_fields(item, state)
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
        if state.get("three_bloods_washed"):
            item.three_bloods_washed = True
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
        _restore_god_weapon_fields(item, state)
        _restore_relic_fields(item, state)
        _restore_pit_mimic_fields(item, state)
        _restore_loot_payloads(item, state)
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
            # Curb fixtures are owned by ``player_shops`` registry heal, not
            # the items table -- skip reload so boot cannot stack copies.
            if civic_fixture_mod.is_fixture_item(item):
                continue
            # Glass-shop torch benches are heal-only (ensure_glassbench_look).
            # Legacy 1717 rows lost glassbench_prop on save; skip by name too.
            if _is_heal_only_glassbench_item(item):
                continue
            # Missing holder room (map rename / unload) -- game hook picks
            # the lost-item vault; bare engine falls back to start_room.
            sink = lookup_room(game, holder_key)
            if sink is None:
                sink = hooks_mod.demesne_lookup_room_for_persist(game, holder_key)
            sink = sink or orphan_item_room(game)
            if sink is not None:
                # dedupe=False: this Item was just constructed above, so it
                # can never already be in ``sink.contents`` -- the identity
                # check Room.add() normally does to guard against a double
                # add is provably a no-op here, but it is an O(len(contents))
                # scan every call. On a room that accumulates many floor
                # items (a lost-item vault after map renames, a popular
                # floor-loot spot, ...) that turned load_world_items
                # quadratic instead of linear. track_floor=False: the
                # index this would update is fully rebuilt right after
                # load_world by rebuild_floor_item_room_index (one O(rooms)
                # pass) -- see that function's docstring -- and
                # ``game._persist_force_full`` is already True for this
                # whole boot, so per-item dirty-marking here is thrown away
                # unread before it could ever matter.
                sink.add(item, dedupe=False, track_floor=False)
            # Catalog gear + pit potion on_use heal (same as inventory).
            hooks_mod.enrich_loaded_item(item)
        elif holder_type == "gear":
            # Job kit bag -- not surface inventory (supers/gear_bag).
            # CNUM first, then exact storage key -- never fuzzy find_character.
            owner = _resolve_holder(holder_cnum, holder_key)
            if owner:
                bag = getattr(owner, "gear_bag", None)
                if bag is None or not isinstance(bag, list):
                    owner.gear_bag = []
                    bag = owner.gear_bag
                bag.append(item)
                hooks_mod.enrich_loaded_item(item)
        else:
            owner = _resolve_holder(holder_cnum, holder_key)
            if owner:               # owner should always exist; guard anyway
                owner.inventory.append(item)
                # Enrich from catalog when only catalog_id survived.
                hooks_mod.enrich_loaded_item(item)

    boot_profile_mod.mark("load_world_items")
    from engine.item_inum import heal_unique_item_inums

    unique_items = heal_unique_item_inums(game)
    stamped = int(unique_items.get("stamped") or 0)
    reallocated = int(unique_items.get("reallocated") or 0)
    if stamped or reallocated:
        print(
            f"[boot] item INUM heal stamped={stamped} "
            f"reallocated={reallocated}",
            flush=True,
        )
    # One dirty per holder/room, not per copy -- first boot otherwise
    # queues the same Echo thousands of times and stalls autosave.
    dirty_owners = set()
    dirty_rooms = set()
    for _item, owner, room in unique_items.get("changed") or ():
        if owner is not None:
            owner_key = getattr(owner, "key", None)
            if owner_key and owner_key not in dirty_owners:
                dirty_owners.add(owner_key)
                mark_character_dirty(game, owner, force=True)
        elif room is not None:
            room_key = getattr(room, "key", None) or id(room)
            if room_key not in dirty_rooms:
                dirty_rooms.add(room_key)
                mark_floor_room_dirty(game, room)

    boot_profile_mod.mark("load_world_inum")
    # After all inventory rows land, rebuild equipment maps from equipped flags.
    from engine.char_index import iter_characters
    from engine import hooks
    for char in iter_characters(game):
        hooks.rebind_character_equipment(char)
    boot_profile_mod.mark("load_world_rebind")

    # Per-character lodging / vehicle board normalization (boot lifecycle
    # Phase C) -- replaces full-world boot sweeps for these fields.
    for idx, char in enumerate(iter_characters(game)):
        hooks.normalize_character_after_load(char, game)
        if idx and idx % 200 == 0:
            game_heartbeat_mod.touch_heartbeat("load_world_normalize")
    boot_profile_mod.mark("load_world_normalize")
    boot_profile_mod.print_accum("load_world_normalize")

    # Section 6: relink each spirit's body/body_room object refs now that
    # every Item has been placed back into its room.
    for char, body_room_key, body_key in pending_body_links:
        resolve_pending_body_link(game, char, body_room_key, body_key)

    # Rebuild follow-bond pointers from persisted leader keys (copyover /
    # save-load). Must run after every Character exists in the roster.
    try:
        from engine import group as group_mod

        group_mod.heal_follow_bonds(game)
    except Exception:
        pass


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
    from engine.room_vnum import lookup_room

    room = lookup_room(game, body_room_key) if body_room_key else None
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
        try:
            state = json.loads(container or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            # HB extra: corrupt vault/stash container must not abort
            # character load the way HB-01 used to abort boot.
            print(
                f"[persistence] corrupt saved container for {key!r} -- "
                "loading as empty state",
                flush=True,
            )
            state = {}
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
    if state.get("world_fixture"):
        item.world_fixture = True
    if state.get("glassbench_prop"):
        item.glassbench_prop = True
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
    from engine.item_inum import apply_saved_inum

    apply_saved_inum(item, state.get("inum"))
    if state.get("magic_focus_id"):
        item.magic_focus_id = state["magic_focus_id"]
    if state.get("magic_focus_kind"):
        item.magic_focus_kind = state["magic_focus_kind"]
    if state.get("magic_focus_target_key"):
        item.magic_focus_target_key = state["magic_focus_target_key"]
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
    if state.get("god_forge_infused_until"):
        try:
            item.god_forge_infused_until = int(state["god_forge_infused_until"])
        except (TypeError, ValueError):
            pass
    if state.get("god_forge_infuse_damage_bonus") is not None:
        item.god_forge_infuse_damage_bonus = state[
            "god_forge_infuse_damage_bonus"
        ]
    if state.get("god_forge_infuse_accuracy_bonus") is not None:
        item.god_forge_infuse_accuracy_bonus = state[
            "god_forge_infuse_accuracy_bonus"
        ]
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
    if isinstance(state.get("angel_blade_growth_mods"), list):
        item.angel_blade_growth_mods = [
            str(m) for m in state["angel_blade_growth_mods"] if str(m).strip()
        ]
    if state.get("angel_blade_budget_earned") is not None:
        try:
            item.angel_blade_budget_earned = int(state["angel_blade_budget_earned"])
        except (TypeError, ValueError):
            pass
    if state.get("angel_blade_prototype_seed"):
        item.angel_blade_prototype_seed = str(state["angel_blade_prototype_seed"])
    if state.get("bound_angel_twin"):
        item.bound_angel_twin = True
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
    if state.get("wet") is not None:
        item.wet = bool(state["wet"])
    if state.get("stock_until_tick") is not None:
        try:
            item.stock_until_tick = int(state["stock_until_tick"])
        except (TypeError, ValueError):
            pass
    if state.get("pet_servings") is not None:
        try:
            item.pet_servings = int(state["pet_servings"])
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
    if state.get("laptop_battery_ticks") is not None:
        try:
            item.laptop_battery_ticks = int(state["laptop_battery_ticks"])
        except (TypeError, ValueError):
            pass
    if state.get("virus_bricked_until") is not None:
        try:
            item.virus_bricked_until = int(state["virus_bricked_until"])
        except (TypeError, ValueError):
            pass
    if state.get("is_phone"):
        item.is_phone = True
    if state.get("is_payphone"):
        item.is_payphone = True
        item.furniture = True
    _restore_phone_media_fields(item, state)
    if state.get("is_ethereal"):
        item.is_ethereal = True
    _restore_portal_key_fields(item, state)
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
    _restore_god_weapon_fields(item, state)
    _restore_relic_fields(item, state)
    _restore_pit_mimic_fields(item, state)
    _restore_loot_payloads(item, state)
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
            "key": _persist_item_field(item, "key"),
            "description": _persist_item_field(
                item, "description", fallback=_persist_item_field(item, "key"),
            ),
            "container": _item_container_dict(item),
        })
    for item in list(getattr(character, "gear_bag", None) or []):
        rows.append({
            "holder_type": "gear",
            "key": _persist_item_field(item, "key"),
            "description": _persist_item_field(
                item, "description", fallback=_persist_item_field(item, "key"),
            ),
            "container": _item_container_dict(item),
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
    """Inverse of ``compress_vault_envelope``.

    Junk bytes used to raise raw zlib/JSON errors mid-restore. Log and
    raise a single ValueError so callers can refuse the vault row.
    """
    try:
        raw = zlib.decompress(payload_bytes)
        return json.loads(raw.decode("utf-8"))
    except (TypeError, ValueError, OverflowError, OSError, zlib.error) as exc:
        from engine import log_util
        log_util.ops("persistence", f"vault envelope corrupt ({exc!r})")
        raise ValueError("corrupt vault envelope") from exc
