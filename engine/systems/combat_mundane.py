"""
combat_mundane.py -- the "mundane" default combat engine.

This is the promoted/generalized version of what used to live only in
``basegame/combat.py``: a flat hit / critical / miss roll via
``engine.systems.combat_core.roll_weighted_outcome``, with fixed tuning
constants and simple HP subtraction. Any game (not just basegame) that
imports this module gets a working fistfight engine registered under the
id ``"mundane"`` -- ``basegame/combat.py`` becomes a thin dispatcher over
``engine.systems.combat_engine`` keyed by ``character.combat_engine``,
defaulting to ``"mundane"`` when unset.

Hard rule 5 (``AGENTS.md``): ``build_brief`` is pure (no mutation);
``apply_brief`` mutates from a frozen brief; ``narrate`` renders prose
from brief + result as a separate step.
"""

from __future__ import annotations

from engine.systems import combat_core, combat_engine

# Historic basegame tuning -- mirrored in ``basegame/combat.py`` for
# backward compat with anything that imported those constants directly.
HIT_CHANCE = 0.65
CRITICAL_CHANCE = 0.10
DAMAGE_PER_HIT = 8.0
DAMAGE_PER_CRITICAL = 16.0


def build_brief(attacker, defender, game=None, *, rng=None, **_ctx):
    """Roll one weighted outcome and compute damage -- no HP mutation.

    ``rng`` is the same test seam ``roll_weighted_outcome`` accepts: pass
    ``lambda: 0.0`` for a deterministic critical, ``lambda: 1.0`` for a
    guaranteed miss past every weighted bucket.
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
    return {
        "engine": "mundane",
        "attacker": attacker,
        "defender": defender,
        "outcome": outcome,
        "damage": damage,
    }


def apply_brief(brief, game=None):
    """Subtract ``brief["damage"]`` from the defender's HP (floor at 0)."""
    defender = brief["defender"]
    damage = brief.get("damage", 0.0)
    if damage:
        current = float(getattr(defender, "hp", 0.0) or 0.0)
        defender.hp = max(0.0, current - damage)
    return {
        "outcome": brief["outcome"],
        "damage": damage,
    }


def _name(character):
    """Display key for prose -- ``key``, then ``name``, then a fallback."""
    return (
        getattr(character, "key", None)
        or getattr(character, "name", None)
        or "someone"
    )


def narrate(brief, result):
    """One-line prose from the frozen brief + apply result (hard rule 5)."""
    attacker_name = _name(brief["attacker"])
    defender_name = _name(brief["defender"])
    outcome = brief["outcome"]
    damage = result["damage"]
    if outcome == "critical":
        return (
            f"{attacker_name}'s blow lands with crushing force on "
            f"{defender_name} for {damage:.0f} damage."
        )
    if outcome == "hit":
        return (
            f"{attacker_name}'s punch lands solidly on {defender_name} "
            f"for {damage:.0f} damage."
        )
    return (
        f"{attacker_name} swings at {defender_name} and misses."
    )


# Self-registers on import -- same idiom as ``combat_martial_arts.py``.
combat_engine.register_combat_engine(
    "mundane",
    build_brief=build_brief,
    apply_brief=apply_brief,
    narrate=narrate,
)
