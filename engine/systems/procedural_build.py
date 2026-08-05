"""
procedural_build.py -- generic GM procedural room builders (engine).

Generic street-home shell builders, neighborhood hub prep, floor
corridor parsers, and ``populate homes`` / ``populate neighborhood``
orchestration. Game-specific unit prose registers via ``engine.hooks``
(SUPERS ``populate.py`` facade).
"""

from __future__ import annotations

import random
import re

from engine import hooks
from engine import map_store
from engine.command_support import DIRECTIONS
from engine.room_naming import split_structured_title, street_hub_leaf


# Look-name pattern for apartment corridors: "Apartment Floor B" or
# structured "Lebanon - Apartments - Floor B".
# ``apartments?`` tolerates the plural so zone titles that prefix the
# building name still parse the floor letter.
_APARTMENT_FLOOR_RE = re.compile(
    r"apartments?\s*(?:-|)\s*floor\s+([A-Za-z])\b",
    re.IGNORECASE,
)

# Look-name pattern for hotel corridors: "Hotel Floor 1" or
# "Lebanon - Hotel - Floor 2".
_HOTEL_FLOOR_RE = re.compile(
    r"hotel\s*(?:-|)\s*floor\s+(\d+)\b",
    re.IGNORECASE,
)

# Look-name pattern for motel corridors: "Motel Floor 1",
# "Lebanon - Motel - Floor 2", or named motels like
# "Lebanon - Sinner's Motel - Floor B1" (basement letter+digit floors).
_MOTEL_FLOOR_RE = re.compile(
    r"motel\s*(?:-|)\s*floor\s+([A-Za-z]?\d+)\b",
    re.IGNORECASE,
)

# Look-name pattern for hospital corridors: "Hospital Floor 2",
# "Lebanon Hospital Floor 2", or "Lebanon - Hospital - Floor 2".
_HOSPITAL_FLOOR_RE = re.compile(
    r"hospital\s*(?:-|)\s*floor\s+(\d+)\b",
    re.IGNORECASE,
)


def _parse_floor_token(room, pattern):
    """Return regex group(1) from look name, then key, or None."""
    name = _room_look_name(room)
    match = pattern.search(name)
    if match:
        return match.group(1)
    key = str(getattr(room, "key", "") or "")
    match = pattern.search(key)
    if match:
        return match.group(1)
    return None


def _normalize_exit_direction(token):
    """Canonical exit direction from a dig/link token, or None."""
    raw = (token or "").strip().lower()
    if not raw:
        return None
    if raw in ("in", "out", "leave"):
        return raw
    return DIRECTIONS.get(raw)


# Guest-room letters stamped by ``populate hotels`` / ``populate motels``
# / ``populate hospitals``.
_HOTEL_ROOM_LETTERS = ("A", "B", "C", "D", "E", "F")


# Address-line suffixes a street hub title may end with (look name).
# Used by populate homes / all homes / neighborhood.
ADDRESS_SUFFIXES = (
    "Street", "Boulevard", "Blvd", "Avenue", "Ave",
    "Lane", "Drive", "Road", "Circle", "Court",
    "Place", "Way", "Terrace", "Trail", "Parkway",
    "Row", "Alley", "Loop", "Crescent", "Close",
    "Grove", "Heights", "Hill", "Hollow", "Landing",
    "Pass", "Path", "Pike", "Run", "Square",
    "Trace", "View", "Walk", "Crossing", "Commons",
)

# Studio layout: house porches leave the street canvas. Street z <= 0 →
# houses at z-5; street z >= 1 → houses at z+5 (never share the street layer).
HOUSE_LAYOUT_LAYER_DELTA = 5


def house_layout_layer(street_z):
    """Studio ``layout.z`` for house porches off a street at ``street_z``."""
    z = int(street_z)
    if z >= 1:
        return z + HOUSE_LAYOUT_LAYER_DELTA
    return z - HOUSE_LAYOUT_LAYER_DELTA

# Longer suffixes first so "Parkway" wins over "Way", "Boulevard" over none.
_SUFFIX_RE = re.compile(
    r"^(?P<head>.+?)\s+(?P<suffix>"
    + "|".join(
        re.escape(s) for s in sorted(ADDRESS_SUFFIXES, key=len, reverse=True)
    )
    + r")$",
    re.IGNORECASE,
)


# Branch room kinds for generic homes (central living is always present).
# "den" is the second living-room flavor from the design pool.
_HOME_BRANCH_KINDS = ("bedroom", "bathroom", "kitchen", "den")

# Room-key sub-labels for street homes (key shape: "{Street} {addr} {Sub}").
_HOME_SUB_LABELS = (
    "Guest Bedroom", "Backyard", "Porch", "Living", "Bedroom",
    "Kitchen", "Bathroom", "Den", "Office",
)
_HOME_KEY_RE = re.compile(
    r"^(?P<street>.+?)\s+(?P<addr>\d+)\s+(?P<sub>"
    + "|".join(re.escape(s) for s in _HOME_SUB_LABELS)
    + r")$",
    re.IGNORECASE,
)

# Cardinal exits used for living → branch rooms (pick three of four).
_BRANCH_DIRS = ("north", "east", "south", "west")

# First-batch address range (odd numbers only, step 2).
_ADDRESS_MIN = 12001
_ADDRESS_MAX = 12999


def _street_layout_xyz(game, street_room):
    """Return (x, y, z) for the street hub from map JSON, or (0, 0, 0)."""
    try:
        path, _kind, _filename, _map_id = map_store.resolve_map_path(
            game, street_room,
        )
        doc = map_store.load_doc(path)
        entry = map_store.find_room_entry(doc, street_room.key) or {}
        layout = entry.get("layout")
        if isinstance(layout, dict) and "x" in layout and "y" in layout:
            return (
                int(layout["x"]),
                int(layout["y"]),
                int(layout.get("z", 0) or 0),
            )
    except Exception:
        pass
    return 0, 0, 0


def _find_free_layout_near(occupied, origin_x, origin_y, max_radius=64):
    """Nearest empty (x, y) to origin (spiral); occupied is a set of pairs."""
    if (origin_x, origin_y) not in occupied:
        return origin_x, origin_y
    for radius in range(1, max_radius + 1):
        for dx in range(-radius, radius + 1):
            for dy in (-radius, radius):
                cand = (origin_x + dx, origin_y + dy)
                if cand not in occupied:
                    return cand
        for dy in range(-radius + 1, radius):
            for dx in (-radius, radius):
                cand = (origin_x + dx, origin_y + dy)
                if cand not in occupied:
                    return cand
    return origin_x + max_radius + 1, origin_y


# Same convention as map_store.py / tools/retrofit_zone_layout.py /
# maps._LAYOUT_XY_DELTA -- kept as a local copy per this codebase's existing
# pattern rather than a shared import (each caller owns its own small table).
_HOME_LAYOUT_DELTA = {
    "north": (0, 1), "south": (0, -1),
    "east": (1, 0), "west": (-1, 0),
    "northeast": (1, 1), "northwest": (-1, 1),
    "southeast": (1, -1), "southwest": (-1, -1),
}
_HOME_LAYER_UP = frozenset({"up", "in"})
_HOME_LAYER_DOWN = frozenset({"down", "out"})


def _stamp_home_layouts(game, street_room, new_rooms):
    """Assign Studio layout on every new home room (wall_floor_breach_mechanic.md
    Phase A -- no more silent layout-less interiors).

    Porch gets an explicit cell on ``house_layout_layer(street_z)``. Every
    other room in the shell (Living, branches, backyard, upstairs bedroom,
    office, ...) is then stamped by walking the batch's own ``exits`` graph
    outward from the porch -- ``in``/``up`` move z+1 (matches
    tools/retrofit_zone_layout.py's LAYER_UP), compass directions move x/y,
    exactly like the retrofit crawler does for legacy zones. This needs no
    per-room-kind special-casing: whatever shape a future home layout adds,
    as long as it links back through ``exits``, it gets a cell for free.
    """
    if not new_rooms:
        return
    sx, sy, sz = _street_layout_xyz(game, street_room)
    house_z = house_layout_layer(sz)
    occupied = set()
    try:
        path, _k, _f, _m = map_store.resolve_map_path(game, street_room)
        doc = map_store.load_doc(path)
        for entry in doc.get("rooms") or []:
            layout = entry.get("layout")
            if not isinstance(layout, dict):
                continue
            try:
                if int(layout.get("z", 0) or 0) != house_z:
                    continue
                occupied.add((int(layout["x"]), int(layout["y"])))
            except (TypeError, ValueError, KeyError):
                continue
    except Exception:
        pass

    by_key = {e.get("key"): e for e in new_rooms if e.get("key")}
    frontier = []
    for entry in new_rooms:
        key = entry.get("key") or ""
        if not key.endswith(" Porch"):
            continue
        if not isinstance(entry.get("layout"), dict):
            px, py = _find_free_layout_near(occupied, sx, sy)
            occupied.add((px, py))
            entry["layout"] = {"x": px, "y": py, "z": house_z}
        frontier.append(entry)

    # BFS the rest of this house's shell outward from its porch(es), one
    # batch may contain several houses (populate homes N) so each porch
    # seeds its own walk.
    visited_keys = {e.get("key") for e in frontier}
    while frontier:
        current = frontier.pop(0)
        layout = current.get("layout")
        if not isinstance(layout, dict):
            continue
        try:
            cx, cy, cz = (
                int(layout["x"]), int(layout["y"]), int(layout.get("z", 0) or 0),
            )
        except (TypeError, ValueError, KeyError):
            continue
        for direction, dest_key in (current.get("exits") or {}).items():
            dest = by_key.get(dest_key)
            if dest is None or dest_key in visited_keys:
                continue
            d = str(direction or "").strip().lower()
            if d in _HOME_LAYOUT_DELTA:
                dx, dy = _HOME_LAYOUT_DELTA[d]
                dest["layout"] = {"x": cx + dx, "y": cy + dy, "z": cz}
            elif d in _HOME_LAYER_UP:
                dest["layout"] = {"x": cx, "y": cy, "z": cz + 1}
            elif d in _HOME_LAYER_DOWN:
                dest["layout"] = {"x": cx, "y": cy, "z": cz - 1}
            else:
                # Unknown/address-style exit -- skip, same as
                # retrofit_zone_layout.md Q7 (digit/nest exits).
                continue
            visited_keys.add(dest_key)
            frontier.append(dest)


def _room_look_name(room):
    """Player-facing room name (title preferred over storage key)."""
    if room is None:
        return ""
    if hasattr(room, "look_title"):
        return (room.look_title() or "").strip()
    title = getattr(room, "title", None)
    if title:
        return str(title).strip()
    return str(getattr(room, "key", "") or "").strip()


def _city_label_for_room(room):
    """Town / city label for structured ROOM NAMEs (via hook)."""
    return hooks.populate_city_label(room)


def _structured_room_title(room, main, sub=None):
    """Build City - Main [- Sub] using the standing room's map city."""
    city = _city_label_for_room(room)
    return hooks.populate_room_title(city, main, sub)


def parse_apartment_floor_letter(room):
    """Return the floor letter from an Apartment Floor look name, or None."""
    token = _parse_floor_token(room, _APARTMENT_FLOOR_RE)
    return token.upper() if token else None


def parse_street_name(room):
    """Return the street look name if it ends with an address suffix, else None.

    Homes expect a look title like ``Ferguson Street``, ``Stevenson Lane``,
    or structured ``Lebanon - Campbell Pass`` (see ``ADDRESS_SUFFIXES``).
    Bare plaza names without a suffix refuse.
    """
    
    name = street_hub_leaf(_room_look_name(room)) or _room_look_name(room)
    if not name:
        return None
    match = _SUFFIX_RE.match(name)
    if not match:
        return None
    # Canonicalize spacing / suffix capitalization from the catalog spelling.
    head = match.group("head").strip()
    raw_suffix = match.group("suffix")
    suffix = next(
        (s for s in ADDRESS_SUFFIXES if s.lower() == raw_suffix.lower()),
        raw_suffix.title(),
    )
    return f"{head} {suffix}"


def is_street_hub(room):
    """True when the room is a populate-homes street hub (not a porch/unit).

    Porch titles look like ``12305 Ferguson Street`` or structured
    ``Lebanon - 12305 Ferguson Street - Porch`` and would otherwise match
    the address-suffix pattern -- exclude private_home / is_house /
    leading house numbers / room sub-labels.
    """
    if room is None:
        return False
    if getattr(room, "is_house", False) or getattr(room, "private_home", False):
        return False
    key = str(getattr(room, "key", "") or "")
    if re.search(
        r"\s(Porch|Living|Bedroom|Kitchen|Bathroom|Den|Backyard|Office|"
        r"Guest Bedroom)$",
        key,
        re.IGNORECASE,
    ):
        return False
    name = _room_look_name(room)
    if re.match(r"^\d+\s+", name):
        return False
    # Structured porch / interior: City - 12215 Street - Living
    if re.search(
        r"\s-\s+\d+\s+.+\s-\s+"
        r"(Porch|Living|Bedroom|Kitchen|Bathroom|Den|Backyard|Office)\b",
        name,
        re.IGNORECASE,
    ):
        return False
    return parse_street_name(room) is not None


def is_apartment_floor(room):
    """True when the room is an Apartment Floor corridor."""
    return parse_apartment_floor_letter(room) is not None


def parse_hotel_floor_number(room):
    """Parse floor token from a corridor room."""
    return _parse_floor_token(room, _HOTEL_FLOOR_RE)

def is_hotel_floor(room):
    """True when the room is a Hotel Floor corridor."""
    return parse_hotel_floor_number(room) is not None


def _motel_floor_sort_key(floor_num):
    """Stable sort key for motel floor labels (1, 2, B1, B2, …)."""
    text = str(floor_num or "")
    if text.isdigit():
        return (0, int(text), "")
    match = re.match(r"^([A-Za-z]+)(\d+)$", text)
    if match:
        return (1, int(match.group(2)), match.group(1).upper())
    return (2, 0, text.lower())


def parse_motel_floor_number(room):
    """Parse floor token from a corridor room."""
    return _parse_floor_token(room, _MOTEL_FLOOR_RE)

def is_motel_floor(room):
    """True when the room is a Motel Floor corridor."""
    return parse_motel_floor_number(room) is not None


def parse_hospital_floor_number(room):
    """Parse floor token from a corridor room."""
    return _parse_floor_token(room, _HOSPITAL_FLOOR_RE)

def is_hospital_floor(room):
    """True when the room is a Hospital Floor corridor."""
    return parse_hospital_floor_number(room) is not None


def rooms_on_same_map(game, anchor_room):
    """Yield live rooms that share ``anchor_room.map_id`` (Lebanon-only, etc.).

    Populate-all scopes to the map file, not the zone tag string.
    """
    map_id = getattr(anchor_room, "map_id", None)
    if not map_id:
        return
    for room in game.rooms.values():
        if getattr(room, "map_id", None) == map_id:
            yield room


def _used_street_titles_on_map(game, anchor_room):
    """Lowercased street hub look names already present on this map."""
    used = set()
    for room in rooms_on_same_map(game, anchor_room):
        if not is_street_hub(room):
            continue
        name = parse_street_name(room)
        if name:
            used.add(name.lower())
    return used


def pick_neighborhood_title(game, anchor_room, *, rng=None):
    """Pick an unused ``{Name} {Suffix}`` title for this map."""
    rng = rng or random
    used = _used_street_titles_on_map(game, anchor_room)
    # Shuffle candidates so repeats across maps stay varied.
    names = list(hooks.populate_neighborhood_names())
    suffixes = list(ADDRESS_SUFFIXES)
    rng.shuffle(names)
    rng.shuffle(suffixes)
    for name in names:
        for suffix in suffixes:
            title = f"{name} {suffix}"
            if title.lower() not in used:
                return title
    # Exhausted unique pairs -- allow a numbered fallback.
    for n in range(2, 100):
        title = f"{rng.choice(names)} {rng.choice(suffixes)} {n}"
        if title.lower() not in used:
            return title
    return f"New {rng.choice(suffixes)}"




def _existing_street_addresses(game, street_room, street_name):
    """Collect integer address numbers already on this street.

    Sources:
      - Street exits whose direction is pure digits (``12305``)
      - Live rooms whose title starts with ``{N} {street_name}``
      - Live keys shaped ``{street_name} {N} Porch``
    """
    found = set()
    for direction in (street_room.exits or {}):
        text = str(direction).strip()
        if text.isdigit():
            found.add(int(text))
    title_re = re.compile(
        rf"^(\d+)\s+{re.escape(street_name)}\b",
        re.IGNORECASE,
    )
    key_re = re.compile(
        rf"^{re.escape(street_name)}\s+(\d+)\s+Porch$",
        re.IGNORECASE,
    )
    for room in game.rooms.values():
        title = _room_look_name(room)
        match = title_re.match(title)
        if match:
            found.add(int(match.group(1)))
            continue
        key = str(getattr(room, "key", "") or "")
        match = key_re.match(key)
        if match:
            found.add(int(match.group(1)))
    return sorted(found)


def pick_home_addresses(existing, count, *, rng=None):
    """Return ``count`` new odd-ish address numbers stepped by 2.

    - No existing: random odd base in [_ADDRESS_MIN, _ADDRESS_MAX], then
      +2 for each additional house in this batch.
    - Existing: randomly extend from the current max (+2…) or min (-2…),
      so a second populate feels like building up or down the block.
    """
    if count < 1:
        return []
    rng = rng or random
    if not existing:
        # Odd numbers only so consecutive homes share the same parity.
        span = (_ADDRESS_MAX - _ADDRESS_MIN) // 2
        base = _ADDRESS_MIN + 2 * rng.randint(0, span)
        return [base + 2 * i for i in range(count)]

    lo, hi = min(existing), max(existing)
    go_high = rng.choice((True, False))
    if go_high:
        return [hi + 2 * (i + 1) for i in range(count)]
    # Going low: first new address is lo-2, then lo-4, … — return ascending.
    nums = [lo - 2 * (i + 1) for i in range(count)]
    # Guard against dropping to non-positive house numbers.
    while nums and nums[-1] <= 0:
        # Flip to the high side if the low side ran out.
        return [hi + 2 * (i + 1) for i in range(count)]
    return sorted(nums)


def parse_street_home_key(key):
    """Return ``(street, addr, sub)`` from a populate home room key, or None.

    Keys look like ``Campbell Pass 12237 Porch`` or
    ``Hickory Parkway 12641 Living``. Internal keys are never rewritten —
    only titles / flags use this parse.
    """
    text = str(key or "").strip()
    match = _HOME_KEY_RE.match(text)
    if not match:
        return None
    return (
        match.group("street").strip(),
        match.group("addr").strip(),
        match.group("sub").strip(),
    )


def expected_street_home_title(city, street, addr, sub):
    """Canonical player ROOM NAME for a street-home chamber."""
    return hooks.populate_room_title(
        str(city).strip(), f"{addr} {street}", sub,
    )


def validate_home_shell(rooms, street_exits, *, street_key):
    """Assert number→porch→living→branches (raises ValueError on drift).

    Hard shell (docs/AREA_BUILDING.md §4c / house-porch-shell rule):
      street digit exit → Porch (outdoor + private_home)
      porch in → Living; living out → porch
      other chambers branch from Living (backyard may be outdoor)
    """
    by_key = {r.get("key"): r for r in rooms if r.get("key")}
    for addr, porch_key in (street_exits or {}).items():
        porch = by_key.get(porch_key)
        if porch is None:
            raise ValueError(f"home shell: missing porch {porch_key!r}")
        if not porch.get("outdoor"):
            raise ValueError(f"home shell: porch {porch_key!r} must be outdoor")
        if not porch.get("private_home"):
            raise ValueError(
                f"home shell: porch {porch_key!r} must be private_home"
            )
        pe = porch.get("exits") or {}
        if pe.get("out") != street_key:
            raise ValueError(
                f"home shell: porch {porch_key!r} out must be street"
            )
        living_key = pe.get("in")
        if not living_key or not str(living_key).endswith(" Living"):
            raise ValueError(
                f"home shell: porch {porch_key!r} in must be Living, "
                f"got {living_key!r}"
            )
        living = by_key.get(living_key)
        if living is None:
            raise ValueError(f"home shell: missing living {living_key!r}")
        if living.get("outdoor"):
            raise ValueError(
                f"home shell: living {living_key!r} must not be outdoor"
            )
        le = living.get("exits") or {}
        if le.get("out") != porch_key:
            raise ValueError(
                f"home shell: living {living_key!r} out must be porch"
            )
        parsed = parse_street_home_key(porch_key)
        if parsed and str(addr) != parsed[1]:
            raise ValueError(
                f"home shell: street exit {addr!r} mismatches porch "
                f"address {parsed[1]!r}"
            )


def _branch_room_entry(kind, room_key, title, living_key, back_dir,
                       *, area_type, zone, include_fridge):
    """Build one interior branch room (bedroom / bath / kitchen / den)."""
    entry = {
        "key": room_key,
        "title": title,
        "area_type": area_type,
        "wilderness": False,
        "outdoor": False,
        "exits": {back_dir: living_key},
        "is_house": True,
        "is_home": True,
        "main_homeroom": living_key,
    }
    if zone:
        entry["zone"] = zone

    if kind == "bedroom":
        entry["description"] = (
            f"{title}. A small bedroom with a quilted bed. "
            f"{back_dir.capitalize()} returns to the living room."
        )
        entry["resources"] = ["sleep", "water", "hygiene"]
        entry["resource_capacity"] = {"sleep": 1}
        entry["seed_items"] = [{"item": "worn_bed"}]
    elif kind == "bathroom":
        entry["description"] = (
            f"{title}. A compact bathroom with a sink and shower. "
            f"{back_dir.capitalize()} returns to the living room."
        )
        entry["resources"] = ["hygiene", "water"]
    elif kind == "kitchen":
        entry["description"] = (
            f"{title}. A galley kitchen with a humming refrigerator. "
            f"{back_dir.capitalize()} returns to the living room."
        )
        entry["resources"] = ["water", "food", "hygiene"]
        if include_fridge:
            entry["seed_items"] = [{"item": "refrigerator"}]
    else:  # den
        entry["description"] = (
            f"{title}. A second sitting room with a battered TV. "
            f"{back_dir.capitalize()} returns to the living room."
        )
        entry["resources"] = ["water", "entertainment", "hygiene"]
        entry["seed_items"] = [{"item": "battered_tv"}]
    return entry


_CARDINAL_BACK = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
}


def _porch_and_living_shell(
    street_key, porch_key, living_key, addr_label, *,
    area_type, zone, has_kitchen, living_exits, large, city,
    porch_description=None,
):
    """Shared porch + living dicts for generic and large homes."""
    
    porch_title = hooks.populate_room_title(city, addr_label, "Porch")
    living_title = hooks.populate_room_title(city, addr_label, "Living")
    porch_desc = porch_description or (
        f"A painted porch and mailbox at {addr_label}. "
        "Type in to enter. Out returns to the street."
    )
    porch = {
        "key": porch_key,
        "title": porch_title,
        "description": porch_desc,
        "area_type": area_type,
        "wilderness": False,
        "outdoor": True,
        "private_home": True,
        "exits": {
            "out": street_key,
            "in": living_key,
        },
    }
    if zone:
        porch["zone"] = zone

    living_seeds = [{"item": "battered_tv"}, {"item": "tabletop_radio"}]
    # Fridge lives in the kitchen when one was rolled; otherwise living.
    if not has_kitchen:
        living_seeds.append({"item": "refrigerator"})

    size_note = (
        " Stairs climb to the second floor; a back door opens to the yard."
        if large else ""
    )
    living = {
        "key": living_key,
        "title": living_title,
        "description": (
            f"Inside {addr_label} — couch, local news on a battered TV"
            f"{', fridge hum' if not has_kitchen else ''}. "
            f"Out to the porch.{size_note}"
        ),
        "area_type": area_type,
        "wilderness": False,
        "outdoor": False,
        "exits": living_exits,
        "is_house": True,
        "is_home": True,
        "main_homeroom": living_key,
        "resources": ["water", "entertainment", "hygiene"],
        "seed_items": living_seeds,
    }
    if zone:
        living["zone"] = zone
    return porch, living




def populate_lot(game, room):
    """Stamp the current room as an empty claimable homestead lot.

    Does not dig rooms -- players ``claimplot`` then raise a shell.
    Persists via map JSON when the room is authored (Studio / dig).
    """
    if room is None:
        return False, "Stand in a room to mark as a build lot."
    if getattr(room, "wilderness", False):
        return False, "Build lots are town parcels, not wilderness cells."
    room.build_lot = True
    # Soft persist when map_store can write this room.
    try:
        map_store.rset_field(game, room, "build_lot", "on")
    except Exception:
        # Runtime-only stamp still works until next map reload.
        pass
    return True, (
        f"Marked {room.key} as a build lot (build_lot). "
        "Players may claimplot here."
    )


def _build_generic_home(street_room, street_name, address, *, rng):
    """Porch + living + three random branch rooms (generic layout)."""
    
    area_type = getattr(street_room, "area_type", None) or "city"
    zone = getattr(street_room, "zone", None)
    street_key = street_room.key
    city = _city_label_for_room(street_room)
    addr = str(address)

    porch_key = f"{street_name} {addr} Porch"
    living_key = f"{street_name} {addr} Living"
    addr_label = f"{addr} {street_name}"

    kinds = list(rng.sample(
        [k for k in _HOME_BRANCH_KINDS if k != "bedroom"], 2,
    ))
    # Every generic home gets a bedroom — random triples could omit sleep entirely.
    kinds.insert(0, "bedroom")
    dirs = list(rng.sample(_BRANCH_DIRS, 3))
    has_kitchen = "kitchen" in kinds

    living_exits = {"out": porch_key}
    branch_entries = []
    for kind, direction in zip(kinds, dirs):
        label = kind.capitalize() if kind != "den" else "Den"
        branch_key = f"{street_name} {addr} {label}"
        branch_title = hooks.populate_room_title(city, addr_label, label)
        back = _CARDINAL_BACK[direction]
        living_exits[direction] = branch_key
        branch_entries.append(
            _branch_room_entry(
                kind, branch_key, branch_title, living_key, back,
                area_type=area_type, zone=zone,
                include_fridge=(kind == "kitchen"),
            )
        )

    porch, living = _porch_and_living_shell(
        street_key, porch_key, living_key, addr_label,
        area_type=area_type, zone=zone, has_kitchen=has_kitchen,
        living_exits=living_exits, large=False, city=city,
    )
    rooms = [porch, living] + branch_entries
    return rooms, {addr: porch_key}


def _build_large_home(street_room, street_name, address, *, rng):
    """Porch + six-room large layout (backyard + upstairs).

    Interior rooms (6):
      1. Living (claim hub)
      2-3. Two random ground rooms from the branch pool
      4. Backyard (outdoor yard off living)
      5. Upstairs bedroom (up from living)
      6. Office / entertainment (off the bedroom)

    Plus the porch entry (+1) from the street.
    """
    
    area_type = getattr(street_room, "area_type", None) or "city"
    zone = getattr(street_room, "zone", None)
    street_key = street_room.key
    city = _city_label_for_room(street_room)
    addr = str(address)

    porch_key = f"{street_name} {addr} Porch"
    living_key = f"{street_name} {addr} Living"
    backyard_key = f"{street_name} {addr} Backyard"
    bedroom_key = f"{street_name} {addr} Bedroom"
    office_key = f"{street_name} {addr} Office"
    addr_label = f"{addr} {street_name}"

    # Two random ground rooms; upstairs bedroom is fixed separately.
    kinds = list(rng.sample(_HOME_BRANCH_KINDS, 2))
    # Prefer a leftover cardinal for the backyard after placing ground rooms.
    dirs = list(rng.sample(_BRANCH_DIRS, 2))
    yard_dir = rng.choice(
        [d for d in _BRANCH_DIRS if d not in dirs] or list(_BRANCH_DIRS)
    )
    has_kitchen = "kitchen" in kinds

    living_exits = {
        "out": porch_key,
        "up": bedroom_key,
        yard_dir: backyard_key,
    }
    branch_entries = []
    for kind, direction in zip(kinds, dirs):
        label = kind.capitalize() if kind != "den" else "Den"
        branch_key = f"{street_name} {addr} {label}"
        # Avoid colliding with the fixed upstairs Bedroom key when the
        # ground roll also picks bedroom (use Guest Bedroom instead).
        if kind == "bedroom":
            label = "Guest Bedroom"
            branch_key = f"{street_name} {addr} Guest Bedroom"
        branch_title = hooks.populate_room_title(city, addr_label, label)
        back = _CARDINAL_BACK[direction]
        living_exits[direction] = branch_key
        branch_entries.append(
            _branch_room_entry(
                kind, branch_key, branch_title, living_key, back,
                area_type=area_type, zone=zone,
                include_fridge=(kind == "kitchen"),
            )
        )

    backyard = {
        "key": backyard_key,
        "title": hooks.populate_room_title(city, addr_label, "Backyard"),
        "description": (
            f"A fenced backyard behind {addr_label}. "
            f"{_CARDINAL_BACK[yard_dir].capitalize()} returns inside."
        ),
        "area_type": area_type,
        "wilderness": False,
        "outdoor": True,
        "exits": {_CARDINAL_BACK[yard_dir]: living_key},
        # Yard is outdoor living space -- not the claim hub / hard door.
        "resources": ["entertainment"],
    }
    if zone:
        backyard["zone"] = zone

    # Upstairs: living --up--> bedroom --east--> office (entertainment).
    office_dir = "east"
    office_back = "west"
    bedroom = {
        "key": bedroom_key,
        "title": hooks.populate_room_title(city, addr_label, "Bedroom"),
        "description": (
            f"Upstairs bedroom at {addr_label}. A quilted bed faces the "
            f"window. Down returns to the living room; {office_dir} opens "
            "into a small office den."
        ),
        "area_type": area_type,
        "wilderness": False,
        "outdoor": False,
        "exits": {
            "down": living_key,
            office_dir: office_key,
        },
        "is_house": True,
        "is_home": True,
        "main_homeroom": living_key,
        "resources": ["sleep", "water", "hygiene"],
        "resource_capacity": {"sleep": 1},
        "seed_items": [{"item": "worn_bed"}],
    }
    if zone:
        bedroom["zone"] = zone

    office = {
        "key": office_key,
        "title": hooks.populate_room_title(city, addr_label, "Office"),
        "description": (
            f"A second-floor office / entertainment room at {addr_label}. "
            "A battered TV and a cluttered desk share the space. "
            f"{office_back.capitalize()} returns to the bedroom."
        ),
        "area_type": area_type,
        "wilderness": False,
        "outdoor": False,
        "exits": {office_back: bedroom_key},
        "is_house": True,
        "is_home": True,
        "main_homeroom": living_key,
        "resources": ["water", "entertainment", "hygiene"],
        "seed_items": [{"item": "battered_tv"}],
    }
    if zone:
        office["zone"] = zone

    porch, living = _porch_and_living_shell(
        street_key, porch_key, living_key, addr_label,
        area_type=area_type, zone=zone, has_kitchen=has_kitchen,
        living_exits=living_exits, large=True, city=city,
    )
    rooms = [porch, living, backyard, bedroom, office] + branch_entries
    return rooms, {addr: porch_key}


def _build_one_home(street_room, street_name, address, *, rng, large=False):
    """Return (new_room_dicts, street_exit_patch) for one house at address."""
    if large:
        return _build_large_home(
            street_room, street_name, address, rng=rng,
        )
    return _build_generic_home(
        street_room, street_name, address, rng=rng,
    )


def populate_homes(game, street_room, count, *, large=False, rng=None):
    """Create ``count`` claimable street homes off the standing street.

    Returns (ok, message). ``large`` builds the backyard + upstairs layout.
    """
    if count < 1:
        return False, "Usage: populate homes <n> [large]"
    if count > 20:
        return False, "Cap is 20 homes per populate call."

    street_name = parse_street_name(street_room)
    if not street_name:
        suffixes = ", ".join(ADDRESS_SUFFIXES[:8]) + ", …"
        return False, (
            "Stand on a street hub whose look name ends with an address "
            f"suffix ({suffixes}), e.g. Ferguson Street or Stevenson Lane. "
            "Or type: populate neighborhood  (then populate homes <n>)"
        )

    rng = rng or random
    existing = _existing_street_addresses(game, street_room, street_name)
    addresses = pick_home_addresses(existing, count, rng=rng)

    # Refuse if any chosen address / key already collides.
    new_rooms = []
    street_exits = {}
    for address in addresses:
        addr = str(address)
        if addr in (street_room.exits or {}):
            return False, (
                f"Street already has exit {addr!r}. "
                "Try again or clear that exit first."
            )
        porch_key = f"{street_name} {addr} Porch"
        if porch_key in game.rooms:
            return False, (
                f"Room {porch_key!r} already exists. "
                "Address picker collided -- try again."
            )
        rooms, patch = _build_one_home(
            street_room, street_name, address, rng=rng, large=large,
        )
        validate_home_shell(
            rooms, patch, street_key=street_room.key,
        )
        new_rooms.extend(rooms)
        street_exits.update(patch)

    _stamp_home_layouts(game, street_room, new_rooms)

    msg = map_store.append_hand_rooms(
        game,
        street_room,
        new_rooms,
        extra_exits={street_room.key: street_exits},
    )
    size = "large " if large else ""
    addr_list = ", ".join(str(a) for a in addresses)
    tip = (
        f"{street_name}: added {count} {size}home(s) at {addr_list}. "
        f"Players type the address number (e.g. {addresses[0]}) from the "
        f"street, then in. "
        f"{msg}"
    )
    return True, tip




def populate_all_homes(game, anchor_room, count, *, large=False, rng=None):
    """Populate every street hub on the same map as ``anchor_room``.

    Street hubs are rooms whose look name ends with an ``ADDRESS_SUFFIXES``
    token. Same ``count`` / ``large`` applies to every street.
    """
    map_id = getattr(anchor_room, "map_id", None)
    if not map_id:
        return False, (
            "This room has no map_id -- cannot scope populate all to a map."
        )
    if count < 1:
        return False, "Usage: populate all homes <n> [large]"
    streets = [
        room for room in rooms_on_same_map(game, anchor_room)
        if is_street_hub(room)
    ]
    if not streets:
        return False, (
            f"No street hubs (look name ending in Street/Lane/Circle/…) "
            f"found on map {map_id!r}. "
            "Use populate neighborhood on empty blocks first."
        )
    rng = rng or random
    lines = [
        f"populate all homes {count}"
        f"{' large' if large else ''} on map {map_id} "
        f"({len(streets)} street(s)):"
    ]
    any_ok = False
    for street in sorted(streets, key=lambda r: _room_look_name(r).lower()):
        ok, msg = populate_homes(
            game, street, count, large=large, rng=rng,
        )
        mark = "ok" if ok else "skip"
        if ok:
            any_ok = True
        lines.append(f"  [{mark}] {_room_look_name(street)}: {msg}")
    return any_ok, "\r\n".join(lines)


def _is_neighborhood_wipe_candidate(room, hub):
    """True when a room looks like part of a populated street (safe to delete).

    Keeps wilderness / foreign-map / plain outdoor connectors (e.g. The
    Wastes) — those are only unlinked from the hub, not destroyed.
    """
    if room is None or hub is None:
        return False
    if room is hub:
        return False
    hub_map = getattr(hub, "map_id", None)
    room_map = getattr(room, "map_id", None)
    if hub_map and room_map and room_map != hub_map:
        return False
    if getattr(room, "is_house", False) or getattr(room, "private_home", False):
        return True
    key = str(getattr(room, "key", "") or "")
    if re.search(
        r"\s(Porch|Living|Bedroom|Kitchen|Bathroom|Den|Backyard|Office|"
        r"Guest Bedroom)$",
        key,
        re.IGNORECASE,
    ):
        return True
    # Numbered address titles: ``12305 Ferguson Street``.
    name = _room_look_name(room)
    if re.match(r"^\d+\s+", name):
        return True
    return False


def _collect_wipe_keys(game, hub, keep):
    """Keys reachable from hub exits (except keep) that are wipe candidates.

    BFS never enters ``keep`` or ``hub``. Non-candidate destinations (the
    wastes, another street) are not deleted — only unlinked later.
    """
    keep_key = getattr(keep, "key", None)
    hub_key = getattr(hub, "key", None)
    doomed = set()
    blocked = {keep_key, hub_key}
    queue = []
    for dest in (getattr(hub, "exits", None) or {}).values():
        dest_key = getattr(dest, "key", None) if dest is not None else None
        if not dest_key or dest_key in blocked:
            continue
        room = game.rooms.get(dest_key)
        if room is None:
            continue
        if not _is_neighborhood_wipe_candidate(room, hub):
            continue
        doomed.add(dest_key)
        queue.append(dest_key)
    seen = set(blocked) | doomed
    while queue:
        key = queue.pop()
        room = game.rooms.get(key)
        if room is None:
            continue
        for dest in (getattr(room, "exits", None) or {}).values():
            dest_key = getattr(dest, "key", None) if dest is not None else None
            if not dest_key or dest_key in seen:
                continue
            other = game.rooms.get(dest_key)
            if other is None:
                continue
            if not _is_neighborhood_wipe_candidate(other, hub):
                # Still block traversing into keep/hub; skip wastes.
                seen.add(dest_key)
                continue
            seen.add(dest_key)
            doomed.add(dest_key)
            queue.append(dest_key)
    return doomed


def _prepare_neighborhood_hub(game, hub, *, keep=None, clear_exits=True):
    """Clear lodging flags, stamp outdoor + city, optionally trim exits.

    When ``keep`` is set and ``clear_exits`` is True (directional wipe),
    every exit on the hub except the one pointing at ``keep`` is removed
    (JSON + live). The reverse keep→hub link is left alone.

    Pass ``clear_exits=False`` when topping up homes so numeric address
    exits to existing porches stay linked.
    """

    notes = []

    # Drop house / nest flags so the hub is a clean street block again.
    for flag in ("is_house", "is_home", "private_home", "wilderness"):
        if getattr(hub, flag, False):
            try:
                notes.append(map_store.rset_field(game, hub, flag, "off"))
            except ValueError as err:
                notes.append(str(err))
    # Clear compound pointer when a hub was wrongly stamped as interior.
    if getattr(hub, "main_homeroom", None):
        try:
            notes.append(
                map_store.rset_field(game, hub, "main_homeroom", "")
            )
        except ValueError:
            hub.main_homeroom = None
            notes.append(f"cleared {hub.key}.main_homeroom (live)")

    if not getattr(hub, "outdoor", False):
        try:
            notes.append(map_store.rset_field(game, hub, "outdoor", "on"))
        except ValueError as err:
            notes.append(str(err))

    if (getattr(hub, "area_type", None) or "").lower() != "city":
        try:
            notes.append(map_store.rset_field(game, hub, "area_type", "city"))
        except ValueError as err:
            notes.append(str(err))

    if keep is not None and clear_exits:
        keep_key = getattr(keep, "key", None)
        # Collect directions to unlink first (mutating while iterating is messy).
        to_clear = []
        for direction, dest in list((getattr(hub, "exits", None) or {}).items()):
            dest_key = getattr(dest, "key", None) if dest is not None else None
            if dest_key != keep_key:
                to_clear.append(direction)
        for direction in to_clear:
            try:
                # Unidirectional clear on hub — reverse on a deleted house
                # is already gone; reverse on wastes should stay so staff
                # can still walk back from the wastes into the hub.
                map_store.unlink_exit(
                    game, hub, direction, bidirectional=False,
                )
                notes.append(f"cleared hub exit {direction}")
            except ValueError as err:
                notes.append(str(err))
        # Ensure keep still has a way back to the hub when one existed.
        # (Do not invent a new reverse if staff never linked one.)

    return notes


def populate_neighborhood(
    game, room, *, direction=None, homes_count=None, large=False, rng=None,
):
    """Rename a street hub (or dig/wipe toward one) and stamp outdoor + city.

    Bare: operate on the standing room (must not be a house/porch).

    With ``direction``: stand in the keep room (e.g. wastes / intersection),
    point at the hub. If that exit is missing, dig a new outdoor city hub
    there (bidirectional). If the hub already exists and ``homes_count`` is
    omitted, delete wipe-candidate rooms hanging off it (houses / porches /
    interiors), clear hub exits except the link back to you, stamp outdoor
    + ``area_type`` city, then rename the hub.

    With ``homes_count`` (target total on that street): do **not** wipe
    existing homes. After the hub is ready, stock via ``populate_homes``
    only the shortfall (``homes_count - already_there``). Addresses reuse
    the same +/- 2 high/low picker as ``populate homes``, so a later
    ``5`` after an earlier ``3`` adds two neighbor addresses only.
    When homes are already present, the street look name is kept so old
    porch titles stay coherent.

    Returns (ok, message).
    """
    if room is None:
        return False, "You are nowhere."

    if homes_count is not None:
        if homes_count < 1:
            return False, (
                "Usage: populate neighborhood <direction> <n> [large]  "
                "(n is the target house count on that street)"
            )
        if homes_count > 20:
            return False, "Cap is 20 homes per populate call."


    rng = rng or random
    keep = None
    hub = room
    wipe_notes = []
    # Target-count runs preserve existing houses so a later higher n
    # only tops up (wipe would erase the earlier batch).
    preserve_homes = homes_count is not None

    if direction:
        canon = _normalize_exit_direction(direction)
        if canon is None:
            return False, (
                f"Unknown direction {direction!r}. "
                "Try: populate neighborhood <n|s|e|w|nw|ne|…> [amount]"
            )
        dest = (getattr(room, "exits", None) or {}).get(canon)
        keep = room
        dug_fresh = False

        # No exit yet -- dig a blank hub in that direction (staff asked
        # for the neighborhood; do not refuse an empty cell).
        if dest is None:
            dig_local = "Neighborhood Hub"
            try:
                dig_msg = map_store.dig_room(
                    game, room, canon, dig_local,
                    description=(
                        "A quiet residential stretch waiting for homes."
                    ),
                )
            except ValueError as err:
                return False, str(err)
            wipe_notes.append(dig_msg)
            dug_fresh = True
            dest = (getattr(room, "exits", None) or {}).get(canon)

        hub = dest if hasattr(dest, "exits") else game.rooms.get(
            getattr(dest, "key", dest)
        )
        if hub is None:
            return False, (
                f"Exit {canon!r} points at a missing room. "
                f"Unlink it (room unlink {canon}) then retry so a hub "
                "can be dug."
            )

        if is_apartment_floor(hub):
            return False, (
                "That room is an apartment floor -- use populate apartments "
                "instead of populate neighborhood."
            )
        if is_hotel_floor(hub):
            return False, (
                "That room is a hotel floor -- use populate hotels "
                "instead of populate neighborhood."
            )
        if is_motel_floor(hub):
            return False, (
                "That room is a motel floor -- use populate motels "
                "instead of populate neighborhood."
            )
        if is_hospital_floor(hub):
            return False, (
                "That room is a hospital floor -- use populate hospitals "
                "instead of populate neighborhood."
            )

        # Existing hub without a homes target: wipe old houses/porches.
        # With a homes target: keep them so we can top up the count.
        if not dug_fresh and not preserve_homes:
            doomed = _collect_wipe_keys(game, hub, keep)
            if doomed:
                try:
                    removed, del_msg = map_store.delete_hand_rooms(
                        game, hub, doomed, relocate_to=hub,
                    )
                    wipe_notes.append(del_msg)
                    wipe_notes.append(f"wiped {removed} neighborhood room(s)")
                except ValueError as err:
                    return False, str(err)

        prep = _prepare_neighborhood_hub(
            game, hub, keep=keep, clear_exits=not preserve_homes,
        )
        wipe_notes.extend(prep)
        # Make sure the hub still points back at the standing room.
        # dig_room already links both ways; this repairs older one-way digs.
        back = map_store.opposite_direction(canon)
        if back:
            current = (getattr(hub, "exits", None) or {}).get(back)
            cur_key = (
                getattr(current, "key", None) if current is not None else None
            )
            if cur_key != keep.key:
                try:
                    link_msg = map_store.link_rooms(
                        game, hub, back, keep.key,
                        bidirectional=False,
                    )
                    wipe_notes.append(link_msg)
                except ValueError as err:
                    wipe_notes.append(str(err))
    else:
        # Bare: must already stand on the hub block.
        if getattr(room, "is_house", False) or getattr(room, "private_home", False):
            return False, (
                "Cannot run populate neighborhood inside a house / porch. "
                "Stand on the empty block, or use "
                "populate neighborhood <direction> from the back-link room."
            )
        if is_apartment_floor(room):
            return False, (
                "This room is an apartment floor -- use populate apartments "
                "instead of populate neighborhood."
            )
        if is_hotel_floor(room):
            return False, (
                "This room is a hotel floor -- use populate hotels "
                "instead of populate neighborhood."
            )
        if is_motel_floor(room):
            return False, (
                "This room is a motel floor -- use populate motels "
                "instead of populate neighborhood."
            )
        if is_hospital_floor(room):
            return False, (
                "This room is a hospital floor -- use populate hospitals "
                "instead of populate neighborhood."
            )
        wipe_notes.extend(_prepare_neighborhood_hub(game, hub, keep=None))

    # How many homes already hang off this hub (numeric street exits /
    # porch titles)? Used to decide rename-vs-keep and top-up shortfall.
    street_name_now = parse_street_name(hub)
    existing_addrs = (
        _existing_street_addresses(game, hub, street_name_now)
        if street_name_now
        else []
    )
    have_homes = len(existing_addrs)

    # Keep the street look name when topping up so porch titles like
    # "12001 Stevenson Lane" stay aligned with the hub.
    keep_title = preserve_homes and have_homes > 0 and bool(street_name_now)
    if keep_title:
        title = _room_look_name(hub)
        title_msg = f"kept street name {title!r}"
    else:
        
        street_leaf = pick_neighborhood_title(game, hub, rng=rng)
        title = hooks.populate_room_title(_city_label_for_room(hub), street_leaf)
        try:
            title_msg = map_store.rset_field(game, hub, "title", title)
        except ValueError as err:
            return False, str(err)

    # Friendly street blurb when the description is still a stub / wipe leftover.
    desc = (getattr(hub, "description", None) or "").strip()
    stubby = (
        not desc
        or (desc.endswith(".") and len(desc) < 40)
        or "newly dug" in desc.lower()
        or "painted porch" in desc.lower()
        or desc == f"{hub.key}."
        or "quiet residential stretch" in desc.lower()
    )
    if stubby or (direction and not keep_title):
        if homes_count is not None:
            new_desc = (
                f"{title} — a quiet residential stretch with claimable "
                "porches down the block."
            )
        else:
            new_desc = (
                f"{title} — a quiet residential stretch. "
                "Claimable porches wait down the block once homes are "
                "populated."
            )
        try:
            map_store.rset_field(game, hub, "description", new_desc)
        except ValueError:
            pass

    extra = ""
    if wipe_notes:
        extra = " " + "; ".join(wipe_notes[:6])
        if len(wipe_notes) > 6:
            extra += f"; …(+{len(wipe_notes) - 6} more)"

    where = (
        f"via {direction!r} from {keep.key!r}"
        if keep is not None
        else "here"
    )
    hub_msg = (
        f"Neighborhood hub is now {title!r} (key still {hub.key!r}, {where}). "
        f"{title_msg} outdoor={getattr(hub, 'outdoor', False)} "
        f"area_type={getattr(hub, 'area_type', None)!r}."
        f"{extra}"
    )

    if homes_count is None:
        return True, (
            f"{hub_msg} "
            f"Next: populate homes <n>  or  "
            f"populate neighborhood <direction> <n>"
        )

    # Target total: only create the shortfall. populate_homes reuses
    # pick_home_addresses so new numbers sit +/- 2 off the current
    # high or low (neighbor continuity).
    need = homes_count - have_homes
    if need <= 0:
        return True, (
            f"{hub_msg} Already has {have_homes} home(s) "
            f"(target {homes_count}) -- nothing added."
        )

    ok_h, msg_h = populate_homes(
        game, hub, need, large=large, rng=rng,
    )
    if not ok_h:
        return False, f"{hub_msg} Homes failed: {msg_h}"
    size = "large " if large else ""
    return True, (
        f"{hub_msg} Stocked {need} {size}home(s) toward target "
        f"{homes_count} (had {have_homes}). {msg_h}"
    )


def _room_entry_has_sleep(entry):
    """True when a rooms[] dict already offers sleep or a bed item."""
    if "sleep" in (entry.get("resources") or []):
        return True
    for spec in entry.get("seed_items") or []:
        item = spec.get("item") if isinstance(spec, dict) else spec
        if item and "bed" in str(item).lower():
            return True
    return False


def _house_cluster_hub_key(cluster_entries):
    """Pick the living-hub key for an is_house cluster."""
    for entry in cluster_entries:
        hub = (entry.get("main_homeroom") or "").strip()
        if hub:
            return hub
    for entry in cluster_entries:
        key = entry.get("key") or ""
        if key.endswith(" Living"):
            return key
    return (cluster_entries[0].get("key") or "").strip()


def _stamp_pullout_bed_on_living(entry):
    """Add sleep + worn_bed to a living hub (returns True if mutated)."""
    changed = False
    resources = list(entry.get("resources") or [])
    if "sleep" not in resources:
        resources.append("sleep")
        entry["resources"] = resources
        changed = True
    capacity = dict(entry.get("resource_capacity") or {})
    if int(capacity.get("sleep") or 0) < 1:
        capacity["sleep"] = 1
        entry["resource_capacity"] = capacity
        changed = True
    seeds = list(entry.get("seed_items") or [])
    has_bed = any(
        "bed" in str(
            (spec.get("item") if isinstance(spec, dict) else spec) or ""
        ).lower()
        for spec in seeds
    )
    if not has_bed:
        seeds.append({"item": "worn_bed"})
        entry["seed_items"] = seeds
        changed = True
    desc = (entry.get("description") or "").strip()
    low = desc.lower()
    if desc and "pull-out bed" not in low and "quilted bed" not in low:
        if desc.endswith("."):
            entry["description"] = (
                desc[:-1] + ", and a worn pull-out bed along the wall."
            )
        else:
            entry["description"] = (
                desc + " A worn pull-out bed waits along the wall."
            )
        changed = True
    return changed


def fix_beds_in_map_doc(doc, *, min_rooms=3):
    """Stamp pull-out beds on house clusters missing sleep on disk.

    Returns ``(fixed_count, living_keys)``.
    """
    rooms = doc.get("rooms") or []
    clusters = {}
    for entry in rooms:
        if not entry.get("is_house"):
            continue
        hub = _house_cluster_hub_key([entry]) or entry.get("key")
        if not hub:
            continue
        clusters.setdefault(hub, []).append(entry)

    fixed_keys = []
    for hub, cluster in clusters.items():
        if len(cluster) < min_rooms:
            continue
        if any(_room_entry_has_sleep(e) for e in cluster):
            continue
        living = next((e for e in cluster if e.get("key") == hub), None)
        if living is None:
            living = next(
                (e for e in cluster if (e.get("key") or "").endswith(" Living")),
                cluster[0],
            )
        if _stamp_pullout_bed_on_living(living):
            fixed_keys.append(living.get("key") or hub)
    return len(fixed_keys), fixed_keys


def populate_fix_beds(game):
    """Retroactively add sleep + worn_bed to house clusters missing beds.

    Scans every map/zone JSON on disk, persists fixes, and syncs loaded
    live rooms when ``game`` is provided.

    Returns ``(ok, message)``.
    """
    if game is None:
        return False, "No game."
    import maps as maps_mod

    total_fixed = 0
    files_touched = 0
    sample_keys = []
    for path in maps_mod.iter_map_json_paths():
        try:
            doc = map_store.load_doc(path)
        except (OSError, ValueError):
            continue
        count, keys = fix_beds_in_map_doc(doc)
        if not count:
            continue
        map_store.save_doc_validated(path, doc)
        files_touched += 1
        total_fixed += count
        sample_keys.extend(keys)
        by_key = {
            e.get("key"): e
            for e in (doc.get("rooms") or [])
            if e.get("key")
        }
        for room_key in keys:
            live = game.rooms.get(room_key)
            entry = by_key.get(room_key)
            if live is None or entry is None:
                continue
            hooks.map_store_apply_entry_fields(live, entry)
            hooks.map_store_place_seed_items(
                game,
                room_key,
                entry.get("seed_items") or [],
                where="populate_fix_beds",
            )

    if not total_fixed:
        return True, (
            "Fix beds: no house clusters missing sleep/bed were found "
            "on disk."
        )
    preview = ", ".join(sample_keys[:5])
    if len(sample_keys) > 5:
        preview += ", …"
    return True, (
        f"Fix beds: stamped {total_fixed} living hub(s) across "
        f"{files_touched} file(s). {preview}"
    )


def _city_for_home_entry(entry, doc=None):
    """Best-effort city label for a rooms[] street-home dict."""
    title = str(entry.get("title") or "").strip()
    city, _main, _sub = split_structured_title(title)
    if city:
        return city
    stamped = str(entry.get("city_name") or "").strip()
    if stamped:
        return stamped
    map_id = ""
    if isinstance(doc, dict):
        map_id = str(doc.get("id") or doc.get("map_id") or "").strip()
    return hooks.populate_city_for_map_id(map_id)


def fix_shell_in_map_doc(doc):
    """Heal street-home titles / outdoor / digit exits in one map JSON.

    Returns ``(rooms_changed, exit_patches, sample_keys)``.
    """
    rooms = doc.get("rooms") or []
    by_key = {e.get("key"): e for e in rooms if e.get("key")}
    changed_keys = []
    exit_patches = 0

    for entry in rooms:
        key = entry.get("key") or ""
        parsed = parse_street_home_key(key)
        if not parsed:
            continue
        street, addr, sub = parsed
        city = _city_for_home_entry(entry, doc)
        want_title = expected_street_home_title(city, street, addr, sub)
        mutated = False
        if (entry.get("title") or "").strip() != want_title:
            entry["title"] = want_title
            mutated = True

        sub_l = sub.lower()
        if sub_l == "porch":
            if not entry.get("outdoor"):
                entry["outdoor"] = True
                mutated = True
            if not entry.get("private_home"):
                entry["private_home"] = True
                mutated = True
            # Porch is the hard door — not the claim hub.
            if entry.get("is_home"):
                entry["is_home"] = False
                mutated = True
            if entry.get("is_house"):
                entry["is_house"] = False
                mutated = True
            living_key = f"{street} {addr} Living"
            pe = dict(entry.get("exits") or {})
            if pe.get("in") != living_key and living_key in by_key:
                pe["in"] = living_key
                entry["exits"] = pe
                mutated = True
        elif sub_l == "backyard":
            if not entry.get("outdoor"):
                entry["outdoor"] = True
                mutated = True
        else:
            # Living + interiors: indoor.
            if entry.get("outdoor"):
                entry["outdoor"] = False
                mutated = True
            if sub_l == "living":
                porch_key = f"{street} {addr} Porch"
                le = dict(entry.get("exits") or {})
                if le.get("out") != porch_key and porch_key in by_key:
                    le["out"] = porch_key
                    entry["exits"] = le
                    mutated = True
                if not entry.get("is_house"):
                    entry["is_house"] = True
                    mutated = True
                if not entry.get("is_home"):
                    entry["is_home"] = True
                    mutated = True
                living_key = key
                if (entry.get("main_homeroom") or "").strip() != living_key:
                    entry["main_homeroom"] = living_key
                    mutated = True

        if mutated:
            changed_keys.append(key)

    # Digit exits on hubs must land on the Porch, never Living / interiors.
    for entry in rooms:
        key = entry.get("key") or ""
        if parse_street_home_key(key):
            continue
        exits = dict(entry.get("exits") or {})
        dirty = False
        for direction, dest in list(exits.items()):
            if not str(direction).isdigit():
                continue
            dest_s = str(dest or "")
            parsed = parse_street_home_key(dest_s)
            if parsed is None:
                continue
            street, addr, sub = parsed
            if str(direction) != addr:
                continue
            if sub.lower() == "porch":
                continue
            porch_key = f"{street} {addr} Porch"
            if porch_key in by_key:
                exits[direction] = porch_key
                dirty = True
                exit_patches += 1
        if dirty:
            entry["exits"] = exits
            if key not in changed_keys:
                changed_keys.append(key)

    return len(changed_keys), exit_patches, changed_keys


def heal_street_home_shell_live(game):
    """Boot / memory heal: titles + outdoor + digit→porch on live Rooms.

    Does not write JSON. Returns rooms touched.
    """
    if game is None:
        return 0
    
    touched = 0
    rooms = getattr(game, "rooms", None) or {}
    for key, room in list(rooms.items()):
        parsed = parse_street_home_key(key)
        if not parsed:
            continue
        street, addr, sub = parsed
        city = (
            str(getattr(room, "city_name", None) or "").strip()
            or hooks.populate_city_for_map_id(getattr(room, "map_id", None) or "")
        )
        want = expected_street_home_title(city, street, addr, sub)
        changed = False
        if (getattr(room, "title", None) or "").strip() != want:
            room.title = want
            changed = True
        sub_l = sub.lower()
        if sub_l == "porch":
            if not getattr(room, "outdoor", False):
                room.outdoor = True
                changed = True
            if not getattr(room, "private_home", False):
                room.private_home = True
                changed = True
            if getattr(room, "is_home", False):
                room.is_home = False
                changed = True
            if getattr(room, "is_house", False):
                room.is_house = False
                changed = True
            living_key = f"{street} {addr} Living"
            living = rooms.get(living_key)
            if living is not None and room.exits.get("in") is not living:
                room.exits["in"] = living
                changed = True
        elif sub_l == "backyard":
            if not getattr(room, "outdoor", False):
                room.outdoor = True
                changed = True
        else:
            if getattr(room, "outdoor", False):
                room.outdoor = False
                changed = True
            if sub_l == "living":
                porch = rooms.get(f"{street} {addr} Porch")
                if porch is not None and room.exits.get("out") is not porch:
                    room.exits["out"] = porch
                    changed = True
        if changed:
            touched += 1

    # Retarget hub digit exits that skip the porch.
    for room in rooms.values():
        if parse_street_home_key(getattr(room, "key", "") or ""):
            continue
        for direction, dest in list((room.exits or {}).items()):
            if not str(direction).isdigit() or dest is None:
                continue
            dest_key = getattr(dest, "key", None) or ""
            parsed = parse_street_home_key(dest_key)
            if parsed is None:
                continue
            street, addr, sub = parsed
            if str(direction) != addr or sub.lower() == "porch":
                continue
            porch = rooms.get(f"{street} {addr} Porch")
            if porch is not None:
                room.exits[direction] = porch
                touched += 1
    return touched


def populate_fix_shell(game):
    """Heal street-home shell (titles, outdoor, digit→porch) + persist JSON.

    Returns ``(ok, message)``.
    """
    if game is None:
        return False, "No game."
    import maps as maps_mod

    total_rooms = 0
    total_exits = 0
    files_touched = 0
    sample = []
    for path in maps_mod.iter_map_json_paths():
        try:
            doc = map_store.load_doc(path)
        except (OSError, ValueError):
            continue
        count, exits_n, keys = fix_shell_in_map_doc(doc)
        if not count and not exits_n:
            continue
        map_store.save_doc_validated(path, doc)
        files_touched += 1
        total_rooms += count
        total_exits += exits_n
        sample.extend(keys[:3])
        by_key = {
            e.get("key"): e
            for e in (doc.get("rooms") or [])
            if e.get("key")
        }
        for room_key in keys:
            live = game.rooms.get(room_key)
            entry = by_key.get(room_key)
            if live is None or entry is None:
                continue
            hooks.map_store_apply_entry_fields(live, entry)
            # Digit exit retargets on hubs need live wiring too.
            for direction, dest_key in (entry.get("exits") or {}).items():
                if not str(direction).isdigit():
                    continue
                dest = game.rooms.get(dest_key)
                if dest is not None:
                    live.exits[direction] = dest

    live_n = heal_street_home_shell_live(game)
    if not total_rooms and not total_exits and not live_n:
        return True, (
            "Fix shell: street homes already match "
            "number → porch → living → branches."
        )
    preview = ", ".join(sample[:5])
    if len(sample) > 5:
        preview += ", …"
    return True, (
        f"Fix shell: healed {total_rooms} room stamp(s) + "
        f"{total_exits} hub exit(s) across {files_touched} file(s); "
        f"live touch-up {live_n}. {preview}"
    )


