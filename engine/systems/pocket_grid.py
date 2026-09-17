"""
pocket_grid.py -- engine macro+micro pocket grid (folklore peel Wave 6).

A claimed pocket is a ``macro_size × macro_size`` grid of macro cells, each
holding a fixed ``MICRO_SIZE × MICRO_SIZE`` foot grid. Rooms are ephemeral:
they live in ``game.demesne_rooms`` / ``game.demesne_macro`` caches, not in
git zone JSON.

This module owns room-key syntax, claim stamps, lazy materialize, and
compass exit wiring. Prose blurbs, hub ingress, terrain paint, and movement
dispatch stay on the game package (SUPERS facade).
"""

from __future__ import annotations

import re

from engine import hooks
from engine.world import Room

# Founding demesne is 3×3 macro cells; each macro is 10×10 micro steps.
FOUNDING_MACRO_SIZE = 3
MICRO_SIZE = 10
MACRO_CAP = 20

# Eight-way micro routing (+y north, +x east) -- same compass set as America
# overland foot grid and the live demesne facade.
_CARDINALS = ("north", "south", "east", "west")
_DIAGONALS = ("northeast", "northwest", "southeast", "southwest")
_DIR_DELTA = {
    "north": (0, 1),
    "south": (0, -1),
    "east": (1, 0),
    "west": (-1, 0),
    "northeast": (1, 1),
    "northwest": (-1, 1),
    "southeast": (1, -1),
    "southwest": (-1, -1),
}
_OPPOSITE = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
    "northeast": "southwest",
    "southwest": "northeast",
    "northwest": "southeast",
    "southeast": "northwest",
}

_KEY_RE_MICRO = re.compile(
    r"^Demesne:(\S+) \((\d+),(\d+)\)/(\d+),(\d+)$"
)


def cell_key(mx, my) -> str:
    """Meta dict key for one macro cell: ``f"{int(mx)},{int(my)}"``."""
    return f"{int(mx)},{int(my)}"


def parse_cell_coords(text):
    """Parse ``0,1`` or ``0 1`` into ``(mx, my)`` ints, or None."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if "," in raw:
        parts = raw.split(",", 1)
    else:
        parts = raw.split(None, 1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None


def micro_room_key(demesne_id, mx, my, ux, uy) -> str:
    """Stable room key for one pocket micro foot cell."""
    return f"Demesne:{demesne_id} ({mx},{my})/{ux},{uy}"


def macro_room_key(demesne_id, mx, my) -> str:
    """Stable room key for a macro stub room (enter alias target)."""
    return f"Demesne:{demesne_id} macro ({mx},{my})"


def hub_room_key(demesne_id) -> str:
    """Room key for the private hub (invite/portal mouth)."""
    return f"Demesne:{demesne_id} hub"


def parse_micro_key(key):
    """
    Parse ``Demesne:<id> (<mx>,<my>)/<ux>,<uy>`` back to parts.

    Returns ``(demesne_id, mx, my, ux, uy)`` or None.
    """
    m = _KEY_RE_MICRO.match(str(key or ""))
    if not m:
        return None
    return (
        m.group(1),
        int(m.group(2)),
        int(m.group(3)),
        int(m.group(4)),
        int(m.group(5)),
    )


def stamp_pocket_claim(
    room,
    *,
    demesne_id,
    mx,
    my,
    ux,
    uy,
    macro_size,
) -> None:
    """Claim stamp (no prose). Idempotent field assignment. No-op if room is None."""
    if room is None:
        return
    room.demesne_id = demesne_id
    room.virtual_overland = True
    room.overland_macro = (int(mx), int(my))
    room.overland_micro = (int(ux), int(uy))
    room.demesne_macro_size = int(macro_size)


def is_demesne_room(room) -> bool:
    """True when ``room`` carries a pocket ``demesne_id`` stamp."""
    return bool(getattr(room, "demesne_id", None)) if room is not None else False


def demesne_id_of(room):
    """Return the demesne_id stamped on a room, or None."""
    return None if room is None else getattr(room, "demesne_id", None)


def ensure_pocket_caches(game) -> None:
    """Idempotent: ``game.demesne_rooms``, ``demesne_macro``, ``demesne_ground`` dicts."""
    if game is None:
        return
    if not hasattr(game, "demesne_rooms"):
        game.demesne_rooms = {}
    if not hasattr(game, "demesne_macro"):
        game.demesne_macro = {}
    if not hasattr(game, "demesne_ground"):
        game.demesne_ground = {}


def neighbor_coord(mx, my, ux, uy, direction, macro_size):
    """
    Next cell after one compass step, wrapping micro into neighboring macro.

    Returns ``(nmx, nmy, nux, nuy)`` or None at the demesne frontier.
    """
    delta = _DIR_DELTA.get(direction)
    if delta is None:
        return None
    ddx, ddy = delta
    nux, nuy = int(ux) + ddx, int(uy) + ddy
    nmx, nmy = int(mx), int(my)

    if nux >= MICRO_SIZE:
        nmx += 1
        nux = 0
    elif nux < 0:
        nmx -= 1
        nux = MICRO_SIZE - 1
    if nuy >= MICRO_SIZE:
        nmy += 1
        nuy = 0
    elif nuy < 0:
        nmy -= 1
        nuy = MICRO_SIZE - 1

    size = int(macro_size)
    if not (0 <= nmx < size and 0 <= nmy < size):
        return None
    return nmx, nmy, nux, nuy


def wire_cell_exits(cache, room, mx, my, ux, uy, size) -> None:
    """Wire one micro cell to materialized neighbors (lazy grid)."""
    for direction, (ddx, ddy) in _DIR_DELTA.items():
        nux, nuy = ux + ddx, uy + ddy
        nmx, nmy = mx, my

        if nux >= MICRO_SIZE:
            nmx += 1
            nux = 0
        elif nux < 0:
            nmx -= 1
            nux = MICRO_SIZE - 1
        if nuy >= MICRO_SIZE:
            nmy += 1
            nuy = 0
        elif nuy < 0:
            nmy -= 1
            nuy = MICRO_SIZE - 1

        if not (0 <= nmx < size and 0 <= nmy < size):
            room.exits[direction] = None
            continue

        neighbor = cache.get((nmx, nmy, nux, nuy))
        if neighbor is not None:
            room.exits[direction] = neighbor
            opp = _OPPOSITE[direction]
            if neighbor.exits.get(opp) in (None, neighbor):
                neighbor.exits[opp] = room
        else:
            room.exits[direction] = room


def wire_micro_exits(rooms_for_demesne, size) -> None:
    """
    Link cardinal exits within and between macro cells on materialized rooms.

    Rooms at the demesne frontier get ``None`` exits (sealed edge).
    """
    for (mx, my, ux, uy), room in rooms_for_demesne.items():
        for direction, (ddx, ddy) in _DIR_DELTA.items():
            nux, nuy = ux + ddx, uy + ddy
            nmx, nmy = mx, my

            if nux >= MICRO_SIZE:
                nmx += 1
                nux = 0
            elif nux < 0:
                nmx -= 1
                nux = MICRO_SIZE - 1
            if nuy >= MICRO_SIZE:
                nmy += 1
                nuy = 0
            elif nuy < 0:
                nmy -= 1
                nuy = MICRO_SIZE - 1

            if not (0 <= nmx < size and 0 <= nmy < size):
                room.exits[direction] = None
                continue

            neighbor = rooms_for_demesne.get((nmx, nmy, nux, nuy))
            room.exits[direction] = neighbor


def _default_pocket_micro_room(game, pocket, mx, my, ux, uy):
    """Blank stamped foot cell when no game builder is registered."""
    demesne_id = pocket["demesne_id"]
    macro_size = int(pocket.get("macro_size") or FOUNDING_MACRO_SIZE)
    room = Room(micro_room_key(demesne_id, mx, my, ux, uy), "Open ground.")
    room.game = game
    stamp_pocket_claim(
        room,
        demesne_id=demesne_id,
        mx=mx,
        my=my,
        ux=ux,
        uy=uy,
        macro_size=macro_size,
    )
    for direction in _DIR_DELTA:
        room.exits[direction] = room
    return room


def get_or_create_pocket_micro(game, pocket, mx, my, ux, uy, *, build_room=None):
    """
    Lazy materialize one foot cell in a claimed pocket grid.

    ``pocket`` is a dict with ``demesne_id`` and optional ``macro_size``
    (default ``FOUNDING_MACRO_SIZE``). Returns None when coords are out of
    bounds. Does not hydrate floor loot -- the game facade handles that.
    """
    if pocket is None or game is None:
        return None
    ensure_pocket_caches(game)
    demesne_id = pocket["demesne_id"]
    size = int(pocket.get("macro_size") or FOUNDING_MACRO_SIZE)
    mx, my, ux, uy = int(mx), int(my), int(ux), int(uy)
    if not (0 <= mx < size and 0 <= my < size):
        return None
    if not (0 <= ux < MICRO_SIZE and 0 <= uy < MICRO_SIZE):
        return None

    cache = game.demesne_rooms.setdefault(demesne_id, {})
    coord = (mx, my, ux, uy)
    room = cache.get(coord)
    if room is not None:
        return room

    if build_room is not None:
        room = build_room(game, pocket, mx, my, ux, uy)
    else:
        room = hooks.build_pocket_micro_room(game, pocket, mx, my, ux, uy)
        if room is None:
            room = _default_pocket_micro_room(game, pocket, mx, my, ux, uy)

    cache[coord] = room
    center = MICRO_SIZE // 2
    if ux == center and uy == center:
        game.demesne_macro.setdefault(demesne_id, {})[(mx, my)] = room

    wire_cell_exits(cache, room, mx, my, ux, uy, size)

    try:
        hooks.on_virtual_room_created(game, room)
    except Exception:
        pass

    return room


def expand_grid(game, pocket, new_size):
    """
    Grow ``macro_size`` and rewire materialized cells.

    Returns ``(ok, old_size, new_size)``. Does not format player prose.
    """
    if pocket is None or game is None:
        return False, FOUNDING_MACRO_SIZE, FOUNDING_MACRO_SIZE
    new_size = min(int(new_size), MACRO_CAP)
    old_size = int(pocket.get("macro_size") or FOUNDING_MACRO_SIZE)
    if new_size <= old_size:
        return False, old_size, old_size

    demesne_id = pocket["demesne_id"]
    ensure_pocket_caches(game)
    cache = game.demesne_rooms.setdefault(demesne_id, {})
    pocket["macro_size"] = new_size
    wire_micro_exits(cache, new_size)
    return True, old_size, new_size


def shrink_frontier(game, pocket, new_size):
    """
    Drop cached cells with ``mx >= new_size`` or ``my >= new_size``.

    Clamps ``new_size`` to at least ``FOUNDING_MACRO_SIZE``. The game facade
    must stash floor loot before calling this. Returns
    ``(ok, old_size, new_size, removed_count)``.
    """
    if pocket is None or game is None:
        return False, FOUNDING_MACRO_SIZE, FOUNDING_MACRO_SIZE, 0
    new_size = max(int(new_size), FOUNDING_MACRO_SIZE)
    old_size = int(pocket.get("macro_size") or FOUNDING_MACRO_SIZE)
    if new_size >= old_size:
        return False, old_size, new_size, 0

    demesne_id = pocket["demesne_id"]
    ensure_pocket_caches(game)
    cache = game.demesne_rooms.get(demesne_id) or {}
    macro_cache = game.demesne_macro.get(demesne_id) or {}

    removed = 0
    to_remove = []
    for (mx, my, ux, uy), _room in list(cache.items()):
        if mx < new_size and my < new_size:
            continue
        to_remove.append((mx, my, ux, uy))
        removed += 1

    for coord in to_remove:
        cache.pop(coord, None)
        mx, my = coord[0], coord[1]
        macro_cache.pop((mx, my), None)

    pocket["macro_size"] = new_size
    wire_micro_exits(cache, new_size)
    return True, old_size, new_size, removed
