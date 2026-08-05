"""registries.py -- optional hooks for deeper classic rules later.

MVP ships lean OSR math inline in stats/combat/skills. Games and future
classic stages register overrides here without forking core modules.
"""

_AC_CALCULATOR = None
_ATTACK_BONUS = None
_DAMAGE_ROLL = None
_SKILL_CHECK = None
_SPELL_RESOLVER = None
_LEVEL_UP_FEATURES = None


def register_ac_calculator(fn):
    """``fn(character) -> int`` ascending AC."""
    global _AC_CALCULATOR
    _AC_CALCULATOR = fn


def get_ac_calculator():
    return _AC_CALCULATOR


def register_attack_bonus(fn):
    """``fn(attacker, defender, *, weapon_ctx=None) -> int`` total to-hit."""
    global _ATTACK_BONUS
    _ATTACK_BONUS = fn


def get_attack_bonus():
    return _ATTACK_BONUS


def register_damage_roll(fn):
    """``fn(attacker, defender, *, weapon_ctx=None, crit=False) -> int``."""
    global _DAMAGE_ROLL
    _DAMAGE_ROLL = fn


def get_damage_roll():
    return _DAMAGE_ROLL


def register_skill_check(fn):
    """``fn(character, skill, dc, *, rng=None) -> (success, detail)``."""
    global _SKILL_CHECK
    _SKILL_CHECK = fn


def get_skill_check():
    return _SKILL_CHECK


def register_spell_resolver(fn):
    """``fn(caster, spell_id, target, game, *, rng=None) -> outcome dict``."""
    global _SPELL_RESOLVER
    _SPELL_RESOLVER = fn


def get_spell_resolver():
    return _SPELL_RESOLVER


def register_level_up_features(fn):
    """``fn(character, new_level) -> None`` feat / subclass stub hook."""
    global _LEVEL_UP_FEATURES
    _LEVEL_UP_FEATURES = fn


def get_level_up_features():
    return _LEVEL_UP_FEATURES
