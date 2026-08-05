"""stats.py -- classic ability scores mapped onto the engine stat spine.

Players see STR/DEX/CON/INT/WIS/CHA. The engine still stores POW/VIT/FOC/
FIN/RES/PRE on ``character.stats`` so shared HP helpers and hooks work.
"""

from engine import stats as engine_stats

# Player-facing ability names (display + persist blob).
ABILITY_NAMES = ("STR", "DEX", "CON", "INT", "WIS", "CHA")

# Classic label -> engine spine key (1:1 mapping per MVP plan).
ENGINE_MAP = {
    "STR": "POW",
    "DEX": "FIN",
    "CON": "VIT",
    "INT": "FOC",
    "WIS": "RES",
    "CHA": "PRE",
}
REVERSE_ENGINE_MAP = {v: k for k, v in ENGINE_MAP.items()}

# Chargen: start at 10, spend bonus points (OSR-flavored lean point-buy).
ABILITY_DEFAULT = 10
BONUS_POOL = 8
ABILITY_MAX = 16


def default_abilities():
    """Fresh ability dict for chargen."""
    return {name: float(ABILITY_DEFAULT) for name in ABILITY_NAMES}


def ability_mod(score):
    """OSR / classic modifier: (score - 10) // 2."""
    return int(float(score) - 10) // 2


def sync_engine_stats(character):
    """Copy classic_abilities onto the shared engine spine."""
    abilities = getattr(character, "classic_abilities", None) or {}
    for classic_name, engine_name in ENGINE_MAP.items():
        character.stats[engine_name] = float(
            abilities.get(classic_name, ABILITY_DEFAULT)
        )


def sync_classic_abilities(character):
    """Copy engine spine back into classic_abilities (load / repair)."""
    if not hasattr(character, "classic_abilities") or not isinstance(
        character.classic_abilities, dict
    ):
        character.classic_abilities = default_abilities()
    for classic_name, engine_name in ENGINE_MAP.items():
        if engine_name in character.stats:
            character.classic_abilities[classic_name] = float(
                character.stats[engine_name]
            )


def get_ability(character, name):
    """Read one classic ability score (falls back to engine spine)."""
    abilities = getattr(character, "classic_abilities", None)
    if isinstance(abilities, dict) and name in abilities:
        return float(abilities[name])
    engine_key = ENGINE_MAP.get(name)
    if engine_key:
        return float(character.stats.get(engine_key, ABILITY_DEFAULT))
    return float(ABILITY_DEFAULT)


def armor_class(character):
    """Ascending AC: 10 + DEX mod + armor bonus (hookable)."""
    from classic.rules import registries

    hook = registries.get_ac_calculator()
    if hook is not None:
        return int(hook(character))
    dex_mod = ability_mod(get_ability(character, "DEX"))
    armor = int(getattr(character, "classic_armor_bonus", 0) or 0)
    return 10 + dex_mod + armor


def max_hp(character):
    """Max HP from class hit die, level, and CON mod."""
    from classic import classes as classes_module

    level = int(getattr(character, "classic_level", 1) or 1)
    class_id = getattr(character, "classic_class", None) or "war"
    hit_die = classes_module.hit_die(class_id)
    con_mod = ability_mod(get_ability(character, "CON"))
    # OSR MVP: max die each level (no rolling at chargen).
    per_level = max(1, hit_die + con_mod)
    return max(1, per_level * level)


def _recompute_hp(character):
    """engine.hooks.recompute_hp target: heal to full."""
    character.hp = max_hp(character)
    character.hp_cap = max_hp(character)


def register_hooks():
    """Wire classic stat hooks onto the engine."""
    from engine import hooks

    hooks.set_recompute_hp(_recompute_hp)
