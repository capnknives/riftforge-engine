"""walk.py -- paced in-zone travel (engine paced_travel primitives)."""

from engine.systems import paced_travel as paced_mod


def cmd_walk(character, args, game):
    """Paced path toward a named room in this zone."""
    paced_mod.cmd_paced_travel(character, args, game, pace="walk")


def cmd_jog(character, args, game):
    """Same as walk, a little faster."""
    paced_mod.cmd_paced_travel(character, args, game, pace="jog")


def cmd_run(character, args, game):
    """Same as walk, fastest pace."""
    paced_mod.cmd_paced_travel(character, args, game, pace="run")
