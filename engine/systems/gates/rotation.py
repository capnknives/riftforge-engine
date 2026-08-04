"""
rotation -- shared open-set init and round-robin tick for gate networks.

Simple round-robin, fixed open-count rotation. A network needing weighted
room selection, a chance to stay fully closed, or other bespoke policy
should reuse ``GateNetwork`` and ``open_keys`` below and write its own
driver instead of forcing that policy through ``tick_rotation`` -- see the
package docstring.
"""

from __future__ import annotations

from engine.systems.gates.network import GateNetwork


def open_keys(game, network: GateNetwork):
    """Return (and lazily create) the open room-key set on ``game``."""
    keys = getattr(game, network.open_attr, None)
    if keys is None:
        keys = set()
        setattr(game, network.open_attr, keys)
        return keys
    return keys


def is_open(room, game, network: GateNetwork, *, flag_attr=None):
    """True when ``room`` is in the network open-set.

    If ``flag_attr`` is set (e.g. ``devils_gate``), the room must also
    carry that boolean flag so stray open keys cannot open non-gates.
    """
    if room is None:
        return False
    if flag_attr is not None and not getattr(room, flag_attr, False):
        return False
    return room.key in open_keys(game, network)


def ensure_initialized(game, network: GateNetwork, rooms):
    """First boot: open the first ``open_count`` rooms if none are open.

    ``rooms`` is an ordered list of Room objects (stable authoring order
    or sorted keys -- callers choose).
    """
    keys = open_keys(game, network)
    if keys:
        return
    if not rooms:
        return
    for room in rooms[: network.open_count]:
        keys.add(room.key)
    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    setattr(game, network.rotate_at_attr, ticks + network.rotate_ticks)


def tick_rotation(game, network: GateNetwork, rooms):
    """Round-robin which mouths are open (classic Devil's Gate behavior).

    When there are not enough rooms to rotate, keep every candidate open.
    """
    ensure_initialized(game, network, rooms)
    if len(rooms) <= network.open_count:
        keys = open_keys(game, network)
        keys.clear()
        for room in rooms:
            keys.add(room.key)
        return

    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    rotate_at = int(getattr(game, network.rotate_at_attr, 0) or 0)
    if ticks < rotate_at:
        return

    idx = int(getattr(game, network.index_attr, 0) or 0)
    idx = (idx + 1) % len(rooms)
    setattr(game, network.index_attr, idx)
    keys = open_keys(game, network)
    keys.clear()
    for offset in range(network.open_count):
        keys.add(rooms[(idx + offset) % len(rooms)].key)
    setattr(game, network.rotate_at_attr, ticks + network.rotate_ticks)
