"""
needs -- hunger/thirst demo meters for the basegame reference town.

Thin basegame wrapper over ``engine.systems.needs`` (no supers import).
Registers two lifestyle meters and a tick driver that advances every
Character in the world -- the Phase 3 proof that the engine registry works
without SUPERS' eleven-meter ``decay()`` fiction.
"""

from __future__ import annotations

from engine.char_index import iter_characters
from engine.systems import needs as needs_engine

# Same calendar copy supers/needs.py uses (must match engine/game_calendar.py).
_TICKS_PER_GAME_DAY = 9600
_TICKS_PER_HOUR = _TICKS_PER_GAME_DAY // 24

# Realistic meal/drink pacing anchors (SUPERS magnitudes; basegame demo only).
_HUNGER_SEEK_GAME_HOURS = 8.0
_THIRST_SEEK_GAME_HOURS = 6.0

HUNGER_PER_TICK = needs_engine.seek_rate(
    _HUNGER_SEEK_GAME_HOURS * _TICKS_PER_HOUR,
)
THIRST_PER_TICK = needs_engine.seek_rate(
    _THIRST_SEEK_GAME_HOURS * _TICKS_PER_HOUR,
)

NEEDS = ("hunger", "thirst")

_RATES = {
    "hunger": HUNGER_PER_TICK,
    "thirst": THIRST_PER_TICK,
}


def _register_needs_meters():
    """Declare hunger/thirst on the engine's generic meter registry."""
    for name, rate in _RATES.items():
        needs_engine.register_meter(name, rate)


_register_needs_meters()


def attach_character(character):
    """Attach every basegame need meter at 0.0 (fully satisfied)."""
    needs_engine.attach_meters(character, NEEDS)


def tick_demo_needs(game):
    """Advance hunger/thirst for every Character currently in the world."""
    for character in iter_characters(game):
        for name in NEEDS:
            needs_engine.advance(character, name, _RATES[name])
