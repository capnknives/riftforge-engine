"""umbral.py -- shroud/unshroud verbs for the Alien Umbral path."""

from engine.systems import umbral as umbral_mod


def cmd_shroud(character, args, game):
    """Pull night around you -- hide from look / presence (Umbral only)."""
    umbral_mod.cmd_shroud(character, args, game)


def cmd_unshroud(character, args, game):
    """Drop the Umbral night shroud."""
    umbral_mod.cmd_unshroud(character, args, game)
