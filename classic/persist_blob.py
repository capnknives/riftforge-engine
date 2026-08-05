"""persist_blob.py -- classic save/load blob for engine.persistence."""

from classic import stats as stats_module


def character_to_blob(character):
    """Character -> JSON-safe dict."""
    return {
        "classic_class": character.classic_class,
        "classic_level": int(getattr(character, "classic_level", 1) or 1),
        "classic_abilities": dict(
            getattr(character, "classic_abilities", None)
            or stats_module.default_abilities()
        ),
        "classic_armor_bonus": int(
            getattr(character, "classic_armor_bonus", 0) or 0
        ),
        "classic_skills": dict(
            getattr(character, "classic_skills", None) or {}
        ),
        "hp": float(getattr(character, "hp", 0) or 0),
        "combat_engine": getattr(character, "combat_engine", "osr"),
    }


def apply_character_blob(character, data):
    """Restore classic fields from a saved blob."""
    character.classic_class = data.get("classic_class", character.classic_class)
    character.classic_level = int(
        data.get("classic_level", getattr(character, "classic_level", 1)) or 1
    )
    saved_abilities = data.get("classic_abilities")
    if isinstance(saved_abilities, dict):
        character.classic_abilities = {
            k: float(v)
            for k, v in saved_abilities.items()
            if k in stats_module.ABILITY_NAMES
        }
    stats_module.sync_engine_stats(character)
    character.classic_armor_bonus = int(
        data.get("classic_armor_bonus", getattr(character, "classic_armor_bonus", 0))
        or 0
    )
    saved_skills = data.get("classic_skills")
    if isinstance(saved_skills, dict):
        character.classic_skills = {
            k: int(v) for k, v in saved_skills.items()
        }
    raw_engine = data.get(
        "combat_engine", getattr(character, "combat_engine", "osr")
    )
    character.combat_engine = "osr" if raw_engine == "classic" else raw_engine
    character.hp = float(
        data.get("hp", stats_module.max_hp(character))
    )
    character.hp_cap = stats_module.max_hp(character)
