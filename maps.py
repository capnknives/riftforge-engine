"""
maps.py -- loads the world's maps from content/maps/*.json and builds the
live Room objects from that data.

Phase: "separate maps into their own content folder" (a live player/design
request). Before this, the entire map (the 100x100 Wastes grid plus the
hand-authored Plaza/Alley/Chamber area) was hardcoded directly in
world.py's build_world(). That's the same problem content.py already solved
once for Origins/Disciplines: game content living as Python code instead of
data makes it harder to add more of it without growing one function forever.
This module applies the same fix to the map, and adds one new idea the old
hardcoded version didn't need: a room's exits (or a grid cell's extra
"portal" exit) can point at a room key defined in a COMPLETELY DIFFERENT
file. That one mechanism is *both* how the Wastes grid's gateway cell has
always opened onto Central Plaza (same file) *and* how a whole separate
plane (e.g. content/maps/cinder_reach.json) can be reached from it -- no
special "cross-map" feature was needed, just resolving every exit against
one shared dict instead of the current file's own rooms.

Layering: this module imports Room/Item from world.py -- the same direction
persistence.py already imports Character/Item from world.py -- and is in
turn imported BY world.py's build_world() (imported lazily, inside the
function, to dodge the circular import that a top-of-file `import maps`
would otherwise create -- the same trick Character.__init__ already uses
for stats.py/training.py). content.py is untouched: this is a parallel
data-driven catalog, not an extension of it (Origins/Disciplines describe
characters; maps describe the world they stand in).

Grid ↔ pocket zone travel (towns, spirit gates, dungeon mouths) is authored
in top-level pockets[] at creation time -- {kind, at: [x,y], hub_room,
enter_as}. The loader wires Room.zone_entries / zone_exit_to so players use
enter <name> / exit. Do not also author grid.portals or hub exits.out for
the same pair; leftover in/out for that pair is stripped as a safety net.
Nested indoor doors and Plaza↔Cinder Reach still use in/out/leave.
Authoring SoT: docs/CONTENT_AUTHORING.md, help build-maps, map editor
Pockets panel.
"""

import glob
import json
import os
import re

from world import Room

# Resolve relative to THIS file's directory (not the process's current
# working directory), same reasoning as content.py's _CONTENT_DIR. Mutable
# via set_maps_dir below (two-repo purity Phase 3: docs/plans/
# two_repo_purity.md) -- a future standalone engine consumer with no
# content/maps/ of its own can point this somewhere else before calling
# load_all_maps(); a bare SUPERS checkout never needs to touch it.
_MAPS_DIR = os.path.join(os.path.dirname(__file__), "content", "maps")


def set_maps_dir(path):
    """Point the map loader at a different content/maps/ directory.

    Pass None to restore the default (this repo's own content/maps/).
    """
    global _MAPS_DIR
    _MAPS_DIR = (
        path if path is not None
        else os.path.join(os.path.dirname(__file__), "content", "maps")
    )


def get_maps_dir():
    """Return the directory load_all_maps() currently reads from."""
    return _MAPS_DIR

# D25's second fork (docs/SYSTEMS_DESIGN.md section 10): area_type is a
# formal, engine-read tag (not just editor UI metadata), so it needs a
# controlled vocabulary the loader can validate map JSON against -- an
# unrecognized area_type is a content typo, and this is where it fails
# loud instead of quietly becoming a room no filter ever matches.
#
# Each value is that area_type's DEFAULT bestiary_categories -- used only
# when a grid/room's JSON doesn't specify bestiary_categories explicitly
# (see _add_room below). Empty for every entry today (no generic-terrain
# spawn tables exist yet, only the plane-flavored "earth-dweller"/
# "fire-being" categories both current maps already set explicitly) --
# the hook exists so a future map author can lean on area_type alone
# instead of repeating bestiary_categories on every grid.
AREA_TYPES = {
    "ruins": [],
    "city": [],
    "mountains": [],
    "ocean": [],
    "lake": [],
    "forest": [],
    "plains": [],
}

# Legacy map JSON may still say area_type "wilderness" from before bug #26
# retired it as a terrain tag. Remap at load so old content keeps working
# while `look` never shows "Area: Wilderness" again.
_LEGACY_AREA_TYPE_ALIASES = {
    "wilderness": "plains",
}

# Which area_types default Room.wilderness to True when a grid/room's JSON
# doesn't say so explicitly (see _add_room's `wilderness=None` case below).
# A live player-reported gap: wilderness was a completely separate flag
# from area_type, so a hand-authored room tagged area_type "forest" or
# "lake" got NO world.wilderness_encounter_tick spawns at all unless its
# JSON *also* separately set "wilderness": true -- nothing derived one
# from the other, even though "a forest is wilderness, same as a lake" is
# exactly the expected reading of area_type. Ruins/city are the only
# "not wild by default" types; an explicit wilderness: true/false in JSON
# always wins regardless (e.g. a hand-placed safehouse inside a forest).
WILD_AREA_TYPES = frozenset({
    "forest", "lake", "mountains", "ocean", "plains",
})

# Radiant / NEEDS resource tags allowed on Room.resources (and capacity keys).
# "vendor" marks a shop location; food/water/sleep map to survival need meters;
# entertainment/social (#56) are ambient leisure tags (idle to sate, no buy).
# Venue subtypes (bar, arcade, …) are preference-only -- they do NOT create
# need meters; Cadence / leisure.py score them for personality-driven picks.
KNOWN_RESOURCE_TAGS = frozenset({
    "food", "water", "sleep", "vendor", "blood",
    "entertainment", "social", "training", "work",
    # Home shower / wash for the hygiene Cadence need.
    "hygiene",
    # Easy-fit town services (D63/D64/D68): player rumor board, post mail,
    # and scrip bank counters -- same resource-tag shape as vendor/work.
    "bank", "mail", "rumor_board",
    # Leisure venue flavors (personality prefs; still need entertainment/social
    # on the room for ambient sate). library also marks hunter research dens.
    "bar", "arcade", "theater", "library", "park", "plaza", "nightlife",
})

# Controlled plane vocabulary (map JSON top-level "plane"). Realm is the
# family; plane is the specific dimension. Unknown planes fail loud at load.
PLANES = frozenset({
    "earth", "fire", "water", "air", "stone",
    "heaven", "hell", "purgatory", "dream",
    "stellar",
})

# plane -> realm family. Cosmic Favor's elemental/eldritch tether is a
# separate character system -- do not conflate with map realm. Aspect
# `earth` Elementals live on map plane `stone` so prime material
# (plane "earth") stays the normal Wastes/town baseline.
REALM_FOR_PLANE = {
    "earth": "prime",
    "fire": "elemental",
    "water": "elemental",
    "air": "elemental",
    "stone": "elemental",
    "heaven": "spirit",
    "hell": "spirit",
    "purgatory": "spirit",
    "dream": "spirit",
    "stellar": "void",
}

# Pocket kinds for map JSON pockets[] metadata (authors/tools).
POCKET_KINDS = frozenset({"settlement", "dungeon", "landmark"})

# Filled by the most recent load_all_maps() call -- Game may copy this onto
# game.map_registry. Keys are map file "id" strings.
LAST_MAP_REGISTRY = {}

# D29 overland ASCII minimap (docs/SYSTEMS_DESIGN.md section 10): one
# letter per area_type so telnet clients that ignore color still read
# terrain. Letter is the primary signal; ANSI color (AREA_TYPE_COLOR /
# PLANE_AREA_COLORS below) is supplementary only -- never color alone
# (section 8 accessibility).
AREA_TYPE_GLYPH = {
    "ruins": "R",
    "city": "C",
    "mountains": "M",
    "ocean": "O",
    "lake": "L",
    "forest": "F",
    "plains": "P",
}

# ANSI 16-color escapes (stdlib only -- no third-party color libs).
# Reset with ANSI_RESET after every colored cell so a color never leaks
# into the next glyph or the legend line.
ANSI_RESET = "\x1b[0m"
AREA_TYPE_COLOR = {
    "ruins": "\x1b[37m",        # white/grey stone
    "city": "\x1b[36m",         # cyan settlement
    "mountains": "\x1b[90m",    # bright black / dark grey
    "ocean": "\x1b[34m",        # blue
    "lake": "\x1b[94m",         # bright blue
    "forest": "\x1b[32m",       # green
    "plains": "\x1b[92m",       # bright green
}

# Suggestion #8 plane color modifiers: same tile letters, different
# palette when the room's plane is not the default earth look. Lookup is
# (plane, area_type) -- missing pairs fall back to AREA_TYPE_COLOR so a
# new plane only needs the cells it actually recolors.
PLANE_AREA_COLORS = {
    "fire": {
        "ruins": "\x1b[91m",       # bright red scorched stone
        "city": "\x1b[33m",        # amber settlement
        "mountains": "\x1b[91m",
        "ocean": "\x1b[35m",       # magenta -- magma "seas"
        "lake": "\x1b[35m",        # sulfur pools
        "forest": "\x1b[31m",      # burned forest
        "plains": "\x1b[33m",
    },
    "water": {
        "ruins": "\x1b[36m",
        "city": "\x1b[96m",
        "mountains": "\x1b[34m",
        "ocean": "\x1b[94m",
        "lake": "\x1b[96m",
        "forest": "\x1b[36m",
        "plains": "\x1b[36m",
    },
    "air": {
        "ruins": "\x1b[97m",
        "city": "\x1b[37m",
        "mountains": "\x1b[97m",
        "ocean": "\x1b[96m",
        "lake": "\x1b[96m",
        "forest": "\x1b[37m",
        "plains": "\x1b[97m",
    },
    "stone": {
        "ruins": "\x1b[33m",
        "city": "\x1b[37m",
        "mountains": "\x1b[33m",
        "ocean": "\x1b[90m",
        "lake": "\x1b[90m",
        "forest": "\x1b[32m",
        "plains": "\x1b[33m",
    },
    "heaven": {
        "ruins": "\x1b[97m",
        "city": "\x1b[96m",
        "mountains": "\x1b[97m",
        "ocean": "\x1b[94m",
        "lake": "\x1b[96m",
        "forest": "\x1b[92m",
        "plains": "\x1b[97m",
    },
    "hell": {
        "ruins": "\x1b[91m",
        "city": "\x1b[31m",
        "mountains": "\x1b[91m",
        "ocean": "\x1b[35m",
        "lake": "\x1b[31m",
        "forest": "\x1b[31m",
        "plains": "\x1b[33m",
    },
    "purgatory": {
        "ruins": "\x1b[90m",
        "city": "\x1b[37m",
        "mountains": "\x1b[90m",
        "ocean": "\x1b[90m",
        "lake": "\x1b[37m",
        "forest": "\x1b[90m",
        "plains": "\x1b[37m",
    },
    "dream": {
        "ruins": "\x1b[95m",
        "city": "\x1b[95m",
        "mountains": "\x1b[35m",
        "ocean": "\x1b[94m",
        "lake": "\x1b[96m",
        "forest": "\x1b[92m",
        "plains": "\x1b[95m",
    },
}

# Suggestion #26: generic per-area_type room descriptions for grid cells
# that have no cell_overrides description. {x}/{y} placeholders match
# the existing grid default_description format so authors can still
# override per-map via JSON default_description (that wins when present
# and no area_type template is wanted -- see _build_grid).
AREA_TYPE_DESCRIPTIONS = {
    "ruins": (
        "Crumbling stone and half-buried foundations mark what was once "
        "a settlement. A weathered marker reads ({x}, {y})."
    ),
    "city": (
        "Packed earth and worn paths suggest nearby settlement. A marker "
        "reads ({x}, {y})."
    ),
    "mountains": (
        "Jagged rock and thin air -- the ground climbs in every direction. "
        "A cliff-face marker reads ({x}, {y})."
    ),
    "ocean": (
        "Open water stretches to the horizon; waves slap against whatever "
        "footing you have. A buoy marker reads ({x}, {y})."
    ),
    "lake": (
        "Still water laps at a muddy shore. Reeds and insects fill the "
        "quiet. A shoreline marker reads ({x}, {y})."
    ),
    "forest": (
        "Trees close in overhead; undergrowth claws at your legs. A carved "
        "trunk marker reads ({x}, {y})."
    ),
    "plains": (
        "Open grassland rolls under a wide sky. A simple stake marker "
        "reads ({x}, {y})."
    ),
}

# Plane-flavored description overlays for suggestion #8 (optional look
# text). Used when a grid cell has no cell_overrides description AND the
# map's plane has an entry here -- otherwise AREA_TYPE_DESCRIPTIONS (or
# the map's default_description) applies.
PLANE_AREA_DESCRIPTIONS = {
    "fire": {
        "ruins": (
            "Scorched stone and melted slag mark what fire left of a "
            "structure. A heat-scarred marker reads ({x}, {y})."
        ),
        "forest": (
            "Blackened trunks stand like spears in a burned woodland. "
            "Embers still glow in the underbrush. A charred marker "
            "reads ({x}, {y})."
        ),
        "lake": (
            "A sulfur pool steams where water once was -- the surface "
            "hisses and stinks. A heat-scarred marker reads ({x}, {y})."
        ),
        "ocean": (
            "A sea of slow magma rolls under a sky of ash. A heat-scarred "
            "marker reads ({x}, {y})."
        ),
        "plains": (
            "Scorched grassland crackles underfoot; heat shimmers on "
            "every horizon. A heat-scarred marker reads ({x}, {y})."
        ),
        "mountains": (
            "Obsidian ridges and volcanic vents claw at a red sky. A "
            "heat-scarred marker reads ({x}, {y})."
        ),
        "city": (
            "Heat-warped foundations and blackened paving mark a ruined "
            "settlement. A heat-scarred marker reads ({x}, {y})."
        ),
    },
    "heaven": {
        "plains": (
            "Soft light lies over endless white grass. A bright marker "
            "reads ({x}, {y})."
        ),
        "city": (
            "Pale stone avenues run between towers of light. A radiant "
            "marker reads ({x}, {y})."
        ),
        "ruins": (
            "Weathered marble still gleams as if newly washed. A radiant "
            "marker reads ({x}, {y})."
        ),
        "forest": (
            "Silver-leafed trees hum with a quiet choir. A radiant "
            "marker reads ({x}, {y})."
        ),
        "mountains": (
            "Cloud-piercing peaks catch a sun that never sets. A radiant "
            "marker reads ({x}, {y})."
        ),
        "lake": (
            "Still water mirrors a sky without night. A radiant marker "
            "reads ({x}, {y})."
        ),
        "ocean": (
            "An endless bright sea rolls without storm. A radiant "
            "marker reads ({x}, {y})."
        ),
    },
    "hell": {
        "plains": (
            "Cracked basalt and choking heat stretch to a red horizon. A "
            "branded marker reads ({x}, {y})."
        ),
        "ruins": (
            "Blackened arches lean over pits of ash. A branded marker "
            "reads ({x}, {y})."
        ),
        "city": (
            "Iron streets ring with distant screams. A branded marker "
            "reads ({x}, {y})."
        ),
        "forest": (
            "Thorned trees drip pitch instead of sap. A branded marker "
            "reads ({x}, {y})."
        ),
        "mountains": (
            "Jagged peaks vomit smoke into a blood-red sky. A branded "
            "marker reads ({x}, {y})."
        ),
        "lake": (
            "A lake of boiling pitch steams and pops. A branded marker "
            "reads ({x}, {y})."
        ),
        "ocean": (
            "A sea of fire rolls under ashfall. A branded marker "
            "reads ({x}, {y})."
        ),
    },
    "purgatory": {
        "plains": (
            "Grey dust and half-forgotten footprints cover a liminal "
            "plain. A faded marker reads ({x}, {y})."
        ),
        "ruins": (
            "Empty halls of ash-stone wait without purpose. A faded "
            "marker reads ({x}, {y})."
        ),
        "city": (
            "Silent streets hold neither day nor night. A faded marker "
            "reads ({x}, {y})."
        ),
        "forest": (
            "Leafless trees stand in fog that never lifts. A faded "
            "marker reads ({x}, {y})."
        ),
        "mountains": (
            "Dull ridges rise into featureless cloud. A faded marker "
            "reads ({x}, {y})."
        ),
        "lake": (
            "Still grey water reflects nothing clearly. A faded marker "
            "reads ({x}, {y})."
        ),
        "ocean": (
            "A colourless sea laps without tide. A faded marker "
            "reads ({x}, {y})."
        ),
    },
    "dream": {
        "plains": (
            "Soft ground shifts underfoot like half-remembered meadow. A "
            "drifting marker reads ({x}, {y})."
        ),
        "forest": (
            "Trees rearrange when you blink. A drifting marker "
            "reads ({x}, {y})."
        ),
        "ruins": (
            "Familiar doorways lead nowhere twice. A drifting marker "
            "reads ({x}, {y})."
        ),
        "city": (
            "Streets fold into each other like nested thoughts. A "
            "drifting marker reads ({x}, {y})."
        ),
        "mountains": (
            "Impossible peaks lean at wrong angles. A drifting marker "
            "reads ({x}, {y})."
        ),
        "lake": (
            "Water shows skies that are not above you. A drifting "
            "marker reads ({x}, {y})."
        ),
        "ocean": (
            "An ocean of ink and starlight has no shore. A drifting "
            "marker reads ({x}, {y})."
        ),
    },
}

def _normalize_area_type(area_type):
    """Resolve a map JSON area_type, including legacy aliases, to a catalog value."""
    if area_type is None:
        return "plains"
    return _LEGACY_AREA_TYPE_ALIASES.get(area_type, area_type)


# D29 default view: 3 cells in each cardinal direction = 7x7 window.
MINIMAP_RADIUS = 3

# Distant landmark bands on overland look (Chebyshev distance:
# max(|dx|, |dy|)). Tunable in one place; pockets opt in with
# JSON "visible_as". Same-cell (d == 0) is omitted -- the gateway
# description + Enter line already cover standing on the landmark.
LANDMARK_NEARBY_MAX = 8
LANDMARK_DISTANCE_MAX = 20
LANDMARK_HORIZON_MAX = 35

# Filled by _link_pockets during load_all_maps; cleared at each reload.
# Key = grid_prefix (e.g. "The Wastes"); value = list of
# {"x": int, "y": int, "name": str} for pockets with visible_as.
_LANDMARKS_BY_PREFIX = {}

# Compiled once: "The Wastes (50, 50)" / "The Cinder Reach (10, 10)".
# Groups: prefix, x, y. Used by parse_grid_key for rooms that were not
# stamped at load (defensive) and by tests.
_GRID_KEY_RE = re.compile(
    r"^(.+) \((-?\d+), (-?\d+)\)$"
)


def _bearing_8way(dx, dy):
    """Map a grid delta to one of eight compass labels, or None if (0, 0).

    Convention matches _link_grid_neighbors: +y is north, +x is east.
    When one axis is at least twice the other, use a cardinal; otherwise
    use the matching diagonal (northeast, southwest, …).
    """
    if dx == 0 and dy == 0:
        return None
    ax, ay = abs(dx), abs(dy)
    # Mostly north/south (horizontal component small).
    if ax * 2 <= ay:
        return "north" if dy > 0 else "south"
    # Mostly east/west (vertical component small).
    if ay * 2 <= ax:
        return "east" if dx > 0 else "west"
    # Diagonal: concatenate ("north" + "east" -> "northeast").
    ns = "north" if dy > 0 else "south"
    ew = "east" if dx > 0 else "west"
    return ns + ew


def _landmark_band_phrase(distance):
    """Return the look prefix for a Chebyshev distance, or None if hidden.

    Bands (inclusive): nearby 1..NEARBY_MAX, distance NEARBY_MAX+1..DISTANCE_MAX,
    horizon DISTANCE_MAX+1..HORIZON_MAX. Distance 0 and beyond HORIZON_MAX
    return None (caller omits the line).
    """
    if distance <= 0 or distance > LANDMARK_HORIZON_MAX:
        return None
    if distance <= LANDMARK_NEARBY_MAX:
        return "Nearby"
    if distance <= LANDMARK_DISTANCE_MAX:
        return "In the distance"
    return "On the horizon"


def landmark_vista_lines(room):
    """Build look extras naming distant landmarks on this overland cell.

    Only stamped grid rooms participate (grid_prefix + grid_x/y). Landmarks
    come from pockets that authored visible_as at load time. Returns an
    empty list indoors, off-grid, or when nothing is in range. Lines are
    sorted nearer-first, then by name, so output stays stable.
    """
    prefix = getattr(room, "grid_prefix", None)
    px = getattr(room, "grid_x", None)
    py = getattr(room, "grid_y", None)
    if prefix is None or px is None or py is None:
        return []
    landmarks = _LANDMARKS_BY_PREFIX.get(prefix) or []
    if not landmarks:
        return []

    scored = []
    for entry in landmarks:
        lx, ly = entry["x"], entry["y"]
        name = entry["name"]
        dx = lx - px
        dy = ly - py
        # Chebyshev: king-move distance on the grid (fits 8-way bearings).
        distance = max(abs(dx), abs(dy))
        phrase = _landmark_band_phrase(distance)
        if phrase is None:
            continue
        direction = _bearing_8way(dx, dy)
        if direction is None:
            continue
        scored.append((distance, name, phrase, direction))

    scored.sort(key=lambda row: (row[0], row[1].lower()))
    return [
        f"{phrase} to the {direction}: {name}."
        for _distance, name, phrase, direction in scored
    ]


def parse_grid_key(key):
    """Parse a procedural grid room key into (prefix, x, y), or None.

    Grid keys are authored as f\"{prefix} ({x}, {y})\" in _build_grid --
    e.g. \"The Wastes (50, 50)\". Hand-authored rooms (\"Central Plaza\")
    return None so callers can tell \"not on a map grid\" from a parse
    error without raising.
    """
    match = _GRID_KEY_RE.match(key)
    if not match:
        return None
    prefix, x_str, y_str = match.group(1), match.group(2), match.group(3)
    return prefix, int(x_str), int(y_str)


def _cell_color(plane, area_type):
    """ANSI escape for one minimap cell, or empty string if unknown.

    Prefers a plane-specific palette (PLANE_AREA_COLORS) then falls back
    to the default AREA_TYPE_COLOR. Missing keys stay uncolored -- the
    letter glyph still carries the meaning (section 8 a11y).
    """
    plane_palette = PLANE_AREA_COLORS.get(plane) or {}
    return plane_palette.get(area_type) or AREA_TYPE_COLOR.get(area_type, "")


def _cell_glyph(area_type):
    """Single-character terrain token for one area_type (D29)."""
    return AREA_TYPE_GLYPH.get(area_type, "?")


def render_minimap(rooms, center_room, radius=MINIMAP_RADIUS, use_color=True):
    """Build a local ASCII terrain window around `center_room`.

    Returns a multi-line string (rows joined by \\n, NOT \\r\\n -- the
    command handler adds telnet line endings) or None when the room is
    not a stamped grid cell. North is higher y (top of the printout),
    matching _link_grid_neighbors. The player's cell is always '@'.

    `rooms` is the shared game.rooms dict; neighbors are looked up by
    reconstructing keys from grid_prefix + coordinates so we never walk
    exits (portals like 'in'/'out' must not pull nested rooms onto the
    overland map).
    """
    prefix = getattr(center_room, "grid_prefix", None)
    cx = getattr(center_room, "grid_x", None)
    cy = getattr(center_room, "grid_y", None)
    # Defensive fallback: older rooms or tests that skipped stamping.
    if prefix is None or cx is None or cy is None:
        parsed = parse_grid_key(center_room.key)
        if parsed is None:
            return None
        prefix, cx, cy = parsed

    rows = []
    for dy in range(radius, -radius - 1, -1):  # north (high y) first
        cells = []
        for dx in range(-radius, radius + 1):   # west (low x) first
            x, y = cx + dx, cy + dy
            if dx == 0 and dy == 0:
                cells.append("@")
                continue
            key = f"{prefix} ({x}, {y})"
            neighbor = rooms.get(key)
            if neighbor is None:
                # Off the grid edge -- blank, not '?', so the map's shape
                # at a boundary is obvious without inventing terrain.
                cells.append(" ")
                continue
            glyph = _cell_glyph(getattr(neighbor, "area_type", "plains"))
            if use_color:
                color = _cell_color(
                    getattr(neighbor, "plane", "earth"),
                    getattr(neighbor, "area_type", "plains"),
                )
                if color:
                    glyph = f"{color}{glyph}{ANSI_RESET}"
            cells.append(glyph)
        rows.append("".join(cells))

    legend = " ".join(
        f"{AREA_TYPE_GLYPH[t]}={t}" for t in sorted(AREA_TYPE_GLYPH)
    )
    header = f"{prefix} ({cx}, {cy})  (@ = you)"
    return "\n".join([header, *rows, legend])


def _description_for_cell(plane, area_type, x, y, map_default):
    """Pick the room description for one grid cell with no override.

    Priority: plane+area_type flavor (PLANE_AREA_DESCRIPTIONS) >
    area_type template (AREA_TYPE_DESCRIPTIONS) > the map file's own
    default_description. Keeps suggestion #26's per-type generics while
    letting a Fire Plane still sound like fire without duplicating every
    cell in JSON.
    """
    plane_table = PLANE_AREA_DESCRIPTIONS.get(plane) or {}
    template = plane_table.get(area_type) or AREA_TYPE_DESCRIPTIONS.get(
        area_type
    )
    if template is None:
        template = map_default
    return template.format(x=x, y=y)


def _load_map_files():
    """Read and parse every content/maps/*.json file.

    Returns a list of (filename, data) pairs -- filename is kept alongside
    each dict purely so error messages below can say WHICH file a bad
    reference came from. sorted() makes load order (and therefore which
    file an error blames first) deterministic across platforms, even
    though correctness never depends on order: every exit is resolved
    against the shared `rooms` dict in a second pass, not the current
    file's own rooms.
    """
    paths = sorted(glob.glob(os.path.join(_MAPS_DIR, "*.json")))
    maps = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            # os.path.basename: just "wastes.json", not the full path --
            # shorter and just as useful in an error message.
            maps.append((os.path.basename(path), json.load(f)))
    return maps


def _add_room(rooms, filename, key, description, gravity=1.0,
              wilderness=None, area_type=None, bestiary_categories=None,
              plane=None, realm=None, map_id=None,
              grid_prefix=None, grid_x=None, grid_y=None,
              resources=None, zone=None, resource_capacity=None,
              consecrated=None, evil_zone=None, is_house=None, is_grave=None,
              private_home=None, vampire_safe=None, hunter_safe=None,
              evil_ward=None,
              is_cell=None, robable=None,
              outdoor=None, floor_sleep=None, vampire_nest=None, hospital=None,
              devils_trap=None, salt_line=None, iron_ward=None,
              devils_gate=None, unholy=None, crossroads=None, demon_deal=None,
              jobs=None, dark=None,
              hidden_directions=None):
    """Create one Room and insert it into the shared `rooms` dict.

    Raises loudly if `key` is already taken -- by an earlier room in this
    same file OR by a different file entirely -- the same "fail loud at
    boot" spirit as content.py's _validate(), just guarding map data
    instead of character data. A silent overwrite here would mean two
    rooms silently collapsing into one, which is a much worse bug to find
    later than a boot-time crash.

    Optional plane/realm/map_id/grid_* args are stamped by _build_grid
    (and by hand-authored rooms that inherit their map file's plane) for
    the D29 minimap, suggestion #8 overlays, and realm catalog tooling.
    """
    if key in rooms:
        raise ValueError(
            f"{filename}: room key {key!r} is already used by another "
            "map file or an earlier entry in this one"
        )
    room = Room(key, description)
    room.gravity = gravity
    # area_type=None (JSON omitted it) falls back to Room's own default
    # ("plains") for backward compatibility with map files written before
    # this field existed. Legacy JSON that still says "wilderness" is
    # remapped to plains (bug #26: wilderness is a flag, not terrain).
    # Anything explicitly present must be a real, catalogued value though
    # ("fail loud" -- same spirit as the key-collision check above).
    room.area_type = _normalize_area_type(area_type)
    if room.area_type not in AREA_TYPES:
        raise ValueError(
            f"{filename}: room key {key!r} has unknown area_type "
            f"{room.area_type!r} -- must be one of {sorted(AREA_TYPES)}"
        )
    # wilderness=None means the JSON didn't specify it at all -- default
    # from area_type (WILD_AREA_TYPES above), the same "unspecified vs.
    # explicit" distinction bestiary_categories already uses below. An
    # explicit true/false in the JSON always wins even if it disagrees
    # with the area type.
    room.wilderness = (
        wilderness if wilderness is not None
        else room.area_type in WILD_AREA_TYPES
    )
    # bestiary_categories=None means the JSON didn't specify it at all --
    # default to the area_type's own catalog list (list(...) copies it, so
    # rooms sharing an area_type never share the SAME list object -- a
    # future planar-influence mutation on one room must never leak into
    # another). An explicit [] in JSON is different from an unspecified
    # one: it means "no categories, on purpose," so it's kept as-is rather
    # than falling back to the area_type default.
    room.bestiary_categories = (
        list(bestiary_categories) if bestiary_categories is not None
        else list(AREA_TYPES[room.area_type])
    )
    # plane=None -> Room's default ("earth"). Map JSON can override per
    # file (see load_all_maps); grid cells and authored rooms in that
    # file share the same plane unless a future per-cell override lands.
    if plane is not None:
        if plane not in PLANES:
            raise ValueError(
                f"{filename}: unknown plane {plane!r} -- must be one of "
                f"{sorted(PLANES)}"
            )
        room.plane = plane
    # Realm: explicit JSON wins; else derive from plane; else Room default.
    if realm is not None:
        room.realm = realm
    else:
        room.realm = REALM_FOR_PLANE.get(room.plane, "prime")
    if map_id is not None:
        room.map_id = map_id
    # Radiant town simulation: NEEDS resource tags + home-zone id + optional
    # per-tag scarcity caps (see Room.__init__). resources=None/zone=None mean
    # the JSON omitted them -- keep Room's defaults (no resources, ungrouped),
    # so every map file written before this field existed still loads. list(...)
    # / dict(...) copy so rooms never share a mutable object (same reasoning as
    # bestiary_categories above -- a live shop-close on one room must not leak).
    if resources is not None:
        for tag in resources:
            if tag not in KNOWN_RESOURCE_TAGS:
                raise ValueError(
                    f"room {key!r}: unknown resource tag {tag!r} -- "
                    f"must be one of {sorted(KNOWN_RESOURCE_TAGS)}"
                )
        room.resources = list(resources)
    if jobs is not None:
        if not isinstance(jobs, list):
            raise ValueError(
                f"room {key!r}: 'jobs' must be a list of job id strings"
            )
        for jid in jobs:
            if not isinstance(jid, str) or not jid.strip():
                raise ValueError(
                    f"room {key!r}: each jobs entry must be a non-empty string"
                )
        room.jobs = [j.strip() for j in jobs]
    if zone is not None:
        room.zone = zone
    if resource_capacity is not None:
        for tag in resource_capacity:
            if tag not in KNOWN_RESOURCE_TAGS:
                raise ValueError(
                    f"room {key!r}: unknown resource_capacity key {tag!r} -- "
                    f"must be one of {sorted(KNOWN_RESOURCE_TAGS)}"
                )
        room.resource_capacity = dict(resource_capacity)
    # Divine faith economy: consecrated=None (omitted) keeps Room default
    # False; explicit true marks chapels / holy ground for minister bonus.
    if consecrated is not None:
        room.consecrated = bool(consecrated)
    # D34: eviltown flag -- evil_zone=None (omitted) keeps Room default False;
    # explicit true marks a haunt for evil NPCs (a sewer, say) within an
    # otherwise ordinary settlement. See Room.evil_zone's own comment.
    if evil_zone is not None:
        room.evil_zone = bool(evil_zone)
    # Sanctuary flags -- omitted keeps Room defaults (False). Vampires avoid
    # vampire_safe; hunters avoid hunter_safe (see supers/cadence.py).
    # evil_ward hard-blocks Demons / Vampires / possessed / evil alignment
    # (see supers/wards.py + move_gate).
    if vampire_safe is not None:
        room.vampire_safe = bool(vampire_safe)
    if hunter_safe is not None:
        room.hunter_safe = bool(hunter_safe)
    if evil_ward is not None:
        room.evil_ward = bool(evil_ward)
    # Cadence D39/D49: house homes and grave plots (omitted -> Room defaults).
    if is_house is not None:
        room.is_house = bool(is_house)
    if is_grave is not None:
        room.is_grave = bool(is_grave)
    # Private-home hard door (entryway / apartment unit). Omitted -> False.
    if private_home is not None:
        room.private_home = bool(private_home)
    # Jail cell + robable counters (crime / deputy loop).
    if is_cell is not None:
        room.is_cell = bool(is_cell)
    if robable is not None:
        room.robable = bool(robable)
    # Outdoor exposure: explicit JSON wins; omitted defaults to wilderness
    # so overland grids are outdoor without tagging every cell. Hand-
    # authored indoor rooms omit the field and stay False (Room default)
    # unless wilderness=true. Town streets set "outdoor": true explicitly.
    if outdoor is not None:
        room.outdoor = bool(outdoor)
    else:
        room.outdoor = bool(room.wilderness)
    # Floor-sleep / vampire-nest flags (omitted -> Room defaults False).
    if floor_sleep is not None:
        room.floor_sleep = bool(floor_sleep)
    if vampire_nest is not None:
        room.vampire_nest = bool(vampire_nest)
    # Evil Strikes Back: Town Clinic / hospital rooms.
    if hospital is not None:
        room.hospital = bool(hospital)
    # Thin D44 authored traps (omitted -> Room defaults False).
    if devils_trap is not None:
        room.devils_trap = bool(devils_trap)
    if salt_line is not None:
        room.salt_line = bool(salt_line)
    if iron_ward is not None:
        room.iron_ward = bool(iron_ward)
    # D47 Devil's Gate rooms (omitted -> Room default False).
    if devils_gate is not None:
        room.devils_gate = bool(devils_gate)
    # D47/D48 Demon travel whitelist (omitted -> Room defaults False).
    if unholy is not None:
        room.unholy = bool(unholy)
    if crossroads is not None:
        room.crossroads = bool(crossroads)
    if demon_deal is not None:
        room.demon_deal = bool(demon_deal)
    # D67 dark rooms (omitted -> Room default False).
    if dark is not None:
        room.dark = bool(dark)
    # D66 secret exits: list of direction strings; validated against
    # exits after _link_room_exits. Omitted keeps empty tuple.
    if hidden_directions is not None:
        if not isinstance(hidden_directions, list):
            raise ValueError(
                f"room {key!r}: 'hidden_directions' must be a list of "
                "direction strings"
            )
        cleaned = []
        for d in hidden_directions:
            if not isinstance(d, str) or not d.strip():
                raise ValueError(
                    f"room {key!r}: each hidden_directions entry must be "
                    "a non-empty string"
                )
            cleaned.append(d.strip().lower())
        room.hidden_directions = tuple(cleaned)
    if grid_prefix is not None:
        room.grid_prefix = grid_prefix
        room.grid_x = grid_x
        room.grid_y = grid_y
    rooms[key] = room


def _resolve_plane_and_realm(filename, data):
    """Validate map-level plane and return (plane, realm).

    Omitted plane defaults to earth/prime (Room defaults). Explicit plane
    must be in PLANES; optional realm must match REALM_FOR_PLANE[plane].
    """
    plane = data.get("plane")
    if plane is None:
        plane = "earth"
    if plane not in PLANES:
        raise ValueError(
            f"{filename}: unknown plane {plane!r} -- must be one of "
            f"{sorted(PLANES)}"
        )
    expected_realm = REALM_FOR_PLANE[plane]
    realm = data.get("realm", expected_realm)
    if realm != expected_realm:
        raise ValueError(
            f"{filename}: realm {realm!r} does not match plane {plane!r} "
            f"(expected {expected_realm!r})"
        )
    return plane, realm


def _build_grid(rooms, filename, grid, plane=None, realm=None, map_id=None):
    """Build every cell of one map's procedural grid into `rooms`.

    This is Milestone F's old 100x100 Wastes loop, generalized to any
    width/height/prefix so a second, smaller grid (e.g. a 20x20 Fire
    Plane) can reuse it verbatim. Eagerly creates every cell -- cheap,
    since Room is just data -- using per-area_type / per-plane description
    templates (see _description_for_cell) unless `cell_overrides` supplies
    a specific description for that cell (e.g. a settlement gateway needs
    its own text, not the generic terrain line).

    `plane` / `realm` / `map_id` are stamped onto every cell for minimap
    overlays and map-registry tooling.
    """
    prefix = grid["key_prefix"]
    width = grid["width"]
    height = grid["height"]
    gravity = grid.get("gravity", 1.0)
    # .get(...) with no default (None, not False) preserves "unspecified
    # vs. explicit" -- see _add_room's wilderness=None case.
    wilderness = grid.get("wilderness")
    # The D25 terrain tag (see AREA_TYPES above); .get(...) with no default
    # leaves this None when the JSON omits it, so _add_room's "unspecified
    # falls back to wilderness" rule applies uniformly instead of this
    # function silently pre-deciding a different default.
    area_type = grid.get("area_type")
    # Which bestiary.py categories (see bestiary.py's module docstring)
    # this grid's rooms are eligible to spawn from -- e.g. the Wastes
    # grid's ["earth-dweller"], the Cinder Reach grid's ["fire-being"].
    # This is the grid-wide default; a specific cell can still punch
    # through it via cell_overrides below (a LIVE planar-influence
    # override -- swapping Room.bestiary_categories at runtime -- is a
    # separate, not-yet-built mechanic; see docs/SYSTEMS_DESIGN.md's
    # roadmap item). .get(...) with no default (None, not []) preserves
    # the "unspecified vs. explicitly empty" distinction _add_room relies
    # on to decide whether to fall back to the area_type's own default
    # categories.
    bestiary_categories = grid.get("bestiary_categories")
    default_description = grid["default_description"]
    overrides = grid.get("cell_overrides", {})

    for x in range(width):
        for y in range(height):
            key = f"{prefix} ({x}, {y})"
            override = overrides.get(f"{x},{y}", {})
            # A cell override can also punch through the grid's
            # area_type/bestiary_categories for just this one cell -- e.g.
            # a settlement gateway sitting in an otherwise-uniform
            # wilderness grid. .get(...) falls back to the grid-wide value
            # whenever the override doesn't mention that field -- "override
            # only what you need, inherit the rest," the same shape as the
            # description override just above. This is what the standalone
            # map editor tool (docs/SYSTEMS_DESIGN.md section 9) writes
            # when an author paints a single cell a different area type
            # than the rest of the grid.
            cell_area_type = override.get("area_type", area_type)
            cell_bestiary_categories = override.get(
                "bestiary_categories", bestiary_categories)
            # Description priority: explicit cell override > plane/area
            # template > map default_description (suggestion #26 / #8).
            if "description" in override:
                description = override["description"]
            else:
                # cell_area_type may still be None here (grid omitted
                # area_type too) -- treat that as the grid default.
                resolved_type = _normalize_area_type(cell_area_type)
                # When a cell inherits the grid's default terrain (no
                # area_type override), the map file's default_description
                # wins over generic per-type templates -- so The Wastes can
                # keep its scrub-line default even though the terrain tag
                # is "plains", and painted forest cells still get forest
                # flavor from _description_for_cell below.
                grid_default_type = _normalize_area_type(area_type)
                if (
                    resolved_type == grid_default_type
                    and default_description
                ):
                    description = default_description.format(x=x, y=y)
                else:
                    description = _description_for_cell(
                        plane or "earth",
                        resolved_type,
                        x, y,
                        default_description,
                    )
            _add_room(
                rooms, filename, key, description, gravity,
                wilderness, cell_area_type, cell_bestiary_categories,
                plane=plane, realm=realm, map_id=map_id,
                grid_prefix=prefix, grid_x=x, grid_y=y,
            )


def _link_grid_neighbors(rooms, grid):
    """Wire north/south/east/west exits between every cell of one map's
    grid. Cardinal convention: north = y+1, south = y-1, east = x+1,
    west = x-1 -- same as the old build_world(). Edges omit the outward
    exit (no wraparound), so cmd_move's "You can't go that way" hard-walls
    the grid boundary exactly as it always has.
    """
    prefix = grid["key_prefix"]
    width = grid["width"]
    height = grid["height"]
    for x in range(width):
        for y in range(height):
            room = rooms[f"{prefix} ({x}, {y})"]
            if y + 1 < height:
                room.exits["north"] = rooms[f"{prefix} ({x}, {y + 1})"]
            if y - 1 >= 0:
                room.exits["south"] = rooms[f"{prefix} ({x}, {y - 1})"]
            if x + 1 < width:
                room.exits["east"] = rooms[f"{prefix} ({x + 1}, {y})"]
            if x - 1 >= 0:
                room.exits["west"] = rooms[f"{prefix} ({x - 1}, {y})"]


def _link_grid_portals(rooms, filename, grid):
    """Wire any extra named exits onto specific grid cells -- e.g. the
    Wastes gateway cell's "in" exit, or a Fire Plane gateway's "out".

    `to_room` is looked up in the shared `rooms` dict built from EVERY
    file, not just this one -- that's the whole mechanism that lets a
    grid cell in one map open onto a hand-authored room (or another
    grid's cell) in a totally different map file.
    """
    prefix = grid["key_prefix"]
    for portal in grid.get("portals", []):
        key = f"{prefix} ({portal['x']}, {portal['y']})"
        to_room = portal["to_room"]
        if to_room not in rooms:
            raise ValueError(
                f"{filename}: portal from {key!r} points at unknown room "
                f"{to_room!r}"
            )
        rooms[key].exits[portal["direction"]] = rooms[to_room]


def _link_room_exits(rooms, filename, room_data):
    """Resolve one hand-authored room's `exits` dict against the shared
    `rooms` dict. Exactly the same cross-map mechanism as
    _link_grid_portals above, just for authored rooms instead of grid
    cells -- this is how Central Plaza's "in" exit can point at a room
    defined in an entirely different map file's JSON.
    """
    room = rooms[room_data["key"]]
    for direction, to_room in room_data.get("exits", {}).items():
        if to_room not in rooms:
            raise ValueError(
                f"{filename}: {room_data['key']!r}'s {direction!r} exit "
                f"points at unknown room {to_room!r}"
            )
        room.exits[direction] = rooms[to_room]
    # D66: every hidden direction must actually be an exit (fail loud).
    for direction in getattr(room, "hidden_directions", ()) or ():
        if direction not in room.exits:
            raise ValueError(
                f"{filename}: {room_data['key']!r} lists hidden direction "
                f"{direction!r} but has no matching exit"
            )


def _pocket_enter_aliases(hub, pocket):
    """Build lowercase enter <name> aliases for one pocket hub.

    Always includes the hub room key and Room.zone (when set). Optional
    pocket JSON \"enter_as\": [\"city\", \"town\"] adds more player-facing
    names without changing the hub key.
    """
    aliases = set()
    aliases.add(hub.key.lower())
    zone = getattr(hub, "zone", None)
    if zone:
        aliases.add(str(zone).lower())
    for extra in pocket.get("enter_as") or []:
        aliases.add(str(extra).strip().lower())
    aliases.discard("")
    return sorted(aliases)


def _link_pockets(rooms, filename, data):
    """Wire grid <-> pocket zone travel via enter/exit (not exits{}).

    Each pocket names a grid cell (at [x,y]) and a hub_room. Links land on
    Room.zone_entries (gateway cell) and Room.zone_exit_to (hub + every
    room sharing the hub's zone) so cardinal / in / out movement never
    walks the pocket boundary. Legacy exits['in']/['out'] and matching
    grid.portals for the same pair are stripped so the verbs stay separate.
    """
    pockets = data.get("pockets") or []
    if not pockets:
        return []
    grid = data.get("grid")
    if not grid:
        raise ValueError(
            f"{filename}: pockets[] require a grid block (key_prefix)"
        )
    prefix = grid["key_prefix"]
    width = int(grid["width"])
    height = int(grid["height"])
    hub_keys = []
    for i, pocket in enumerate(pockets):
        kind = pocket.get("kind", "landmark")
        if kind not in POCKET_KINDS:
            raise ValueError(
                f"{filename}: pockets[{i}] kind {kind!r} -- must be one of "
                f"{sorted(POCKET_KINDS)}"
            )
        at = pocket.get("at")
        if not (isinstance(at, (list, tuple)) and len(at) == 2):
            raise ValueError(
                f"{filename}: pockets[{i}] needs \"at\": [x, y]"
            )
        x, y = int(at[0]), int(at[1])
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(
                f"{filename}: pockets[{i}] at [{x}, {y}] outside grid "
                f"{width}x{height}"
            )
        hub_key = pocket.get("hub_room")
        if not hub_key or hub_key not in rooms:
            raise ValueError(
                f"{filename}: pockets[{i}] hub_room {hub_key!r} unknown"
            )
        # Legacy field names kept for content compat; ignored for exits{}.
        direction = pocket.get("direction", "in")
        return_direction = pocket.get("return_direction", "out")
        cell_key = f"{prefix} ({x}, {y})"
        cell = rooms[cell_key]
        hub = rooms[hub_key]

        # Fail loud if a non-pocket exit already claimed these directions
        # toward a DIFFERENT room -- authors must not mix schemas.
        existing = cell.exits.get(direction)
        if existing is not None and existing is not hub:
            raise ValueError(
                f"{filename}: pockets[{i}] conflicts -- {cell_key!r} "
                f"{direction!r} already goes to {existing.key!r}"
            )
        existing_back = hub.exits.get(return_direction)
        if existing_back is not None and existing_back is not cell:
            raise ValueError(
                f"{filename}: pockets[{i}] conflicts -- {hub_key!r} "
                f"{return_direction!r} already goes to {existing_back.key!r}"
            )

        # Zone travel (enter / exit) -- not movement exits.
        for alias in _pocket_enter_aliases(hub, pocket):
            prior = cell.zone_entries.get(alias)
            if prior is not None and prior is not hub:
                raise ValueError(
                    f"{filename}: pockets[{i}] enter alias {alias!r} "
                    f"already points at {prior.key!r}"
                )
            cell.zone_entries[alias] = hub
        hub.zone_exit_to = cell
        # Every room in the same Cadence zone can `exit` back to the grid.
        zone_id = getattr(hub, "zone", None)
        if zone_id:
            for other in rooms.values():
                if getattr(other, "zone", None) == zone_id:
                    other.zone_exit_to = cell
        else:
            hub.zone_exit_to = cell

        # Strip legacy in/out so cmd_move / in / out cannot walk the link.
        if cell.exits.get(direction) is hub:
            del cell.exits[direction]
        if hub.exits.get(return_direction) is cell:
            del hub.exits[return_direction]
        # Drop matching grid.portals entries so a later re-link isn't needed;
        # portals already applied in pass 2 before pockets -- strip now.
        portals = grid.get("portals") or []
        grid["portals"] = [
            p for p in portals
            if not (
                int(p.get("x", -1)) == x
                and int(p.get("y", -1)) == y
                and p.get("direction") == direction
                and p.get("to_room") == hub_key
            )
        ]
        # Opt-in distant look vista: only pockets with a player-facing name.
        visible_as = str(pocket.get("visible_as") or "").strip()
        if visible_as:
            _LANDMARKS_BY_PREFIX.setdefault(prefix, []).append({
                "x": x,
                "y": y,
                "name": visible_as,
            })
        hub_keys.append(hub_key)
    return hub_keys


def load_all_maps():
    """Load every content/maps/*.json file and build the live world from
    them.

    Returns (rooms, start_room, seed_items) -- the exact shape world.py's
    build_world() has always returned, so nothing downstream (server.py,
    persistence.py) needs to know or care that the map is now data-driven:
    - rooms: every Room built, keyed by its name, across every map file.
    - start_room: the one room a JSON file marked "is_start": true.
    - seed_items: (Item, room_key) pairs to place ONLY on a brand-new
      database (server.py already guards this with persistence.is_seeded).

    Also refreshes module-level LAST_MAP_REGISTRY for Game.map_registry
    and rebuilds _LANDMARKS_BY_PREFIX from pocket visible_as fields.

    Two passes, for the same reason the old build_world() needed two
    passes for its grid: pass 1 creates every Room from every file FIRST,
    so pass 2 can freely wire an exit at ANY of them -- including one from
    a file loaded before or after the current one -- without caring about
    load order. Pocket zone links (enter/exit) are wired after exits so
    legacy in/out portal pairs can be stripped cleanly.
    """
    global LAST_MAP_REGISTRY, _LANDMARKS_BY_PREFIX
    map_files = _load_map_files()

    # Fresh registry each load so copyover / re-import never duplicates.
    _LANDMARKS_BY_PREFIX = {}

    rooms = {}
    start_room = None
    seed_items = []
    registry = {}
    seen_prefixes = {}

    # Pass 1: create every Room (grid cells + hand-authored rooms).
    for filename, data in map_files:
        plane, realm = _resolve_plane_and_realm(filename, data)
        map_id = data.get("id") or os.path.splitext(filename)[0]
        grid = data.get("grid")
        if grid:
            prefix = grid["key_prefix"]
            if prefix in seen_prefixes:
                raise ValueError(
                    f"{filename}: grid key_prefix {prefix!r} already used "
                    f"by {seen_prefixes[prefix]}"
                )
            seen_prefixes[prefix] = filename
            _build_grid(
                rooms, filename, grid,
                plane=plane, realm=realm, map_id=map_id,
            )
        for room_data in data.get("rooms", []):
            _add_room(
                rooms,
                filename,
                room_data["key"],
                room_data["description"],
                room_data.get("gravity", 1.0),
                room_data.get("wilderness"),
                room_data.get("area_type"),
                room_data.get("bestiary_categories"),
                plane=plane,
                realm=realm,
                map_id=map_id,
                resources=room_data.get("resources"),
                zone=room_data.get("zone"),
                resource_capacity=room_data.get("resource_capacity"),
                consecrated=room_data.get("consecrated"),
                evil_zone=room_data.get("evil_zone"),
                is_house=room_data.get("is_house"),
                is_grave=room_data.get("is_grave"),
                private_home=room_data.get("private_home"),
                vampire_safe=room_data.get("vampire_safe"),
                hunter_safe=room_data.get("hunter_safe"),
                evil_ward=room_data.get("evil_ward"),
                is_cell=room_data.get("is_cell"),
                robable=room_data.get("robable"),
                outdoor=room_data.get("outdoor"),
                floor_sleep=room_data.get("floor_sleep"),
                vampire_nest=room_data.get("vampire_nest"),
                hospital=room_data.get("hospital"),
                devils_trap=room_data.get("devils_trap"),
                salt_line=room_data.get("salt_line"),
                iron_ward=room_data.get("iron_ward"),
                devils_gate=room_data.get("devils_gate"),
                unholy=room_data.get("unholy"),
                crossroads=room_data.get("crossroads"),
                demon_deal=room_data.get("demon_deal"),
                jobs=room_data.get("jobs"),
                dark=room_data.get("dark"),
                hidden_directions=room_data.get("hidden_directions"),
            )
        registry[map_id] = {
            "realm": realm,
            "plane": plane,
            "grid_prefix": grid["key_prefix"] if grid else None,
            "width": grid["width"] if grid else None,
            "height": grid["height"] if grid else None,
            "pocket_hubs": list(
                p.get("hub_room") for p in (data.get("pockets") or [])
                if p.get("hub_room")
            ),
            "filename": filename,
        }

    # Pass 2: every Room now exists, so wire exits and collect the rest.
    for filename, data in map_files:
        grid = data.get("grid")
        if grid:
            _link_grid_neighbors(rooms, grid)
            _link_grid_portals(rooms, filename, grid)
        for room_data in data.get("rooms", []):
            _link_room_exits(rooms, filename, room_data)
            for item_data in room_data.get("seed_items", []):
                from engine.hooks import make_world_item
                room_key = room_data["key"]
                seed_items.append((
                    make_world_item(
                        item_data,
                        where=f"{filename}: room {room_key!r} seed_items",
                    ),
                    room_key,
                ))
            if room_data.get("is_start"):
                if start_room is not None:
                    raise ValueError(
                        f"{filename}: more than one room is marked "
                        f"'is_start' (already had {start_room.key!r})"
                    )
                start_room = rooms[room_data["key"]]
        # After portals + authored exits: convert pockets to enter/exit.
        _link_pockets(rooms, filename, data)

    if start_room is None:
        raise ValueError(
            "no room across content/maps/*.json is marked 'is_start'"
        )

    LAST_MAP_REGISTRY = registry
    return rooms, start_room, seed_items
