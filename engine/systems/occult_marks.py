"""
occult_marks.py -- folklore occult room stamps (devil's trap, salt, iron, holy water).

Authored booleans live on room objects via ``room.engine`` kind fields.
This module owns generic query + stamp helpers; temporary circles, magic
saltlines, and demon leave-blocks stay on game hooks so ``engine/`` never
imports a game package.
"""

from __future__ import annotations

from engine import hooks

_MARK_DEVILS_TRAP = "devils_trap"
_MARK_SALT_LINE = "salt_line"
_MARK_IRON_WARD = "iron_ward"
_MARK_HOLY_WATER = "holy_water_ward"


def ensure_occult_defaults(room) -> None:
    """Fill occult stamp attrs when never stamped. Idempotent."""
    if room is None:
        return
    for attr in (_MARK_DEVILS_TRAP, _MARK_SALT_LINE, _MARK_IRON_WARD, _MARK_HOLY_WATER):
        if not hasattr(room, attr):
            setattr(room, attr, False)


def room_is_devils_trap(room, game_time_ticks=0) -> bool:
    """True for a permanent authored trap or a hooked temporary circle."""
    if room is None:
        return False
    ensure_occult_defaults(room)
    if getattr(room, _MARK_DEVILS_TRAP, False):
        return True
    return hooks.temporary_devils_trap(room, game_time_ticks)


def stamp_devils_trap(room) -> None:
    """Mark the room with a permanent devil's trap. Idempotent."""
    if room is None:
        return
    ensure_occult_defaults(room)
    room.devils_trap = True


def clear_devils_trap(room) -> None:
    """Remove the permanent authored trap flag.

    Does not clear game Hellcraft ``hell_circle_until`` timers -- those
    expire on their own via ``set_temporary_devils_trap``.
    """
    if room is None:
        return
    ensure_occult_defaults(room)
    room.devils_trap = False


def room_has_salt_line(room, game_time_ticks=0) -> bool:
    """True when the room has an authored salt line or hooked temporary salt."""
    if room is None:
        return False
    ensure_occult_defaults(room)
    if getattr(room, _MARK_SALT_LINE, False):
        return True
    return hooks.temporary_salt_line(room, game_time_ticks)


def stamp_salt_line(room) -> None:
    """Mark the room with a permanent salt line. Idempotent."""
    if room is None:
        return
    ensure_occult_defaults(room)
    room.salt_line = True


def clear_salt_line(room) -> None:
    """Remove the permanent authored salt line flag."""
    if room is None:
        return
    ensure_occult_defaults(room)
    room.salt_line = False


def room_has_iron_ward(room) -> bool:
    """True when an iron ward stamp is active on the room."""
    if room is None:
        return False
    ensure_occult_defaults(room)
    return bool(getattr(room, _MARK_IRON_WARD, False))


def stamp_iron_ward(room) -> None:
    """Mark the room with an iron ward. Idempotent."""
    if room is None:
        return
    ensure_occult_defaults(room)
    room.iron_ward = True


def clear_iron_ward(room) -> None:
    """Remove the permanent iron ward flag."""
    if room is None:
        return
    ensure_occult_defaults(room)
    room.iron_ward = False


def room_has_holy_water_ward(room) -> bool:
    """True when a holy-water blessing stamp is active on the room."""
    if room is None:
        return False
    ensure_occult_defaults(room)
    return bool(getattr(room, _MARK_HOLY_WATER, False))


def stamp_holy_water_ward(room) -> None:
    """Mark the room with a holy-water ward (folklore reagent site). Idempotent."""
    if room is None:
        return
    ensure_occult_defaults(room)
    room.holy_water_ward = True


def clear_holy_water_ward(room) -> None:
    """Remove the holy-water ward stamp."""
    if room is None:
        return
    ensure_occult_defaults(room)
    room.holy_water_ward = False


def room_marks_for_hud(room, game_time_ticks=0) -> list[str]:
    """Player-safe mark ids for Zone HUD (folklore names, no show proper nouns)."""
    if room is None:
        return []
    marks = []
    if room_is_devils_trap(room, game_time_ticks):
        marks.append(_MARK_DEVILS_TRAP)
    if room_has_salt_line(room, game_time_ticks):
        marks.append(_MARK_SALT_LINE)
    if room_has_iron_ward(room):
        marks.append(_MARK_IRON_WARD)
    if room_has_holy_water_ward(room):
        marks.append(_MARK_HOLY_WATER)
    return marks


def occult_mark_blocks(character, room) -> str | None:
    """Ask the game whether occult marks block this character from leaving.

    Returns a player-facing refusal string, or None when movement may proceed.
    """
    return hooks.occult_mark_blocks(character, room)
