"""
luck.py -- generic luck bias math for probability nudges (engine kernel).

Pure helpers: soul/surplus/aura layers combine into one bias float, then
``apply_luck_to_probability`` scales a base chance. No SUPERS imports --
game policy (meter storage, Fortuna hall, Rabbit's Foot) lives in
``supers/luck.py``.

Design: docs/plans/luck_stat_system.md + docs/plans/luck_stat_build.md.
"""

from __future__ import annotations

from dataclasses import dataclass


# Default per-roll cap passed from SUPERS tuning (LUCK_BIAS_CAP).
_DEFAULT_CAP = 0.15


@dataclass(frozen=True)
class LuckContext:
    """Tag + weight for one roll domain (combat, wage tip, training, …)."""

    tag: str
    weight: float = 1.0
    cap: float = _DEFAULT_CAP


def clamp_soul(value: int, *, lo: int = -100, hi: int = 100) -> int:
    """Clamp soul luck to the mortal meter band."""
    return max(lo, min(hi, int(value)))


def soul_bias(value: int, *, weight: float, cap: float) -> float:
    """Soul luck contribution: (luck/100) * domain weight, clamped to ±cap.

    Do not also multiply by cap -- that double-damped luck 100 combat down
    to ~2% and made the chargen table unfeelable next to surplus.
    """
    raw = (clamp_soul(value) / 100.0) * float(weight)
    bound = abs(float(cap))
    return max(-bound, min(bound, raw))


def surplus_bias(
    surplus: int,
    *,
    roll_cap: int = 40,
    mult: float = 0.003,
) -> float:
    """Diminishing surplus roll layer (only the borrow pool below roll_cap counts)."""
    capped = max(0, min(int(surplus), int(roll_cap)))
    return capped * float(mult)


def combine_bias(soul: float, surplus: float, aura: float) -> float:
    """Sum the three bias layers into one float."""
    return float(soul) + float(surplus) + float(aura)


def apply_luck_to_probability(p: float, bias: float) -> float:
    """Scale base probability by (1 + bias) and clamp to [0.0, 1.0]."""
    adjusted = float(p) * (1.0 + float(bias))
    return max(0.0, min(1.0, adjusted))


def roll_serendipity_gate(rng, chance: float) -> bool:
    """Return True when a serendipity / overload roll fires (injectable rng)."""
    roll = rng() if callable(rng) else rng.random()
    return float(roll) < float(chance)
