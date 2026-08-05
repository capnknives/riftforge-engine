"""
combat_backends.py -- register and load basegame's combat backends.

Wires the engine ``combat_runtime`` registry to basegame's two shipped
backends:

  load swing          round-based resolve_round (mundane / martial_arts)
  load active_combat  timestamp-buffered twitch (kinetic)

Called once from ``bootstrap.register_all_hooks``.
"""

from __future__ import annotations

from engine.systems import combat_runtime as cr
from engine.systems import fight as fight_mod


def _load_swing():
    """Import swing engines so they self-register on combat_engine."""
    from engine.systems import combat_martial_arts  # noqa: F401
    from engine.systems import combat_mundane  # noqa: F401
    from engine.systems import combat_osr  # noqa: F401


def _tick_swing(game):
    from basegame import combat as combat_mod
    combat_mod.resolve_round(game)


def _load_active_combat():
    from engine.systems import active_combat  # noqa: F401


def _tick_active_combat(game):
    from basegame import active_combat_demo as demo_mod
    demo_mod.tick(game)


def register_backends():
    """Register swing + active_combat with combat_runtime (idempotent)."""
    cr.register_combat_backend(
        cr.BACKEND_SWING,
        load_fn=_load_swing,
        tick_fn=_tick_swing,
        fight_mode=fight_mod.MODE_NARRATIVE,
        label="round-based swing combat (combat_engine: mundane, martial_arts, osr, …)",
    )
    cr.register_combat_backend(
        cr.BACKEND_ACTIVE,
        load_fn=_load_active_combat,
        tick_fn=_tick_active_combat,
        fight_mode=fight_mod.MODE_ACTIVE,
        label="timestamp-buffered twitch combat (active_combat: kinetic, …)",
    )


def bootstrap_combat(*, default_backend=None, load_all=True):
    """Register, load, and set the module default backend.

    ``load_all=True`` loads both backends so arena rooms can still use
    active combat while the game default stays ``swing``. Pass
    ``load_all=False`` and load only what you need in tests.
    """
    register_backends()
    if default_backend is not None:
        cr.set_default_combat_backend(default_backend)
    if load_all:
        cr.load_combat_backend(cr.BACKEND_SWING)
        cr.load_combat_backend(cr.BACKEND_ACTIVE)
    return cr
