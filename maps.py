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
enter_as}. The loader wires Room.zone_entries on the grid cell and stamps
zone_exit + zone_exit_to on the hub only (the mouth), so players use
enter <name> / exit from that room -- not from house interiors. Do not
also author grid.portals or hub exits.out for the same pair; leftover
in/out for that pair is stripped as a safety net. Nested indoor doors
still use in/out/leave (elemental reaches use their own portals; Central
Plaza no longer links into Cinder Reach). Authoring SoT:
docs/CONTENT_AUTHORING.md, help build-maps, map editor Pockets panel.
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
# Pocket / zone-only JSON (no overland grid) may live here -- same schema
# as content/maps/*.json. Folder vocabulary (map file vs zone file vs
# Cadence room ``zone`` string): docs/CONTENT_AUTHORING.md + help build-maps.
# Alien / plane stubs without a grid (stellar_orbit, umbral_vault) stay under
# content/maps/ on purpose -- do not move them into zones/.
_ZONES_DIR = os.path.join(os.path.dirname(__file__), "content", "zones")


def set_maps_dir(path):
    """Point the map loader at a different content/maps/ directory.

    Pass None to restore the default (this repo's own content/maps/).
    Does not change content/zones/ (use set_zones_dir).
    """
    global _MAPS_DIR
    _MAPS_DIR = (
        path if path is not None
        else os.path.join(os.path.dirname(__file__), "content", "maps")
    )


def get_maps_dir():
    """Return the directory load_all_maps() currently reads from."""
    return _MAPS_DIR


def set_zones_dir(path):
    """Point the zone loader at a different content/zones/ directory."""
    global _ZONES_DIR
    _ZONES_DIR = (
        path if path is not None
        else os.path.join(os.path.dirname(__file__), "content", "zones")
    )


def get_zones_dir():
    """Return the pocket/zone JSON directory."""
    return _ZONES_DIR

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
    # Fire Plane Heart Furnace cells (Cinder Reach) -- landmark heat,
    # not wild-by-default (see WILD_AREA_TYPES); no random wilderness rolls.
    "furnace": [],
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
    # Human Mundane Nerve Work floors (Path-flavored rooms in wastes town).
    # Preference / montage tags only -- they do NOT create need meters.
    # See supers/mundane.py NERVE_WORK_TAGS.
    "clinic", "jail", "case", "tip", "workshop", "salvage",
    "barracks", "spar", "ritual", "fence",
    "lab", "patrol",
    # Tornado / severe-weather cellar (weather_climatology shelter table).
    "storm_shelter",
})

# Controlled plane vocabulary (map JSON top-level "plane"). Realm is the
# family; plane is the specific dimension. Unknown planes fail loud at load.
PLANES = frozenset({
    "earth", "fire", "water", "air", "stone",
    "heaven", "hell", "purgatory", "dream",
    "stellar", "umbral",
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
    "umbral": "void",
}

# Pocket kinds for map JSON pockets[] metadata (authors/tools).
POCKET_KINDS = frozenset({"settlement", "dungeon", "landmark"})

# Filled by the most recent load_all_maps() call -- Game may copy this onto
# game.map_registry. Keys are map file "id" strings.
LAST_MAP_REGISTRY = {}

# Map/zone files present on disk with ``"autoload": false`` (skipped at boot).
# Refreshed by load_all_maps() / catalog_map_files(). Keys are map ids.
LAST_DEFERRED_MAPS = {}

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
    "furnace": "H",
}

# Atlas / highway-map glyph set (xycoordmapUSguidelines): topography uses
# shape symbols; cities and routes stamp map_glyph on top. Used when a
# grid sets ``"glyph_set": "atlas"`` (stamped onto every cell).
ATLAS_AREA_GLYPH = {
    "ruins": ":",
    "city": "*",
    "mountains": "^",
    "ocean": "~",
    "lake": "o",
    "forest": "T",
    "plains": ".",
    "furnace": "H",
}

# Layer colors for atlas maps -- letter/glyph is still the primary signal;
# ANSI only accents (section 8 a11y). Bright routes over muted topo.
# (Foreground-only escapes -- used for non-filled atlas fallbacks and the
# legend. The FILLED "Forgotten Kingdoms" look composes fg+bg below.)
MAP_LAYER_COLOR = {
    "ocean": "\x1b[34m",           # blue water
    "plains": "\x1b[32m",          # muted green fields
    "mountains": "\x1b[90m",       # dark grey rock
    "lake": "\x1b[94m",            # bright blue
    "forest": "\x1b[32m",
    "highway": "\x1b[33m",         # yellow asphalt / Impala road
    "mountain_highway": "\x1b[33m",  # yellow pass through Rockies
    "city": "\x1b[97m",            # bright white hub letter
}

# --- Filled "Forgotten Kingdoms" atlas palette ------------------------
# When a grid opts in with ``"glyph_set": "atlas"`` we paint each cell as a
# solid COLORED BLOCK (ANSI background) with the glyph on top -- a filled
# overland map (blue sea, green land, grey rock) instead of glyphs on
# black. These are the numeric SGR codes; _room_display_color composes them
# into a "\x1b[<fg>;<bg>m" escape. Only atlas maps use this path, so the
# Wastes / elemental reaches keep their plain foreground look untouched.
# a11y: the glyph is still the primary signal (section 8) -- the ASCII map
# is a sighted surface; screen-reader players get directional text instead.
ATLAS_BG = {                        # background fill per terrain
    "ocean": "44",                  # blue sea
    "lake": "46",                   # cyan freshwater
    "plains": "42",                 # green fields
    "forest": "42",                 # green timber
    "mountains": "100",             # bright-black (grey) rock
    "city": "41",                   # red hub block
    "ruins": "100",
    "furnace": "41",
}
ATLAS_TOPO_FG = {                   # glyph color when the cell is bare topo
    "ocean": "96",                  # bright-cyan ripples on blue
    "lake": "97",
    "plains": "30",                 # black stipple on green
    "forest": "30",
    "mountains": "37",              # white peaks on grey
    "city": "97",
    "ruins": "37",
    "furnace": "97",
}
ATLAS_LAYER_FG = {                  # glyph color for a bright overlay layer
    "highway": "93",                # bright-yellow asphalt on the terrain
    "mountain_highway": "93",       # yellow pass over grey rock
    "city": "97",                   # bright-white hub letter on red
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
    "furnace": "\x1b[91m",      # bright red -- Heart Furnace heat
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


def ensure_hand_room_identity(room_data, *, where="map"):
    """Ensure a hand-authored room dict has a non-empty storage ``key``.

    Storage key is what character ``room_key`` / dig / goto persist on.
    Optional ``title`` is player-facing look text and may match the key.

    If ``key`` is missing or blank but ``title`` is set, copy title → key
    (builder typed a name and left key empty). If both are empty, raise
    ``ValueError`` so boot / Studio save fail loud instead of inventing
    a silent Plaza dump later.

    Mutates ``room_data`` in place; returns the resolved key string.
    """
    if not isinstance(room_data, dict):
        raise ValueError(f"{where}: room entry must be a dict")
    key = str(room_data.get("key") or "").strip()
    title = str(room_data.get("title") or "").strip()
    if not key and title:
        key = title
        room_data["key"] = key
    elif key:
        room_data["key"] = key
    if not key:
        raise ValueError(
            f"{where}: hand room needs a non-empty 'key' "
            f"(or a 'title' to copy into key) -- got key={room_data.get('key')!r} "
            f"title={room_data.get('title')!r}"
        )
    # Keep title only when it differs from the storage key (look falls
    # back to key otherwise).
    if title and title != key:
        room_data["title"] = title
    else:
        room_data.pop("title", None)
    return key


def qualify_hand_room_key(map_id, local_name, *, taken=None):
    """Map-scope a builder-typed room name into a globally unique storage key.

    Room identity in ``game.rooms`` is still a flat global string, but dig /
    Area Studio builders type short zone-local names (``Apartment Floor C``).
    This helper stores ``{map_id}:Apartment Floor C`` and returns the short
    string as the look ``title`` so towns can reuse the same display name.

    Rules:
      * ``map_id:Name`` already for this map → keep (normalize map_id case).
      * ``other:Name`` (explicit foreign qualify) → keep as typed.
      * bare ``Name`` → ``{map_id}:Name`` with title ``Name``.
      * If the preferred key is in ``taken``, append `` #2``, `` #3``, …

    Returns ``(storage_key, title)``. ``title`` is always the short look
    name when the key is qualified; callers may omit writing title when it
    equals the key (should not happen for qualified digs).
    """
    map_id = (map_id or "").strip() or "map"
    local = (local_name or "").strip()
    if not local:
        raise ValueError("room name required to qualify a hand-room key")
    taken = {str(k) for k in (taken or ())}

    prefix = f"{map_id}:"
    if local.lower().startswith(prefix.lower()):
        rest = local[len(prefix):].strip() or local
        key = f"{map_id}:{rest}"
        title = rest
    elif ":" in local:
        # Staff typed an explicit map:name — trust it; look name is the tail.
        key = local
        tail = local.split(":", 1)[1].strip()
        title = tail or local
    else:
        key = f"{map_id}:{local}"
        title = local

    base = key
    n = 2
    while key in taken:
        key = f"{base} #{n}"
        n += 1
    return key, title


def ensure_map_hand_room_keys(data, *, filename="map"):
    """Run ``ensure_hand_room_identity`` on every hand room in a map dict.

    Also rewrites exit / pocket hub strings that pointed at the old blank
    identity are not needed -- blank keys never linked. Returns how many
    rooms received a key copied from title.
    """
    if not isinstance(data, dict):
        return 0
    filled = 0
    rooms = data.get("rooms") or []
    for i, room_data in enumerate(rooms):
        if not isinstance(room_data, dict):
            continue
        before = str(room_data.get("key") or "").strip()
        ensure_hand_room_identity(
            room_data, where=f"{filename} rooms[{i}]",
        )
        after = str(room_data.get("key") or "").strip()
        if not before and after:
            filled += 1
    return filled


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


def _cell_glyph(area_type, *, glyph_set=None):
    """Single-character terrain token for one area_type (D29 / atlas)."""
    table = ATLAS_AREA_GLYPH if glyph_set == "atlas" else AREA_TYPE_GLYPH
    return table.get(area_type, "?")


def _room_display_glyph(room):
    """Pick the ASCII glyph for one room on minimap / full atlas.

    Priority: authored ``map_glyph`` (city letter, highway =/|/+) >
    glyph_set / area_type table. Always a single printable character.
    """
    authored = getattr(room, "map_glyph", None)
    if authored:
        text = str(authored).strip()
        if text:
            # One cell only -- never let JSON paste a multi-char mess.
            return text[0]
    glyph_set = getattr(room, "glyph_set", None)
    return _cell_glyph(getattr(room, "area_type", "plains"), glyph_set=glyph_set)


def _room_display_color(room):
    """ANSI prefix for one atlas/minimap cell, or empty string.

    Atlas maps (``glyph_set == "atlas"``) render as FILLED colored blocks:
    a background fill from the terrain (ATLAS_BG) plus a foreground glyph
    color -- bright yellow for a highway layer / white for a city, else a
    readable topo tint (ATLAS_TOPO_FG). Composed into one "\\x1b[fg;bgm"
    escape. Non-atlas maps keep the old foreground-only palette so the
    Wastes / elemental reaches look exactly as before. Glyph stays the
    primary signal (section 8 a11y); color only accents.
    """
    if getattr(room, "glyph_set", None) == "atlas":
        area = getattr(room, "area_type", "plains") or "plains"
        bg = ATLAS_BG.get(area, "40")            # default black fill
        layer = getattr(room, "map_layer", None)
        if layer:
            # A road / city overlay: bright foreground on the terrain fill.
            fg = ATLAS_LAYER_FG.get(str(layer).strip().lower())
            if fg is None:
                fg = ATLAS_TOPO_FG.get(area, "37")
        else:
            fg = ATLAS_TOPO_FG.get(area, "37")
        return f"\x1b[{fg};{bg}m"
    # Non-atlas maps: original foreground-only behavior.
    layer = getattr(room, "map_layer", None)
    if layer:
        color = MAP_LAYER_COLOR.get(str(layer).strip().lower())
        if color:
            return color
    return _cell_color(
        getattr(room, "plane", "earth"),
        getattr(room, "area_type", "plains"),
    )


def _map_legend_for(rooms_sample):
    """Legend line: atlas symbols when any cell uses them, else letters."""
    # If any room in the window uses map_glyph / atlas set, show atlas key.
    uses_atlas = False
    for room in rooms_sample:
        if room is None:
            continue
        if getattr(room, "map_glyph", None) or getattr(room, "glyph_set", None) == "atlas":
            uses_atlas = True
            break
    if uses_atlas:
        return (
            "@=you *=city .=plains ^=mtn ~=ocean o=lake "
            "==EW-hwy |=NS-hwy +=junction T=forest "
            "(bright=highway/city; muted=topo)"
        )
    return " ".join(
        f"{AREA_TYPE_GLYPH[t]}={t}" for t in sorted(AREA_TYPE_GLYPH)
    )


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
    seen_rooms = []
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
            seen_rooms.append(neighbor)
            glyph = _room_display_glyph(neighbor)
            if use_color:
                color = _room_display_color(neighbor)
                if color:
                    glyph = f"{color}{glyph}{ANSI_RESET}"
            cells.append(glyph)
        rows.append("".join(cells))

    legend = _map_legend_for(seen_rooms or [center_room])
    header = f"{prefix} ({cx}, {cy})  (@ = you)"
    return "\n".join([header, *rows, legend])


# Directions that place a neighbor on the XY town minimap (Y north).
# up/down/in/out and street-door labels stay off the XY plane.
_LAYOUT_XY_DELTA = {
    "north": (0, 1), "south": (0, -1),
    "east": (1, 0), "west": (-1, 0),
    "northeast": (1, 1), "northwest": (-1, 1),
    "southeast": (1, -1), "southwest": (-1, -1),
}

# Default radius for town exit-graph / layout windows (smaller than
# overland 7x7 -- indoor graphs get noisy fast).
TOWN_MINIMAP_RADIUS = 2
# Shorter windows when the map is embedded in look (not bare ``map``).
LOOK_TOWN_MINIMAP_RADIUS = 1   # 3x3
LOOK_GRID_MINIMAP_RADIUS = 2   # 5x5 instead of 7x7


def _town_room_glyph(room):
    """Single glyph for a town neighbor on the local map.

    Prefers authored map_glyph, else first letter of the look title,
    else '#'. Always one printable character.
    """
    authored = getattr(room, "map_glyph", None)
    if authored:
        text = str(authored).strip()
        if text:
            return text[0]
    title = ""
    if hasattr(room, "look_title"):
        try:
            title = room.look_title() or ""
        except Exception:
            title = getattr(room, "key", "") or ""
    else:
        title = getattr(room, "key", "") or ""
    for ch in str(title):
        if ch.isalnum():
            return ch.upper()
    return "#"


def _rooms_by_layout(rooms, map_id, layout_z):
    """Index hand rooms by (layout_x, layout_y) for one map_id + z layer.

    Skips grid cells and rooms missing layout. When two rooms share a
    cell, the first wins (collision is a Studio authoring issue).
    """
    index = {}
    for room in rooms.values():
        if getattr(room, "grid_prefix", None) is not None:
            continue
        if getattr(room, "map_id", None) != map_id:
            continue
        lx = getattr(room, "layout_x", None)
        ly = getattr(room, "layout_y", None)
        if lx is None or ly is None:
            continue
        lz = getattr(room, "layout_z", None)
        if lz is None:
            lz = 0
        if int(lz) != int(layout_z):
            continue
        key = (int(lx), int(ly))
        if key not in index:
            index[key] = room
    return index


def render_layout_minimap(
    rooms, center_room, radius=TOWN_MINIMAP_RADIUS, use_color=True,
):
    """Local ASCII window from Studio layout coords (same map_id + z).

    Returns None when the center room has no layout_x/y. North is high y.
    """
    cx = getattr(center_room, "layout_x", None)
    cy = getattr(center_room, "layout_y", None)
    if cx is None or cy is None:
        return None
    map_id = getattr(center_room, "map_id", None)
    if not map_id:
        return None
    lz = getattr(center_room, "layout_z", None)
    if lz is None:
        lz = 0
    index = _rooms_by_layout(rooms, map_id, lz)
    rows = []
    for dy in range(radius, -radius - 1, -1):
        cells = []
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                cells.append("@")
                continue
            neighbor = index.get((int(cx) + dx, int(cy) + dy))
            if neighbor is None:
                cells.append(" ")
                continue
            glyph = _town_room_glyph(neighbor)
            if use_color:
                color = _room_display_color(neighbor)
                if color:
                    glyph = f"{color}{glyph}{ANSI_RESET}"
            cells.append(glyph)
        rows.append("".join(cells))
    label = getattr(center_room, "zone", None) or map_id
    header = f"{label}  (@ = you)"
    legend = "@=you  letter=nearby room  (layout)"
    return "\n".join([header, *rows, legend])


def render_exit_graph_minimap(
    rooms, center_room, radius=TOWN_MINIMAP_RADIUS, use_color=True,
):
    """BFS exit-graph local map for hand rooms without layout coords.

    Walks cardinal/diagonal exits only (skips up/down/in/out and door
    labels). Places each reached room relative to the center; collisions
    keep the first occupant. Returns None only when center_room is None.
    """
    _ = rooms  # rooms dict unused -- we walk live exit pointers
    if center_room is None:
        return None
    # pos -> room; center at (0, 0)
    placed = {(0, 0): center_room}
    # BFS: queue of (room, x, y, depth)
    from collections import deque
    queue = deque([(center_room, 0, 0, 0)])
    seen = {id(center_room)}
    while queue:
        room, x, y, depth = queue.popleft()
        if depth >= radius:
            continue
        exits = getattr(room, "exits", None) or {}
        for direction, dest in exits.items():
            delta = _LAYOUT_XY_DELTA.get(str(direction).lower())
            if delta is None or dest is None:
                continue
            nx, ny = x + delta[0], y + delta[1]
            # Stay inside the visible window.
            if abs(nx) > radius or abs(ny) > radius:
                continue
            dest_id = id(dest)
            if dest_id in seen:
                continue
            seen.add(dest_id)
            if (nx, ny) not in placed:
                placed[(nx, ny)] = dest
            queue.append((dest, nx, ny, depth + 1))

    rows = []
    for dy in range(radius, -radius - 1, -1):
        cells = []
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                cells.append("@")
                continue
            neighbor = placed.get((dx, dy))
            if neighbor is None:
                cells.append(" ")
                continue
            glyph = _town_room_glyph(neighbor)
            if use_color:
                color = _room_display_color(neighbor)
                if color:
                    glyph = f"{color}{glyph}{ANSI_RESET}"
            cells.append(glyph)
        rows.append("".join(cells))
    label = (
        getattr(center_room, "zone", None)
        or getattr(center_room, "map_id", None)
        or "local"
    )
    header = f"{label}  (@ = you)"
    legend = "@=you  letter=linked room  (exits)"
    return "\n".join([header, *rows, legend])


def render_local_map(
    rooms, center_room, radius=None, use_color=True, *, compact=False,
):
    """Dispatcher: overland grid, Studio layout, or exit-graph town map.

    Returns a multi-line string (\\n joined) or None when nothing can
    render (no room). Grid cells keep the full overland radius; town
    windows default to TOWN_MINIMAP_RADIUS.

    ``compact`` (look embed): smaller radius and no header/legend so the
    room sheet stays short; bare ``map`` leaves this False.
    """
    if center_room is None:
        return None
    is_grid = (
        getattr(center_room, "grid_prefix", None) is not None
        or parse_grid_key(getattr(center_room, "key", "") or "") is not None
    )
    if is_grid:
        if radius is None:
            r = LOOK_GRID_MINIMAP_RADIUS if compact else MINIMAP_RADIUS
        else:
            r = radius
        rendered = render_minimap(
            rooms, center_room, radius=r, use_color=use_color,
        )
    else:
        if radius is None:
            town_r = (
                LOOK_TOWN_MINIMAP_RADIUS if compact else TOWN_MINIMAP_RADIUS
            )
        else:
            town_r = radius
        rendered = None
        # Prefer authored Studio layout when both x and y are stamped.
        if (
            getattr(center_room, "layout_x", None) is not None
            and getattr(center_room, "layout_y", None) is not None
        ):
            rendered = render_layout_minimap(
                rooms, center_room, radius=town_r, use_color=use_color,
            )
        if not rendered:
            rendered = render_exit_graph_minimap(
                rooms, center_room, radius=town_r, use_color=use_color,
            )
    if not rendered:
        return None
    if compact:
        # Drop header + legend -- look only needs the glyph window.
        lines = rendered.split("\n")
        if len(lines) >= 3:
            return "\n".join(lines[1:-1])
    return rendered


def render_full_grid(
    rooms,
    center_room,
    *,
    width,
    height,
    use_color=True,
    wrap=False,
    mark_you=True,
):
    """Build an ASCII dump of an entire overland grid (giant map).

    Same glyphs / @-you / north-up convention as ``render_minimap``, but
    every cell from (0,0) to (width-1, height-1) is drawn. Missing cells
    (should not happen on a healthy stamp) render as blank. ``wrap`` is
    only used in the header tip -- the dump always shows the full
    rectangle; torus edges are invisible on paper.

    ``mark_you`` (default True) draws ``@`` at the center room's grid
    coords. Bare travel ``atlas`` passes False when the viewer is not on
    America so the dump does not lie about position.

    Returns a multi-line string (\\n joined) or None if ``center_room`` is
    not a stamped grid cell / bounds are invalid.
    """
    prefix = getattr(center_room, "grid_prefix", None)
    cx = getattr(center_room, "grid_x", None)
    cy = getattr(center_room, "grid_y", None)
    if prefix is None or cx is None or cy is None:
        parsed = parse_grid_key(center_room.key)
        if parsed is None:
            return None
        prefix, cx, cy = parsed
    try:
        width = int(width)
        height = int(height)
    except (TypeError, ValueError):
        return None
    if width < 1 or height < 1:
        return None

    rows = []
    seen_rooms = []
    # North (high y) at the top of the printout -- same as minimap.
    for y in range(height - 1, -1, -1):
        cells = []
        for x in range(width):
            if mark_you and x == cx and y == cy:
                cells.append("@")
                continue
            neighbor = rooms.get(f"{prefix} ({x}, {y})")
            if neighbor is None:
                cells.append(" ")
                continue
            seen_rooms.append(neighbor)
            glyph = _room_display_glyph(neighbor)
            if use_color:
                color = _room_display_color(neighbor)
                if color:
                    glyph = f"{color}{glyph}{ANSI_RESET}"
            cells.append(glyph)
        rows.append("".join(cells))

    legend = _map_legend_for(seen_rooms or [center_room])
    wrap_note = "  (wraps at edges)" if wrap else ""
    if mark_you:
        header = (
            f"{prefix} FULL {width}x{height}  you=({cx}, {cy})  (@ = you)"
            f"{wrap_note}"
        )
    else:
        header = (
            f"{prefix} FULL {width}x{height}  (reference atlas)"
            f"{wrap_note}"
        )
    # Optional south-edge ruler so wide atlases stay oriented (every 10).
    if width >= 10:
        ruler = "".join(str(x % 10) for x in range(width))
        rows.append(ruler)
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


def _map_id_for(filename, data):
    """Stable map id: JSON ``id`` or basename without .json."""
    return data.get("id") or os.path.splitext(filename)[0]


def _autoload_enabled(data):
    """True unless the file explicitly sets ``"autoload": false``.

    Deferred maps stay on disk for ``gm maps load`` but are skipped at boot
    so they do not register rooms, NPCs (via missing home_room), or world
    catalog rows until a GM hot-loads them.
    """
    return data.get("autoload", True) is not False


def iter_map_json_paths():
    """Yield absolute paths for every content/maps + content/zones JSON.

    Duplicate basenames across the two directories raise ValueError (same
    rule as boot load).
    """
    paths = sorted(glob.glob(os.path.join(_MAPS_DIR, "*.json")))
    if os.path.isdir(_ZONES_DIR):
        paths.extend(sorted(glob.glob(os.path.join(_ZONES_DIR, "*.json"))))
    seen = set()
    for path in paths:
        base = os.path.basename(path)
        if base in seen:
            raise ValueError(
                f"duplicate map/zone filename {base!r} under content/maps "
                f"and content/zones -- rename one"
            )
        seen.add(base)
        yield path


def resolve_map_file(name):
    """Find a map/zone JSON by id, basename, or basename.json.

    Returns (abspath, basename, data, kind) where kind is ``map`` or
    ``zone``. Raises FileNotFoundError if nothing matches.
    """
    needle = (name or "").strip()
    if not needle:
        raise FileNotFoundError("empty map name")
    if not needle.endswith(".json"):
        candidates = (needle + ".json", needle)
    else:
        candidates = (needle, needle[:-5])
    needles = {c.lower() for c in candidates}
    needles.add(needle.lower())
    for path in iter_map_json_paths():
        base = os.path.basename(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        map_id = _map_id_for(base, data)
        if (
            base.lower() in needles
            or map_id.lower() in needles
            or os.path.splitext(base)[0].lower() in needles
        ):
            kind = "zone" if os.path.dirname(path).endswith("zones") else "map"
            return path, base, data, kind
    raise FileNotFoundError(f"no map/zone file matching {name!r}")


def catalog_map_files():
    """Return metadata for every on-disk map/zone (loaded or deferred).

    Each row: id, filename, kind, plane, autoload, path, hub_hint
    (runtime_hub or first room key).
    """
    rows = []
    for path in iter_map_json_paths():
        base = os.path.basename(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        map_id = _map_id_for(base, data)
        kind = "zone" if os.path.dirname(path).endswith("zones") else "map"
        rooms = data.get("rooms") or []
        hub = data.get("runtime_hub") or (
            rooms[0].get("key") if rooms else None
        )
        rows.append({
            "id": map_id,
            "filename": base,
            "kind": kind,
            "plane": data.get("plane") or "earth",
            "autoload": _autoload_enabled(data),
            "path": path,
            "hub_hint": hub,
            "room_count": len(rooms),
            "has_grid": bool(data.get("grid")),
        })
    rows.sort(key=lambda r: (r["kind"], r["id"]))
    return rows


def _load_map_files(*, include_deferred=False):
    """Read and parse content/maps/*.json plus content/zones/*.json.

    Zone files use the same map JSON schema (typically pocket-only rooms).
    Returns a list of (filename, data) pairs -- filename is basename so
    error messages stay short. sorted() makes load order deterministic.
    Duplicate basenames across maps/ and zones/ fail loud at boot.

    Files with ``"autoload": false`` are skipped unless include_deferred
    is True (used by catalog / hot-load, not by boot).
    """
    global LAST_DEFERRED_MAPS
    maps = []
    deferred = {}
    for path in iter_map_json_paths():
        base = os.path.basename(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        map_id = _map_id_for(base, data)
        if not _autoload_enabled(data):
            deferred[map_id] = {
                "filename": base,
                "path": path,
                "plane": data.get("plane") or "earth",
                "kind": (
                    "zone" if os.path.dirname(path).endswith("zones")
                    else "map"
                ),
            }
            if not include_deferred:
                continue
        maps.append((base, data))
    LAST_DEFERRED_MAPS = deferred
    return maps


def _room_looks_like_sewer(key, title=None):
    """True when key/title names an underground sewer.

    Used here for engine-generic outdoor/dark defaults (sewers stay indoor
    + dark). SUPERS' evil_zone sewer carve-out lives in
    ``supers.maps_room_json`` (Stage G stamper).
    """
    blob = f"{key or ''} {title or ''}".lower()
    return "sewer" in blob


def _stamp_room_city_meta(room, data):
    """Copy zone/map city_name + color roles onto a live Room.

    See docs/AREA_BUILDING.md. No-op when the file omits city fields
    entirely (overland grids, hollows without a city header yet).
    """
    if room is None or not isinstance(data, dict):
        return
    if not any(
        k in data
        for k in ("city_name", "city_color", "sub_color", "main_colors")
    ):
        return
    from engine.room_naming import parse_city_meta

    meta = parse_city_meta(data)
    room.city_name = meta["city_name"] or None
    room.city_color = meta["city_color"]
    room.sub_color = meta["sub_color"]
    room.main_colors = dict(meta["main_colors"])


def _add_room(rooms, filename, key, description, gravity=1.0,
              wilderness=None, area_type=None, bestiary_categories=None,
              plane=None, realm=None, map_id=None,
              grid_prefix=None, grid_x=None, grid_y=None,
              resources=None, zone=None, resource_capacity=None,
              dungeon=None,
              no_random_spawn=None,
              zone_exit=None,
              no_combat=None,
              outdoor=None, dark=None,
              hidden_directions=None, title=None,
              map_glyph=None, map_layer=None, glyph_set=None,
              vnum=None, layout=None,
              jobs=None,
              game_fields=None):
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

    Optional title is the player-facing look name when it should differ
    from the unique storage key (highway stretches, etc.). Empty/omitted
    keeps look_title() falling back to key.

    Optional vnum is the hand-room mapper id (``CA00001``). Validated and
    checked for global uniqueness across ``rooms`` already loaded. Grid
    cells must omit it. Missing vnum is allowed (retro script / dig assign).

    Optional map_glyph / map_layer / glyph_set control atlas minimap art
    (earth_america US road-atlas style): glyph overrides the area_type
    letter; layer picks the terrain fill + bright highway/city colors;
    glyph_set "atlas" switches the default topography symbols (~ . ^ o)
    and turns on the filled colored-block render.

    Optional layout is Area Studio canvas coords
    (``{"x": int, "y": int, "z": int}``). Stamped onto Room.layout_*
    for the town local minimap; omitted leaves those None (exit-graph
    fallback).

    Optional ``game_fields`` is the raw hand-room / cell-override dict.
    After engine-generic stamps below, ``engine.hooks.stamp_map_room``
    lets the registered game (SUPERS) apply its authored flavor flags
    (Phase 7 Stage G). Lean boots leave the hook unset.
    """
    if key in rooms:
        raise ValueError(
            f"{filename}: room key {key!r} is already used by another "
            "map file or an earlier entry in this one"
        )
    room = Room(key, description)
    room.gravity = gravity
    # Player-facing title (optional). Strip empties so "" never shadows key.
    if title is not None:
        cleaned = str(title).strip()
        room.title = cleaned or None
    # Hand-room vnum (optional). Never on grid cells (grid_prefix set below).
    if vnum is not None and str(vnum).strip():
        if grid_prefix is not None:
            raise ValueError(
                f"{filename}: room key {key!r} is a grid cell -- must not "
                f"have a vnum (got {vnum!r})"
            )
        from engine import room_vnum as room_vnum_mod
        try:
            normalized = room_vnum_mod.validate_vnum(vnum)
        except ValueError as err:
            raise ValueError(
                f"{filename}: room key {key!r}: {err}"
            ) from err
        for other in rooms.values():
            other_v = getattr(other, "vnum", None)
            if other_v and other_v == normalized:
                raise ValueError(
                    f"{filename}: room key {key!r} vnum {normalized!r} "
                    f"already used by {other.key!r}"
                )
        room.vnum = normalized
    # Atlas minimap overrides (optional).
    if map_glyph is not None:
        g = str(map_glyph).strip()
        room.map_glyph = g[0] if g else None
    if map_layer is not None:
        layer = str(map_layer).strip().lower()
        room.map_layer = layer or None
    if glyph_set is not None:
        gs = str(glyph_set).strip().lower()
        room.glyph_set = gs or None
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
    # Pocket mouth: zone_exit=None (omitted) keeps False; explicit true marks
    # a room where players may type `exit` to leave the zone (Southern
    # Highway, South Gate, dungeon Entrance). Pocket linking also stamps
    # this True on hub_room. See Room.zone_exit.
    if zone_exit is not None:
        room.zone_exit = bool(zone_exit)
    # Authored leveling dungeon -- omitted keeps False; explicit true marks
    # players-only PvE (Cadence cannot path in; see Room.dungeon).
    if dungeon is not None:
        room.dungeon = bool(dungeon)
        # Dungeon zones default to no wilderness / procedural random rolls
        # unless the room explicitly sets no_random_spawn.
        if no_random_spawn is None and room.dungeon:
            room.no_random_spawn = True
    # Explicit no_random_spawn wins (True or False) when authored.
    if no_random_spawn is not None:
        room.no_random_spawn = bool(no_random_spawn)
    # Civic peace: no attack/spar/aggro (Central Plaza). Omitted -> False.
    if no_combat is not None:
        room.no_combat = bool(no_combat)
    # Outdoor exposure: explicit JSON wins. When omitted, defaults to
    # wilderness so classic overland grids stay open-sky without tagging
    # every cell. Dual-layer America sets grid outdoor:true with
    # wilderness:false (sky/weather while driving; encounters only on
    # virtual foot micro). Hand-authored indoor rooms omit both and stay
    # False (Room default) unless wilderness=true. Town streets set
    # "outdoor": true explicitly. Sewer-named rooms default indoor.
    if outdoor is None and _room_looks_like_sewer(key, title):
        outdoor = False
    if outdoor is not None:
        room.outdoor = bool(outdoor)
    else:
        room.outdoor = bool(room.wilderness)
    # D67 dark rooms (omitted -> Room default False). Sewers default dark.
    if dark is None and _room_looks_like_sewer(key, title):
        dark = True
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
    # Studio / dig layout canvas (town minimap). Invalid blobs stay None.
    if isinstance(layout, dict) and "x" in layout and "y" in layout:
        try:
            room.layout_x = int(layout["x"])
            room.layout_y = int(layout["y"])
            room.layout_z = int(layout.get("z", 0) or 0)
        except (TypeError, ValueError):
            room.layout_x = None
            room.layout_y = None
            room.layout_z = None
    # SUPERS (or any game) authored flavor flags -- Stage G hook.
    from engine import hooks as _hooks
    _hooks.stamp_map_room(room, game_fields or {}, filename=filename)
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
    # Same shape as wilderness: omitted means "inherit outdoor-from-
    # wilderness in _add_room"; true/false stamps every cell unless a
    # cell_overrides entry punches through (America Overland: outdoor
    # true + wilderness false so the atlas is open sky without roadside
    # wilderness ticks while you drive).
    outdoor = grid.get("outdoor")
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
    # Optional atlas glyph set for this whole grid (earth_america, …).
    grid_glyph_set = grid.get("glyph_set")
    # Cadence confinement for whole grids (Elemental Reaches, …). Cell
    # overrides may punch through; omitted keeps zone=None (Wastes-style).
    grid_zone = grid.get("zone")


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
            # Per-cell cover / flag stamps (Area Studio + hand JSON): when
            # a cell_overrides entry sets wilderness/outdoor/resources/
            # spawn_nest/… those punch through the grid-wide defaults the
            # same way area_type already does. Omitted keys fall back to
            # the grid-wide value (or None) so _add_room keeps its
            # "unspecified vs explicit" defaults.
            cell_wilderness = override.get("wilderness", wilderness)
            # outdoor: cell override wins; else grid outdoor; else None
            # so _add_room still defaults outdoor from wilderness.
            cell_outdoor = override.get("outdoor", outdoor)
            cell_glyph_set = override.get("glyph_set", grid_glyph_set)
            # Zone: cell override wins; else grid-wide Cadence zone.
            if "zone" in override:
                cell_zone = override.get("zone")
            else:
                cell_zone = grid_zone
            _add_room(
                rooms, filename, key, description, gravity,
                cell_wilderness, cell_area_type, cell_bestiary_categories,
                plane=plane, realm=realm, map_id=map_id,
                grid_prefix=prefix, grid_x=x, grid_y=y,
                resources=override.get("resources"),
                zone=cell_zone,
                resource_capacity=override.get("resource_capacity"),
                dungeon=override.get("dungeon"),
                no_random_spawn=override.get("no_random_spawn"),
                zone_exit=override.get("zone_exit"),
                no_combat=override.get("no_combat"),
                outdoor=cell_outdoor,
                jobs=override.get("jobs"),
                dark=override.get("dark"),
                hidden_directions=override.get("hidden_directions"),
                title=override.get("title"),
                map_glyph=override.get("map_glyph"),
                map_layer=override.get("map_layer"),
                glyph_set=cell_glyph_set,
                game_fields=override,
            )


def _link_grid_neighbors(rooms, grid):
    """Wire cardinal + diagonal exits between every cell of one map's grid.

    Convention: north = y+1, south = y-1, east = x+1, west = x-1 -- same
    as landmark bearings. Diagonals (ne/nw/se/sw) are king-move links so
    overland walking matches 8-way landmark hints (suggestion #80).

    By default edges omit outward exits (hard walls). Set ``"wrap": true``
    on the grid JSON for a torus / globe loop: leaving the east edge lands
    on the west edge (and the same for N/S and diagonals). Walk pathfinding
    follows exits, so wrap works for ``walk`` without a separate math path.
    """
    prefix = grid["key_prefix"]
    width = grid["width"]
    height = grid["height"]
    # Explicit True only -- omit / false / null keep hard edges (Wastes).
    wrap = grid.get("wrap") is True

    def _cell(nx, ny):
        """Resolve neighbor coords; wrap with modulo, else None off-edge."""
        if wrap:
            # Python's % on positives is fine; keep nx/ny in range for keys.
            return rooms[f"{prefix} ({nx % width}, {ny % height})"]
        if 0 <= nx < width and 0 <= ny < height:
            return rooms[f"{prefix} ({nx}, {ny})"]
        return None

    for x in range(width):
        for y in range(height):
            room = rooms[f"{prefix} ({x}, {y})"]
            # Cardinals.
            north = _cell(x, y + 1)
            if north is not None:
                room.exits["north"] = north
            south = _cell(x, y - 1)
            if south is not None:
                room.exits["south"] = south
            east = _cell(x + 1, y)
            if east is not None:
                room.exits["east"] = east
            west = _cell(x - 1, y)
            if west is not None:
                room.exits["west"] = west
            # Diagonals -- canonical keys match DIRECTIONS ("ne" -> "northeast").
            ne = _cell(x + 1, y + 1)
            if ne is not None:
                room.exits["northeast"] = ne
            nw = _cell(x - 1, y + 1)
            if nw is not None:
                room.exits["northwest"] = nw
            se = _cell(x + 1, y - 1)
            if se is not None:
                room.exits["southeast"] = se
            sw = _cell(x - 1, y - 1)
            if sw is not None:
                room.exits["southwest"] = sw


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
    Room.zone_entries (gateway cell) and stamp zone_exit + zone_exit_to on
    the hub only (the mouth you enter into -- e.g. Southern Highway). Side
    streets and house interiors stay unflagged so `exit` cannot teleport
    from indoors. Legacy exits['in']/['out'] and matching grid.portals for
    the same pair are stripped so the verbs stay separate.
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
        # Only the pocket hub is a zone_exit mouth: exit from Southern
        # Highway / South Gate / etc., not every house or sewer room.
        for alias in _pocket_enter_aliases(hub, pocket):
            prior = cell.zone_entries.get(alias)
            if prior is not None and prior is not hub:
                raise ValueError(
                    f"{filename}: pockets[{i}] enter alias {alias!r} "
                    f"already points at {prior.key!r}"
                )
            cell.zone_entries[alias] = hub
        hub.zone_exit_to = cell
        hub.zone_exit = True

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


def _stamp_registry_entry(registry, filename, data, plane, realm, map_id):
    """Write one map_registry row (boot or hot-load)."""
    from engine.room_naming import parse_city_meta

    grid = data.get("grid")
    city_meta = parse_city_meta(data)
    registry[map_id] = {
        "realm": realm,
        "plane": plane,
        "grid_prefix": grid["key_prefix"] if grid else None,
        "width": grid["width"] if grid else None,
        "height": grid["height"] if grid else None,
        # Torus / globe neighbor loop (see _link_grid_neighbors).
        "wrap": bool(grid and grid.get("wrap") is True),
        "pocket_hubs": list(
            p.get("hub_room") for p in (data.get("pockets") or [])
            if p.get("hub_room")
        ),
        "filename": filename,
        "autoload": _autoload_enabled(data),
        "runtime_hub": data.get("runtime_hub"),
        # Official city label + ROOM NAME paint roles (docs/AREA_BUILDING.md).
        "city_name": city_meta["city_name"],
        "city_color": city_meta["city_color"],
        "sub_color": city_meta["sub_color"],
        "main_colors": city_meta["main_colors"],
    }


def create_rooms_from_map_data(rooms, filename, data):
    """Pass-1: create grid cells + hand rooms from one map JSON into rooms.

    Returns (map_id, plane, realm, grid_was_built). Raises on key collision
    or unknown plane — same rules as boot.
    """
    plane, realm = _resolve_plane_and_realm(filename, data)
    map_id = _map_id_for(filename, data)
    grid = data.get("grid")
    if grid:
        # Hot-load of a second grid with a colliding prefix must fail loud.
        prefix = grid["key_prefix"]
        for existing in rooms.values():
            if getattr(existing, "grid_prefix", None) == prefix:
                raise ValueError(
                    f"{filename}: grid key_prefix {prefix!r} already used "
                    f"by a loaded room"
                )
        _build_grid(
            rooms, filename, grid,
            plane=plane, realm=realm, map_id=map_id,
        )
    for room_data in data.get("rooms", []):
        ensure_hand_room_identity(
            room_data, where=f"{filename} hand room",
        )
        room_dungeon = (
            room_data["dungeon"] if "dungeon" in room_data
            else data.get("dungeon")
        )
        room_no_random = (
            room_data["no_random_spawn"] if "no_random_spawn" in room_data
            else data.get("no_random_spawn")
        )
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
            dungeon=room_dungeon,
            no_random_spawn=room_no_random,
            zone_exit=room_data.get("zone_exit"),
            no_combat=room_data.get("no_combat"),
            outdoor=room_data.get("outdoor"),
            jobs=room_data.get("jobs"),
            dark=room_data.get("dark"),
            hidden_directions=room_data.get("hidden_directions"),
            title=room_data.get("title"),
            vnum=room_data.get("vnum"),
            layout=room_data.get("layout"),
            game_fields=room_data,
        )
        _stamp_room_city_meta(rooms[room_data["key"]], data)
    return map_id, plane, realm, bool(grid)


def link_map_data(rooms, filename, data):
    """Pass-2: wire exits, pockets, and collect seed_items for one file.

    Returns list of (Item, room_key) seed pairs (caller places them).
    """
    seed_items = []
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
    _link_pockets(rooms, filename, data)
    return seed_items


def wire_pocket_at_cell(
    rooms,
    *,
    host_prefix,
    host_width,
    host_height,
    x,
    y,
    hub_key,
    kind="settlement",
    enter_as=None,
    visible_as=None,
    filename="(runtime)",
):
    """Attach hub_room to an overland cell for enter/exit (runtime or boot).

    Same rules as pockets[] in map JSON. Mutates cell.zone_entries and
    stamps zone_exit + zone_exit_to on the hub only. Returns the cell Room.
    """
    if kind not in POCKET_KINDS:
        raise ValueError(
            f"{filename}: pocket kind {kind!r} -- must be one of "
            f"{sorted(POCKET_KINDS)}"
        )
    x, y = int(x), int(y)
    if not (0 <= x < int(host_width) and 0 <= y < int(host_height)):
        raise ValueError(
            f"{filename}: pocket at [{x}, {y}] outside grid "
            f"{host_width}x{host_height}"
        )
    if hub_key not in rooms:
        raise ValueError(f"{filename}: hub_room {hub_key!r} unknown")
    cell_key = f"{host_prefix} ({x}, {y})"
    if cell_key not in rooms:
        raise ValueError(f"{filename}: overland cell {cell_key!r} missing")
    cell = rooms[cell_key]
    hub = rooms[hub_key]
    pocket = {
        "kind": kind,
        "at": [x, y],
        "hub_room": hub_key,
        "enter_as": list(enter_as or []),
        "visible_as": visible_as or "",
    }
    for alias in _pocket_enter_aliases(hub, pocket):
        prior = cell.zone_entries.get(alias)
        if prior is not None and prior is not hub:
            raise ValueError(
                f"{filename}: enter alias {alias!r} already points at "
                f"{prior.key!r}"
            )
        cell.zone_entries[alias] = hub
    hub.zone_exit_to = cell
    hub.zone_exit = True
    visible = str(visible_as or "").strip()
    if visible:
        _LANDMARKS_BY_PREFIX.setdefault(host_prefix, []).append({
            "x": x,
            "y": y,
            "name": visible,
        })
    return cell


def unwire_pockets_pointing_at(rooms, hub_keys, host_prefix=None):
    """Remove zone_entries / landmarks that target hubs being unloaded."""
    hub_set = set(hub_keys)
    for room in rooms.values():
        entries = getattr(room, "zone_entries", None) or {}
        for alias, dest in list(entries.items()):
            if dest is not None and getattr(dest, "key", None) in hub_set:
                del entries[alias]
    if host_prefix and host_prefix in _LANDMARKS_BY_PREFIX:
        # Landmarks do not store hub keys; leave them — load path can
        # re-add. Strip nothing here to avoid deleting unrelated vistas.
        pass


def rooms_for_map_id(rooms, map_id):
    """Return list of Room objects stamped with this map_id."""
    return [
        r for r in rooms.values()
        if getattr(r, "map_id", None) == map_id
    ]


def load_all_maps(*, include_deferred=False):
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

    ``include_deferred`` (default False): when True, also load files with
    ``"autoload": false`` (e.g. Lebanon). Boot stays False so deferred
    zones wait for ``gm maps load``. Save validators (in-game dig/link,
    Area Studio) pass True so exits that point at hot-loadable rooms are
    checked instead of falsely reporting "unknown room".
    """
    global LAST_MAP_REGISTRY, _LANDMARKS_BY_PREFIX
    map_files = _load_map_files(include_deferred=include_deferred)

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
            # Top-level "dungeon": true stamps every room unless the room
            # sets its own dungeon key (players-only leveling zones).
            ensure_hand_room_identity(
                room_data, where=f"{filename} hand room",
            )
            room_dungeon = (
                room_data["dungeon"] if "dungeon" in room_data
                else data.get("dungeon")
            )
            room_no_random = (
                room_data["no_random_spawn"] if "no_random_spawn" in room_data
                else data.get("no_random_spawn")
            )
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
                dungeon=room_dungeon,
                no_random_spawn=room_no_random,
                zone_exit=room_data.get("zone_exit"),
                no_combat=room_data.get("no_combat"),
                outdoor=room_data.get("outdoor"),
                jobs=room_data.get("jobs"),
                dark=room_data.get("dark"),
                hidden_directions=room_data.get("hidden_directions"),
                title=room_data.get("title"),
                vnum=room_data.get("vnum"),
                layout=room_data.get("layout"),
                game_fields=room_data,
            )
            _stamp_room_city_meta(rooms[room_data["key"]], data)
        _stamp_registry_entry(registry, filename, data, plane, realm, map_id)

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
