"""Compact overland grid layers (terrain_rows + road_rows).

America atlas JSON stores two glyph-string arrays instead of a
``cell_overrides`` blob per land tile. Named mouths (cities, dungeons,
the bunker) stay in sparse ``cell_overrides`` and win on a per-key merge.

Optional ``highland_rows`` marks mining country with ``H`` (same size as
terrain_rows). Visual biome stays in terrain_rows -- only ridge cells
are ``^`` mountains. Both CONUS atlases use this
(``earth_america.json`` and secret ``earth_frontierland_1861.json``).

y=0 is the first row (south); x=0 is the first character (west). That
matches grid_x / grid_y on generated Rooms.

Engine-safe: no supers imports.
"""

from __future__ import annotations

# One character per terrain cell. Space is the Albers margin (not ocean).
TERRAIN_GLYPH_TO_AREA = {
    " ": "void",
    "~": "ocean",
    "o": "lake",
    ".": "plains",
    "^": "mountains",
    "T": "forest",
    "'": "wetland",
    ",": "desert",
}
AREA_TO_TERRAIN_GLYPH = {
    area: glyph for glyph, area in TERRAIN_GLYPH_TO_AREA.items()
}
# Tiny alphabet for the full-country GMCP / web / Mudlet silhouette.
# HUD Canada land is forced to space in country_atlas_glyph -- not here.
COUNTRY_ATLAS_AREA_GLYPH = {
    "ocean": "~",
    "lake": "o",
    "plains": ".",
    "forest": "T",
    "mountains": "^",
    "hills": "n",
    "desert": ",",
    "swamp": ",",
    "wetland": "'",
    "city": "*",
    "highway": "=",
    "mountain_highway": "=",
    "trail": "-",
    "road": "=",
    "void": " ",
}
# Interstate overlay painted on top of topography.
# Live paint is a single "=" tile. Older maps still store | / \ + from
# the cardinal-only line-art era -- loaders accept them and normalize
# the visible glyph to "=" in expand_grid_cell.
ROAD_GLYPHS = frozenset("=|/\\+")
# HUD-only void-margin tint on CONUS atlases (cosmetic; area_type stays void).
WATER_VIEW_GLYPHS = frozenset("~o")
# HUD-only Canada land on America void margins (cosmetic; area_type void).
LAND_VIEW_GLYPHS = frozenset(".T^,")
# Inland river overlay on land (Mississippi, …) — does not change area_type.
RIVER_GLYPHS = frozenset("~n")
# Max depth band index on playable water maps (0/1/2).
DEPTH_BAND_GLYPHS = frozenset("012")
# Layers that sit ON land instead of replacing the terrain fill.
ROAD_OVERLAY_LAYERS = frozenset({"highway", "mountain_highway", "trail"})
# Mining-country mask. Space = not highland. H = Rockies / Sierra /
# Appalachian belt (peaks AND forested/high-plain fill).
HIGHLAND_GLYPHS = frozenset("H")


def layer_char(rows, x, y):
    """Return the glyph at (x, y) in a row-list, or None if missing."""
    if not rows or y < 0 or y >= len(rows):
        return None
    row = rows[y]
    if not isinstance(row, str) or x < 0 or x >= len(row):
        return None
    return row[x]


def validate_layer_rows(rows, width, height, *, name, where, allowed=None):
    """Fail loud when a compact layer is the wrong size or has bad glyphs."""
    if rows is None:
        return
    if not isinstance(rows, list):
        raise ValueError(f"{where}: grid.{name} must be a list of strings")
    if len(rows) != int(height):
        raise ValueError(
            f"{where}: grid.{name} has {len(rows)} rows; "
            f"grid.height is {height}"
        )
    for y, row in enumerate(rows):
        if not isinstance(row, str):
            raise ValueError(
                f"{where}: grid.{name}[{y}] must be a string"
            )
        if len(row) != int(width):
            raise ValueError(
                f"{where}: grid.{name}[{y}] length {len(row)} != "
                f"grid.width {width}"
            )
        if allowed is None:
            continue
        bad = sorted({ch for ch in row if ch not in allowed})
        if bad:
            raise ValueError(
                f"{where}: grid.{name}[{y}] has unknown glyph(s) "
                f"{bad!r} -- allowed {sorted(allowed)}"
            )


def expand_grid_cell(grid, x, y):
    """Merge terrain_rows + road_rows + cell_overrides for one cell.

    Returns a dict in the same shape as a ``cell_overrides`` entry
    (possibly empty). Sparse overrides win key-by-key so a city letter
    replaces the highway glyph on that mouth, while a dungeon title can
    keep the interstate piece underneath.

    Maps without layers (Wastes, Reaches) fall through to overrides only.
    """
    grid = grid or {}
    out = {}
    terrain_ch = layer_char(grid.get("terrain_rows"), x, y)
    if terrain_ch is not None:
        area = TERRAIN_GLYPH_TO_AREA.get(terrain_ch)
        if area is not None:
            out["area_type"] = area
    road_ch = layer_char(grid.get("road_rows"), x, y)
    if road_ch and road_ch in ROAD_GLYPHS:
        under = out.get("area_type") or grid.get("area_type")
        if under and under not in ("highway", "trail", "road"):
            out["terrain_base"] = under
        if str(grid.get("route_kind") or "").strip().lower() == "trail":
            out["area_type"] = "trail"
            out["map_glyph"] = "-"
            out["map_layer"] = "trail"
            out["title"] = "Wagon trail"
        else:
            out["area_type"] = "highway"
            # One tile -- not directional line-art. Cars step 8-way on this.
            out["map_glyph"] = "="
            out["map_layer"] = (
                "mountain_highway" if under == "mountains" else "highway"
            )
            out["title"] = "Highway"
    highland_ch = layer_char(grid.get("highland_rows"), x, y)
    if highland_ch and highland_ch in HIGHLAND_GLYPHS:
        # Mining-country flag only. Do not stamp a hills letter -- fill
        # already shows as forest T / plains . / desert , like the rest
        # of the atlas. Peaks stay grey ^.
        out["highland"] = True
    # HUD water on America void margin — paint only; sim stays void.
    view_ch = layer_char(grid.get("water_view_rows"), x, y)
    sim_area = out.get("area_type") or grid.get("area_type")
    if (
        view_ch
        and view_ch in WATER_VIEW_GLYPHS
        and str(sim_area or "").strip().lower() == "void"
    ):
        out["water_view"] = True
        out["map_glyph"] = view_ch
        out["map_layer"] = "water_view"
    # HUD Canada land on America void — paint only; sim stays void.
    land_ch = layer_char(grid.get("land_view_rows"), x, y)
    if (
        land_ch
        and land_ch in LAND_VIEW_GLYPHS
        and str(sim_area or "").strip().lower() == "void"
        and not out.get("water_view")
    ):
        out["land_view"] = True
        out["map_glyph"] = land_ch
        out["map_layer"] = "land_view"
    # Inland river overlay on land — never replaces terrain area_type.
    # After highland so a Mississippi cell can still set map_layer=river.
    river_ch = layer_char(grid.get("river_rows"), x, y)
    if river_ch and river_ch in RIVER_GLYPHS:
        base = str(out.get("area_type") or "").strip().lower()
        if base not in ("void", "ocean", "lake", "highway", "trail"):
            out["map_glyph"] = river_ch
            out["map_layer"] = "river"
    depth_ch = layer_char(grid.get("depth_rows"), x, y)
    if depth_ch and depth_ch in DEPTH_BAND_GLYPHS:
        out["max_water_band"] = int(depth_ch)
    key = f"{x},{y}"
    override = (grid.get("cell_overrides") or {}).get(key) or {}
    if isinstance(override, dict):
        for field, value in override.items():
            out[field] = value
    # City letters win. Leftover | / \ + from old JSON collapse to "=".
    layer = str(out.get("map_layer") or "").strip().lower()
    glyph = str(out.get("map_glyph") or "")
    if layer in ("highway", "mountain_highway") and glyph in ROAD_GLYPHS:
        out["map_glyph"] = "="
    return out


def is_hud_foreign_land(cell_or_room):
    """True for HUD-only Canada land paint on America (or 1861) void.

    Those cells stay in the JSON and on the local minimap so a closer
    window can show across a northern border. Country silhouettes
    (web HUD, Mudlet Atlas packet, in-game ``atlas`` / ``map big``)
    omit them so the lower forty-eight stays familiar. Real
    ``earth_canada`` terrain is ordinary land, not this flag -- you
    see that atlas when you are actually on that map.
    """
    if cell_or_room is None:
        return False
    if isinstance(cell_or_room, dict):
        if cell_or_room.get("land_view"):
            return True
        layer = str(cell_or_room.get("map_layer") or "").strip().lower()
        return layer == "land_view"
    if getattr(cell_or_room, "land_view", False):
        return True
    layer = str(getattr(cell_or_room, "map_layer", "") or "").strip().lower()
    return layer == "land_view"


def country_atlas_glyph(cell):
    """One glyph for the full-country silhouette (web / Mudlet / GMCP).

    HUD Canada land paints as off-map space. Authored city letters,
    highways, and HUD water (Gulf, lakes, ocean) still show. Local
    minimaps use the raw ``map_glyph`` instead of this helper.
    """
    cell = cell or {}
    if is_hud_foreign_land(cell):
        return " "
    authored = str(cell.get("map_glyph") or "").strip()
    if authored:
        return authored[0]
    area = str(cell.get("area_type") or "").strip().lower()
    return COUNTRY_ATLAS_AREA_GLYPH.get(area, ".")
