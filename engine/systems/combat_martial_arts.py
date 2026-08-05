"""
combat_martial_arts.py -- the "martial_arts" default combat engine.

Ships alongside `combat_mundane.py` specifically to *prove* the registry in
`combat_engine.py` is not secretly shaped around one game's idea of combat --
this engine's math and persisted state are deliberately unlike mundane's
flat hit/critical/miss roll:

  * A three-way stance rock-paper-scissors (``guard`` beats ``strike`` beats
    ``grapple`` beats ``guard``) decides whether a swing lands clean, gets
    countered, or clashes even.
  * A combo counter persists on the *attacker* across swings
    (``character.martial_combo``), climbing while they keep landing
    stance-advantage hits and resetting the moment they clash or get
    countered -- a small piece of state `mundane`'s engine has no equivalent
    of at all.

A character can pin their stance for a swing via ``character.martial_stance``
(one of ``STANCES`` -- useful for deterministic tests today, and a natural
hook for a future player-facing "stance <name>" command); leaving it unset
rolls a stance at random via the same ``rng`` seam every engine in this
module accepts.
"""

from __future__ import annotations

import random

from engine.systems import combat_engine

# The three stances and the beat cycle between them (rock-paper-scissors).
# STANCES[i] beats STANCES[i - 1] (wrapping), i.e. guard>strike>grapple>guard.
STANCES = ("guard", "strike", "grapple")
BEATS = {"guard": "strike", "strike": "grapple", "grapple": "guard"}

BASE_DAMAGE = 6.0
CLASH_DAMAGE = 3.0  # half base -- an even clash still grazes, just weakly.
COMBO_BONUS_PER_STACK = 3.0
MAX_COMBO = 3


def _stance_for(character, rng=None):
    """The stance a character fights this swing with.

    Prefers an explicitly pinned ``character.martial_stance`` (deterministic
    tests, or a future player-typed stance) over a random roll.
    """
    pinned = getattr(character, "martial_stance", None)
    if pinned in STANCES:
        return pinned
    roll_fn = rng if rng is not None else random.random
    # Evenly split [0, 1) into three buckets -- clamp the top edge so a
    # rng() that returns exactly 1.0 (some fixed test doubles do) still
    # lands on the last stance instead of indexing past the tuple.
    idx = int(roll_fn() * len(STANCES))
    return STANCES[min(idx, len(STANCES) - 1)]


def build_brief(attacker, defender, game=None, *, rng=None, **_ctx):
    """Decide stances, the resulting outcome, and the damage/combo it means.

    Pure computation -- reads ``attacker.martial_combo`` but does not write
    it; ``apply_brief`` is what actually persists the updated combo count.
    """
    attacker_stance = _stance_for(attacker, rng=rng)
    defender_stance = _stance_for(defender, rng=rng)

    if BEATS[attacker_stance] == defender_stance:
        outcome = "advantage"
    elif BEATS[defender_stance] == attacker_stance:
        outcome = "countered"
    else:
        outcome = "clash"

    combo_before = int(getattr(attacker, "martial_combo", 0) or 0)
    if outcome == "advantage":
        combo_after = min(combo_before + 1, MAX_COMBO)
        damage = BASE_DAMAGE + COMBO_BONUS_PER_STACK * combo_after
    elif outcome == "clash":
        combo_after = 0
        damage = CLASH_DAMAGE
    else:  # countered -- the defender's stance beat the attacker's own.
        combo_after = 0
        damage = 0.0

    return {
        "engine": "martial_arts",
        "attacker": attacker,
        "defender": defender,
        "attacker_stance": attacker_stance,
        "defender_stance": defender_stance,
        "outcome": outcome,
        "damage": damage,
        "combo_after": combo_after,
    }


def apply_brief(brief, game=None):
    """Persist the attacker's new combo count and apply damage to the
    defender.
    """
    attacker = brief["attacker"]
    defender = brief["defender"]
    attacker.martial_combo = brief["combo_after"]
    damage = brief.get("damage", 0.0)
    if damage:
        current = float(getattr(defender, "hp", 0.0) or 0.0)
        defender.hp = max(0.0, current - damage)
    return {
        "outcome": brief["outcome"],
        "damage": damage,
        "combo": brief["combo_after"],
    }


def _name(character):
    return (
        getattr(character, "key", None)
        or getattr(character, "name", None)
        or "someone"
    )


def narrate(brief, result):
    """Plain prose reading the frozen brief + result -- never computed inline
    in ``build_brief``/``apply_brief`` (hard rule 5's brief-then-prose split).
    """
    attacker_name = _name(brief["attacker"])
    defender_name = _name(brief["defender"])
    a_stance = brief["attacker_stance"]
    d_stance = brief["defender_stance"]
    outcome = brief["outcome"]
    if outcome == "advantage":
        return (
            f"{attacker_name}'s {a_stance} beats {defender_name}'s "
            f"{d_stance} -- clean hit for {result['damage']:.0f} damage "
            f"(combo {result['combo']})."
        )
    if outcome == "countered":
        return (
            f"{defender_name}'s {d_stance} counters {attacker_name}'s "
            f"{a_stance} -- no damage, combo broken."
        )
    return (
        f"{attacker_name}'s {a_stance} clashes with {defender_name}'s "
        f"{d_stance} -- a glancing {result['damage']:.0f} damage, "
        f"combo reset."
    )


# Self-registers on import -- any module that imports this one (engine_smoke,
# basegame/combat.py, a future game) gets "martial_arts" available with zero
# extra wiring, the same way importing a hooks module activates its side
# effects elsewhere in this codebase.
combat_engine.register_combat_engine(
    "martial_arts",
    build_brief=build_brief,
    apply_brief=apply_brief,
    narrate=narrate,
)
