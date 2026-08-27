"""room_reset.py -- timer-based room repop when empty.

When a room carries ``reset_empty_ticks`` (int) and optional
``reset_item_specs`` (list of item seed dicts), this module tracks how long
the room has had **no player characters** standing in it. After that many
game ticks, floor items are cleared and the authored default contents are
restored from the snapshot.

Pairs conceptually with ``engine/systems/spawn/nest_ai.py`` (tick-driven
world maintenance) but operates on **room loot**, not NPC spawns.

``tick`` only walks ``game._reset_rooms`` (rooms that declared a positive
``reset_empty_ticks`` at boot or via ``note_reset_room``), not every room in
``game.rooms`` — live worlds have ~12k rooms but only a handful reset.

Register ``tick`` from the game's ``tick_bootstrap`` (basegame does this).

stdlib only.
"""

from __future__ import annotations


def _now_tick(game):
    return int(getattr(game, "game_time_ticks", 0) or 0)


def _reset_room_key(room):
    """Stable index key — ``room.key`` matches ``game.rooms`` dict keys."""
    key = getattr(room, "key", None)
    if key is not None:
        return key
    return id(room)


def _room_qualifies_for_reset(room):
    """True when ``room`` should stay on the reset index."""
    if room is None:
        return False
    delay = getattr(room, "reset_empty_ticks", None)
    if delay is None:
        return False
    try:
        return int(delay) > 0
    except (TypeError, ValueError):
        return False


def _ensure_reset_index(game):
    """Return ``game._reset_rooms``, building it once on first use."""
    idx = getattr(game, "_reset_rooms", None)
    if idx is None:
        register_reset_rooms(game)
        idx = getattr(game, "_reset_rooms", None)
    return idx if idx is not None else set()


def note_reset_room(game, room):
    """Add ``room`` to the reset tick index when it gains ``reset_empty_ticks``.

    Call from map load / staff ``rset`` writers when they set a positive delay.
    Boot ``register_reset_rooms`` already covers the common JSON-load path.
    """
    if not _room_qualifies_for_reset(room):
        return False
    idx = getattr(game, "_reset_rooms", None)
    if idx is None:
        idx = set()
        game._reset_rooms = idx
    idx.add(_reset_room_key(room))
    return True


def drop_reset_room(game, room):
    """Remove ``room`` from the reset tick index (flag cleared or room gone)."""
    idx = getattr(game, "_reset_rooms", None)
    if not idx:
        return False
    key = _reset_room_key(room)
    if key in idx:
        idx.discard(key)
        return True
    return False


def _players_in_room(room):
    """Live player characters (not NPCs) currently in ``room``."""
    found = []
    for obj in list(getattr(room, "contents", []) or []):
        if getattr(obj, "is_npc", False):
            continue
        if getattr(obj, "session", None) is not None:
            found.append(obj)
        elif not getattr(obj, "is_guest", False):
            # Echoes count as occupants -- room is not "empty".
            found.append(obj)
    return found


def _item_specs_for_room(room):
    """Authoring snapshot: explicit list or captured at boot."""
    specs = getattr(room, "reset_item_specs", None)
    if isinstance(specs, list):
        return list(specs)
    snap = getattr(room, "_reset_item_snapshot", None)
    if isinstance(snap, list):
        return list(snap)
    return []


def capture_reset_snapshot(room):
    """Store current floor Items as seed specs for later restore.

    Called at boot for rooms with ``reset_empty_ticks`` set. Each spec is
    ``{"key": str, "description": str}`` plus optional extra Item fields
    copied when present.
    """
    from world import Item

    if room is None:
        return []
    specs = []
    for obj in list(getattr(room, "contents", []) or []):
        if not isinstance(obj, Item):
            continue
        if getattr(obj, "is_body", False) or getattr(obj, "furniture", False):
            continue
        spec = {
            "key": getattr(obj, "key", "something"),
            "description": getattr(obj, "description", ""),
        }
        for attr in ("catalog_id", "weight", "volume", "max_hp"):
            val = getattr(obj, attr, None)
            if val is not None:
                spec[attr] = val
        specs.append(spec)
    room._reset_item_snapshot = specs
    return specs


def register_reset_rooms(game):
    """Boot pass: snapshot every room that declares ``reset_empty_ticks``.

    Also builds ``game._reset_rooms`` so ``tick`` never scans the full world.
    """
    rooms = getattr(game, "rooms", None) or {}
    index = set()
    game._reset_rooms = index
    count = 0
    for room in rooms.values():
        if not _room_qualifies_for_reset(room):
            continue
        index.add(_reset_room_key(room))
        if not _item_specs_for_room(room):
            capture_reset_snapshot(room)
        count += 1
    return count


def _clear_resettable_items(room):
    """Remove pocketable floor items (not bodies / furniture / characters)."""
    from world import Item

    removed = []
    for obj in list(getattr(room, "contents", []) or []):
        if not isinstance(obj, Item):
            continue
        if getattr(obj, "is_body", False) or getattr(obj, "furniture", False):
            continue
        room.remove(obj)
        removed.append(obj)
    return removed


def _spawn_spec(room, spec):
    """Place one Item from a seed spec onto ``room``."""
    from world import Item

    if not isinstance(spec, dict):
        return None
    key = spec.get("key") or "something"
    desc = spec.get("description") or key
    item = Item(key, desc)
    for attr in ("catalog_id", "weight", "volume", "max_hp"):
        if attr in spec and spec[attr] is not None:
            setattr(item, attr, spec[attr])
    room.add(item)
    return item


def reset_room(room, game=None):
    """Force-restore ``room`` to its authored default floor items.

    Returns the number of items spawned. ``game`` is accepted for symmetry
    with other engine kits but unused here.
    """
    del game
    if room is None:
        return 0
    _clear_resettable_items(room)
    spawned = 0
    for spec in _item_specs_for_room(room):
        if _spawn_spec(room, spec) is not None:
            spawned += 1
    room._reset_empty_since = None
    return spawned


def _resolve_indexed_room(game, key):
    """Map index key back to a live room object, or ``None``."""
    rooms = getattr(game, "rooms", None) or {}
    if isinstance(key, str):
        return rooms.get(key)
    for room in rooms.values():
        if _reset_room_key(room) == key:
            return room
    return None


def tick(game):
    """Advance empty-room timers and restore defaults when due.

    Only visits ``game._reset_rooms`` — not ``game.rooms.values()``.
    Drops stale keys when the room vanished or no longer has a reset delay.
    """
    index = _ensure_reset_index(game)
    if not index:
        return
    now = _now_tick(game)
    for key in list(index):
        room = _resolve_indexed_room(game, key)
        if room is None or not _room_qualifies_for_reset(room):
            index.discard(key)
            continue
        delay = int(room.reset_empty_ticks)
        if _players_in_room(room):
            room._reset_empty_since = None
            continue
        since = getattr(room, "_reset_empty_since", None)
        if since is None:
            room._reset_empty_since = now
            continue
        try:
            since_i = int(since)
        except (TypeError, ValueError):
            room._reset_empty_since = now
            continue
        if now - since_i >= delay:
            reset_room(room, game)
            room._reset_empty_since = now


def register_tick(game):
    """Wire ``tick`` onto ``game`` via ``engine.tick_registry``."""
    from engine.tick_registry import register_tick

    register_tick(game, tick, order=85, name="room_reset")
