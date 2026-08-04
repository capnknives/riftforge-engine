"""needs.py -- the engine's generic capped 0.0-1.0 meter kit.

Every game built on Riftforge can attach any number of named float meters to
a Character (or any object) and let them run 0.0 (satisfied) -> 1.0
(critical) via the helpers below. This module knows nothing about what a
meter *means* -- no hunger, no thirst, no fear. That is game content: SUPERS'
``supers/needs.py`` attaches its own eleven-meter set (hunger, thirst,
energy, duty, entertainment, social, gym, hygiene, fear, homesickness,
vessel_strain) plus all of its fiction (Vampire fuel mirroring, Celestial
Mantle skips, pack duty, homesickness distance tiers, ...) on top of these
primitives, the same way ``engine/stats.py``'s six primaries + Tier get
SUPERS-specific formulas layered on in ``supers/stats.py``
(docs/plans/two_repo_purity.md, Phase 7 Stage 4).

A game that wants zero meters just never calls any of this -- there is no
default meter set and nothing here mutates a Character on its own.

This module is pure math + attribute access: no networking, no database, no
game loop, and (per the Phase 7 purity gate) zero ``supers`` imports.
"""

from __future__ import annotations

# Meter level that means "past comfortable -- go seek the resource" and the
# level that means "in real trouble" (loudest flavor / urgent handling). A
# game is free to pass its own threshold into any function below instead --
# these are just the sane defaults SUPERS itself tunes to.
SEEK_THRESHOLD = 0.60
CRITICAL_THRESHOLD = 0.95


def seek_rate(target_ticks, *, threshold=SEEK_THRESHOLD):
    """Per-tick accrual rate so a meter starting at 0.0 reaches ``threshold``
    after ``target_ticks`` ticks of steady accrual.

    This is the "RATE = SEEK_THRESHOLD / (target_hours * ticks_per_hour)"
    formula every lifestyle meter in a game's tuning file ends up repeating
    by hand -- callers pass whatever tick math gets them to their own
    target (game-hours * ticks-per-hour, game-days * ticks-per-day, ...).
    """
    return threshold / target_ticks


def attach_meters(character, names, *, value=0.0):
    """Set every meter in ``names`` on ``character`` to ``value`` (default
    0.0, fully satisfied). Call once from a game's character-attach step.
    """
    for name in names:
        setattr(character, name, value)


def ensure_meters(character, names):
    """Fill in any meter in ``names`` missing from ``character`` (old saves,
    a meter added to the game's set after a character already existed).
    """
    if character is None:
        return
    for name in names:
        if not hasattr(character, name) or getattr(character, name) is None:
            setattr(character, name, 0.0)


def dump_meters(character, names):
    """Dict of every meter in ``names`` for persist -- 0.0 when missing."""
    ensure_meters(character, names)
    return {name: float(getattr(character, name, 0.0) or 0.0) for name in names}


def load_meters(character, names, saved, *, precision=3):
    """Apply meter values from a persist blob (missing keys -> 0.0).

    ``saved`` that isn't a dict (corrupt / missing blob) attaches a fresh
    zeroed set instead of raising.
    """
    if not isinstance(saved, dict):
        attach_meters(character, names)
        return
    for name in names:
        setattr(
            character,
            name,
            round(float(saved.get(name, 0.0) or 0.0), precision),
        )


def clamp_meters(character, names):
    """Re-clamp every meter in ``names`` into 0.0..1.0 without changing its
    trajectory -- for callers that nudge several linked meters by hand and
    want a single pass to catch any that drifted past the ceiling/floor.
    """
    for name in names:
        raw = float(getattr(character, name, 0.0) or 0.0)
        setattr(character, name, max(0.0, min(1.0, raw)))


def advance(character, name, rate, *, ceiling=1.0):
    """Raise one meter by ``rate``, clamped at ``ceiling`` -- the innermost
    step of any decay tick. Returns the new level.
    """
    current = float(getattr(character, name, 0.0) or 0.0)
    new_level = min(ceiling, current + rate)
    setattr(character, name, new_level)
    return new_level


def satisfy(character, name):
    """Reset one meter to 0.0 -- instant and total (a game may special-case
    specific meters on top of this for its own fiction, e.g. re-syncing from
    a shared resource instead of wiping).
    """
    setattr(character, name, 0.0)


def sate_ambient(character, name, drip, *, floor=0.0):
    """Lower one meter by ``drip``, clamped at ``floor`` (default 0.0).

    For meters sated by presence/idling rather than a discrete action.
    Returns the new level.
    """
    current = float(getattr(character, name, 0.0) or 0.0)
    new_level = max(floor, current - drip)
    setattr(character, name, new_level)
    return new_level


def is_critical(character, name, *, critical=CRITICAL_THRESHOLD):
    """True if this one meter has crossed the critical line."""
    return float(getattr(character, name, 0.0) or 0.0) >= critical


def most_urgent(character, names, *, skip=(), threshold=SEEK_THRESHOLD):
    """(name, level) for the worst meter in ``names`` at/above ``threshold``,
    or None if every meter is content. Ties break in ``names`` order (first
    seen wins), so callers get a stable pick instead of dithering.

    ``skip`` excludes meters that should appear on a status sheet but never
    drive urgent-need selection (SUPERS' Duty is one such meter).
    """
    worst = None
    for name in names:
        if name in skip:
            continue
        level = float(getattr(character, name, 0.0) or 0.0)
        if level >= threshold and (worst is None or level > worst[1]):
            worst = (name, level)
    return worst


def most_critical(character, names, *, critical=CRITICAL_THRESHOLD):
    """(name, level) for the worst meter in ``names`` at/above ``critical``,
    or None. Same tie-breaking as ``most_urgent``.
    """
    worst = None
    for name in names:
        level = float(getattr(character, name, 0.0) or 0.0)
        if level >= critical and (worst is None or level > worst[1]):
            worst = (name, level)
    return worst


def level_phrase(level, *, seek=SEEK_THRESHOLD, very=0.85, critical=CRITICAL_THRESHOLD):
    """A coarse severity phrase for a 0.0-1.0 meter level, for status
    displays. Bands default to the same SEEK/CRITICAL split most games will
    tune around; ``very`` is the extra "getting bad" band between them.
    """
    if level >= critical:
        return "critically"
    if level >= very:
        return "very"
    if level >= seek:
        return "somewhat"
    return "a little"


# -- Optional dynamic registry -----------------------------------------
#
# Everything above works with a plain ``names`` list/tuple a game defines
# itself -- no registration required. This registry exists for generic
# tooling (a `list meters` command, a lean tick driver for a game with no
# SUPERS-style bespoke decay(), Area Studio) that wants to discover what
# meters a game declared and their base rates without importing the game.
# It does not replace or drive SUPERS' own ``decay()`` -- that function's
# per-meter branching (Ashen prey freezes, Celestial lifestyle skips,
# vessel-strain plane multipliers, fuel-as-sustenance sync, ...) is 100%
# game fiction and stays entirely in ``supers/needs.py``, unmodified. It
# reads its rates *from* this registry instead of a private duplicate.

_meters: dict[str, dict] = {}


def register_meter(name, base_rate, *, is_fuel=False):
    """Register one meter's base per-tick decay rate.

    Idempotent -- re-registering the same name overwrites its entry. Set
    ``is_fuel=True`` for a meter a game may mirror from a shared fuel tank
    for some characters instead of decaying independently (SUPERS: hunger
    and thirst, for Vampire blood / Celestial Grace / etc.) -- purely
    descriptive metadata here, the mirroring logic itself stays game-side.
    """
    _meters[str(name)] = {"base_rate": float(base_rate), "is_fuel": bool(is_fuel)}


def registered_meters():
    """Frozen snapshot of every registered meter name."""
    return frozenset(_meters)


def meter_rate(name, default=0.0):
    """Registered base rate for ``name``, or ``default`` if unregistered."""
    entry = _meters.get(name)
    return entry["base_rate"] if entry is not None else default


def is_fuel_meter(name):
    """True when ``name`` was registered with ``is_fuel=True``."""
    entry = _meters.get(name)
    return bool(entry) and entry["is_fuel"]


def reset_meters():
    """Clear every registration. Test-only -- production boots never need
    this (registration is idempotent; see ``register_meter``)."""
    _meters.clear()
