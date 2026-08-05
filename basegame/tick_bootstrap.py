"""tick_bootstrap.py -- register basegame's Game.on_tick pipeline.

Mirrors supers/tick_bootstrap.py's role and order-band convention
(docs/plans/two_repo_purity.md). Combat ticks route through
``engine.systems.combat_runtime`` (load ``swing`` / ``active_combat`` at
bootstrap -- see ``basegame/combat_backends.py``).
"""

from engine.tick_registry import clear_ticks, register_tick
from engine.systems import regional_weather as weather_module


def _rift_gate_tick(game):
    """Rotate which elemental rift mouths are open."""
    from basegame import gates as gates_mod
    gates_mod.tick_rotation(game)


def _demo_needs_tick(game):
    """Advance hunger/thirst on every Character in the world."""
    from basegame import needs as needs_mod
    needs_mod.tick_demo_needs(game)


def _combat_runtime_tick(game):
    """Drain every loaded combat backend (swing + active_combat)."""
    from engine.systems import combat_runtime as combat_runtime_mod
    combat_runtime_mod.tick(game)


def _clinic_tick(game):
    """KO timeouts, ward recovery, and auto-discharge."""
    from engine.systems import clinic as clinic_mod
    clinic_mod.tick(game)


def _justice_tick(game):
    from engine.systems import justice as justice_mod
    justice_mod.tick(game)


def _umbral_tick(game):
    """Drain Umbral Charge while shrouded; auto-unshroud at 0."""
    from engine.systems import umbral as umbral_mod
    umbral_mod.tick(game)


def register_default_ticks(game):
    """Wire every basegame tick handler onto `game` (idempotent clear+fill)."""
    clear_ticks(game)
    register_tick(game, _combat_runtime_tick, order=10, name="combat")
    register_tick(game, _clinic_tick, order=11, name="clinic")
    register_tick(game, _justice_tick, order=12, name="justice")
    register_tick(game, _umbral_tick, order=13, name="umbral")
    register_tick(game, weather_module.tick_all, order=80, name="weather")
    register_tick(game, _rift_gate_tick, order=81, name="rift_gates")
    register_tick(game, _demo_needs_tick, order=82, name="demo_needs")
    from basegame import spawn_nests as spawn_nests_mod
    register_tick(game, spawn_nests_mod.tick_nests, order=83, name="spawn_nests")
    from engine.systems import paced_travel as paced_travel_mod
    register_tick(game, paced_travel_mod.tick_walks, order=66, name="paced_walk")
    # economy lands in a later stage.
