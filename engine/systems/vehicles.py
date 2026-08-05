"""
vehicles.py -- generic boarded-vehicle framework for the RiftForge engine.

A vehicle parks in a world room and owns a private interior Room. Characters
board with enter/board; followers in the same park room may auto-board as
passengers. Park locations persist across restarts in ``vehicle_parking.json``
beside the save DB (``game.report_dir``).

Games register catalog loaders and optional validators via ``engine.hooks``
(see ``register_vehicle_catalog``, ``set_vehicle_park_spot_extra_gate``, …).
SUPERS layers Impala/Cadence/atlas cruise on top; this module deliberately
stays free of ``import supers``.

``drive_step`` moves a parked vehicle one hop through the room graph via
normal ``room.exits`` -- simpler than SUPERS' scenic America atlas cruise.
"""

from __future__ import annotations

import json
import os
import re

from engine import hooks as hooks_mod
from engine.content_store import save_json
from engine.world import Room

# Live park spots -- next to riftforge.db / bug_reports.log (not in git).
PARKING_FILENAME = "vehicle_parking.json"


def _require_keys(spec, keys, where):
    """Raise AssertionError when any key in ``keys`` is missing from ``spec``."""
    for key in keys:
        if key not in spec:
            raise AssertionError(f"{where}: missing required key {key!r}")


def _require_nonempty_str(spec, key, where):
    """Raise AssertionError unless ``spec[key]`` is a non-empty string."""
    val = spec.get(key)
    if not isinstance(val, str) or not val.strip():
        raise AssertionError(f"{where}: {key} must be a non-empty string")


def validate_vehicle_entry(vehicle_id, spec, *, where=None):
    """Fail loud if one vehicles catalog row is malformed (engine defaults)."""
    where = where or f"vehicles.json: '{vehicle_id}'"
    if not isinstance(vehicle_id, str) or not vehicle_id.strip():
        raise AssertionError(f"{where}: vehicle ids must be non-empty strings")
    if not isinstance(spec, dict):
        raise AssertionError(f"{where}: vehicle spec must be a dict")
    _require_keys(spec, ("key", "parked_room", "interior_key"), where)
    _require_nonempty_str(spec, "key", where)
    _require_nonempty_str(spec, "parked_room", where)
    _require_nonempty_str(spec, "interior_key", where)
    aliases = spec.get("aliases")
    if aliases is not None:
        if not isinstance(aliases, list) or not aliases:
            raise AssertionError(f"{where}: aliases must be a non-empty list")
        for i, alias in enumerate(aliases):
            if not isinstance(alias, str) or not alias.strip():
                raise AssertionError(
                    f"{where}: aliases[{i}] must be a non-empty string"
                )
    seats = spec.get("seats", 4)
    if isinstance(seats, bool) or not isinstance(seats, int) or seats < 1:
        raise AssertionError(f"{where}: seats must be a positive int")
    if "interior_description" in spec:
        _require_nonempty_str(spec, "interior_description", where)
    hooks_mod.vehicle_catalog_extra_validator(vehicle_id, spec, where=where)


def validate_vehicles_file(data, *, where="vehicles.json"):
    """Fail loud if a vehicles catalog envelope is malformed."""
    if not isinstance(data, dict) or not data:
        raise AssertionError(f"{where}: must be a non-empty dict")
    for vehicle_id, spec in data.items():
        if str(vehicle_id).startswith("_"):
            continue
        validate_vehicle_entry(vehicle_id, spec, where=f"{where}: '{vehicle_id}'")


def validate_travel_hub_entry(hub_id, hub, *, where=None):
    """Fail loud if one travel_hubs catalog row is malformed (engine defaults)."""
    where = where or f"travel_hubs.json: '{hub_id}'"
    if not isinstance(hub_id, str) or not hub_id.strip():
        raise AssertionError(f"{where}: hub ids must be non-empty strings")
    if not isinstance(hub, dict):
        raise AssertionError(f"{where}: hub spec must be a dict")
    _require_keys(hub, ("zone", "aliases"), where)
    _require_nonempty_str(hub, "zone", where)
    if not hub.get("park_room") and not hub.get("gateway_room"):
        raise AssertionError(
            f"{where}: park_room or gateway_room is required"
        )
    aliases = hub.get("aliases")
    if not isinstance(aliases, list) or not aliases:
        raise AssertionError(f"{where}: aliases must be a non-empty list")
    for i, alias in enumerate(aliases):
        if not isinstance(alias, str) or not alias.strip():
            raise AssertionError(
                f"{where}: aliases[{i}] must be a non-empty string"
            )
    macro = hub.get("macro")
    if macro is not None:
        if not isinstance(macro, (list, tuple)) or len(macro) != 2:
            raise AssertionError(f"{where}: macro must be [x, y]")
        for i, coord in enumerate(macro):
            if isinstance(coord, bool) or not isinstance(coord, int):
                raise AssertionError(
                    f"{where}: macro[{i}] must be an int"
                )
    if "arrive_hint" in hub and hub["arrive_hint"] is not None:
        if not isinstance(hub["arrive_hint"], str):
            raise AssertionError(f"{where}: arrive_hint must be a string")
    if "label" in hub and hub["label"] is not None:
        _require_nonempty_str(hub, "label", where)


def validate_travel_hubs_file(data, *, where="travel_hubs.json"):
    """Fail loud if travel_hubs.json envelope is malformed."""
    if not isinstance(data, dict) or not data:
        raise AssertionError(f"{where}: must be a non-empty dict")
    for hub_id, hub in data.items():
        if str(hub_id).startswith("_"):
            continue
        validate_travel_hub_entry(hub_id, hub, where=f"{where}: '{hub_id}'")


def parking_path(game):
    """Absolute path to the parking state file for this Game."""
    directory = getattr(game, "report_dir", None) or "."
    return os.path.join(directory, PARKING_FILENAME)


def canonical_park_key(game, key):
    """Resolve a legacy/alias park string to the room's identity key."""
    if not key:
        return key
    from engine.room_vnum import lookup_room, internal_room_key
    room = lookup_room(game, key)
    if room is None:
        rooms = getattr(game, "rooms", None) or {}
        room = rooms.get(key)
    if room is not None:
        return internal_room_key(room) or getattr(room, "key", key)
    return key


def load_parking_state(game):
    """Return {vehicle_id: park_info} from disk, or {}."""
    path = parking_path(game)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for vid, entry in data.items():
        if not isinstance(vid, str):
            continue
        if isinstance(entry, str):
            out[vid] = {"parked_room": entry}
        elif isinstance(entry, dict):
            info = {}
            if isinstance(entry.get("parked_room"), str):
                info["parked_room"] = entry["parked_room"]
            macro = entry.get("macro")
            if isinstance(macro, (list, tuple)) and len(macro) == 2:
                try:
                    info["macro"] = [int(macro[0]), int(macro[1])]
                except (TypeError, ValueError):
                    pass
            micro = entry.get("micro")
            if micro is None and "micro" in entry:
                info["micro"] = None
            elif isinstance(micro, (list, tuple)) and len(micro) == 2:
                try:
                    info["micro"] = [int(micro[0]), int(micro[1])]
                except (TypeError, ValueError):
                    pass
            if info:
                out[vid] = info
    return out


def save_parking_state(game):
    """Atomically write every vehicle's park room and/or overland coords."""
    if not getattr(game, "vehicles", None):
        return
    if not getattr(game, "_parking_save_scrubbing", False):
        game._parking_save_scrubbing = True
        try:
            rooms = getattr(game, "rooms", None) or {}
            for veh in game.vehicles.values():
                if not isinstance(veh, dict):
                    continue
                park_key = veh.get("parked_room")
                interior_key = veh.get("interior_key")
                if not park_key:
                    continue
                nested = bool(
                    (interior_key and park_key == interior_key)
                    or (
                        isinstance(park_key, str)
                        and park_key.startswith("Inside ")
                    )
                )
                if not nested and park_key in rooms:
                    nested = is_vehicle_interior_room(rooms[park_key], game)
                if not nested and park_key in rooms:
                    park_room = rooms[park_key]
                    if not room_is_valid_park_spot(park_room, game):
                        nested = True
                if not nested:
                    continue
                owner = None
                owner_key = (veh.get("owner_key") or "").strip()
                if owner_key:
                    find = getattr(game, "find_character", None)
                    if callable(find):
                        owner = find(owner_key)
                dest = nearest_driveable_park_key(
                    game, rooms.get(park_key), character=owner,
                )
                if not dest:
                    dest = safe_park_room_key(
                        game,
                        owner,
                        avoid_keys={interior_key} if interior_key else None,
                    )
                if dest and dest != park_key:
                    veh["parked_room"] = dest
                    veh["macro_pos"] = None
                    veh["micro_pos"] = None
                    continue
                dest = safe_park_room_key(
                    game,
                    avoid_keys={interior_key} if interior_key else None,
                )
                if dest and dest != park_key:
                    veh["parked_room"] = dest
                    veh["macro_pos"] = None
                    veh["micro_pos"] = None
        finally:
            game._parking_save_scrubbing = False
    path = parking_path(game)
    try:
        prior = load_parking_state(game)
    except Exception:
        prior = {}
    if not isinstance(prior, dict):
        prior = {}
    payload = dict(prior)
    for vid, veh in game.vehicles.items():
        entry = {}
        room_key = veh.get("parked_room")
        interior_key = veh.get("interior_key")
        if (
            isinstance(room_key, str)
            and room_key
            and room_key != interior_key
            and not room_key.startswith("Inside ")
        ):
            entry["parked_room"] = room_key
        macro = veh.get("macro_pos")
        if isinstance(macro, (list, tuple)) and len(macro) == 2:
            entry["macro"] = [int(macro[0]), int(macro[1])]
        micro = veh.get("micro_pos")
        if micro is None and "macro" in entry:
            entry["micro"] = None
        elif isinstance(micro, (list, tuple)) and len(micro) == 2:
            entry["micro"] = [int(micro[0]), int(micro[1])]
        if entry:
            payload[vid] = entry
        elif vid in payload:
            del payload[vid]
    save_json(path, payload)


def ensure_vehicle_defaults(character):
    """Attach vehicle fields if missing (idempotent)."""
    if not hasattr(character, "in_vehicle"):
        character.in_vehicle = None
    if not hasattr(character, "vehicle_role"):
        character.vehicle_role = None


def ensure_catalog_vehicle_rooms_for_load(game):
    """Pre-load: catalog vehicle interiors stamped with stable VNUMs."""
    ensure_game_vehicles(game, pre_character_load=True)


def ensure_game_vehicles(game, *, pre_character_load=False):
    """Stamp catalog vehicles onto ``game`` and ensure interior rooms exist."""
    if getattr(game, "_vehicles_ready", False):
        return
    loader = hooks_mod.vehicle_catalog_loader()
    catalog = loader() if loader is not None else {}
    if catalog:
        validate_vehicles_file(catalog)
    hub_loader = hooks_mod.travel_hub_catalog_loader()
    hubs = hub_loader() if hub_loader is not None else {}
    if hubs:
        validate_travel_hubs_file(hubs)
    saved_parks = load_parking_state(game)
    game.travel_hubs = hubs
    game.vehicles = {}
    rooms = getattr(game, "rooms", {})
    for vid, spec in catalog.items():
        if str(vid).startswith("_"):
            continue
        interior_key = spec["interior_key"]
        if interior_key not in rooms:
            interior = Room(
                interior_key,
                spec.get(
                    "interior_description",
                    "The inside of a vehicle. Type 'leave' to climb out.",
                ),
            )
            interior.zone = None
            interior.wilderness = False
            interior.outdoor = False
            interior.area_type = "city"
            interior.is_vehicle_interior = True
            rooms[interior_key] = interior
        else:
            interior = rooms[interior_key]
            if getattr(interior, "map_missing_stub", False):
                occupants = list(interior.characters())
                fresh = Room(
                    interior_key,
                    spec.get(
                        "interior_description",
                        "The inside of a vehicle. Type 'leave' to climb out.",
                    ),
                )
                fresh.zone = None
                fresh.wilderness = False
                fresh.outdoor = False
                fresh.area_type = "city"
                fresh.is_vehicle_interior = True
                rooms[interior_key] = fresh
                for who in occupants:
                    try:
                        who.move_to(fresh)
                    except Exception:
                        who.location = fresh
                interior = fresh
                print(
                    f"[vehicles] replaced map_missing_stub cabin "
                    f"{interior_key!r}",
                    flush=True,
                )
        parked_key = spec["parked_room"]
        saved = saved_parks.get(vid) or {}
        if saved.get("parked_room") and saved["parked_room"] in rooms:
            parked_key = saved["parked_room"]
        parked_key = canonical_park_key(game, parked_key)
        macro_pos = None
        micro_pos = None
        if "macro" in saved:
            try:
                macro_pos = (int(saved["macro"][0]), int(saved["macro"][1]))
            except (TypeError, ValueError, IndexError, KeyError):
                macro_pos = None
        if "micro" in saved:
            raw_micro = saved.get("micro")
            if raw_micro is None:
                micro_pos = None
            else:
                try:
                    micro_pos = (int(raw_micro[0]), int(raw_micro[1]))
                except (TypeError, ValueError, IndexError):
                    micro_pos = None
        game.vehicles[vid] = {
            "id": vid,
            "key": spec.get("key", vid),
            "aliases": [a.lower() for a in spec.get("aliases", [])],
            "parked_room": parked_key,
            "macro_pos": macro_pos,
            "micro_pos": micro_pos,
            "interior_key": interior_key,
            "interior": interior,
            "seats": int(spec.get("seats", 4)),
            "driver": None,
            "drive_until": 0,
            "drive_dest": None,
            "drive_started": 0,
            "beat_index": 0,
            "scenic_path": None,
            "scenic_mode": False,
            "scenic_last_step": 0,
        }
    _stamp_catalog_vehicle_interior_vnums(game)
    game._vehicles_ready = True
    if not os.path.isfile(parking_path(game)):
        save_parking_state(game)


def _stamp_catalog_vehicle_interior_vnums(game):
    """Stamp VNUM identity on catalog vehicle interiors (pre-load safe)."""
    from engine import room_vnum as room_vnum_mod

    rooms = getattr(game, "rooms", None) or {}
    taken = room_vnum_mod.collect_taken_vnums(rooms.values())
    for veh in (getattr(game, "vehicles", None) or {}).values():
        if not isinstance(veh, dict):
            continue
        for slot in ("interior",):
            room = veh.get(slot)
            if room is None or not room_vnum_mod.hand_room_wants_vnum(room):
                continue
            vnum = room_vnum_mod.stamp_hand_room(game, room, taken=taken)
            veh[f"{slot}_key"] = room_vnum_mod.internal_room_key(room)
            existing = rooms.get(vnum)
            if (
                existing is not None
                and existing is not room
                and getattr(existing, "map_missing_stub", False)
            ):
                for who in list(existing.characters()):
                    try:
                        who.move_to(room)
                    except Exception:
                        who.location = room
                rooms.pop(vnum, None)


def vehicle_for_interior_room(game, room):
    """Return the live vehicle whose interior is ``room``, or None."""
    if room is None or game is None:
        return None
    ensure_game_vehicles(game)
    room_key = getattr(room, "key", None)
    for veh in (getattr(game, "vehicles", None) or {}).values():
        interior = veh.get("interior")
        if interior is room:
            return veh
        if room_key and veh.get("interior_key") == room_key:
            return veh
        if room_key and veh.get("cockpit_key") == room_key:
            return veh
        if room_key and veh.get("cargo_key") == room_key:
            return veh
    return None


def is_vehicle_interior_room(room, game=None):
    """True when ``room`` is (or looks like) a vehicle cabin."""
    if room is None:
        return False
    if getattr(room, "is_vehicle_interior", False):
        return True
    key = getattr(room, "key", None) or ""
    if isinstance(key, str) and key.startswith("Inside "):
        return True
    if game is not None and vehicle_for_interior_room(game, room) is not None:
        return True
    return False


def room_is_valid_park_spot(room, game=None, *, character=None):
    """True when a vehicle may list ``room`` as its curb (``parked_room``)."""
    if room is None:
        return False
    if is_vehicle_interior_room(room, game):
        return False
    if hooks_mod.vehicle_park_spot_blocked_extra(room, game, character):
        return False
    if getattr(room, "outdoor", False):
        return True
    if getattr(room, "vehicle_berth", False):
        return True
    return False


def nearest_driveable_park_key(game, room, *, character=None):
    """Best curb near ``room`` when the current park spot is invalid."""
    if room is None or game is None:
        return None
    rooms = getattr(game, "rooms", None) or {}
    exits = getattr(room, "exits", None) or {}
    outdoor = []
    indoor_berth = []
    for neighbor in exits.values():
        if neighbor is None:
            continue
        if not room_is_valid_park_spot(neighbor, game, character=character):
            continue
        key = getattr(neighbor, "key", None)
        if not key:
            continue
        if getattr(neighbor, "outdoor", False):
            outdoor.append(key)
        else:
            indoor_berth.append(key)
    if outdoor:
        return outdoor[0]
    if indoor_berth:
        return indoor_berth[0]
    zone = getattr(room, "zone", None)
    if zone:
        for key, cand in rooms.items():
            if getattr(cand, "zone", None) != zone:
                continue
            if room_is_valid_park_spot(cand, game, character=character):
                return key
    return None


def safe_park_room_key(game, character=None, *, preferred=None, avoid_keys=None):
    """Pick a real curb key -- never a vehicle cabin or blocked room."""
    rooms = getattr(game, "rooms", None) or {}
    avoid = set(avoid_keys or ())

    def _ok(key):
        if not key or key in avoid or key not in rooms:
            return False
        return room_is_valid_park_spot(
            rooms[key], game, character=character,
        )

    candidates = []
    if preferred:
        candidates.append(preferred)
    loc = getattr(character, "location", None) if character is not None else None
    if loc is not None:
        loc_key = getattr(loc, "key", None)
        if loc_key and not room_is_valid_park_spot(
            loc, game, character=character,
        ):
            near = nearest_driveable_park_key(
                game, loc, character=character,
            )
            if near:
                candidates.append(near)
        else:
            candidates.append(loc_key)
    start = getattr(game, "start_room", None)
    if start is not None:
        candidates.append(getattr(start, "key", None))
    starter_keys = hooks_mod.overland_starter_keys()
    if starter_keys:
        candidates.append(starter_keys[0])
    for key in candidates:
        if _ok(key):
            return key
    for key, room in rooms.items():
        if key in avoid:
            continue
        if room_is_valid_park_spot(room, game, character=character):
            return key
    return None


def _vehicle_query_matches(needle, aliases):
    """True when ``needle`` should board a vehicle with these aliases."""
    needle = _normalize_board_needle(needle)
    if not needle:
        return False
    cleaned = set()
    for a in aliases or []:
        low = _normalize_board_needle(a)
        if low:
            cleaned.add(low)
    if needle in cleaned:
        return True
    for a in cleaned:
        if needle in a or a.startswith(needle):
            return True
        n_words = needle.split()
        if len(n_words) > 1:
            a_words = set(a.split())
            if all(w in a_words for w in n_words):
                return True
    return False


def _normalize_board_needle(text):
    """Lowercase board query / alias without quotes."""
    low = re.sub(r"['\"]+", "", (text or "").strip().lower())
    return re.sub(r"\s+", " ", low).strip()


def _vehicle_parked_in_room(game, veh, room):
    """True when this vehicle is parked where ``room`` is."""
    from engine.systems import overland as overland_mod

    if overland_mod.is_virtual_room(room):
        macro = veh.get("macro_pos")
        micro = veh.get("micro_pos")
        if (
            isinstance(macro, (list, tuple))
            and isinstance(micro, (list, tuple))
            and tuple(macro) == tuple(room.overland_macro)
            and tuple(micro) == tuple(room.overland_micro)
        ):
            return True
        return False
    park_key = veh.get("parked_room")
    interior_key = veh.get("interior_key")
    if park_key and interior_key and park_key == interior_key:
        return False
    park = game.rooms.get(park_key)
    if park is None:
        return False
    interior = veh.get("interior")
    if interior is not None and park is interior:
        return False
    if is_vehicle_interior_room(park, game):
        return False
    return park is room


def find_vehicle_at(game, room, query, character=None):
    """Return vehicle dict parked in ``room`` matching ``query``, or None."""
    ensure_game_vehicles(game)
    if room is None:
        return None
    needle = _normalize_board_needle(query)
    if not needle:
        return None
    matches = []
    for veh in game.vehicles.values():
        if not _vehicle_parked_in_room(game, veh, room):
            continue
        aliases = set(veh.get("aliases") or []) | {
            (veh.get("key") or "").lower(),
            (veh.get("id") or "").lower(),
        }
        if _vehicle_query_matches(needle, aliases):
            matches.append(veh)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    owner_key = getattr(character, "key", None) if character is not None else None
    if owner_key:
        owned = [v for v in matches if v.get("owner_key") == owner_key]
        if len(owned) == 1:
            return owned[0]
        if owned:
            matches = owned
    return matches[0]


def parked_vehicles_in(game, room):
    """List vehicle dicts parked in ``room``."""
    ensure_game_vehicles(game)
    if room is None:
        return []
    return [
        v for v in game.vehicles.values()
        if _vehicle_parked_in_room(game, v, room)
    ]


def vehicle_by_id(game, vid):
    """Look up a live vehicle by catalog id."""
    ensure_game_vehicles(game)
    return game.vehicles.get(vid)


def vehicle_occupants(game, veh):
    """Characters aboard this vehicle (cabin + cockpit + cargo for charters)."""
    out = []
    seen = set()
    rooms = []
    interior = veh.get("interior") or game.rooms.get(veh.get("interior_key"))
    if interior is not None:
        rooms.append(interior)
    if veh.get("is_charter"):
        cockpit = veh.get("cockpit") or game.rooms.get(veh.get("cockpit_key"))
        cargo = veh.get("cargo") or game.rooms.get(veh.get("cargo_key"))
        if cockpit is not None:
            rooms.append(cockpit)
        if cargo is not None:
            rooms.append(cargo)
    for room in rooms:
        for who in room.characters():
            if who not in seen:
                seen.add(who)
                out.append(who)
    return out


def _send(character, text):
    """Send to a live Session if present (Echoes stay quiet)."""
    session = getattr(character, "session", None)
    if session is not None:
        session.send(text)


def _board_one(character, veh, role, game):
    """Move one character into the vehicle interior with a role."""
    ensure_vehicle_defaults(character)
    interior = veh["interior"]
    face = character.key
    here = character.location
    if here is not None:
        here.broadcast(
            f"{face} climbs into the {veh['key']}.",
            exclude=character,
        )
    character.move_to(interior)
    character.in_vehicle = veh["id"]
    character.vehicle_role = role
    if role == "driver":
        veh["driver"] = character
        _send(
            character,
            f"You slide into the driver's seat of the {veh['key']}. "
            "Type leave to climb out.",
        )
    else:
        _send(
            character,
            f"You take the passenger side of the {veh['key']}.",
        )
    interior.broadcast(
        f"{face} settles in ({role}).",
        exclude=character,
    )


def board_followers(driver, veh, game):
    """Auto-board same-room followers of ``driver`` as passengers."""
    park = None
    park_key = veh.get("parked_room") if isinstance(veh, dict) else None
    if park_key and game is not None:
        park = (getattr(game, "rooms", None) or {}).get(park_key)
    if park is None:
        park = driver.location
    elif getattr(driver, "in_vehicle", None) == veh.get("id"):
        pass
    elif driver.location is not None and driver.location is not park:
        park = driver.location
    if park is None:
        return
    seats = int(veh.get("seats", 4))
    occupied = len(vehicle_occupants(game, veh))

    def _try_board(passenger):
        nonlocal occupied
        if occupied >= seats:
            return False
        if passenger.location is not park:
            return False
        if getattr(passenger, "spirit", False):
            return False
        if getattr(passenger, "in_vehicle", None):
            return False
        _board_one(passenger, veh, "passenger", game)
        occupied += 1
        return True

    for follower in list(getattr(driver, "followers", None) or []):
        if occupied >= seats:
            _send(driver, "The vehicle is full -- some followers stay behind.")
            break
        _try_board(follower)

    for tailer in list(getattr(driver, "staff_tailers", None) or []):
        if occupied >= seats:
            break
        if getattr(tailer, "staff_tailing", None) is not driver:
            continue
        _try_board(tailer)


def try_board(character, args, game):
    """Attempt to board a parked vehicle. True if handled."""
    ensure_game_vehicles(game)
    ensure_vehicle_defaults(character)
    if getattr(character, "in_vehicle", None):
        _send(character, "You're already in a vehicle. Type 'leave' to get out.")
        return True
    room = character.location
    query = (args or "").strip()
    if not query:
        return False
    veh = find_vehicle_at(game, room, query, character=character)
    if veh is None:
        parked = parked_vehicles_in(game, room)
        if parked:
            aliases = set()
            for v in parked:
                aliases |= set(v.get("aliases") or []) | {
                    (v.get("key") or "").lower(),
                    (v.get("id") or "").lower(),
                }
            needle = _normalize_board_needle(query)
            if _vehicle_query_matches(needle, aliases):
                names = ", ".join(v.get("key") or v.get("id") for v in parked)
                _send(
                    character,
                    f"No vehicle named '{query}' here. Try: enter {names}",
                )
                return True
        return False
    occupants = vehicle_occupants(game, veh)
    if len(occupants) >= int(veh.get("seats", 4)):
        _send(character, f"The {veh['key']} is full.")
        return True
    role = "driver" if veh.get("driver") is None else "passenger"
    if role == "passenger":
        driver = veh.get("driver")
        if driver is None or getattr(driver, "in_vehicle", None) != veh["id"]:
            role = "driver"
            veh["driver"] = None
    _board_one(character, veh, role, game)
    board_followers(character, veh, game)
    return True


def leave_vehicle(character, game, *, pull_followers=True):
    """Exit the vehicle onto its park room (generic room-graph curb)."""
    ensure_game_vehicles(game)
    ensure_vehicle_defaults(character)
    vid = getattr(character, "in_vehicle", None)
    if not vid:
        return False
    veh = vehicle_by_id(game, vid)
    if veh is None:
        character.in_vehicle = None
        character.vehicle_role = None
        return False
    park_key = veh.get("parked_room")
    park = game.rooms.get(park_key) if park_key else None
    interior = veh.get("interior")
    if (
        park is None
        or park is interior
        or is_vehicle_interior_room(park, game)
        or (
            veh.get("parked_room")
            and veh.get("interior_key")
            and veh.get("parked_room") == veh.get("interior_key")
        )
    ):
        fallback = safe_park_room_key(
            game,
            character,
            avoid_keys={veh.get("interior_key")} if veh.get("interior_key") else None,
        )
        if fallback and fallback in (getattr(game, "rooms", None) or {}):
            veh["parked_room"] = fallback
            veh["macro_pos"] = None
            veh["micro_pos"] = None
            save_parking_state(game)
            park = game.rooms[fallback]
        else:
            _send(
                character,
                "The vehicle has nowhere safe to stop.",
            )
            return True
    if park is None:
        _send(character, "The vehicle has nowhere to stop.")
        return False
    role = character.vehicle_role
    face = character.key
    interior.broadcast(f"{face} climbs out.", exclude=character)
    character.move_to(park)
    character.in_vehicle = None
    character.vehicle_role = None
    if veh.get("driver") is character:
        veh["driver"] = None
    park.broadcast(f"{face} climbs out of the {veh['key']}.", exclude=character)
    _send(character, f"You leave the {veh['key']}.")
    save_parking_state(game)
    if pull_followers and role == "driver":
        for follower in list(getattr(character, "followers", None) or []):
            if getattr(follower, "in_vehicle", None) == vid:
                leave_vehicle(follower, game, pull_followers=False)
    return True


def drive_step(character, vehicle, direction, game) -> bool:
    """Move a boarded vehicle one room-graph hop via ``parked_room`` exits.

    Uses the vehicle anchor room's normal ``exits`` dict -- not SUPERS'
    America atlas / macro-micro cruise (that stays game-specific).
    Returns True when the vehicle relocated; False when blocked.
    """
    if character is None or vehicle is None or game is None:
        return False
    if getattr(character, "in_vehicle", None) != vehicle.get("id"):
        return False
    if getattr(character, "vehicle_role", None) != "driver":
        return False
    park_key = vehicle.get("parked_room")
    if not park_key:
        return False
    rooms = getattr(game, "rooms", None) or {}
    park = rooms.get(park_key)
    if park is None:
        return False
    direction = (direction or "").strip().lower()
    if not direction:
        return False
    exits = getattr(park, "exits", None) or {}
    dest_key = exits.get(direction)
    if not dest_key:
        return False
    dest = rooms.get(dest_key) if isinstance(dest_key, str) else dest_key
    if dest is None:
        return False
    if not room_is_valid_park_spot(dest, game, character=character):
        return False
    new_key = getattr(dest, "key", dest_key)
    vehicle["parked_room"] = new_key
    vehicle["macro_pos"] = None
    vehicle["micro_pos"] = None
    save_parking_state(game)
    label = direction
    for who in vehicle_occupants(game, vehicle):
        _send(who, f"The {vehicle.get('key', 'vehicle')} rolls {label}.")
    return True
