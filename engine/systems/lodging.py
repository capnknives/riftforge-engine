"""lodging.py -- generic bed occupancy, lodging units, and home claims.

Engine-side lodging mechanics: bed furniture detection, sharing rules
(via ``lodging_are_family`` hook), safe-sleep policy (via
``lodging_sleep_policy`` hook), and rent/claim ledger scans. Game layers
(SUPERS hotel rent, house doors, sleep verbs) stay in ``supers/lodging.py``.

Pure logic: no sockets, no ``supers`` imports.
"""

from __future__ import annotations

from engine import hooks

# BASIC lodging amenity tags stamped on homes / hotel rooms / bunk rooms.
HOME_BASIC_RESOURCES = ("water", "entertainment", "hygiene")

# Max people on one bed Item. Empty = open; one sleeper = open only to a
# registered family member (lover hook); two = full.
BED_SHARE_MAX = 2


def is_lodging_unit(room):
    """True for claimable homes and generic hotel / lodging rooms.

    Checks authored room flags only -- legacy hotel key lists stay in the
    game layer (SUPERS ``is_hotel_guest_room``).
    """
    if room is None:
        return False
    if getattr(room, "is_house", False):
        return True
    if getattr(room, "is_hotel_room", False):
        return True
    if getattr(room, "is_lodging", False):
        return True
    return False


def stamp_home_basics(room):
    """Ensure ``room`` offers the BASIC lodging amenity tags.

    Idempotent. Fires ``stamp_lodging_room`` so the game may add fields
    after the generic stamp (H1 ``stamp_map_room`` pattern).
    Returns True when at least one tag was added.
    """
    if room is None:
        return False
    resources = list(getattr(room, "resources", None) or [])
    changed = False
    for tag in HOME_BASIC_RESOURCES:
        if tag not in resources:
            resources.append(tag)
            changed = True
    if changed:
        room.resources = resources
    hooks.stamp_lodging_room(room)
    return changed


def _is_bed(obj):
    """True if ``obj`` is a furniture Item that provides sleep."""
    from world import Item

    if not isinstance(obj, Item):
        return False
    if not getattr(obj, "furniture", False):
        return False
    return getattr(obj, "need", None) == "sleep"


def has_bunks(room):
    """True when this room offers unlimited stronghold bunks (no bed Item)."""
    if room is None:
        return False
    if not getattr(room, "has_bunks", False):
        return False
    return "sleep" in (getattr(room, "resources", ()) or ())


def beds_in_room(room):
    """Return every bed furniture Item currently in ``room``."""
    if room is None:
        return []
    return [obj for obj in room.contents if _is_bed(obj)]


def bed_occupants(bed, room):
    """Return every Character currently asleep on this bed."""
    from world import Character

    if room is None or bed is None:
        return []
    bed_id = id(bed)
    found = []
    for obj in room.contents:
        if not isinstance(obj, Character):
            continue
        if not getattr(obj, "asleep", False):
            continue
        if getattr(obj, "sleep_bed_id", None) == bed_id:
            found.append(obj)
    return found


def bed_occupant(bed, room):
    """Return one Character asleep on this bed, or None."""
    occ = bed_occupants(bed, room)
    return occ[0] if occ else None


def bed_available_to(character, bed, room):
    """True if ``character`` may lie down on this bed right now."""
    if character is None or bed is None:
        return False
    occ = bed_occupants(bed, room)
    if not occ:
        return True
    if len(occ) >= BED_SHARE_MAX:
        return False
    return hooks.lodging_are_family(character, occ[0])


def free_beds(room, prefer_owner=None, family_id=None, for_character=None):
    """Beds ``for_character`` can use, ordered: preferred owner first."""
    free = []
    for bed in beds_in_room(room):
        if for_character is not None:
            if not bed_available_to(for_character, bed, room):
                continue
        elif bed_occupant(bed, room) is not None:
            continue
        free.append(bed)
    if not free:
        return []

    def _rank(bed):
        owner = getattr(bed, "owner_key", None)
        if prefer_owner and owner == prefer_owner:
            return 0
        if for_character is not None:
            occ = bed_occupants(bed, room)
            if len(occ) == 1 and hooks.lodging_are_family(for_character, occ[0]):
                return 0
        if owner is None or owner == "":
            return 1
        if prefer_owner and owner != prefer_owner:
            return 2
        return 1

    free.sort(key=_rank)
    return free


def pick_bed(room, character, bed_name=None):
    """Choose a bed for ``character`` in ``room``.

    Returns ``(bed, None)`` or ``(None, reason_string)``.
    """
    beds = beds_in_room(room)
    if not beds:
        return None, "There is no bed here."
    if bed_name:
        needle = bed_name.strip().lower()
        named = [b for b in beds if needle in b.key.lower()]
        if not named:
            return None, f"You don't see a bed called '{bed_name}' here."
        beds = named
    free = [bed for bed in beds if bed_available_to(character, bed, room)]
    if not free:
        return None, "Every bed here is taken."
    with_family = [
        b for b in free
        if any(
            hooks.lodging_are_family(character, o) for o in bed_occupants(b, room)
        )
    ]
    if with_family:
        return with_family[0], None
    own = [b for b in free if getattr(b, "owner_key", None) == character.key]
    if own:
        return own[0], None
    unowned = [b for b in free if not getattr(b, "owner_key", None)]
    if unowned:
        return unowned[0], None
    return free[0], None


def is_safe_sleep_venue(room, character=None, game=None):
    """True when sleep here is a bed / home / authored floor camp (not public).

    Game-specific rules (vehicles, vampire zones, …) register via
    ``lodging_sleep_policy``; when the hook returns a bool, that wins.
    Otherwise fall back to generic bed / bunk / house checks.
    """
    if room is None:
        return False
    policy = hooks.lodging_sleep_policy(room, character, game)
    if policy is not None:
        return bool(policy)
    if has_bunks(room):
        return True
    if getattr(room, "floor_sleep", False):
        return True
    if "sleep" in getattr(room, "resources", ()) and not beds_in_room(room):
        return True
    if getattr(room, "is_house", False):
        return True
    if beds_in_room(room):
        return True
    return False


def _compound_hub_key(room):
    """Return the claim-hub key for ``room``, or ``room.key`` / None."""
    if room is None:
        return None
    main = getattr(room, "main_homeroom", None)
    if main:
        return main
    return getattr(room, "key", None)


def claimants_of(game, room_key):
    """Characters whose home is this room or its house compound."""
    if not room_key or game is None:
        return []
    cache = getattr(game, "_cadence_claimants_cache", None)
    if isinstance(cache, dict) and room_key in cache:
        return cache[room_key]
    from engine.char_index import iter_characters

    dest = game.rooms.get(room_key)
    main = _compound_hub_key(dest) if dest is not None else room_key
    found = []
    for obj in iter_characters(game):
        home_key = getattr(obj, "home_room_key", None)
        if not home_key:
            continue
        if home_key == room_key or (main and home_key == main):
            found.append(obj)
            continue
        if dest is None or not main:
            continue
        home_room = game.rooms.get(home_key)
        if home_room is None:
            continue
        if _compound_hub_key(home_room) == main:
            found.append(obj)
    if isinstance(cache, dict):
        cache[room_key] = found
    return found


def is_room_claimed(game, room):
    """True if any living character lists this room (or its house) as home."""
    return bool(claimants_of(game, room.key))
