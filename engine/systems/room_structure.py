"""
room_structure.py -- generic per-room-prop structural HP + wrecked state.

Any room can author entries (a wall, a floor tile, a fixture -- this
module has no opinion on what a "target" represents) that track their own
HP and a wrecked flag, independent of the room's own ephemeral object
(rooms rebuild from JSON every boot -- this state lives on ``game``
instead, round-tripped through a keyed meta blob the same shape any other
small persisted flag uses).

State shape: ``{room_key: {target_id: {"hp": int, "wrecked": bool}}}``. A
``(room_key, target_id)`` pair with no entry yet is untouched -- treated
as full HP / not wrecked by every reader here.

Peeled from ``supers/room_structure.py`` under
docs/plans/riftforge_core_expansion.md Phase 5c. That module's combat
integration -- *when* a swing chips a prop's HP, tied to SUPERS'
Structured Battle Brief shape and its own `slam`/`throw` verb hint
mechanism (`stamp_wall_slam_pick`, `apply_wall_slam`,
`breach_defender_through_wall`) -- has no generic core to extract, same
finding as Phase 5's `build_brief`: it reads `brief["reaction"]`,
`brief["slam_targets"]`, `combat_lexicon` prose, and SUPERS-tuned damage
constants throughout. That combat-decision layer stays exactly where it
is; this module only owns the state.

stdlib only.
"""

from __future__ import annotations

META_KEY = "wall_structure_state"


def normalize_loaded(blob):
    """Sanitize a loaded meta blob into the ``{room: {target: {...}}}`` shape."""
    out = {}
    if not isinstance(blob, dict):
        return out
    for room_key, targets in blob.items():
        if not isinstance(targets, dict):
            continue
        clean_targets = {}
        for target_id, state in targets.items():
            if not isinstance(state, dict):
                continue
            hp = state.get("hp")
            if not isinstance(hp, int) or isinstance(hp, bool) or hp < 0:
                continue
            clean_targets[str(target_id)] = {
                "hp": hp,
                "wrecked": bool(state.get("wrecked", False)),
            }
        if clean_targets:
            out[str(room_key)] = clean_targets
    return out


def export_meta(game):
    """Return the persistable blob for ``save_meta_json``."""
    state = getattr(game, "wall_structure_state", None)
    return normalize_loaded(state)


def load_into_game(game, blob):
    """Hydrate ``game.wall_structure_state`` from a loaded meta blob."""
    game.wall_structure_state = normalize_loaded(blob)


def ensure_game_state(game):
    """Idempotent init for code paths that run before a full meta load."""
    if not isinstance(getattr(game, "wall_structure_state", None), dict):
        game.wall_structure_state = {}
    return game.wall_structure_state


def get_wall_state(game, room, target_id, *, default_hp_max):
    """Return ``{"hp": int, "wrecked": bool}`` for one target.

    Never stamped yet -- and not wrecked -- returns full ``default_hp_max``
    HP (the caller's own derived/authored max), not a mutated record.
    """
    room_key = getattr(room, "key", None) or room
    state = ensure_game_state(game)
    targets = state.get(room_key) or {}
    existing = targets.get(target_id)
    if existing is None:
        return {"hp": int(default_hp_max), "wrecked": False}
    return dict(existing)


def is_wrecked(game, room, target_id):
    """Shortcut for the common wrecked-state-only check."""
    room_key = getattr(room, "key", None) or room
    state = ensure_game_state(game)
    targets = state.get(room_key) or {}
    existing = targets.get(target_id)
    return bool(existing and existing.get("wrecked"))


def set_wall_state(game, room, target_id, *, hp, wrecked=None):
    """Write structural HP (and optionally wrecked) for one target.

    ``wrecked`` defaults to ``hp <= 0`` when not given explicitly.
    """
    room_key = getattr(room, "key", None) or room
    if room_key is None:
        raise ValueError("set_wall_state needs a room or room key")
    state = ensure_game_state(game)
    targets = state.setdefault(room_key, {})
    hp = max(0, int(hp))
    if wrecked is None:
        wrecked = hp <= 0
    targets[target_id] = {"hp": hp, "wrecked": bool(wrecked)}
    return targets[target_id]


def repair_wall(game, room, target_id, *, hp_max):
    """Restore one target to full HP, clearing wrecked. Idempotent."""
    return set_wall_state(game, room, target_id, hp=int(hp_max), wrecked=False)


def clear_room(game, room):
    """Drop all structural state for a room (e.g. a rebuilt/removed zone)."""
    room_key = getattr(room, "key", None) or room
    state = ensure_game_state(game)
    state.pop(room_key, None)
