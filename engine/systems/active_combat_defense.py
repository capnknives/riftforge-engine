"""
active_combat_defense.py -- auto-defense + proficiency for twitch combat.

docs/plans/fast_paced_combat_engine.md decisions #12–#16 + hard parts:

  * Always-on passive roll for dodge/block unless that type is toggled off.
  * Weighted-random among viable types via ``combat_core.roll_weighted_outcome``
    (never always-optimal -- § decision #15).
  * Parry is **manual-only** -- never in the auto-defense weighted pool (#16).
  * ``character.defense_proficiency`` climbs from attempts of that type (#14).
  * Defense never checks Balance/Equilibrium (§10.1).

Manual defense is the upgrade path (#13): better odds than the auto roll.
"""

from __future__ import annotations

from engine.systems import combat_core

# Defense kinds. Auto pool is dodge+block only; parry stays manual.
DEFENSE_DODGE = "dodge"
DEFENSE_BLOCK = "block"
DEFENSE_PARRY = "parry"
AUTO_DEFENSES = (DEFENSE_DODGE, DEFENSE_BLOCK)
ALL_DEFENSES = (DEFENSE_DODGE, DEFENSE_BLOCK, DEFENSE_PARRY)

# Attrs on Character (composed data).
PROFICIENCY_ATTR = "defense_proficiency"
AUTO_OFF_ATTR = "auto_defense_off"  # set of disabled kinds, e.g. {"block"}

# Preferred auto-defense bias by incoming attack verb family.
# Heavier kinetic leans block; sweeping/leg leans dodge. Weights feed
# roll_weighted_outcome -- best option gets the largest share, not exclusive.
_VERB_BIAS = {
    "jab": {DEFENSE_DODGE: 0.55, DEFENSE_BLOCK: 0.35},
    "punch": {DEFENSE_DODGE: 0.40, DEFENSE_BLOCK: 0.50},
    "uppercut": {DEFENSE_DODGE: 0.35, DEFENSE_BLOCK: 0.55},
    "headbutt": {DEFENSE_DODGE: 0.30, DEFENSE_BLOCK: 0.60},
    "sweep": {DEFENSE_DODGE: 0.65, DEFENSE_BLOCK: 0.25},
    "kick": {DEFENSE_DODGE: 0.60, DEFENSE_BLOCK: 0.30},
    "legkick": {DEFENSE_DODGE: 0.60, DEFENSE_BLOCK: 0.30},
}
_DEFAULT_BIAS = {DEFENSE_DODGE: 0.45, DEFENSE_BLOCK: 0.45}

MAX_PROFICIENCY = 50
PROFICIENCY_STEP = 1


def ensure_defaults(character):
    """Idempotent composed-data stamps for defense state."""
    if getattr(character, PROFICIENCY_ATTR, None) is None:
        setattr(character, PROFICIENCY_ATTR, {
            DEFENSE_DODGE: 0,
            DEFENSE_BLOCK: 0,
            DEFENSE_PARRY: 0,
        })
    if getattr(character, AUTO_OFF_ATTR, None) is None:
        setattr(character, AUTO_OFF_ATTR, set())


def _stat(character, name, default=5.0):
    stats = getattr(character, "stats", None) or {}
    try:
        return float(stats.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def proficiency(character, kind):
    """Current proficiency rank for ``kind`` (0..MAX_PROFICIENCY)."""
    ensure_defaults(character)
    bag = getattr(character, PROFICIENCY_ATTR) or {}
    try:
        return int(bag.get(kind, 0) or 0)
    except (TypeError, ValueError):
        return 0


def bump_proficiency(character, kind):
    """Climb proficiency for an attempted defense type (manual or auto)."""
    if kind not in ALL_DEFENSES:
        return
    ensure_defaults(character)
    bag = getattr(character, PROFICIENCY_ATTR)
    current = proficiency(character, kind)
    bag[kind] = min(MAX_PROFICIENCY, current + PROFICIENCY_STEP)


def set_auto_defense(character, kind, enabled):
    """Enable or disable auto-``kind`` (``autoblock off`` style)."""
    if kind not in AUTO_DEFENSES:
        return False
    ensure_defaults(character)
    off = getattr(character, AUTO_OFF_ATTR)
    if enabled:
        off.discard(kind)
    else:
        off.add(kind)
    return True


def auto_defense_enabled(character, kind):
    """True when auto-``kind`` is allowed to roll for this character."""
    ensure_defaults(character)
    return kind not in getattr(character, AUTO_OFF_ATTR)


def success_chance(character, kind, *, manual=False):
    """Probability that a chosen defense succeeds against a hit.

    Stat sets the band; proficiency nudges within it. Manual attempts get
    a flat upgrade bonus (decision #13) so typing is better than auto.
    """
    prof = proficiency(character, kind)
    if kind == DEFENSE_DODGE:
        base = 0.25 + 0.03 * _stat(character, "FIN") + 0.004 * prof
    elif kind == DEFENSE_BLOCK:
        base = 0.30 + 0.025 * _stat(character, "VIT") + 0.004 * prof
    elif kind == DEFENSE_PARRY:
        blend = 0.5 * _stat(character, "FOC") + 0.5 * _stat(character, "FIN")
        base = 0.15 + 0.025 * blend + 0.005 * prof
    else:
        base = 0.25
    if manual:
        base += 0.12
    return max(0.05, min(0.90, base))


def choose_auto_defense(character, verb, *, rng=None):
    """Pick dodge or block via weighted roll, or None if every type is off.

    Never returns ``parry`` (decision #16). Uses situational verb bias plus
    proficiency so the "best" option is favored but not guaranteed.
    """
    ensure_defaults(character)
    bias = dict(_VERB_BIAS.get(verb, _DEFAULT_BIAS))
    weights = []
    for kind in AUTO_DEFENSES:
        if not auto_defense_enabled(character, kind):
            continue
        # Proficiency gently tilts the weight toward trained defenses.
        weight = bias.get(kind, 0.3) * (1.0 + 0.02 * proficiency(character, kind))
        if weight > 0:
            weights.append((kind, weight))
    if not weights:
        return None
    # Reserve a tiny share so the roll always lands on a named defense
    # (no "missed the pick" default) -- default is the first weight's name.
    return combat_core.roll_weighted_outcome(
        weights,
        default=weights[0][0],
        reserve=0.0,
        rng=rng,
    )


def roll_defense_success(character, kind, *, manual=False, rng=None):
    """True when the defense attempt succeeds.

    ``rng`` is the same 0..1 seam ``roll_weighted_outcome`` accepts.
    """
    roll_fn = rng if rng is not None else __import__("random").random
    return float(roll_fn()) < success_chance(character, kind, manual=manual)


def block_mitigation(character):
    """Flat damage reduction when a block succeeds (scales with VIT)."""
    return 4.0 + 0.8 * _stat(character, "VIT")
