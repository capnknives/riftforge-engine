"""
clinic.py -- generic KO -> clinic admit/discharge pipeline.

Decoupled from SUPERS' blood/balance/overland hooks. Games opt in by
attaching ``downed``, ``hospitalized``, and ``hospital_until_tick`` on
characters and marking rooms with ``room.hospital`` truthy.

Stdlib only; zero ``supers`` imports.
"""

from __future__ import annotations

import random as _random_module

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


# --- Ward selection + discharge bookkeeping (generic peel) ---------------


def _room_map_id(room):
    """map_id string for a room, or None."""
    if room is None:
        return None
    return getattr(room, "map_id", None) or None


def _room_zone(room):
    """zone tag for a room, or None."""
    if room is None:
        return None
    return getattr(room, "zone", None) or None


def prefer_near(candidates, near_room):
    """Filter candidate rooms to the same map (then zone) as ``near_room``.

    Returns the narrowed list when non-empty; otherwise the original
    candidates unchanged (global fallback). Ported from supers/hospital.py
    with zero game-package imports.
    """
    if not candidates or near_room is None:
        return list(candidates)
    map_id = _room_map_id(near_room)
    if map_id:
        local = [
            room for room in candidates
            if _room_map_id(room) == map_id
        ]
        if local:
            return local
    zone = _room_zone(near_room)
    if zone:
        local = [
            room for room in candidates
            if _room_zone(room) == zone
        ]
        if local:
            return local
    return list(candidates)


def count_patients(room, *, count_collapsed=True):
    """How many hospitalized (and optionally collapsed) actors are in ``room``."""
    if room is None:
        return 0
    n = 0
    for obj in getattr(room, "contents", ()) or ():
        if getattr(obj, "hospitalized", False):
            n += 1
            continue
        if count_collapsed and getattr(obj, "collapsed", False):
            n += 1
    return n


def pick_ward(
    game,
    rooms_getter=None,
    *,
    near_room=None,
    rng=None,
    room_filter=None,
    fallback=None,
):
    """Choose a ward room near ``near_room``: empty, else least occupied.

    ``rooms_getter`` defaults to ``game.rooms.values()``; games with custom
    room stores or SUPERS-specific filters pass their own callable.
    ``room_filter`` optionally narrows to recovery wards (hospital + sleep,
    Earth plane, etc.) before the proximity pass. ``fallback`` is called
    when no wards match (e.g. return a hospital lobby).
  """
    if game is None:
        return None
    if rooms_getter is None:
        rooms = getattr(game, "rooms", None) or {}
        rooms_getter = rooms.values
    roll = rng if rng is not None else _random_module
    wards = list(rooms_getter())
    if room_filter is not None:
        wards = [room for room in wards if room_filter(room)]
    preferred = prefer_near(wards, near_room)
    wards = preferred if preferred else wards
    if not wards:
        if callable(fallback):
            return fallback()
        return None
    empty = [ward for ward in wards if count_patients(ward) == 0]
    if empty:
        return roll.choice(empty)
    min_n = min(count_patients(ward) for ward in wards)
    candidates = [ward for ward in wards if count_patients(ward) == min_n]
    return roll.choice(candidates)


# --- Aggregate-HP resolver (discharge threshold) ---------------------------

_max_hp_resolver = None


def set_max_hp_resolver(fn):
    """Register fn(character) -> float for discharge HP thresholds.

    Same registration pattern as ``engine.systems.body_parts.set_max_hp_resolver``;
    games register once at import (``supers/hospital.py`` wires ``stats.max_hp``).
    Pass None to clear (test-only).
    """
    global _max_hp_resolver
    _max_hp_resolver = fn


def _resolve_max_hp(character, max_hp_resolver):
    if max_hp_resolver is not None:
        return float(max_hp_resolver(character))
    if _max_hp_resolver is not None:
        return float(_max_hp_resolver(character))
    return _hp_cap(character)


def is_ready_to_discharge(
    character,
    *,
    now_tick,
    max_hp_resolver=None,
    until_tick_attr="hospital_until_tick",
    hospitalized_attr="hospitalized",
    discharge_hp_frac=1.0,
):
    """True when stay timer elapsed or HP is at/above ``discharge_hp_frac`` of max."""
    if not getattr(character, hospitalized_attr, False):
        return False
    until = int(getattr(character, until_tick_attr, 0) or 0)
    max_hp = _resolve_max_hp(character, max_hp_resolver)
    hp = float(getattr(character, "hp", 0) or 0)
    return now_tick >= until or hp >= max_hp * discharge_hp_frac
