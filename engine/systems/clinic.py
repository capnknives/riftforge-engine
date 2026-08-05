"""
clinic.py -- generic KO -> clinic admit/discharge pipeline.

Decoupled from SUPERS' blood/balance/overland hooks. Games opt in by
attaching ``downed``, ``hospitalized``, and ``hospital_until_tick`` on
characters and marking rooms with ``room.hospital`` truthy.

Stdlib only; zero ``supers`` imports.
"""

from __future__ import annotations

# Default pacing when ``game`` is absent (smoke / unit tests).
DEFAULT_KO_ADMIT_TICKS = 3
DEFAULT_STAY_TICKS = 20
RECOVERY_HP_PER_TICK = 1.0
DISCHARGE_HP_FRACTION = 0.5
HP_CAP_DEFAULT = 10.0


def _now_tick(game):
    return int(getattr(game, "game_time_ticks", 0) or 0)


def _hp_cap(character):
    """Best-effort HP ceiling without importing game packages."""
    for name in ("hp_cap", "hp_max"):
        val = getattr(character, name, None)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    try:
        hp = float(getattr(character, "hp", 0) or 0)
    except (TypeError, ValueError):
        hp = HP_CAP_DEFAULT
    return max(hp, HP_CAP_DEFAULT)


def enter_ko(character, *, until_tick=None, game=None):
    """Mark a character downed on the ground (simple KO, no finisher machinery)."""
    character.downed = True
    character.downed_until_tick = int(
        until_tick if until_tick is not None
        else _now_tick(game) + DEFAULT_KO_ADMIT_TICKS
    )


def is_ko(character):
    """True when the character is downed and not yet hospitalized."""
    return bool(getattr(character, "downed", False)) and not bool(
        getattr(character, "hospitalized", False)
    )


def clear_ko(character, *, game=None):
    """Clear downed state without admitting."""
    character.downed = False
    character.downed_until_tick = 0
    if game is not None:
        from engine import hooks
        hooks.clinic_ko_clear(character, game)


def find_clinic_room(game, *, near_room=None):
    """Return the first hospital room in ``game.rooms``, optionally nearest."""
    rooms = getattr(game, "rooms", None) or {}
    hospitals = [
        room for room in rooms.values()
        if getattr(room, "hospital", False)
    ]
    if not hospitals:
        return None
    if near_room is None:
        return hospitals[0]
    # Prefer a hospital in the same zone when possible.
    zone = getattr(near_room, "zone", None)
    if zone:
        for room in hospitals:
            if getattr(room, "zone", None) == zone:
                return room
    return hospitals[0]


def admit(character, room, *, until_tick=None, reason=None, game=None, attacker=None):
    """Move a downed character into a hospital room until ``until_tick``."""
    if room is None or not getattr(room, "hospital", False):
        return False
    now = _now_tick(game)
    character.hospitalized = True
    character.hospital_until_tick = int(
        until_tick if until_tick is not None else now + DEFAULT_STAY_TICKS
    )
    character.downed = False
    character.downed_until_tick = 0
    character.hp = max(1.0, float(getattr(character, "hp", 0) or 0))
    if getattr(character, "location", None) is not room:
        mover = getattr(character, "move_to", None)
        if callable(mover):
            mover(room)
        else:
            character.location = room
    if game is not None:
        from engine import hooks
        hooks.clinic_on_admit(character, room, game, reason, attacker=attacker)
    return True


def discharge(character, *, game=None):
    """Release a hospitalized character back to play."""
    character.hospitalized = False
    character.hospital_until_tick = 0
    character.downed = False
    character.downed_until_tick = 0
    if game is not None:
        from engine import hooks
        hooks.clinic_on_discharge(character, game)
    return True


def tick(game):
    """Advance KO timeouts, hospital recovery, and auto-discharge."""
    from engine.char_index import iter_characters

    now = _now_tick(game)
    for character in list(iter_characters(game)):
        if is_ko(character):
            until = int(getattr(character, "downed_until_tick", 0) or 0)
            if until and now >= until:
                ward = find_clinic_room(game, near_room=getattr(character, "location", None))
                if ward is not None:
                    admit(character, ward, game=game)

        if not getattr(character, "hospitalized", False):
            continue

        until = int(getattr(character, "hospital_until_tick", 0) or 0)
        max_hp = _hp_cap(character)
        character.hp = min(
            max_hp,
            float(getattr(character, "hp", 0) or 0) + RECOVERY_HP_PER_TICK,
        )
        ready_hp = max_hp * DISCHARGE_HP_FRACTION
        if (until and now >= until) or float(character.hp) >= ready_hp:
            discharge(character, game=game)
