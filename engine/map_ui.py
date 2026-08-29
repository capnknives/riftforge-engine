"""
engine/map_ui.py -- minimap, city-paint, and grid display helpers (H1b/c).

Root ``maps.py`` re-exports this module for backward compatibility.
Map JSON loading stays in ``engine.world_maps`` (H1a).
"""
import glob
import json
import math
import os
import re
import textwrap

from engine.world import Room
from engine.world_maps import get_zones_dir


# Multi-tile city "paint" for grid pockets (docs/plans/
# zone_layout_retrofit.md Phase 2). Off by default per map -- a pocket's own
# "at" cell, hub wiring, and enter/exit are unaffected either way; this
# only gates whether EXTRA macro cells get city terrain/glyph/desc.
#
# When ON for a map and a pocket has no manual ``span`` override (or span is
# [1,1]), paint is derived from the linked zone file's room ``layout``
# coords relative to ``hub_room``, using ``CITY_PAINT_LAYOUT_UNITS``
# in-town layout blocks per macro cell (default 20; wilderness micro
# stays MICRO_SIZE=10). Manual ``span``: [w,h] still wins -- corner
# +x/+y box from ``at`` for staff fixes.
#
# Per-map gate: ``gm citypaint on|off <map_id>`` (persists in meta).
# ``earth_america`` defaults OFF (atlas too coarse at 78x18). God demesne
# realms are the usual host -- flip paint on per demesne map after sizing.
# Layout-units dial: ``gm citypaint units <n>``. Does not retroactively
# repaint already-loaded rooms -- map reload required.
CITY_PAINT_LAYOUT_UNITS_DEFAULT = 20
CITY_PAINT_LAYOUT_UNITS_MIN = 1
CITY_PAINT_LAYOUT_UNITS_MAX = 200
CITY_PAINT_LAYOUT_UNITS = CITY_PAINT_LAYOUT_UNITS_DEFAULT

# Built-in defaults before any meta override (unknown maps default OFF).
CITY_PAINT_MAP_DEFAULTS = {
    "earth_america": False,
}

# Persisted per-map overrides from meta ``city_paint.maps``.
CITY_PAINT_BY_MAP_ID = {}

# hub_room key (or legacy_key) -> zone JSON doc; rebuilt from
# content/zones/*.json on load (see refresh_zone_hub_index).
LAST_ZONE_DOC_BY_HUB_KEY = {}


def city_paint_enabled_for(map_id):
    """True when multi-tile city sprawl paint is enabled for one map id."""
    mid = str(map_id or "").strip()
    if not mid:
        return False
    if mid in CITY_PAINT_BY_MAP_ID:
        return bool(CITY_PAINT_BY_MAP_ID[mid])
    return bool(CITY_PAINT_MAP_DEFAULTS.get(mid, False))


def set_city_paint_for_map(map_id, enabled):
    """Flip city paint for one map (gm citypaint on|off <map_id>)."""
    mid = str(map_id or "").strip()
    if not mid:
        raise ValueError("map_id required")
    CITY_PAINT_BY_MAP_ID[mid] = bool(enabled)


def set_city_paint_layout_units(value):
    """Set in-town layout blocks per macro paint cell (gm citypaint units)."""
    global CITY_PAINT_LAYOUT_UNITS
    n = int(value)
    if not (CITY_PAINT_LAYOUT_UNITS_MIN <= n <= CITY_PAINT_LAYOUT_UNITS_MAX):
        raise ValueError(
            f"city paint layout_units must be "
            f"{CITY_PAINT_LAYOUT_UNITS_MIN}.."
            f"{CITY_PAINT_LAYOUT_UNITS_MAX}"
        )
    CITY_PAINT_LAYOUT_UNITS = n


def city_paint_meta_snapshot():
    """Persisted city-paint blob for meta save/load."""
    return {
        "maps": dict(CITY_PAINT_BY_MAP_ID),
        "layout_units": int(CITY_PAINT_LAYOUT_UNITS),
    }


def apply_city_paint_meta(blob):
    """Restore per-map paint flags + layout-units dial from meta JSON."""
    if not isinstance(blob, dict):
        return
    maps_blob = blob.get("maps")
    if isinstance(maps_blob, dict):
        CITY_PAINT_BY_MAP_ID.clear()
        for mid, val in maps_blob.items():
            key = str(mid).strip()
            if key:
                CITY_PAINT_BY_MAP_ID[key] = bool(val)
    # Legacy global ``enabled`` is ignored -- per-map only; earth_america
    # stays off unless explicitly toggled (old global on ruined the atlas).
    units = blob.get("layout_units")
    if units is not None:
        set_city_paint_layout_units(units)


def _layout_positive_macro_extent(delta, units):
    """Macro cells east/north of hub for a positive layout delta."""
    return math.floor(delta / units) if delta > 0 else 0


def _layout_negative_macro_extent(delta, units):
    """Macro cells west/south of hub for a negative layout delta."""
    return math.ceil(delta / units) if delta < 0 else 0


def refresh_zone_hub_index():
    """Rebuild hub_room -> zone JSON from content/zones/*.json on disk."""
    global LAST_ZONE_DOC_BY_HUB_KEY
    zones_dir = get_zones_dir()
    index = {}
    if not os.path.isdir(zones_dir):
        LAST_ZONE_DOC_BY_HUB_KEY = index
        return index
    for path in sorted(glob.glob(os.path.join(zones_dir, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[maps] zone hub index skipped {path!r}: {exc}")
            continue
        _index_zone_doc_rooms(index, data)
    LAST_ZONE_DOC_BY_HUB_KEY = index
    return index


def _index_zone_doc_rooms(index, zone_doc):
    """Register every room key/legacy_key in one zone doc into index."""
    for room in zone_doc.get("rooms") or []:
        if not isinstance(room, dict):
            continue
        key = room.get("key")
        if key:
            index[str(key)] = zone_doc
        legacy = room.get("legacy_key")
        if legacy:
            index[str(legacy)] = zone_doc


def register_zone_doc_for_hub(hub_key, zone_doc):
    """Test/smoke helper: point one hub at a zone doc without disk I/O."""
    global LAST_ZONE_DOC_BY_HUB_KEY
    LAST_ZONE_DOC_BY_HUB_KEY = dict(LAST_ZONE_DOC_BY_HUB_KEY)
    _index_zone_doc_rooms(LAST_ZONE_DOC_BY_HUB_KEY, zone_doc)
    if hub_key:
        LAST_ZONE_DOC_BY_HUB_KEY[str(hub_key)] = zone_doc


def zone_doc_for_pocket(pocket, hub_key):
    """Resolve zone JSON for auto paint (optional pocket zone_id, else hub)."""
    zone_id = str(pocket.get("zone_id") or "").strip()
    zones_dir = get_zones_dir()
    if zone_id:
        if not os.path.isdir(zones_dir):
            return None
        path = os.path.join(zones_dir, f"{zone_id}.json")
        if not os.path.isfile(path):
            path = os.path.join(zones_dir, zone_id)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, json.JSONDecodeError):
                return None
    if not LAST_ZONE_DOC_BY_HUB_KEY:
        refresh_zone_hub_index()
    return LAST_ZONE_DOC_BY_HUB_KEY.get(str(hub_key))


def layout_footprint_macro_rect(zone_doc, hub_key, *, layout_units=None):
    """Macro paint rectangle from zone layouts relative to hub_room.

    Returns (mx_lo, mx_hi, my_lo, my_hi) inclusive macro offsets from the
    pocket mouth, or None when the zone/hub/layout data is missing.
    """
    units = (
        int(layout_units)
        if layout_units is not None
        else int(CITY_PAINT_LAYOUT_UNITS)
    )
    if units < 1:
        return None
    hub_lx = hub_ly = None
    coords = []
    hub_key = str(hub_key or "")
    for room in zone_doc.get("rooms") or []:
        if not isinstance(room, dict):
            continue
        layout = room.get("layout")
        if not isinstance(layout, dict):
            continue
        if "x" not in layout or "y" not in layout:
            continue
        try:
            lx, ly = int(layout["x"]), int(layout["y"])
        except (TypeError, ValueError):
            continue
        coords.append((lx, ly))
        key = str(room.get("key") or "")
        legacy = str(room.get("legacy_key") or "")
        if key == hub_key or legacy == hub_key:
            hub_lx, hub_ly = lx, ly
    if hub_lx is None or not coords:
        return None
    dxs = [lx - hub_lx for lx, ly in coords]
    dys = [ly - hub_ly for lx, ly in coords]
    return (
        _layout_negative_macro_extent(min(dxs), units),
        _layout_positive_macro_extent(max(dxs), units),
        _layout_negative_macro_extent(min(dys), units),
        _layout_positive_macro_extent(max(dys), units),
    )


def recommended_rect_span_from_layout(zone_doc, hub_key, *, layout_units=None):
    """Offline advice: [width, height] macro box matching auto paint."""
    rect = layout_footprint_macro_rect(
        zone_doc, hub_key, layout_units=layout_units,
    )
    if rect is None:
        return [1, 1]
    mx_lo, mx_hi, my_lo, my_hi = rect
    return [mx_hi - mx_lo + 1, my_hi - my_lo + 1]


_SPRAWL_DESC_TEMPLATE = (
    "You stand in the sprawling reaches of {name}, its rooftops and haze "
    "visible for miles along the highway."
)


def _pocket_span_cells(x, y, span, width, height):
    """Yield every (cx, cy) in a manual +x/+y span box from pocket ``at``."""
    sx, sy = int(span[0]), int(span[1])
    x_max, y_max = x + sx - 1, y + sy - 1
    if x_max >= width or y_max >= height:
        raise ValueError(
            f"pocket span {sx}x{sy} at [{x}, {y}] exceeds grid "
            f"{width}x{height} (box reaches [{x_max}, {y_max}])"
        )
    for cy in range(y, y_max + 1):
        for cx in range(x, x_max + 1):
            yield (cx, cy)


def _macro_offset_paint_cells(at_x, at_y, mx_lo, mx_hi, my_lo, my_hi):
    """Yield atlas (cx, cy) for hub-anchored bidirectional macro paint."""
    for mdx in range(int(mx_lo), int(mx_hi) + 1):
        for mdy in range(int(my_lo), int(my_hi) + 1):
            if mdx == 0 and mdy == 0:
                continue
            yield at_x + mdx, at_y + mdy


def _paint_city_sprawl_cell(
    rooms, filename, prefix, pocket, at_x, at_y, cx, cy,
    mouth, hub_cells, claimed, mouth_at,
):
    """Paint one macro cell as city sprawl (first-claim-wins)."""
    if (cx, cy) == (at_x, at_y):
        return
    if (cx, cy) in hub_cells:
        print(
            f"[maps] {filename}: pocket paint at {mouth_at!r} skips "
            f"[{cx},{cy}] -- claimed by another pocket's mouth"
        )
        return
    if (cx, cy) in claimed:
        print(
            f"[maps] {filename}: pocket paint at {mouth_at!r} skips "
            f"[{cx},{cy}] -- already painted by pocket at "
            f"{claimed[(cx, cy)]!r}"
        )
        return
    cell = rooms.get(f"{prefix} ({cx}, {cy})")
    if cell is None:
        return
    claimed[(cx, cy)] = mouth_at
    city_name = str(
        pocket.get("visible_as") or pocket.get("hub_room") or "the city"
    ).strip()
    # Preserve topo art for atlas maps -- only the pocket mouth keeps the hub
    # letter (authored map_glyph on ``at``). Lowercase hub letters across the
    # sprawl footprint ruined the FK overland look (zone_layout_retrofit Q5).
    orig_area = getattr(cell, "area_type", None) or "plains"
    cell.area_type = "city"
    cell.wilderness = False
    cell.description = _SPRAWL_DESC_TEMPLATE.format(name=city_name)
    glyph_set = getattr(cell, "glyph_set", None)
    if glyph_set == "atlas":
        cell.map_glyph = _cell_glyph(orig_area, glyph_set="atlas")


def _paint_pocket_span(
    rooms, filename, prefix, pocket, x, y, mouth, hub_cells, claimed,
    width, height, map_id=None,
):
    """Paint city terrain onto one pocket's span cells (Phase 2 / H1c)."""
    if not city_paint_enabled_for(map_id):
        return
    span = pocket.get("span")
    manual = (
        isinstance(span, (list, tuple))
        and len(span) == 2
        and (int(span[0]) > 1 or int(span[1]) > 1)
    )
    hub_key = pocket.get("hub_room")
    mouth_at = (x, y)
    if manual:
        paint_cells = list(_pocket_span_cells(x, y, span, width, height))
    else:
        zone_doc = zone_doc_for_pocket(pocket, hub_key)
        if zone_doc is None:
            return
        rect = layout_footprint_macro_rect(zone_doc, hub_key)
        if rect is None:
            return
        mx_lo, mx_hi, my_lo, my_hi = rect
        if mx_lo == 0 and mx_hi == 0 and my_lo == 0 and my_hi == 0:
            return
        paint_cells = list(
            _macro_offset_paint_cells(x, y, mx_lo, mx_hi, my_lo, my_hi)
        )
    for cx, cy in paint_cells:
        _paint_city_sprawl_cell(
            rooms, filename, prefix, pocket, x, y, cx, cy,
            mouth, hub_cells, claimed, mouth_at,
        )


# Filled by the most recent load_all_maps() call -- Game may copy this onto
AREA_TYPE_GLYPH = {
    "ruins": "R",
    "city": "C",
    "city_street": "s",
    "mountains": "M",
    "ocean": "O",
    "lake": "L",
    "forest": "F",
    "plains": "P",
    "furnace": "H",
    "highway": "=",
    "trail": "-",
    "desert": "D",
    "wetland": "W",
    "void": ".",
}

# Atlas / highway-map glyph set (xycoordmapUSguidelines): topography uses
# shape symbols; cities and routes stamp map_glyph on top. Used when a
# grid sets ``"glyph_set": "atlas"`` (stamped onto every cell).
# Desert/wetland use punctuation (not d/w) so they do not read as city
# hub letters beside L/D/W/V on the America atlas.
ATLAS_AREA_GLYPH = {
    "ruins": ":",
    "city": "*",
    "city_street": "s",
    "mountains": "^",
    "ocean": "~",
    "lake": "o",
    "forest": "T",
    "plains": ".",
    "furnace": "H",
    "highway": "=",
    "trail": "-",
    "desert": ",",
    "wetland": "'",
    "void": " ",
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
    "trail": "\x1b[33m",           # dim tan wagon ruts (not interstate gold)
    "city": "\x1b[97m",            # bright white hub letter
    "route": "\x1b[95m",          # bright magenta -- on-road drive overlay
    "offroad": "\x1b[91m",        # bright red -- off-road segment of overlay
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
    "desert": "43",                 # yellow-tan scrub
    "wetland": "42",                # green bayou (glyph distinguishes)
    "highway": "43",                # yellow-tan asphalt (browser HUD + filled atlas)
    "road": "43",
    "trail": "43",                  # dusty tan ruts
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
    "desert": "30",                 # dark stipple on tan
    "wetland": "36",                # cyan-green reeds on green
}
ATLAS_LAYER_FG = {                  # glyph color for a bright overlay layer
    "highway": "93",                # bright-yellow asphalt on the terrain
    "mountain_highway": "93",       # yellow pass over grey rock
    "trail": "90",                  # dark-grey ruts on tan (not bright I-80)
    "city": "97",                   # bright-white hub letter on red
    # Calculated-drive preview (not a JSON map_layer). Bright magenta /
    # bright red are unused by highway (93), city (97), and trail (90)
    # so the accent stays distinguishable on green / tan / grey / blue fills.
    "route": "95",                 # bright-magenta on-road trail marker
    "offroad": "91",               # bright-red dirt-segment of that trail
}

# Route-preview overlay glyphs. These REPLACE the terrain glyph so the
# trail is visible with ``config color off`` (section 8 a11y -- never
# color alone). ``*`` is already the atlas city token, ``@`` is you,
# ``=``/``-``/``|``/``+`` are highway stamps -- ``#`` / ``x`` are unused
# in AREA_TYPE_GLYPH / ATLAS_AREA_GLYPH and stay 1-col ASCII.
ROUTE_GLYPH_ONROAD = "#"
ROUTE_GLYPH_OFFROAD = "x"

# ANSI 16-color escapes (stdlib only -- no third-party color libs).
# Reset with ANSI_RESET after every colored cell so a color never leaks
# into the next glyph or the legend line.
ANSI_RESET = "\x1b[0m"
AREA_TYPE_COLOR = {
    "ruins": "\x1b[37m",        # white/grey stone
    "city": "\x1b[36m",         # cyan settlement
    "city_street": "\x1b[36m",
    "mountains": "\x1b[90m",    # bright black / dark grey
    "ocean": "\x1b[34m",        # blue
    "lake": "\x1b[94m",         # bright blue
    "forest": "\x1b[32m",       # green
    "plains": "\x1b[92m",       # bright green
    "furnace": "\x1b[91m",      # bright red -- Heart Furnace heat
    "highway": "\x1b[33m",
    "trail": "\x1b[90m",
    "desert": "\x1b[33m",
    "wetland": "\x1b[32m",
    "void": "\x1b[90m",
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
    "coalescence": {
        "ruins": "\x1b[37m",
        "city": "\x1b[37m",
        "mountains": "\x1b[90m",
        "ocean": "\x1b[90m",
        "lake": "\x1b[37m",
        "forest": "\x1b[90m",
        "plains": "\x1b[37m",
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
    "highway": (
        "Asphalt ribbon cuts the continent. A faded mile marker reads "
        "({x}, {y}). Drive the cardinals; cities wait at the hubs."
    ),
    "desert": (
        "Sun-blasted scrub and hardpan. Heat shimmers off the road "
        "shoulder. A marker reads ({x}, {y})."
    ),
    "wetland": (
        "Black water and cypress knees. The air smells of mud and rot. "
        "A marker reads ({x}, {y})."
    ),
    "void": (
        "Nothing mapped here -- off the drivable atlas at ({x}, {y})."
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
    "coalescence": {
        "ruins": (
            "Stone feels remembered rather than built -- belief pinning "
            "shape before geography. A pale marker reads ({x}, {y})."
        ),
        "plains": (
            "Open gravel under sky that does not belong to any map. A "
            "pale marker reads ({x}, {y})."
        ),
    },
}

# Player-facing terrain badge on ``look`` (right-hand ``[ … ]`` tag).
# Earth uses ``area_type`` title case unless overridden here.
PLANE_AREA_LABELS = {
    ("fire", "plains"): "Scorched Plains",
    ("fire", "forest"): "Burned Wood",
    ("fire", "mountains"): "Obsidian Peaks",
    ("fire", "ruins"): "Scorched Ruins",
    ("fire", "lake"): "Sulfur Pool",
    ("fire", "ocean"): "Magma Sea",
    ("fire", "city"): "Scorched Settlement",
    ("fire", "furnace"): "Furnace",
    ("water", "ocean"): "Deep Tide",
    ("water", "lake"): "Brine Shallows",
    ("water", "ruins"): "Tide Ruins",
    ("water", "plains"): "Tidal Flats",
    ("air", "plains"): "Open Sky",
    ("air", "mountains"): "Cloud Peaks",
    ("stone", "mountains"): "Living Rock",
    ("stone", "plains"): "Stone Flats",
    ("heaven", "plains"): "Empyrean Fields",
    ("heaven", "city"): "Sanctified City",
    ("heaven", "ruins"): "Radiant Ruins",
    ("heaven", "forest"): "Silver Wood",
    ("hell", "plains"): "Infernal Waste",
    ("hell", "ruins"): "Infernal Ruins",
    ("hell", "city"): "Iron Streets",
    ("hell", "forest"): "Thorn Wood",
    ("purgatory", "plains"): "Ashen Marches",
    ("purgatory", "ruins"): "Liminal Ruins",
    ("purgatory", "city"): "Grey Streets",
    ("dream", "plains"): "Dreamscape",
    ("dream", "forest"): "Shifting Wood",
    ("dream", "ruins"): "Impossible Ruins",
    ("coalescence", "ruins"): "Belief Glimpse",
    ("coalescence", "plains"): "Witness Gravel",
    ("empty", "void"): "The Empty",
}

# Named pocket zones may override the default title-case badge.
VENUE_ZONE_AREA_LABELS = {
    "black-horizon": "The Black Horizon",
    "the-empty": "The Empty",
}


def area_display_label(plane, area_type, *, zone=None):
    """Terrain badge text for ``format_room`` / screenreader look."""
    zone_key = str(zone or "").strip().lower()
    venue = VENUE_ZONE_AREA_LABELS.get(zone_key)
    if venue:
        return venue
    p = str(plane or "earth").strip().lower() or "earth"
    at = str(area_type or "plains").strip().lower() or "plains"
    if p == "earth":
        return at.replace("_", " ").title()
    labeled = PLANE_AREA_LABELS.get((p, at))
    if labeled:
        return labeled
    return at.replace("_", " ").title()


MINIMAP_RADIUS = 3

# Distant landmark bands on overland look (Chebyshev distance:
# max(|dx|, |dy|)). Tunable in one place; pockets opt in with
# JSON "visible_as". Same-cell (d == 0) is omitted -- the gateway
# description + Enter line already cover standing on the landmark.
#
# Staff (gm_mode) keep the long continental bands for ops / building.
# Players get short immersion bands so Texas woods do not name LA / NY.
LANDMARK_NEARBY_MAX = 8
LANDMARK_DISTANCE_MAX = 20
LANDMARK_HORIZON_MAX = 35
PLAYER_LANDMARK_NEARBY_MAX = 2
PLAYER_LANDMARK_DISTANCE_MAX = 3
PLAYER_LANDMARK_HORIZON_MAX = 4

# ``_LANDMARKS_BY_PREFIX`` is owned by ``engine.world_maps`` (imported above).

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


def _landmark_band_limits(*, gm=False):
    """Return (nearby_max, distance_max, horizon_max) for look vista.

    ``gm=True`` (staff ``gm_mode``) keeps the long continental bands.
    Players use the short immersion caps so a Texas homestead does not
    list every coast city on look.
    """
    if gm:
        return (
            LANDMARK_NEARBY_MAX,
            LANDMARK_DISTANCE_MAX,
            LANDMARK_HORIZON_MAX,
        )
    return (
        PLAYER_LANDMARK_NEARBY_MAX,
        PLAYER_LANDMARK_DISTANCE_MAX,
        PLAYER_LANDMARK_HORIZON_MAX,
    )


def _landmark_band_phrase(distance, *, nearby_max=None, distance_max=None,
                          horizon_max=None):
    """Return the look prefix for a Chebyshev distance, or None if hidden.

    Bands (inclusive): nearby 1..NEARBY_MAX, distance NEARBY_MAX+1..DISTANCE_MAX,
    horizon DISTANCE_MAX+1..HORIZON_MAX. Distance 0 and beyond HORIZON_MAX
    return None (caller omits the line). Defaults are the staff (long) caps
    when limits are omitted -- callers should pass player/GM limits.
    """
    if nearby_max is None:
        nearby_max = LANDMARK_NEARBY_MAX
    if distance_max is None:
        distance_max = LANDMARK_DISTANCE_MAX
    if horizon_max is None:
        horizon_max = LANDMARK_HORIZON_MAX
    if distance <= 0 or distance > horizon_max:
        return None
    if distance <= nearby_max:
        return "Nearby"
    if distance <= distance_max:
        return "In the distance"
    return "On the horizon"


def landmark_vista_lines(room, character=None):
    """Build look extras naming distant landmarks on this overland cell.

    Only stamped grid rooms participate (grid_prefix + grid_x/y). Landmarks
    come from pockets that authored visible_as at load time. Returns an
    empty list indoors, off-grid, or when nothing is in range. Lines are
    sorted nearer-first, then by name, so output stays stable.

    ``character`` selects band caps: staff with ``gm_mode`` see the long
    continental vista; everyone else gets player immersion range.
    """
    prefix = getattr(room, "grid_prefix", None)
    px = getattr(room, "grid_x", None)
    py = getattr(room, "grid_y", None)
    if prefix is None or px is None or py is None:
        return []
    from engine import world_maps as _wm
    landmarks = (_wm._LANDMARKS_BY_PREFIX.get(prefix) or [])
    if not landmarks:
        return []

    gm = bool(character is not None and getattr(character, "gm_mode", False))
    nearby_max, distance_max, horizon_max = _landmark_band_limits(gm=gm)

    scored = []
    for entry in landmarks:
        lx, ly = entry["x"], entry["y"]
        name = entry["name"]
        dx = lx - px
        dy = ly - py
        # Chebyshev: king-move distance on the grid (fits 8-way bearings).
        distance = max(abs(dx), abs(dy))
        phrase = _landmark_band_phrase(
            distance,
            nearby_max=nearby_max,
            distance_max=distance_max,
            horizon_max=horizon_max,
        )
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
    # Homestead claim on this America pad (live stamp, may lack map_glyph).
    if getattr(room, "homestead_owner", None):
        return "H"
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


def _normalize_macro_coords(cells):
    """Turn an iterable of (x, y) into a list of [int, int] pairs.

    Accepts tuples or lists (JSON / GMCP callers often send lists). A
    malformed row is skipped so one bad point cannot crash the map
    render. Empty / None input yields an empty list.
    """
    if not cells:
        return []
    out = []
    for pair in cells:
        try:
            # pair[0]/pair[1] works for tuple, list, and other sequences.
            x, y = pair[0], pair[1]
            out.append([int(x), int(y)])
        except (TypeError, ValueError, IndexError):
            continue
    return out


def _route_display_color(room, *, offroad=False):
    """ANSI prefix for one calculated-route overlay cell.

    Atlas maps keep the terrain background fill (so the trail still sits
    on green plains / yellow asphalt / blue water) and swap only the
    glyph foreground to ATLAS_LAYER_FG ``route`` / ``offroad``. Non-atlas
    maps use the matching MAP_LAYER_COLOR foreground-only escape. The
    glyph (``#`` / ``x``) is the primary signal; color is the accent
    (section 8 a11y).
    """
    role = "offroad" if offroad else "route"
    if room is not None and getattr(room, "glyph_set", None) == "atlas":
        area = getattr(room, "area_type", "plains") or "plains"
        bg = ATLAS_BG.get(area, "40")
        fg = ATLAS_LAYER_FG.get(role, "95")
        return f"\x1b[{fg};{bg}m"
    return MAP_LAYER_COLOR.get(role, "")


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
            "==hwy T=woods ,=desert H=home"
        )
    return " ".join(
        f"{AREA_TYPE_GLYPH[t]}={t}" for t in sorted(AREA_TYPE_GLYPH)
    )


def _fit_legend(legend, width):
    """Wrap a legend so 80-col telnet never splits a glyph mid-line."""
    width = max(12, int(width or 80))
    if len(legend) <= width:
        return legend
    return "\n".join(
        textwrap.wrap(legend, width=width, break_long_words=False)
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

# Hand rooms in towns never draw the small ASCII window (map / maplook /
# mapmove). Overland grids and dungeons still use layout / exit-graph /
# terrain windows; ``map big`` / ``atlas`` are separate (macro atlas).
_LOCAL_MAP_SUPPRESSED_AREA_TYPES = frozenset({"city", "city_street"})


def local_map_suppressed(room):
    """True when the local minimap must not render for *room*.

    Town interiors (city / city_street hand rooms) stay text-only on look.
    Grid cells and non-town pockets are unaffected.
    """
    if room is None:
        return True
    if getattr(room, "grid_prefix", None) is not None:
        return False
    area = (getattr(room, "area_type", None) or "").strip().lower()
    return area in _LOCAL_MAP_SUPPRESSED_AREA_TYPES


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


def find_room_by_layout_direction(rooms, room, direction):
    """Return the room one layout cell over from ``room`` in ``direction``,
    or ``None`` when nothing is stamped there.

    Combat-callable generalization of the ``_LAYOUT_XY_DELTA`` /
    ``_rooms_by_layout`` machinery ASCII minimaps already use
    (wall_floor_breach_mechanic.md Phase C) -- reuses the exact same
    layout geometry Studio/the retrofit crawler already stamp, rather
    than inventing a second coordinate system for breach targeting.
    ``direction`` accepts the 8 compass values or ``up``/``down`` (z
    axis, same convention as ``dig_room`` / retrofit's LAYER_UP/DOWN).

    Never raises: a room with no layout, or nothing stamped at the
    destination cell, is a normal "no neighbor here" outcome -- callers
    (e.g. a wall breach) are expected to degrade gracefully, not treat
    this as a content defect.
    """
    if room is None or rooms is None:
        return None
    lx = getattr(room, "layout_x", None)
    ly = getattr(room, "layout_y", None)
    if lx is None or ly is None:
        return None
    lz = int(getattr(room, "layout_z", None) or 0)
    d = str(direction or "").strip().lower()
    if d in _LAYOUT_XY_DELTA:
        dx, dy = _LAYOUT_XY_DELTA[d]
        target_xy, target_z = (int(lx) + dx, int(ly) + dy), lz
    elif d == "up":
        target_xy, target_z = (int(lx), int(ly)), lz + 1
    elif d == "down":
        target_xy, target_z = (int(lx), int(ly)), lz - 1
    else:
        return None
    index = _rooms_by_layout(rooms, getattr(room, "map_id", None), target_z)
    return index.get(target_xy)


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
    *, edge_ok=None,
):
    """BFS exit-graph local map for hand rooms without layout coords.

    Walks cardinal/diagonal exits only (skips up/down/in/out and door
    labels). Places each reached room relative to the center; collisions
    keep the first occupant. Returns None only when center_room is None.

    Optional ``edge_ok(from_room, direction, dest)`` mirrors look/move
    visibility (hidden exits, closed Devil's Gates, …).
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
            if edge_ok is not None and not edge_ok(room, direction, dest):
                continue
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
    character=None, game=None,
):
    """Dispatcher: overland grid, Studio layout, or exit-graph town map.

    Returns a multi-line string (\\n joined) or None when nothing can
    render (no room). Grid cells keep the full overland radius; town
    windows default to TOWN_MINIMAP_RADIUS.

    ``compact`` (look embed): smaller radius and no header/legend so the
    room sheet stays short; bare ``map`` leaves this False.

    When ``character`` is set, exit-graph maps honor the same hidden-exit
    and closed-gate filters as ``look`` / ``cmd_move``.
    """
    if center_room is None or local_map_suppressed(center_room):
        return None
    edge_ok = None
    if character is not None:
        from engine import hooks
        from engine import vision as vision_mod

        def edge_ok(from_room, direction, dest):
            if not vision_mod.character_knows_exit(
                character, from_room, direction,
            ):
                return False
            return hooks.look_exit_visible(dest, game)
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
                edge_ok=edge_ok,
            )
    if not rendered:
        return None
    if compact:
        # Drop header + legend -- look only needs the glyph window.
        lines = rendered.split("\n")
        if len(lines) >= 3:
            rendered = "\n".join(lines[1:-1])
    return append_mapzone_overlay(
        rendered, center_room, game, character=character,
    )


def zone_overlay_footer_lines(center_room, game, character=None):
    """Plain nearby-zone bearings for ASCII map footer (suggestion 179)."""
    if character is None:
        return []
    from engine import display_prefs
    from engine import hooks
    from engine.systems import overland as overland_mod

    display_prefs.ensure_display_defaults(character)
    if not getattr(character, "mapzone_overlay", False):
        return []
    room = center_room
    lines = overland_mod.look_nearby_zone_lines(room, game)
    if lines:
        return lines
    resolved = hooks.map_center_room(character, game)
    if resolved is not None and resolved is not room:
        return overland_mod.look_nearby_zone_lines(resolved, game)
    return []


def append_mapzone_overlay(rendered, center_room, game, *, character=None):
    """Append optional zone-bearing footer to a rendered map string."""
    if not rendered:
        return rendered
    extra = zone_overlay_footer_lines(center_room, game, character)
    if not extra:
        return rendered
    return rendered + "\n" + "\n".join(extra)


def render_full_grid(
    rooms,
    center_room,
    *,
    width,
    height,
    use_color=True,
    wrap=False,
    mark_you=True,
    character=None,
    game=None,
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
    rendered = "\n".join([header, *rows, legend])
    return append_mapzone_overlay(
        rendered, center_room, game, character=character,
    )


# Standard telnet when NAWS is silent. Maps must not use the 67-col
# sheet budget -- that clips a 78-wide America silhouette.
TELNET_COLS = 80
TELNET_ROWS = 24
# Header + legend + one travel hint + prompt, so the glyph window plus
# chrome still fits a 24-row screen.
VIEWPORT_CHROME_ROWS = 6


def viewport_dims(character, atlas_w, atlas_h):
    """Columns x rows for the telnet atlas *camera* (not the full country).

    NAWS may **shrink** the window for narrow/short clients so map lines
    never wrap. Wide Mudlet/browser terminals must not expand the telnet
    dump to the full 96-wide grid -- those clients paint the whole atlas
    out of band via ``RiftForge.Map.Atlas`` / the browser HUD.

    Defaults when NAWS is silent: 80 cols, 24 rows minus chrome (not the
    67-col sheet budget). Caps at those same telnet defaults even when
    NAWS reports a larger window. Never larger than the atlas itself.
    """
    max_view_w = TELNET_COLS
    max_view_h = TELNET_ROWS - VIEWPORT_CHROME_ROWS
    session = getattr(character, "session", None) if character else None
    cols = None
    if session is not None:
        tw = getattr(session, "term_width", None)
        if tw:
            try:
                cols = int(tw)
            except (TypeError, ValueError):
                cols = None
    if not cols:
        cols = max_view_w
    cols = max(7, min(int(cols), max_view_w, int(atlas_w)))
    rows = None
    if session is not None:
        th = getattr(session, "term_height", None)
        if th:
            try:
                rows = int(th) - VIEWPORT_CHROME_ROWS
            except (TypeError, ValueError):
                rows = None
    if not rows:
        rows = max_view_h
    rows = max(8, min(int(rows), max_view_h, int(atlas_h)))
    return cols, rows


def viewport_origin(cx, cy, atlas_w, atlas_h, view_w, view_h):
    """Southwest-inclusive origin so (cx, cy) sits near the window center."""
    atlas_w = int(atlas_w)
    atlas_h = int(atlas_h)
    view_w = max(1, min(int(view_w), atlas_w))
    view_h = max(1, min(int(view_h), atlas_h))
    x0 = int(cx) - view_w // 2
    y0 = int(cy) - view_h // 2
    x0 = max(0, min(x0, atlas_w - view_w))
    y0 = max(0, min(y0, atlas_h - view_h))
    return x0, y0, view_w, view_h


def render_viewport_grid(
    rooms,
    center_room,
    *,
    width,
    height,
    view_w,
    view_h,
    use_color=True,
    mark_you=True,
    character=None,
    game=None,
    route_cells=None,
    offroad_cells=None,
):
    """ASCII camera window -- never wider/taller than view_w x view_h cells.

    Each terrain row is exactly ``view_w`` glyphs so an 80-col client cannot
    wrap the silhouette. Off-atlas cells are spaces (not ocean, not wrap).
    Returns (text, payload) where payload is the GMCP-friendly dict, or
    (None, None) if the center is not a stamped grid cell.

    ``route_cells`` / ``offroad_cells`` are optional iterables of atlas
    (world) ``(x, y)`` pairs -- not viewport-relative. When ``route_cells``
    is passed, those cells swap their terrain glyph for a trail marker
    (``#`` on-road, ``x`` off-road) so the path is visible even with color
    off. ``offroad_cells`` is the dirt subset; everything else in
    ``route_cells`` is treated as highway. ``@`` (you) still wins if the
    character stands on a route cell. The payload grows a ``route`` key
    only when ``route_cells`` is not None (omit, don't send ``[]``, for
    ordinary map renders).
    """
    prefix = getattr(center_room, "grid_prefix", None)
    cx = getattr(center_room, "grid_x", None)
    cy = getattr(center_room, "grid_y", None)
    if prefix is None or cx is None or cy is None:
        parsed = parse_grid_key(center_room.key)
        if parsed is None:
            return None, None
        prefix, cx, cy = parsed
    try:
        width = int(width)
        height = int(height)
        view_w = int(view_w)
        view_h = int(view_h)
        cx = int(cx)
        cy = int(cy)
    except (TypeError, ValueError):
        return None, None
    if width < 1 or height < 1:
        return None, None
    x0, y0, view_w, view_h = viewport_origin(
        cx, cy, width, height, view_w, view_h,
    )

    # Membership sets are viewport-loop (x, y) lookups in atlas coords --
    # the same integers the nested range() already walks. Payload lists
    # keep caller order so a HUD can stroke the polyline.
    route_path = None
    route_offroad = []
    route_set = set()
    offroad_set = set()
    if route_cells is not None:
        route_path = _normalize_macro_coords(route_cells)
        route_offroad = _normalize_macro_coords(offroad_cells)
        route_set = {(p[0], p[1]) for p in route_path}
        offroad_set = {(p[0], p[1]) for p in route_offroad}

    glyph_rows = []
    color_rows = []
    seen_rooms = []
    # North (high y) first -- same as the full dump.
    for y in range(y0 + view_h - 1, y0 - 1, -1):
        raw_cells = []
        paint_cells = []
        for x in range(x0, x0 + view_w):
            if mark_you and x == cx and y == cy:
                raw_cells.append("@")
                paint_cells.append("@")
                continue
            if not (0 <= x < width and 0 <= y < height):
                raw_cells.append(" ")
                paint_cells.append(" ")
                continue
            neighbor = rooms.get(f"{prefix} ({x}, {y})")
            on_route = (x, y) in route_set
            is_offroad = on_route and (x, y) in offroad_set
            if neighbor is None and not on_route:
                raw_cells.append(" ")
                paint_cells.append(" ")
                continue
            if neighbor is not None:
                seen_rooms.append(neighbor)
            if on_route:
                glyph = (
                    ROUTE_GLYPH_OFFROAD if is_offroad else ROUTE_GLYPH_ONROAD
                )
            else:
                glyph = _room_display_glyph(neighbor)
            raw_cells.append(glyph)
            if use_color:
                if on_route:
                    color = _route_display_color(
                        neighbor, offroad=is_offroad,
                    )
                elif neighbor is not None:
                    color = _room_display_color(neighbor)
                else:
                    color = ""
                if color:
                    glyph = f"{color}{glyph}{ANSI_RESET}"
            paint_cells.append(glyph)
        glyph_rows.append("".join(raw_cells))
        color_rows.append("".join(paint_cells))

    header = (
        f"{prefix} you=({cx},{cy}) "
        f"{view_w}x{view_h}/{width}x{height} @=you"
    )
    legend = _fit_legend(_map_legend_for(seen_rooms or [center_room]), view_w)
    if route_path:
        # Glyph key for the overlay -- sighted color-off and SR clients
        # both need the characters named, not just the ANSI accent.
        route_key = (
            f"{ROUTE_GLYPH_ONROAD}=route "
            f"{ROUTE_GLYPH_OFFROAD}=off-road"
        )
        legend = _fit_legend(f"{legend} {route_key}", view_w)
    text = "\n".join([header, *color_rows, legend])
    text = append_mapzone_overlay(
        text, center_room, game, character=character,
    )
    payload = {
        "atlas": prefix,
        "full_width": width,
        "full_height": height,
        "you": [cx, cy],
        "origin": [x0, y0],
        "view_width": view_w,
        "view_height": view_h,
        "rows": glyph_rows,
    }
    if route_path is not None:
        payload["route"] = {
            "path": route_path,
            "offroad": route_offroad,
        }
    return text, payload





