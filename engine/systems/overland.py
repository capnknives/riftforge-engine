"""
overland.py -- dual-layer US overland (Finalmap x earth_america).

Macro layer: 78x18 America Overland atlas (vehicles; pure coords).
Micro layer: virtual 10x10 wilderness per macro tile (on foot; never saved
as map rooms). Static zones stay classic Rooms entered at micro (5, 5).

America Overland grid Rooms still exist for atlas ``map`` / pocket wiring;
player *presence* on the dual layer uses ephemeral VirtualRooms keyed by
(macro_x, macro_y, micro_x, micro_y).

No networking. Stdlib only. Lives in engine/ so basegame and SUPERS can
share one implementation; games point maps.set_maps_dir() at their atlas JSON.
"""

from __future__ import annotations

import importlib
import json
import os
import re

from engine.world import Room


def _try_game_module(qualname):
    """Import an optional game package module; None when absent.

    Public / basegame trees have no ``supers/``. SUPERS-only paths
    (vehicles, Lebanon starter heal, solar land, planar influence) call
    through this helper so ``engine_smoke`` stays clean. A later purity
    pass should replace these with ``engine.hooks`` registrations
    (see ``docs/plans/two_repo_purity.md`` § next purity pass).
    """
    try:
        return importlib.import_module(qualname)
    except ImportError:
        return None


# Lebanon / bunker string defaults when starter_town is not installed.
_FALLBACK_PLAZA_KEY = "Lebanon Square"
_FALLBACK_OVERLAND_HUB_KEY = "Main Street S9"
_FALLBACK_BUNKER_OVERLAND_KEY = "America Overland (35, 11)"


def _starter_keys():
    """Plaza / overland-hub / bunker-pad keys (SUPERS starter_town or fallbacks)."""
    st = _try_game_module("supers.starter_town")
    if st is None:
        return (
            _FALLBACK_PLAZA_KEY,
            _FALLBACK_OVERLAND_HUB_KEY,
            _FALLBACK_BUNKER_OVERLAND_KEY,
        )
    return (
        getattr(st, "PLAZA_KEY", _FALLBACK_PLAZA_KEY),
        getattr(st, "OVERLAND_HUB_KEY", _FALLBACK_OVERLAND_HUB_KEY),
        getattr(st, "BUNKER_OVERLAND_KEY", _FALLBACK_BUNKER_OVERLAND_KEY),
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

# Classic guideline size (xycoordmapUSguidelines.docx).
MACRO_WIDTH = 78
MACRO_HEIGHT = 18
MICRO_SIZE = 10
# City / landmark physical entrance sits at micro center.
LANDMARK_MICRO = (5, 5)
# America Overland key_prefix from earth_america.json (no leading "The").
AMERICA_PREFIX = "America Overland"
# Legacy room keys / help text sometimes used a "The " prefix.
_AMERICA_PREFIXES = frozenset({AMERICA_PREFIX, "The America Overland"})
EARTH_AMERICA_ID = "earth_america"

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
}

# Water / lake block Impala travel (v1 road fantasy).
_VEHICLE_BLOCKED = frozenset({"ocean", "lake"})


def _content_maps_dir():
    """Absolute path to the active maps/ directory (game-selectable)."""
    import maps as maps_mod
    return maps_mod.get_maps_dir()


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
    """Return True when (x, y) is inside the 78×18 macro atlas."""
    return 0 <= x < MACRO_WIDTH and 0 <= y < MACRO_HEIGHT


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


def is_aerial_room(room):
    """True when this Room is a Stellar macro-hover sky cell."""
    return bool(getattr(room, "aerial_overland", False))


def ensure_overland_defaults(character):
    """Attach dual-layer fields if missing (idempotent)."""
    if not hasattr(character, "macro_pos"):
        character.macro_pos = None
    if not hasattr(character, "micro_pos"):
        character.micro_pos = None


def clear_overland_coords(character):
    """Leave the dual layer (entering a static zone)."""
    ensure_overland_defaults(character)
    character.macro_pos = None
    character.micro_pos = None


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
    pair = _parse_pos_pair(getattr(room, "overland_macro", None))
    if pair is not None and clamp_macro(*pair):
        return pair
    # Authored America Overland (x, y) cell.
    import maps as maps_mod
    parsed = maps_mod.parse_grid_key(getattr(room, "key", "") or "")
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
            place_on_overland(character, game, macro, micro)
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
    if macro is None:
        return False
    # Gate / pad occupancy -> landmark center (same as boot heal).
    return bool(place_on_overland(character, game, macro, LANDMARK_MICRO))


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
        self.prefix = grid.get("key_prefix") or AMERICA_PREFIX
        self.width = int(grid.get("width") or MACRO_WIDTH)
        self.height = int(grid.get("height") or MACRO_HEIGHT)
        self.default_area = grid.get("area_type") or "ocean"
        self.bestiary_categories = list(grid.get("bestiary_categories") or [])
        self.terrain = {}
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
                "title": override.get("title"),
                "bestiary_categories": list(cats or []),
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

    def description_at(self, mx, my):
        """Return authored cell description or a terrain pool blurb."""
        cell = self.terrain.get((mx, my))
        if cell and cell.get("description"):
            return cell["description"]
        area = self.terrain_at(mx, my)
        return _TERRAIN_BLURBS.get(
            area,
            f"American overland at ({mx}, {my}).",
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


def load_earth_america_atlas():
    """Load OverlandAtlas from content/maps/earth_america.json."""
    path = os.path.join(_content_maps_dir(), "earth_america.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return OverlandAtlas(data)


def ensure_game_overland(game):
    """Stamp atlas + virtual-room manager onto `game` (idempotent)."""
    if getattr(game, "_overland_ready", False):
        return
    game.overland_atlas = load_earth_america_atlas()
    game.overland_rooms = {}  # 4D key -> ephemeral Room
    game.overland_ground = {}  # 4D key -> [Item, ...]
    game._overland_ready = True


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
    if not is_virtual_room(room):
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
        ensure_game_overland(game)
        atlas = getattr(game, "overland_atlas", None)
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
    if not is_virtual_room(room):
        return []
    ensure_game_overland(game)
    atlas = getattr(game, "overland_atlas", None)
    macro = getattr(room, "overland_macro", None)
    micro = getattr(room, "overland_micro", None)
    if atlas is None or macro is None or micro is None:
        return []
    lines = nearby_landmark_bearing_lines(atlas, macro, micro)
    if not lines:
        return []
    # Lead-in so screenreader Paths / prose both carry meaning.
    return ["Nearby zones:"] + lines


def get_virtual_room(game, macro, micro):
    """Return (create if needed) the ephemeral Room for these 4D coords.

    Never written to map JSON / SQLite as a permanent room. Ground stash
    items are hydrated into contents on create.
    """
    ensure_game_overland(game)
    atlas = game.overland_atlas
    key = _quad_key(macro, micro)
    existing = game.overland_rooms.get(key)
    if existing is not None:
        return existing

    mx, my = macro
    ux, uy = micro
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
    room.plane = "earth"
    room.realm = "prime"
    room.map_id = atlas.map_id
    room.zone = None
    room.bestiary_categories = atlas.bestiary_at(mx, my)
    # Cardinal exits are virtual markers -- move handler ignores them and
    # recomputes from coords. Still listed so look / screenreader show paths.
    for direction in ("north", "south", "east", "west"):
        room.exits[direction] = room  # placeholder; try_overland_move wins
    if landmark and (ux, uy) == LANDMARK_MICRO:
        hub_key = landmark.get("hub_room")
        hub = (getattr(game, "rooms", {}) or {}).get(hub_key) if hub_key else None
        if hub is not None:
            for alias in landmark.get("enter_as") or []:
                room.zone_entries[alias] = hub
            # Also allow the hub key lowercase.
            room.zone_entries[hub.key.lower()] = hub

    # Hydrate dropped items for this 4D cell.
    for item in list(game.overland_ground.get(key) or []):
        if item not in room.contents:
            room.contents.append(item)
            item.location = room

    game.overland_rooms[key] = room
    return room


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


def place_on_overland(character, game, macro, micro):
    """Bind character to the dual layer at macro + micro (on foot)."""
    ensure_overland_defaults(character)
    ensure_game_overland(game)
    mx, my = macro
    ux, uy = micro
    if not clamp_macro(mx, my):
        return False
    if not (0 <= ux < MICRO_SIZE and 0 <= uy < MICRO_SIZE):
        return False
    character.macro_pos = (mx, my)
    character.micro_pos = (ux, uy)
    room = get_virtual_room(game, (mx, my), (ux, uy))
    character.move_to(room)
    return True


def sync_ground_stash(game, room):
    """Persist floor items from a virtual room into game.overland_ground."""
    if not is_virtual_room(room):
        return
    ensure_game_overland(game)
    macro = getattr(room, "overland_macro", None)
    micro = getattr(room, "overland_micro", None)
    if macro is None or micro is None:
        return
    key = _quad_key(macro, micro)
    from world import Item
    items = [obj for obj in room.contents if isinstance(obj, Item)]
    if items:
        game.overland_ground[key] = items
    elif key in game.overland_ground:
        del game.overland_ground[key]


def prune_empty_virtual_rooms(game):
    """Drop ephemeral rooms with no characters (items stay in ground stash).

    Keeps pads that still carry active planar influence / invasion state so
    GM and tick code can resolve them by key after the last walker leaves.
    """
    ensure_game_overland(game)
    from world import Character
    influence_mod = _try_game_module("supers.planar_influence")
    dead = []
    for key, room in list(game.overland_rooms.items()):
        sync_ground_stash(game, room)
        has_char = any(isinstance(o, Character) for o in room.contents)
        if has_char:
            continue
        if influence_mod is not None and influence_mod.is_influenced(room, game):
            continue
        dead.append(key)
    for key in dead:
        del game.overland_rooms[key]


# ---------------------------------------------------------------------------
# Movement
# ---------------------------------------------------------------------------


def _vehicle_can_enter(atlas, mx, my):
    """True when the Impala may roll onto this macro cell."""
    area = atlas.terrain_at(mx, my)
    return area not in _VEHICLE_BLOCKED


def pathfind_vehicle_macro(atlas, start, goal, *, max_steps=4000):
    """BFS a driveable America-macro path from ``start`` to ``goal``.

    Cardinal steps only (n/s/e/w). Skips ocean/lake. Returns a list of
    ``(x, y)`` tiles **after** start through and including goal, or
    ``None`` when unreachable / inputs invalid. Used by scenic / slow
    atlas cruises (You are what you drive C+).
    """
    start = _parse_pos_pair(start)
    goal = _parse_pos_pair(goal)
    if start is None or goal is None:
        return None
    if not clamp_macro(*start) or not clamp_macro(*goal):
        return None
    if not _vehicle_can_enter(atlas, *start):
        return None
    if not _vehicle_can_enter(atlas, *goal):
        return None
    if start == goal:
        return []
    # Standard BFS: queue of positions; came_from rebuilds the path.
    from collections import deque

    came_from = {start: None}
    queue = deque([start])
    steps = 0
    cardinals = ((0, 1), (0, -1), (1, 0), (-1, 0))
    while queue and steps < max_steps:
        steps += 1
        cur = queue.popleft()
        if cur == goal:
            break
        cx, cy = cur
        for dx, dy in cardinals:
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


def try_overland_move(character, direction, game):
    """Handle N/S/E/W (and diagonals) while on the dual layer.

    Returns True when the move was handled (success or blocked message).
    Returns False when the caller should use classic Room.exits movement.
    """
    if mission_pocket_blocks_overland_move(character):
        return False
    ensure_overland_defaults(character)
    mode = overland_mode(character)
    # Legacy America pads / cleared virtual cells: adopt foot presence so
    # micro 10x10 + macro edge-cross run instead of classic Room.exits.
    if mode == "zone":
        if adopt_foot_overland_presence(character, game):
            mode = overland_mode(character)
        else:
            return False
    if mode == "zone":
        return False
    ensure_game_overland(game)
    atlas = game.overland_atlas
    delta = _DIR_DELTA.get(direction)
    if delta is None:
        session = getattr(character, "session", None)
        if session is not None:
            session.send("You can't go that way.")
        return True

    dx, dy = delta
    macro = _parse_pos_pair(character.macro_pos)
    if macro is None:
        return False

    if mode == "vehicle":
        nx, ny = macro[0] + dx, macro[1] + dy
        if not clamp_macro(nx, ny):
            _send(character, "You have reached the edge of the map.")
            return True
        if not _vehicle_can_enter(atlas, nx, ny):
            area = atlas.terrain_at(nx, ny)
            _send(
                character,
                f"You can't drive onto {area} from here.",
            )
            return True
        character.macro_pos = (nx, ny)
        # Stay in the vehicle interior; update every occupant's macro.
        vid = getattr(character, "in_vehicle", None)
        vehicles_mod = _try_game_module("supers.vehicles") if vid else None
        if vid and vehicles_mod is not None:
            veh = vehicles_mod.vehicle_by_id(game, vid)
            if veh is not None:
                veh["macro_pos"] = (nx, ny)
                veh["micro_pos"] = None
                for who in vehicles_mod.vehicle_occupants(game, veh):
                    ensure_overland_defaults(who)
                    who.macro_pos = (nx, ny)
                    who.micro_pos = None
                vehicles_mod.save_parking_state(game)
        # Off-road wear (Slice E): highway map_layer is safe.
        area = atlas.terrain_at(nx, ny)
        cell = atlas.terrain.get((nx, ny)) or {}
        layer = str(cell.get("map_layer") or "").lower()
        risk_tag = area
        if layer in ("highway", "mountain_highway", "city") or area == "city":
            risk_tag = "road"
        if vid and vehicles_mod is not None:
            kit_mod = _try_game_module("supers.vehicle_kit")
            veh = vehicles_mod.vehicle_by_id(game, vid)
            if kit_mod is not None:
                dmg_msg = kit_mod.apply_offroad_step(
                    veh, character, game, risk_tag
                )
                if dmg_msg:
                    _send(character, dmg_msg)
                    if int((veh or {}).get("condition", 100) or 100) <= 0:
                        return True
        _send(
            character,
            f"The road rolls on -- now at overland ({nx}, {ny}).",
        )
        # Same as hub cruise: atlas for sighted, text for screenreader.
        if vid and vehicles_mod is not None:
            vehicles_mod._redraw_scenic_map(character, game, nx, ny)
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
        character.macro_pos = (nx, ny)
        character.stellar_flight_macro = [nx, ny]
        character.move_to(get_aerial_room(game, (nx, ny)))
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

    old_room = character.location
    from command_support import _presence_face, is_staff_stealth_presence
    face = _presence_face(character)
    stealth = is_staff_stealth_presence(character)
    if old_room is not None and not stealth:
        old_room.broadcast(
            f"{face} leaves to the {direction}.",
            exclude=character,
        )
    place_on_overland(character, game, (mx, my), (ux, uy))
    new_room = character.location
    if new_room is not None and not stealth:
        new_room.broadcast(
            f"{face} arrives from the "
            f"{_opposite(direction)}.",
            exclude=character,
        )
    from engine.verbs.basic import cmd_look
    if character.session is not None:
        cmd_look(character, "", game, after_move=True)
    # Same on-entry encounter roll classic Room.exits get via _move_one
    # (wilderness hostiles / procedural dungeons / aggro). Drive-layer
    # vehicle steps above skip this -- macro pads are wilderness:false.
    from engine import hooks as _hooks
    _hooks.encounter_check(game, new_room)
    prune_empty_virtual_rooms(game)
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
    ensure_game_overland(game)
    atlas = game.overland_atlas
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
    from command_support import _presence_face
    clear_overland_coords(character)
    stamp_zone_entry(character, hub)
    face = _presence_face(character)
    _do_transition(
        character, hub, game,
        f"{face} enters {hub.key}.",
        f"{face} arrives.",
    )
    dungeons_mod = _try_game_module("supers.dungeons")
    if dungeons_mod is not None:
        dungeons_mod.notify_entered_dungeon_hub(character, game, hub)
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
        import maps as maps_mod
        parsed = maps_mod.parse_grid_key(dest.key)
        if parsed and parsed[0] in _AMERICA_PREFIXES:
            return (parsed[1], parsed[2])
    return None


def current_zone_macro(game, room):
    """America macro (x, y) for the zone ``room`` sits in, or ``None``.

    Every real classic-zone room shares its ``zone`` name with exactly one
    ``zone_exit`` mouth Room -- this resolves that mouth's macro cell so
    callers (the atlas ``@`` marker, ``map big``'s center-room lookup) can
    place a character at the right cell even while they're standing deep
    inside a town/dungeon, not literally on the overland grid (where
    ``macro_pos`` is intentionally ``None`` -- see ``clear_overland_coords``).
    """
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
    ensure_game_overland(game)
    from command_support import _presence_face, is_staff_stealth_presence
    face = _presence_face(character)
    stealth = is_staff_stealth_presence(character)
    old = character.location
    if old is not None and not stealth:
        old.broadcast(
            f"{face} exits to the overland.",
            exclude=character,
        )
    place_on_overland(character, game, macro, LANDMARK_MICRO)
    new = character.location
    if new is not None and not stealth:
        new.broadcast(
            f"{face} arrives.",
            exclude=character,
        )
    from engine.verbs.basic import cmd_look
    if character.session is not None:
        cmd_look(character, "", game)
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
    for (mx, my), landmark in atlas.landmarks.items():
        hub_key = landmark.get("hub_room")
        hub = rooms.get(hub_key)
        if hub is None:
            continue
        hub.overland_exit_macro = (mx, my)
        hub.zone_exit = True
        # Destination Room for non-America / classic exit path.
        cell_key = f"America Overland ({mx}, {my})"
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
    rooms = getattr(game, "rooms", None) or {}
    home_key = getattr(actor, "home_room_key", None)
    if home_key and home_key in rooms:
        return home_key
    zone = getattr(actor, "home_zone", None)
    plaza_key, hub_key, _bunker_pad = _starter_keys()
    if zone == "men-of-letters":
        for key in (
            "Bunker Library Stacks",
            "Bunker Gatehouse",
            "Bunker Overflow Bunks",
        ):
            if key in rooms:
                return key
    if zone == "lebanon-town" or not zone:
        if plaza_key in rooms:
            return plaza_key
        if hub_key in rooms:
            return hub_key
    return None


def home_hub_macro(game, actor):
    """America macro (x, y) for the actor's settlement pocket mouth."""
    if game is None or actor is None:
        return None
    ensure_game_overland(game)
    stamp_pocket_overland_exits(game)
    rooms = getattr(game, "rooms", None) or {}
    _plaza_key, _hub_key, bunker_overland_key = _starter_keys()

    zone = getattr(actor, "home_zone", None)
    # Prefer the zone_exit mouth that matches home_zone.
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
            import maps as maps_mod
            parsed = maps_mod.parse_grid_key(getattr(exit_to, "key", "") or "")
            if parsed and parsed[0] in _AMERICA_PREFIXES:
                return (parsed[1], parsed[2])

    # Hard fallbacks from starter SoT (Lebanon / bunker road).
    if zone == "men-of-letters":
        parsed = None
        import maps as maps_mod
        parsed = maps_mod.parse_grid_key(bunker_overland_key)
        if parsed:
            return (parsed[1], parsed[2])
        return (35, 11)
    # Lebanon default.
    mouth = rooms.get(starter_town_mod.OVERLAND_HUB_KEY)
    if mouth is not None:
        macro = _parse_macro_pair(getattr(mouth, "overland_exit_macro", None))
        if macro is not None:
            return macro
        exit_to = getattr(mouth, "zone_exit_to", None)
        if exit_to is not None:
            import maps as maps_mod
            parsed = maps_mod.parse_grid_key(getattr(exit_to, "key", "") or "")
            if parsed and parsed[0] in _AMERICA_PREFIXES:
                return (parsed[1], parsed[2])
    return (35, 10)


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
        dest = (getattr(game, "rooms", None) or {}).get(dest_room_key)
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
            vehicles_mod = _try_game_module("supers.vehicles")
            if vehicles_mod is None:
                return False
            return vehicles_mod.cadence_travel_toward(game, actor, dest_key)
        return False

    dist = overland_macro_distance(macro, home_macro)
    if dist is None or dist > WILD_ROAM_MACRO_RADIUS:
        if not dest_key:
            return False
        vehicles_mod = _try_game_module("supers.vehicles")
        if vehicles_mod is None:
            return False
        return vehicles_mod.cadence_travel_toward(game, actor, dest_key)

    # Local hike: at landmark micro of the home tile -> enter pocket.
    if macro == home_macro and micro == LANDMARK_MICRO:
        return _enter_alias_at_landmark(game, actor, dest_key)

    # One dual-layer step toward the home hub (npc_do so look/encounters fire).
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
    """Boot-heal: yank far dual-layer foot travelers back to settlement.

    Targets characters whose ``home_zone`` is a starter settlement and who
    sit more than ``WILD_ROAM_MACRO_RADIUS`` America macros from their
    pocket mouth. Clears dual-layer coords and stamps a legal zone_entry
    hub so ``exit`` works later. Idempotent. Never deletes characters.
    """
    stats = {"yanked": 0, "cleared_entry": 0}
    if game is None:
        return stats
    ensure_game_overland(game)
    stamp_pocket_overland_exits(game)
    from world import Character
    plaza_key, hub_key, _bunker_pad = _starter_keys()

    rooms = getattr(game, "rooms", None) or {}
    plaza = rooms.get(plaza_key) or getattr(
        game, "start_room", None
    )
    bunker = (
        rooms.get("Bunker Overflow Bunks")
        or rooms.get("Bunker Gatehouse")
        or rooms.get("Bunker Library Stacks")
    )
    lebanon_mouth = rooms.get(hub_key)
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
        else:
            dest = plaza or bunker
            mouth = lebanon_mouth
        if dest is None:
            continue
        clear_overland_coords(char)
        # Drop a stale wastes / missing entry stamp so exit works at S9.
        stamped = getattr(char, "zone_entry_hub_key", None)
        if stamped and stamped not in rooms:
            char.zone_entry_hub_key = None
            stats["cleared_entry"] += 1
        if mouth is not None:
            char.zone_entry_hub_key = mouth.key
        loc = getattr(char, "location", None)
        if loc is not dest:
            char.move_to(dest)
        stats["yanked"] += 1
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
    from world import Character

    moved = 0
    for char in list(getattr(game, "characters", []) or []):
        if not isinstance(char, Character):
            continue
        ensure_overland_defaults(char)
        # Already on dual layer -- ensure virtual room bind.
        if overland_mode(char) == "on_foot":
            macro = _parse_pos_pair(char.macro_pos)
            micro = _parse_pos_pair(char.micro_pos)
            if macro and micro:
                place_on_overland(char, game, macro, micro)
            continue
        if overland_mode(char) == "vehicle":
            continue
        if overland_mode(char) == "flying":
            macro = _parse_pos_pair(char.macro_pos)
            if macro:
                place_aerial_overland(char, game, macro)
            else:
                solar_mod = _try_game_module("supers.solar")
                if solar_mod is not None:
                    solar_mod.land_all_the_way(char, game)
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
