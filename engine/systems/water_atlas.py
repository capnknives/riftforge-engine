"""Earth playable water atlas (engine-only; no supers imports).

Sibling water maps share plane ``earth`` with ``earth_america`` but use
their own ``map_id`` grids. Depth band names are consumed by
``supers/water_endurance.py`` via engine hooks — this module owns ids,
geo seams, ephemeral rooms, and compass/depth hops.
"""

from __future__ import annotations

import json
import os

from engine import hooks as hooks_mod
from engine.world import Room

GULF_MAP_ID = "earth_gulf"
LAKES_MAP_ID = "earth_great_lakes"

GULF_PREFIX = "Gulf"
LAKES_PREFIX = "Great Lakes"

WATER_MAP_IDS = frozenset({GULF_MAP_ID, LAKES_MAP_ID})

# Max depth band labels per water body (index 0/1/2 in depth_rows).
BANDS_LAKE = ("surface", "mid", "lakebed")
BANDS_OFFSHORE = ("inshore", "slope", "abyss")

# Regional crop bounds (lon_lo, lon_hi, lat_lo, lat_hi) — mirror build scripts.
GULF_BOUNDS = (-98.0, -81.0, 24.4, 30.8)
GULF_WIDTH = 48
GULF_HEIGHT = 32
LAKES_BOUNDS = (-93.0, -76.0, 41.0, 49.0)
LAKES_WIDTH = 44
LAKES_HEIGHT = 32

_WATER_SHELF_HUBS = frozenset({"Gulf Inshore Shelf", "Lakes Surface Shelf"})

_DIR_DELTA = {
    "north": (0, 1),
    "south": (0, -1),
    "east": (1, 0),
    "west": (-1, 0),
    "northeast": (1, 1),
    "northwest": (-1, 1),
    "southeast": (1, -1),
    "southwest": (-1, -1),
}

_BAND_TITLES = {
    (GULF_MAP_ID, "inshore"): "Gulf Inshore Shelf",
    (GULF_MAP_ID, "slope"): "Gulf Slope",
    (GULF_MAP_ID, "abyss"): "Gulf Abyss",
    (LAKES_MAP_ID, "surface"): "Lakes Surface Shelf",
    (LAKES_MAP_ID, "mid"): "Lakes Midwater",
    (LAKES_MAP_ID, "lakebed"): "Lakes Lakebed",
}


def _content_maps_dir():
    """Repo ``content/maps`` regardless of cwd."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "content",
        "maps",
    )


def is_water_map_id(map_id) -> bool:
    """True for playable Earth water sibling atlases."""
    return str(map_id or "").strip().lower() in WATER_MAP_IDS


def water_kind_for_map(map_id) -> str:
    """Return ``lake`` or ``offshore`` for endurance policy."""
    mid = str(map_id or "").strip().lower()
    if mid == LAKES_MAP_ID:
        return "lake"
    return "offshore"


def default_band_for_map(map_id) -> str:
    """Shallow entry band when a seam does not specify depth."""
    if str(map_id or "").strip().lower() == LAKES_MAP_ID:
        return BANDS_LAKE[0]
    return BANDS_OFFSHORE[0]


def bands_for_map(map_id) -> tuple[str, ...]:
    """Ordered depth labels for one water map."""
    if str(map_id or "").strip().lower() == LAKES_MAP_ID:
        return BANDS_LAKE
    return BANDS_OFFSHORE


def is_water_atlas_room(room) -> bool:
    """True when the actor stands on a playable water-atlas cell."""
    if room is None:
        return False
    if bool(getattr(room, "water_atlas", False)):
        return True
    return is_water_map_id(getattr(room, "map_id", None))


def ensure_water_defaults(character):
    """Attach water-atlas fields if missing (idempotent)."""
    if not hasattr(character, "water_map_id"):
        character.water_map_id = None
    if not hasattr(character, "water_pos"):
        character.water_pos = None
    if not hasattr(character, "water_band"):
        character.water_band = None


def clear_water_coords(character):
    """Leave the water grid (America overland or classic zone)."""
    ensure_water_defaults(character)
    character.water_map_id = None
    character.water_pos = None
    character.water_band = None


def _load_water_atlas_data(map_id: str) -> dict:
    path = os.path.join(_content_maps_dir(), f"{map_id}.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_water_atlas(map_id: str):
    """Load one water OverlandAtlas (lazy import avoids cycles)."""
    from engine.systems.overland import OverlandAtlas

    return OverlandAtlas(_load_water_atlas_data(map_id))


def ensure_game_water_atlases(game):
    """Stamp ``game.water_atlases`` + ephemeral room cache (idempotent)."""
    if getattr(game, "_water_atlases_ready", False):
        return
    atlases = {}
    for map_id in (GULF_MAP_ID, LAKES_MAP_ID):
        atlases[map_id] = load_water_atlas(map_id)
    game.water_atlases = atlases
    game.water_rooms = {}
    game._water_atlases_ready = True


def _water_atlas(game, map_id: str):
    ensure_game_water_atlases(game)
    return game.water_atlases[map_id]


def _lonlat_in_bounds(lat: float, lon: float, bounds) -> bool:
    lon_lo, lon_hi, lat_lo, lat_hi = bounds
    return lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi


def _lonlat_to_regional(
    lat: float,
    lon: float,
    bounds,
    width: int,
    height: int,
) -> tuple[int, int] | None:
    """Map WGS84 onto a regional water grid (y=0 south)."""
    if not _lonlat_in_bounds(lat, lon, bounds):
        return None
    lon_lo, lon_hi, lat_lo, lat_hi = bounds
    x = int((lon - lon_lo) / (lon_hi - lon_lo) * width)
    y = int((lat - lat_lo) / (lat_hi - lat_lo) * height)
    x = max(0, min(width - 1, x))
    y = max(0, min(height - 1, y))
    return x, y


def _regional_to_lonlat(
    x: int,
    y: int,
    bounds,
    width: int,
    height: int,
) -> tuple[float, float]:
    """Cell center (lat, lon) for a regional water grid."""
    lon_lo, lon_hi, lat_lo, lat_hi = bounds
    lon = lon_lo + (int(x) + 0.5) / width * (lon_hi - lon_lo)
    lat = lat_lo + (int(y) + 0.5) / height * (lat_hi - lat_lo)
    return lat, lon


def _is_playable_water_cell(atlas, x: int, y: int) -> bool:
    """True when terrain is sim ocean/lake on a water atlas."""
    area = str(atlas.terrain_at(x, y) or "").strip().lower()
    return area in ("ocean", "lake")


def lonlat_to_water_cell(lat: float, lon: float) -> tuple[str, int, int] | None:
    """Pick gulf or lakes grid cell for a WGS84 point, or None."""
    # Great Lakes box sits north of the Gulf — check lakes first when both
    # could overlap (they should not in practice).
    lakes_xy = _lonlat_to_regional(
        lat, lon, LAKES_BOUNDS, LAKES_WIDTH, LAKES_HEIGHT,
    )
    if lakes_xy is not None:
        atlas = load_water_atlas(LAKES_MAP_ID)
        if _is_playable_water_cell(atlas, *lakes_xy):
            return LAKES_MAP_ID, lakes_xy[0], lakes_xy[1]
    gulf_xy = _lonlat_to_regional(
        lat, lon, GULF_BOUNDS, GULF_WIDTH, GULF_HEIGHT,
    )
    if gulf_xy is not None:
        atlas = load_water_atlas(GULF_MAP_ID)
        if _is_playable_water_cell(atlas, *gulf_xy):
            return GULF_MAP_ID, gulf_xy[0], gulf_xy[1]
    return None


def max_depth_band_index(atlas, x: int, y: int) -> int:
    """Highest depth band index allowed at (x, y) from ``depth_rows``."""
    cell = atlas.terrain.get((x, y)) or {}
    try:
        return int(cell.get("max_water_band", 0))
    except (TypeError, ValueError):
        return 0


def _band_index(map_id: str, band: str) -> int:
    bands = bands_for_map(map_id)
    try:
        return bands.index(band)
    except ValueError:
        return 0


def _band_title(map_id: str, band: str, x: int, y: int) -> str:
    """Player-facing room title — no map ids or raw coords."""
    titled = _BAND_TITLES.get((map_id, band))
    if titled:
        return titled
    if map_id == LAKES_MAP_ID:
        return "Great Lakes Water"
    return "Gulf Water"


def _band_description(map_id: str, band: str, x: int, y: int, atlas) -> str:
    """Short prose for one depth band."""
    cell = atlas.terrain.get((x, y)) or {}
    if cell.get("description"):
        return str(cell["description"])
    if map_id == LAKES_MAP_ID:
        if band == "surface":
            return (
                "Cold freshwater under a wide gray sky. The shore is a "
                "haze to the north."
            )
        if band == "mid":
            return "Green-gray lake water closes overhead. The bottom is still below."
        return "Silt and cold dark. Lakebed — no abyss trench here."
    if band == "inshore":
        return (
            "Brown Gulf water within sight of the coast. Salt haze and "
            "slow swell under open sky."
        )
    if band == "slope":
        return "The shelf drops away. Blue-green water and distant pressure."
    return "Black trench water. The abyss holds still and deep."


def _water_room_key(map_id: str, x: int, y: int, band: str) -> str:
    return f"water:{map_id}:{x},{y}:{band}"


def get_water_room(game, map_id: str, x: int, y: int, band: str):
    """Return (create if needed) the ephemeral Room for one depth band."""
    ensure_game_water_atlases(game)
    atlas = _water_atlas(game, map_id)
    band = str(band or default_band_for_map(map_id)).strip().lower()
    key = _water_room_key(map_id, x, y, band)
    existing = game.water_rooms.get(key)
    if existing is not None:
        return existing

    title = _band_title(map_id, band, x, y)
    desc = _band_description(map_id, band, x, y, atlas)
    room = Room(title, desc)
    room.game = game
    room.water_atlas = True
    room.virtual_overland = True
    room.map_id = map_id
    room.grid_prefix = atlas.prefix
    room.grid_x = x
    room.grid_y = y
    room.water_pos = (x, y)
    room.water_band = band
    room.area_type = atlas.terrain_at(x, y)
    room.outdoor = True
    room.wilderness = True
    room.plane = "earth"
    room.realm = "prime"
    room.zone = None
    shallow = band in (BANDS_LAKE[0], BANDS_OFFSHORE[0])
    room.lake_underwater = not shallow
    try:
        room.max_water_band = max_depth_band_index(atlas, x, y)
    except (TypeError, ValueError):
        room.max_water_band = 0
    # No self-loop exits — compass/depth use try_water_* helpers (like aerial).
    game.water_rooms[key] = room
    return room


def _send(character, message: str):
    session = getattr(character, "session", None)
    if session is not None:
        session.send(str(message))


def _refuse_water(character) -> bool:
    """Fire (and future hard gates) via hook — no supers import here."""
    ok, tell = hooks_mod.can_enter_water(character)
    if ok:
        return False
    if tell:
        _send(character, tell)
    return True


def place_on_water(character, game, map_id: str, x: int, y: int, band=None):
    """Materialize a water room; stamp foreign grid coords on the actor."""
    ensure_water_defaults(character)
    map_id = str(map_id or "").strip().lower()
    if not is_water_map_id(map_id):
        return False
    if _refuse_water(character):
        return False
    ensure_game_water_atlases(game)
    atlas = _water_atlas(game, map_id)
    x = max(0, min(int(x), atlas.width - 1))
    y = max(0, min(int(y), atlas.height - 1))
    if not _is_playable_water_cell(atlas, x, y):
        return False
    band = str(band or default_band_for_map(map_id)).strip().lower()
    ok, tell = hooks_mod.water_may_enter_band(
        character, water_kind_for_map(map_id), band,
    )
    if not ok:
        if tell:
            _send(character, tell)
        return False
    from engine.systems.overland import clear_overland_coords

    clear_overland_coords(character)
    clear_water_coords(character)
    character.water_map_id = map_id
    character.water_pos = (x, y)
    character.water_band = band
    room = get_water_room(game, map_id, x, y, band)
    character.move_to(room)
    return True


def _parse_pos_pair(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _dest_macro_after_step(macro, micro, direction):
    """Compute macro tile after one dual-layer foot step (overland shape)."""
    from engine.systems.overland import MICRO_SIZE, clamp_macro

    delta = _DIR_DELTA.get(direction)
    if delta is None:
        return None
    macro = _parse_pos_pair(macro)
    micro = _parse_pos_pair(micro)
    if macro is None or micro is None:
        return None
    dx, dy = delta
    ux, uy = micro[0] + dx, micro[1] + dy
    mx, my = macro
    if ux > MICRO_SIZE - 1:
        mx += 1
        ux = 0
    elif ux < 0:
        mx -= 1
        ux = MICRO_SIZE - 1
    if uy > MICRO_SIZE - 1:
        my += 1
        uy = 0
    elif uy < 0:
        my -= 1
        uy = MICRO_SIZE - 1
    if not clamp_macro(mx, my):
        return None
    return mx, my


def seam_dest_for_america_cell(game, mx: int, my: int):
    """If ``(mx, my)`` is void + HUD water_view, return water map coords."""
    from engine.systems.overland import ensure_game_overland
    from engine.atlas_geo import cell_to_lonlat

    ensure_game_overland(game)
    atlas = game.overland_atlas
    cell = atlas.terrain.get((mx, my)) or {}
    area = str(atlas.terrain_at(mx, my) or "").strip().lower()
    if area != "void" or not cell.get("water_view"):
        return None
    lat, lon = cell_to_lonlat(mx, my, atlas.width, atlas.height)
    hop = lonlat_to_water_cell(lat, lon)
    if hop is None:
        return None
    return hop[0], hop[1], hop[2]


def seam_dest(game, room, direction):
    """Foot seam from America toward water_view void — not America ocean."""
    from engine.systems.overland import is_real_overland_room

    if not is_real_overland_room(room):
        return None
    macro = getattr(room, "overland_macro", None)
    micro = getattr(room, "overland_micro", None)
    dest = _dest_macro_after_step(macro, micro, direction)
    if dest is None:
        return None
    hop = seam_dest_for_america_cell(game, dest[0], dest[1])
    if hop is None:
        return None
    return hop


def _return_to_america_coast(character, game, lat: float, lon: float):
    """Leave water onto the matching America land cell."""
    from engine import atlas_geo
    from engine.systems.overland import (
        LANDMARK_MICRO,
        clear_overland_coords,
        ensure_game_overland,
        place_on_overland,
    )

    ensure_game_overland(game)
    atlas = game.overland_atlas
    width, height = atlas.width, atlas.height
    mx, my = atlas_geo.lonlat_to_cell(
        lat, lon, width, height, projection="albers-conus",
    )
    mx, my = atlas_geo.snap_to_land_cell(game, mx, my)
    clear_water_coords(character)
    clear_overland_coords(character)
    place_on_overland(character, game, (mx, my), LANDMARK_MICRO)
    return True


def _water_bounds_for_map(map_id: str):
    if map_id == LAKES_MAP_ID:
        return LAKES_BOUNDS, LAKES_WIDTH, LAKES_HEIGHT
    return GULF_BOUNDS, GULF_WIDTH, GULF_HEIGHT


def try_water_compass_move(character, direction, game) -> bool:
    """N/S/E/W on a water atlas grid; return hop to America when leaving."""
    room = character.location
    if not is_water_atlas_room(room):
        return False
    direction = (direction or "").strip().lower()
    delta = _DIR_DELTA.get(direction)
    if delta is None:
        return False
    ensure_water_defaults(character)
    map_id = character.water_map_id or getattr(room, "map_id", None)
    pos = _parse_pos_pair(character.water_pos) or getattr(room, "water_pos", None)
    band = character.water_band or getattr(room, "water_band", None)
    if not map_id or pos is None or not band:
        return False
    if _refuse_water(character):
        return True
    ensure_game_water_atlases(game)
    atlas = _water_atlas(game, map_id)
    dx, dy = delta
    nx, ny = pos[0] + dx, pos[1] + dy
    if 0 <= nx < atlas.width and 0 <= ny < atlas.height:
        if _is_playable_water_cell(atlas, nx, ny):
            if place_on_water(character, game, map_id, nx, ny, band):
                _send(character, f"You swim {direction}.")
            return True
    bounds, width, height = _water_bounds_for_map(map_id)
    lat, lon = _regional_to_lonlat(pos[0], pos[1], bounds, width, height)
    if _return_to_america_coast(character, game, lat, lon):
        _send(character, "You stroke for shore and climb onto dry land.")
        return True
    _send(character, "You have reached the edge of the water.")
    return True


def _adjacent_band(map_id: str, band: str, deeper: bool) -> str | None:
    bands = bands_for_map(map_id)
    idx = _band_index(map_id, band)
    if deeper:
        nxt = idx + 1
    else:
        nxt = idx - 1
    if nxt < 0 or nxt >= len(bands):
        return None
    return bands[nxt]


def try_water_down(character, game) -> bool:
    """Descend one depth band when endurance allows."""
    room = character.location
    if not is_water_atlas_room(room):
        return False
    if _refuse_water(character):
        return True
    ensure_water_defaults(character)
    map_id = character.water_map_id or getattr(room, "map_id", None)
    pos = _parse_pos_pair(character.water_pos) or getattr(room, "water_pos", None)
    band = character.water_band or getattr(room, "water_band", None)
    if not map_id or pos is None or not band:
        return False
    target = _adjacent_band(map_id, band, deeper=True)
    if target is None:
        _send(character, "You cannot go deeper here.")
        return True
    ensure_game_water_atlases(game)
    atlas = _water_atlas(game, map_id)
    max_idx = max_depth_band_index(atlas, pos[0], pos[1])
    if _band_index(map_id, target) > max_idx:
        _send(character, "The bottom is not that deep here.")
        return True
    ok, tell = hooks_mod.water_may_enter_band(
        character, water_kind_for_map(map_id), target,
    )
    if not ok:
        _send(character, tell or "You cannot hold at that depth.")
        return True
    place_on_water(character, game, map_id, pos[0], pos[1], target)
    _send(character, "You dive deeper.")
    return True


def try_water_up(character, game) -> bool:
    """Rise one depth band toward the surface."""
    room = character.location
    if not is_water_atlas_room(room):
        return False
    ensure_water_defaults(character)
    map_id = character.water_map_id or getattr(room, "map_id", None)
    pos = _parse_pos_pair(character.water_pos) or getattr(room, "water_pos", None)
    band = character.water_band or getattr(room, "water_band", None)
    if not map_id or pos is None or not band:
        return False
    target = _adjacent_band(map_id, band, deeper=False)
    if target is None:
        _send(character, "You are already at the surface.")
        return True
    place_on_water(character, game, map_id, pos[0], pos[1], target)
    _send(character, "You rise toward the light.")
    return True


def try_water_vertical_move(character, direction, game) -> bool:
    """Dispatch ``down`` / ``up`` on water atlas rooms."""
    direction = (direction or "").strip().lower()
    if direction == "down":
        return try_water_down(character, game)
    if direction == "up":
        return try_water_up(character, game)
    return False


def america_room_water_cell(game, room):
    """Map an America mouth room to playable water grid coords."""
    from engine.systems.overland import america_macro_from_room, ensure_game_overland
    from engine.atlas_geo import cell_to_lonlat

    ensure_game_overland(game)
    macro = america_macro_from_room(room, game)
    if macro is None:
        return None
    atlas = game.overland_atlas
    lat, lon = cell_to_lonlat(macro[0], macro[1], atlas.width, atlas.height)
    return lonlat_to_water_cell(lat, lon)


def water_zone_enter_dest(character, dest, raw_enter, game):
    """``enter gulf`` / ``enter lakes`` from a coast mouth → playable grid."""
    if dest is None:
        return dest
    hub_key = str(getattr(dest, "key", "") or "")
    if hub_key not in _WATER_SHELF_HUBS:
        return dest
    if _refuse_water(character):
        return dest
    mouth = character.location
    hop = america_room_water_cell(game, mouth)
    if hop is None:
        if hub_key == "Gulf Inshore Shelf":
            hop = (GULF_MAP_ID, 24, 28)
        else:
            hop = (LAKES_MAP_ID, 22, 16)
    map_id, x, y = hop
    band = default_band_for_map(map_id)
    if not place_on_water(character, game, map_id, x, y, band):
        return dest
    return character.location
