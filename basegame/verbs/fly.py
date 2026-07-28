"""fly.py -- Stellar flight verbs for basegame demo."""

from engine.systems import aerial as aerial_mod


def cmd_fly(character, args, game):
    aerial_mod.cmd_fly(character, args, game)


def cmd_descend(character, args, game):
    aerial_mod.cmd_descend(character, args, game)
