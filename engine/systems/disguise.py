"""
disguise.py -- temporary appearance override (costume / grifter wardrobe).

Stdlib only; pierce policy registers via ``register_pierce_check``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

_pierce_check: Optional[Callable] = None


@dataclass
class DisguiseProfile:
    label: str
    appearance: dict
    until_tick: int | None = None
    source: str | None = None


def register_pierce_check(fn: Callable) -> None:
    """Register ``fn(viewer, subject) -> bool`` when disguise fails."""
    global _pierce_check
    _pierce_check = fn


def pierce_disguise(viewer, subject) -> bool:
    """True when disguise should fail (known face, mismatch, etc.)."""
    if _pierce_check is None:
        return False
    return bool(_pierce_check(viewer, subject))


def get_disguise(character) -> DisguiseProfile | None:
    """Return active disguise profile, or None when unset / expired."""
    if character is None:
        return None
    raw = getattr(character, "disguise_profile", None)
    if not isinstance(raw, dict) or not raw:
        return None
    until = raw.get("until_tick")
    if until is not None:
        try:
            until_i = int(until)
        except (TypeError, ValueError):
            until_i = 0
        if until_i > 0:
            # Caller must pass game tick for expiry; without game we keep it.
            pass
    return DisguiseProfile(
        label=str(raw.get("label") or "a stranger"),
        appearance=dict(raw.get("appearance") or {}),
        until_tick=raw.get("until_tick"),
        source=raw.get("source"),
    )


def apply_disguise(
    character,
    *,
    label: str,
    appearance: dict,
    until_tick: int | None = None,
    source: str | None = None,
) -> None:
    """Stamp a disguise profile onto ``character``."""
    character.disguise_profile = {
        "label": str(label),
        "appearance": dict(appearance or {}),
        "until_tick": until_tick,
        "source": source,
    }


def clear_disguise(character) -> None:
    """Remove costume / disguise override."""
    character.disguise_profile = None


def disguise_short_desc(character) -> str | None:
    """Stranger face line from disguise slots, or None."""
    profile = get_disguise(character)
    if profile is None:
        return None
    appearance = profile.appearance or {}
    hair = appearance.get("hair")
    physique = appearance.get("physique")
    if hair and physique:
        return f"{profile.label} with {hair} hair and a {physique} build"
    if profile.label:
        return profile.label
    return "someone in disguise"


def tick_disguise_expiry(character, *, now_tick: int) -> bool:
    """Clear expired disguise; return True when cleared."""
    raw = getattr(character, "disguise_profile", None)
    if not isinstance(raw, dict):
        return False
    until = raw.get("until_tick")
    if until is None:
        return False
    try:
        until_i = int(until)
    except (TypeError, ValueError):
        clear_disguise(character)
        return True
    if until_i > 0 and now_tick >= until_i:
        clear_disguise(character)
        return True
    return False
