"""
maps.py -- thin facade re-exporting engine.world_maps.

Loader, minimap stack, and city-paint metadata live in ``engine/world_maps.py``
(two-repo purity H1a–H1c). Import paths like ``import maps`` unchanged.
"""
from engine.world_maps import (
    CITY_PAINT_LAYOUT_UNITS_DEFAULT,
    CITY_PAINT_LAYOUT_UNITS_MIN,
    CITY_PAINT_LAYOUT_UNITS_MAX,
    set_city_paint_enabled,
    set_city_paint_layout_units,
    city_paint_meta_snapshot,
    apply_city_paint_meta,
    refresh_zone_hub_index,
    register_zone_doc_for_hub,
    zone_doc_for_pocket,
    layout_footprint_macro_rect,
    recommended_rect_span_from_layout,
    AREA_TYPE_GLYPH,
    ATLAS_AREA_GLYPH,
    MAP_LAYER_COLOR,
    ATLAS_BG,
    ATLAS_TOPO_FG,
    ATLAS_LAYER_FG,
    ANSI_RESET,
    AREA_TYPE_COLOR,
    PLANE_AREA_COLORS,
    AREA_TYPE_DESCRIPTIONS,
    PLANE_AREA_DESCRIPTIONS,
    MINIMAP_RADIUS,
    LANDMARK_NEARBY_MAX,
    LANDMARK_DISTANCE_MAX,
    LANDMARK_HORIZON_MAX,
    PLAYER_LANDMARK_NEARBY_MAX,
    PLAYER_LANDMARK_DISTANCE_MAX,
    PLAYER_LANDMARK_HORIZON_MAX,
    landmark_vista_lines,
    parse_grid_key,
    render_minimap,
    render_layout_minimap,
    render_exit_graph_minimap,
    render_local_map,
    render_full_grid,
    find_room_by_layout_direction,
    local_map_suppressed,
    TOWN_MINIMAP_RADIUS,
    LOOK_TOWN_MINIMAP_RADIUS,
    LOOK_GRID_MINIMAP_RADIUS,
    set_maps_dir,
    get_maps_dir,
    set_zones_dir,
    get_zones_dir,
    iter_map_json_paths,
    resolve_map_file,
    catalog_map_files,
    _load_map_files,
    load_all_maps,
    create_rooms_from_map_data,
    link_map_data,
    _add_room,
    _build_grid,
    _link_pockets,
    wire_pocket_at_cell,
    unwire_pockets_pointing_at,
    rooms_for_map_id,
    ensure_hand_room_identity,
    qualify_hand_room_key,
    ensure_map_hand_room_keys,
    validate_map_file_header,
    validate_zone_header,
    validate_grid_block,
    validate_pocket_link,
    pocket_enter_aliases,
    zone_entry_look_hints,
    _link_grid_neighbors,
    _link_grid_portals,
    _link_room_exits,
    _resolve_loaded_room,
    _map_id_for,
    _autoload_enabled,
    _normalize_area_type,
    _resolve_plane_and_realm,
    WILD_AREA_TYPES,
    KNOWN_ENV_TAGS,
    KNOWN_MATERIAL_TAGS,
    KNOWN_RESOURCE_TAGS,
    KNOWN_SLAM_DIRECTIONS,
    MATERIAL_STRENGTH,
    MATERIAL_STRENGTH_HP,
    PLANES,
    REALM_FOR_PLANE,
    POCKET_KINDS,
    _LEGACY_AREA_TYPE_ALIASES,
)

# Module-level state that must stay live-bound to engine.world_maps (not
# copied at import time). ``from maps import X`` and attribute reads both
# resolve through __getattr__.
_LIVE_ATTRS = frozenset({
    "CITY_PAINT_ENABLED",
    "CITY_PAINT_LAYOUT_UNITS",
    "LAST_ZONE_DOC_BY_HUB_KEY",
    "_LANDMARKS_BY_PREFIX",
    "LAST_MAP_REGISTRY",
    "LAST_ROOM_ALIASES",
    "LAST_DEFERRED_MAPS",
})


def __getattr__(name):
    """Lazy exports that depend on game hooks or live engine.world_maps state."""
    if name == "AREA_TYPES":
        from engine import hooks
        return hooks.map_area_types()
    if name in _LIVE_ATTRS:
        from engine import world_maps as _wm
        return getattr(_wm, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
