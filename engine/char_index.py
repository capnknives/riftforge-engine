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


def authored_room_index(game, attr, builder):
    """Cache ``builder(rooms)`` until the authored map graph changes.

    Overland virtual cells change ``len(game.rooms)`` every hop. Indexes of
    stamped dens, hubs, hotels, and gyms must key on
    ``room_graph_cache_token`` (``_static_room_graph_gen``) so Cadence does
    not re-walk ~12k rooms every heartbeat. ``builder`` receives the rooms
    mapping and should skip ``room_is_virtual_overland`` cells.
    """
    if game is None:
        return builder({})
    token = room_graph_cache_token(game)
    cache = getattr(game, attr, None)
    if cache is not None and cache[0] == token:
        return cache[1]
    rooms = getattr(game, "rooms", None) or {}
    indexed = builder(rooms)
    setattr(game, attr, (token, indexed))
    return indexed


class RoomMap(dict):
    """``game.rooms`` dict that stamps ``room.game`` on every insert.

    Procedural dungeons, strongholds, and smoke tests all do
    ``game.rooms[key] = room``; without this stamp, Room.add would not
    know which Game owns the character roster.

    Phase 3: ``get`` also resolves legacy dig keys via
    ``game.room_aliases`` (VNUM identity keys are the primary dict keys).

    Authored (non-virtual) insert/delete bumps ``_static_room_graph_gen``
    so room-graph caches rebuild once, not on every overland cell spawn.
    """

    def __init__(self, game):
        """Bind to the owning Game (called before rooms are filled)."""
        super().__init__()
        self._game = game

    def __setitem__(self, key, room):
        """Insert ``room`` and point ``room.game`` at the owning Game."""
        old = dict.get(self, key)
        room.game = self._game
        super().__setitem__(key, room)
        if old is room:
            return
        game = self._game
        if old is not None:
            _maybe_bump_static_graph(game, old)
        _maybe_bump_static_graph(game, room)

    def __delitem__(self, key):
        """Drop a room and bump authored-graph gen when it was a real map room."""
        room = dict.get(self, key)
        super().__delitem__(key)
        if room is not None:
            _maybe_bump_static_graph(self._game, room)

    def _resolve_alias(self, key):
        """Return Room for identity key or Phase 3 legacy alias, else None."""
        hit = dict.get(self, key)
        if hit is not None:
            return hit
        if key is None:
            return None
        aliases = getattr(self._game, "room_aliases", None) or {}
        mapped = aliases.get(key)
        if mapped is not None:
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
        """Stamp every room, including bulk ``update`` from build_world."""
        # dict.update can bypass __setitem__ for some call shapes --
        # assign one-by-one so stamping is never skipped.
        if isinstance(other, dict):
            items = other.items()
        else:
            items = other
        for key, room in items:
            self[key] = room
        for key, room in kwargs.items():
            self[key] = room


def find_character_by_key(game, key):
    """Locate a Character by exact ``Character.key``, or None.

    Uses the live ``game.characters`` roster when wired (scan ~50 actors,
    not ~12k rooms). Falls back to a room scan for stubs/tests that never
    stamped ``room.game``.
    """
    if game is None or not key:
        return None
    chars = getattr(game, "characters", None)
    if isinstance(chars, set):
        for ch in chars:
            if getattr(ch, "key", None) == key:
                return ch
        return None
    from engine.world import Character
    for room in (getattr(game, "rooms", None) or {}).values():
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
    for room in rooms.values():
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


def register_character(game, character):
    """Add ``character`` to the live roster (idempotent)."""
    chars = getattr(game, "characters", None)
    if isinstance(chars, set):
        chars.add(character)
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
    from engine import hooks as hooks_mod
    hooks_mod.fuel_loop_roster_notify(game, character, op="remove")


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
