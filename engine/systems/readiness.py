"""
readiness.py -- Balance and Equilibrium cooldown deadlines.

docs/plans/fast_paced_combat_engine.md decision #17 + hard part §10.1:

  * ``character.balance_ready_at`` / ``character.equilibrium_ready_at`` are
    wall-clock deadlines (via injectable ``now_fn``, default
    ``time.monotonic`` -- §10.5). Spending an action sets a new deadline;
    there is no regenerating pool and no per-tick fractional regen.
  * These deadlines gate a character's own **offense/aim queue only**.
    They must NEVER gate defense (manual or auto). Defense is keyed only
    to the incoming Telegraph's window.

FIN shortens Balance recovery; FOC shortens Equilibrium recovery. Games
can swap the duration math via ``set_recovery_provider``.
"""

from __future__ import annotations

import time

TRACK_BALANCE = "balance"
TRACK_EQUILIBRIUM = "equilibrium"

# Attr names on Character (composed data, never subclasses).
_ATTR = {
    TRACK_BALANCE: "balance_ready_at",
    TRACK_EQUILIBRIUM: "equilibrium_ready_at",
}

# Optional game hook: (character, track, base_duration) -> duration seconds.
_recovery_provider = None


def set_recovery_provider(fn):
    """Register ``fn(character, track, base_duration) -> duration`` or None."""
    global _recovery_provider
    _recovery_provider = fn


def _now(now_fn):
    """Resolve the injectable clock -- never call monotonic unconditionally."""
    return (now_fn or time.monotonic)()


def _stat(character, name, default=5.0):
    """Read one primary from ``character.stats`` with a safe default."""
    stats = getattr(character, "stats", None) or {}
    try:
        return float(stats.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def default_recovery_duration(character, track, base_duration):
    """Engine reference recovery: FIN speeds Balance, FOC speeds Equilibrium.

    Multiplier = 1 / (1 + 0.04 * stat) so default-5 stats ~0.83x base, high
    stats approach but never reach zero recovery. Pure math -- no mutation.
    """
    if track == TRACK_BALANCE:
        fin = _stat(character, "FIN")
        mult = 1.0 / (1.0 + 0.04 * fin)
    elif track == TRACK_EQUILIBRIUM:
        foc = _stat(character, "FOC")
        mult = 1.0 / (1.0 + 0.04 * foc)
    else:
        mult = 1.0
    return max(0.05, float(base_duration) * mult)


def is_ready(character, track, *, now_fn=None):
    """True when ``character`` may spend ``track`` right now.

    Missing attr / None deadline means ready (never spent yet).
    """
    attr = _ATTR.get(track)
    if attr is None:
        return True
    deadline = getattr(character, attr, None)
    if deadline is None:
        return True
    return _now(now_fn) >= float(deadline)


def spend(character, track, base_duration, *, now_fn=None):
    """Set ``character``'s readiness deadline for ``track``.

    Returns the absolute deadline that was written. Defense code must not
    call this -- Balance/Equilibrium are offense/aim only (§10.1).
    """
    attr = _ATTR.get(track)
    if attr is None:
        return None
    provider = _recovery_provider or default_recovery_duration
    duration = provider(character, track, base_duration)
    deadline = _now(now_fn) + float(duration)
    setattr(character, attr, deadline)
    return deadline


def spend_balance(character, base_duration, *, now_fn=None):
    """Spend Physical Balance -- somatic strikes, dodge-as-movement, etc."""
    return spend(character, TRACK_BALANCE, base_duration, now_fn=now_fn)


def spend_equilibrium(character, base_duration, *, now_fn=None):
    """Spend Mental Equilibrium -- aim, precision, complex prep."""
    return spend(character, TRACK_EQUILIBRIUM, base_duration, now_fn=now_fn)


def ensure_defaults(character):
    """Stamp readiness attrs on ``character`` if missing (idempotent)."""
    if not hasattr(character, "balance_ready_at"):
        character.balance_ready_at = None
    if not hasattr(character, "equilibrium_ready_at"):
        character.equilibrium_ready_at = None
