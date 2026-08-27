"""Compact overland grid layers (terrain_rows + road_rows).

America atlas JSON stores two glyph-string arrays instead of a
``cell_overrides`` blob per land tile. Named mouths (cities, dungeons,
the bunker) stay in sparse ``cell_overrides`` and win on a per-key merge.

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
# Interstate overlay painted on top of topography.
ROAD_GLYPHS = frozenset("=|/\\+")


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
        out["area_type"] = "highway"
        out["map_glyph"] = road_ch
        out["map_layer"] = (
            "mountain_highway" if under == "mountains" else "highway"
        )
        out["title"] = "Highway"
    key = f"{x},{y}"
    override = (grid.get("cell_overrides") or {}).get(key) or {}
    if isinstance(override, dict):
        for field, value in override.items():
            out[field] = value
    return out
