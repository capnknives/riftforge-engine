"""
network -- configuration for a rotating gate / rift mouth set.

A ``GateNetwork`` names the Game attributes that hold the open-set,
round-robin index, and next-rotate tick. Room discovery stays with the
caller (predicate over ``game.rooms``, or a curated key list).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GateNetwork:
    """Immutable config for one rotating mouth network on a live Game.

    Attributes:
        id: Short stable id (``devils``, ``purgatory_rift``).
        open_attr: Game attribute holding a ``set`` of open room keys.
        index_attr: Game attribute holding the round-robin start index.
        rotate_at_attr: Game attribute holding the next rotate tick.
        open_count: How many mouths stay open at once.
        rotate_ticks: Ticks between rotations when there are enough rooms.
    """

    id: str
    open_attr: str
    index_attr: str
    rotate_at_attr: str
    open_count: int
    rotate_ticks: int
