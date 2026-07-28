"""tick_bootstrap.py -- register basegame's Game.on_tick pipeline.

Mirrors supers/tick_bootstrap.py's role and order-band convention
(docs/plans/two_repo_purity.md). The skeleton reference town has no
needs/combat tick-driven systems yet -- this exists as the seam later
basegame stages register onto.
"""

from engine.tick_registry import clear_ticks, register_tick
from engine.systems import regional_weather as weather_module


def register_default_ticks(game):
    """Wire every basegame tick handler onto `game` (idempotent clear+fill)."""
    clear_ticks(game)
    register_tick(game, weather_module.tick_all, order=80, name="weather")
    # needs/economy/combat land in later stages.
