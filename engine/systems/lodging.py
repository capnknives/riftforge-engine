"""
lodging.py -- generic beds, home compounds, lease ticks, sleep venue checks.

Game-specific lover-sharing, hotel key lists, vagrancy fiction, and zone
gates register via ``engine.hooks`` (``set_lodging_sleep_policy``,
``set_lodging_bed_eligibility``, ``set_lodging_rent_tick``). Stdlib only;
zero ``supers`` imports.
"""

from __future__ import annotations

from engine import hooks as hooks_mod

# BASIC lodging amenities shared by houses, apartments, and hotel guest rooms.
HOME_BASIC_RESOURCES = ("water", "entertainment", "hygiene")

# Max people on one bed Item (empty / one sleeper / full).
BED_SHARE_MAX = 2

_TICKS_PER_GAME_DAY = 9600
LEASE_TICKS = _TICKS_PER_GAME_DAY


def is_hotel_guest_room(room):
    """True when ``room`` is a rented hotel guest unit (``is_hotel_room``)."""
    if room is None:
        return False
    return bool(getattr(room, "is_hotel_room", False))


def is_lodging_unit(room):
    """True for claimable homes and hotel guest rooms."""
    if room is None:
        return False
    if getattr(room, "is_house", False):
        return True
    return is_hotel_guest_room(room)


def stamp_home_basics(room):
    """Ensure ``room`` offers the BASIC lodging amenity tags (idempotent)."""
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
    return changed


def ensure_home_basics(game):
    """Boot catch-up: stamp BASIC amenities on every lodging unit."""
    if game is None:
        return
    for room in getattr(game, "rooms", {}).values():
        if is_lodging_unit(room):
            stamp_home_basics(room)


def _resolve_exit_room(dest, game=None):
    """Return a live Room from an exits{} value (Room or key string)."""
    if dest is None:
        return None
    if not isinstance(dest, str):
        return dest
    rooms = getattr(game, "rooms", None) or {}
    return rooms.get(dest)


def is_house_satellite_chamber(room):
    """True when this interior is an attached bay/yard, not the claim hub.

    Garage and backyard remodeled chambers carry a named inbound door on
    the living room (and a ``living`` return). They must never win
    ``pick_main_homeroom`` or look treats them as a separate unclaimed
    house (buyhome on look).
    """
    if room is None:
        return False
    label = (getattr(room, "remodel_inbound_exit", None) or "").strip().lower()
    if label:
        return True
    rtype = (getattr(room, "remodel_type", None) or "").strip().lower()
    if rtype in ("garage", "backyard"):
        return True
    exits = getattr(room, "exits", None) or {}
    # Attached garage bay: named return to living plus street/porch out.
    if "living" in exits and (
        "out" in exits or getattr(room, "vehicle_berth", False)
    ):
        return True
    # Fenced backyard pocket: named living return, outdoor.
    if "living" in exits and getattr(room, "outdoor", False):
        return True
    return False


def house_interior_cluster(seed, game):
    """Every ``is_house`` room in the same interior compound as ``seed``."""
    if seed is None or game is None:
        return []
    if not getattr(seed, "is_house", False):
        return []
    seen = {seed.key: seed}
    stack = [seed]
    while stack:
        room = stack.pop()
        for dest in (room.exits or {}).values():
            neighbor = _resolve_exit_room(dest, game)
            if neighbor is None:
                continue
            if not getattr(neighbor, "is_house", False):
                continue
            key = getattr(neighbor, "key", None)
            if not key or key in seen:
                continue
            seen[key] = neighbor
            stack.append(neighbor)
    return list(seen.values())


def pick_main_homeroom(rooms, game=None):
    """Choose the claim-hub Room for a house interior cluster."""
    interiors = [r for r in (rooms or ()) if r is not None]
    if not interiors:
        return None
    # Attached garage/backyard bays are never the deed hub.
    hubs = [r for r in interiors if not is_house_satellite_chamber(r)]
    if not hubs:
        # Do not elect a garage/backyard as the deed hub (bug report 863).
        return None
    by_key = {r.key: r for r in hubs if getattr(r, "key", None)}
    for key, room in by_key.items():
        if key.endswith(" Living"):
            return room
    for room in hubs:
        outbound = _resolve_exit_room((room.exits or {}).get("out"), game)
        if outbound is not None and getattr(outbound, "private_home", False):
            return room
    for room in hubs:
        main = getattr(room, "main_homeroom", None)
        if main and main == getattr(room, "key", None):
            return room
    return sorted(hubs, key=lambda r: r.key)[0]


def stamp_house_home_links(rooms, main_room=None, game=None):
    """Stamp ``is_home`` + ``main_homeroom`` on every room in the cluster."""
    interiors = [r for r in (rooms or ()) if r is not None]
    if not interiors:
        return None
    main = main_room or pick_main_homeroom(interiors, game=game)
    if main is None:
        return None
    main_key = main.key
    for room in interiors:
        room.is_home = True
        room.main_homeroom = main_key
        if not getattr(room, "is_house", False):
            room.is_house = True
    return main_key


def ensure_house_home_links(game):
    """Boot catch-up: link every ``is_house`` cluster with home stamps."""
    if game is None:
        return 0
    stamped = 0
    seen = set()
    for room in getattr(game, "rooms", {}).values():
        if not getattr(room, "is_house", False):
            continue
        key = getattr(room, "key", None)
        if not key or key in seen:
            continue
        cluster = house_interior_cluster(room, game)
        if not cluster:
            continue
        for member in cluster:
            seen.add(member.key)
        main_guess = pick_main_homeroom(cluster, game=game)
        main_key = main_guess.key if main_guess else None
        already = True
        for member in cluster:
            if not getattr(member, "is_home", False):
                already = False
                break
            if getattr(member, "main_homeroom", None) != main_key:
                already = False
                break
        if already and main_key:
            continue
        if stamp_house_home_links(cluster, main_guess, game=game):
            stamped += 1
    return stamped


def main_homeroom_key(room):
    """Return the claim-hub key for ``room``, or ``room.key`` / None."""
    if room is None:
        return None
    main = getattr(room, "main_homeroom", None)
    if main:
        return main
    return getattr(room, "key", None)


def same_house_room(a, b):
    """True when two rooms share a ``main_homeroom`` (or are the same room)."""
    if a is None or b is None:
        return False
    if a is b:
        return True
    if getattr(a, "key", None) and a.key == getattr(b, "key", None):
        return True
    ma = main_homeroom_key(a)
    mb = main_homeroom_key(b)
    return bool(ma) and ma == mb


def room_is_character_home(character, room, game=None):
    """True when ``room`` is (or belongs to) the character's claimed home."""
    if character is None or room is None:
        return False
    home_key = getattr(character, "home_room_key", None)
    if not home_key:
        return False
    if home_key == getattr(room, "key", None):
        return True
    if not getattr(room, "is_house", False) and not getattr(room, "is_home", False):
        return False
    rooms = getattr(game, "rooms", None) if game is not None else None
    home_room = rooms.get(home_key) if rooms else None
    if home_room is None:
        return main_homeroom_key(room) == home_key
    return same_house_room(home_room, room)


def room_allows_wash(room):
    """True when wash / shower / cleanup works in ``room``."""
    if room is None:
        return False
    if "hygiene" in getattr(room, "resources", ()):
        return True
    return is_lodging_unit(room)


def _is_bed(obj):
    """True if ``obj`` is a furniture Item that provides sleep."""
    from world import Item

    if not isinstance(obj, Item):
        return False
    if not getattr(obj, "furniture", False):
        return False
    return getattr(obj, "need", None) == "sleep"


def has_bunks(room):
    """True when this room offers unlimited stronghold bunks."""
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
    # One sleeper: game hook decides sharing (lovers, family, …).
    return hooks_mod.lodging_bed_eligibility(character, bed, room)


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
            if len(occ) == 1 and hooks_mod.lodging_bed_eligibility(
                for_character, bed, room,
            ):
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
    with_share = [
        b for b in free
        if bed_occupants(b, room)
        and hooks_mod.lodging_bed_eligibility(character, b, room)
    ]
    if with_share:
        return with_share[0], None
    own = [b for b in free if getattr(b, "owner_key", None) == character.key]
    if own:
        return own[0], None
    unowned = [b for b in free if not getattr(b, "owner_key", None)]
    if unowned:
        return unowned[0], None
    return free[0], None


def allows_floor_sleep(room):
    """True if this room offers sleep without requiring a bed Item."""
    if room is None:
        return False
    if "sleep" not in getattr(room, "resources", ()):
        return False
    if has_bunks(room) or getattr(room, "floor_sleep", False):
        return True
    return not beds_in_room(room)


def base_safe_sleep_venue(room):
    """Engine-only safe-sleep checks (no game hooks)."""
    if room is None:
        return False
    if has_bunks(room):
        return True
    if allows_floor_sleep(room) or getattr(room, "floor_sleep", False):
        return True
    if getattr(room, "is_house", False):
        return True
    if beds_in_room(room):
        return True
    return False


def is_safe_sleep_venue(room, character=None, game=None):
    """True when sleep here is bed / home / authored floor camp (not public)."""
    if base_safe_sleep_venue(room):
        return True
    return hooks_mod.lodging_sleep_policy(room, character, game)


# Reverse index: home_room_key / compound hub -> characters. Look used to
# call claimants_of once per house (look_home_hint / list_free_homes), and
# each call walked the whole roster then lookup_room (full map scan on a
# miss). Live command_slow: look 16-22s, phases=look:….
_CLAIMANTS_INDEX_ATTR = "_claimants_by_home_index"
_CLAIMANTS_VERSION_ATTR = "_claimants_index_version"


def bump_claimants_index(game):
    """Invalidate the home-claimants reverse index (claim / rent / unclaim).

    Cheap: one counter increment. The next ``claimants_of`` rebuilds from
    the live roster. Also clears Cadence's per-room memo so it cannot
    serve a stale list for the rest of the heartbeat.
    """
    if game is None:
        return
    current = getattr(game, _CLAIMANTS_VERSION_ATTR, 0) or 0
    setattr(game, _CLAIMANTS_VERSION_ATTR, current + 1)
    setattr(game, _CLAIMANTS_INDEX_ATTR, None)
    cache = getattr(game, "_cadence_claimants_cache", None)
    if isinstance(cache, dict):
        cache.clear()


def _claimants_index(game):
    """home key / living-hub key -> characters who claim that compound.

    Built with one roster pass per tick (or after ``bump_claimants_index``).
    Resolves homes through ``game.rooms.get`` (identity + alias) -- never
    ``lookup_room``'s O(rooms) miss scan, which is what made one look
    freeze the asyncio loop for tens of seconds on live.
    """
    if game is None:
        return {}
    tick = int(getattr(game, "game_time_ticks", 0) or 0)
    version = getattr(game, _CLAIMANTS_VERSION_ATTR, 0) or 0
    chars = getattr(game, "characters", None)
    n_chars = len(chars) if isinstance(chars, set) else 0
    cached = getattr(game, _CLAIMANTS_INDEX_ATTR, None)
    if cached is not None:
        cached_tick, cached_version, cached_n, index = cached
        if (
            cached_tick == tick
            and cached_version == version
            and cached_n == n_chars
        ):
            return index
    from engine.char_index import iter_characters

    rooms = getattr(game, "rooms", None) or {}
    by_key = {}
    for obj in iter_characters(game):
        home_key = getattr(obj, "home_room_key", None)
        if not home_key:
            continue
        dest = rooms.get(home_key) if hasattr(rooms, "get") else None
        if dest is None:
            by_key.setdefault(home_key, []).append(obj)
            continue
        canon = getattr(dest, "key", None) or home_key
        by_key.setdefault(canon, []).append(obj)
        if home_key != canon:
            by_key.setdefault(home_key, []).append(obj)
        main = main_homeroom_key(dest)
        if main and main != canon:
            by_key.setdefault(main, []).append(obj)
    index = {key: tuple(group) for key, group in by_key.items()}
    setattr(game, _CLAIMANTS_INDEX_ATTR, (tick, version, n_chars, index))
    return index


def claimants_of(game, room_key):
    """Characters whose home is this room or its house compound."""
    if not room_key or game is None:
        return []
    cache = getattr(game, "_cadence_claimants_cache", None)
    if isinstance(cache, dict) and room_key in cache:
        return cache[room_key]
    rooms = getattr(game, "rooms", None) or {}
    dest = rooms.get(room_key) if hasattr(rooms, "get") else None
    main = main_homeroom_key(dest) if dest is not None else room_key
    index = _claimants_index(game)
    seen = set()
    found = []
    for key in (room_key, main, getattr(dest, "key", None)):
        if not key:
            continue
        for obj in index.get(key, ()):
            ident = id(obj)
            if ident in seen:
                continue
            seen.add(ident)
            found.append(obj)
    if isinstance(cache, dict):
        cache[room_key] = found
    return found


def is_room_claimed(game, room):
    """True if any living character lists this room (or its house) as home."""
    return bool(claimants_of(game, room.key))


def is_player_character(character):
    """True for living player bodies (not town / immersion roster NPCs)."""
    return character is not None and not getattr(character, "is_npc", False)


def _clear_bed_owners_for(game, room_key, owner_key):
    """Clear ``owner_key`` on beds in ``room_key`` that match this renter."""
    room = game.rooms.get(room_key)
    if room is None:
        return 0
    cleared = 0
    for bed in beds_in_room(room):
        if getattr(bed, "owner_key", None) == owner_key:
            bed.owner_key = None
            cleared += 1
    return cleared


def clear_room_bed_owners(room):
    """Wipe ``owner_key`` on every bed furniture Item in ``room``."""
    if room is None:
        return 0
    cleared = 0
    for bed in beds_in_room(room):
        if getattr(bed, "owner_key", None):
            bed.owner_key = None
            cleared += 1
    return cleared


def is_lodging_corridor(room):
    """True for hotel, motel, or apartment floor corridors."""
    if room is None:
        return False
    from engine.systems.procedural_build import (
        is_apartment_floor,
        is_hotel_floor,
        is_motel_floor,
    )

    return (
        is_hotel_floor(room)
        or is_motel_floor(room)
        or is_apartment_floor(room)
    )


def _lodging_unit_on_crossing(from_room, direction, dest):
    """Return the guest unit room when this hop is corridor <-> unit."""
    if from_room is None or dest is None:
        return None
    d = str(direction or "").strip().lower()
    if is_lodging_corridor(from_room):
        if is_hotel_guest_room(dest):
            return dest
        if getattr(dest, "is_house", False) and (
            getattr(dest, "private_home", False)
            or getattr(dest, "main_homeroom", None) == getattr(dest, "key", None)
        ):
            return dest
    if d == "out" and is_lodging_corridor(dest):
        if is_hotel_guest_room(from_room):
            return from_room
        if getattr(from_room, "is_house", False):
            return from_room
    return None


def is_lodging_unit_door(from_room, direction, dest):
    """True when crossing a numbered hotel/motel/apartment unit threshold."""
    return _lodging_unit_on_crossing(from_room, direction, dest) is not None


def lodging_unit_move_phrase(direction, from_room, dest):
    """Return a player-facing phrase like ``into room 2d``, or None."""
    if from_room is None or dest is None:
        return None
    d = str(direction or "").strip().lower()
    if _lodging_unit_on_crossing(from_room, direction, dest) is None:
        return None
    if d == "out":
        return "out into the hall"
    if is_lodging_corridor(from_room):
        return f"into room {d}"
    return None


def lodging_unit_arrive_phrase(dest_room):
    """Arrive suffix when someone enters a guest unit from the corridor."""
    if dest_room is None:
        return None
    if is_hotel_guest_room(dest_room):
        return "from the hall"
    if getattr(dest_room, "is_house", False) and (
        getattr(dest_room, "private_home", False)
        or getattr(dest_room, "main_homeroom", None) == getattr(dest_room, "key", None)
    ):
        return "from the hall"
    return None


def iter_hotel_guest_rooms(game):
    """Yield every live hotel guest Room (``is_hotel_room`` flag)."""
    if game is None:
        return
    from engine.char_index import authored_room_index, room_is_virtual_overland

    def _build(rooms):
        out = []
        seen = set()
        for room in rooms.values():
            if room_is_virtual_overland(room):
                continue
            if not is_hotel_guest_room(room):
                continue
            key = getattr(room, "key", None)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(room)
        return out

    for room in authored_room_index(game, "_hotel_guest_rooms_cache", _build):
        yield room


def clear_hotel_bed_stamps_for(game, owner_key):
    """Remove ``owner_key`` from beds in every hotel guest room."""
    if not owner_key or game is None:
        return 0
    cleared = 0
    for room in iter_hotel_guest_rooms(game):
        cleared += _clear_bed_owners_for(game, room.key, owner_key)
    return cleared


def tick_leases(game):
    """Clear expired hotel leases and free hotel bed owner_key stamps."""
    from engine.char_index import iter_characters

    if game is None:
        return
    now = getattr(game, "game_time_ticks", 0)
    for obj in iter_characters(game):
        until = getattr(obj, "lease_until_tick", None)
        if until is None:
            continue
        if now < until:
            continue
        home = getattr(obj, "home_room_key", None)
        guest_room = game.rooms.get(home) if home else None
        if guest_room is not None and is_hotel_guest_room(guest_room):
            _clear_bed_owners_for(game, home, obj.key)
            obj.home_room_key = None
        obj.lease_until_tick = None
    hooks_mod.lodging_rent_tick(game)
