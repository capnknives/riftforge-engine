"""
mine_rooms.py -- ephemeral MineRoom virtual rooms wired to graph nodes.

Same spirit as overland VirtualRoom: never written to map JSON / SQLite
rooms table. ``mine_room_key(mouth_key, room_id)`` round-trips through
persistence via the character's saved room key.
"""

from __future__ import annotations

import random

from engine.systems import mine_graph as graph_mod

MINE_ROOM_PREFIX = "Mine:"

# Generic engine-side room description pools -- game-agnostic mine flavor
# (timber, ore carts, drip water, lamp soot), no SUPERS-specific lore. Picked
# once per room on first creation and cached on the Room object, so a given
# room reads consistently for every visitor after that.
_GENERIC_DRIFT_DESCRIPTIONS = (
    "Timber sets brace the tunnel at intervals, dark with age and drip water; "
    "a lamp's worth of soot has streaked one wall black.",
    "The passage narrows here, rough-cut stone still showing the arc of an "
    "old drill pattern; water beads and falls somewhere out of sight.",
    "Loose gravel and broken slate litter the floor beneath a run of sagging "
    "timber sets; the air smells of wet stone and old smoke.",
    "A stretch of bare rock face gives way to hand-set timber shoring, the "
    "wood soft with age; somewhere close, water drips in a slow, steady rhythm.",
)

_OFFICE_ROOM_DESCRIPTIONS = (
    "A collapsed desk and a rusted filing cabinet crowd this small chamber "
    "-- someone ran a mining operation's paperwork out of this room, once.",
    "Splintered shelving lines one wall, a few brittle ledger pages still "
    "clinging to the boards; this was the company's business, moved below ground.",
    "A stove-in stool sits before a desk buried under decades of rock dust; "
    "whoever worked this office left in a hurry, or never came back.",
)

_CAGE_ROOM_DESCRIPTIONS = (
    "A rusted hoist frame dominates this chamber, its cage lift long since "
    "stalled at this landing; cable hangs slack from the drum overhead.",
    "This is a cage landing -- iron rails run to a lift shaft, and the winch "
    "gears above have locked solid with age.",
    "Chain and pulley hang motionless over an open lift well; the cage "
    "itself rests here, its floor plate gone soft with rust.",
)

_SHAFT_ROOM_DESCRIPTIONS = (
    "A vertical shaft drops away into darkness here, its walls ringed with "
    "old ladder rungs bolted into the stone.",
    "The passage opens onto a shaft mouth, a rusted ladder climbing one "
    "wall toward a distant patch of daylight far above.",
    "Timber cribbing frames a shaft that plunges straight down, the air "
    "moving faster here than anywhere else in the workings.",
)

_ROOM_KIND_DESCRIPTIONS = {
    "office": _OFFICE_ROOM_DESCRIPTIONS,
    "cage": _CAGE_ROOM_DESCRIPTIONS,
    "shaft": _SHAFT_ROOM_DESCRIPTIONS,
}


def _pick_room_description(room_kind):
    """Random variant from the pool matching room_kind, or the generic pool."""
    pool = _ROOM_KIND_DESCRIPTIONS.get(room_kind) or _GENERIC_DRIFT_DESCRIPTIONS
    return random.choice(pool)


def mine_room_key(mouth_key, room_id):
    """Canonical virtual mine room key."""
    return f"{MINE_ROOM_PREFIX}{mouth_key}:{room_id}"


def parse_mine_room_key(key):
    """Parse key -> (mouth_key, room_id) or None."""
    if not key or not str(key).startswith(MINE_ROOM_PREFIX):
        return None
    rest = str(key)[len(MINE_ROOM_PREFIX):]
    if ":" not in rest:
        return None
    # mouth_key is mine:map:mx:my:ux:uy -- six colon parts; room_id follows
    parts = rest.split(":")
    if len(parts) < 7:
        return None
    mouth_key = ":".join(parts[:6])
    room_id = parts[6]
    if not graph_mod.parse_mouth_key(mouth_key):
        return None
    return mouth_key, room_id


def _mine_room_store(game):
    """Return ``game.mine_rooms``, creating the dict on first use."""
    store = getattr(game, "mine_rooms", None)
    if store is None:
        store = {}
        game.mine_rooms = store
    return store


def _new_mine_room(game, mouth_key, room_id, node):
    """Build one ephemeral mine Room and register it before wiring exits.

    Registration happens here, not after walking dests, so a cyclic graph
    (office down to cage, cage up to office) cannot RecursionError while
    ``get_mine_room`` is still creating the first node.
    """
    from world import Room

    store = _mine_room_store(game)
    key = mine_room_key(mouth_key, room_id)
    depth = int(node.get("depth") or 1)
    title = f"Mine drift (depth {depth})"
    desc = _pick_room_description(node.get("room_kind"))

    # Persist key is Mine:<mouth>:<room_id> so copyover can rematerialize.
    # Look still uses the human title (never leak the storage key).
    room = Room(key, desc)
    room.title = title
    room.game = game
    room.virtual_mine = True
    room.mouth_key = mouth_key
    room.mine_room_id = room_id
    room.mine_depth = depth
    room.dark = True
    room.outdoor = False
    room.wilderness = False
    # Mouths on the 1861 atlas stay on that plane -- Prime Earth is a
    # typed return, not a copyover default (mine rooms used to stamp
    # earth always, so stub-heal / Cadence treated a drift as home).
    room.plane = graph_mod.plane_for_mouth_key(mouth_key)
    room.realm = "prime"

    for fix in node.get("fixtures") or []:
        if fix.get("provides_light"):
            room.dark = False
            room.mine_has_light = True
            break

    store[key] = room
    return room


def _wire_mine_room_exits(game, mouth, mouth_key, room, room_id, node):
    """Attach carved dest Rooms now that the connected component is in the store."""
    store = _mine_room_store(game)
    for direction, face in (node.get("exits") or {}).items():
        if not face.get("carved"):
            continue
        dest_id = face.get("dest_room_id")
        if dest_id:
            dest_room = store.get(mine_room_key(mouth_key, dest_id))
            if dest_room:
                room.exits[direction] = dest_room
        elif direction == "up" and room_id == mouth.get("shaft_room_id"):
            # Up from shaft returns to wilderness mouth (handled by move hook).
            room.exits["up"] = room  # placeholder; try_mine_move wins


def get_mine_room(game, mouth_key, room_id):
    """Return (create if needed) ephemeral Room for a graph node.

    Mine graphs are cyclic by design -- company layouts link office down to
    cage and cage up to office, and player-carved shafts are bidirectional.
    This walks the connected component on a stack and registers each Room
    *before* wiring exits. The old recursive "create, recurse dest, then
    store" path RecursionError'd on boot when a saved body sat in a looping
    drift (live 2026-09-16 boot hold).
    """
    store = _mine_room_store(game)
    key = mine_room_key(mouth_key, room_id)
    existing = store.get(key)
    if existing is not None:
        return existing

    mouth = graph_mod.get_mouth(game, mouth_key)
    node = graph_mod.room_by_id(mouth, room_id) if mouth else None
    if node is None:
        return None

    pending = [room_id]
    created = []
    while pending:
        rid = pending.pop()
        rkey = mine_room_key(mouth_key, rid)
        if store.get(rkey) is not None:
            continue
        n = graph_mod.room_by_id(mouth, rid)
        if n is None:
            continue
        room = _new_mine_room(game, mouth_key, rid, n)
        created.append((room, rid, n))
        for direction, face in (n.get("exits") or {}).items():
            if not face.get("carved"):
                continue
            dest_id = face.get("dest_room_id")
            if dest_id and store.get(mine_room_key(mouth_key, dest_id)) is None:
                pending.append(dest_id)

    for room, rid, n in created:
        _wire_mine_room_exits(game, mouth, mouth_key, room, rid, n)

    return store.get(key)


def resolve_mine_saved_room_key(game, room_key):
    """Materialize a mine virtual room from a saved key."""
    parsed = parse_mine_room_key(room_key)
    if parsed is None:
        return None
    mouth_key, room_id = parsed
    graph_mod.get_mouth(game, mouth_key)  # ensure loaded
    return get_mine_room(game, mouth_key, room_id)


def wilderness_mouth_room(game, mouth_key):
    """Return the wilderness surface room for a mouth key."""
    parsed = graph_mod.parse_mouth_key(mouth_key)
    if parsed is None:
        return None
    _map_id, mx, my, ux, uy = parsed
    from engine.systems.overland import get_virtual_room

    plane = graph_mod.plane_for_mouth_key(mouth_key)
    return get_virtual_room(game, (mx, my), (ux, uy), plane=plane)
