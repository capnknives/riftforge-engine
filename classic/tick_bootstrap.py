"""tick_bootstrap.py -- classic Game.on_tick pipeline (combat at order 10)."""

from engine.tick_registry import clear_ticks, register_tick


def _combat_tick(game):
    from classic import combat as combat_mod
    combat_mod.resolve_round(game)


def register_default_ticks(game):
    clear_ticks(game)
    register_tick(game, _combat_tick, order=10, name="combat")
