"""
firearms.py -- generic ranged-weapon state for engine active (twitch) combat.

``load`` chambers a round from the magazine; ``aim`` sets a sight line on a
target (optional body zone); ``fire`` discharges the chambered round through
the active-combat telegraph pipeline.

Melee twitch verbs (punch, kick, …) never use ``aim`` -- that verb is
firearm-only in the engine. Games register weapon catalogs via hooks; this
module only tracks per-character ammo + sight state.

Stdlib only; zero ``supers`` imports.
"""

from __future__ import annotations

# Character.engine_firearm: {"magazine": int, "max_magazine": int, "chambered": bool}
FIREARM_ATTR = "engine_firearm"
# Character.firearm_sight: {"target": Character, "zone": str | None}
SIGHT_ATTR = "firearm_sight"

AIM_ZONES = frozenset({
    "head", "neck", "torso", "arms", "hands", "legs", "feet",
})

DEFAULT_MAGAZINE = 6


def default_firearm(*, magazine=DEFAULT_MAGAZINE, chambered=False):
    """Fresh weapon state dict for demos and tests."""
    return {
        "magazine": int(magazine),
        "max_magazine": int(magazine),
        "chambered": bool(chambered),
    }


def ensure_firearm(character, spec=None):
    """Stamp ``engine_firearm`` when missing (idempotent)."""
    current = getattr(character, FIREARM_ATTR, None)
    if not isinstance(current, dict):
        setattr(character, FIREARM_ATTR, dict(spec or default_firearm()))
    elif spec is not None:
        current.update(spec)
    if not hasattr(character, SIGHT_ATTR):
        setattr(character, SIGHT_ATTR, None)


def get_firearm(character):
    """Return the live firearm dict or None when unarmed."""
    ensure_firearm(character)
    weapon = getattr(character, FIREARM_ATTR, None)
    return weapon if isinstance(weapon, dict) else None


def has_firearm(character):
    """True when the character carries a ranged weapon with ammo tracking."""
    return get_firearm(character) is not None


def clear_sight(character):
    """Drop any queued sight line."""
    setattr(character, SIGHT_ATTR, None)


def get_sight(character):
    """Return ``{"target": Character, "zone": str|None}`` or None."""
    sight = getattr(character, SIGHT_ATTR, None)
    return sight if isinstance(sight, dict) else None


def set_sight(character, target, *, zone=None):
    """Record aim on ``target``; optional ``zone`` must be in ``AIM_ZONES``."""
    if zone is not None and zone not in AIM_ZONES:
        return False, (
            "Sight where? "
            + ", ".join(sorted(AIM_ZONES))
            + " (or omit for center mass)."
        )
    setattr(character, SIGHT_ATTR, {"target": target, "zone": zone})
    return True, None


def load_chamber(character):
    """Move one round from magazine into the chamber. Returns (ok, message)."""
    weapon = get_firearm(character)
    if weapon is None:
        return False, "You are not holding a ranged weapon."
    if weapon.get("chambered"):
        return False, "A round is already chambered."
    mag = int(weapon.get("magazine") or 0)
    if mag <= 0:
        return False, "The magazine is empty -- reload first."
    weapon["magazine"] = mag - 1
    weapon["chambered"] = True
    return True, "You chamber a round."


def reload_magazine(character, *, amount=None):
    """Fill magazine from abstract reserve (demo hook). Returns (ok, message)."""
    weapon = get_firearm(character)
    if weapon is None:
        return False, "You are not holding a ranged weapon."
    cap = int(weapon.get("max_magazine") or DEFAULT_MAGAZINE)
    current = int(weapon.get("magazine") or 0)
    if current >= cap:
        return False, "The magazine is already full."
    fill = cap - current if amount is None else min(int(amount), cap - current)
    if fill <= 0:
        return False, "Nothing to reload."
    weapon["magazine"] = current + fill
    return True, f"You load {fill} round{'s' if fill != 1 else ''} into the magazine."


def can_fire(character):
    """True when chambered and a sight line exists."""
    weapon = get_firearm(character)
    if weapon is None or not weapon.get("chambered"):
        return False
    sight = get_sight(character)
    return sight is not None and sight.get("target") is not None


def consume_chamber(character):
    """Spend the chambered round after a trigger pull (idempotent-safe)."""
    weapon = get_firearm(character)
    if weapon is None:
        return
    weapon["chambered"] = False
    clear_sight(character)

