"""persist_blob.py -- basegame's half of the character<->JSON blob codec.

Mirrors supers/persist_blob.py's role: engine.persistence stores an opaque
JSON blob per character (the ``characters.stats`` SQLite column) and never
reads its fields itself -- each game owns its own shape entirely.
Registered via engine.hooks.set_blob_codec in
basegame/bootstrap.py.register_core_hooks.
"""

from engine import stats as engine_stats
from basegame import stats as stats_module


def _wallet_fields(character):
    """Return on-hand + bank cents fields for the save blob."""
    from engine.systems import economy as economy_mod

    economy_mod.migrate_wallet_fields(character)
    return {
        "dollars": int(getattr(character, "dollars", 0) or 0),
        "cents": int(getattr(character, "cents", 0) or 0),
        "bank_dollars": int(getattr(character, "bank_dollars", 0) or 0),
        "bank_cents": int(getattr(character, "bank_cents", 0) or 0),
    }


def character_to_blob(character):
    """character -> JSON-safe dict (engine.persistence writes this verbatim)."""
    blob = {
        "bg_path": character.bg_path,
        "bg_stats": dict(getattr(character, "bg_stats", {}) or character.stats),
        "hp": character.hp,
        "mail_inbox": list(getattr(character, "mail_inbox", None) or []),
        # Login credentials survive reboot (same contract as supers blob).
        "password_hash": getattr(character, "password_hash", "") or "",
        "account": (getattr(character, "account", None) or "").strip(),
        # Identity + economy -- without these, reboot wipes Alien/Stellar and cash.
        "origin": getattr(character, "origin", "mundane") or "mundane",
        "alien_path": getattr(character, "alien_path", None),
        "bg_stellar": bool(getattr(character, "bg_stellar", False)),
        "bg_umbral": bool(getattr(character, "bg_umbral", False)),
        "solar_charge": float(getattr(character, "solar_charge", 1.0) or 1.0),
        "umbral_charge": float(getattr(character, "umbral_charge", 1.0) or 1.0),
        "tier": int(getattr(character, "tier", 0) or 0),
    }
    blob.update(_wallet_fields(character))
    from basegame import body_parts as body_parts_module

    blob["body_parts"] = body_parts_module.blob_body_parts(character)
    return blob


def _restore_origin_fields(character, data):
    """Reapply Alien/Stellar/Umbral flags saved in the blob."""
    if "origin" in data:
        character.origin = data.get("origin") or "mundane"
    if "alien_path" in data:
        character.alien_path = data.get("alien_path")
    if data.get("bg_stellar"):
        from engine.systems import aerial as aerial_mod

        aerial_mod.ensure_stellar_defaults(character)
        character.bg_stellar = True
        if "solar_charge" in data:
            character.solar_charge = float(data.get("solar_charge") or 1.0)
    elif "bg_stellar" in data:
        character.bg_stellar = bool(data.get("bg_stellar"))
    if data.get("bg_umbral"):
        from engine.systems import umbral as umbral_mod

        umbral_mod.ensure_umbral_defaults(character)
        character.bg_umbral = True
        if "umbral_charge" in data:
            character.umbral_charge = float(data.get("umbral_charge") or 1.0)
    elif "bg_umbral" in data:
        character.bg_umbral = bool(data.get("bg_umbral"))


def apply_character_blob(character, data):
    """Restore a saved character's basegame fields from `data` (mutates in place).

    Falls back to attach_basegame's fresh defaults for any key an older
    save is missing -- never assumes the blob is complete (same contract
    as SUPERS' apply_character_blob).
    """
    character.bg_path = data.get("bg_path", character.bg_path)
    saved_stats = data.get("bg_stats")
    if isinstance(saved_stats, dict):
        # Prefer bg_stats bag when present; else shared engine spine.
        bag = getattr(character, "bg_stats", None)
        if isinstance(bag, dict):
            bag.update(
                {k: v for k, v in saved_stats.items() if k in engine_stats.STAT_NAMES}
            )
        else:
            character.stats.update(
                {k: v for k, v in saved_stats.items() if k in engine_stats.STAT_NAMES}
            )
    character.hp = data.get("hp", stats_module.max_hp(character))
    character.password_hash = data.get("password_hash", "") or ""
    character.account = data.get("account", getattr(character, "account", "") or "")
    saved_mail = data.get("mail_inbox")
    if isinstance(saved_mail, list):
        character.mail_inbox = list(saved_mail)
    elif not hasattr(character, "mail_inbox"):
        character.mail_inbox = []
    _restore_origin_fields(character, data)
    if "tier" in data:
        character.tier = int(data.get("tier") or 0)
    for field in ("dollars", "cents", "bank_dollars", "bank_cents"):
        if field in data:
            setattr(character, field, int(data.get(field) or 0))
    from basegame import body_parts as body_parts_module

    body_parts_module.restore_body_parts(character, data.get("body_parts"))
