"""
collapse.py -- generic street-collapse FSM for sustained critical needs.

Games register eligibility and needs-frozen gates; this module owns the
``collapsed`` / ``critical_since_tick`` / ``collapsed_at_tick`` timer math.
SUPERS ``hospital.py`` keeps asleep/dreaming prose, room broadcast, and admit.

Stdlib only; zero ``supers`` imports.
"""

from __future__ import annotations

from typing import Callable, Optional

_eligible: Optional[Callable] = None
_needs_frozen: Optional[Callable] = None

ATTR_COLLAPSED = "collapsed"
ATTR_COLLAPSED_AT = "collapsed_at_tick"
ATTR_CRITICAL_SINCE = "critical_since_tick"


def set_eligible(fn):
    """Register ``fn(character) -> bool`` — False skips collapse risk."""
    global _eligible
    _eligible = fn


def set_needs_frozen(fn):
    """Register ``fn(character) -> bool`` — True resets critical timer."""
    global _needs_frozen
    _needs_frozen = fn


def ensure_defaults(character):
    """Fill collapse attrs on old saves / fresh Characters."""
    if not hasattr(character, ATTR_COLLAPSED):
        character.collapsed = False
    if not hasattr(character, ATTR_CRITICAL_SINCE):
        character.critical_since_tick = 0
    if not hasattr(character, ATTR_COLLAPSED_AT):
        character.collapsed_at_tick = 0


def is_collapsed(character):
    """True when the actor is in street coma (pre-admit)."""
    return bool(getattr(character, ATTR_COLLAPSED, False))


def is_eligible(character):
    """Eligibility gate — default True when no game hook registered."""
    if _eligible is None:
        return True
    return bool(_eligible(character))


def needs_frozen(character):
    """True when need meters should not advance collapse risk."""
    if _needs_frozen is None:
        return False
    return bool(_needs_frozen(character))


def clear_critical_timer(character):
    """Reset sustained-critical accumulator."""
    character.critical_since_tick = 0


def critical_sustained_elapsed(
    game,
    character,
    critical: bool,
    collapse_seconds: float,
) -> bool:
    """Advance critical-since timer; True when collapse threshold met."""
    ensure_defaults(character)
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    if not critical:
        clear_critical_timer(character)
        return False
    if not character.critical_since_tick:
        character.critical_since_tick = now
        return False
    from engine import game_clock_tuning as clock_mod

    threshold = clock_mod.ticks_for_wall_seconds(collapse_seconds, game)
    elapsed = now - int(character.critical_since_tick)
    return elapsed >= threshold


def apply_collapse_markers(game, character):
    """Stamp collapsed street-coma markers (no asleep/dreaming — game adds)."""
    ensure_defaults(character)
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    character.collapsed = True
    character.collapsed_at_tick = now
    clear_critical_timer(character)


def iter_street_collapse_fallback(game, fallback_seconds):
    """Yield collapsed, non-hospitalized characters past the fallback timer."""
    from engine.char_index import iter_characters

    ensure_registry = ensure_defaults
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    from engine import game_clock_tuning as clock_mod

    threshold = clock_mod.ticks_for_wall_seconds(fallback_seconds, game)
    for obj in iter_characters(game):
        ensure_registry(obj)
        if not is_collapsed(obj):
            continue
        if bool(getattr(obj, "hospitalized", False)):
            continue
        started = int(getattr(obj, ATTR_COLLAPSED_AT, 0) or 0)
        if started and (now - started) >= threshold:
            yield obj
