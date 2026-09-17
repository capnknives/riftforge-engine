"""O(1) lookup for items that carry a unique world key.

Bodies (``is_body``) are unique by ``Item.key``. Body receptacles
(``body_receptacle``) are also indexed, but tray/plot names repeat per
morgue and graveyard (``morgue drawer 1`` in every town). The map therefore
stores a list per key; lookup prefers the caller's room so a Lebanon
drawer never steals a Calumet detach.

Generic stacked loot is not indexed here -- many coins share a name, so a
global item-by-key map would lie.

Room.add / Room.remove keep the map warm. First lookup after copyover
rebuilds once from room contents so hearse / morgue ticks never walk
~20k rooms per queued corpse (Pass 24 P24-5 / P24-19).
"""


def item_is_unique_indexed(obj):
    """True when ``obj`` is a body or tray/plot with a unique ``.key``."""
    if obj is None:
        return False
    return bool(
        getattr(obj, "is_body", False)
        or getattr(obj, "body_receptacle", False)
    )


def _index(game):
    idx = getattr(game, "_unique_items_by_key", None)
    if not isinstance(idx, dict):
        idx = {}
        game._unique_items_by_key = idx
    return idx


def _bucket_list(idx, key):
    """Return the live list for ``key``, migrating a leftover single object."""
    hit = idx.get(key)
    if hit is None:
        return []
    if isinstance(hit, list):
        return hit
    bucket = [hit]
    idx[key] = bucket
    return bucket


def _add_to_index(idx, key, obj):
    bucket = _bucket_list(idx, key)
    if obj not in bucket:
        bucket.append(obj)
    idx[key] = bucket


def _drop_from_index(idx, key, obj):
    if not key or obj is None:
        return
    hit = idx.get(key)
    if hit is obj:
        idx.pop(key, None)
        return
    if not isinstance(hit, list):
        return
    try:
        hit.remove(obj)
    except ValueError:
        return
    if not hit:
        idx.pop(key, None)


def rebuild_unique_item_index(game):
    """Walk rooms once and stamp ``game._unique_items_by_key``."""
    if game is None:
        return
    from engine.char_index import snapshot_room_values

    idx = {}
    for room in snapshot_room_values(getattr(game, "rooms", None) or {}):
        for obj in list(getattr(room, "contents", None) or []):
            if not item_is_unique_indexed(obj):
                continue
            key = str(getattr(obj, "key", "") or "").strip()
            if key:
                _add_to_index(idx, key, obj)
    game._unique_items_by_key = idx
    game._unique_items_index_warm = True


def ensure_unique_item_index(game):
    """Rebuild when no live map exists yet (boot / FakeGame stubs)."""
    if game is None:
        return
    if getattr(game, "_unique_items_index_warm", False) and isinstance(
        getattr(game, "_unique_items_by_key", None), dict
    ):
        return
    rebuild_unique_item_index(game)


def register_unique_item(game, obj):
    """Remember a unique body / receptacle after it lands in a room."""
    if game is None or not item_is_unique_indexed(obj):
        return
    key = str(getattr(obj, "key", "") or "").strip()
    if not key:
        return
    _add_to_index(_index(game), key, obj)


def unregister_unique_item(game, obj):
    """Drop a unique body / receptacle when it leaves a room."""
    if game is None or obj is None:
        return
    idx = getattr(game, "_unique_items_by_key", None)
    if not isinstance(idx, dict):
        return
    key = str(getattr(obj, "key", "") or "").strip()
    _drop_from_index(idx, key, obj)


def note_unique_item_renamed(game, obj, old_key):
    """Keep the map honest when a plot stone restamps ``.key``."""
    if game is None or obj is None:
        return
    idx = getattr(game, "_unique_items_by_key", None)
    old = str(old_key or "").strip()
    if isinstance(idx, dict) and old:
        _drop_from_index(idx, old, obj)
    register_unique_item(game, obj)


def find_unique_item(game, key, *, prefer_room=None):
    """Return the unique Item for ``key``, or None.

    Warms the index on first call. Duplicate tray/plot names pick the
    object in ``prefer_room`` when given, else the first registered (same
    order as the old rooms.values() scan). Haulers keep the body off the
    floor; callers that care about hauled corpses still walk the roster.
    """
    want = str(key or "").strip()
    if not want or game is None:
        return None
    ensure_unique_item_index(game)
    idx = getattr(game, "_unique_items_by_key", None)
    if not isinstance(idx, dict):
        return None
    candidates = _bucket_list(idx, want)

    def _key_matches(obj):
        return (
            obj is not None
            and str(getattr(obj, "key", "") or "").strip() == want
        )

    if prefer_room is not None:
        for obj in candidates:
            if getattr(obj, "location", None) is prefer_room and _key_matches(obj):
                return obj
        # Room.add registers; contents.append after a warm index does not.
        # Scan this one room (not the continent) and repair the map.
        for obj in list(getattr(prefer_room, "contents", None) or []):
            if item_is_unique_indexed(obj) and _key_matches(obj):
                register_unique_item(game, obj)
                return obj
    for obj in candidates:
        if _key_matches(obj):
            return obj
    return None
