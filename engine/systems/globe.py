"""
globe.py -- small orthographic Earth disk for international flight montages.

Layer stack (see docs/plans/globe_flight_layer.md):

  globe (this) → America macro 78×18 → micro 10×10 foot wilderness

The disk stays intentionally tiny (~22×11 cells) so dropping into the US
atlas still feels like a zoom. Domestic America travel stays on the macro;
this surface is for airport flight montages (rotate + optional great-circle
trail). Meaning is carried by glyphs (``~`` / ``.`` / hub letters / ``*``)
plus labels — color is optional accent via ``engine.style``.
"""

from __future__ import annotations

import math

# Character aspect: fewer rows than columns keeps the disk visually round.
GLOBE_COLS = 22
GLOBE_ROWS = 11

# Coarse 36×18 equirectangular landmask (authorable by eye).
# Lon: col 0 = -180 … col 35 ≈ +180. Lat: row 0 = +90 … row 17 = -90.
# Atlantic gap carved so Americas / Europe–Africa stay distinct.
_MASK = [
    "                                    ",
    "            ...                     ",
    "     .....  ...         ......      ",
    "    .......       ... ..........    ",
    "    .......      ................   ",
    "    .......       . .............   ",
    "    .......      ................   ",
    "     .....      ................    ",
    "       ..       ......  .......     ",
    "        ..      ......  ........    ",
    "       ....     ......      ...     ",
    "       ....     ......              ",
    "       ....      ....        ...    ",
    "       ....                 .....   ",
    "       ....                 .....   ",
    "        ..                   ...    ",
    "....................................",
    "....................................",
]
_MASK_W = 36
_MASK_H = 18

# Flight hubs painted on the disk (glyph is the primary tell).
# lat/lon are approximate real-world positions for projection.
HUBS = {
    "nyc": {"glyph": "N", "label": "New York", "lat": 40.7, "lon": -74.0},
    "london": {"glyph": "L", "label": "London", "lat": 51.5, "lon": -0.1},
    "rio": {"glyph": "R", "label": "Rio", "lat": -22.9, "lon": -43.2},
    "tokyo": {"glyph": "T", "label": "Tokyo", "lat": 35.7, "lon": 139.7},
    "sydney": {"glyph": "S", "label": "Sydney", "lat": -33.9, "lon": 151.2},
    # Lebanon KS — not a painted hub letter (too small on the disk); used
    # as a domestic montage camera start near the geographic center.
    "lebanon": {"glyph": "", "label": "Lebanon KS", "lat": 39.4, "lon": -98.5},
    "notbigville": {"glyph": "", "label": "Notbigville KS", "lat": 39.4, "lon": -98.5},
}


def norm_lon(lon):
    """Wrap longitude into (-180, 180]."""
    while lon > 180:
        lon -= 360
    while lon < -180:
        lon += 360
    return lon


def land_at(lat, lon):
    """True when the coarse landmask marks this lat/lon as land."""
    row = int(round((90 - lat) / 180 * (_MASK_H - 1)))
    col = int(round((lon + 180) / 360 * (_MASK_W - 1)))
    row = max(0, min(_MASK_H - 1, row))
    col = max(0, min(_MASK_W - 1, col))
    return _MASK[row][col] == "."


def project(lat, lon, center_lon, cols=GLOBE_COLS, rows=GLOBE_ROWS):
    """Orthographic project lat/lon onto the disk.

    Returns ``(x, y, z)`` with integer cell coords, or ``None`` when the
    point is on the far hemisphere (``z`` too small).
    """
    cx = (cols - 1) / 2.0
    cy = (rows - 1) / 2.0
    rx = cols / 2.0 - 0.45
    ry = rows / 2.0 - 0.45
    dlon = math.radians(lon - center_lon)
    lat_r = math.radians(lat)
    x = math.cos(lat_r) * math.sin(dlon)
    y = -math.sin(lat_r)
    z = math.cos(lat_r) * math.cos(dlon)
    if z <= 0.08:
        return None
    return (
        int(round(cx + x * rx)),
        int(round(cy + y * ry)),
        z,
    )


def great_circle(lat1, lon1, lat2, lon2, steps):
    """Sample ``steps`` interior points along the great circle (no endpoints).

    Used to draw the ``*`` flight track on international montages.
    """
    if steps < 1:
        return []
    φ1 = math.radians(lat1)
    λ1 = math.radians(lon1)
    φ2 = math.radians(lat2)
    λ2 = math.radians(lon2)
    # Angular distance (haversine).
    d = 2 * math.asin(
        math.sqrt(
            math.sin((φ2 - φ1) / 2) ** 2
            + math.cos(φ1) * math.cos(φ2) * math.sin((λ2 - λ1) / 2) ** 2
        )
    )
    if d < 1e-6:
        return []
    pts = []
    for i in range(1, steps):
        f = i / steps
        a = math.sin((1 - f) * d) / math.sin(d)
        b = math.sin(f * d) / math.sin(d)
        x = a * math.cos(φ1) * math.cos(λ1) + b * math.cos(φ2) * math.cos(λ2)
        y = a * math.cos(φ1) * math.sin(λ1) + b * math.cos(φ2) * math.sin(λ2)
        z = a * math.sin(φ1) + b * math.sin(φ2)
        lat = math.degrees(math.atan2(z, math.sqrt(x * x + y * y)))
        lon = math.degrees(math.atan2(y, x))
        pts.append((lat, lon))
    return pts


def _paint_cell(character, kind, ch):
    """Apply a gothic color role; glyphs remain the meaning signal."""
    from engine.style import paint_for

    if kind == "water":
        return paint_for(character, "dark_cyan", ch)
    if kind == "land":
        return paint_for(character, "absinthe_green", ch)
    if kind == "hub":
        return paint_for(character, "dark_red", ch)
    if kind == "flight":
        return paint_for(character, "gold", ch)
    return ch


def render_globe(
    character=None,
    center_lon=-30,
    trail=None,
    hub_ids=None,
    screenreader=False,
    cols=GLOBE_COLS,
    rows=GLOBE_ROWS,
):
    """Build the circular globe string (ANSI when sighted).

    ``trail`` is an optional list of ``(lat, lon)`` for the ``*`` track.
    ``hub_ids`` limits which HUBS glyphs paint (default: nyc + london).
    Screenreader path: one-line SVO summary, no ASCII disk.
    """
    if hub_ids is None:
        hub_ids = ("nyc", "london")
    if screenreader or (
        character is not None and getattr(character, "screenreader", False)
    ):
        hub_bits = []
        for hid in hub_ids:
            h = HUBS.get(hid) or {}
            label = h.get("label") or hid
            hub_bits.append(label)
        trail_note = (
            " Flight track marked."
            if trail
            else ""
        )
        return (
            f"[GLOBE] Earth disk centered near longitude {center_lon:.0f}. "
            f"Hubs: {', '.join(hub_bits) or 'none'}.{trail_note}"
        )

    cx = (cols - 1) / 2.0
    cy = (rows - 1) / 2.0
    rx = cols / 2.0 - 0.45
    ry = rows / 2.0 - 0.45

    # kind grid: out | water | land | flight | hub
    kinds = [["out"] * cols for _ in range(rows)]
    chars = [[" "] * cols for _ in range(rows)]

    for y in range(rows):
        for x in range(cols):
            nx = (x - cx) / rx
            ny = (y - cy) / ry
            if nx * nx + ny * ny > 1.0:
                continue
            z = math.sqrt(max(0.0, 1.0 - nx * nx - ny * ny))
            lat = math.degrees(math.asin(max(-1.0, min(1.0, -ny))))
            lon = norm_lon(center_lon + math.degrees(math.atan2(nx, z)))
            if land_at(lat, lon):
                chars[y][x] = "."
                kinds[y][x] = "land"
            else:
                chars[y][x] = "~"
                kinds[y][x] = "water"

    for lat, lon in trail or ():
        pr = project(lat, lon, center_lon, cols=cols, rows=rows)
        if pr is None:
            continue
        px, py, _z = pr
        if not (0 <= px < cols and 0 <= py < rows):
            continue
        if kinds[py][px] == "out":
            continue
        chars[py][px] = "*"
        kinds[py][px] = "flight"

    for hid in hub_ids:
        h = HUBS.get(hid) or {}
        glyph = (h.get("glyph") or "").strip()
        if not glyph:
            continue
        pr = project(h["lat"], h["lon"], center_lon, cols=cols, rows=rows)
        if pr is None:
            continue
        px, py, _z = pr
        if not (0 <= px < cols and 0 <= py < rows):
            continue
        if kinds[py][px] == "out":
            continue
        chars[py][px] = glyph
        kinds[py][px] = "hub"

    lines = []
    for y in range(rows):
        parts = []
        for x in range(cols):
            ch = chars[y][x]
            kind = kinds[y][x]
            if kind == "out":
                parts.append(ch)
            else:
                parts.append(_paint_cell(character, kind, ch))
        lines.append("".join(parts))
    return "\n".join(lines)


def build_rotate_frames(lon_start, lon_end, steps, hub_ids=None):
    """Domestic-style montage: globe turns from ``lon_start`` to ``lon_end``."""
    if steps < 1:
        steps = 1
    frames = []
    for i in range(steps):
        t = i / max(1, steps - 1) if steps > 1 else 1.0
        # Shortest signed delta across the date line.
        delta = norm_lon(lon_end - lon_start)
        # norm_lon maps to (-180,180]; prefer the short turn.
        if delta > 180:
            delta -= 360
        if delta < -180:
            delta += 360
        lon = norm_lon(lon_start + delta * t)
        frames.append({
            "center_lon": lon,
            "trail": [],
            "hub_ids": list(hub_ids or ("nyc", "london")),
        })
    return frames


def build_path_frames(origin_id, dest_id, steps, hub_ids=None):
    """International-style montage: great-circle track grows across the disk."""
    origin = HUBS.get(origin_id) or {}
    dest = HUBS.get(dest_id) or {}
    if not origin or not dest:
        return build_rotate_frames(-30, -30, steps, hub_ids=hub_ids)
    if steps < 2:
        steps = 2
    # Sample the full track once; each frame reveals a longer prefix.
    samples = great_circle(
        origin["lat"], origin["lon"], dest["lat"], dest["lon"], steps + 1,
    )
    # Include endpoints so the track reaches both hubs.
    full = [
        (origin["lat"], origin["lon"]),
        *samples,
        (dest["lat"], dest["lon"]),
    ]
    frames = []
    hubs = list(hub_ids or (origin_id, dest_id))
    for i in range(steps):
        # Reveal progressively more of the track.
        end = 1 + int(round((i + 1) / steps * (len(full) - 1)))
        trail = full[: max(2, end)]
        # Camera follows the newest trail point.
        lat, lon = trail[-1]
        frames.append({
            "center_lon": norm_lon(lon),
            "trail": trail,
            "hub_ids": hubs,
        })
    return frames
