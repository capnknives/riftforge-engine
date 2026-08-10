"""
cmdset_darkness.py -- basegame proof for engine/cmdset stacked overrides.

Rooms tagged ``engine_dark_demo`` on the room object activate a cmdset that
replaces ``look`` / ``l`` with a can't-see line. Normal rooms keep the
default ``cmd_look`` from ``engine/verbs/basic.py``.
"""

from __future__ import annotations


def _dark_room_matcher(character, room) -> bool:
    """True when the room opts into the darkness demo cmdset."""
    return bool(getattr(room, "engine_dark_demo", False))


def cmd_look_dark(character, args, game):
    """Replacement look in pitch-black demo rooms."""
    character.session.send(
        "It is too dark to see anything. (basegame darkness cmdset demo)"
    )


def register_darkness_cmdset() -> None:
    """Register the demo cmdset once at basegame bootstrap."""
    from engine import cmdset

    cmdset.register_cmdset(
        "basegame_darkness",
        {
            "look": (cmd_look_dark, "Look around (darkness demo -- can't see)."),
            "l": (cmd_look_dark, "Look around (darkness demo -- can't see)."),
        },
        priority=10,
        matcher=_dark_room_matcher,
    )
