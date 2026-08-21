"""
doors.py -- per-exit door detection and persisted lock state.

Door *existence* is derived from room topology at boot (retroactive heal
does not rewrite exits). Lock state lives in game meta because rooms
rebuild from JSON every boot.

stdlib only; zero ``supers`` imports.
"""

from __future__ import annotations

META_KEY = "door_lock_state"
LOOK_DOOR_CLOSED_SUFFIX = " (door closed)"

_CARDINAL_DIRS = frozenset({
    "north", "south", "east", "west",
    "northeast", "northwest", "southeast", "southwest",
    "up", "down", "in", "out",
})


def _pair_key(room_key, direction):
    """Stable id for one directed exit (from_room, direction)."""
    return f"{room_key}|{str(direction or '').strip().lower()}"


def normalize_loaded(blob):
    """Sanitize persisted lock map into ``{room_key|dir: bool}``."""
    out = {}
    if not isinstance(blob, dict):
        return out
    for raw_key, locked in blob.items():
        if not isinstance(raw_key, str) or "|" not in raw_key:
            continue
        room_key, direction = raw_key.rsplit("|", 1)
        direction = direction.strip().lower()
        if not room_key or direction not in _CARDINAL_DIRS:
            continue
        out[_pair_key(room_key, direction)] = bool(locked)
    return out


def export_meta(game):
    """Return the persistable door-lock blob."""
    state = getattr(game, "door_lock_state", None)
    return normalize_loaded(state)


def load_into_game(game, blob):
    """Hydrate ``game.door_lock_state`` from SQLite meta."""
    game.door_lock_state = normalize_loaded(blob)


def ensure_game_state(game):
    """Idempotent init before readers run."""
    if not isinstance(getattr(game, "door_lock_state", None), dict):
        game.door_lock_state = {}
    return game.door_lock_state


def direction_between(from_room, dest):
    """Return the exit label from ``from_room`` to ``dest``, or None."""
    if from_room is None or dest is None:
        return None
    dest_key = getattr(dest, "key", None)
    for direction, neighbor in (from_room.exits or {}).items():
        if neighbor is dest:
            return direction
        if dest_key and getattr(neighbor, "key", None) == dest_key:
            return direction
    return None


def _authored_door_override(room, direction):
    """Return True/False when JSON forces door on/off; None = use heuristics."""
    if room is None:
        return None
    d = str(direction or "").strip().lower()
    doors = getattr(room, "doors", None)
    if isinstance(doors, dict):
        for key, val in doors.items():
            if str(key).strip().lower() == d:
                return bool(val)
    if isinstance(doors, (list, tuple, set)):
        lowered = {str(x).strip().lower() for x in doors}
        if d in lowered:
            return True
    no_door = getattr(room, "no_door", None)
    if isinstance(no_door, (list, tuple, set)):
        if d in {str(x).strip().lower() for x in no_door}:
            return False
    return None


def _same_home_compound(a, b):
    """True when two rooms belong to one house / homestead compound."""
    if a is None or b is None:
        return False
    if a is b:
        return True
    if getattr(a, "key", None) and a.key == getattr(b, "key", None):
        return True
    from engine.systems.lodging import main_homeroom_key

    ma = main_homeroom_key(a)
    mb = main_homeroom_key(b)
    if ma and ma == mb:
        return True
    pa = getattr(a, "homestead_plot_id", None)
    pb = getattr(b, "homestead_plot_id", None)
    return bool(pa) and pa == pb


def exit_is_door(from_room, direction, dest):
    """True when this step crosses a closable door (not zone travel)."""
    if from_room is None or dest is None:
        return False
    d = str(direction or "").strip().lower()
    if d not in _CARDINAL_DIRS:
        return False
    override = _authored_door_override(from_room, d)
    if override is not None:
        return override
    from_out = bool(getattr(from_room, "outdoor", False))
    to_out = bool(getattr(dest, "outdoor", False))
    # Any outdoor <-> indoor threshold is a structure door (shops, clinics, …).
    if from_out != to_out:
        return True
    # Interior house chambers share a compound -- bedroom/bath/kitchen doors.
    if (
        getattr(from_room, "is_house", False)
        and getattr(dest, "is_house", False)
        and _same_home_compound(from_room, dest)
    ):
        return True
    # Hotel / apartment guest units use private_home on the unit itself.
    if getattr(dest, "private_home", False) or getattr(from_room, "private_home", False):
        return True
    return False


def is_exit_locked(game, from_room, direction, dest):
    """True when a persisted interior/structure door lock is set.

    Private-home thresholds use ``room.unlocked`` via ``supers.lodging`` --
    not this helper.
    """
    if game is None or from_room is None or dest is None:
        return False
    if not exit_is_door(from_room, direction, dest):
        return False
    state = ensure_game_state(game)
    key = _pair_key(getattr(from_room, "key", None), direction)
    if key and state.get(key, False):
        return True
    from engine.map_store import opposite_direction

    rev = opposite_direction(direction)
    if rev:
        rev_key = _pair_key(getattr(dest, "key", None), rev)
        if rev_key and state.get(rev_key, False):
            return True
    return False


def set_exit_locked(game, from_room, direction, locked):
    """Persist lock state for one directed exit. Returns True when written."""
    if game is None or from_room is None:
        return False
    room_key = getattr(from_room, "key", None)
    if not room_key:
        return False
    d = str(direction or "").strip().lower()
    if d not in _CARDINAL_DIRS:
        return False
    dest = (from_room.exits or {}).get(d)
    if dest is None:
        dest = (from_room.exits or {}).get(direction)
    if dest is None:
        return False
    if not exit_is_door(from_room, d, dest):
        return False
    state = ensure_game_state(game)
    key = _pair_key(room_key, d)
    want = bool(locked)
    if state.get(key, False) == want:
        return False
    if want:
        state[key] = True
    else:
        state.pop(key, None)
    game._door_lock_dirty = True
    return True


def heal_orphan_lock_state(game):
    """Drop lock rows for missing rooms or dead exits (boot heal)."""
    if game is None:
        return 0
    state = ensure_game_state(game)
    if not state:
        return 0
    rooms = getattr(game, "rooms", None) or {}
    removed = 0
    for key in list(state.keys()):
        if "|" not in key:
            state.pop(key, None)
            removed += 1
            continue
        room_key, direction = key.rsplit("|", 1)
        room = rooms.get(room_key)
        if room is None:
            state.pop(key, None)
            removed += 1
            continue
        dest = (room.exits or {}).get(direction)
        if dest is None or not exit_is_door(room, direction, dest):
            state.pop(key, None)
            removed += 1
    if removed:
        game._door_lock_dirty = True
    return removed


def count_doors(game):
    """Return how many closable exits exist (audit / boot log)."""
    if game is None:
        return 0
    rooms = getattr(game, "rooms", None) or {}
    total = 0
    for room in rooms.values():
        for direction, dest in (room.exits or {}).items():
            if dest is not None and exit_is_door(room, direction, dest):
                total += 1
    return total
