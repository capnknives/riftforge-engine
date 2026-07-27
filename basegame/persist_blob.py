"""persist_blob.py -- basegame's half of the character<->JSON blob codec.

Mirrors supers/persist_blob.py's role: engine.persistence stores an opaque
JSON blob per character (the ``characters.stats`` SQLite column) and never
reads its fields itself -- each game owns its own shape entirely.
Registered via engine.hooks.set_blob_codec in
basegame/bootstrap.py.register_core_hooks.
"""

from basegame import stats as stats_module


def character_to_blob(character):
    """character -> JSON-safe dict (engine.persistence writes this verbatim)."""
    return {
        "bg_path": character.bg_path,
        "bg_stats": dict(character.bg_stats),
        "hp": character.hp,
    }


def apply_character_blob(character, data):
    """Restore a saved character's basegame fields from `data` (mutates in place).

    Falls back to attach_basegame's fresh defaults for any key an older
    save is missing -- never assumes the blob is complete (same contract
    as SUPERS' apply_character_blob).
    """
    character.bg_path = data.get("bg_path", character.bg_path)
    saved_stats = data.get("bg_stats")
    if isinstance(saved_stats, dict):
        character.bg_stats.update(
            {k: v for k, v in saved_stats.items() if k in stats_module.STAT_NAMES}
        )
    character.hp = data.get("hp", stats_module.max_hp(character))
