"""
map_hud.py -- player-safe overland DTO for the browser atlas widget.

Built from OverlandAtlas + map_ui glyph tables. Never includes room keys,
VNUMs, hub_room, or plot: ids (player-facing-no-vnums).

Mudlet does not receive this unless the client listed RiftForge.Map in
Core.Supports.Set -- the web page does; typical telnet clients do not.
"""

from __future__ import annotations

from engine import hooks
from engine import map_ui

# Player-facing atlas titles (never show raw map_id like earth_america).
_MAP_TITLES = {
    "earth_america": "America",
    "earth_gulf": "Gulf",
    "earth_great_lakes": "Great Lakes",
    "earth_frontierland_1861": "Frontierland",
}

# SGR 16-color codes used by ATLAS_BG / ATLAS_TOPO_FG / ATLAS_LAYER_FG
# mapped to the same gothic hex the browser ANSI colorizer uses.
_SGR_HEX = {
    30: "#505050",
    31: "#c04040",
    32: "#60a060",
    33: "#c9a227",
    34: "#6080c0",
    35: "#a060a0",
    36: "#7ec8a8",
    37: "#e8e8e8",
    40: "#0a0a0a",
    41: "#5a1a28",
    42: "#1e3a1e",
    43: "#5a4a18",
    44: "#1a2848",
    45: "#3a1a3a",
    46: "#1a3a3a",
    47: "#4a4a4a",
    90: "#5a5a5a",
    91: "#e07070",
    92: "#80c080",
    93: "#e8d080",
    94: "#80a0e0",
    95: "#d080d0",
    96: "#90e0d0",
    97: "#ffffff",
    100: "#404040",
    101: "#8b2942",
    102: "#3d6b3d",
    103: "#8b7a2a",
    104: "#3d4a6b",
    105: "#6b3d6b",
    106: "#2a6b6b",
    107: "#b8b8b8",
}


def _sgr_hex(code) -> str:
    """Turn an ANSI SGR color number (int or numeric str) into CSS hex."""
    try:
        n = int(code)
    except (TypeError, ValueError):
        return "#1a1a1a"
    return _SGR_HEX.get(n, "#1a1a1a")


def _cell_style(area_type, map_glyph, map_layer, *, glyph_set="atlas",
                terrain_base=None, hide_hud_land=True):
    """Return (glyph, fg_hex, bg_hex) for one atlas cell.

    Country grids hide HUD Canada land (``land_view``) so CONUS stays
    CONUS. Pass ``hide_hud_land=False`` only for a local closer window.
    """
    if hide_hud_land and str(map_layer or "").strip().lower() == "land_view":
        area_type = "void"
        map_glyph = " "
        map_layer = None
        terrain_base = None
    area = area_type or "plains"
    glyph = None
    if map_glyph:
        text = str(map_glyph).strip()
        if text:
            glyph = text[0]
    if not glyph:
        glyph = map_ui._cell_glyph(area, glyph_set=glyph_set)
    if glyph_set == "atlas":
        fg_code, bg_code = map_ui.atlas_overlay_sgr(
            area, map_layer, terrain_base, glyph,
        )
        bg = _sgr_hex(bg_code)
        fg = _sgr_hex(fg_code)
    else:
        bg = "#0a0a0a"
        fg = "#e8e8e8"
    return glyph, fg, bg


def _you_cell(character, game, atlas):
    """Return [x, y] on this atlas or None when the viewer is not on it."""
    if character is None or atlas is None:
        return None
    room = hooks.map_center_room(character, game)
    if room is None:
        room = getattr(character, "location", None)
    if room is not None:
        mid = getattr(room, "map_id", None)
        gx = getattr(room, "grid_x", None)
        gy = getattr(room, "grid_y", None)
        if mid == atlas.map_id and gx is not None and gy is not None:
            try:
                return [int(gx), int(gy)]
            except (TypeError, ValueError):
                pass
    macro = getattr(character, "macro_pos", None)
    if isinstance(macro, (list, tuple)) and len(macro) == 2:
        try:
            mx, my = int(macro[0]), int(macro[1])
        except (TypeError, ValueError):
            return None
        if 0 <= mx < atlas.width and 0 <= my < atlas.height:
            return [mx, my]
    return None


def _safe_landmark(raw):
    """Player-facing landmark dict -- no hub_room / keys."""
    if not isinstance(raw, dict):
        return None
    aliases = [
        str(a).strip().lower()
        for a in (raw.get("enter_as") or [])
        if str(a).strip()
    ]
    visible = str(raw.get("visible_as") or "").strip()
    kind = str(raw.get("kind") or "landmark").strip() or "landmark"
    at = raw.get("at")
    if not (isinstance(at, (list, tuple)) and len(at) == 2):
        return None
    try:
        mx, my = int(at[0]), int(at[1])
    except (TypeError, ValueError):
        return None
    return {
        "x": mx,
        "y": my,
        "label": visible,
        "enter_as": aliases,
        "kind": kind,
    }


def _assert_player_safe(blob):
    """Raise if a DTO accidentally includes internal ids (tests + boot)."""
    dumped = ""
    try:
        import json

        dumped = json.dumps(blob, default=str)
    except (TypeError, ValueError):
        dumped = str(blob)
    lower = dumped.lower()
    for needle in ("hub_room", "plot:", "gmspirit:", "homestead:"):
        if needle in lower:
            raise ValueError(f"map HUD DTO leaked {needle}")
    # VNUM-shaped tokens (LE00009) are mapper-only -- never in this package.
    import re

    if re.search(r"\b[A-Z]{2}\d{5}\b", dumped):
        raise ValueError("map HUD DTO leaked a VNUM-shaped token")


def build_map_payload(character, game, *, include_grid=True):
    """Build RiftForge.Map JSON, or None when there is no overland atlas.

    ``include_grid`` is False for the live browser HUD: the 96x60 glyph
    grid plus palette is too large for a 64 KB WebSocket frame. Terrain
    lives on ``RiftForge.Map.Atlas`` (compact rows). This payload is
    you-are-here plus landmarks.
    """
    atlas = None
    if game is not None:
        from engine.systems import overland as overland_mod

        atlas = overland_mod.atlas_for_character(character, game)
        if atlas is None:
            atlas = getattr(game, "overland_atlas", None)
    if atlas is None:
        return None
    width = int(getattr(atlas, "width", 0) or 0)
    height = int(getattr(atlas, "height", 0) or 0)
    if width <= 0 or height <= 0:
        return None

    you = _you_cell(character, game, atlas)
    mark = you is not None

    pal = []
    pal_index = {}
    rows = []
    bg_rows = []
    if include_grid:
        glyph_set = "atlas"
        for y in range(height):
            glyphs = []
            bgs = []
            for x in range(width):
                cell = (atlas.terrain or {}).get((x, y)) or {}
                area = cell.get("area_type") or atlas.default_area
                glyph, fg, bg = _cell_style(
                    area,
                    cell.get("map_glyph"),
                    cell.get("map_layer"),
                    glyph_set=glyph_set,
                    terrain_base=cell.get("terrain_base"),
                )
                key = (fg, bg)
                if key not in pal_index:
                    pal_index[key] = len(pal)
                    pal.append({"fg": fg, "bg": bg})
                glyphs.append(glyph)
                bgs.append(pal_index[key])
            rows.append("".join(glyphs))
            bg_rows.append(bgs)

    marks = []
    for _xy, raw in (getattr(atlas, "landmarks", None) or {}).items():
        safe = _safe_landmark(raw)
        if safe is None:
            continue
        verbs = hooks.web_map_cell_verbs(
            character, atlas.map_id, safe["x"], safe["y"], safe
        )
        entry = {
            "x": safe["x"],
            "y": safe["y"],
            "n": safe["label"] or "a landmark",
            "v": verbs or [],
        }
        marks.append(entry)

    map_id = str(getattr(atlas, "map_id", "") or "overland")
    payload = {
        "map": map_id,
        "title": _MAP_TITLES.get(map_id, "Overland"),
        "w": width,
        "h": height,
        "you": you,
        "mark": mark,
        "mode": "conus",
        "marks": marks,
    }
    if include_grid:
        payload["pal"] = pal
        payload["g"] = rows
        payload["p"] = bg_rows
    _assert_player_safe(payload)
    return payload
