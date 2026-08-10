"""
maps.py -- backward-compatible facade for map loading and display.

H1a loader: ``engine.world_maps`` (JSON load, pockets, grids, room factory).
H1b/c UI: ``engine.map_ui`` (minimap, city-paint, landmark vistas).

Import paths like ``import maps`` / ``maps.load_all_maps()`` are unchanged.
"""
from engine import map_ui as _map_ui  # noqa: F401
from engine.world_maps import *  # noqa: F403

# Re-export all map UI symbols (minimap, city-paint, description tables).
from engine.map_ui import *  # noqa: F403

# Module-level state that must stay live-bound to engine.world_maps (not
# copied at import time).
_LIVE_ATTRS = frozenset({
    "CITY_PAINT_BY_MAP_ID",
    "CITY_PAINT_MAP_DEFAULTS",
    "CITY_PAINT_LAYOUT_UNITS",
    "LAST_ZONE_DOC_BY_HUB_KEY",
    "_LANDMARKS_BY_PREFIX",
    "LAST_MAP_REGISTRY",
    "LAST_ROOM_ALIASES",
    "LAST_DEFERRED_MAPS",
})

# ``from engine.world_maps import *`` snapshots these at import time; drop
# the copies so attribute access delegates to world_maps via __getattr__.
for _live_name in _LIVE_ATTRS:
    globals().pop(_live_name, None)


def __getattr__(name):
    """Lazy exports: game hooks, live world_maps state, and ``_`` names.

    ``from engine.world_maps import *`` does not re-export underscore-prefixed
    helpers (_autoload_enabled, _map_id_for, …) but supers/ and engine/ still
    reach them via ``maps._…`` for boot heal and deferred map load.
    """
    if name == "AREA_TYPES":
        from engine import hooks
        return hooks.map_area_types()
    if name in _LIVE_ATTRS:
        from engine import world_maps as _wm
        return getattr(_wm, name)
    from engine import world_maps as _wm
    if hasattr(_wm, name):
        return getattr(_wm, name)
    from engine import map_ui as _mu
    if hasattr(_mu, name):
        return getattr(_mu, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
