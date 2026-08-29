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

# America atlas scenic cruise pacing (SUPERS may override via hook).
SCENIC_STEP_EVERY = 2
DRIVE_TICKS = SCENIC_STEP_EVERY + 2

# Scenic tick hooks (SUPERS registers at bootstrap).
_vehicle_scenic_macro_step = None
_vehicle_tick_drive_extra = None
_vehicle_scenic_step_every = None
_vehicle_scenic_step_player_line = None
_vehicle_scenic_abort_line = None


def set_vehicle_scenic_macro_step(fn):
    """Register fn(game, veh, nx, ny, driver) -> (ok, status)."""
    global _vehicle_scenic_macro_step
    _vehicle_scenic_macro_step = fn


def set_vehicle_tick_drive_extra(fn):
    """Register fn(game, veh, nx, ny) after each scenic macro step."""
    global _vehicle_tick_drive_extra
    _vehicle_tick_drive_extra = fn


def set_vehicle_scenic_step_every(fn):
    """Register fn(game) -> int heartbeats between macro steps."""
    global _vehicle_scenic_step_every
    _vehicle_scenic_step_every = fn


def set_vehicle_scenic_step_player_line(fn):
    """Register fn(game, veh, nx, ny) -> str|None for macro step You-lines."""
    global _vehicle_scenic_step_player_line
    _vehicle_scenic_step_player_line = fn


def vehicle_scenic_step_player_line(game, veh, nx, ny):
    if _vehicle_scenic_step_player_line is not None:
        try:
            line = _vehicle_scenic_step_player_line(game, veh, nx, ny)
            if line:
                return line
        except Exception:
            pass
    return f"The road rolls on -- overland ({nx}, {ny})."


def set_vehicle_scenic_abort_line(fn):
    """Register fn(game, veh) -> str|None when a scenic path hits blocked terrain."""
    global _vehicle_scenic_abort_line
    _vehicle_scenic_abort_line = fn


def vehicle_scenic_abort_line(game, veh):
    if _vehicle_scenic_abort_line is not None:
        try:
            line = _vehicle_scenic_abort_line(game, veh)
            if line:
                return line
        except Exception:
            pass
    return None


# Motorcycle / charter hooks (SUPERS registers at bootstrap).
_vehicle_is_motorcycle = None
_vehicle_is_horse_ride = None
_vehicle_rider_on_curb = None
_vehicle_motorcycle_occupants = None
_vehicle_mount_one = None
_vehicle_hatch_blocked = None
_vehicle_leave_motorcycle = None
_vehicle_parked_without_rider = None


def set_vehicle_is_motorcycle(fn):
    global _vehicle_is_motorcycle
    _vehicle_is_motorcycle = fn


def vehicle_is_motorcycle(veh):
    if _vehicle_is_motorcycle is not None:
        return bool(_vehicle_is_motorcycle(veh))
    return False


def set_vehicle_is_horse_ride(fn):
    """Register fn(veh) -> bool for ephemeral horse rides (not motorcycles).

    Distinct from ``vehicle_is_motorcycle``: SUPERS wires that hook as
    open-top (bike *or* horse) for mount UX. Horses skip the scenic
    out-of-gas abort; motorcycles do not.
    """
    global _vehicle_is_horse_ride
    _vehicle_is_horse_ride = fn


def vehicle_is_horse_ride(veh):
    if _vehicle_is_horse_ride is not None:
        return bool(_vehicle_is_horse_ride(veh))
    return False


def set_vehicle_rider_on_curb(fn):
    global _vehicle_rider_on_curb
    _vehicle_rider_on_curb = fn


def vehicle_rider_on_curb(character, veh, game):
    if _vehicle_rider_on_curb is not None:
        return bool(_vehicle_rider_on_curb(character, veh, game))
    return False


def set_vehicle_motorcycle_occupants(fn):
    global _vehicle_motorcycle_occupants
    _vehicle_motorcycle_occupants = fn


def vehicle_motorcycle_occupants(game, veh):
    if _vehicle_motorcycle_occupants is not None:
        return _vehicle_motorcycle_occupants(game, veh)
    return []


def set_vehicle_mount_one(fn):
    global _vehicle_mount_one
    _vehicle_mount_one = fn


def vehicle_mount_one(character, veh, role, game):
    if _vehicle_mount_one is not None:
        _vehicle_mount_one(character, veh, role, game)
        return
    raise RuntimeError("motorcycle mount hook not registered")


def set_vehicle_hatch_blocked(fn):
    global _vehicle_hatch_blocked
    _vehicle_hatch_blocked = fn


def vehicle_hatch_blocked(character, game, veh):
    if _vehicle_hatch_blocked is not None:
        return bool(_vehicle_hatch_blocked(character, game, veh))
    return False


def set_vehicle_leave_motorcycle(fn):
    global _vehicle_leave_motorcycle
    _vehicle_leave_motorcycle = fn


def vehicle_leave_motorcycle(character, game, veh, **kwargs):
    if _vehicle_leave_motorcycle is not None:
        return _vehicle_leave_motorcycle(character, game, veh, **kwargs)
    return False


def set_vehicle_parked_without_rider(fn):
    global _vehicle_parked_without_rider
    _vehicle_parked_without_rider = fn


def vehicle_parked_without_rider(game, room, parked):
    if _vehicle_parked_without_rider is not None:
        return _vehicle_parked_without_rider(game, room, parked)
    return []


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


def park_keys_match(game, left, right):
    """True when two park strings refer to the same room (VNUM or legacy)."""
    if not left or not right:
        return left == right
    return canonical_park_key(game, left) == canonical_park_key(game, right)


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
            extra = entry.get("extra")
            if isinstance(extra, dict) and extra:
                info["extra"] = dict(extra)
            if info:
                out[vid] = info
    return out


def tick_scrub_invalid_vehicle_parks(game):
    """Skippable heartbeat: rehome nested/invalid parks off the drive path.

    ``save_parking_state`` used to walk every vehicle through ``lookup_room``
    (linear scan of ``game.rooms`` on a miss) on every street hop. Azure
    billed that to Cadence nest-helper ``north`` as ``dir_veh`` ~4s.
    """
    if game is None or not getattr(game, "vehicles", None):
        return
    if getattr(game, "_parking_save_scrubbing", False):
        return
    game._parking_save_scrubbing = True
    try:
        rooms = getattr(game, "rooms", None) or {}
        # Snapshot -- rehoming a nested park below can call back into
        # kit/park helpers that touch ``game.vehicles`` mid-scrub.
        for veh in list(game.vehicles.values()):
            if not isinstance(veh, dict):
                continue
            park_key = veh.get("parked_room")
            interior_key = veh.get("interior_key")
            if not park_key:
                continue
            from engine.room_vnum import lookup_room

            park_room = rooms.get(park_key)
            if park_room is None:
                park_room = lookup_room(game, park_key)
            if park_room is None:
                park_room = lookup_room(
                    game, canonical_park_key(game, park_key),
                )
            nested = bool(
                (interior_key and park_key == interior_key)
                or (
                    isinstance(park_key, str)
                    and park_key.startswith("Inside ")
                )
            )
            if not nested and park_room is not None:
                nested = is_vehicle_interior_room(park_room, game)
            if not nested and park_room is not None:
                if not room_is_valid_park_spot(park_room, game):
                    owner = None
                    owner_key = (veh.get("owner_key") or "").strip()
                    if owner_key:
                        find = getattr(game, "find_character", None)
                        if callable(find):
                            owner = find(owner_key)
                    if hooks_mod.vehicle_park_scrub_exempt(
                        game, veh, park_room, owner,
                    ):
                        continue
                    nested = True
            if not nested:
                continue
            owner = None
            owner_key = (veh.get("owner_key") or "").strip()
            if owner_key:
                find = getattr(game, "find_character", None)
                if callable(find):
                    owner = find(owner_key)
            if park_room is None:
                park_room = rooms.get(park_key)
            if park_room is None:
                park_room = lookup_room(game, park_key)
            rehome = hooks_mod.vehicle_invalid_park_rehome(
                game, veh, park_room, owner,
            )
            if rehome and rehome != park_key:
                rehome_room = lookup_room(game, rehome) or rooms.get(rehome)
                if rehome_room is not None:
                    veh["parked_room"] = rehome
                    veh["macro_pos"] = None
                    veh["micro_pos"] = None
                    continue
            dest = nearest_driveable_park_key(
                game, park_room or rooms.get(park_key), character=owner,
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


def save_parking_state(game):
    """Atomically write every vehicle's park room and/or overland coords.

    Invalid-park scrub lives on ``tick_scrub_invalid_vehicle_parks`` so a
    Cadence street hop does not walk the whole room table.
    """
    if not getattr(game, "vehicles", None):
        return
    path = parking_path(game)
    try:
        prior = load_parking_state(game)
    except Exception:
        prior = {}
    if not isinstance(prior, dict):
        prior = {}
    payload = dict(prior)
    # Snapshot before iterating -- a hook callback (trunk extra fields,
    # park-scrub exemptions) or a kit ensure still in flight elsewhere in
    # the same tick can add/remove a vehicle id while this walks the
    # roster, which raises "dictionary changed size during iteration" on
    # the live ``game.vehicles`` dict (bug report -- makecar crash).
    for vid, veh in list(game.vehicles.items()):
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
        extra = hooks_mod.vehicle_extra_persist_fields(veh)
        if extra:
            entry["extra"] = extra
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
        extra = saved.get("extra")
        if isinstance(extra, dict) and extra:
            hooks_mod.vehicle_extra_persist_apply(game.vehicles[vid], extra)
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


def vehicle_host_room(game, interior_room):
    """World room where a vehicle interior is parked or cruising.

    Vehicle cabins stamp ``area_type = "city"`` at creation so look/GMCP
    must resolve the live curb or America atlas cell instead of the cabin
    stub (bug report 646).
    """
    if interior_room is None or game is None:
        return None
    if not is_vehicle_interior_room(interior_room, game):
        return None
    veh = vehicle_for_interior_room(game, interior_room)
    if veh is None:
        return None
    rooms = getattr(game, "rooms", None) or {}
    from engine.systems import overland as overland_mod

    macro = overland_mod._parse_pos_pair(veh.get("macro_pos"))
    if macro is not None:
        key = overland_mod.america_cell_key(macro[0], macro[1])
        host = rooms.get(key)
        if host is not None:
            return host
    park_key = veh.get("parked_room")
    interior_key = veh.get("interior_key")
    if park_key and interior_key and park_key == interior_key:
        return None
    if not park_key:
        return None
    from engine.room_vnum import lookup_room

    canon = canonical_park_key(game, park_key)
    for candidate in (canon, park_key):
        if not candidate:
            continue
        host = lookup_room(game, candidate) or rooms.get(candidate)
        if host is not None and not is_vehicle_interior_room(host, game):
            return host
    return None


def look_area_source_room(game, room):
    """Room whose plane/area_type/zone should drive look badges."""
    if room is None:
        return room
    host = vehicle_host_room(game, room)
    return host if host is not None else room


def room_is_garage_berth(room):
    """True when an indoor room is an authored garage bay (not a living room).

    Homestead yard pads and remodeled bays may ship without ``vehicle_berth``
    on older saves; ``save_parking_state`` used to scrub those curbs onto the
    porch (bug report 674).
    """
    if room is None:
        return False
    if getattr(room, "homestead_garage_pad", False):
        return True
    rtype = (getattr(room, "remodel_type", None) or "").strip().lower()
    if rtype == "garage":
        return True
    label = (getattr(room, "remodel_inbound_exit", None) or "").strip().lower()
    if label == "garage":
        return True
    if getattr(room, "vehicle_berth", False):
        return True
    title = (
        getattr(room, "title", None) or getattr(room, "key", None) or ""
    ).lower()
    if getattr(room, "is_house", False) and "garage" in title:
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
    if getattr(room, "no_park", False):
        return False
    if getattr(room, "outdoor", False):
        return True
    if room_is_garage_berth(room):
        if not getattr(room, "vehicle_berth", False):
            room.vehicle_berth = True
        return True
    if getattr(room, "vehicle_berth", False):
        return True
    return False


def _rooms_items_snapshot(rooms):
    """Stable ``(key, room)`` pairs -- park gates may insert overland cells."""
    if not rooms:
        return []
    try:
        return list(rooms.items())
    except RuntimeError:
        return list(rooms.items())


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
        for key, cand in _rooms_items_snapshot(rooms):
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
    # Copyover used to walk every world room here for each body whose
    # location was a cabin (zone=None, so nearest_driveable_park_key
    # cannot zone-scan). Live billed that as ~15s ``_owned_vehicles``.
    # Reuse the first valid curb for this process.
    cached = getattr(game, "_vehicle_park_fallback_key", None)
    if cached and _ok(cached):
        return cached
    for key, room in _rooms_items_snapshot(rooms):
        if key in avoid:
            continue
        if room_is_valid_park_spot(room, game, character=character):
            if game is not None:
                game._vehicle_park_fallback_key = key
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
    canon = canonical_park_key(game, park_key)
    from engine.room_vnum import lookup_room
    park = lookup_room(game, canon) or game.rooms.get(canon) or game.rooms.get(park_key)
    if park is None:
        return False
    interior = veh.get("interior")
    if interior is not None and park is interior:
        return False
    if is_vehicle_interior_room(park, game):
        return False
    return park is room


# Generic board words must never pick a stranger's kit (bug report 850).
_GENERIC_BOARD_NEEDLES = frozenset({
    "car",
    "ride",
    "vehicle",
    "truck",
    "bike",
    "my car",
    "my ride",
})


def find_vehicle_at(game, room, query, character=None):
    """Return vehicle dict parked in ``room`` matching ``query``, or None."""
    ensure_game_vehicles(game)
    if room is None:
        return None
    needle = _normalize_board_needle(query)
    if not needle:
        return None
    matches = []
    # Snapshot -- ``ensure_game_vehicles`` / ``ensure_character_vehicles``
    # can stamp a brand-new vehicle into ``game.vehicles`` from a nested
    # call triggered while this scan is mid-roster.
    for veh in list(game.vehicles.values()):
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
    owner_key = getattr(character, "key", None) if character is not None else None

    def _is_owned(veh):
        if owner_key and veh.get("owner_key") == owner_key:
            return True
        vid = veh.get("id")
        if character is not None and vid and character_owns_vehicle_id(character, vid):
            return True
        return False

    if needle in _GENERIC_BOARD_NEEDLES:
        owned = [v for v in matches if _is_owned(v)]
        if not owned:
            return None
        return owned[0]
    if len(matches) == 1:
        return matches[0]
    if owner_key:
        owned = [v for v in matches if _is_owned(v)]
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


def character_owns_vehicle_id(character, vid):
    """True when ``vid`` is this character's owned kit (may not be stamped yet).

    Owned kits (`owned:Name:slot`) are stamped onto ``game.vehicles`` after
    load-time Phase C normalize. Clearing board flags while the id still
    matches the save soft-locks ``drive`` / ``leave`` (bug report 675).
    """
    if character is None or not vid:
        return False
    text = str(vid).strip()
    if not text:
        return False
    for kit in getattr(character, "owned_vehicles", None) or []:
        if not isinstance(kit, dict):
            continue
        kit_id = (kit.get("id") or "").strip()
        if kit_id == text:
            return True
    if text.startswith("owned:"):
        parts = text.split(":")
        if len(parts) >= 2 and parts[1] == getattr(character, "key", None):
            return True
    return False


def _legacy_owned_interior_key(kit, owner_key):
    """Pre-VNUM cabin key shape (must stay aligned with vehicle_kit helper)."""
    safe_name = re.sub(r"[^a-zA-Z0-9 ]+", "", (kit or {}).get("name") or "Car").strip()
    safe_owner = re.sub(r"[^a-zA-Z0-9_\-]+", "_", str(owner_key or "anon"))
    return f"Inside {safe_name} ({safe_owner})"


def _move_to_live_interior(character, veh, room):
    """Shift ``character`` onto ``veh``'s live cabin when ``room`` is stale."""
    live = veh.get("interior")
    if live is None:
        return veh
    if live is not room:
        character.move_to(live)
    return veh


def _character_in_vehicle_cabin(character, veh):
    """True when ``character`` stands in ``veh``'s kit interior room."""
    room = getattr(character, "location", None)
    if room is None or veh is None:
        return False
    interior = veh.get("interior")
    if interior is not None and room is interior:
        return True
    room_key = getattr(room, "key", None)
    interior_key = veh.get("interior_key")
    return bool(room_key and interior_key and room_key == interior_key)


def eject_motorcycle_from_cabin(character, game, veh):
    """Move a motorcycle rider off the kit cabin and onto the parked curb.

    Motorcycles mount on the curb, not inside their storage cabin. Bodies
    saved or healed into ``Inside …`` without a curb mount must land on
    ``parked_room`` so ``leave``, exits, and ``stuck`` work (bug report 846).
    """
    if character is None or game is None or veh is None:
        return False
    if not vehicle_is_motorcycle(veh):
        return False
    if not _character_in_vehicle_cabin(character, veh):
        return False
    park_key = veh.get("parked_room")
    if not park_key:
        return False
    rooms = getattr(game, "rooms", None) or {}
    park = rooms.get(park_key)
    if park is None:
        from engine.room_vnum import lookup_room

        park = lookup_room(game, park_key)
    if park is None or getattr(character, "location", None) is park:
        return False
    character.move_to(park)
    if getattr(character, "in_vehicle", None) == veh.get("id"):
        character.in_vehicle = None
        character.vehicle_role = None
        if veh.get("driver") is character:
            veh["driver"] = None
    return True


def _resolve_stale_owned_interior(character, game, room):
    """Return a live owned-kit vehicle when the body sits on a replaced cabin.

    ``ensure_character_vehicles`` can allocate a fresh interior VNUM while
    the character still references the previous Room object (reconnect /
    deferred login stamp). Move onto the live cabin before relinking board
    flags.

    Never grab the first owned ride when several exist -- that mis-assigned
    ``in_vehicle`` after reconnect (bug report 676). Prefer the saved
    ``in_vehicle`` kit when several owned rides share a cabin key (677).
    """
    if character is None or game is None or room is None:
        return None
    if not is_vehicle_interior_room(room, game):
        return None
    room_key = getattr(room, "key", None)
    kits = [
        k for k in (getattr(character, "owned_vehicles", None) or [])
        if isinstance(k, dict)
    ]
    owner_key = getattr(character, "key", None)
    preferred = (getattr(character, "in_vehicle", None) or "").strip()
    if preferred:
        kits.sort(
            key=lambda kit: 0
            if (kit.get("id") or "").strip() == preferred
            else 1
        )

    def _live_vehicle(kit):
        kit_id = (kit.get("id") or "").strip()
        if not kit_id:
            return None
        veh = vehicle_by_id(game, kit_id)
        if veh is None or veh.get("interior") is None:
            return None
        return veh

    # Pass 1: exact live interior / stamped interior_key match.
    for kit in kits:
        veh = _live_vehicle(kit)
        if veh is None:
            continue
        live = veh.get("interior")
        if live is room:
            return veh
        if room_key and veh.get("interior_key") == room_key:
            return _move_to_live_interior(character, veh, room)

    # Pass 2: persisted board id (675) beats stale-cabin guess.
    saved_vid = getattr(character, "in_vehicle", None)
    if saved_vid and character_owns_vehicle_id(character, saved_vid):
        veh = vehicle_by_id(game, saved_vid)
        if veh is not None and veh.get("interior") is not None:
            return _move_to_live_interior(character, veh, room)

    # Pass 3: legacy ``Inside Name (Owner)`` key before VNUM stamp.
    if room_key:
        for kit in kits:
            legacy = _legacy_owned_interior_key(kit, owner_key)
            if room_key != legacy:
                continue
            veh = _live_vehicle(kit)
            if veh is not None:
                return _move_to_live_interior(character, veh, room)

    # Pass 4: only one owned ride -- safe unambiguous fallback.
    live_vehs = [v for k in kits if (v := _live_vehicle(k)) is not None]
    if len(live_vehs) == 1:
        return _move_to_live_interior(character, live_vehs[0], room)

    return None


def heal_character_vehicle_board_state(character, game):
    """Re-link one body's ``in_vehicle`` / ``vehicle_role`` after load.

    Older saves wrote the interior ``room_key`` but omitted board flags;
    ``leave`` / ``drive`` soft-locked until healed. Persistence load calls
    this per character (boot lifecycle Phase C) instead of a full-world scan.
    Returns True when board fields or driver slot were touched.
    """
    if game is None or character is None:
        return False
    ensure_game_vehicles(game)
    ensure_vehicle_defaults(character)
    room = getattr(character, "location", None)
    # Owned stale cabins before generic interior lookup so a replaced VNUM
    # does not relink to another parked ride in game.vehicles (677).
    veh_here = _resolve_stale_owned_interior(character, game, room)
    if veh_here is None:
        veh_here = vehicle_for_interior_room(game, room)
    vid = getattr(character, "in_vehicle", None)

    # Saved owned board id outranks a stale-cabin mis-guess (bug 676).
    if (
        veh_here is not None
        and vid
        and character_owns_vehicle_id(character, vid)
        and veh_here.get("id") != vid
    ):
        saved_veh = vehicle_by_id(game, vid)
        if saved_veh is not None and saved_veh.get("interior") is not None:
            veh_here = _move_to_live_interior(character, saved_veh, room)

    if veh_here is not None:
        if vehicle_is_motorcycle(veh_here):
            if eject_motorcycle_from_cabin(character, game, veh_here):
                return True
        want_id = veh_here.get("id")
        touched = False
        live = veh_here.get("interior")
        if live is not None and live is not room:
            character.move_to(live)
            room = live
            touched = True
        if vid != want_id:
            character.in_vehicle = want_id
            touched = True
        role = getattr(character, "vehicle_role", None)
        if role not in ("driver", "passenger"):
            clear_stale_driver_slot(game, veh_here)
            live_driver = effective_driver(game, veh_here)
            if live_driver is not None and live_driver is not character:
                character.vehicle_role = "passenger"
            else:
                character.vehicle_role = "driver"
                veh_here["driver"] = character
            touched = True
        elif role == "driver":
            veh_here["driver"] = character
        return touched

    if not vid:
        return False
    veh = vehicle_by_id(game, vid)
    if veh is None:
        if character_owns_vehicle_id(character, vid):
            return False
        character.in_vehicle = None
        character.vehicle_role = None
        return True
    from engine.systems import overland as overland_mod

    if vehicle_is_motorcycle(veh):
        if eject_motorcycle_from_cabin(character, game, veh):
            return True
        interior = veh.get("interior")
        if room is interior:
            park_key = veh.get("parked_room")
            park = (
                (getattr(game, "rooms", None) or {}).get(park_key)
                if park_key
                else None
            )
            if park is not None:
                character.move_to(park)
                return True
            return False
        on_curb = vehicle_rider_on_curb(character, veh, game)
        on_road = overland_mod.is_virtual_room(room) and (
            getattr(character, "in_vehicle", None) == veh.get("id")
        )
        if on_curb or on_road:
            role = getattr(character, "vehicle_role", None)
            if role not in ("driver", "passenger"):
                clear_stale_driver_slot(game, veh)
                live_driver = effective_driver(game, veh)
                if live_driver is not None and live_driver is not character:
                    character.vehicle_role = "passenger"
                else:
                    character.vehicle_role = "driver"
                    veh["driver"] = character
                return True
            if role == "driver":
                veh["driver"] = character
            return False
    interior = veh.get("interior")
    if room is not interior:
        room_key = getattr(room, "key", None)
        interior_key = veh.get("interior_key")
        if (
            interior is not None
            and room_key
            and interior_key
            and room_key == interior_key
        ):
            character.move_to(interior)
            role = getattr(character, "vehicle_role", None)
            touched = True
            if role not in ("driver", "passenger"):
                clear_stale_driver_slot(game, veh)
                live_driver = effective_driver(game, veh)
                if live_driver is not None and live_driver is not character:
                    character.vehicle_role = "passenger"
                else:
                    character.vehicle_role = "driver"
                    veh["driver"] = character
            elif role == "driver":
                veh["driver"] = character
            return touched
        character.in_vehicle = None
        character.vehicle_role = None
        if veh.get("driver") is character:
            veh["driver"] = None
        return True
    return False


def heal_vehicle_board_state(game):
    """Full-world board heal (smoke / manual repair — not boot_seed).

    Prefer ``heal_character_vehicle_board_state`` on persistence load.
    """
    if game is None:
        return 0
    from engine.char_index import iter_characters

    healed = 0
    for char in list(iter_characters(game)):
        if heal_character_vehicle_board_state(char, game):
            healed += 1
    if healed:
        print(
            f"[vehicles] heal_vehicle_board_state: relinked/cleared {healed}",
            flush=True,
        )
    return healed


def stamp_logout_vehicle_anchor(character, game=None):
    """Remember room + board state when a live Session ends (bug report 683).

    Offline Cadence may board the Echo into an owned ride and drive away.
    On the next login, ``restore_logout_vehicle_anchor`` puts players who
    logged off **on foot** back where they stood instead of inside a car
    the Echo moved across town/plane.
    """
    if character is None:
        return
    from engine import room_vnum as room_vnum_mod

    room = getattr(character, "location", None)
    if room is not None:
        character.logout_room_key = room_vnum_mod.internal_room_key(room)
    else:
        character.logout_room_key = None
    character.logout_in_vehicle = getattr(character, "in_vehicle", None)


def restore_logout_vehicle_anchor(character, game):
    """Login: undo Echo vehicle drift when the player logged off on foot.

    Mirrors staff ``gm_spirit_room_key`` restore (disconnect pins intent;
    Cadence must not override it for vehicle boarding). Returns True when
    the body was ejected from a cabin and moved back to ``logout_room_key``.
    """
    if character is None or game is None:
        return False

    logout_room_key = getattr(character, "logout_room_key", None)
    logout_vid = getattr(character, "logout_in_vehicle", None)

    def _clear_stamp():
        character.logout_room_key = None
        character.logout_in_vehicle = None

    if not logout_room_key or logout_vid:
        _clear_stamp()
        return False

    ensure_game_vehicles(game)
    ensure_vehicle_defaults(character)
    room = getattr(character, "location", None)
    boarded = bool(getattr(character, "in_vehicle", None))
    in_cabin = boarded or is_vehicle_interior_room(room, game)
    if not in_cabin:
        _clear_stamp()
        return False

    if boarded:
        leave_vehicle(character, game, pull_followers=False)
    else:
        try_leave_vehicle(character, game, pull_followers=False)

    from engine import room_vnum as room_vnum_mod

    dest = room_vnum_mod.lookup_room(game, logout_room_key)
    if dest is None:
        dest = (getattr(game, "rooms", None) or {}).get(logout_room_key)
    if dest is not None and not is_vehicle_interior_room(dest, game):
        character.move_to(dest)
        _clear_stamp()
        return True

    _clear_stamp()
    return False


def effective_driver(game, veh):
    """Return the live driver aboard ``veh``, or None when the slot is empty/stale.

    ``veh["driver"]`` alone is not enough: logout/fold cycles can leave a
    dangling pointer whose ``in_vehicle`` flag still matches even though the
    body is not in the cabin or no longer holds the driver role (bug report
    620 — owner re-boarded as passenger behind a ghost driver slot).
    """
    if game is None or veh is None:
        return None
    driver = veh.get("driver")
    if driver is None:
        return None
    vid = veh.get("id")
    if not vid or getattr(driver, "in_vehicle", None) != vid:
        return None
    if getattr(driver, "vehicle_role", None) != "driver":
        return None
    occupants = vehicle_occupants(game, veh)
    if driver not in occupants:
        return None
    return driver


def clear_stale_driver_slot(game, veh):
    """Drop ``veh["driver"]`` when ``effective_driver`` says the slot is empty."""
    if veh is None:
        return False
    if effective_driver(game, veh) is not None:
        return False
    if veh.get("driver") is None:
        return False
    veh["driver"] = None
    return True


def vehicle_occupants(game, veh):
    """Characters aboard this vehicle (cabin + cockpit + cargo for charters)."""
    if vehicle_is_motorcycle(veh):
        return vehicle_motorcycle_occupants(game, veh)
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
    if vehicle_is_motorcycle(veh):
        vehicle_mount_one(character, veh, role, game)
        return
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
    clear_stale_driver_slot(game, veh)
    live_driver = effective_driver(game, veh)
    role = "driver" if live_driver is None else "passenger"
    _board_one(character, veh, role, game)
    board_followers(character, veh, game)
    return True


def eject_before_relocate(character, game):
    """Leave a boarded vehicle before cosmic teleport or plane hops.

    Vehicles do not follow characters across planes; clearing ``in_vehicle``
    prevents cabin/curb desync (bug report 438).
    """
    if character is None or game is None:
        return
    if not getattr(character, "in_vehicle", None):
        return
    leave_vehicle(character, game, pull_followers=False)


def try_leave_vehicle(character, game, *, pull_followers=True):
    """Leave when boarded; heal stale cabin / missing board flags first.

    Player ``leave`` and hub ``taxi`` call this so reconnect after deploy
    cannot soft-lock when the body is still in a cabin but ``in_vehicle``
    was cleared (bug reports 675 / 677).
    """
    ensure_game_vehicles(game)
    ensure_vehicle_defaults(character)
    if not getattr(character, "in_vehicle", None):
        room = getattr(character, "location", None)
        if is_vehicle_interior_room(room, game):
            heal_character_vehicle_board_state(character, game)
    if getattr(character, "in_vehicle", None):
        return leave_vehicle(character, game, pull_followers=pull_followers)
    room = getattr(character, "location", None)
    if is_vehicle_interior_room(room, game):
        veh = vehicle_for_interior_room(game, room)
        if veh is not None and eject_motorcycle_from_cabin(character, game, veh):
            label = (veh.get("key") or "ride").strip()
            _send(character, f"You step out of the {label}.")
            return True
    return False


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
    if vehicle_hatch_blocked(character, game, veh):
        _send(character, "The hatch is sealed for flight.")
        return False

    if vehicle_is_motorcycle(veh):
        if veh.get("drive_until") and game.game_time_ticks < veh["drive_until"]:
            clear_active_drive(veh)
        elif veh.get("scenic_path") or veh.get("scenic_mode"):
            clear_active_drive(veh)
        return vehicle_leave_motorcycle(
            character, game, veh, pull_followers=pull_followers,
        )
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


def clear_active_drive(veh):
    """Cancel in-progress tile cruise (and any legacy montage stamps)."""
    if veh is None:
        return
    veh["drive_until"] = 0
    veh["drive_dest"] = None
    veh["drive_started"] = 0
    veh["scenic_path"] = None
    veh["scenic_last_step"] = 0
    veh["scenic_mode"] = False


def _scenic_step_every(game):
    if _vehicle_scenic_step_every is not None:
        try:
            return int(_vehicle_scenic_step_every(game))
        except Exception:
            pass
    return SCENIC_STEP_EVERY


def _apply_scenic_macro_step(game, veh, nx, ny):
    """Move a boarded vehicle one America-macro tile (scenic tick)."""
    driver = None
    for who in vehicle_occupants(game, veh):
        if getattr(who, "vehicle_role", None) == "driver":
            driver = who
            break
    if driver is None:
        occupants = vehicle_occupants(game, veh)
        driver = occupants[0] if occupants else None
    if _vehicle_scenic_macro_step is not None:
        ok, status = _vehicle_scenic_macro_step(game, veh, nx, ny, driver)
        if not ok:
            return False, status or "blocked"
        if status == "stranded":
            return True, "stranded"
        return True, status
    # Bare engine: stamp macro on vehicle + occupants.
    from engine.systems import overland as overland_mod

    veh["macro_pos"] = (nx, ny)
    veh["micro_pos"] = None
    veh["parked_room"] = None
    for who in vehicle_occupants(game, veh):
        overland_mod.ensure_overland_defaults(who)
        who.macro_pos = (nx, ny)
        who.micro_pos = None
    return True, None


def tick_drives(game, *, finish_drive_fn=None):
    """Advance America tile-cruise macro steps once per heartbeat.

    ``finish_drive_fn(game, veh)`` is called when a scenic path completes;
    games pass their arrival prose handler (SUPERS ``_finish_drive``).
    """
    if not getattr(game, "_vehicles_ready", False):
        return
    now = game.game_time_ticks
    # Snapshot -- ``finish_drive_fn`` arrival prose (road encounters,
    # impound/tow, carvana sale) can add/remove a vehicle mid-heartbeat.
    for veh in list(game.vehicles.values()):
        path = veh.get("scenic_path")
        if not (veh.get("scenic_mode") and isinstance(path, list)):
            if veh.get("drive_until"):
                veh["drive_until"] = 0
                veh["drive_dest"] = None
            continue
        last = int(veh.get("scenic_last_step") or 0)
        step_every = int(veh.get("scenic_step_every") or 0) or _scenic_step_every(game)
        if now - last < step_every:
            continue
        if not path:
            veh["scenic_mode"] = False
            veh["scenic_path"] = None
            if finish_drive_fn is not None:
                finish_drive_fn(game, veh)
            continue
        step = path.pop(0)
        try:
            nx, ny = int(step[0]), int(step[1])
        except (TypeError, ValueError, IndexError):
            clear_active_drive(veh)
            continue
        ok, status = _apply_scenic_macro_step(game, veh, nx, ny)
        veh["scenic_last_step"] = now
        if not ok:
            clear_active_drive(veh)
            abort = vehicle_scenic_abort_line(game, veh)
            if abort is None:
                abort = (
                    "The road trip aborts -- road blocked. "
                    "You stay aboard where you are."
                )
            for who in vehicle_occupants(game, veh):
                _send(who, abort)
            continue
        line = vehicle_scenic_step_player_line(game, veh, nx, ny)
        for who in vehicle_occupants(game, veh):
            _send(who, line)
        if _vehicle_tick_drive_extra is not None:
            _vehicle_tick_drive_extra(game, veh, nx, ny)
        if status == "wrecked":
            clear_active_drive(veh)
            continue
        if status == "stranded":
            if vehicle_is_horse_ride(veh):
                continue
            clear_active_drive(veh)
            for who in vehicle_occupants(game, veh):
                _send(who, "The engine dies -- out of gas.")
            continue
        if not path:
            veh["scenic_mode"] = False
            veh["scenic_path"] = None
            if finish_drive_fn is not None:
                finish_drive_fn(game, veh)


# ``config vehicles compact on`` summarizes ordinary look when more than
# two unoccupied rides are parked here (three or more).
_COMPACT_VEHICLES_THRESHOLD = 2


def _board_alias_for(veh):
    """Short board token for look hints."""
    aliases = veh.get("aliases") or []
    for pref in ("car", "ride", "truck", "bike"):
        if pref in aliases:
            return pref
    key = (veh.get("key") or veh.get("id") or "vehicle").lower()
    return key.split()[0] if key else "vehicle"


def _parked_for_look(room, game):
    """Unoccupied vehicles parked in ``room`` (hook may filter riders)."""
    ensure_game_vehicles(game)
    if room is None:
        return []
    return vehicle_parked_without_rider(
        game, room, parked_vehicles_in(game, room),
    )


def _vehicle_look_screenreader(character):
    if character is None:
        return False
    return bool(getattr(character, "screenreader", False))


def _wants_compact_vehicle_look(character, game):
    if character is None:
        return False
    from engine import display_prefs
    prefs = display_prefs.preference_character(character, game)
    display_prefs.ensure_display_defaults(prefs)
    return display_prefs.wants_compact_vehicles(prefs)


def look_vehicle_detail_lines(room, game, character=None):
    """Full parked-vehicle rows for ``look vehicles``."""
    parked = _parked_for_look(room, game)
    if not parked:
        return []
    sr = _vehicle_look_screenreader(character)
    lines = []
    for veh in parked:
        name = veh.get("key") or veh.get("id") or "?"
        alias = _board_alias_for(veh)
        verb = "mount" if vehicle_is_motorcycle(veh) else "board"
        if sr:
            text = f"{name}. {verb.capitalize()}: enter {alias}."
        else:
            text = f"{name} -- {verb}: enter {alias}"
        lines.append(text)
    return lines


def look_vehicle_hint(room, game, character=None):
    """Extra look line when vehicles are parked in this room."""
    parked = _parked_for_look(room, game)
    if not parked:
        return None
    sr = _vehicle_look_screenreader(character)
    if (
        _wants_compact_vehicle_look(character, game)
        and len(parked) > _COMPACT_VEHICLES_THRESHOLD
    ):
        if sr:
            return (
                f"Vehicles: quite a few parked here ({len(parked)}). "
                "Look vehicles for the list."
            )
        return (
            "There are quite a few vehicles here. "
            "Look vehicles for the full list."
        )
    names = [v.get("key") or v.get("id") or "?" for v in parked]
    if len(parked) == 1:
        alias = _board_alias_for(parked[0])
        verb = "mount" if vehicle_is_motorcycle(parked[0]) else "board"
        if sr:
            return f"Vehicle: {names[0]}. {verb.capitalize()}: enter {alias}."
        return f"Parked: {names[0]}. {verb.capitalize()}: enter {alias}."
    listed = "; ".join(names) if sr else ", ".join(names)
    tip = "Board: enter <name>, or enter car for yours."
    if sr:
        return f"Vehicles ({len(parked)}): {listed}. {tip}"
    return f"Parked: {listed}. {tip}"
