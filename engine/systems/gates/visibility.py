"""
visibility -- look/move hide for closed flag-based gate destinations.

A closed gate is invisible: look omits the exit, and move into it says
"You can't go that way." Consumed via ``engine.hooks.set_look_exit_visible``
-- ``engine/verbs/basic.py`` already checks that hook for both look and
move, so a game only needs to register its network's ``exit_visible_for_flag``
(or a small wrapper over it) once at boot.
"""

from __future__ import annotations

from engine.systems.gates.network import GateNetwork
from engine.systems.gates.rotation import is_open


def exit_visible_for_flag(dest, game, network: GateNetwork, flag_attr: str):
    """False when ``dest`` is a closed gate of this network.

    Non-gate rooms (missing ``flag_attr``) always stay visible.
    """
    if dest is None:
        return True
    if not getattr(dest, flag_attr, False):
        return True
    return is_open(dest, game, network, flag_attr=flag_attr)


def visible_exits(room, game, network: GateNetwork, flag_attr: str):
    """``(direction, dest)`` pairs omitting closed gate destinations."""
    if room is None:
        return []
    return [
        (direction, dest)
        for direction, dest in room.exits.items()
        if exit_visible_for_flag(dest, game, network, flag_attr)
    ]
