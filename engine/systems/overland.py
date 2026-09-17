"""
overland.py -- dual-layer US overland (Finalmap x earth_america).

Macro layer: 96x60 America Overland CONUS atlas (vehicles; pure coords).
Micro layer: virtual 10x10 wilderness per macro tile (on foot; never saved
as map rooms). Static zones stay classic Rooms entered at micro (5, 5).

America Overland grid Rooms still exist for atlas ``map`` / pocket wiring;
player *presence* on the dual layer uses ephemeral VirtualRooms keyed by
(macro_x, macro_y, micro_x, micro_y).

No networking. Stdlib only. Lives in engine/ so basegame and SUPERS can
share one implementation; games point maps.set_maps_dir() at their atlas JSON.
"""

from __future__ import annotations

import json
import os
import re

from engine import hooks as hooks_mod
from engine import map_ui
from engine import world_maps
from engine.world import Room

_FALLBACK_PLAZA_KEY = "Lebanon Square"
_FALLBACK_OVERLAND_HUB_KEY = "Main Street S9"
_FALLBACK_BUNKER_OVERLAND_KEY = "America Overland (44, 33)"


def _starter_keys():
    """Plaza / overland-hub / bunker-pad keys (game hook or Lebanon fallbacks)."""
    keys = hooks_mod.overland_starter_keys()
    if keys is not None:
        return keys
    return (
        _FALLBACK_PLAZA_KEY,
        _FALLBACK_OVERLAND_HUB_KEY,
        _FALLBACK_BUNKER_OVERLAND_KEY,
    )


def _next_overland_foot_direction(macro, micro, dest_macro):
    """Pick one N/S/E/W step on the dual layer toward ``dest_macro``.

    Returns a direction string, or None when already on the landmark
    micro of the destination tile. Kept in-engine so Cadence homeward
    walks without importing ``supers.walk``.
    """
    mx, my = macro
    ux, uy = micro
    tx, ty = dest_macro
    goal_micro = LANDMARK_MICRO
    if (mx, my) == (tx, ty):
        gx, gy = goal_micro
        if (ux, uy) == (gx, gy):
            return None
        if ux < gx:
            return "east"
        if ux > gx:
            return "west"
        if uy < gy:
            return "north"
        if uy > gy:
            return "south"
        return None
    dx = tx - mx
    dy = ty - my
    if abs(dx) >= abs(dy) and dx != 0:
        return "east" if dx > 0 else "west"
    if dy != 0:
        return "north" if dy > 0 else "south"
    return None

# Saved / stub virtual wilderness keys round-trip as plain Room titles.
_WILDERNESS_KEY_RE = re.compile(
    r"^Wilderness \((\d+),(\d+)\)/(\d+),(\d+)$"
)

# Live CONUS atlas size (content/maps/earth_america.json). load_earth_america_atlas()
# restamps these from the JSON so clamp_macro always tracks the file.
MACRO_WIDTH = 96
MACRO_HEIGHT = 60
MICRO_SIZE = 10
# City / landmark physical entrance sits at micro center.
LANDMARK_MICRO = (5, 5)
# America Overland key_prefix from earth_america.json (no leading "The").
AMERICA_PREFIX = "America Overland"
# Legacy room keys / help text sometimes used a "The " prefix.
_AMERICA_PREFIXES = frozenset({AMERICA_PREFIX, "The America Overland"})
EARTH_AMERICA_ID = "earth_america"
# Secret 1861 atlas — never adopt onto the live America dual-layer.
FRONTIERLAND_PREFIX = "Frontierland Overland"
FRONTIERLAND_MAP_ID = "earth_frontierland_1861"
FRONTIERLAND_PLANE = "earth_frontierland_1861"

# Cardinal deltas: +y north, +x east (same as maps.py grids).
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
# King-move steps for vehicle pathfind (n/ne/e/se/s/sw/w/nw).
_VEHICLE_STEPS = tuple(_DIR_DELTA.values())

# Terrain pools for virtual look (plain labels; color is decoration only).
_TERRAIN_BLURBS = {
    "plains": (
        "Open American ground stretches under a wide sky. "
        "Distant highway hum rides the wind."
    ),
    "mountains": (
        "Broken ridges and rock spines cut the horizon. "
        "The air thins and the footing turns mean."
    ),
    "forest": (
        "Timber closes in. Needles and leaf-litter mute every step."
    ),
    "lake": (
        "Water sheets out in cold light. The shore smells of mud and reeds."
    ),
    "ocean": (
        "Open water under a hard sky. There is nowhere solid to stand."
    ),
    "city": (
        "Approach roads braid toward a settlement. "
        "Signage and sodium glow mark the edge of town."
    ),
    "void": (
        "This cell is off the contiguous United States. "
        "The brass globe is how you leave the lower forty-eight."
    ),
}

# Water / lake / off-CONUS void block Impala travel (v1 road fantasy).
_VEHICLE_BLOCKED = frozenset({"ocean", "lake", "void", "water"})

# area_type / map_layer strings that count as paved or settled road for
# ``offroad_fraction`` when callers do not override ``road_areas``.
_DEFAULT_ROAD_AREAS = frozenset(
    {"highway", "mountain_highway", "city", "road"},
)


def _content_maps_dir():
    """Absolute path to the active maps/ directory (game-selectable)."""
    return world_maps.get_maps_dir()


def _parse_pos_pair(value):
    """Coerce [x,y] or (x,y) to a 2-tuple of ints, or None."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return (int(value[0]), int(value[1]))
        except (TypeError, ValueError):
            return None
    return None


def clamp_macro(x, y):
    """Return True when (x, y) is inside the live America atlas."""
    return 0 <= x < MACRO_WIDTH and 0 <= y < MACRO_HEIGHT


def coords_on_atlas(atlas, nx, ny):
    """True when ``(nx, ny)`` sits on ``atlas`` (America or 1861).

    ``clamp_macro`` follows whichever atlas last mutated the module
    globals. Scenic horse rides must clamp the camera they are actually
    on, or a Frontierland cruise can look like an America edge refuse.
    """
    if atlas is None:
        return clamp_macro(nx, ny)
    try:
        width = int(getattr(atlas, "width", None) or MACRO_WIDTH)
        height = int(getattr(atlas, "height", None) or MACRO_HEIGHT)
        return 0 <= int(nx) < width and 0 <= int(ny) < height
    except (TypeError, ValueError):
        return clamp_macro(nx, ny)


def overland_mode(character):
    """Return 'vehicle' | 'flying' | 'on_foot' | 'zone' for dual-layer routing.

    Vehicle: aboard with micro_pos None and macro_pos set.
    Flying: Stellar macro hover (is_flying, micro_pos None, not in vehicle).
    On foot: both coords set (virtual wilderness).
    Zone: everything else (classic Rooms).
    """
    macro = _parse_pos_pair(getattr(character, "macro_pos", None))
    micro = getattr(character, "micro_pos", None)
    if getattr(character, "in_vehicle", None) and macro is not None and micro is None:
        return "vehicle"
    if (
        bool(getattr(character, "is_flying", False))
        and macro is not None
        and micro is None
        and not getattr(character, "in_vehicle", None)
    ):
        return "flying"
    if macro is not None and _parse_pos_pair(micro) is not None:
        return "on_foot"
    return "zone"


def is_virtual_room(room):
    """True when this Room is an ephemeral dual-layer wilderness cell."""
    return bool(getattr(room, "virtual_overland", False))


def _is_frontierland_overland_grid(room):
    """True when ``room`` is a 1861 *atlas cell*, not a Sunrise street.

    Town / camp pockets share plane ``earth_frontierland_1861`` with the
    trail. Treating every 1861 room as a grid cell hid Samuel in the
    trail-mouth Rail Camp and skipped FY00023 (bug reports 1424 / 1425).
    """
    if room is None:
        return False
    prefix = str(getattr(room, "grid_prefix", None) or "").strip()
    if prefix == FRONTIERLAND_PREFIX:
        return True
    parsed = map_ui.parse_grid_key(getattr(room, "key", "") or "")
    if parsed and parsed[0] == FRONTIERLAND_PREFIX:
        return True
    if is_virtual_room(room):
        map_id = str(getattr(room, "map_id", None) or "").strip().lower()
        return map_id == FRONTIERLAND_MAP_ID
    return False


def _is_private_overland_grid(room):
    """True when ``room`` reuses dual-layer shape for a private grid.

    Demesne wilds and water atlases own their macro/micro coords -- they
    must never be mistaken for America or Frontierland CONUS indices.
    """
    if room is None:
        return False
    if bool(getattr(room, "demesne_id", None)):
        return True
    map_id = str(getattr(room, "map_id", None) or "").strip().lower()
    from engine.systems.water_atlas import is_water_map_id

    if is_water_map_id(map_id) or bool(getattr(room, "water_atlas", False)):
        return True
    return False


def _is_foreign_to_america_atlas(room):
    """Never treat this room's overland coords as America Overland indices."""
    return _is_private_overland_grid(room) or _is_frontierland_overland_grid(room)


def _is_foreign_overland_grid(room):
    """Backward-compatible alias for non-America overland grids.

    Callers that block *America* coord travel (``walk 44 32`` from a
    demesne or the 1861 pocket) should keep using this name. Adoption and
    virtual-room routing use the narrower helpers above instead.
    """
    return _is_foreign_to_america_atlas(room)


def is_real_overland_room(room):
    """True when this is a live cell of a shared CONUS atlas (America or 1861).

    Like :func:`is_virtual_room` but excludes private grids (demesnes,
    water) that reuse the same dual-layer shape. Prefer this over
    ``is_virtual_room`` whenever the caller is about to touch a plane's
    atlas or re-place the character on it.
    """
    return is_virtual_room(room) and not _is_private_overland_grid(room)


def is_aerial_room(room):
    """True when this Room is a Stellar macro-hover sky cell."""
    return bool(getattr(room, "aerial_overland", False))


def ensure_overland_defaults(character):
    """Attach dual-layer fields if missing (idempotent)."""
    if not hasattr(character, "macro_pos"):
        character.macro_pos = None
    if not hasattr(character, "micro_pos"):
        character.micro_pos = None
    if not hasattr(character, "overland_plane"):
        character.overland_plane = None


def stamp_overland_plane(character, plane=None, room=None):
    """Remember which atlas a dual-layer foot cell belongs to.

    Earth and 1861 wilderness rooms share the persist title
    ``Wilderness (mx,my)/ux,uy``. Copyover must not guess the century
    from that string alone.
    """
    ensure_overland_defaults(character)
    if plane is not None:
        character.overland_plane = str(plane).strip().lower() or None
        return
    loc = room if room is not None else getattr(character, "location", None)
    if loc is not None and is_real_overland_room(loc):
        character.overland_plane = _overland_plane_for_room(loc)
        return
    character.overland_plane = None


def clear_overland_coords(character):
    """Leave the dual layer (entering a static zone)."""
    ensure_overland_defaults(character)
    character.macro_pos = None
    character.micro_pos = None
    character.overland_plane = None


def parse_wilderness_room_key(key):
    """Parse ``Wilderness (mx,my)/ux,uy`` into ((mx, my), (ux, uy)) or None."""
    if not key:
        return None
    match = _WILDERNESS_KEY_RE.match(str(key).strip())
    if not match:
        return None
    mx, my, ux, uy = (int(match.group(i)) for i in range(1, 5))
    if not clamp_macro(mx, my):
        return None
    if not (0 <= ux < MICRO_SIZE and 0 <= uy < MICRO_SIZE):
        return None
    return ((mx, my), (ux, uy))


def america_macro_from_room(room, game=None):
    """Return America macro (x, y) for a grid / virtual / gate Room, or None."""
    if room is None:
        return None
    if _is_foreign_to_america_atlas(room):
        # Frontierland / demesne coords must never read as America cells.
        return None
    pair = _parse_pos_pair(getattr(room, "overland_macro", None))
    if pair is not None and clamp_macro(*pair):
        return pair
    # Authored America Overland (x, y) cell.
    parsed = map_ui.parse_grid_key(getattr(room, "key", "") or "")
    if parsed and parsed[0] in _AMERICA_PREFIXES:
        try:
            mx, my = int(parsed[1]), int(parsed[2])
        except (TypeError, ValueError, IndexError):
            return None
        if clamp_macro(mx, my):
            return (mx, my)
    # Wilderness title key (persist stub after reboot).
    wild = parse_wilderness_room_key(getattr(room, "key", "") or "")
    if wild is not None:
        return wild[0]
    # Gates of <visible_as> -- reverse-lookup the landmark macro.
    key = (getattr(room, "key", "") or "").strip()
    if key.startswith("Gates of ") and game is not None:
        ensure_game_overland(game)
        atlas = getattr(game, "overland_atlas", None)
        needle = key[len("Gates of "):].strip().lower()
        if atlas is not None and needle:
            for (mx, my), landmark in (atlas.landmarks or {}).items():
                visible = str(landmark.get("visible_as") or "").strip().lower()
                if visible and visible == needle:
                    return (mx, my)
    return None


def frontierland_macro_from_room(room, game=None):
    """Return Frontierland macro (x, y) for a grid / virtual Room, or None."""
    if room is None:
        return None
    if not _is_frontierland_overland_grid(room):
        return None
    pair = _parse_pos_pair(getattr(room, "overland_macro", None))
    if pair is not None and clamp_macro(*pair):
        return pair
    parsed = map_ui.parse_grid_key(getattr(room, "key", "") or "")
    if parsed and parsed[0] == FRONTIERLAND_PREFIX:
        try:
            mx, my = int(parsed[1]), int(parsed[2])
        except (TypeError, ValueError, IndexError):
            return None
        if clamp_macro(mx, my):
            return (mx, my)
    wild = parse_wilderness_room_key(getattr(room, "key", "") or "")
    if wild is not None:
        return wild[0]
    key = (getattr(room, "key", "") or "").strip()
    if key.startswith("Gates of ") and game is not None:
        ensure_game_frontierland(game)
        atlas = getattr(game, "frontierland_atlas", None)
        needle = key[len("Gates of "):].strip().lower()
        if atlas is not None and needle:
            for (mx, my), landmark in (atlas.landmarks or {}).items():
                visible = str(landmark.get("visible_as") or "").strip().lower()
                if visible and visible == needle:
                    return (mx, my)
    return None


def adopt_foot_overland_presence(character, game):
    """Put a character onto the dual-layer foot grid when standing on America.

    Mid-session paths (mission eject, taxi, GM goto, legacy pads) can leave
    a body on an America Overland Room *without* ``macro_pos`` / ``micro_pos``.
    Classic ``Room.exits`` then hop whole macros and skip the 10x10 micro
    layer; virtual rooms without coords self-loop and feel stuck.

    Returns True when the character is (now) on_foot on a virtual cell.
    Never deletes characters. No-op for vehicle mode / indoor zones.
    """
    if character is None or game is None:
        return False
    ensure_overland_defaults(character)
    ensure_game_overland(game)
    # Already dual-layer foot -- rebind the ephemeral room if needed.
    if overland_mode(character) == "on_foot":
        macro = _parse_pos_pair(character.macro_pos)
        micro = _parse_pos_pair(character.micro_pos)
        if macro and micro:
            loc = getattr(character, "location", None)
            plane = _overland_plane_for_room(loc) if loc is not None else "earth"
            place_on_overland(
                character, game, macro, micro, plane=plane,
            )
            return True
        return False
    # Aboard: do not yank drivers onto foot mid-cruise.
    if overland_mode(character) == "vehicle":
        return False
    if getattr(character, "in_vehicle", None):
        return False

    room = getattr(character, "location", None)
    if room is None:
        return False
    if _is_private_overland_grid(room):
        # Demesne / water grids own their own movement dispatch.
        return False

    # Live virtual cell missing coords (cleared mid-session) -- rehydrate.
    if is_virtual_room(room):
        macro = _parse_pos_pair(getattr(room, "overland_macro", None))
        micro = _parse_pos_pair(getattr(room, "overland_micro", None))
        if macro and micro:
            return bool(place_on_overland(character, game, macro, micro))
        return False

    # Wilderness (mx,my)/ux,uy stub after a reboot before heal ran.
    wild = parse_wilderness_room_key(getattr(room, "key", "") or "")
    if wild is not None:
        return bool(place_on_overland(character, game, wild[0], wild[1]))

    # Classic America Overland pad / Gates of … stub.
    macro = america_macro_from_room(room, game)
    if macro is not None:
        return bool(place_on_overland(character, game, macro, LANDMARK_MICRO))
    # 1861 Frontierland grid mouth -- same micro layer, separate atlas.
    fl_macro = frontierland_macro_from_room(room, game)
    if fl_macro is None:
        return False
    return bool(
        place_on_overland(
            character, game, fl_macro, LANDMARK_MICRO, plane=FRONTIERLAND_PLANE,
        )
    )


# ---------------------------------------------------------------------------
# Atlas (terrain + landmarks from earth_america.json)
# ---------------------------------------------------------------------------


class OverlandAtlas:
    """Read-only index of macro terrain and landmark pockets.

    Built once from the map JSON so vehicle / foot logic does not need a
    live America Overland Room for every query.
    """

    def __init__(self, data):
        """Stamp width/height/prefix/terrain/landmarks from one map dict."""
        grid = data.get("grid") or {}
        self.map_id = data.get("id") or EARTH_AMERICA_ID
        self.plane = str(data.get("plane") or "earth").strip().lower() or "earth"
        self.prefix = grid.get("key_prefix") or AMERICA_PREFIX
        self.width = int(grid.get("width") or MACRO_WIDTH)
        self.height = int(grid.get("height") or MACRO_HEIGHT)
        self.default_area = grid.get("area_type") or "ocean"
        self.bestiary_categories = list(grid.get("bestiary_categories") or [])
        self.terrain = {}
        from engine.atlas_layers import expand_grid_cell
        has_layers = bool(grid.get("terrain_rows") or grid.get("road_rows"))
        if has_layers:
            # Expand compact glyph rows so .terrain.get still sees highways
            # (Impala off-road vs asphalt uses map_layer on this dict).
            for y in range(self.height):
                for x in range(self.width):
                    override = expand_grid_cell(grid, x, y)
                    if not override:
                        continue
                    area = override.get("area_type") or self.default_area
                    cats = override.get("bestiary_categories")
                    if cats is None:
                        cats = self.bestiary_categories
                    self.terrain[(x, y)] = {
                        "area_type": area,
                        "description": override.get("description"),
                        "map_glyph": override.get("map_glyph"),
                        "map_layer": override.get("map_layer"),
                        "terrain_base": override.get("terrain_base"),
                        "highland": bool(override.get("highland")),
                        "title": override.get("title"),
                        "bestiary_categories": list(cats or []),
                        "water_view": bool(override.get("water_view")),
                        "land_view": bool(override.get("land_view")),
                        "max_water_band": override.get("max_water_band"),
                        "resources": list(override.get("resources") or []),
                    }
        else:
            for key, override in (grid.get("cell_overrides") or {}).items():
                parts = str(key).split(",")
                if len(parts) != 2:
                    continue
                try:
                    x, y = int(parts[0]), int(parts[1])
                except ValueError:
                    continue
                area = override.get("area_type") or self.default_area
                cats = override.get("bestiary_categories")
                if cats is None:
                    cats = self.bestiary_categories
                self.terrain[(x, y)] = {
                    "area_type": area,
                    "description": override.get("description"),
                    "map_glyph": override.get("map_glyph"),
                    "map_layer": override.get("map_layer"),
                    "terrain_base": override.get("terrain_base"),
                    "highland": bool(override.get("highland")),
                    "title": override.get("title"),
                    "bestiary_categories": list(cats or []),
                    "water_view": bool(override.get("water_view")),
                    "land_view": bool(override.get("land_view")),
                    "max_water_band": override.get("max_water_band"),
                    "resources": list(override.get("resources") or []),
                }
        # macro (x,y) -> landmark dict
        self.landmarks = {}
        for pocket in data.get("pockets") or []:
            at = pocket.get("at")
            if not (isinstance(at, (list, tuple)) and len(at) == 2):
                continue
            mx, my = int(at[0]), int(at[1])
            aliases = [
                str(a).strip().lower()
                for a in (pocket.get("enter_as") or [])
                if str(a).strip()
            ]
            self.landmarks[(mx, my)] = {
                "at": (mx, my),
                "hub_room": pocket.get("hub_room"),
                "enter_as": aliases,
                "visible_as": str(pocket.get("visible_as") or "").strip(),
                "kind": pocket.get("kind") or "landmark",
            }

    def terrain_at(self, mx, my):
        """Return area_type string for one macro cell."""
        cell = self.terrain.get((mx, my))
        if cell:
            return cell.get("area_type") or self.default_area
        return self.default_area

    def bestiary_at(self, mx, my):
        """Return bestiary category list for one macro cell (grid default)."""
        cell = self.terrain.get((mx, my))
        if cell and cell.get("bestiary_categories") is not None:
            return list(cell.get("bestiary_categories") or [])
        return list(self.bestiary_categories or [])

    def resources_at(self, mx, my):
        """Return resource tags stamped on one macro cell (may be empty)."""
        cell = self.terrain.get((mx, my)) or {}
        return list(cell.get("resources") or [])

    def description_at(self, mx, my):
        """Return authored cell description or a terrain pool blurb."""
        cell = self.terrain.get((mx, my)) or {}
        if cell.get("description"):
            blurb = cell["description"]
        else:
            area = self.terrain_at(mx, my)
            blurb = _TERRAIN_BLURBS.get(
                area,
                f"American overland at ({mx}, {my}).",
            )
        from engine.systems.mine_graph import with_mine_country_tell

        return with_mine_country_tell(
            blurb,
            self.terrain_at(mx, my),
            highland=bool(cell.get("highland")),
        )

    def landmark_at(self, mx, my):
        """Return landmark dict for this macro cell, or None."""
        return self.landmarks.get((mx, my))

    def find_landmark_by_alias(self, alias):
        """Resolve enter <alias> to a landmark dict, or None."""
        needle = (alias or "").strip().lower()
        if not needle:
            return None
        for landmark in self.landmarks.values():
            names = set(landmark.get("enter_as") or [])
            hub = landmark.get("hub_room")
            if hub:
                names.add(str(hub).lower())
            if needle in names:
                return landmark
            # Substring / startswith for short player typing.
            for name in names:
                if needle in name or name.startswith(needle):
                    return landmark
        return None


def _is_paved_road_cell(atlas, mx, my, road_areas=None):
    """True when a macro cell counts as on-road for vehicle routing."""
    if road_areas is None:
        road_areas = _DEFAULT_ROAD_AREAS
    if not clamp_macro(mx, my):
        return False
    if not _vehicle_can_enter(atlas, mx, my):
        return False
    cell = atlas.terrain.get((mx, my)) or {}
    area = atlas.terrain_at(mx, my)
    layer = str(cell.get("map_layer") or "").lower()
    return area in road_areas or layer in road_areas


def heal_vehicle_road_connectivity(atlas):
    """No-op: 8-way driving made diagonal/cardinal asphalt fillers obsolete.

    The old stamps filled every diagonal pair (and every 2-apart parallel
    pair) with extra highway cells -- a 30-mile tile became a 60-mile smear.
    Cars now step n/ne/e/se/s/sw/w/nw on the painted overlay.
    Kept so boot and smokes that still call this helper stay import-safe.
    """
    return 0


def load_earth_america_atlas():
    """Load OverlandAtlas from content/maps/earth_america.json.

    Also stamps MACRO_WIDTH / MACRO_HEIGHT so clamp_macro follows the
    live grid (96x60 CONUS after the silhouette swap; 78x18 fallback
    only until this loader runs).
    """
    global MACRO_WIDTH, MACRO_HEIGHT
    path = os.path.join(_content_maps_dir(), "earth_america.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    atlas = OverlandAtlas(data)
    heal_vehicle_road_connectivity(atlas)
    MACRO_WIDTH = int(atlas.width)
    MACRO_HEIGHT = int(atlas.height)
    return atlas


def ensure_game_overland(game):
    """Stamp atlas + virtual-room manager onto `game` (idempotent)."""
    if getattr(game, "_overland_ready", False):
        return
    game.overland_atlas = load_earth_america_atlas()
    game.overland_rooms = {}  # 4D key -> ephemeral Room
    game.overland_ground = {}  # 4D key -> [Item, ...]
    game._overland_ready = True


def load_frontierland_atlas():
    """Load OverlandAtlas from content/maps/earth_frontierland_1861.json."""
    path = os.path.join(_content_maps_dir(), "earth_frontierland_1861.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return OverlandAtlas(data)


def ensure_game_frontierland(game):
    """Stamp 1861 Frontierland atlas + virtual-room manager (idempotent)."""
    if getattr(game, "_frontierland_overland_ready", False):
        return
    game.frontierland_atlas = load_frontierland_atlas()
    # Isolated Prime-parity mouths (Rail Camp at Fossil Butte) sit off
    # the 1861 highland. Paint wagon trail so walk/ride/horse n can
    # actually reach them (bug reports 1423 / 1427).
    bridge_isolated_atlas_landmarks(game.frontierland_atlas)
    game.frontierland_rooms = {}
    game.frontierland_ground = {}
    game._frontierland_overland_ready = True


def frontierland_cell_key(mx, my):
    """Canonical Frontierland Overland Room key."""
    return f"{FRONTIERLAND_PREFIX} ({mx}, {my})"


def _overland_plane_for_room(room):
    """Atlas plane id for a stamped grid / virtual cell / 1861 town room."""
    if room is None:
        return "earth"
    plane = str(getattr(room, "plane", None) or "").strip().lower()
    if plane == FRONTIERLAND_PLANE:
        return FRONTIERLAND_PLANE
    map_id = str(getattr(room, "map_id", None) or "").strip().lower()
    if map_id == FRONTIERLAND_MAP_ID:
        return FRONTIERLAND_PLANE
    if _is_frontierland_overland_grid(room):
        return FRONTIERLAND_PLANE
    return "earth"


def _atlas_bundle_for_plane(game, plane):
    """Return (atlas, ephemeral_rooms, ground_stash) for one plane."""
    if str(plane or "").strip().lower() == FRONTIERLAND_PLANE:
        ensure_game_frontierland(game)
        return (
            game.frontierland_atlas,
            game.frontierland_rooms,
            game.frontierland_ground,
        )
    ensure_game_overland(game)
    return game.overland_atlas, game.overland_rooms, game.overland_ground


def _atlas_bundle_for_room(room, game):
    """Resolve atlas storage from a Room's plane / map stamps."""
    return _atlas_bundle_for_plane(game, _overland_plane_for_room(room))


def _atlas_bundle_for_character(character, game):
    """Resolve atlas storage from the actor's current location."""
    loc = getattr(character, "location", None)
    if loc is not None:
        return _atlas_bundle_for_room(loc, game)
    ensure_game_overland(game)
    return game.overland_atlas, game.overland_rooms, game.overland_ground


def atlas_for_character(character, game):
    """Return the CONUS OverlandAtlas the viewer is standing on.

    1861 Frontierland is a second camera (same 96x60 cells, wagon trails).
    Web / Mudlet HUDs must switch to that atlas while the body is on that
    plane -- never keep painting live America cities over the 1861 @.
    """
    if game is None:
        return None
    loc = getattr(character, "location", None) if character is not None else None
    if loc is not None and (
        _is_frontierland_overland_grid(loc)
        or str(getattr(loc, "plane", "") or "").strip().lower()
        == FRONTIERLAND_PLANE
        or str(getattr(loc, "map_id", "") or "").strip().lower()
        == FRONTIERLAND_MAP_ID
    ):
        ensure_game_frontierland(game)
        return getattr(game, "frontierland_atlas", None)
    return getattr(game, "overland_atlas", None)


def _apply_atlas_paint_to_room(room, cell):
    """Copy HUD overlay stamps from an atlas terrain dict onto a Room."""
    cell = cell or {}
    glyph = str(cell.get("map_glyph") or "").strip()
    if glyph:
        room.map_glyph = glyph[0]
    layer = str(cell.get("map_layer") or "").strip().lower()
    if layer:
        room.map_layer = layer
    base = str(cell.get("terrain_base") or "").strip().lower()
    if base:
        room.terrain_base = base
    room.water_view = bool(cell.get("water_view"))
    room.land_view = bool(cell.get("land_view"))
    room.highland = bool(cell.get("highland"))
    resources = list(cell.get("resources") or [])
    if resources:
        room.resources = resources


def _quad_key(macro, micro):
    """Stable tuple key for virtual rooms / ground stash."""
    mx, my = macro
    ux, uy = micro
    return (int(mx), int(my), int(ux), int(uy))


# ---------------------------------------------------------------------------
# Virtual rooms
# ---------------------------------------------------------------------------


def _proximity_line(micro, landmark):
    """Flavor when on a landmark macro but not yet at the gate."""
    if landmark is None:
        return None
    ux, uy = micro
    gx, gy = LANDMARK_MICRO
    # Chebyshev distance to the gate cell.
    dist = max(abs(ux - gx), abs(uy - gy))
    name = landmark.get("visible_as") or "a settlement"
    if dist == 0:
        return None  # enter exit covers arrival
    if dist == 1:
        return f"The massive gates of {name} loom just ahead of you."
    if dist > 3:
        return f"You see the faint glow of {name} in the distance."
    return f"The approach to {name} grows clearer with each step."


# Cardinal / intercardinal names for nearby-zone bearings (plain labels).
# Matches ``_DIR_DELTA``: +y is north, +x is east on the America atlas.
_BEARING_NAMES = {
    (0, 1): "north",
    (0, -1): "south",
    (1, 0): "east",
    (-1, 0): "west",
    (1, 1): "northeast",
    (-1, 1): "northwest",
    (1, -1): "southeast",
    (-1, -1): "southwest",
}

# How far (macro Chebyshev) a settlement may be and still show on look.
NEARBY_LANDMARK_MACRO_RANGE = 3
# Cap so a dense dungeon belt does not bury Lebanon / bunker tells.
NEARBY_LANDMARK_MAX_LINES = 4


def _bearing_name(dx, dy):
    """Map integer delta to a compass word, or None if no displacement."""
    if dx == 0 and dy == 0:
        return None
    sx = 0 if dx == 0 else (1 if dx > 0 else -1)
    sy = 0 if dy == 0 else (1 if dy > 0 else -1)
    return _BEARING_NAMES.get((sx, sy))


def _landmark_display_name(landmark):
    """Player-facing settlement name (never color-alone)."""
    name = (landmark or {}).get("visible_as") or ""
    name = str(name).strip()
    if name:
        return name
    aliases = (landmark or {}).get("enter_as") or []
    if aliases:
        return str(aliases[0]).strip()
    hub = (landmark or {}).get("hub_room")
    return str(hub).strip() if hub else "a settlement"


def nearby_landmark_bearing_lines(atlas, macro, micro):
    """Plain-text compass lines for settlements near this foot cell.

    Example: ``North: the Men of Letters bunker.`` / ``South: Lebanon,
    Kansas.`` Used by virtual look (sighted + screenreader) so players
    always see which way town / bunker / city hubs lie -- not color alone.
    """
    if atlas is None:
        return []
    mx, my = macro
    ux, uy = _parse_pos_pair(micro) or (LANDMARK_MICRO[0], LANDMARK_MICRO[1])
    # Fine position in macro units (gate sits at micro center).
    here_x = float(mx) + (float(ux) + 0.5) / float(MICRO_SIZE)
    here_y = float(my) + (float(uy) + 0.5) / float(MICRO_SIZE)
    scored = []
    for (lmx, lmy), landmark in (atlas.landmarks or {}).items():
        # Prefer town / bunker hubs over every dungeon mouth when crowded.
        kind = str(landmark.get("kind") or "landmark").lower()
        # Always include ordinary landmarks; dungeons only when very close.
        cheb = max(abs(int(lmx) - int(mx)), abs(int(lmy) - int(my)))
        if kind == "dungeon" and cheb > 1:
            continue
        if cheb > NEARBY_LANDMARK_MACRO_RANGE:
            continue
        gate_x = float(lmx) + (float(LANDMARK_MICRO[0]) + 0.5) / float(
            MICRO_SIZE
        )
        gate_y = float(lmy) + (float(LANDMARK_MICRO[1]) + 0.5) / float(
            MICRO_SIZE
        )
        dx = gate_x - here_x
        dy = gate_y - here_y
        # +y is north on the America atlas (same as _DIR_DELTA).
        bearing = _bearing_name(dx, dy)
        name = _landmark_display_name(landmark)
        if bearing is None:
            # Standing on the landmark macro at / near the gate.
            if (ux, uy) == LANDMARK_MICRO:
                continue  # enter line already covers arrival
            # Same tile, not at gate -- proximity_line covers flavor; still
            # give a compass toward the gate for screenreader Paths parity.
            bearing = _bearing_name(
                float(LANDMARK_MICRO[0]) - float(ux),
                float(LANDMARK_MICRO[1]) - float(uy),
            )
            if bearing is None:
                continue
            scored.append(
                (0, 0.0, f"{bearing.title()}: {name} (gates).")
            )
            continue
        dist = (dx * dx + dy * dy) ** 0.5
        # Prefer non-dungeon hubs (Lebanon / bunker) over dungeon mouths.
        priority = 0 if kind != "dungeon" else 1
        scored.append((priority, dist, f"{bearing.title()}: {name}."))
    scored.sort(key=lambda row: (row[0], row[1]))
    lines = []
    for row in scored[:NEARBY_LANDMARK_MAX_LINES]:
        lines.append(row[-1])
    return lines


def virtual_exit_dest_label(room, direction, game=None):
    """Look / Paths label for one virtual wilderness exit.

    Cardinal exits on ephemeral rooms point at the same Room object, so
    the default look_title repeats uselessly. Instead name what that
    step approaches (next micro cell, or a nearby settlement).
    """
    if not is_real_overland_room(room):
        return None
    direction = (direction or "").strip().lower()
    delta = _DIR_DELTA.get(direction)
    if delta is None:
        return None
    macro = getattr(room, "overland_macro", None)
    micro = getattr(room, "overland_micro", None)
    if macro is None or micro is None:
        return None
    mx, my = macro
    ux, uy = micro
    dx, dy = delta
    nx, ny = ux + dx, uy + dy
    # Micro edge cross -> neighboring macro (match try_overland_move).
    if nx > MICRO_SIZE - 1:
        mx += 1
        nx = 0
    elif nx < 0:
        mx -= 1
        nx = MICRO_SIZE - 1
    if ny > MICRO_SIZE - 1:
        my += 1
        ny = 0
    elif ny < 0:
        my -= 1
        ny = MICRO_SIZE - 1
    n_macro = (mx, my)
    if not clamp_macro(*n_macro):
        return "map edge (hard bounce)"
    atlas = None
    if game is not None:
        atlas = _atlas_bundle_for_room(room, game)[0]
    landmark = atlas.landmark_at(*n_macro) if atlas is not None else None
    area = atlas.terrain_at(*n_macro) if atlas is not None else "wilderness"
    if landmark and (nx, ny) == LANDMARK_MICRO:
        name = _landmark_display_name(landmark)
        return f"gates of {name}"
    if landmark:
        # Only "toward" when this step closes Chebyshev distance to the gate.
        # Walking away used to still say "toward Lebanon" for every exit,
        # which made micro progress feel stuck.
        ox, oy = micro
        gx, gy = LANDMARK_MICRO
        before = max(abs(ox - gx), abs(oy - gy))
        after = max(abs(nx - gx), abs(ny - gy))
        name = _landmark_display_name(landmark)
        if after < before:
            return f"toward {name}"
        if after > before:
            return f"wilderness ({area})"
        return f"along the approach to {name}"
    # Same-macro step with a landmark somewhere on this tile.
    here_macro = getattr(room, "overland_macro", None)
    here_lm = (
        atlas.landmark_at(*here_macro)
        if atlas is not None and here_macro is not None
        else None
    )
    if here_lm and (nx, ny) == LANDMARK_MICRO:
        return f"gates of {_landmark_display_name(here_lm)}"
    if here_lm:
        # Step that closes distance to the gate.
        ox, oy = getattr(room, "overland_micro", (0, 0))
        gx, gy = LANDMARK_MICRO
        before = max(abs(ox - gx), abs(oy - gy))
        after = max(abs(nx - gx), abs(ny - gy))
        if after < before:
            return f"toward {_landmark_display_name(here_lm)}"
    return f"wilderness ({area})"


def look_nearby_zone_lines(room, game):
    """Extra look lines: nearby settlement bearings for virtual tiles."""
    if not is_real_overland_room(room):
        return []
    atlas = _atlas_bundle_for_room(room, game)[0]
    macro = getattr(room, "overland_macro", None)
    micro = getattr(room, "overland_micro", None)
    if atlas is None or macro is None or micro is None:
        return []
    lines = nearby_landmark_bearing_lines(atlas, macro, micro)
    if not lines:
        return []
    # Lead-in so screenreader Paths / prose both carry meaning.
    return ["Nearby zones:"] + lines


def get_virtual_room(game, macro, micro, *, plane="earth"):
    """Return (create if needed) the ephemeral Room for these 4D coords.

    Never written to map JSON / SQLite as a permanent room. Ground stash
    items are hydrated into contents on create.
    """
    mx, my = int(macro[0]), int(macro[1])
    ux, uy = int(micro[0]), int(micro[1])
    # 1861 authored grid cells are the SoT mouth. A virtual twin at the
    # same landmark micro left Samuel on one Rail Camp and the rider on
    # another -- gm goto vs player enter camp (bug reports 1424 / 1425).
    if str(plane or "").strip().lower() == FRONTIERLAND_PLANE and (
        ux, uy
    ) == LANDMARK_MICRO:
        persist = (getattr(game, "rooms", None) or {}).get(
            frontierland_cell_key(mx, my)
        )
        if persist is not None:
            return persist
    atlas, overland_rooms, overland_ground = _atlas_bundle_for_plane(
        game, plane,
    )
    key = _quad_key(macro, micro)
    existing = overland_rooms.get(key)
    if existing is not None:
        return existing
    area = atlas.terrain_at(mx, my)
    landmark = atlas.landmark_at(mx, my)
    title = f"Wilderness ({mx},{my})/{ux},{uy}"
    if landmark and (ux, uy) == LANDMARK_MICRO:
        visible = landmark.get("visible_as") or "the settlement"
        title = f"Gates of {visible}"

    desc_parts = [atlas.description_at(mx, my)]
    prox = _proximity_line((ux, uy), landmark)
    if prox:
        desc_parts.append(prox)
    # Nearby-zone bearings land in room_look_extras (look_nearby_zone_lines)
    # so cached virtual rooms stay fresh and look is not double-printed.
    if landmark and (ux, uy) == LANDMARK_MICRO:
        aliases = ", ".join(landmark.get("enter_as") or ["enter"])
        desc_parts.append(
            f"A marked approach stands open. Type enter <name> "
            f"(here: {aliases}) to go in."
        )

    room = Room(title, " ".join(desc_parts))
    # Stamp owning Game so Room.add registers Characters into
    # game.characters (engine/char_index) -- same as authored rooms.
    room.game = game
    room.virtual_overland = True
    room.overland_macro = (mx, my)
    room.overland_micro = (ux, uy)
    room.grid_prefix = atlas.prefix
    room.grid_x = mx
    room.grid_y = my
    room.area_type = area
    room.wilderness = area not in ("city",)
    room.outdoor = True
    if str(plane or "").strip().lower() == FRONTIERLAND_PLANE:
        room.plane = FRONTIERLAND_PLANE
    else:
        room.plane = "earth"
    room.realm = "prime"
    room.map_id = atlas.map_id
    room.zone = None
    _apply_atlas_paint_to_room(room, atlas.terrain.get((mx, my)))
    room.bestiary_categories = atlas.bestiary_at(mx, my)
    # Compass exits are virtual markers -- move handler ignores them and
    # recomputes from coords. Eight-way so look / screenreader list ne/nw
    # as well as n/s/e/w; blocked terrain still refuses at step time.
    for direction in _DIR_DELTA:
        room.exits[direction] = room  # placeholder; try_overland_move wins
    if landmark and (ux, uy) == LANDMARK_MICRO:
        hub_key = landmark.get("hub_room")
        hub = (getattr(game, "rooms", {}) or {}).get(hub_key) if hub_key else None
        if hub is not None:
            for alias in landmark.get("enter_as") or []:
                room.zone_entries[alias] = hub
            # Also allow the hub key lowercase.
            room.zone_entries[hub.key.lower()] = hub
    # Multi-pocket cells (e.g. bunker + wild den mouths) wire every alias
    # on the authored America Overland grid cell during map load -- copy
    # those entries so exit/look at micro 5,5 lists all valid enters.
    cell_key = f"{atlas.prefix} ({mx}, {my})"
    mouth = (getattr(game, "rooms", {}) or {}).get(cell_key)
    cell_entries = getattr(mouth, "zone_entries", None) if mouth is not None else None
    if (
        (ux, uy) == LANDMARK_MICRO
        and isinstance(cell_entries, dict)
        and cell_entries
    ):
        for alias, dest in cell_entries.items():
            if dest is not None:
                room.zone_entries[alias] = dest

    # Homestead v2: re-wire claimed micro mouths onto fresh virtual rooms.
    from engine import hooks
    hooks.on_virtual_room_created(game, room)

    # Hydrate dropped items for this 4D cell.
    for item in list(overland_ground.get(key) or []):
        if item not in room.contents:
            room.contents.append(item)
            item.location = room

    overland_rooms[key] = room
    return room


def resolve_wilderness_saved_room_key(game, room_key):
    """Materialize a dual-layer foot cell from a saved ``room_key``.

    Virtual wilderness rooms live in ``game.overland_rooms``, not
    ``game.rooms``. Without this, ``persistence._resolve_saved_room``
    registers a ``map_missing_stub`` that has no exits -- copyover /
    reboot looked like being stranded on a blank tile (bug report 502).
    """
    parsed = parse_wilderness_room_key(room_key)
    if parsed is None:
        return None
    macro, micro = parsed
    for plane in ("earth", FRONTIERLAND_PLANE):
        try:
            room = get_virtual_room(game, macro, micro, plane=plane)
            if room is not None:
                return room
        except Exception as exc:
            from engine import log_util

            log_util.ops(
                "overland",
                f"materialize failed key={room_key!r} plane={plane}",
                exc=exc,
            )
    return None


def resolve_virtual_overland_room_key(game, room_key):
    """Materialize any saved dual-layer foot cell key (wilderness or gate).

    Foot cells use either ``Wilderness (mx,my)/ux,uy`` titles or ``Gates of
    {landmark}`` at the settlement micro center. Both live in
    ``game.overland_rooms`` only -- ``game.rooms.get`` misses them on
    copyover (bug report 647).
    """
    if game is None:
        return None
    key = str(room_key or "").strip()
    if not key:
        return None
    wild = resolve_wilderness_saved_room_key(game, key)
    if wild is not None:
        return wild
    if key.startswith("Gates of "):
        needle = key[len("Gates of "):].strip().lower()
        for plane in ("earth", FRONTIERLAND_PLANE):
            atlas = _atlas_bundle_for_plane(game, plane)[0]
            if atlas is None or not needle:
                continue
            for macro, landmark in (atlas.landmarks or {}).items():
                visible = str(landmark.get("visible_as") or "").strip().lower()
                if visible and visible == needle:
                    try:
                        return get_virtual_room(
                            game, macro, LANDMARK_MICRO, plane=plane,
                        )
                    except Exception as exc:
                        from engine import log_util

                        log_util.ops(
                            "overland",
                            f"materialize failed key={room_key!r}",
                            exc=exc,
                        )
                        return None
    for rooms in (
        getattr(game, "overland_rooms", None) or {},
        getattr(game, "frontierland_rooms", None) or {},
    ):
        for candidate in rooms.values():
            if (getattr(candidate, "key", None) or "") == key:
                return candidate
    return None


def get_aerial_room(game, macro):
    """Return (create if needed) the sky Room hovering over a macro tile."""
    ensure_game_overland(game)
    atlas = game.overland_atlas
    mx, my = macro
    key = f"aerial:{mx},{my}"
    existing = game.overland_rooms.get(key)
    if existing is not None:
        return existing

    area = atlas.terrain_at(mx, my)
    landmark = atlas.landmark_at(mx, my)
    visible = (landmark or {}).get("visible_as") or f"({mx},{my})"
    title = f"Sky above {visible}"
    desc_parts = [
        f"You hang in open air above the {area} below -- "
        f"overland ({mx}, {my}) spreads under your boots.",
        "Cardinal moves carry you across the macro grid. "
        "Type fly to climb higher, descend to drop a tier, or land to settle.",
    ]
    if landmark:
        aliases = ", ".join(landmark.get("enter_as") or ["enter"])
        desc_parts.append(
            f"Settlement gates wait below ({aliases}) -- land before entering."
        )

    room = Room(title, " ".join(desc_parts))
    room.game = game
    room.aerial_overland = True
    room.virtual_overland = True
    room.overland_macro = (mx, my)
    room.overland_micro = None
    room.grid_prefix = atlas.prefix
    room.grid_x = mx
    room.grid_y = my
    room.area_type = area
    room.wilderness = area not in ("city",)
    room.outdoor = True
    room.plane = "earth"
    room.realm = "prime"
    room.map_id = atlas.map_id
    room.zone = None
    room.highland = bool((atlas.terrain.get((mx, my)) or {}).get("highland"))
    room.bestiary_categories = atlas.bestiary_at(mx, my)
    for direction in ("north", "south", "east", "west"):
        room.exits[direction] = room
    game.overland_rooms[key] = room
    return room


def place_aerial_overland(character, game, macro):
    """Bind a flying Stellar above a macro tile (no micro layer)."""
    ensure_overland_defaults(character)
    ensure_game_overland(game)
    mx, my = macro
    if not clamp_macro(mx, my):
        return False
    character.macro_pos = (mx, my)
    character.micro_pos = None
    character.is_flying = True
    character.stellar_flight_macro = [mx, my]
    room = get_aerial_room(game, (mx, my))
    character.move_to(room)
    return True


def place_on_overland(character, game, macro, micro, *, plane=None):
    """Bind character to the dual layer at macro + micro (on foot)."""
    ensure_overland_defaults(character)
    if plane is None:
        loc = getattr(character, "location", None)
        plane = _overland_plane_for_room(loc) if loc is not None else "earth"
    _atlas_bundle_for_plane(game, plane)
    mx, my = macro
    # Vehicle macro cruises pass micro=None (aboard, not on foot). Default to
    # landmark center so tick_drives cannot crash unpacking None.
    pair = _parse_pos_pair(micro)
    if pair is None:
        pair = LANDMARK_MICRO
    ux, uy = pair
    atlas = None
    if str(plane or "").strip().lower() == FRONTIERLAND_PLANE:
        ensure_game_frontierland(game)
        atlas = getattr(game, "frontierland_atlas", None)
    else:
        ensure_game_overland(game)
        atlas = getattr(game, "overland_atlas", None)
    if atlas is not None:
        if not coords_on_atlas(atlas, mx, my):
            return False
    elif not clamp_macro(mx, my):
        return False
    if not (0 <= ux < MICRO_SIZE and 0 <= uy < MICRO_SIZE):
        return False
    character.macro_pos = (mx, my)
    character.micro_pos = (ux, uy)
    room = get_virtual_room(game, (mx, my), (ux, uy), plane=plane)
    stamp_overland_plane(character, plane=plane, room=room)
    character.move_to(room)
    return True


def sync_ground_stash(game, room):
    """Persist floor items from a virtual room into the plane ground stash."""
    if not is_virtual_room(room):
        return
    _, _, overland_ground = _atlas_bundle_for_room(room, game)
    macro = getattr(room, "overland_macro", None)
    micro = getattr(room, "overland_micro", None)
    if macro is None or micro is None:
        return
    key = _quad_key(macro, micro)
    from world import Item
    items = [obj for obj in room.contents if isinstance(obj, Item)]
    if items:
        overland_ground[key] = items
    elif key in overland_ground:
        del overland_ground[key]


def prune_virtual_room_if_empty(game, room):
    """Drop one ephemeral cell after the last body leaves it.

    Full ``prune_empty_virtual_rooms`` used to run after every foot hop and
    walked every virtual cell. Nest-helper Cadence ``npc_do`` north billed
    that whole scan to ``try_dir`` (Azure ~4s).
    """
    if game is None or room is None or not is_virtual_room(room):
        return
    _, overland_rooms, _ = _atlas_bundle_for_room(room, game)
    macro = getattr(room, "overland_macro", None)
    micro = getattr(room, "overland_micro", None)
    if macro is None or micro is None:
        return
    key = _quad_key(macro, micro)
    live = overland_rooms.get(key)
    if live is None or live is not room:
        return
    sync_ground_stash(game, room)
    from world import Character
    if any(isinstance(o, Character) for o in room.contents):
        return
    if hooks_mod.overland_room_influenced(room, game):
        return
    overland_rooms.pop(key, None)


def prune_empty_virtual_rooms(game):
    """Drop ephemeral rooms with no characters (items stay in ground stash).

    Keeps pads that still carry active planar influence / invasion state so
    GM and tick code can resolve them by key after the last walker leaves.
    """
    ensure_game_overland(game)
    from world import Character
    dead = []
    for key, room in list(game.overland_rooms.items()):
        sync_ground_stash(game, room)
        has_char = any(isinstance(o, Character) for o in room.contents)
        if has_char:
            continue
        if hooks_mod.overland_room_influenced(room, game):
            continue
        dead.append(key)
    for key in dead:
        del game.overland_rooms[key]


def _prune_after_overland_hop(game, old_room):
    """O(1) prune of the cell just left -- never a full virtual-map walk.

    A once-per-tick full ``prune_empty_virtual_rooms`` here still billed
    ~4s to the first Cadence ``north`` of the heartbeat (Azure Wayne Pearson
    ``phases=try_dir:4400`` after look-skip). Sweep empty cells from the
    skippable ``overland_prune`` tick instead, 15ms at a time.
    """
    prune_virtual_room_if_empty(game, old_room)


def tick_prune_empty_virtual_rooms(game, *, max_ms=15.0):
    """Skippable heartbeat: drop empty America micro-cells, bounded wall.

    Round-robins ``game.overland_rooms`` so a large ephemeral map cannot
    freeze look. ``max_ms`` is a wall budget, not a completeness gate.
    """
    if game is None:
        return
    ensure_game_overland(game)
    rooms = getattr(game, "overland_rooms", None) or {}
    keys = list(rooms.keys())
    n = len(keys)
    if n == 0:
        return
    import time as _time

    t0 = _time.perf_counter()
    start = int(getattr(game, "_overland_prune_rr", 0) or 0) % n
    scanned = 0
    while scanned < n and (_time.perf_counter() - t0) * 1000.0 < max_ms:
        key = keys[(start + scanned) % n]
        prune_virtual_room_if_empty(game, rooms.get(key))
        scanned += 1
    game._overland_prune_rr = (start + scanned) % n


def _overland_auto_look(character, game, *, after_move=False):
    """Room dump after an overland hop -- live players only.

    Cadence ``npc_do`` attaches SilentSession (``session is not None``), so
    the old gate rebuilt look UI into a sink on every America micro-step.
    """
    from engine.npc_act import is_live_session

    if not is_live_session(getattr(character, "session", None)):
        return
    from engine.verbs.basic import cmd_look, _push_overland_map_gmcp

    cmd_look(character, "", game, after_move=after_move)
    _push_overland_map_gmcp(character, game)


# ---------------------------------------------------------------------------
# Movement
# ---------------------------------------------------------------------------


def _vehicle_can_enter(atlas, mx, my):
    """True when the Impala may roll onto this macro cell."""
    area = atlas.terrain_at(mx, my)
    return area not in _VEHICLE_BLOCKED


def pathfind_vehicle_macro(atlas, start, goal, *, cost_fn=None, max_steps=4000):
    """Find a driveable America-macro path from ``start`` to ``goal``.

    Eight directions (n/ne/e/se/s/sw/w/nw). Skips ocean/lake/void/water via
    ``_vehicle_can_enter``. Returns a list of ``(x, y)`` tiles **after**
    start through and including goal, or ``None`` when unreachable /
    inputs invalid. A diagonal interstate is one NE step -- not a cardinal
    detour through the fields.

    When ``cost_fn`` is ``None`` (default), uses unweighted BFS — fewest
    hops, identical hop-count policy to the original v1 behavior so existing
    callers (dispatch, taxi, charter legs, vehicle kit smokes) stay stable
    aside from the new diagonal steps.

    When ``cost_fn`` is provided, uses Dijkstra's algorithm (``heapq`` min-
    heap) so each 8-way step onto neighbor ``(nx, ny)`` adds
    ``cost_fn(atlas.terrain_at(nx, ny))`` to the path total. BFS always
    treats every hop as equal cost; Dijkstra is needed when terrain types
    have different weights (highway cheap, forest expensive, etc.).

    ``cost_fn`` contract: receives the **area_type string** returned by
    ``atlas.terrain_at(x, y)`` (e.g. ``"highway"``, ``"forest"``,
    ``"city"`` — not the full terrain cell dict). Must return a numeric
    step cost (typically ``>= 0``). Games supply their own policy via a
    lambda; this engine module stays game-agnostic.

    ``max_steps`` caps how many cells the search may **pop** from its
    frontier (BFS deque or Dijkstra heap) before giving up.
    """
    start = _parse_pos_pair(start)
    goal = _parse_pos_pair(goal)
    if start is None or goal is None:
        return None
    if not coords_on_atlas(atlas, *start) or not coords_on_atlas(atlas, *goal):
        return None
    if not _vehicle_can_enter(atlas, *start):
        return None
    if not _vehicle_can_enter(atlas, *goal):
        return None
    if start == goal:
        return []

    neighbors = _VEHICLE_STEPS
    came_from = {start: None}

    if cost_fn is None:
        # Standard BFS: queue of positions; came_from rebuilds the path.
        from collections import deque

        queue = deque([start])
        steps = 0
        while queue and steps < max_steps:
            steps += 1
            cur = queue.popleft()
            if cur == goal:
                break
            cx, cy = cur
            for dx, dy in neighbors:
                nx, ny = cx + dx, cy + dy
                nxt = (nx, ny)
                if nxt in came_from:
                    continue
                if not clamp_macro(nx, ny):
                    continue
                if not _vehicle_can_enter(atlas, nx, ny):
                    continue
                came_from[nxt] = cur
                queue.append(nxt)
    else:
        # Weighted shortest path: heapq.heappop always returns the lowest-
        # cost frontier cell first (like a priority queue). Stale heap
        # entries — pushed before we found a cheaper route — are skipped
        # when their stored cost exceeds cost_so_far[cur].
        import heapq

        cost_so_far = {start: 0}
        # Tuple order matters: heapq compares element[0] first (total cost).
        heap = [(0, start)]
        steps = 0
        while heap and steps < max_steps:
            steps += 1
            cur_cost, cur = heapq.heappop(heap)
            if cur_cost > cost_so_far.get(cur, float("inf")):
                continue
            if cur == goal:
                break
            cx, cy = cur
            for dx, dy in neighbors:
                nx, ny = cx + dx, cy + dy
                nxt = (nx, ny)
                if not clamp_macro(nx, ny):
                    continue
                if not _vehicle_can_enter(atlas, nx, ny):
                    continue
                # Charge for entering the destination cell's terrain.
                step_cost = cost_fn(atlas.terrain_at(nx, ny))
                new_cost = cur_cost + step_cost
                if new_cost < cost_so_far.get(nxt, float("inf")):
                    cost_so_far[nxt] = new_cost
                    came_from[nxt] = cur
                    heapq.heappush(heap, (new_cost, nxt))

    if goal not in came_from:
        return None
    # Rebuild goal -> start, then reverse to start-exclusive path.
    path = []
    node = goal
    while node is not None and node != start:
        path.append(node)
        node = came_from.get(node)
    path.reverse()
    return path


def bridge_isolated_macro(atlas, src_macro, dest_macro):
    """Paint a short trail through void so an isolated atlas mouth is reachable.

    Rail Camp sits on Fossil Butte's Prime cell. The 1861 highland
    silhouette leaves that cell a one-tile forest island, so walk/ride
    died with water in the way (bug reports 1423 / 1427). Scenic cruise
    and foot/horse compass share this corridor.

    ``src`` should be a cell on the connected landmass (Sunrise, a town).
    ``dest`` is the island mouth. No-op when a path already exists.
    """
    from collections import deque

    if atlas is None:
        return
    src = _parse_pos_pair(src_macro)
    dest = _parse_pos_pair(dest_macro)
    if src is None or dest is None:
        return
    if pathfind_vehicle_macro(atlas, src, dest) is not None:
        return
    width = int(getattr(atlas, "width", 0) or 0)
    height = int(getattr(atlas, "height", 0) or 0)
    steps = _VEHICLE_STEPS
    # Flood land reachable from the seed without crossing void.
    land = {src}
    queue = deque([src])
    while queue:
        x, y = queue.popleft()
        for dx, dy in steps:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            if (nx, ny) in land:
                continue
            if not _vehicle_can_enter(atlas, nx, ny):
                continue
            land.add((nx, ny))
            queue.append((nx, ny))
    # Walk void-only from the isolated dest until we touch that land.
    came = {dest: None}
    queue = deque([dest])
    hit = None
    while queue:
        x, y = queue.popleft()
        if (x, y) in land:
            hit = (x, y)
            break
        for dx, dy in steps:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            if (nx, ny) in came:
                continue
            area = atlas.terrain_at(nx, ny)
            if (nx, ny) not in land and area != "void":
                continue
            came[(nx, ny)] = (x, y)
            queue.append((nx, ny))
    if hit is None:
        return
    cell = hit
    while cell is not None:
        if not _vehicle_can_enter(atlas, *cell):
            atlas.terrain[cell] = dict(atlas.terrain.get(cell) or {})
            atlas.terrain[cell]["area_type"] = "trail"
        cell = came.get(cell)


def bridge_isolated_atlas_landmarks(atlas):
    """Connect every landmark to the largest landmass on this atlas.

    Picks the landmark whose land-flood is biggest (the continent), then
    paints void trails to every other mouth. Idempotent when paths exist.
    """
    if atlas is None:
        return
    marks = list((getattr(atlas, "landmarks", None) or {}).keys())
    if len(marks) < 2:
        return
    best_src = None
    best_size = -1
    for mx, my in marks:
        if not _vehicle_can_enter(atlas, mx, my):
            continue
        # Flood size: how much land this mouth can already reach.
        size = 0
        from collections import deque

        seen = {(mx, my)}
        queue = deque([(mx, my)])
        while queue:
            x, y = queue.popleft()
            size += 1
            for dx, dy in _VEHICLE_STEPS:
                nx, ny = x + dx, y + dy
                if (nx, ny) in seen:
                    continue
                if not _vehicle_can_enter(atlas, nx, ny):
                    continue
                seen.add((nx, ny))
                queue.append((nx, ny))
        if size > best_size:
            best_size = size
            best_src = (mx, my)
    if best_src is None:
        return
    for dest in marks:
        bridge_isolated_macro(atlas, best_src, dest)


def offroad_fraction(atlas, path, road_areas=None):
    """Measure how much of a macro vehicle path is off-road.

    ``path`` is the same ``[(x, y), ...]`` list ``pathfind_vehicle_macro``
    returns (tiles after start through goal). For each cell, we read
    ``atlas.terrain_at(x, y)`` (area_type string) and, when present,
    ``atlas.terrain[(x, y)]["map_layer"]`` — matching how games already
    classify highway vs wilderness without importing game code here.

    A cell counts as **on-road** when its area_type **or** map_layer is in
    ``road_areas``. Default ``road_areas`` is ``_DEFAULT_ROAD_AREAS``
    (highway, mountain_highway, city, road).

    Returns ``(offroad_steps, total_steps, fraction)`` where
    ``fraction = offroad_steps / total_steps``. Empty paths return
    ``(0, 0, 0.0)`` to avoid division by zero.
    """
    if road_areas is None:
        road_areas = _DEFAULT_ROAD_AREAS
    total_steps = len(path or [])
    if total_steps == 0:
        return (0, 0, 0.0)

    offroad_steps = 0
    for x, y in path:
        area = atlas.terrain_at(x, y)
        cell = atlas.terrain.get((x, y)) or {}
        layer = str(cell.get("map_layer") or "").lower()
        on_road = area in road_areas or layer in road_areas
        if not on_road:
            offroad_steps += 1
    fraction = offroad_steps / total_steps
    return (offroad_steps, total_steps, fraction)


def mission_pocket_blocks_overland_move(character):
    """True when cardinal hops must use Room.exits, not the dual layer.

    Hunt strongholds stamp ``mission_instance`` on every pocket room.
    Cadence can still leave a body with ``macro_pos`` / ``micro_pos`` when
    dispatch uses ``move_to`` on a personal entrance (Gates shortcut) --
    without this guard, ``try_overland_move`` hijacks west/east into the
    America micro grid and ejects the hunter from the stronghold.
    """
    room = getattr(character, "location", None)
    return room is not None and getattr(room, "mission_instance", False)


def pit_pocket_blocks_overland_move(character):
    """True in generated Purgatory pit floors -- always use Room.exits.

    Runners can carry stale ``macro_pos`` / ``micro_pos`` from Earth travel
    into the pocket. Clear those coords so ``try_overland_move`` never
    hijacks compass steps away from the ephemeral spine graph.
    """
    room = getattr(character, "location", None)
    if room is None or not getattr(room, "purgatory_pit", False):
        return False
    clear_overland_coords(character)
    return True


def classic_zone_room_blocks_overland_move(character):
    """True when the body stands in a static zone room, not on the dual layer.

    Taxi drops, hospital admit, GM ``goto``, and plain ``move_to`` can leave
    stale ``macro_pos`` / ``micro_pos`` while the character is indoors (hotel
    guest room, asylum ward, etc.). ``overland_mode`` then reads ``on_foot``
    and ``try_overland_move`` hijacks ``down`` / ``out`` / numbered doors
    with "You can't go that way." instead of ``Room.exits`` (bug reports 44,
    254, 252).
    """
    room = getattr(character, "location", None)
    if room is None:
        return False
    if is_virtual_room(room) or is_aerial_room(room):
        return False
    if parse_wilderness_room_key(getattr(room, "key", "") or ""):
        return False
    parsed = map_ui.parse_grid_key(getattr(room, "key", "") or "")
    if parsed and parsed[0] in _AMERICA_PREFIXES:
        return False
    if parsed and parsed[0] == FRONTIERLAND_PREFIX:
        return False
    # Aboard on open America: vehicle cabins are classic zone rooms but
    # drivers steer via macro hops, not Room.exits (bug report 993).
    if getattr(character, "in_vehicle", None) and overland_mode(character) == "vehicle":
        return False
    return True


def resolve_character_road_macro(character, game):
    """America macro for taxi/drive distance from the actor's real location.

    Taxi drops, portal hops, and GM ``goto`` can leave a stale ``macro_pos``
    from an old overland hike while the body stands indoors in another town
    (bug report 1346: Grants Pass portal + seek mother still cruised ~38
    tiles from Lebanon). When the body is on the classic zone layer, ignore
    leftover coords and derive the mouth from the current room / zone hub.
    """
    ensure_overland_defaults(character)
    if not classic_zone_room_blocks_overland_move(character):
        macro = _parse_pos_pair(getattr(character, "macro_pos", None))
        if macro is not None:
            return macro
    room = getattr(character, "location", None)
    if room is None:
        return None
    zone_macro = current_zone_macro(game, room)
    if zone_macro is not None:
        return zone_macro
    stamped = _parse_pos_pair(getattr(room, "overland_exit_macro", None))
    if stamped is not None:
        return stamped
    return america_macro_from_room(room, game)


def _apply_relocate_hooks_before(character):
    """Cancel training / attune / recover before an overland step.

    Classic Room.exits moves run the same hooks from
    ``command_support._move_one``; dual-layer foot and flight hops bypass
    that path (bug report 388).
    """
    from engine.hooks import before_relocate

    msg = before_relocate(character)
    if msg and character.session is not None:
        character.session.send(msg)
    return getattr(character, "working", False)


def _apply_relocate_hooks_after(character, dest, game, was_working):
    """Post-arrival side effects (stop work, drag body, …) after overland."""
    from engine.hooks import after_arrive

    after_arrive(character, dest, game, was_working)


def try_overland_move(character, direction, game):
    """Handle N/S/E/W (and diagonals) while on the dual layer.

    Returns True when the move was handled (success or blocked message).
    Returns False when the caller should use classic Room.exits movement.
    """
    import time as _time
    from engine import lag_watch
    t_ov = _time.perf_counter()
    if mission_pocket_blocks_overland_move(character):
        return False
    if pit_pocket_blocks_overland_move(character):
        return False
    if classic_zone_room_blocks_overland_move(character):
        if overland_mode(character) != "zone":
            clear_overland_coords(character)
        return False
    ensure_overland_defaults(character)
    mode = overland_mode(character)
    # Legacy America pads / cleared virtual cells: adopt foot presence so
    # micro 10x10 + macro edge-cross run instead of classic Room.exits.
    if mode == "zone":
        if adopt_foot_overland_presence(character, game):
            mode = overland_mode(character)
        elif hooks_mod.overland_vehicle_compass_as_foot(character, game):
            # Horse on a persist atlas cell: mount cleared coords so
            # overland_mode is zone. Drop onto the 10x10 so n steers
            # instead of the cabin move-gate (bug reports 1427 / 1429).
            mode = overland_mode(character)
        else:
            return False
    if mode == "zone":
        return False
    atlas, _, _ = _atlas_bundle_for_character(character, game)
    delta = _DIR_DELTA.get(direction)
    if delta is None:
        # Vertical / named doors (down, up, out, 3a, …) belong to Room.exits.
        return False

    dx, dy = delta
    macro = _parse_pos_pair(character.macro_pos)
    if macro is None:
        return False

    if mode == "vehicle":
        # Game policy may drop a boarded rider onto the foot micro grid
        # (horses steer like walking). Cars stay on paced macro hops.
        if hooks_mod.overland_vehicle_compass_as_foot(character, game):
            mode = overland_mode(character)
        if mode == "vehicle":
            if hooks_mod.overland_queue_vehicle_macro_move(
                character, direction, game,
            ):
                lag_watch.stamp_move_phase(game, "ov_veh", t_ov)
                return True
            _send(character, "You cannot drive that way right now.")
            lag_watch.stamp_move_phase(game, "ov_veh", t_ov)
            return True

    if mode == "flying":
        nx, ny = macro[0] + dx, macro[1] + dy
        if not clamp_macro(nx, ny):
            _send(character, "You have reached the edge of the map.")
            return True
        old_room = character.location
        from command_support import _presence_face, is_staff_stealth_presence
        face = _presence_face(character)
        stealth = is_staff_stealth_presence(character)
        if old_room is not None and not stealth:
            old_room.broadcast(
                f"{face} flies {direction}.",
                exclude=character,
            )
        was_working = _apply_relocate_hooks_before(character)
        character.macro_pos = (nx, ny)
        character.stellar_flight_macro = [nx, ny]
        character.move_to(get_aerial_room(game, (nx, ny)))
        _apply_relocate_hooks_after(
            character, character.location, game, was_working,
        )
        _send(
            character,
            f"You fly {direction} -- now hovering over ({nx}, {ny}).",
        )
        if character.location is not None and not stealth:
            character.location.broadcast(
                f"{face} arrives from the {direction}.",
                exclude=character,
            )
        return True

    # On foot: micro step with macro edge-crossing (Finalmap).
    micro = _parse_pos_pair(character.micro_pos)
    if micro is None:
        # One more adopt attempt if coords were half-cleared.
        if adopt_foot_overland_presence(character, game):
            micro = _parse_pos_pair(character.micro_pos)
            macro = _parse_pos_pair(character.macro_pos) or macro
        if micro is None:
            return False
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
        _send(character, "You have reached the edge of the map.")
        return True

    from engine import hooks
    block_msg = hooks.blocked_foot_step(
        game, (mx, my), (ux, uy), character,
    )
    if block_msg:
        _send(character, block_msg)
        return True

    from engine.systems import water_atlas as water_atlas_mod

    on_frontierland = atlas.map_id == FRONTIERLAND_MAP_ID
    seam = (
        None
        if on_frontierland
        else water_atlas_mod.seam_dest_for_america_cell(game, mx, my)
    )
    if seam is not None:
        map_id, wx, wy = seam
        import time as _time
        from engine import lag_watch

        lag_watch.stamp_move_phase(game, "ov_setup", t_ov)
        old_room = character.location
        from command_support import _presence_face, is_staff_stealth_presence

        face = _presence_face(character)
        stealth = is_staff_stealth_presence(character)
        if old_room is not None and not stealth:
            old_room.broadcast(
                f"{face} wades toward the open water.",
                exclude=character,
            )
        was_working = _apply_relocate_hooks_before(character)
        if water_atlas_mod.place_on_water(character, game, map_id, wx, wy):
            new_room = character.location
            _apply_relocate_hooks_after(
                character, new_room, game, was_working,
            )
            if new_room is not None and not stealth:
                new_room.broadcast(
                    f"{face} arrives from the {_opposite(direction)}.",
                    exclude=character,
                )
            from command_support import _pull_followers_to

            _pull_followers_to(
                character, old_room, new_room, game, direction=direction,
            )
            _overland_auto_look(character, game, after_move=True)
            from engine.npc_act import is_live_session

            if is_live_session(getattr(character, "session", None)):
                hooks.encounter_check(game, new_room)
            _prune_after_overland_hop(game, old_room)
            hooks.after_move_step(character, direction, new_room, game)
            lag_watch.stamp_move_phase(game, "ov_place", t_ov)
            return True
        _send(character, "The water will not take you.")
        return True

    import time as _time
    from engine import lag_watch
    lag_watch.stamp_move_phase(game, "ov_setup", t_ov)
    t_place = _time.perf_counter()
    old_room = character.location
    from command_support import _presence_face, is_staff_stealth_presence
    face = _presence_face(character)
    stealth = is_staff_stealth_presence(character)
    if old_room is not None and not stealth:
        old_room.broadcast(
            f"{face} leaves to the {direction}.",
            exclude=character,
        )
    was_working = _apply_relocate_hooks_before(character)
    plane = _overland_plane_for_room(old_room) if old_room is not None else "earth"
    place_on_overland(character, game, (mx, my), (ux, uy), plane=plane)
    lag_watch.stamp_move_phase(game, "ov_place", t_place)
    new_room = character.location
    t_after = _time.perf_counter()
    _apply_relocate_hooks_after(character, new_room, game, was_working)
    lag_watch.stamp_move_phase(game, "ov_after", t_after)
    if new_room is not None and not stealth:
        new_room.broadcast(
            f"{face} arrives from the "
            f"{_opposite(direction)}.",
            exclude=character,
        )
    from command_support import _pull_followers_to

    _pull_followers_to(character, old_room, new_room, game, direction=direction)
    t_look = _time.perf_counter()
    _overland_auto_look(character, game, after_move=True)
    lag_watch.stamp_move_phase(game, "ov_look", t_look)
    # Same on-entry encounter roll classic Room.exits get via _move_one
    # (wilderness hostiles / procedural dungeons / aggro). Drive-layer
    # vehicle steps above skip this -- macro pads are wilderness:false.
    # Cadence ``npc_do`` attaches SilentSession; pathfind.step used to
    # skip cmd_move on wilderness so NPCs did not re-roll encounters
    # every hop. Call encounter only for live players.
    from engine import hooks as _hooks
    from engine.npc_act import is_live_session
    t_enc = _time.perf_counter()
    if is_live_session(getattr(character, "session", None)):
        _hooks.encounter_check(game, new_room)
    lag_watch.stamp_move_phase(game, "ov_enc", t_enc)
    t_prune = _time.perf_counter()
    _prune_after_overland_hop(game, old_room)
    lag_watch.stamp_move_phase(game, "ov_prune", t_prune)
    # Classic Room.exits moves call after_move_step from _move_one; on-foot
    # dual-layer hops bypass that path -- still burn wilderness hike exhaustion,
    # stamp tracks, and run move hooks (bug report 220).
    t_step = _time.perf_counter()
    _hooks.after_move_step(character, direction, new_room, game)
    lag_watch.stamp_move_phase(game, "ov_step", t_step)
    return True


def _opposite(direction):
    """Opposite compass word for arrive prose."""
    pairs = {
        "north": "south", "south": "north",
        "east": "west", "west": "east",
        "northeast": "southwest", "southwest": "northeast",
        "northwest": "southeast", "southeast": "northwest",
    }
    return pairs.get(direction, "distance")


def _send(character, text):
    """Send to a live Session if present."""
    session = getattr(character, "session", None)
    if session is not None:
        session.send(text)


# ---------------------------------------------------------------------------
# Zone enter / exit
# ---------------------------------------------------------------------------


def try_enter_landmark(character, args, game):
    """enter <alias> from micro (5,5) on a landmark macro.

    Returns True if handled (including failure messages).
    """
    if overland_mode(character) != "on_foot":
        return False
    atlas, _, _ = _atlas_bundle_for_character(character, game)
    macro = _parse_pos_pair(character.macro_pos)
    micro = _parse_pos_pair(character.micro_pos)
    if macro is None or micro is None:
        return False
    landmark = atlas.landmark_at(*macro)
    if landmark is None:
        return False
    raw = (args or "").strip()
    # Bare enter on a gate cell: list aliases.
    if not raw:
        if micro != LANDMARK_MICRO:
            _send(
                character,
                "No zone entrance here. Walk to the center of this "
                "tile (micro 5,5) near the settlement.",
            )
            return True
        aliases = landmark.get("enter_as") or []
        _send(
            character,
            "Enter which zone? Try: enter " + ", ".join(aliases[:8]),
        )
        return True
    # Must stand on the gate. Wrong micro with a named target: fall through
    # so classic zone_entries on the virtual room can still match (e.g.
    # ``enter bunker`` from the overland cell mouth).
    if micro != LANDMARK_MICRO:
        if raw:
            return False
        _send(
            character,
            "You need to reach the gates first "
            "(center of this overland tile).",
        )
        return True
    # Resolve alias -- prefer this cell's landmark, else any atlas match
    # that matches this cell.
    needle = raw.lower()
    aliases = set(landmark.get("enter_as") or [])
    hub_key = landmark.get("hub_room")
    if hub_key:
        aliases.add(str(hub_key).lower())
    matched = needle in aliases or any(
        needle in a or a.startswith(needle) for a in aliases
    )
    if not matched:
        # Let classic cmd_enter try zone_entries on the virtual room.
        return False
    hub = (getattr(game, "rooms", {}) or {}).get(hub_key)
    if hub is None:
        _send(character, "That settlement isn't open yet.")
        return True
    from engine.verbs.basic import _do_transition, stamp_zone_entry
    from engine.room_vnum import describe_room
    clear_overland_coords(character)
    stamp_zone_entry(character, hub)
    _do_transition(
        character, hub, game,
        f"{{name}} enters {describe_room(hub)}.",
        "{name} arrives.",
    )
    from engine import hooks as hooks_mod

    try:
        hooks_mod.note_zone_visit(character, game, room=hub)
    except Exception:
        pass
    hooks_mod.overland_notify_dungeon_hub(character, game, hub)
    return True


def _room_zone_exit_macro(room):
    """America macro (x, y) a ``zone_exit`` Room lets you step out onto.

    Prefers the stamped ``overland_exit_macro`` field; falls back to
    parsing the grid key off ``zone_exit_to`` (the linked overland mouth
    Room) when the macro tuple itself was never stamped. Returns ``None``
    when neither is present/parseable -- callers should treat that as "this
    room has no known overland exit point."
    """
    macro = _parse_pos_pair(getattr(room, "overland_exit_macro", None))
    if macro is not None:
        return macro
    dest = getattr(room, "zone_exit_to", None)
    if dest is not None:
        parsed = map_ui.parse_grid_key(dest.key)
        if parsed and parsed[0] in _AMERICA_PREFIXES:
            return (parsed[1], parsed[2])
        if parsed and parsed[0] == FRONTIERLAND_PREFIX:
            return (parsed[1], parsed[2])
    return None


def find_pocket_zone_exit(start):
    """Same-zone ``zone_exit`` mouth reachable from ``start``, or ``None``.

    Walks ``exits`` and ``zone_entries`` inside this pocket only -- O(the
    rooms you can actually walk), never a scan of every room in ``game``.
    Does not follow ``zone_exit_to`` onto the America grid; the mouth Room
    itself carries ``zone_exit``.
    """
    if start is None:
        return None
    if getattr(start, "zone_exit", False):
        return start
    zone = getattr(start, "zone", None)
    seen = {id(start)}
    queue = [start]
    idx = 0
    while idx < len(queue):
        room = queue[idx]
        idx += 1
        hops = []
        exits = getattr(room, "exits", None) or {}
        if isinstance(exits, dict):
            hops.extend(exits.values())
        entries = getattr(room, "zone_entries", None) or {}
        if isinstance(entries, dict):
            hops.extend(entries.values())
        for neighbor in hops:
            if neighbor is None or id(neighbor) in seen:
                continue
            neighbor_zone = getattr(neighbor, "zone", None)
            if zone and neighbor_zone and neighbor_zone != zone:
                continue
            seen.add(id(neighbor))
            if getattr(neighbor, "zone_exit", False):
                return neighbor
            queue.append(neighbor)
    return None


def current_zone_macro(game, room):
    """America macro (x, y) for the zone ``room`` sits in, or ``None``.

    Prefers a pocket BFS from ``room`` to that zone's ``zone_exit`` mouth
    (O(pocket)). Falls back to scanning ``game.rooms`` only when the mouth
    is not wired through exits -- atlas ``@`` still needs a cell then.
    """
    exit_room = find_pocket_zone_exit(room)
    if exit_room is not None:
        macro = _room_zone_exit_macro(exit_room)
        if macro is not None:
            return macro
    zone = getattr(room, "zone", None)
    if not zone:
        return None
    rooms = getattr(game, "rooms", None) or {}
    for candidate in rooms.values():
        if not getattr(candidate, "zone_exit", False):
            continue
        if getattr(candidate, "zone", None) != zone:
            continue
        macro = _room_zone_exit_macro(candidate)
        if macro is not None:
            return macro
    return None


def step_out_of_zone_to_road(character, game):
    """Put this body on the America foot grid for the pocket they stand in.

    Indoor / shrine rooms are not dual-layer cells, so
    ``adopt_foot_overland_presence`` is a no-op there. Find this pocket's
    ``zone_exit`` (O(pocket)), then land on that mouth's America cell.

    Returns True when this call newly placed them on the road.
    """
    if character is None or game is None:
        return False
    ensure_overland_defaults(character)
    ensure_game_overland(game)
    here = getattr(character, "location", None)
    if overland_mode(character) == "on_foot" or (
        here is not None and getattr(here, "virtual_overland", False)
    ):
        adopt_foot_overland_presence(character, game)
        return False
    if adopt_foot_overland_presence(character, game):
        return True
    exit_room = find_pocket_zone_exit(here)
    if exit_room is None:
        return False
    if here is exit_room and try_exit_to_overland(character, game):
        return True
    macro = _room_zone_exit_macro(exit_room)
    if macro is None:
        return False
    plane = _overland_plane_for_room(exit_room)
    return bool(
        place_on_overland(
            character, game, macro, LANDMARK_MICRO, plane=plane,
        )
    )


def try_exit_to_overland(character, game):
    """exit from a pocket mouth back onto micro (5,5) of its macro cell.

    Requires ``Room.zone_exit`` (and a macro destination via
    ``overland_exit_macro`` or ``zone_exit_to``). Returns True if handled.
    """
    room = character.location
    if room is None or not getattr(room, "zone_exit", False):
        return False
    macro = _room_zone_exit_macro(room)
    if macro is None:
        return False
    plane = _overland_plane_for_room(room)
    from command_support import _presence_face, is_staff_stealth_presence
    face = _presence_face(character)
    stealth = is_staff_stealth_presence(character)
    old = character.location
    if old is not None and not stealth:
        old.broadcast(
            f"{face} exits to the overland.",
            exclude=character,
        )
    place_on_overland(character, game, macro, LANDMARK_MICRO, plane=plane)
    new = character.location
    if new is not None and not stealth:
        new.broadcast(
            f"{face} arrives.",
            exclude=character,
        )
    _overland_auto_look(character, game)
    return True


def stamp_pocket_overland_exits(game):
    """Stamp overland_exit_macro onto earth_america pocket mouths only.

    Called after maps load so ``exit`` from a ``zone_exit`` hub can drop
    players onto virtual wilderness instead of the gateway cell Room.
    Side streets / house interiors are NOT stamped -- ``exit`` there must
    refuse (see Room.zone_exit).
    """
    ensure_game_overland(game)
    atlas = game.overland_atlas
    rooms = getattr(game, "rooms", {}) or {}
    from engine.room_vnum import lookup_room

    for (mx, my), landmark in atlas.landmarks.items():
        hub_key = landmark.get("hub_room")
        hub = lookup_room(game, hub_key)
        if hub is None:
            continue
        hub.overland_exit_macro = (mx, my)
        hub.zone_exit = True
        # Destination Room for non-America / classic exit path.
        cell_key = f"America Overland ({mx}, {my})"
        cell = rooms.get(cell_key)
        if cell is not None:
            hub.zone_exit_to = cell


def stamp_frontierland_pocket_overland_exits(game):
    """Stamp overland_exit_macro onto 1861 pocket mouths (Frontierland atlas)."""
    ensure_game_frontierland(game)
    atlas = game.frontierland_atlas
    rooms = getattr(game, "rooms", {}) or {}
    from engine.room_vnum import lookup_room

    for (mx, my), landmark in atlas.landmarks.items():
        hub_key = landmark.get("hub_room")
        hub = lookup_room(game, hub_key)
        if hub is None:
            continue
        hub.overland_exit_macro = (mx, my)
        hub.zone_exit = True
        cell_key = frontierland_cell_key(mx, my)
        cell = rooms.get(cell_key)
        if cell is not None:
            hub.zone_exit_to = cell


# ---------------------------------------------------------------------------
# Cadence homeward on the dual-layer foot grid
# ---------------------------------------------------------------------------

# Adventurer / hunt AI may random-walk only this many America macros from
# their home hub. Farther = taxi/drive home (continental roam soft-lock).
WILD_ROAM_MACRO_RADIUS = 2

# Home zones that boot-heal yank back when stranded far on America foot.
_SETTLEMENT_HOME_ZONES = frozenset({
    "lebanon-town",
    "men-of-letters",
    "harvelles-roadhouse",
})


def _parse_macro_pair(value):
    """Return (x, y) from a stamped overland_exit_macro / tuple / list."""
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        try:
            return (int(value[0]), int(value[1]))
        except (TypeError, ValueError):
            return None
    return _parse_pos_pair(value)


def home_return_room_key(actor, game):
    """Best settlement room key for Cadence return from overland."""
    if actor is None or game is None:
        return None
    from engine.room_vnum import lookup_room, canonical_persisted_room_key

    def _ident(token):
        room = lookup_room(game, token)
        if room is None:
            return None
        return canonical_persisted_room_key(game, token) or room.key

    # Echo dest lock (scheduled city stay) outranks the claimed house.
    dest_park = getattr(actor, "echo_dest_anchor_park", None)
    if dest_park:
        ident = _ident(dest_park)
        if ident:
            return ident
    home_key = getattr(actor, "home_room_key", None)
    if home_key:
        ident = _ident(home_key)
        if ident:
            return ident
    zone = getattr(actor, "home_zone", None)
    plaza_key, hub_key, _bunker_pad = _starter_keys()
    if zone == "men-of-letters":
        for key in (
            "Bunker Library Stacks",
            "Bunker Gatehouse",
            "Bunker Overflow Bunks",
        ):
            ident = _ident(key)
            if ident:
                return ident
    if zone == "harvelles-roadhouse":
        for key in ("HX00003", "HX00001"):
            ident = _ident(key)
            if ident:
                return ident
    if zone == "lebanon-town" or not zone:
        ident = _ident(plaza_key)
        if ident:
            return ident
        ident = _ident(hub_key)
        if ident:
            return ident
    return None


def home_hub_macro(game, actor):
    """America macro (x, y) for the actor's settlement pocket mouth."""
    if game is None or actor is None:
        return None
    ensure_game_overland(game)
    stamp_pocket_overland_exits(game)
    stamp_frontierland_pocket_overland_exits(game)
    rooms = getattr(game, "rooms", None) or {}
    _plaza_key, _hub_key, bunker_overland_key = _starter_keys()

    zone = (
        getattr(actor, "echo_dest_anchor_zone", None)
        or getattr(actor, "home_zone", None)
    )
    # Prefer the zone_exit mouth that matches home_zone (or dest lock).
    for room in rooms.values():
        if not getattr(room, "zone_exit", False):
            continue
        if zone and getattr(room, "zone", None) != zone:
            continue
        macro = _parse_macro_pair(getattr(room, "overland_exit_macro", None))
        if macro is not None:
            return macro
        exit_to = getattr(room, "zone_exit_to", None)
        if exit_to is not None:
            parsed = None
            parsed = map_ui.parse_grid_key(getattr(exit_to, "key", "") or "")
            if parsed and parsed[0] in _AMERICA_PREFIXES:
                return (parsed[1], parsed[2])

    # Hard fallbacks from starter SoT (Lebanon / bunker road).
    if zone == "men-of-letters":
        parsed = None
        parsed = map_ui.parse_grid_key(bunker_overland_key)
        if parsed:
            return (parsed[1], parsed[2])
        return (44, 33)
    # Lebanon default.
    _, hub_key, _ = _starter_keys()
    from engine.room_vnum import lookup_room

    mouth = lookup_room(game, hub_key)
    if mouth is not None:
        macro = _parse_macro_pair(getattr(mouth, "overland_exit_macro", None))
        if macro is not None:
            return macro
        exit_to = getattr(mouth, "zone_exit_to", None)
        if exit_to is not None:
            parsed = map_ui.parse_grid_key(getattr(exit_to, "key", "") or "")
            if parsed and parsed[0] in _AMERICA_PREFIXES:
                return (parsed[1], parsed[2])
    return (44, 32)


def overland_macro_distance(macro_a, macro_b):
    """Manhattan distance between two America macro cells."""
    if macro_a is None or macro_b is None:
        return None
    return abs(macro_a[0] - macro_b[0]) + abs(macro_a[1] - macro_b[1])


def is_far_from_home_hub(game, actor, *, radius=None):
    """True when on-foot America presence is past the roam radius."""
    if overland_mode(actor) != "on_foot":
        return False
    macro = _parse_pos_pair(getattr(actor, "macro_pos", None))
    if macro is None:
        return True
    home = home_hub_macro(game, actor)
    if home is None:
        return True
    limit = WILD_ROAM_MACRO_RADIUS if radius is None else int(radius)
    dist = overland_macro_distance(macro, home)
    return dist is None or dist > limit


def _enter_alias_at_landmark(game, actor, dest_room_key=None):
    """``enter`` home pocket when standing on the landmark micro."""
    from engine.npc_act import npc_do

    room = getattr(actor, "location", None)
    if room is None:
        return False
    entries = getattr(room, "zone_entries", None) or {}
    if not entries:
        return False
    home_zone = getattr(actor, "home_zone", None)
    # Prefer an entry whose hub matches home_zone / dest room.
    dest = None
    if dest_room_key and game is not None:
        from engine.room_vnum import lookup_room

        dest = lookup_room(game, dest_room_key)
    for alias, hub in entries.items():
        if dest is not None and hub is dest:
            npc_do(actor, f"enter {alias}", game)
            return True
        if home_zone and getattr(hub, "zone", None) == home_zone:
            npc_do(actor, f"enter {alias}", game)
            return True
    for pref in (
        "lebanon", "lebanon kansas", "lebanon-town", "town", "city",
        "bunker", "men-of-letters", "welcome", "crossroads",
    ):
        if pref in entries:
            npc_do(actor, f"enter {pref}", game)
            return True
    # Any entry.
    alias = next(iter(entries))
    npc_do(actor, f"enter {alias}", game)
    return True


def cadence_homeward_from_overland(game, actor, dest_room_key=None):
    """One Cadence turn toward settlement from dual-layer wilderness.

    Far from the home hub: ``drive`` / ``taxi`` (continental scale).
    Near: one foot step toward the landmark, then ``enter``.
    Returns True when the turn was consumed.
    """
    if game is None or actor is None:
        return False
    from engine.session_attach import has_live_player_session
    if has_live_player_session(actor, game):
        return False
    if overland_mode(actor) != "on_foot":
        return False
    ensure_game_overland(game)
    ensure_overland_defaults(actor)

    dest_key = dest_room_key or home_return_room_key(actor, game)
    home_macro = home_hub_macro(game, actor)
    macro = _parse_pos_pair(actor.macro_pos)
    micro = _parse_pos_pair(actor.micro_pos)
    if macro is None or micro is None or home_macro is None:
        # Broken stamp -- taxi if we have a dest, else bail.
        if dest_key:
            return hooks_mod.overland_cadence_travel_toward(
                game, actor, dest_key,
            )
        return False

    dist = overland_macro_distance(macro, home_macro)
    loc = getattr(actor, "location", None)
    on_virtual_foot = is_virtual_room(loc)

    # Live hub taxi on an atlas cell: tick_taxis owns the Echo body until
    # landing. Homeward cadence must not wipe stamps on logout (bug 1167).
    taxi_until = getattr(actor, "_taxi_until", None)
    taxi_scenic = getattr(actor, "_taxi_scenic_path", None)
    if taxi_until is not None and isinstance(taxi_scenic, list):
        return False

    # Hub taxi montages cannot pick up on ephemeral wilderness cells -- they
    # re-stamp ``_taxi_until`` every Cadence tick without ever landing.
    if on_virtual_foot and taxi_until is not None:
        actor._taxi_until = None
        actor._taxi_dest_key = None
        actor._taxi_kind = None
        actor._taxi_scenic_path = None
        actor._taxi_scenic_last_step = None

    # Drive / board first when a ride is usable. Settlement rooms may
    # taxi; virtual foot cannot complete a hub montage, but boarding a
    # colocated owned car must still beat hiking (vehicle_seek leftover).
    # Accept the hook only when mode or position actually changed -- a
    # taxi stamp on a wilderness cell is a no-op (cleared above).
    if dest_key and (
        not on_virtual_foot
        or dist is None
        or dist > WILD_ROAM_MACRO_RADIUS
    ):
        before_far = (macro, micro)
        mode_before = overland_mode(actor)
        if hooks_mod.overland_cadence_travel_toward(
            game, actor, dest_key,
        ):
            after_macro = _parse_pos_pair(getattr(actor, "macro_pos", None))
            after_micro = _parse_pos_pair(getattr(actor, "micro_pos", None))
            if (
                (after_macro, after_micro) != before_far
                or overland_mode(actor) != mode_before
            ):
                return True
            # Virtual-foot taxi montage re-stamps forever -- drop it and hike.
            if on_virtual_foot and getattr(actor, "_taxi_until", None) is not None:
                actor._taxi_until = None
                actor._taxi_dest_key = None
                actor._taxi_kind = None
                actor._taxi_scenic_path = None
                actor._taxi_scenic_last_step = None

    # Local or virtual far hike: one dual-layer step toward the home hub.
    if macro == home_macro and micro == LANDMARK_MICRO:
        return _enter_alias_at_landmark(game, actor, dest_key)

    from engine.npc_act import npc_do

    direction = _next_overland_foot_direction(macro, micro, home_macro)
    if direction is None:
        return _enter_alias_at_landmark(game, actor, dest_key)
    before = (macro, micro)
    npc_do(actor, direction, game)
    after_macro = _parse_pos_pair(getattr(actor, "macro_pos", None))
    after_micro = _parse_pos_pair(getattr(actor, "micro_pos", None))
    return (after_macro, after_micro) != before


def cadence_local_overland_roam(game, actor):
    """Random foot step that stays inside ``WILD_ROAM_MACRO_RADIUS``.

    Returns True when a step was taken (or a linger beat consumed).
    When already outside the radius, delegates to homeward.
    """
    import random

    if overland_mode(actor) != "on_foot":
        return False
    if is_far_from_home_hub(game, actor):
        return cadence_homeward_from_overland(game, actor)

    ensure_game_overland(game)
    macro = _parse_pos_pair(getattr(actor, "macro_pos", None))
    home = home_hub_macro(game, actor)
    if macro is None or home is None:
        return cadence_homeward_from_overland(game, actor)

    from engine.npc_act import npc_do

    room = getattr(actor, "location", None)
    exits = list((getattr(room, "exits", None) or {}).keys())
    if not exits:
        return False
    random.shuffle(exits)
    for direction in exits:
        # Probe the delta without moving: reuse try_overland_move math.
        delta = _DIR_DELTA.get(direction)
        if delta is None:
            continue
        micro = _parse_pos_pair(getattr(actor, "micro_pos", None))
        if micro is None:
            continue
        nx = micro[0] + delta[0]
        ny = micro[1] + delta[1]
        new_macro = macro
        if nx < 0 or nx >= MICRO_SIZE or ny < 0 or ny >= MICRO_SIZE:
            # Crossing a macro tile edge.
            new_macro = (macro[0] + delta[0], macro[1] + delta[1])
            if not clamp_macro(new_macro[0], new_macro[1]):
                continue
        dist = overland_macro_distance(new_macro, home)
        if dist is not None and dist > WILD_ROAM_MACRO_RADIUS:
            continue
        before = (
            _parse_pos_pair(actor.macro_pos),
            _parse_pos_pair(actor.micro_pos),
        )
        npc_do(actor, direction, game)
        after = (
            _parse_pos_pair(getattr(actor, "macro_pos", None)),
            _parse_pos_pair(getattr(actor, "micro_pos", None)),
        )
        if after != before:
            return True
    # No legal roam step -- linger (consume the beat so Cadence does not
    # fall into continental random-walk via other callers).
    return True


def heal_stranded_overland_cadence(game):
    """Boot reconcile: start Cadence homeward for far dual-layer foot travelers.

    Targets characters whose ``home_zone`` is a starter settlement and who
    sit more than ``WILD_ROAM_MACRO_RADIUS`` America macros from their
    pocket mouth. Runs one ``cadence_homeward_from_overland`` beat (drive /
    taxi / ``npc_do`` foot step) per stranded body -- no silent ``move_to``.
    Clears invalid ``zone_entry_hub_key`` stamps. Idempotent when nobody is
    stranded. Cadence ``_step_toward_home`` continues on tick.
    """
    stats = {"homeward": 0, "cleared_entry": 0, "still_stranded": 0}
    if game is None:
        return stats
    ensure_game_overland(game)
    stamp_pocket_overland_exits(game)
    stamp_frontierland_pocket_overland_exits(game)
    from world import Character
    plaza_key, hub_key, _bunker_pad = _starter_keys()

    rooms = getattr(game, "rooms", None) or {}
    from engine.room_vnum import lookup_room

    plaza = lookup_room(game, plaza_key) or getattr(
        game, "start_room", None
    )
    bunker = (
        lookup_room(game, "Bunker Overflow Bunks")
        or lookup_room(game, "Bunker Gatehouse")
        or lookup_room(game, "Bunker Library Stacks")
    )
    lebanon_mouth = lookup_room(game, hub_key)
    harvelles_lot = lookup_room(game, "HX00001")
    bunker_mouth = None
    for room in rooms.values():
        if (
            getattr(room, "zone", None) == "men-of-letters"
            and getattr(room, "zone_exit", False)
        ):
            bunker_mouth = room
            break

    cast = list(getattr(game, "characters", None) or [])
    for char in cast:
        if not isinstance(char, Character):
            continue
        from engine.session_attach import has_live_player_session
        if has_live_player_session(char, game):
            continue
        ensure_overland_defaults(char)
        if overland_mode(char) != "on_foot":
            continue
        zone = getattr(char, "home_zone", None)
        if zone not in _SETTLEMENT_HOME_ZONES:
            continue
        if not is_far_from_home_hub(game, char):
            continue
        if zone == "men-of-letters":
            dest = bunker or plaza
            mouth = bunker_mouth
        elif zone == "harvelles-roadhouse":
            dest = harvelles_lot or plaza
            mouth = harvelles_lot
        else:
            dest = plaza or bunker
            mouth = lebanon_mouth
        if dest is None:
            continue
        stamped = getattr(char, "zone_entry_hub_key", None)
        if stamped:
            from engine.room_vnum import lookup_room

            if lookup_room(game, stamped) is None:
                char.zone_entry_hub_key = None
                stats["cleared_entry"] += 1
        dest_key = getattr(dest, "key", None)
        if cadence_homeward_from_overland(game, char, dest_room_key=dest_key):
            stats["homeward"] += 1
        if overland_mode(char) == "zone" and mouth is not None:
            from engine.room_vnum import internal_room_key

            char.zone_entry_hub_key = internal_room_key(mouth) or mouth.key
        elif is_far_from_home_hub(game, char):
            stats["still_stranded"] += 1
    return stats


# ---------------------------------------------------------------------------
# Boot heal / migration
# ---------------------------------------------------------------------------


def heal_dual_layer_positions(game):
    """Convert legacy America Overland Room occupancy to dual-layer coords.

    Idempotent. Never deletes characters. Called from Game boot.
    """
    ensure_game_overland(game)
    stamp_pocket_overland_exits(game)
    stamp_frontierland_pocket_overland_exits(game)
    from world import Character

    moved = 0
    for char in list(getattr(game, "characters", []) or []):
        if not isinstance(char, Character):
            continue
        ensure_overland_defaults(char)
        # Already on dual layer -- ensure virtual room bind.
        if overland_mode(char) == "on_foot":
            room = getattr(char, "location", None)
            # Clinic / hotel interiors can keep stale foot coords after
            # admit or discharge used bare move_to (bug report 254).
            if room is not None and not is_virtual_room(room):
                clear_overland_coords(char)
                continue
            macro = _parse_pos_pair(char.macro_pos)
            micro = _parse_pos_pair(char.micro_pos)
            if macro and micro:
                plane = _overland_plane_for_room(room) if room else "earth"
                place_on_overland(char, game, macro, micro, plane=plane)
            continue
        if overland_mode(char) == "vehicle":
            continue
        if overland_mode(char) == "flying":
            macro = _parse_pos_pair(char.macro_pos)
            if macro:
                place_aerial_overland(char, game, macro)
            else:
                hooks_mod.overland_solar_land_all_the_way(char, game)
            continue
        room = getattr(char, "location", None)
        if room is None:
            continue
        # Shared mid-session / boot adopt (America pad, Wilderness stub,
        # Gates of …, or virtual room missing coords).
        before_mode = overland_mode(char)
        if adopt_foot_overland_presence(char, game):
            if before_mode != "on_foot":
                moved += 1
    return moved


def america_cell_key(mx, my):
    """Canonical America Overland Room key (for atlas / legacy lookups)."""
    return f"{AMERICA_PREFIX} ({mx}, {my})"
