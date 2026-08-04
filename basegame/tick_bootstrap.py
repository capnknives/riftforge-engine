"""tick_bootstrap.py -- register basegame's Game.on_tick pipeline.

Mirrors supers/tick_bootstrap.py's role and order-band convention
(docs/plans/two_repo_purity.md). Registers combat (order 10, matching
SUPERS' own combat tick order), weather, rift gates, the Phase 3
hunger/thirst demo needs driver, and the Phase 6 critter nest spawn
driver.
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


def _combat_tick(game):
    """Advance every ongoing basegame fistfight by one round."""
    from basegame import combat as combat_mod
    combat_mod.resolve_round(game)


def register_default_ticks(game):
    """Wire every basegame tick handler onto `game` (idempotent clear+fill)."""
    clear_ticks(game)
    register_tick(game, _combat_tick, order=10, name="combat")
    register_tick(game, weather_module.tick_all, order=80, name="weather")
    register_tick(game, _rift_gate_tick, order=81, name="rift_gates")
    register_tick(game, _demo_needs_tick, order=82, name="demo_needs")
    from basegame import spawn_nests as spawn_nests_mod
    register_tick(game, spawn_nests_mod.tick_nests, order=83, name="spawn_nests")
    # economy lands in a later stage.
