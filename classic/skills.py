"""skills.py -- classic skill list, ranks, and hookable skill checks."""

import random

from classic import classes as classes_module
from classic import stats as stats_module
from classic.rules import registries

# Shared skill vocabulary (MVP subset).
ALL_SKILLS = (
    "athletics",
    "climb",
    "sneak",
    "pick_lock",
    "religion",
    "medicine",
    "arcana",
    "lore",
    "intimidate",
    "persuade",
    "perception",
    "survival",
)

# Ability key per skill (classic names).
SKILL_ABILITY = {
    "athletics": "STR",
    "climb": "STR",
    "sneak": "DEX",
    "pick_lock": "DEX",
    "religion": "WIS",
    "medicine": "WIS",
    "arcana": "INT",
    "lore": "INT",
    "intimidate": "CHA",
    "persuade": "CHA",
    "perception": "WIS",
    "survival": "WIS",
}


def default_skill_ranks(class_id, level=1):
    """Build skill rank dict for a fresh character at ``level``."""
    level = max(1, min(20, int(level)))
    ranks = {name: 0 for name in ALL_SKILLS}
    for skill in classes_module.CLASS_SKILLS.get(class_id, ()):
        ranks[skill] = level + 3
    return ranks


def skill_bonus(character, skill):
    """Total modifier: ranks + linked ability mod."""
    skill = str(skill or "").strip().lower()
    if skill not in SKILL_ABILITY:
        return 0
    ranks = getattr(character, "classic_skills", None) or {}
    rank = int(ranks.get(skill, 0) or 0)
    ability = SKILL_ABILITY[skill]
    mod = stats_module.ability_mod(
        stats_module.get_ability(character, ability)
    )
    return rank + mod


def roll_skill_check(character, skill, dc, *, rng=None):
    """d20 + bonus vs DC. Returns (success, roll, total)."""
    hook = registries.get_skill_check()
    if hook is not None:
        return hook(character, skill, dc, rng=rng)
    rng = rng or random.random
    roll = int(rng() * 20) + 1
    total = roll + skill_bonus(character, skill)
    return total >= int(dc), roll, total
