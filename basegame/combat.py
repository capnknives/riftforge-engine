"""
combat.py -- thin dispatcher over the engine's pluggable combat engines.

Phase 5 finding (docs/plans/riftforge_core_expansion.md): SUPERS'
``resolve_round`` is already registered on the fully generic
``engine.tick_registry`` (the same mechanism every other basegame system
here uses -- gates, needs, spawn nests), and its Structured Battle Brief
has no generic core to extract (60+ fields, every one of them a specific
SUPERS mechanic -- Momentum, Discipline synergy, ultimates, Swarm crowd
pressure, ...). There is nothing to "peel" there. What proves the engine
is actually pluggable is a *second*, wholly independent game plugging
into the same generic primitives with its own combat -- this module.

This module no longer owns fistfight math directly. It dispatches each
swing through ``engine.systems.combat_engine.resolve_swing``, keyed by
``character.combat_engine`` (default ``"mundane"``). The shipped default
engines ``mundane`` (flat hit/critical/miss roll) and ``martial_arts``
(stance rock-paper-scissors + combo counter) self-register when imported
below. Set ``character.combat_engine = "martial_arts"`` to fight with the
second style; unknown ids fall back to ``"mundane"`` without raising.

The ``{"outcome": ..., "damage": ...}`` return shape of ``resolve_swing``
is preserved for tick_bootstrap and existing tests.
"""

from __future__ import annotations

from engine.systems import combat_engine
# Side-effect imports: both default engines self-register on import.
from engine.systems import combat_martial_arts  # noqa: F401
from engine.systems import combat_mundane

# Historic tuning constants -- re-exported from the mundane engine so
# ``from basegame.combat import HIT_CHANCE`` keeps working.
HIT_CHANCE = combat_mundane.HIT_CHANCE
CRITICAL_CHANCE = combat_mundane.CRITICAL_CHANCE
DAMAGE_PER_HIT = combat_mundane.DAMAGE_PER_HIT
DAMAGE_PER_CRITICAL = combat_mundane.DAMAGE_PER_CRITICAL


def resolve_swing(attacker, defender, *, rng=None):
    """One attacker-vs-defender swing via the attacker's combat engine.

    Returns this game's own tiny result dict -- not a Structured Battle
    Brief, just enough for the caller (and tests) to see what happened.

    ``rng`` is a test-only seam forwarded to the registered engine's
    ``build_brief`` (mirrors ``roll_weighted_outcome``'s ``rng`` param).
    """
    engine_id = getattr(attacker, "combat_engine", None) or "mundane"
    resolved = combat_engine.resolve_swing(
        engine_id, attacker, defender, rng=rng,
    )
    if resolved is None:
        # Unknown engine id (typo, pruned engine) -- soft-fall back to
        # mundane rather than raising in this demo game.
        resolved = combat_engine.resolve_swing(
            "mundane", attacker, defender, rng=rng,
        )
    result = resolved["result"]
    return {
        "outcome": result["outcome"],
        "damage": result["damage"],
    }


def resolve_round(game, *, rng=None):
    """Advance every ongoing basegame fight by one round (order-10 tick,
    mirroring SUPERS' own combat tick order -- see tick_bootstrap.py).
    ``rng`` is the same test-only seam as ``resolve_swing``.

    Skips characters in an active-combat Fight (``combat_mode == "active"``)
    -- those bouts are drained by ``active_combat.tick_active_combat``.
    """
    from engine.char_index import iter_characters
    from engine.systems import fight as fight_mod

    for attacker in list(iter_characters(game)):
        bout = fight_mod.get_fight(attacker)
        if bout is not None and bout.combat_mode == fight_mod.MODE_ACTIVE:
            continue
        target = getattr(attacker, "target", None)
        if target is None:
            continue
        if float(getattr(attacker, "hp", 0.0) or 0.0) <= 0:
            continue
        resolve_swing(attacker, target, rng=rng)
        if float(getattr(target, "hp", 0.0) or 0.0) <= 0:
            from engine.systems import clinic as clinic_mod
            clinic_mod.enter_ko(target, game=game)
            attacker.target = None
            target.target = None
