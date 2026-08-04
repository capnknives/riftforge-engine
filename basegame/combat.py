"""
combat.py -- minimal basegame fistfight resolver.

Phase 5 finding (docs/plans/riftforge_core_expansion.md): SUPERS'
``resolve_round`` is already registered on the fully generic
``engine.tick_registry`` (the same mechanism every other basegame system
here uses -- gates, needs, spawn nests), and its Structured Battle Brief
has no generic core to extract (60+ fields, every one of them a specific
SUPERS mechanic -- Momentum, Discipline synergy, ultimates, Swarm crowd
pressure, ...). There is nothing to "peel" there. What proves the engine
is actually pluggable is a *second*, wholly independent game plugging
into the same generic primitives with its own combat -- this module.

Uses ``engine.systems.combat_core.roll_weighted_outcome`` (the generic
weighted-dice mechanism peeled from SUPERS under ``two_repo_purity``
Phase 7) for the hit/critical/miss roll. The result shape below
(``{"outcome": ..., "damage": ...}``) is this game's own, not shared with
SUPERS' Structured Battle Brief -- a third game could pick any shape it
wants.
"""

from __future__ import annotations

from engine.systems import combat_core

HIT_CHANCE = 0.65
CRITICAL_CHANCE = 0.10
DAMAGE_PER_HIT = 8.0
DAMAGE_PER_CRITICAL = 16.0


def resolve_swing(attacker, defender, *, rng=None):
    """One attacker-vs-defender swing. Returns this game's own tiny
    result dict -- not a Structured Battle Brief, just enough for the
    caller (and tests) to see what happened.

    ``rng`` is a test-only seam (mirrors ``roll_weighted_outcome``'s own
    ``rng`` param, and ``supers/combat.py``'s ``force_reaction``): pass a
    fixed callable for a deterministic swing instead of real gameplay's
    ``random.random``.
    """
    outcome = combat_core.roll_weighted_outcome(
        [("critical", CRITICAL_CHANCE), ("hit", HIT_CHANCE)],
        default="miss",
        rng=rng,
    )
    damage = 0.0
    if outcome == "critical":
        damage = DAMAGE_PER_CRITICAL
    elif outcome == "hit":
        damage = DAMAGE_PER_HIT
    if damage:
        current = float(getattr(defender, "hp", 0.0) or 0.0)
        defender.hp = max(0.0, current - damage)
    return {"outcome": outcome, "damage": damage}


def resolve_round(game, *, rng=None):
    """Advance every ongoing basegame fight by one round (order-10 tick,
    mirroring SUPERS' own combat tick order -- see tick_bootstrap.py).
    ``rng`` is the same test-only seam as ``resolve_swing``.
    """
    from engine.char_index import iter_characters

    for attacker in list(iter_characters(game)):
        target = getattr(attacker, "target", None)
        if target is None:
            continue
        if float(getattr(attacker, "hp", 0.0) or 0.0) <= 0:
            continue
        resolve_swing(attacker, target, rng=rng)
        if float(getattr(target, "hp", 0.0) or 0.0) <= 0:
            attacker.target = None
            target.target = None
