"""character_attach.py -- classic fields on Character (composition, not subclasses)."""

from classic import classes as classes_module
from classic import skills as skills_module
from classic import stats as stats_module


def attach_classic(character):
    """Attach classic RPG fields onto a freshly-built Character."""
    character.classic_class = None
    character.classic_level = 1
    character.classic_abilities = stats_module.default_abilities()
    stats_module.sync_engine_stats(character)
    character.classic_armor_bonus = 0
    character.classic_skills = {
        name: 0 for name in skills_module.ALL_SKILLS
    }
    character.combat_engine = "osr"
    character.target = None
    character.last_instant_action_tick = -1
    character.classic_spell_cooldown_tick = -1
    character.hp = stats_module.max_hp(character)
    character.hp_cap = stats_module.max_hp(character)
    character.downed = False
    character.downed_until_tick = 0


def apply_class_kit(character, class_id):
    """Stamp armor, skills, and combat engine after class is chosen."""
    character.classic_class = class_id
    character.classic_level = 1
    character.classic_armor_bonus = classes_module.starting_armor_bonus(class_id)
    character.classic_skills = skills_module.default_skill_ranks(
        class_id, character.classic_level
    )
    character.combat_engine = "osr"
    character.hp = stats_module.max_hp(character)
    character.hp_cap = stats_module.max_hp(character)
