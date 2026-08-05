"""osr_resolvers.py -- wire classic OSR math into engine/systems/combat_osr."""

from classic import classes as classes_module
from classic import stats as stats_module
from classic.rules import registries


def _classic_attack_bonus(attacker, defender, *, weapon_ctx=None):
    hook = registries.get_attack_bonus()
    if hook is not None:
        return int(hook(attacker, defender, weapon_ctx=weapon_ctx))
    class_id = getattr(attacker, "classic_class", None) or "war"
    level = int(getattr(attacker, "classic_level", 1) or 1)
    bab = classes_module.attack_bonus_at_level(class_id, level)
    ability = classes_module.attack_ability(class_id)
    mod = stats_module.ability_mod(
        stats_module.get_ability(attacker, ability)
    )
    return bab + mod


def _classic_damage_roll(attacker, defender, *, crit=False, weapon_ctx=None):
    hook = registries.get_damage_roll()
    if hook is not None:
        return int(
            hook(attacker, defender, weapon_ctx=weapon_ctx, crit=crit)
        )
    import random

    rng = weapon_ctx.get("rng") if weapon_ctx else None
    class_id = getattr(attacker, "classic_class", None) or "war"
    sides = classes_module.weapon_die_sides(class_id)
    ability = classes_module.attack_ability(class_id)
    mod = stats_module.ability_mod(
        stats_module.get_ability(attacker, ability)
    )

    def _roll_die():
        if rng is None:
            return random.randint(1, sides)
        return int(float(rng()) * sides) + 1

    rolls = [_roll_die()]
    if crit:
        rolls.append(_roll_die())
    return max(1, sum(rolls) + max(0, mod))


def _classic_armor_class(defender, *, weapon_ctx=None):
    return stats_module.armor_class(defender)


def register_classic_osr_resolvers():
    """Register classic class/BAB/AC math on the generic osr engine."""
    from engine.systems import combat_osr

    combat_osr.register_osr_attack_bonus(_classic_attack_bonus)
    combat_osr.register_osr_armor_class(_classic_armor_class)
    combat_osr.register_osr_damage_roll(_classic_damage_roll)
