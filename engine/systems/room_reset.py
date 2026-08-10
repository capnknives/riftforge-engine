"""room_reset.py -- timer-based room repop when empty.

When a room carries ``reset_empty_ticks`` (int) and optional
``reset_item_specs`` (list of item seed dicts), this module tracks how long
the room has had **no player characters** standing in it. After that many
game ticks, floor items are cleared and the authored default contents are
restored from the snapshot.

Pairs conceptually with ``engine/systems/spawn/nest_ai.py`` (tick-driven
world maintenance) but operates on **room loot**, not NPC spawns.

Register ``tick`` from the game's ``tick_bootstrap`` (basegame does this).

stdlib only.
"""

from __future__ import annotations


def _now_tick(game):
    return int(getattr(game, "game_time_ticks", 0) or 0)


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
    """Boot pass: snapshot every room that declares ``reset_empty_ticks``."""
    rooms = getattr(game, "rooms", None) or {}
    count = 0
    for room in rooms.values():
        ticks = getattr(room, "reset_empty_ticks", None)
        if ticks is None:
            continue
        try:
            if int(ticks) <= 0:
                continue
        except (TypeError, ValueError):
            continue
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


def tick(game):
    """Advance empty-room timers and restore defaults when due."""
    rooms = getattr(game, "rooms", None) or {}
    now = _now_tick(game)
    for room in rooms.values():
        delay = getattr(room, "reset_empty_ticks", None)
        if delay is None:
            continue
        try:
            need = int(delay)
        except (TypeError, ValueError):
            continue
        if need <= 0:
            continue
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
        if now - since_i >= need:
            reset_room(room, game)
            room._reset_empty_since = now


def register_tick(game):
    """Wire ``tick`` onto ``game`` via ``engine.tick_registry``."""
    from engine.tick_registry import register_tick

    register_tick(game, tick, order=85, name="room_reset")
