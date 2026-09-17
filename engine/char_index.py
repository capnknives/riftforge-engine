"""
engine/char_index.py -- O(1) Character roster for the live world.

The map holds ~12k rooms (100x100 Wastes + planar grids). Walking
``game.rooms.values()`` once per tick handler to rediscover ~50 actors
was the live lag sawtooth: dozens of full scans every 3 seconds on the
single asyncio thread.

Game keeps ``game.characters`` (a set) updated by Room.add / Room.remove
whenever ``room.game`` is stamped. ``RoomMap`` (below) stamps ``room.game``
on every insert into ``game.rooms``, including procedural dungeons and
smoke-test ad-hoc rooms.

Logout does NOT remove a character -- Echoes stay in the world and stay
in the set (logout != deletion). Only Room.remove (despawn / Hakai /
ephemeral kill) drops them. Character.move_to bypasses Room.remove so a
room change does not flicker the roster.

Authored-room indexes (vampire streets, vacancy desks, Cadence zones)
cache against ``game._static_room_graph_gen``, not ``len(game.rooms)``.
Overland virtual cells churn room-count every heartbeat; bumping gen
only for real map/dungeon rooms keeps those indexes warm.
"""


def room_is_virtual_overland(room):
    """True for ephemeral atlas cells (not authored map rooms).

    Matches ``engine.systems.overland.is_virtual_room`` without importing
    that module -- ``RoomMap`` insert must stay import-light (overland
    already imports ``engine.world``).
    """
    return bool(getattr(room, "virtual_overland", False))


def note_static_room_graph_change(game):
    """Bump the authored-room generation (map dig / dungeon load / delete)."""
    if game is None:
        return
    game._static_room_graph_gen = int(
        getattr(game, "_static_room_graph_gen", 0) or 0
    ) + 1


def _maybe_bump_static_graph(game, room):
    """Bump gen for authored rooms only -- skip dual-layer overland cells."""
    if game is None or room is None:
        return
    if room_is_virtual_overland(room):
        return
    note_static_room_graph_change(game)


def room_graph_cache_token(game):
    """Cache key for indexes of authored rooms.

    Production ``Game`` always stamps ``_static_room_graph_gen``. Overland
    virtual cells change ``len(game.rooms)`` constantly; those inserts do
    not bump the gen, so vampire/vacancy/zone indexes stay warm.

    FakeGame stubs that never set the attr keep the old ``len(rooms)``
    invalidation so targeted smokes stay honest.
    """
    if game is None:
        return (0,)
    if hasattr(game, "_static_room_graph_gen"):
        return (int(game._static_room_graph_gen or 0),)
    rooms = getattr(game, "rooms", None) or {}
    return (len(rooms),)


def snapshot_room_map(rooms):
    """Copy a rooms mapping so index builders can walk it safely.

    Overland cells, look extras, and pocket materialize insert into
    ``game.rooms`` while other code is still iterating that same dict.
    Python then raises ``RuntimeError: dictionary changed size during
    iteration`` -- live ``shoot`` hit this on a lethal drop (ashen prey
    Marches scan), and the same resize raced no_loiter / plane-hub /
    diner-ticket ticks.

    The copy is a plain dict of the rooms that existed at snapshot time.
    Nested inserts still land on the live ``game.rooms``; they just
    cannot resize the view the builder is walking.

    ``dict(rooms.items())`` can itself raise if an insert lands mid-copy
    (single asyncio thread: the inserter has then finished). One retry
    is enough.
    """
    if not rooms:
        return {}
    try:
        return dict(rooms.items())
    except RuntimeError:
        return dict(rooms.items())


def snapshot_room_values(rooms):
    """Tuple of Room objects, safe to iterate while the live map grows.

    Same race as :func:`snapshot_room_map`, for call sites that only need
    ``.values()`` (plane hubs, no_loiter, Devil's Gates).
    """
    if not rooms:
        return ()
    try:
        return tuple(rooms.values())
    except RuntimeError:
        return tuple(rooms.values())


def authored_room_index(game, attr, builder):
    """Cache ``builder(rooms)`` until the authored map graph changes.

    Overland virtual cells change ``len(game.rooms)`` every hop. Indexes of
    stamped dens, hubs, hotels, and gyms must key on
    ``room_graph_cache_token`` (``_static_room_graph_gen``) so Cadence does
    not re-walk ~12k rooms every heartbeat. ``builder`` receives a
    **snapshot** mapping (not the live ``RoomMap``) and should skip
    ``room_is_virtual_overland`` cells.
    """
    if game is None:
        return builder({})
    token = room_graph_cache_token(game)
    cache = getattr(game, attr, None)
    if cache is not None and cache[0] == token:
        return cache[1]
    rooms = snapshot_room_map(getattr(game, "rooms", None) or {})
    indexed = builder(rooms)
    setattr(game, attr, (token, indexed))
    return indexed


class RoomMap(dict):
    """``game.rooms`` dict that stamps ``room.game`` on every insert.

    Procedural dungeons, strongholds, and smoke tests all do
    ``game.rooms[key] = room``; without this stamp, Room.add would not
    know which Game owns the character roster.

    Phase 3: ``get`` / ``in`` / subscript resolve leftover dig keys, VNUM
    case-fold, and the live alias fold (same identity as
    ``room_vnum.lookup_room``). VNUM identity keys are the primary dict keys.


    Authored (non-virtual) insert/delete bumps ``_static_room_graph_gen``
    so room-graph caches rebuild once, not on every overland cell spawn.
    """

    # Live ``room_vnum.RoomAliasBatch`` during a bulk ``update``, else None.
    # Class-level default so a subclass or ``dict`` copy path that skips
    # __init__ still reads "no batch running" instead of AttributeError.
    _alias_batch = None
    _alias_batch_depth = 0

    def __init__(self, game):
        """Bind to the owning Game (called before rooms are filled)."""
        super().__init__()
        self._game = game

    def __setitem__(self, key, room):
        """Insert ``room`` and point ``room.game`` at the owning Game."""
        old = dict.get(self, key)
        room.game = self._game
        super().__setitem__(key, room)
        # Keep a running bulk-insert index in step *before* the alias guard
        # reads it, so the guard sees the same rooms a full rebuild would.
        if self._alias_batch is not None:
            self._alias_batch.add_room(room)
        self._register_vnum_legacy_alias(key, room)
        if old is room:
            return
        game = self._game
        if old is not None:
            _maybe_bump_static_graph(game, old)
        _maybe_bump_static_graph(game, room)

    def _register_vnum_legacy_alias(self, key, room):
        """Phase 3: leftover dig names resolve after insert under the VNUM.

        ``stamp_hand_room`` already writes ``room_aliases``. Runtime pockets
        that only do ``game.rooms[vnum] = room`` still need the reverse map
        or Cadence ``rooms.get(legacy_home)`` misses.
        """
        if room is None:
            return
        if getattr(room, "grid_prefix", None) is not None:
            return
        raw = getattr(room, "vnum", None)
        if not raw:
            return
        from engine.room_vnum import validate_vnum, note_room_alias

        try:
            vnum = validate_vnum(raw)
        except ValueError:
            return
        if str(key) != vnum:
            return
        leg = getattr(room, "legacy_key", None)
        if not leg:
            return
        if self._alias_batch is not None:
            # Same guard, same moment -- just without the O(rooms) rebuild.
            self._alias_batch.note(leg, vnum)
            return
        note_room_alias(self._game, leg, vnum)

    def __delitem__(self, key):
        """Drop a room and bump authored-graph gen when it was a real map room."""
        room = dict.get(self, key)
        super().__delitem__(key)
        if room is not None:
            from engine.room_vnum import drop_room_alias

            leg = getattr(room, "legacy_key", None)
            if leg:
                drop_room_alias(self._game, leg)
            _maybe_bump_static_graph(self._game, room)

    def _resolve_alias(self, key):
        """Return Room for identity key, leftover alias, or folded VNUM."""
        hit = dict.get(self, key)
        if hit is not None:
            return hit
        if key is None:
            return None
        text = str(key).strip()
        if not text:
            return None
        aliases = getattr(self._game, "room_aliases", None) or {}
        if isinstance(aliases, dict):
            mapped = aliases.get(text)
            if mapped is not None:
                hit = dict.get(self, mapped)
                if hit is not None:
                    return hit
        from engine.room_vnum import parse_vnum, format_vnum, _ensure_alias_fold

        parsed = parse_vnum(text)
        if parsed is not None:
            vnum = format_vnum(*parsed)
            if vnum != text:
                hit = dict.get(self, vnum)
                if hit is not None:
                    return hit
        mapped = _ensure_alias_fold(self._game).get(text.lower())
        if mapped:
            return dict.get(self, mapped)
        return None

    def __getitem__(self, key):
        """Identity key first, then Phase 3 legacy dig-key aliases.

        Call sites still write ``rooms[\"lebanon:…\"]`` after VNUM rekey;
        ``in`` / ``get`` already aliased -- subscript must match or boot
        heals crash (``KeyError`` after a true ``in`` check).
        """
        hit = self._resolve_alias(key)
        if hit is not None:
            return hit
        raise KeyError(key)

    def get(self, key, default=None):
        """Identity key first, then Phase 3 legacy dig-key aliases."""
        hit = self._resolve_alias(key)
        if hit is not None:
            return hit
        return default

    def __contains__(self, key):
        """True when identity key or legacy alias resolves."""
        return self._resolve_alias(key) is not None

    def update(self, other=(), **kwargs):
        """Stamp every room, including bulk ``update`` from build_world.

        Runs the inserts under a ``room_vnum.RoomAliasBatch``: the legacy-key
        alias guard is two O(rooms) passes per call, so ``build_world``'s
        single ``update`` was quadratic (~190s of live's ~280s boot). The
        batch keeps those maps incrementally instead of rebuilding them per
        room; the guard's answers do not change.

        ``try/finally`` clears the batch even if an insert raises, so a
        failed boot cannot leave later single inserts on a frozen index.
        """
        # dict.update can bypass __setitem__ for some call shapes --
        # assign one-by-one so stamping is never skipped.
        if isinstance(other, dict):
            items = other.items()
        else:
            items = other
        if self._alias_batch is None:
            from engine.room_vnum import RoomAliasBatch

            # Instance attr shadows the class default; only the outermost
            # update owns it, so a nested update reuses the same maps.
            self._alias_batch = RoomAliasBatch(self._game)
        self._alias_batch_depth += 1
        try:
            for key, room in items:
                self[key] = room
            for key, room in kwargs.items():
                self[key] = room
        finally:
            self._alias_batch_depth -= 1
            if self._alias_batch_depth <= 0:
                self._alias_batch = None
                self._alias_batch_depth = 0


def find_character_by_key(game, key):
    """Locate a Character by exact ``Character.key``, or None.

    Uses the live ``game.characters`` roster when wired (scan ~50 actors,
    not ~12k rooms). Falls back to a room scan for stubs/tests that never
    stamped ``room.game``.

    ``game.characters_by_key`` is an O(1) map kept by register/unregister
    so pet owner / haul lookups do not walk the whole set every beat
    (Pass 25 fuel/pets leftover).
    """
    if game is None or not key:
        return None
    chars = getattr(game, "characters", None)
    idx = getattr(game, "characters_by_key", None)
    if not isinstance(idx, dict) and isinstance(chars, set):
        idx = {
            getattr(ch, "key", None): ch
            for ch in chars
            if getattr(ch, "key", None)
        }
        game.characters_by_key = idx
    if isinstance(idx, dict):
        hit = idx.get(key)
        if hit is not None and getattr(hit, "key", None) == key:
            return hit
        # Repair a half-built map (first register created an empty dict
        # before the rest of the roster landed). Misses still scan the
        # live set so pets / vessels / missions cannot lose a body.
        if isinstance(chars, set):
            for ch in chars:
                if getattr(ch, "key", None) == key:
                    idx[key] = ch
                    return ch
            return None
    # Smoke stubs and some tests wire a name→Character dict, not a set.
    if isinstance(chars, dict):
        hit = chars.get(key)
        if hit is not None and getattr(hit, "key", None) == key:
            return hit
        for ch in chars.values():
            if getattr(ch, "key", None) == key:
                return ch
        return None
    from engine.world import Character
    for room in snapshot_room_values(getattr(game, "rooms", None) or {}):
        for obj in room.contents:
            if isinstance(obj, Character) and obj.key == key:
                return obj
    return None


def capture_character_snapshot(game):
    """Build a roster tuple, ignoring any in-flight heartbeat snapshot.

    ``run_ticks`` / ``run_ticks_async`` stash this on
    ``game._tick_character_snapshot`` so ~30 Origin ``tick_all`` handlers
    share one copy instead of each calling ``tuple(game.characters)``.
    """
    chars = getattr(game, "characters", None)
    if isinstance(chars, set):
        return tuple(chars)
    # Slow fallback -- same shape the old tick helpers used.
    from engine.world import Character
    rooms = getattr(game, "rooms", None) or {}
    found = []
    for room in snapshot_room_values(rooms):
        for obj in room.contents:
            if isinstance(obj, Character):
                found.append(obj)
    return tuple(found)


def iter_characters(game):
    """Return a stable snapshot of every Character currently in the world.

    Prefers the heartbeat snapshot while a tick is running, then
    ``game.characters`` when the registry is wired. Falls back to a full
    room scan for stubs/tests that never stamped ``room.game``.

    Returns a tuple so callers can despawn mid-loop without "set changed
    size during iteration". Command-path walks (outside a tick) still
    see the live set.
    """
    snapshot = getattr(game, "_tick_character_snapshot", None)
    if snapshot is not None:
        return snapshot
    return capture_character_snapshot(game)


def iter_online_characters(game):
    """Yield bodies that have a client Session -- O(sessions), not O(roster).

    Live heartbeats used to walk every town NPC and Echo just to skip
    them (``character.session is None``). With ~500 bodies that walk
    showed up as ``rest_online`` 80–400 ms every tick while only five
    people were actually logged in (2026-08-31 gist 934d887b).

    When ``game.sessions`` has at least one bound character, yield those
    only. Smokes that stamp ``character.session = FakeSession()`` without
    listing the Session still fall back to the roster scan so existing
    tests keep accruing.
    """
    seen = set()
    found = False
    # ``list(...)`` so a disconnect mid-loop cannot change the walk.
    sessions = list(getattr(game, "sessions", None) or ())
    for sess in sessions:
        char = getattr(sess, "character", None)
        if char is None:
            continue
        cid = id(char)
        if cid in seen:
            continue
        seen.add(cid)
        found = True
        yield char
    if found:
        return
    for character in iter_characters(game):
        if getattr(character, "session", None) is None:
            continue
        yield character


def _remember_character_key(game, character):
    """Stamp ``game.characters_by_key`` for O(1) exact-key lookup."""
    key = getattr(character, "key", None)
    if game is None or not key:
        return
    idx = getattr(game, "characters_by_key", None)
    if not isinstance(idx, dict):
        idx = {}
        game.characters_by_key = idx
    idx[key] = character


def _forget_character_key(game, character):
    """Drop a roster row from the exact-key map."""
    key = getattr(character, "key", None)
    idx = getattr(game, "characters_by_key", None)
    if not isinstance(idx, dict) or not key:
        return
    if idx.get(key) is character:
        idx.pop(key, None)


def register_character(game, character):
    """Add ``character`` to the live roster (idempotent)."""
    chars = getattr(game, "characters", None)
    if isinstance(chars, set):
        chars.add(character)
    _remember_character_key(game, character)
    from engine import hooks as hooks_mod
    hooks_mod.fuel_loop_roster_notify(game, character, op="add")
    if getattr(game, "_persist_warm", False):
        from engine.persistence import mark_character_dirty
        # Online player moves must land on the next autosave immediately.
        # Echo / NPC Room.add traffic used to force-dirty the whole Cadence
        # roster every step and saturate the incremental char cap.
        online = getattr(character, "session", None) is not None
        mark_character_dirty(game, character, force=online)


def unregister_character(game, character):
    """Remove ``character`` from the live roster (idempotent)."""
    chars = getattr(game, "characters", None)
    if isinstance(chars, set):
        chars.discard(character)
    _forget_character_key(game, character)
    from engine import hooks as hooks_mod
    hooks_mod.fuel_loop_roster_notify(game, character, op="remove")


def ensure_room_characters_registered(game, room):
    """Register every Character in ``room.contents`` missing from the roster.

    Ephemeral pockets (mission strongholds, procedural dungeons) used to
    spawn hostiles before ``game.rooms[...] = room`` stamped ``room.game``,
    so ``move_to`` never reached ``Room.add``'s roster hook (bug report
    1131). Call after pocket rooms are registered, or from boot heal for
    live pockets opened before the producer fix.
    """
    if game is None or room is None:
        return 0
    chars = getattr(game, "characters", None)
    if not isinstance(chars, set):
        return 0
    from engine.world import Character

    added = 0
    for obj in list(getattr(room, "contents", []) or []):
        if isinstance(obj, Character) and obj not in chars:
            register_character(game, obj)
            added += 1
    return added


def heal_ephemeral_pocket_roster_gaps(game):
    """Register fightable hostiles left out of ``game.characters`` at spawn.

    Scans live mission stronghold and wilderness procedural dungeon rooms
    only -- not the full ~12k room map. Idempotent; no-op when the producer
    already registered occupants.
    """
    if game is None:
        return 0
    from engine import hooks as hooks_mod

    healed = 0
    for room in snapshot_room_values(getattr(game, "rooms", None) or {}):
        if room is None:
            continue
        if not (
            getattr(room, "mission_instance", False)
            or hooks_mod.is_ephemeral_instance_room(room)
        ):
            continue
        healed += ensure_room_characters_registered(game, room)
    return healed


def rebuild_character_index(game):
    """Rebuild ``game.characters`` from room contents (boot / recovery).

    Normal play should never need this -- Room.add/remove keep the set
    truthful. Useful after a bulk load that bypassed move_to, or if ops
    suspect the index drifted.
    """
    from engine.world import Character
    game.characters = set()
    for room in game.rooms.values():
        for obj in room.contents:
            if isinstance(obj, Character):
                game.characters.add(obj)
    from engine import hooks as hooks_mod
    hooks_mod.fuel_loop_roster_notify(game, None, op="rebuild")
