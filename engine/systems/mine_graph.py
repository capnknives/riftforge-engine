"""
mine_graph.py -- shared-world mine mouth registry and graph state.

Each wilderness mouth (map_id + macro + micro coords) owns one persistent
tunnel graph: geology seed, carved room nodes, face carve progress, support
ratings, gate fixtures, and cap/shore-gate enforcement.

Persistence uses ``save_meta_json(conn, META_KEY, blob)`` with the same
normalize/export/load discipline as ``room_structure.py``. Mouths lazy-load
on first interact -- never a full-atlas scan at boot.
"""

from __future__ import annotations

import os
import time

from engine import hooks

META_KEY = "mine_graphs"

MAX_ROOMS_PER_MOUTH = 12
SHORE_GATE_ROOM_COUNT = 6
MAX_CHANNEL_JOINERS = 3
MINE_CAPABLE_AREA_TYPES = frozenset({"mountains", "desert", "hills"})
# Settled / water tiles never host a mouth even if highland_rows marked the cell.
_NOT_MINE_AREA_TYPES = frozenset({
    "city", "city_street", "ocean", "lake", "void", "highway", "trail",
})

# Carve directions inside the graph (down = shaft from surface).
GRAPH_DIRECTIONS = frozenset({
    "north", "south", "east", "west", "up", "down",
})
OPPOSITE = {
    "north": "south", "south": "north",
    "east": "west", "west": "east",
    "up": "down", "down": "up",
}

DEFAULT_SUPPORT_MIN_SHALLOW = 1  # timber
DEFAULT_SUPPORT_MIN_DEEP = 2       # steel (depth >= 3)


def mouth_key_from_room(room):
    """Build a mouth key from a wilderness cell, or None.

    Live America uses dual-layer ``Wilderness (mx,my)/ux,uy`` rooms after
    you leave the car. The 1861 Frontierland atlas is walked as authored
    grid rooms (``Frontierland Overland (x, y)``) -- one mouth per macro
    cell, anchored at the landmark micro center so the key shape matches
    Earth v1. Demesnes / Wastes / other grids stay out: only that
    Frontierland prefix (or map_id) is accepted on the grid path.
    """
    if room is None:
        return None
    from engine.map_ui import parse_grid_key
    from engine.systems.overland import (
        FRONTIERLAND_MAP_ID,
        FRONTIERLAND_PREFIX,
        LANDMARK_MICRO,
        parse_wilderness_room_key,
    )

    wild = parse_wilderness_room_key(getattr(room, "key", "") or "")
    if wild is not None:
        map_id = getattr(room, "map_id", None) or "earth_america"
        (mx, my), (ux, uy) = wild
        return format_mouth_key(map_id, mx, my, ux, uy)

    parsed = parse_grid_key(getattr(room, "key", "") or "")
    if parsed is None:
        return None
    prefix, mx, my = parsed
    map_id = str(getattr(room, "map_id", None) or "").strip()
    if prefix != FRONTIERLAND_PREFIX and map_id != FRONTIERLAND_MAP_ID:
        return None
    ux, uy = LANDMARK_MICRO
    return format_mouth_key(map_id or FRONTIERLAND_MAP_ID, mx, my, ux, uy)


def format_mouth_key(map_id, mx, my, ux, uy):
    """Canonical mouth key string."""
    return f"mine:{map_id}:{int(mx)}:{int(my)}:{int(ux)}:{int(uy)}"


def parse_mouth_key(key):
    """Parse mouth key -> (map_id, mx, my, ux, uy) or None."""
    if not key or not str(key).startswith("mine:"):
        return None
    parts = str(key).split(":")
    if len(parts) != 6:
        return None
    try:
        return parts[1], int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])
    except (TypeError, ValueError):
        return None


def is_mine_capable_area(area_type):
    """True when this wilderness macro terrain may host a mine mouth."""
    return str(area_type or "").lower() in MINE_CAPABLE_AREA_TYPES


def is_mine_capable_cell(area_type, highland=False):
    """True when this atlas cell may host a mine mouth (no Room needed)."""
    area = str(area_type or "").lower()
    if area in _NOT_MINE_AREA_TYPES:
        return False
    if is_mine_capable_area(area):
        return True
    return bool(highland)


def is_mine_capable_room(room):
    """True when this room may host a mine mouth.

    Peaks, desert, and (reserved) hills stay mine-capable by area_type.
    America / 1861 CONUS highland timber / high plains use Room.highland
    from the atlas mask so mining country is not a grey slab on the map.
    """
    if room is None:
        return False
    return is_mine_capable_cell(
        getattr(room, "area_type", None),
        highland=getattr(room, "highland", False),
    )


# Always-on look sentence for mine-capable wilderness (peaks, desert,
# highland timber / high plains). Not a random stumble -- players need
# to see they can still mine down after the atlas stopped painting
# those belts as solid grey rock.
MINE_COUNTRY_LOOK_TELL = (
    "Broken stone shows through the soil -- this is mining country. "
    "Type mine down to open a shaft."
)


def with_mine_country_tell(text, area_type, highland=False):
    """Append the mining-country look line when this cell can host a mouth."""
    body = str(text or "").strip()
    if not is_mine_capable_cell(area_type, highland):
        return body
    if MINE_COUNTRY_LOOK_TELL in body:
        return body
    if body:
        return f"{body} {MINE_COUNTRY_LOOK_TELL}"
    return MINE_COUNTRY_LOOK_TELL


def normalize_loaded(blob):
    """Sanitize loaded meta into ``{mouth_key: mouth_state}``."""
    out = {}
    if not isinstance(blob, dict):
        return out
    for mouth_key, mouth in blob.items():
        clean = _normalize_mouth(mouth_key, mouth)
        if clean:
            out[str(mouth_key)] = clean
    return out


def _normalize_mouth(mouth_key, mouth):
    if not isinstance(mouth, dict):
        return None
    rooms = {}
    raw_rooms = mouth.get("rooms") or {}
    if isinstance(raw_rooms, dict):
        for rid, room in raw_rooms.items():
            nr = _normalize_room(rid, room)
            if nr:
                rooms[str(rid)] = nr
    geology = mouth.get("geology") or {}
    if not isinstance(geology, dict):
        geology = {}
    return {
        "geology": {
            "primary_element": str(geology.get("primary_element") or "iron"),
            "secondary_element": str(geology.get("secondary_element") or "copper"),
            "contamination_tag": geology.get("contamination_tag"),
        },
        "rooms": rooms,
        "marker": _normalize_marker(mouth.get("marker")),
        "company_id": mouth.get("company_id"),
        "last_activity_tick": int(mouth.get("last_activity_tick") or 0),
        "last_activity_unix": float(mouth.get("last_activity_unix") or 0),
        "shaft_room_id": mouth.get("shaft_room_id"),
        "fixtures": _normalize_fixtures(mouth.get("fixtures")),
    }


def _normalize_marker(marker):
    if not isinstance(marker, dict):
        return {"visible": False, "identified": False, "company_id": None}
    return {
        "visible": bool(marker.get("visible")),
        "identified": bool(marker.get("identified")),
        "company_id": marker.get("company_id"),
    }


def _normalize_room(rid, room):
    if not isinstance(room, dict):
        return None
    exits = {}
    for direction, face in (room.get("exits") or {}).items():
        if direction not in GRAPH_DIRECTIONS:
            continue
        exits[direction] = _normalize_face(face)
    fixtures = []
    for fix in room.get("fixtures") or []:
        if isinstance(fix, dict) and fix.get("kind"):
            fixtures.append(dict(fix))
    return {
        "id": str(rid),
        "depth": max(0, int(room.get("depth") or 0)),
        "exits": exits,
        "support_rating": max(0, int(room.get("support_rating") or 0)),
        "unsupported_risk": float(room.get("unsupported_risk") or 0.0),
        "vein_depleted": bool(room.get("vein_depleted")),
        "fixtures": fixtures,
        "discoverable_id": room.get("discoverable_id"),
        "room_kind": room.get("room_kind") or "drift",
    }


def _normalize_face(face):
    if not isinstance(face, dict):
        face = {}
    channel = face.get("channel") or {}
    if not isinstance(channel, dict):
        channel = {}
    joiners = []
    for j in channel.get("joiners") or []:
        if isinstance(j, str) and j:
            joiners.append(j)
    gate = face.get("gate") or {}
    if not isinstance(gate, dict):
        gate = {}
    return {
        "carved": bool(face.get("carved")),
        "carve_hp": max(0, int(face.get("carve_hp") or 0)),
        "carve_hp_max": max(1, int(face.get("carve_hp_max") or 100)),
        "blocked_collapse": bool(face.get("blocked_collapse")),
        "channel": {
            "joiners": joiners[:MAX_CHANNEL_JOINERS],
            "progress": max(0, int(channel.get("progress") or 0)),
            "progress_max": max(1, int(channel.get("progress_max") or 100)),
            "ends_at_tick": channel.get("ends_at_tick"),
        },
        "gate": {
            "installed": bool(gate.get("installed")),
            "locked": bool(gate.get("locked")),
            "owner": gate.get("owner"),
            "access": list(gate.get("access") or []),
        },
        "shore_material": face.get("shore_material"),
        "shore_rating": max(0, int(face.get("shore_rating") or 0)),
    }


def _normalize_fixtures(fixtures):
    if not isinstance(fixtures, dict):
        return {}
    return {str(k): v for k, v in fixtures.items() if isinstance(v, dict)}


def export_meta(game):
    """Return persistable blob."""
    state = getattr(game, "mine_graphs", None)
    return normalize_loaded(state)


def load_into_game(game, blob):
    game.mine_graphs = normalize_loaded(blob)


def ensure_game_state(game):
    if not isinstance(getattr(game, "mine_graphs", None), dict):
        game.mine_graphs = {}
    return game.mine_graphs


def _touch_activity(game, mouth):
    """Stamp last activity for prune heuristics."""
    now_tick = int(getattr(game, "game_time_ticks", 0) or 0)
    mouth["last_activity_tick"] = now_tick
    mouth["last_activity_unix"] = time.time()
    game._mine_graphs_dirty = True


def get_mouth(game, mouth_key, *, create=False, area_type=None):
    """Return mouth state dict, optionally creating with geology seed."""
    state = ensure_game_state(game)
    mouth = state.get(mouth_key)
    if mouth is None and create:
        geology = hooks.mine_geology_seed(mouth_key, area_type or "mountains")
        mouth = {
            "geology": geology,
            "rooms": {},
            "marker": {"visible": False, "identified": False, "company_id": None},
            "company_id": None,
            "last_activity_tick": 0,
            "last_activity_unix": time.time(),
            "shaft_room_id": None,
            "fixtures": {},
        }
        state[mouth_key] = mouth
        game._mine_graphs_dirty = True
    return mouth


def carved_room_count(mouth):
    """How many fully carved rooms exist in this mouth."""
    if not mouth:
        return 0
    count = 0
    for room in (mouth.get("rooms") or {}).values():
        if room.get("id"):
            count += 1
    return count


def support_required_for_depth(depth):
    """Minimum support rating to carve past shore gate from this depth."""
    if depth >= 3:
        return DEFAULT_SUPPORT_MIN_DEEP
    return DEFAULT_SUPPORT_MIN_SHALLOW


def can_carve_new_room(mouth, from_room):
    """Shore gate + 12-room cap checks before starting a carve."""
    count = carved_room_count(mouth)
    if count >= MAX_ROOMS_PER_MOUTH:
        return False, (
            "This mouth has hit the twelve-room cap. Seal or abandon a "
            "branch before carving more."
        )
    if count >= SHORE_GATE_ROOM_COUNT:
        rating = int((from_room or {}).get("support_rating") or 0)
        need = support_required_for_depth(int((from_room or {}).get("depth") or 0))
        if rating < need:
            mat = "steel" if need >= DEFAULT_SUPPORT_MIN_DEEP else "timber"
            return False, (
                f"Supports here are too weak to carve deeper ({rating} < {need}). "
                f"Shore the chamber with {mat} first (shore <dir> {mat})."
            )
    return True, ""


def get_face(room, direction):
    """Return face dict for direction, creating shell if needed."""
    exits = room.setdefault("exits", {})
    face = exits.get(direction)
    if face is None:
        face = _normalize_face({})
        exits[direction] = face
    return face


def room_by_id(mouth, room_id):
    return (mouth.get("rooms") or {}).get(room_id)


def ensure_shaft_room(game, mouth_key, mouth, *, company_layout=None):
    """Create or return the entry shaft room id for this mouth."""
    if mouth.get("shaft_room_id"):
        rid = mouth["shaft_room_id"]
        if rid in (mouth.get("rooms") or {}):
            return rid
    rid = "shaft"
    if company_layout:
        # Company template replaces virgin shaft with pre-seeded graph.
        for room_id, room_data in company_layout.items():
            mouth.setdefault("rooms", {})[room_id] = _normalize_room(room_id, room_data)
        mouth["shaft_room_id"] = company_layout.get("entry_id") or "office"
        _touch_activity(game, mouth)
        return mouth["shaft_room_id"]
    room = _normalize_room(rid, {
        "id": rid,
        "depth": 1,
        "support_rating": 0,
        "room_kind": "shaft",
        "exits": {"up": {"carved": True}},
    })
    mouth.setdefault("rooms", {})[rid] = room
    mouth["shaft_room_id"] = rid
    _touch_activity(game, mouth)
    return rid


def link_rooms(mouth, room_a_id, direction, room_b_id):
    """Bidirectional carved exit between two room records."""
    room_a = room_by_id(mouth, room_a_id)
    room_b = room_by_id(mouth, room_b_id)
    if not room_a or not room_b:
        return
    opp = OPPOSITE.get(direction)
    if not opp:
        return
    face_a = get_face(room_a, direction)
    face_b = get_face(room_b, opp)
    face_a["carved"] = True
    face_b["carved"] = True
    face_a["carve_hp"] = face_a.get("carve_hp_max", 100)
    face_b["carve_hp"] = face_b.get("carve_hp_max", 100)


def new_room_id(mouth):
    """Generate a unique room id within the mouth."""
    existing = set((mouth.get("rooms") or {}).keys())
    n = 1
    while f"r{n}" in existing:
        n += 1
    return f"r{n}"


def complete_carve(game, mouth_key, mouth, from_room_id, direction, miner_key):
    """Finish a carve: create destination room and link."""
    from_room = room_by_id(mouth, from_room_id)
    if not from_room:
        return None, "No such room."
    face = get_face(from_room, direction)
    if face.get("carved"):
        return room_by_id(mouth, face.get("dest_room_id")), "Already carved."
    dest_id = new_room_id(mouth)
    depth = int(from_room.get("depth") or 0) + (1 if direction == "down" else 0)
    depth = max(1, depth)
    dest = _normalize_room(dest_id, {
        "id": dest_id,
        "depth": depth,
        "support_rating": 0,
        "room_kind": "drift",
        "exits": {},
    })
    mouth.setdefault("rooms", {})[dest_id] = dest
    link_rooms(mouth, from_room_id, direction, dest_id)
    face["dest_room_id"] = dest_id
    _touch_activity(game, mouth)
    hooks.on_mine_face_cleared(
        type("Room", (), {"mouth_key": mouth_key, "room_id": dest_id})(),
        direction,
        miner_key,
        game,
    )
    return dest, "Face cleared."


def prune_idle_mouths(game, *, days=None):
    """Boot-heal: drop mouths with no rooms and no recent activity."""
    if days is None:
        try:
            days = int(os.environ.get("MINE_MOUTH_PRUNE_DAYS", "30"))
        except ValueError:
            days = 30
    cutoff = time.time() - (days * 86400)
    state = ensure_game_state(game)
    removed = []
    for key, mouth in list(state.items()):
        if carved_room_count(mouth) > 0:
            continue
        if float(mouth.get("last_activity_unix") or 0) > cutoff:
            continue
        if mouth.get("fixtures"):
            continue
        del state[key]
        removed.append(key)
    if removed:
        game._mine_graphs_dirty = True
    return removed


def boot_heal_mine_graphs(game):
    """Idempotent normalize + prune on boot."""
    state = ensure_game_state(game)
    game.mine_graphs = normalize_loaded(state)
    removed = prune_idle_mouths(game)
    return removed
