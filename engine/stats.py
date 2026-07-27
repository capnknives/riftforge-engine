"""
stats.py -- the engine's generic stat spine: six primaries plus Tier.

Every game built on RiftForge shares this same schema: six named primary
stats (POW/VIT/FOC/FIN/RES/PRE) attached to a Character as plain data
(compose, don't subclass -- see AGENTS.md rule 4), plus an integer Tier
that scales pool-type derived stats (HP, energy, mana, ...) exponentially
via `tier_mult`. `engine/world.py`'s `Character.__init__` sets both
`character.stats` and `character.tier` to the defaults below for every
Character, the same way it owns `character.hp` as generic vitals storage.

What is NOT here, on purpose: what each stat actually produces (HP
formulas, combat accuracy/evasion/crit math, Tier flavor names like
"Godling") is game content, not engine content -- SUPERS keeps all of
that in `supers/stats.py`, which imports the names below instead of
redefining them. A game is free to never read `tier_mult` at all (a
game with no Tier concept just leaves `character.tier` at 0 forever).

This module is pure math + data: no networking, no database, no game loop.
"""

# The six primaries, in display order. What each one means is game
# content (see supers/stats.py's docstring for SUPERS' own reading of
# them) -- the engine only cares that every Character has all six.
STAT_NAMES = ("POW", "VIT", "FOC", "FIN", "RES", "PRE")

# D2: exponential Tier base (tier_mult = TIER_K ** tier). A game that
# doesn't use Tiers can ignore this entirely; SUPERS uses it to scale
# HP/energy/mana pools comic-book-style across its Tier ladder.
TIER_K = 6


def new_stats():
    """A fresh default stat block: 5.0 across all six primaries.

    Floats, not ints: games that grant small fractional training gains
    (SUPERS' training.py, for one) need primaries that can climb in
    increments smaller than a whole point.

    Returns a NEW dict each call -- if this were a shared constant, every
    character would literally share one stats dict, and training one
    character would train them all (a classic Python mutable-default bug).
    """
    return {name: 5.0 for name in STAT_NAMES}


def tier_mult(tier):
    """The exponential tier multiplier: 1, 6, 36, 216, 1296, ... for
    tier 0, 1, 2, 3, 4, ... A game with no Tier concept just calls this
    with tier=0 forever and gets a no-op multiplier of 1."""
    return TIER_K ** tier
