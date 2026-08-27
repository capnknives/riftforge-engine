"""
zone_hud.py -- player-safe interior zone DTO for the browser HUD.

Mirrors engine/map_hud.py for in-town / pocket rooms that have Area Studio
layout coordinates (layout_x / layout_y / layout_z). Never includes room
keys, VNUMs, hub_room, or plot: ids (player-facing-no-vnums).

Pushed as GMCP RiftForge.Zone from engine/gmcp.py when the client listed
RiftForge.Zone or RiftForge.Map. Telnet clients that skip those packages
never see this payload.
"""

from __future__ import annotations

import json

from engine import hooks
from engine import map_hud
from engine.room_vnum import internal_room_key, label_is_bare_vnum

# FIFO cap on persisted visited room keys (internal ids only).
VISITED_ROOM_CAP = 1500

# Stay under the 64 KB WebSocket / GMCP frame. Leave headroom for the
# envelope wrapper around this JSON.
_MAX_ZONE_JSON_BYTES = 60000

# Direction names on Room.exits -> compact DTO tokens (n/s/e/w plus
# vertical / in-out for the caption; the canvas only draws cardinals).
_DIR_SHORT = {
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
    "up": "u",
    "down": "d",
    "in": "in",
    "out": "out",
}

# Empty pane: open overland, vehicle interiors, rooms with no layout.
_HIDDEN_PAYLOAD = {
    "hidden": True,
    "title": "",
    "z": 0,
    "you": [0, 0],
    "rooms": [],
}


def _layout_xyz(room):
    """Return (x, y, z) ints when the room has a 2D layout, else None.

    layout_z omitted on older rooms counts as floor 0. layout_x / layout_y
    must both be present -- overland grid_x/grid_y is a different space.
    """
    if room is None:
        return None
    raw_x = getattr(room, "layout_x", None)
    raw_y = getattr(room, "layout_y", None)
    if raw_x is None or raw_y is None:
        return None
    raw_z = getattr(room, "layout_z", None)
    try:
        x = int(raw_x)
        y = int(raw_y)
        z = int(raw_z) if raw_z is not None else 0
    except (TypeError, ValueError):
        return None
    return x, y, z


def _short_dir(direction) -> str:
    """Compact exit token; unknown names pass through lowercased."""
    raw = str(direction or "").strip().lower()
    if not raw:
        return ""
    return _DIR_SHORT.get(raw, raw)


def _is_private_interior(room) -> bool:
    """True for houses / homes / private porches (fog until visited)."""
    return bool(
        getattr(room, "is_house", False)
        or getattr(room, "is_home", False)
        or getattr(room, "private_home", False)
    )


def _is_public_street(room, zone: str) -> bool:
    """Outdoor civic streets of this zone -- mapped without a prior visit."""
    if not zone:
        return False
    if str(getattr(room, "zone", None) or "") != zone:
        return False
    if not getattr(room, "outdoor", False):
        return False
    if _is_private_interior(room):
        return False
    return _layout_xyz(room) is not None


def normalize_visited_keys(raw) -> list:
    """Load-normalize a visited-key list (cap, strings, drop empties/dupes)."""
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    seen = set()
    for item in raw:
        key = str(item).strip() if item is not None else ""
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= VISITED_ROOM_CAP:
            break
    return out


def dump_visited_keys(character) -> list:
    """JSON-safe visited keys for persist_blob (capped FIFO)."""
    return normalize_visited_keys(
        getattr(character, "visited_room_keys", None)
    )


def record_visit(character) -> None:
    """Remember the room this character is standing in (internal key).

    Called from gmcp.push_room on look / move. The list is internal fog
    for the interior mapper -- never interpolated into player prose.
    Missing attr (bare-engine / basegame) is created on the character.
    """
    if character is None:
        return
    room = getattr(character, "location", None)
    if room is None:
        return
    key = internal_room_key(room) or str(getattr(room, "key", "") or "")
    key = key.strip()
    if not key:
        return
    keys = getattr(character, "visited_room_keys", None)
    if not isinstance(keys, list):
        keys = list(keys) if isinstance(keys, tuple) else []
    if key in keys:
        character.visited_room_keys = keys
        return
    keys.append(key)
    if len(keys) > VISITED_ROOM_CAP:
        keys = keys[-VISITED_ROOM_CAP:]
    character.visited_room_keys = keys


def _player_room_name(room) -> str:
    """look_title, never a storage key or VNUM code."""
    if room is None:
        return "Inside"
    name = ""
    if hasattr(room, "look_title"):
        try:
            name = str(room.look_title() or "").strip()
        except Exception:
            name = ""
    if not name:
        name = "Inside"
    key = str(getattr(room, "key", "") or "")
    if name == key or label_is_bare_vnum(name):
        return "Inside"
    return name


def _zone_title(here, area_room, z: int) -> str:
    """Human area name + interior/floor label -- never raw zone ids."""
    doc = None
    try:
        from engine import map_ui

        index = getattr(map_ui, "LAST_ZONE_DOC_BY_HUB_KEY", None) or {}
        for candidate in (here, area_room):
            if candidate is None:
                continue
            key = str(getattr(candidate, "key", "") or "")
            if key and key in index:
                doc = index.get(key)
                break
            legacy = str(getattr(candidate, "legacy_key", "") or "")
            if legacy and legacy in index:
                doc = index.get(legacy)
                break
    except Exception:
        doc = None
    visible = ""
    if isinstance(doc, dict):
        visible = str(doc.get("runtime_visible_as") or "").strip()
    if visible:
        base = visible
    else:
        base = _player_room_name(here)
        if not base or base == "Inside":
            base = "Inside"
    return f"{base} — interior (floor {z})"


def _visible_exit_tokens(room, game) -> list:
    """Exit direction shorts using the same visibility as Room.Info."""
    tokens = []
    seen = set()
    exits = getattr(room, "exits", None) or {}
    for direction, dest in exits.items():
        if dest is None:
            continue
        if not hooks.look_exit_visible(dest, game):
            continue
        token = _short_dir(direction)
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _is_zone_mouth(room, zone: str) -> bool:
    """True when this room leaves the current zone / atlas (no hub key)."""
    if getattr(room, "zone_exit", False):
        return True
    if getattr(room, "zone_exit_to", None) is not None:
        return True
    exits = getattr(room, "exits", None) or {}
    here_map = getattr(room, "map_id", None)
    for dest in exits.values():
        if dest is None:
            continue
        dest_zone = str(getattr(dest, "zone", None) or "")
        if zone and dest_zone and dest_zone != zone:
            return True
        if zone and not dest_zone:
            return True
        dest_map = getattr(dest, "map_id", None)
        if dest_map and here_map and dest_map != here_map:
            return True
        # Overland grid cell (grid coords, no interior layout).
        if (
            zone
            and getattr(dest, "grid_x", None) is not None
            and getattr(dest, "layout_x", None) is None
        ):
            return True
    return False


def _room_dto(room, game, zone: str) -> dict | None:
    """One player-safe room row, or None when it has no layout."""
    xyz = _layout_xyz(room)
    if xyz is None:
        return None
    x, y, _z = xyz
    area = getattr(room, "area_type", None) or "city"
    return {
        "x": x,
        "y": y,
        "n": _player_room_name(room),
        "e": str(area),
        "ex": _visible_exit_tokens(room, game),
        "m": _is_zone_mouth(room, zone),
    }


def _payload_size(payload: dict) -> int:
    """UTF-8 byte length of compact JSON (GMCP / WS frame budget)."""
    dumped = json.dumps(payload, separators=(",", ":"), default=str)
    return len(dumped.encode("utf-8"))


def _clip_rooms(rooms: list, you_xy, base: dict) -> list:
    """Shrink to a Chebyshev box around you until the payload fits."""
    if not rooms:
        return rooms
    you_x, you_y = you_xy
    payload = dict(base)
    payload["rooms"] = rooms
    if _payload_size(payload) <= _MAX_ZONE_JSON_BYTES:
        return rooms
    radius = 0
    for row in rooms:
        try:
            dx = abs(int(row["x"]) - you_x)
            dy = abs(int(row["y"]) - you_y)
        except (KeyError, TypeError, ValueError):
            continue
        if dx > radius:
            radius = dx
        if dy > radius:
            radius = dy
    while radius >= 0:
        clipped = []
        for row in rooms:
            try:
                dx = abs(int(row["x"]) - you_x)
                dy = abs(int(row["y"]) - you_y)
            except (KeyError, TypeError, ValueError):
                continue
            if max(dx, dy) <= radius:
                clipped.append(row)
        # Always keep the you-are-here row even if the box is tiny.
        if not any(
            row.get("x") == you_x and row.get("y") == you_y for row in clipped
        ):
            for row in rooms:
                if row.get("x") == you_x and row.get("y") == you_y:
                    clipped.append(row)
                    break
        payload["rooms"] = clipped
        if _payload_size(payload) <= _MAX_ZONE_JSON_BYTES or radius == 0:
            return clipped
        radius -= 1
    return rooms[:1]


def _candidate_rooms(game, here, zone: str, visited: set):
    """Rooms to consider for the mapper -- never a full ``game.rooms`` scan.

    Open overland has tens of thousands of cells. Iterating them on every
    look would freeze movement (the bug ``look`` just stopped doing).
    Settlement graphs use the paced-travel zone index; fog-only / no-zone
    rooms look up visited keys plus ``here``.
    """
    if zone:
        from engine.systems.paced_travel import rooms_in_zone

        return rooms_in_zone(game, zone)
    rooms = getattr(game, "rooms", None) or {}
    out = []
    seen = set()
    keys = list(visited)
    if here is not None:
        here_key = internal_room_key(here) or str(getattr(here, "key", "") or "")
        if here_key:
            keys.append(here_key)
    for key in keys:
        room = rooms.get(key) if isinstance(rooms, dict) else None
        if room is None:
            continue
        marker = id(room)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(room)
    if here is not None and id(here) not in seen:
        out.append(here)
    return out


def build_zone_payload(character, game) -> dict:
    """Build RiftForge.Zone JSON (always a dict; hidden when no layout)."""
    here = getattr(character, "location", None) if character is not None else None
    if here is None:
        payload = dict(_HIDDEN_PAYLOAD)
        map_hud._assert_player_safe(payload)
        return payload

    xyz = _layout_xyz(here)
    if xyz is None:
        payload = dict(_HIDDEN_PAYLOAD)
        map_hud._assert_player_safe(payload)
        return payload

    you_x, you_y, z = xyz
    from engine.systems import vehicles as vehicles_mod

    area_room = vehicles_mod.look_area_source_room(game, here)
    if area_room is None:
        area_room = here
    zone = str(getattr(area_room, "zone", None) or "")

    visited = set(normalize_visited_keys(
        getattr(character, "visited_room_keys", None)
    ))
    here_key = internal_room_key(here) or str(getattr(here, "key", "") or "")
    if here_key:
        visited.add(here_key)

    chosen = []
    seen_xy = set()
    saw_here = False
    for room in _candidate_rooms(game, here, zone, visited):
        if room is here:
            saw_here = True
        layout = _layout_xyz(room)
        if layout is None:
            continue
        rx, ry, rz = layout
        if rz != z:
            continue
        room_zone = str(getattr(room, "zone", None) or "")
        if zone:
            if room_zone != zone:
                continue
        else:
            # No zone string (rare hand rooms): only this cell + visited
            # peers on the same map_id -- never every outdoor room on Earth.
            here_map = getattr(here, "map_id", None)
            if getattr(room, "map_id", None) != here_map:
                continue
            room_key = internal_room_key(room) or str(
                getattr(room, "key", "") or ""
            )
            if room is not here and room_key not in visited:
                continue
        include = False
        if zone and _is_public_street(room, zone):
            include = True
        else:
            room_key = internal_room_key(room) or str(
                getattr(room, "key", "") or ""
            )
            if room_key and room_key in visited:
                include = True
        if room is here:
            include = True
        if not include:
            continue
        dto = _room_dto(room, game, zone)
        if dto is None:
            continue
        xy = (dto["x"], dto["y"])
        if xy in seen_xy:
            # Prefer the room the player is standing in on a coord clash.
            if room is not here:
                continue
        seen_xy.add(xy)
        chosen.append(dto)

    # You-are-here even if this room is missing from game.rooms (tests / races).
    if not saw_here:
        here_dto = _room_dto(here, game, zone)
        if here_dto is not None:
            xy = (here_dto["x"], here_dto["y"])
            if xy not in seen_xy:
                chosen.append(here_dto)

    base = {
        "hidden": False,
        "title": _zone_title(here, area_room, z),
        "z": z,
        "you": [you_x, you_y],
        "rooms": [],
    }
    base["rooms"] = _clip_rooms(chosen, (you_x, you_y), base)
    if not base["rooms"]:
        payload = dict(_HIDDEN_PAYLOAD)
        map_hud._assert_player_safe(payload)
        return payload
    map_hud._assert_player_safe(base)
    return base
