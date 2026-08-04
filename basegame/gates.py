"""
gates -- elemental Rift Nexus network on authored ``rift_gate`` rooms.

Thin basegame wrapper over ``engine.systems.gates`` (no supers import).
Constants mirror the Devil's Gate demo shape so rotation timing stays
comparable across games.
"""

from __future__ import annotations

from engine.systems.gates import (
    GateNetwork,
    ensure_initialized as _ensure_initialized,
    exit_visible_for_flag,
    tick_rotation as _tick_rotation,
    visible_exits as _visible_exits,
)

OPEN_GATE_COUNT = 2
GATE_ROTATE_TICKS = 40

FLAG_ATTR = "rift_gate"

NEXUS = GateNetwork(
    id="nexus",
    open_attr="open_rift_gates",
    index_attr="_rift_gate_index",
    rotate_at_attr="_rift_gate_rotate_at",
    open_count=OPEN_GATE_COUNT,
    rotate_ticks=GATE_ROTATE_TICKS,
)


def all_gate_rooms(game):
    """Every Room with authored ``rift_gate`` True (stable key order)."""
    rooms = getattr(game, "rooms", None) or {}
    return sorted(
        (
            room for room in rooms.values()
            if getattr(room, FLAG_ATTR, False)
        ),
        key=lambda r: r.key,
    )


def ensure_initialized(game):
    """First boot: open the first ``open_count`` mouths if none open yet."""
    _ensure_initialized(game, NEXUS, all_gate_rooms(game))


def tick_rotation(game):
    """Rotate which rift mouths are open (tick pipeline)."""
    _tick_rotation(game, NEXUS, all_gate_rooms(game))


def exit_visible(dest, game):
    """False when ``dest`` is a closed rift gate (hidden from look/move)."""
    return exit_visible_for_flag(dest, game, NEXUS, FLAG_ATTR)


def visible_exits(room, game):
    """``(direction, dest)`` pairs omitting closed rift gate destinations."""
    return _visible_exits(room, game, NEXUS, FLAG_ATTR)
