"""
atlas_geo.py -- lat/lon <-> America overland macro cells.

Homesteads (and later mouths) must track *place in the US*, not a frozen
78x18 cell number. When the atlas grid size/projection changes, we
re-project stored coordinates instead of leaving a Kansas plot in the
Atlantic.

Two projections:

* ``legacy-78x18`` -- linear lon/lat into the current shipped atlas.
* ``albers-conus`` -- USGS CONUS Albers (the silhouette preview). Used
  when the live atlas is not 78x18.

Engine-safe: no supers imports.
"""

from __future__ import annotations

import math

# Approximate CONUS box used by the shipped 78x18 atlas.
LEGACY_WIDTH = 78
LEGACY_HEIGHT = 18
LEGACY_WIDTH = LEGACY_WIDTH
LEGACY_HEIGHT = LEGACY_HEIGHT
LON_WEST = -124.85
LON_EAST = -66.95
LAT_SOUTH = 24.40
LAT_NORTH = 49.05

# USGS Contiguous USA Albers Equal Area (matches tools/atlas_conus_shape.py).
_LAT1 = math.radians(29.5)
_LAT2 = math.radians(45.5)
_LAT0 = math.radians(37.5)
_LON0 = math.radians(-96.0)
_N = 0.5 * (math.sin(_LAT1) + math.sin(_LAT2))
_C = math.cos(_LAT1) ** 2 + 2 * _N * math.sin(_LAT1)
_RHO0 = math.sqrt(_C - 2 * _N * math.sin(_LAT0)) / _N

# Albers bbox of the preview CONUS ring in tools/atlas_conus_shape.py
# (polygon + 2% pad). Must stay bit-identical so homestead remap lands
# on the same cells the silhouette preview uses.
_ALBERS_X0 = -0.3835321683954042
_ALBERS_X1 = 0.36782468032245597
_ALBERS_Y0 = -0.21287487139877698
_ALBERS_Y1 = 0.2539398004409997


def projection_for_size(width: int, height: int) -> str:
    """Pick projection from atlas dimensions."""
    if int(width) == LEGACY_WIDTH and int(height) == LEGACY_HEIGHT:
        return "legacy-78x18"
    return "albers-conus"


def albers_xy(lat: float, lon: float) -> tuple[float, float]:
    """Project WGS84 to CONUS Albers (unit sphere)."""
    phi = math.radians(lat)
    lam = math.radians(lon)
    theta = _N * (lam - _LON0)
    rho = math.sqrt(_C - 2 * _N * math.sin(phi)) / _N
    x = rho * math.sin(theta)
    y = _RHO0 - rho * math.cos(theta)
    return x, y


def albers_xy_inv(x: float, y: float) -> tuple[float, float]:
    """Inverse CONUS Albers: Albers (x, y) -> (lat, lon) WGS84."""
    rho = math.copysign(math.hypot(x, _RHO0 - y), _N)
    theta = math.atan2(x, _RHO0 - y)
    sin_phi = (_C - (rho * _N) ** 2) / (2.0 * _N)
    sin_phi = max(-1.0, min(1.0, sin_phi))
    lat = math.degrees(math.asin(sin_phi))
    lon = math.degrees(_LON0 + theta / _N)
    return lat, lon


def albers_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """Inverse CONUS Albers (unit sphere) -> (lat, lon) degrees."""
    # Snyder inverse: n > 0 for the lower 48.
    theta = math.atan2(x, _RHO0 - y)
    rho = math.hypot(x, _RHO0 - y)
    if _N < 0:
        rho = -rho
    phi = math.asin((_C - (rho * _N) ** 2) / (2.0 * _N))
    lam = _LON0 + theta / _N
    return math.degrees(phi), math.degrees(lam)


def lonlat_to_cell(
    lat: float, lon: float, width: int, height: int, *, projection=None
) -> tuple[int, int]:
    """Map a WGS84 point onto a (width x height) atlas; y=0 is south."""
    width = int(width)
    height = int(height)
    projection = projection or projection_for_size(width, height)
    if projection == "legacy-78x18":
        col = int((lon - LON_WEST) / (LON_EAST - LON_WEST) * width)
        row = int((lat - LAT_SOUTH) / (LAT_NORTH - LAT_SOUTH) * height)
    else:
        ax, ay = albers_xy(lat, lon)
        col = int((ax - _ALBERS_X0) / (_ALBERS_X1 - _ALBERS_X0) * width)
        row = int((ay - _ALBERS_Y0) / (_ALBERS_Y1 - _ALBERS_Y0) * height)
    col = max(0, min(width - 1, col))
    row = max(0, min(height - 1, row))
    return col, row


def cell_to_lonlat(
    x: int, y: int, width: int, height: int, *, projection=None
) -> tuple[float, float]:
    """Lat/lon of a cell center. y=0 is south."""
    width = int(width)
    height = int(height)
    projection = projection or projection_for_size(width, height)
    if projection == "legacy-78x18":
        lon = LON_WEST + (int(x) + 0.5) / width * (LON_EAST - LON_WEST)
        lat = LAT_SOUTH + (int(y) + 0.5) / height * (LAT_NORTH - LAT_SOUTH)
        return lat, lon
    ax = _ALBERS_X0 + (int(x) + 0.5) / width * (_ALBERS_X1 - _ALBERS_X0)
    ay = _ALBERS_Y0 + (int(y) + 0.5) / height * (_ALBERS_Y1 - _ALBERS_Y0)
    return albers_to_lonlat(ax, ay)


def atlas_size(game) -> tuple[int, int]:
    """Live America atlas dimensions, falling back to the shipped 78x18."""
    atlas = getattr(game, "overland_atlas", None)
    if atlas is not None:
        try:
            return int(atlas.width), int(atlas.height)
        except (TypeError, ValueError, AttributeError):
            pass
    return LEGACY_WIDTH, LEGACY_HEIGHT


def snap_to_land_cell(game, x: int, y: int, max_radius: int = 8) -> tuple[int, int]:
    """Nudge (x, y) onto a non-ocean atlas cell when the atlas is loaded."""
    atlas = getattr(game, "overland_atlas", None)
    width, height = atlas_size(game)
    x = max(0, min(width - 1, int(x)))
    y = max(0, min(height - 1, int(y)))
    if atlas is None:
        return x, y

    def _land(cx, cy):
        if not (0 <= cx < width and 0 <= cy < height):
            return False
        area = atlas.terrain_at(cx, cy)
        return area not in ("ocean", "lake", "water", "void", None, "")

    if _land(x, y):
        return x, y
    for radius in range(1, max_radius + 1):
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                nx, ny = x + dx, y + dy
                if _land(nx, ny):
                    return nx, ny
    return x, y


# Homestead / GMCP call-site aliases.
lonlat_to_cell = lonlat_to_cell
snap_to_land_cell = snap_to_land_cell
atlas_size = atlas_size
cell_to_lonlat = cell_to_lonlat
snap_to_land_cell = snap_to_land_cell
