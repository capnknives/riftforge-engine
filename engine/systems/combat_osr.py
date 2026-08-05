"""
combat_osr.py -- the "osr" d20 combat engine (third shipped swing style).

Ships alongside ``combat_mundane.py`` and ``combat_martial_arts.py`` as a
generic **d20 + attack bonus vs ascending AC** resolver -- the natural fit for
D&D-alike / OSR games without baking class tables into the engine.

Games register three optional hooks (see ``register_osr_*`` below) to supply
attack bonus, armor class, and damage rolls from their own rules. When a hook
is unset, the engine falls back to simple character attributes:

  * ``osr_attack_bonus`` / ``attack_bonus``
  * ``osr_ac`` / ``armor_class`` (default 10)
  * ``osr_damage_die`` (default 6), ``osr_damage_bonus`` (default 0)

Hard rule 5 (``AGENTS.md``): ``build_brief`` is pure; ``apply_brief``
mutates from a frozen brief; ``narrate`` renders prose separately.

Natural 1 always misses; natural 20 always hits and doubles the damage dice
(two rolls of the damage die, not double the total).
"""

from __future__ import annotations

import random

from engine.systems import combat_engine

_ATTACK_BONUS = None
_ARMOR_CLASS = None
_DAMAGE_ROLL = None


def register_osr_attack_bonus(fn):
    """``fn(attacker, defender, *, weapon_ctx=None) -> int`` total to-hit."""
    global _ATTACK_BONUS
    _ATTACK_BONUS = fn


def register_osr_armor_class(fn):
    """``fn(defender, *, weapon_ctx=None) -> int`` ascending AC."""
    global _ARMOR_CLASS
    _ARMOR_CLASS = fn


def register_osr_damage_roll(fn):
    """``fn(attacker, defender, *, crit=False, weapon_ctx=None) -> int``."""
    global _DAMAGE_ROLL
    _DAMAGE_ROLL = fn


def _rng_roll(rng, sides):
    """Roll 1..sides using optional test rng (0.0-1.0)."""
    if rng is None:
        return random.randint(1, sides)
    return int(float(rng()) * sides) + 1


def _resolve_attack_bonus(attacker, defender, *, weapon_ctx=None):
    if _ATTACK_BONUS is not None:
        return int(_ATTACK_BONUS(attacker, defender, weapon_ctx=weapon_ctx))
    return int(
        getattr(attacker, "osr_attack_bonus", None)
        or getattr(attacker, "attack_bonus", 0)
        or 0
    )


def _resolve_armor_class(defender, *, weapon_ctx=None):
    if _ARMOR_CLASS is not None:
        return int(_ARMOR_CLASS(defender, weapon_ctx=weapon_ctx))
    ac = getattr(defender, "osr_ac", None)
    if ac is not None:
        return int(ac)
    return int(getattr(defender, "armor_class", 10) or 10)


def _resolve_damage_roll(attacker, defender, *, crit=False, weapon_ctx=None):
    if _DAMAGE_ROLL is not None:
        return int(
            _DAMAGE_ROLL(
                attacker, defender, crit=crit, weapon_ctx=weapon_ctx,
            )
        )
    rng = weapon_ctx.get("rng") if weapon_ctx else None
    sides = int(getattr(attacker, "osr_damage_die", 6) or 6)
    bonus = int(getattr(attacker, "osr_damage_bonus", 0) or 0)
    rolls = [_rng_roll(rng, sides)]
    if crit:
        rolls.append(_rng_roll(rng, sides))
    return max(1, sum(rolls) + bonus)


def build_brief(attacker, defender, game=None, *, rng=None, **ctx):
    """d20 + attack bonus vs ascending AC -- pure, no mutation."""
    weapon_ctx = {"rng": rng, **ctx}
    d20 = _rng_roll(rng, 20)
    bonus = _resolve_attack_bonus(
        attacker, defender, weapon_ctx=weapon_ctx,
    )
    total = d20 + bonus
    ac = _resolve_armor_class(defender, weapon_ctx=weapon_ctx)
    outcome = "miss"
    damage = 0
    crit = False
    if d20 == 1:
        outcome = "miss"
    elif d20 == 20 or total >= ac:
        crit = d20 == 20
        damage = _resolve_damage_roll(
            attacker, defender, crit=crit, weapon_ctx=weapon_ctx,
        )
        outcome = "critical" if crit else "hit"
    return {
        "engine": "osr",
        "attacker": attacker,
        "defender": defender,
        "d20": d20,
        "attack_total": total,
        "ac": ac,
        "outcome": outcome,
        "damage": damage,
    }


def apply_brief(brief, game=None):
    """Subtract ``brief["damage"]`` from the defender's HP (floor at 0)."""
    defender = brief["defender"]
    damage = float(brief.get("damage", 0) or 0)
    if damage:
        current = float(getattr(defender, "hp", 0.0) or 0.0)
        defender.hp = max(0.0, current - damage)
    return {
        "outcome": brief["outcome"],
        "damage": damage,
    }


def _name(character):
    return (
        getattr(character, "key", None)
        or getattr(character, "name", None)
        or "someone"
    )


def narrate(brief, result):
    """Tagged one-liner for a11y (hard rule 5 + screenreader tags)."""
    attacker_name = _name(brief["attacker"])
    defender_name = _name(brief["defender"])
    outcome = brief["outcome"]
    damage = result["damage"]
    if outcome == "critical":
        return (
            f"[CRIT] {attacker_name} strikes {defender_name} for "
            f"{damage:.0f} damage."
        )
    if outcome == "hit":
        return (
            f"[HIT] {attacker_name} hits {defender_name} for "
            f"{damage:.0f} damage."
        )
    return f"[MISS] {attacker_name} misses {defender_name}."


combat_engine.register_combat_engine(
    "osr",
    build_brief=build_brief,
    apply_brief=apply_brief,
    narrate=narrate,
)
