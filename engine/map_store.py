"""
engine/map_store.py -- generic OLC map/zone JSON dual-write (dig / link / rset).

Rooms are authored in content/maps/*.json and content/zones/*.json. This
module is the in-game write half so staff can dig/link/edit while standing
in the world and have changes survive copyover/restart.

Contract (same spirit as jobs/npc catalogs):
  1. Patch the owning map/zone JSON on disk (atomic write).
  2. Validate with maps.load_all_maps(); rollback the file on failure.
  3. Apply the same change to the live game.rooms graph (do NOT replace
     game.rooms -- that would orphan Character.location refs).

Grid cells are not in rooms[] -- dig from a grid cell adds a hand room
plus a grid.portals entry. Pocket settlement enter/exit stays Studio/JSON
(pockets[]); do not dig in/out for those gateways.

SUPERS field catalogs for ``rset_field`` register via
``engine.hooks.set_rset_flag_catalog`` at boot.
"""

from __future__ import annotations

import copy
import os

from engine.world import Room
from engine import room_vnum as room_vnum_mod
from engine.content_store import load_json, save_json
from engine import hooks as hooks_mod

import maps

OPPOSITE = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
    "northeast": "southwest",
    "southwest": "northeast",
    "northwest": "southeast",
    "southeast": "northwest",
    "up": "down",
    "down": "up",
    "in": "out",
    "out": "in",
    "leave": "in",
}

def opposite_direction(direction):
    """Return the reverse direction string, or None if unknown."""
    return OPPOSITE.get((direction or "").strip().lower())

def is_grid_room(room):
    """True when this Room was built from a procedural grid cell."""
    return getattr(room, "grid_prefix", None) is not None

def resolve_map_path(game, room):
    """Return (abs_path, kind, filename, map_id) for the room's map/zone file.

    kind is 'map' or 'zone'. Raises ValueError when the room has no map_id
    or the registry/file cannot be found.
    """
    map_id = getattr(room, "map_id", None)
    if not map_id:
        raise ValueError(
            "This room has no map_id -- cannot locate its JSON file."
        )
    registry = getattr(game, "map_registry", None) or {}
    entry = registry.get(map_id)
    if not entry:
        raise ValueError(
            f"map_id {map_id!r} is not in game.map_registry "
            "(was the world loaded from maps.load_all_maps?)."
        )
    filename = entry.get("filename")
    if not filename:
        raise ValueError(f"map_id {map_id!r} has no filename in the registry.")
    maps_path = os.path.join(maps.get_maps_dir(), filename)
    zones_path = os.path.join(maps.get_zones_dir(), filename)
    if os.path.isfile(maps_path):
        return maps_path, "map", filename, map_id
    if os.path.isfile(zones_path):
        return zones_path, "zone", filename, map_id
    raise ValueError(
        f"Map file {filename!r} for map_id {map_id!r} not found under "
        f"content/maps/ or content/zones/."
    )

def load_doc(path):
    """Load one map/zone JSON document (normalize rooms/grid containers)."""
    data = load_json(path)
    return _normalize_doc(data)

def _normalize_doc(data):
    """Ensure rooms/pockets/grid containers exist (deep copy)."""
    data = copy.deepcopy(data) if data else {}
    if data.get("rooms") is None:
        data["rooms"] = []
    if data.get("pockets") is None:
        data["pockets"] = []
    grid = data.get("grid")
    if isinstance(grid, dict):
        grid.setdefault("cell_overrides", {})
        if "portals" not in grid or grid["portals"] is None:
            grid["portals"] = []
    return data

def save_doc_validated(path, doc):
    """Atomic write + full maps.load_all_maps() validate; rollback on failure.

    Validates with ``include_deferred=True`` so exits that target rooms in
    ``autoload: false`` zones (Lebanon, etc.) are resolved. Boot still
    skips those files until ``gm maps load``.

    Returns a short ok dict. Does NOT replace the live game.rooms graph.
    """
    return _save_doc_validated_fn(path, doc)


def _save_doc_validated_impl(path, doc):
    payload = _normalize_doc(doc)
    plane = payload.get("plane")
    if plane in maps.REALM_FOR_PLANE:
        payload["realm"] = maps.REALM_FOR_PLANE[plane]

    had_file = os.path.isfile(path)
    old_bytes = None
    if had_file:
        with open(path, "rb") as handle:
            old_bytes = handle.read()

    # First dig / Studio-validated write: seed a staff backup from the
    # *pre-write* bytes so a bad edit can ``gm maps restore`` later.
    if had_file and old_bytes is not None:
        try:
            from engine import map_backups

            map_id = maps._map_id_for(os.path.basename(path), payload)
            map_backups.seed_backup_bytes_if_missing(old_bytes, map_id)
        except Exception:
            # Never block a dig on backup I/O.
            pass

    save_json(path, payload)
    try:
        rooms, start_room, _ = maps.load_all_maps(include_deferred=True)
    except Exception as exc:
        if had_file and old_bytes is not None:
            with open(path, "wb") as handle:
                handle.write(old_bytes)
        elif not had_file and os.path.isfile(path):
            try:
                os.unlink(path)
            except OSError:
                pass
        raise ValueError(str(exc)) from exc

    map_id = maps._map_id_for(os.path.basename(path), payload)
    backup_note = None
    try:
        from engine import map_archive, map_backups

        map_backups.refresh_backup_from_live(path, map_id, root=os.getcwd())
        map_archive.mark_map_edited(map_id)
        backup_note = map_id
    except Exception:
        pass

    return {
        "ok": True,
        "room_count": len(rooms),
        "start_room": start_room.key if start_room else None,
        "path": path,
        "map_id": map_id,
        "hot_backup_refreshed": backup_note,
    }


_save_doc_validated_fn = _save_doc_validated_impl

def find_room_entry(doc, key):
    """Return the rooms[] dict for key, VNUM, or matching legacy key."""
    if not key:
        return None
    text = str(key).strip()
    for room in doc.get("rooms") or []:
        if room.get("key") == text:
            return room
        if str(room.get("vnum") or "").strip().upper() == text.upper():
            return room
    return None

def _cell_override_key(room):
    """grid.cell_overrides key string for a grid Room."""
    return f"{int(room.grid_x)},{int(room.grid_y)}"

def _ensure_hand_room_entry(doc, room):
    """Return (or create) the rooms[] dict for a live hand-authored Room.

    Grid cells must not be written into rooms[] (key collision with the
    procedural grid). Callers handle grid via cell_overrides / portals.

    Phase 3: live ``room.key`` is the VNUM; JSON may still use the legacy
    dig key until rewrite -- match by VNUM field or ``legacy_key``.
    """
    if is_grid_room(room):
        raise ValueError(
            f"{room.key!r} is a grid cell -- edit via cell_overrides / "
            "portals, not rooms[]."
        )
    entry = find_room_entry(doc, room.key)
    if entry is None:
        leg = getattr(room, "legacy_key", None)
        if leg:
            entry = find_room_entry(doc, leg)
    if entry is not None:
        return entry
    entry = {
        "key": room.key,
        "description": room.description or "",
        "area_type": getattr(room, "area_type", None) or "city",
        "exits": {},
    }
    if getattr(room, "title", None):
        entry["title"] = room.title
    if getattr(room, "vnum", None):
        entry["vnum"] = room.vnum
    if getattr(room, "zone", None):
        entry["zone"] = room.zone
    doc.setdefault("rooms", []).append(entry)
    return entry

def _stamp_live_room_from_source(new_room, source_room, map_id):
    """Copy plane/realm/map_id defaults onto a newly dug Room."""
    new_room.map_id = map_id
    new_room.plane = getattr(source_room, "plane", None) or "earth"
    new_room.realm = getattr(source_room, "realm", None) or "prime"
    new_room.area_type = getattr(source_room, "area_type", None) or "city"
    new_room.wilderness = False
    new_room.outdoor = False
    if getattr(source_room, "zone", None):
        new_room.zone = source_room.zone

def _live_set_exit(game, from_key, direction, to_key):
    """Wire live Room.exits[direction] = Room for to_key."""
    src = game.rooms.get(from_key)
    dest = game.rooms.get(to_key)
    if src is None:
        raise ValueError(f"Live room {from_key!r} missing.")
    if dest is None:
        raise ValueError(f"Live room {to_key!r} missing.")
    src.exits[direction] = dest

def _live_clear_exit(game, from_key, direction):
    """Remove a live exit if present."""
    src = game.rooms.get(from_key)
    if src is None:
        return
    src.exits.pop(direction, None)

def _taken_vnums(game, *, extra=None):
    """Set of validated vnum strings already claimed live (plus extras)."""
    taken = room_vnum_mod.collect_taken_vnums(
        (getattr(game, "rooms", None) or {}).values()
    )
    for raw in extra or ():
        if raw is None or str(raw).strip() == "":
            continue
        taken.add(room_vnum_mod.validate_vnum(raw))
    return taken

def _allocate_and_stamp_vnum(game, entry, live=None, *, taken=None):
    """Assign a globally unique vnum onto a rooms[] dict and optional Room.

    Mutates ``entry`` (sets ``vnum``). When ``live`` is provided, stamps
    ``live.vnum`` too. ``taken`` may be a shared set updated in place for
    batch allocates (append_hand_rooms).
    """
    if taken is None:
        taken = _taken_vnums(game)
    raw = entry.get("vnum")
    if raw is not None and str(raw).strip():
        vnum = room_vnum_mod.validate_vnum(raw)
        if vnum in taken:
            vnum = room_vnum_mod.allocate_vnum_for_name(
                entry.get("key") or getattr(live, "key", ""),
                entry.get("title"),
                taken=taken,
            )
    else:
        vnum = room_vnum_mod.allocate_vnum_for_name(
            entry.get("key") or getattr(live, "key", ""),
            entry.get("title"),
            taken=taken,
        )
    entry["vnum"] = vnum
    taken.add(vnum)
    if live is not None:
        live.vnum = vnum
    return vnum

def inspect_lines(game, room):
    """Build client-wrappable inspect lines for gm room (no color meaning)."""
    lines = []
    # Staff identity: ROOM NAME + VNUM first; internal key is dig/compat only.
    label = room_vnum_mod.staff_room_label(room)
    name = room_vnum_mod.room_name(room) or getattr(room, "key", "")
    lines.append(f"ROOM NAME: {name}")
    vnum = getattr(room, "vnum", None)
    if vnum:
        packed = room_vnum_mod.pack_vnum(vnum)
        lines.append(f"VNUM: {vnum} (GMCP num {packed})")
    elif not is_grid_room(room):
        lines.append("VNUM: (none -- boot heal will stamp on next restart)")
    if label and label != name:
        lines.append(f"Staff label: {label}")
    leg = getattr(room, "legacy_key", None)
    if leg and str(leg).strip() and str(leg).strip() != str(vnum or "").strip():
        lines.append(
            f"Legacy key: {leg} (alias only -- use VNUM / ROOM NAME)"
        )
    try:
        path, kind, filename, map_id = resolve_map_path(game, room)
        lines.append(f"Map: {map_id} ({kind} file {filename})")
        lines.append(f"File: {path}")
    except ValueError as err:
        lines.append(f"Map file: ({err})")
    lines.append(
        f"Plane/realm: {getattr(room, 'plane', '?')}/"
        f"{getattr(room, 'realm', '?')}"
    )
    if getattr(room, "zone", None):
        lines.append(f"Zone: {room.zone}")
    lines.append(f"Area type: {getattr(room, 'area_type', '?')}")
    if is_grid_room(room):
        lines.append(
            f"Grid: {room.grid_prefix} ({room.grid_x}, {room.grid_y})"
        )
    flags = sorted(
        name for name in sorted(hooks_mod.rset_flag_catalog()[0])
        if getattr(room, name, False)
    )
    if flags:
        lines.append("Flags: " + ", ".join(flags))
    resources = getattr(room, "resources", None) or []
    if resources:
        lines.append("Resources: " + ", ".join(resources))
    jobs = getattr(room, "jobs", None) or []
    if jobs:
        lines.append("Jobs: " + ", ".join(jobs))
    exits = getattr(room, "exits", None) or {}
    if exits:
        lines.append("Exits:")
        for direction in sorted(exits.keys(), key=str):
            dest = exits[direction]
            dest_label = room_vnum_mod.staff_room_label(dest) or getattr(
                dest, "key", str(dest),
            )
            lines.append(f"  {direction} -> {dest_label}")
    else:
        lines.append("Exits: (none)")
    lines.append(
        "Edit: room dig|link|unlink|rset|create  (help room | help rset). "
        "Link targets: VNUM or unique ROOM NAME. "
        "Bare room rset lists every field/flag. "
        "Prefer Area Studio for big builds."
    )
    return lines

def _room_owner_hint(game, room_or_key):
    """Short 'map_id=… (filename)' hint for dig/link refusal messages."""
    room = room_or_key
    if isinstance(room_or_key, str):
        room = (getattr(game, "rooms", None) or {}).get(room_or_key)
    if room is None:
        return "unknown map"
    map_id = getattr(room, "map_id", None) or "?"
    registry = getattr(game, "map_registry", None) or {}
    entry = registry.get(map_id) or {}
    filename = entry.get("filename")
    if filename:
        return f"map_id={map_id!r} ({filename})"
    return f"map_id={map_id!r}"

def dig_room(game, from_room, direction, new_key, *,
             description=None, title=None, bidirectional=True):
    """Create a new hand room, link it, persist JSON, update live graph.

    From a hand room: appends to rooms[] and exits.
    From a grid cell: appends rooms[] + grid.portals + reverse exit on the
    new room.

    Builder-typed names are map-qualified (``{map_id}:Name``) so towns can
    reuse the same look name; the short name is stored as ``title``.

    Returns a short success message string. Raises ValueError on refusal.
    """
    direction = (direction or "").strip().lower()
    new_key = (new_key or "").strip()
    title = (title or "").strip() or None
    # Name-only dig: treat title as the builder-typed local name.
    if not new_key and title:
        new_key = title
        title = None
    if not direction or not new_key:
        raise ValueError(
            "Usage: room dig <direction> <ROOM NAME…> "
            "(or pass title= when name is blank)"
        )
    path, _kind, _filename, map_id = resolve_map_path(game, from_room)
    # Map-scope the typed name so 'Apartment Floor C' in Lebanon does not
    # collide with Wastes Ash Court. Explicit title= still wins for look.
    local_name = new_key
    new_key, auto_title = maps.qualify_hand_room_key(
        map_id, local_name, taken=game.rooms.keys(),
    )
    if title is None:
        title = auto_title
    if new_key in game.rooms:
        owner = _room_owner_hint(game, new_key)
        raise ValueError(
            f"Room key {new_key!r} already exists ({owner}). "
            "Pick another name."
        )
    doc = load_doc(path)

    if find_room_entry(doc, new_key) is not None:
        raise ValueError(
            f"Room key {new_key!r} already in {os.path.basename(path)}."
        )

    desc = description or f"A newly dug room ({title or new_key})."
    new_entry = {
        "key": new_key,
        "description": desc,
        "area_type": getattr(from_room, "area_type", None) or "city",
        "exits": {},
    }
    if title and title != new_key:
        new_entry["title"] = title
    zone = getattr(from_room, "zone", None)
    if zone and not is_grid_room(from_room):
        new_entry["zone"] = zone

    back = opposite_direction(direction) if bidirectional else None

    if is_grid_room(from_room):
        grid = doc.get("grid")
        if not isinstance(grid, dict):
            raise ValueError("This map has no grid -- cannot dig from a cell.")
        portals = grid.setdefault("portals", [])
        portals[:] = [
            p for p in portals
            if not (
                int(p.get("x", -1)) == int(from_room.grid_x)
                and int(p.get("y", -1)) == int(from_room.grid_y)
                and str(p.get("direction", "")).lower() == direction
            )
        ]
        portals.append({
            "x": int(from_room.grid_x),
            "y": int(from_room.grid_y),
            "direction": direction,
            "to_room": new_key,
        })
        if back:
            new_entry["exits"][back] = from_room.key
        # This room is a fresh zone mouth off the procedural grid (grid
        # cells carry grid_x/grid_y, not layout) -- seed it at (0,0,0), the
        # same convention retrofit_zone_layout.md uses for a zone entrance,
        # so it and everything dug from it have layout from the start
        # (wall_floor_breach_mechanic.md Phase A).
        new_entry["layout"] = {"x": 0, "y": 0, "z": 0}
        doc.setdefault("rooms", []).append(new_entry)
    else:
        src_entry = _ensure_hand_room_entry(doc, from_room)
        src_entry.setdefault("exits", {})[direction] = new_key
        if back:
            new_entry["exits"][back] = from_room.key
        # Studio layout: every room dug from here on gets a layout stamp
        # (wall_floor_breach_mechanic.md Phase A) -- no more silent
        # layout-less rooms from the in-game dig path. Diagonals included
        # so populate neighborhood nw does not look like SW after Pull
        # from live; up/in/down/out move the z axis (matches
        # tools/retrofit_zone_layout.py LAYER_UP/LAYER_DOWN and
        # maps._LAYOUT_XY_DELTA).
        _LAYOUT_DELTA = {
            "north": (0, 1), "south": (0, -1),
            "east": (1, 0), "west": (-1, 0),
            "northeast": (1, 1), "northwest": (-1, 1),
            "southeast": (1, -1), "southwest": (-1, -1),
        }
        _LAYER_UP = frozenset({"up", "in"})
        _LAYER_DOWN = frozenset({"down", "out"})
        src_layout = src_entry.get("layout")
        if not (
            isinstance(src_layout, dict)
            and "x" in src_layout
            and "y" in src_layout
        ):
            # Live Room may already have layout_* from boot even when
            # the JSON entry was rebuilt without a layout blob.
            lx = getattr(from_room, "layout_x", None)
            ly = getattr(from_room, "layout_y", None)
            if lx is not None and ly is not None:
                src_layout = {
                    "x": int(lx),
                    "y": int(ly),
                    "z": int(getattr(from_room, "layout_z", 0) or 0),
                }
            else:
                src_layout = None
        if not (
            isinstance(src_layout, dict)
            and "x" in src_layout
            and "y" in src_layout
        ):
            # Source room has no layout at all yet (e.g. it is itself a
            # fresh zone mouth) -- seed it at the crawler's own convention
            # (retrofit_zone_layout.md: "seed entrance at (0,0,0)") so this
            # dig, and every dig after it, has real coords to build from.
            src_layout = {"x": 0, "y": 0, "z": 0}
            src_entry["layout"] = dict(src_layout)
            if (
                getattr(from_room, "layout_x", None) is None
                or getattr(from_room, "layout_y", None) is None
            ):
                from_room.layout_x = 0
                from_room.layout_y = 0
                from_room.layout_z = 0
        try:
            lx = int(src_layout["x"])
            ly = int(src_layout["y"])
            lz = int(src_layout.get("z", 0) or 0)
            if direction in _LAYOUT_DELTA:
                dx, dy = _LAYOUT_DELTA[direction]
                new_entry["layout"] = {"x": lx + dx, "y": ly + dy, "z": lz}
            elif direction in _LAYER_UP:
                new_entry["layout"] = {"x": lx, "y": ly, "z": lz + 1}
            elif direction in _LAYER_DOWN:
                new_entry["layout"] = {"x": lx, "y": ly, "z": lz - 1}
            else:
                # Unknown/address-style direction (digit exits etc.) --
                # retrofit_zone_layout.md Q7: skip, do not guess a cell.
                pass
        except (TypeError, ValueError):
            pass
        doc.setdefault("rooms", []).append(new_entry)

    vnum = _allocate_and_stamp_vnum(game, new_entry)
    # Phase 3: VNUM is the graph / JSON identity; ROOM NAME stays in title.
    room_name_face = (title or local_name or "").strip() or local_name
    new_entry["title"] = room_name_face
    legacy_dig_key = new_key
    new_entry["key"] = vnum
    # Fix exit / portal targets written above to use identity keys.
    from_id = room_vnum_mod.internal_room_key(from_room) or from_room.key
    if is_grid_room(from_room):
        for portal in doc.get("grid", {}).get("portals", []) or []:
            if (
                int(portal.get("x", -1)) == int(from_room.grid_x)
                and int(portal.get("y", -1)) == int(from_room.grid_y)
                and str(portal.get("direction", "")).lower() == direction
            ):
                portal["to_room"] = vnum
        if back:
            new_entry["exits"][back] = from_id
    else:
        src_entry = _ensure_hand_room_entry(doc, from_room)
        src_entry.setdefault("exits", {})[direction] = vnum
        if back:
            new_entry["exits"][back] = from_id
    save_doc_validated(path, doc)

    live = Room(vnum, desc)
    live.title = room_name_face
    live.vnum = vnum
    live.legacy_key = legacy_dig_key
    _stamp_live_room_from_source(live, from_room, map_id)
    if zone and not is_grid_room(from_room):
        live.zone = zone
    # Live layout matches JSON when dig stamped canvas coords.
    layout_blob = new_entry.get("layout")
    if isinstance(layout_blob, dict) and "x" in layout_blob and "y" in layout_blob:
        try:
            live.layout_x = int(layout_blob["x"])
            live.layout_y = int(layout_blob["y"])
            live.layout_z = int(layout_blob.get("z", 0) or 0)
        except (TypeError, ValueError):
            pass
    game.rooms[vnum] = live
    aliases = getattr(game, "room_aliases", None)
    if aliases is None:
        game.room_aliases = {}
        aliases = game.room_aliases
    aliases[legacy_dig_key] = vnum
    _live_set_exit(game, from_id, direction, vnum)
    if back:
        _live_set_exit(game, vnum, back, from_id)

    msg = (
        f"Dug {room_vnum_mod.staff_room_label(live) or vnum} "
        f"{direction} of "
        f"{room_vnum_mod.staff_room_label(from_room) or from_id} "
        f"(saved)."
    )
    msg += f" ROOM NAME: {room_name_face!r}."
    msg += f" VNUM: {vnum}."
    return msg

def create_room(game, here, new_key, *, description=None, title=None):
    """Create a disconnected hand room in the same map file as `here`."""
    new_key = (new_key or "").strip()
    title = (title or "").strip() or None
    if not new_key and title:
        new_key = title
        title = None
    if not new_key:
        raise ValueError(
            "Usage: room create <ROOM NAME…> "
            "(or pass title= when name is blank)"
        )
    path, _kind, _filename, map_id = resolve_map_path(game, here)
    local_name = new_key
    new_key, auto_title = maps.qualify_hand_room_key(
        map_id, local_name, taken=game.rooms.keys(),
    )
    if title is None:
        title = auto_title
    if new_key in game.rooms:
        owner = _room_owner_hint(game, new_key)
        raise ValueError(
            f"Room key {new_key!r} already exists ({owner}). "
            "Pick another name."
        )
    doc = load_doc(path)
    if find_room_entry(doc, new_key) is not None:
        raise ValueError(f"Room key {new_key!r} already in file.")
    desc = description or f"A newly created room ({title or new_key})."
    entry = {
        "key": new_key,
        "description": desc,
        "area_type": getattr(here, "area_type", None) or "city",
        "exits": {},
    }
    if title and title != new_key:
        entry["title"] = title
    zone = getattr(here, "zone", None)
    if zone and not is_grid_room(here):
        entry["zone"] = zone
    vnum = _allocate_and_stamp_vnum(game, entry)
    doc.setdefault("rooms", []).append(entry)
    save_doc_validated(path, doc)

    live = Room(new_key, desc)
    if title and title != new_key:
        live.title = title
    live.vnum = vnum
    _stamp_live_room_from_source(live, here, map_id)
    if zone and not is_grid_room(here):
        live.zone = zone
    game.rooms[new_key] = live
    return (
        f"Created disconnected room "
        f"{room_vnum_mod.staff_room_label(live) or new_key} (saved). "
        + (
            f"ROOM NAME: {title!r}. "
            if title and title != new_key else ""
        )
        + f"VNUM: {vnum}. "
        + "Link it with room link <dir> <VNUM|ROOM NAME>."
    )

def link_rooms(game, from_room, direction, to_query, *, bidirectional=True):
    """Link from_room toward an existing room; persist both sides as needed.

    ``to_query`` is a staff address: **VNUM** or unique **ROOM NAME**
    (internal key still accepted silently until Phase 3). Exit JSON still
    stores the destination's internal key.
    """
    direction = (direction or "").strip().lower()
    to_query = (to_query or "").strip()
    if not direction or not to_query:
        raise ValueError(
            "Usage: room link <direction> <VNUM|ROOM NAME>"
        )
    dest, err = room_vnum_mod.resolve_room_or_error(game, to_query)
    if dest is None:
        raise ValueError(err)
    to_key = room_vnum_mod.internal_room_key(dest)
    dest_label = room_vnum_mod.staff_room_label(dest) or to_key
    if from_room is dest:
        raise ValueError("Cannot link a room to itself.")

    back = opposite_direction(direction) if bidirectional else None
    # Refuse silent rewrites of an existing reverse exit on another room
    # (e.g. linking Lebanon up -> Wastes 'Apartment Floor C' would steal
    # Ash Court's down exit). Dig a new unique ROOM NAME instead.
    if back:
        existing_back = (getattr(dest, "exits", None) or {}).get(back)
        existing_key = (
            getattr(existing_back, "key", None)
            if existing_back is not None else None
        )
        if existing_key and existing_key != from_room.key:
            dest_owner = _room_owner_hint(game, dest)
            existing_label = room_vnum_mod.staff_room_label(existing_back) or (
                existing_key
            )
            raise ValueError(
                f"Refusing to overwrite {dest_label}'s {back!r} exit "
                f"(currently -> {existing_label} on {dest_owner}). "
                f"That is probably not the room you meant — dig a NEW "
                f"room with a unique ROOM NAME, or 'room unlink {back}' on "
                f"the destination first if you really intend to rewire it."
            )

    from_path, _, _, _ = resolve_map_path(game, from_room)
    from_doc = load_doc(from_path)

    if is_grid_room(from_room):
        grid = from_doc.get("grid")
        if not isinstance(grid, dict):
            raise ValueError("This map has no grid.")
        portals = grid.setdefault("portals", [])
        portals[:] = [
            p for p in portals
            if not (
                int(p.get("x", -1)) == int(from_room.grid_x)
                and int(p.get("y", -1)) == int(from_room.grid_y)
                and str(p.get("direction", "")).lower() == direction
            )
        ]
        portals.append({
            "x": int(from_room.grid_x),
            "y": int(from_room.grid_y),
            "direction": direction,
            "to_room": to_key,
        })
    else:
        src_entry = _ensure_hand_room_entry(from_doc, from_room)
        src_entry.setdefault("exits", {})[direction] = to_key

    save_doc_validated(from_path, from_doc)

    if back:
        to_path, _, _, _ = resolve_map_path(game, dest)
        to_doc = load_doc(to_path)
        if is_grid_room(dest):
            grid = to_doc.get("grid")
            if isinstance(grid, dict):
                portals = grid.setdefault("portals", [])
                portals[:] = [
                    p for p in portals
                    if not (
                        int(p.get("x", -1)) == int(dest.grid_x)
                        and int(p.get("y", -1)) == int(dest.grid_y)
                        and str(p.get("direction", "")).lower() == back
                    )
                ]
                portals.append({
                    "x": int(dest.grid_x),
                    "y": int(dest.grid_y),
                    "direction": back,
                    "to_room": from_room.key,
                })
        else:
            dest_entry = _ensure_hand_room_entry(to_doc, dest)
            dest_entry.setdefault("exits", {})[back] = from_room.key
        save_doc_validated(to_path, to_doc)

    _live_set_exit(game, from_room.key, direction, to_key)
    if back:
        _live_set_exit(game, to_key, back, from_room.key)

    extra = f" (and {back} back)" if back else ""
    from_label = room_vnum_mod.staff_room_label(from_room) or from_room.key
    return (
        f"Linked {from_label} --{direction}--> {dest_label}{extra} (saved)."
    )

def unlink_exit(game, from_room, direction, *, bidirectional=True):
    """Remove an exit from from_room; optionally clear the reverse."""
    direction = (direction or "").strip().lower()
    if not direction:
        raise ValueError("Usage: room unlink <direction>")
    dest = (getattr(from_room, "exits", None) or {}).get(direction)
    dest_key = getattr(dest, "key", None) if dest is not None else None
    back = opposite_direction(direction) if bidirectional else None

    path, _, _, _ = resolve_map_path(game, from_room)
    doc = load_doc(path)

    if is_grid_room(from_room):
        grid = doc.get("grid")
        if isinstance(grid, dict):
            portals = grid.setdefault("portals", [])
            portals[:] = [
                p for p in portals
                if not (
                    int(p.get("x", -1)) == int(from_room.grid_x)
                    and int(p.get("y", -1)) == int(from_room.grid_y)
                    and str(p.get("direction", "")).lower() == direction
                )
            ]
    else:
        entry = find_room_entry(doc, from_room.key)
        if entry is not None:
            entry.setdefault("exits", {}).pop(direction, None)

    save_doc_validated(path, doc)

    if back and dest_key and dest_key in game.rooms:
        other = game.rooms[dest_key]
        rev = (getattr(other, "exits", None) or {}).get(back)
        if rev is from_room or getattr(rev, "key", None) == from_room.key:
            other_path, _, _, _ = resolve_map_path(game, other)
            other_doc = load_doc(other_path)
            if is_grid_room(other):
                grid = other_doc.get("grid")
                if isinstance(grid, dict):
                    portals = grid.setdefault("portals", [])
                    portals[:] = [
                        p for p in portals
                        if not (
                            int(p.get("x", -1)) == int(other.grid_x)
                            and int(p.get("y", -1)) == int(other.grid_y)
                            and str(p.get("direction", "")).lower() == back
                        )
                    ]
            else:
                oentry = find_room_entry(other_doc, dest_key)
                if oentry is not None:
                    oentry.setdefault("exits", {}).pop(back, None)
            save_doc_validated(other_path, other_doc)
            _live_clear_exit(game, dest_key, back)

    _live_clear_exit(game, from_room.key, direction)
    from_label = room_vnum_mod.staff_room_label(from_room) or from_room.key
    return f"Unlinked {direction} from {from_label} (saved)."

def rset_field(game, room, field, value):
    """Set a persisted field on room (JSON + live). Returns a message."""
    field = (field or "").strip().lower()
    value = (value or "").strip()
    if not field:
        # Same body as bare ``room rset`` / help rset (shared formatter).
        raise ValueError("\n".join(hooks_mod.rset_reference_lines()))

    bool_flags, text_fields = hooks_mod.rset_flag_catalog()
    path, _, _, _ = resolve_map_path(game, room)
    doc = load_doc(path)

    if field in bool_flags:
        on = value.lower() in ("1", "true", "on", "yes", "y")
        off = value.lower() in ("0", "false", "off", "no", "n")
        if not on and not off:
            raise ValueError(
                f"Flag {field}: use on|off (got {value!r}).\n"
                + "\n".join(hooks_mod.rset_reference_lines())
            )
        flag_val = on
        if is_grid_room(room):
            grid = doc.setdefault("grid", {})
            overrides = grid.setdefault("cell_overrides", {})
            cell_key = _cell_override_key(room)
            cell = overrides.setdefault(cell_key, {})
            if flag_val:
                cell[field] = True
            else:
                cell.pop(field, None)
                if not cell:
                    overrides.pop(cell_key, None)
        else:
            entry = _ensure_hand_room_entry(doc, room)
            if flag_val:
                entry[field] = True
            else:
                entry.pop(field, None)
        save_doc_validated(path, doc)
        setattr(room, field, flag_val)
        return f"Set {room.key}.{field} = {flag_val} (saved)."

    if field not in text_fields:
        raise ValueError(
            f"Unknown field {field!r}.\n"
            + "\n".join(hooks_mod.rset_reference_lines())
        )

    if field == "area_type":
        allowed = hooks_mod.map_area_types()
        if value not in allowed:
            raise ValueError(
                f"Unknown area_type {value!r} -- "
                f"must be one of {sorted(allowed)}."
            )

    clear_title = False
    clear_text = False
    if field == "title" and not value:
        clear_title = True
        value = None
    elif field == "main_homeroom" and not value:
        # Empty clears the compound hub pointer (street hub wipe).
        clear_text = True
        value = None
    elif field != "title" and not value:
        raise ValueError(f"{field} needs a value.")

    if is_grid_room(room):
        grid = doc.setdefault("grid", {})
        overrides = grid.setdefault("cell_overrides", {})
        cell_key = _cell_override_key(room)
        cell = overrides.setdefault(cell_key, {})
        if field == "title" and clear_title:
            cell.pop("title", None)
        elif clear_text:
            cell.pop(field, None)
        else:
            cell[field] = value
        if not cell:
            overrides.pop(cell_key, None)
    else:
        entry = _ensure_hand_room_entry(doc, room)
        if field == "title" and clear_title:
            entry.pop("title", None)
        elif clear_text:
            entry.pop(field, None)
        else:
            entry[field] = value

    save_doc_validated(path, doc)

    if field == "title" and clear_title:
        room.title = None
        return f"Cleared {room.key}.title (saved)."
    if clear_text:
        setattr(room, field, None)
        return f"Cleared {room.key}.{field} (saved)."
    setattr(room, field, value)
    return f"Set {room.key}.{field} = {value!r} (saved)."

def _clear_map_missing_stub(room):
    """Drop persistence recovery-stub marks so look shows real prose."""
    if room is None:
        return
    if getattr(room, "map_missing_stub", False):
        try:
            delattr(room, "map_missing_stub")
        except Exception:
            room.map_missing_stub = False


def _refresh_map_backup(path, map_id):
    """Best-effort overwrite of content/map_backups/<map_id>.json."""
    try:
        from engine import map_backups

        map_backups.refresh_backup_from_live(path, map_id, root=os.getcwd())
    except Exception:
        # Never block populate / append on backup I/O.
        pass


def append_hand_rooms(game, anchor_room, new_rooms, *, extra_exits=None):
    """Batch-create hand rooms with full lodging payloads; persist once.

    Used by ``gm populate`` so apartments/homes write ``is_house``,
    ``private_home``, ``resources``, ``seed_items``, etc. in one save --
    plain ``dig_room`` cannot stamp those fields.

    Args:
        game: live Game
        anchor_room: standing room (chooses the map/zone JSON file)
        new_rooms: list of rooms[] dicts (must include unique ``key``;
            may include exits, flags, resources, seed_items, title, …)
        extra_exits: optional ``{from_key: {direction: to_key, …}, …}``
            merged onto existing or newly written rooms[] entries
            (street/floor → porch/unit links)

    Returns:
        Short success message string.

    Raises:
        ValueError on missing keys, collisions, or grid-cell anchors.
    """
    if not new_rooms:
        raise ValueError("No rooms to append.")
    if is_grid_room(anchor_room):
        raise ValueError(
            "Cannot populate from a grid cell -- stand in a hand-authored "
            "floor or street room (or dig a hand room first)."
        )
    path, _kind, _filename, map_id = resolve_map_path(game, anchor_room)
    doc = load_doc(path)
    rooms_list = doc.setdefault("rooms", [])

    # Collision checks before mutating the doc.
    for entry in new_rooms:
        key = (entry.get("key") or "").strip()
        if not key:
            raise ValueError("Each new room needs a non-empty key.")
        if key in game.rooms:
            raise ValueError(f"Room key {key!r} already exists live.")
        if find_room_entry(doc, key) is not None:
            raise ValueError(
                f"Room key {key!r} already in {os.path.basename(path)}."
            )

    # Default zone / area_type from the anchor when the author omitted them.
    zone = getattr(anchor_room, "zone", None)
    area_type = getattr(anchor_room, "area_type", None) or "city"
    taken = _taken_vnums(game)
    for entry in new_rooms:
        entry.setdefault("area_type", area_type)
        if zone and "zone" not in entry:
            entry["zone"] = zone
        entry.setdefault("exits", {})
        _allocate_and_stamp_vnum(game, entry, taken=taken)
        rooms_list.append(entry)

    # Patch exits on existing rooms (floor / street) and any new rooms.
    for from_key, exits in (extra_exits or {}).items():
        # Prefer the rooms[] entry we just appended; else ensure the
        # live anchor (or another existing hand room) has an entry.
        target_entry = find_room_entry(doc, from_key)
        if target_entry is None:
            live_src = game.rooms.get(from_key)
            if live_src is None:
                raise ValueError(
                    f"Cannot patch exits on unknown room {from_key!r}."
                )
            target_entry = _ensure_hand_room_entry(doc, live_src)
        target_entry.setdefault("exits", {}).update(exits)

    save_doc_validated(path, doc)
    # Homes / apartments land in the protected backup slot immediately.
    _refresh_map_backup(path, map_id)

    # Live graph: create rooms, stamp fields, wire exits, place seeds.
    for entry in new_rooms:
        key = entry["key"]
        live = Room(key, entry.get("description") or f"A room ({key}).")
        _stamp_live_room_from_source(live, anchor_room, map_id)
        hooks_mod.map_store_apply_entry_fields(live, entry)
        if entry.get("vnum"):
            live.vnum = room_vnum_mod.validate_vnum(entry["vnum"])
        game.rooms[key] = live

    # Wire every exit declared on new rooms + extra_exits.
    def _all_exit_pairs():
        for entry in new_rooms:
            for direction, to_key in (entry.get("exits") or {}).items():
                yield entry["key"], direction, to_key
        for from_key, exits in (extra_exits or {}).items():
            for direction, to_key in exits.items():
                yield from_key, direction, to_key

    for from_key, direction, to_key in _all_exit_pairs():
        _live_set_exit(game, from_key, direction, to_key)

    seeds_placed = 0
    for entry in new_rooms:
        seeds_placed += hooks_mod.map_store_place_seed_items(
            game,
            entry["key"],
            entry.get("seed_items") or [],
            where="append_hand_rooms",
        )

    n = len(new_rooms)
    msg = (
        f"Populated {n} room{'s' if n != 1 else ''} into "
        f"{os.path.basename(path)} (saved)."
    )
    if seeds_placed:
        msg += f" Placed {seeds_placed} seed item(s)."
    return msg

def delete_hand_rooms(game, anchor_room, keys, *, relocate_to=None):
    """Remove hand-authored rooms from JSON + live ``game.rooms``.

    Scrubs exits in the same map/zone file that pointed at deleted keys.
    Occupants (characters / items) in doomed rooms are moved into
    ``relocate_to`` when given (else the anchor). Grid cells and rooms
    outside this map file are left alone (exits to them are only unlinked
    from surviving rooms on this file).

    Returns (removed_count, short_message).
    """
    doomed = {k for k in (keys or []) if k}
    if not doomed:
        return 0, "Nothing to delete."
    if relocate_to is None:
        relocate_to = anchor_room
    relocate_key = getattr(relocate_to, "key", None)
    if relocate_key in doomed:
        raise ValueError(
            "Cannot delete the relocate target room "
            f"{relocate_key!r}."
        )
    if getattr(anchor_room, "key", None) in doomed:
        raise ValueError(
            "Cannot delete the anchor room "
            f"{getattr(anchor_room, 'key', None)!r}."
        )

    path, _, _, _ = resolve_map_path(game, anchor_room)
    doc = load_doc(path)

    # Drop rooms[] entries.
    rooms_list = doc.get("rooms") or []
    doc["rooms"] = [
        r for r in rooms_list if r.get("key") not in doomed
    ]
    removed_json = len(rooms_list) - len(doc["rooms"])

    # Scrub exits / pocket hubs in the surviving JSON.
    for room in doc.get("rooms") or []:
        exits = room.get("exits") or {}
        for direction, dest in list(exits.items()):
            if dest in doomed:
                del exits[direction]
    for pocket in doc.get("pockets") or []:
        if pocket.get("hub_room") in doomed:
            pocket["hub_room"] = ""

    save_doc_validated(path, doc)

    # Live: move occupants, scrub exits, drop rooms.
    for key in list(doomed):
        room = game.rooms.get(key)
        if room is None:
            continue
        for obj in list(getattr(room, "contents", None) or []):
            if hasattr(obj, "move_to") and relocate_to is not None:
                try:
                    obj.move_to(relocate_to)
                    continue
                except Exception:
                    pass
            # Non-characters: re-home onto relocate when possible.
            if relocate_to is not None:
                try:
                    room.contents.remove(obj)
                except ValueError:
                    pass
                if obj not in (relocate_to.contents or []):
                    relocate_to.contents.append(obj)
                    if hasattr(obj, "location"):
                        obj.location = relocate_to

    # Scrub live exits pointing at doomed keys (all loaded rooms).
    for room in list(game.rooms.values()):
        exits = getattr(room, "exits", None) or {}
        for direction, dest in list(exits.items()):
            dest_key = getattr(dest, "key", None) if dest is not None else None
            if dest_key in doomed or dest in doomed:
                _live_clear_exit(game, room.key, direction)

    for key in doomed:
        game.rooms.pop(key, None)

    msg = (
        f"Deleted {removed_json} room(s) from "
        f"{os.path.basename(path)} (saved)."
    )
    return removed_json, msg

