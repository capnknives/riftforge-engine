"""stats.py -- basegame's own bits on top of the engine's shared stat spine.

The six-primary spine (POW/VIT/FOC/FIN/RES/PRE) and Tier now live in
engine/stats.py -- generic content shared by every game built on RiftForge,
not SUPERS-only (see that module's docstring). basegame reuses it wholesale
rather than inventing its own; this file only holds what's still genuinely
basegame-specific: the chargen point-buy caps and this game's own HP
formula.
"""

from engine import stats as engine_stats

# Chargen point-buy caps -- basegame's own game-design choice, not engine
# content. Every stat starts at engine_stats.new_stats()'s default (5.0);
# the player distributes this many bonus points across the six shared
# primaries (see chargen.py).
STAT_MAX = 10
BONUS_POOL = 8

HP_BASE = 20
HP_PER_VIT = 2


def max_hp(character):
    """This game's max HP formula: a flat base plus scaling off Vitality,
    tiered the same way any game on the engine can be -- basegame
    characters realistically never leave Tier 0, so tier_mult is a no-op
    multiplier of 1 today; kept for consistency, not because basegame
    needs the Tier ladder.
    """
    vit = character.stats.get("VIT", 5.0)
    base = HP_BASE + HP_PER_VIT * vit
    return int(base * engine_stats.tier_mult(character.tier))


def _recompute_hp(character):
    """engine.hooks.recompute_hp target: heal character.hp back to full."""
    character.hp = max_hp(character)


def register_hooks():
    """Wire basegame's stat-derived hooks onto the engine.

    Called from basegame/bootstrap.py.register_core_hooks -- kept as its
    own function (rather than inlined in bootstrap.py) so a future stat
    hook (e.g. a skill-check helper for combat_core.py in Stage 6) has one
    obvious place to register alongside this one.
    """
    from engine import hooks
    hooks.set_recompute_hp(_recompute_hp)
