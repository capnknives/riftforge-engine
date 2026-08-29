"""
wage_curve.py -- gig-work hourly/career pay and roll-bonus math (engine kernel).

Pure curve-math helpers for job shift pay and exhaustion: a stat-driven
hourly multiplier band, a career-grade stat bump, a generic
chance-then-amount roll (tips, clean-shift bonuses -- same shape, two
different callers), and a physical-exhaustion-per-hour curve. Every
function here takes an already-resolved bucket name and stat value --
no job catalog, no job-id lookup tables, and no character/class names.

The game layer keeps its own job-id -> bucket frozensets (irreducibly
game-specific job titles), resolves each character's primary stat, and
delegates the actual math here. Design: docs/plans/engine_liquid_flavor.md
(Wave 2 Part C).
"""

from __future__ import annotations

import random


def hourly_mult(bucket, stat_value):
    """Stat nudge on base hourly rate for one job bucket.

    Service and precision buckets share the same 0.85..1.15 band
    (0.90 base + 0.005 per point of stat past 5); physical uses a
    gentler 0.90..1.10 band (0.94 base + 0.003 per point) since VIT
    there mostly protects against exhaustion, not raw pay. "none"
    (Cadence-only jobs with no stat hook) always returns 1.0.
    """
    if bucket in ("service", "precision"):
        return min(1.15, max(0.85, 0.90 + max(0.0, stat_value - 5.0) * 0.005))
    if bucket == "physical":
        return min(1.10, max(0.90, 0.94 + max(0.0, stat_value - 5.0) * 0.003))
    return 1.0


def career_pay_mult(bucket, stat_value, grade):
    """Regular/Senior career grades gain up to +10% from the bucket stat.

    *grade* below 1 (not yet a career hire) or bucket "none" both
    return 1.0 (no bump). Senior (grade >= 2) gets the full per-point
    bump; Regular (grade 1) gets half -- same shape for every bucket,
    the stat itself is already resolved by the caller.
    """
    if grade < 1 or bucket == "none":
        return 1.0
    scale = 1.0 if grade >= 2 else 0.5
    bump = max(0.0, stat_value - 5.0) * 0.002 * scale
    return min(1.10, 1.0 + bump)


def roll_bonus(
    base_chance,
    chance_per_stat,
    stat_value,
    *,
    cap_chance,
    amount_range,
    rng=random.random,
):
    """Chance-then-randint roll shared by tips and clean-shift bonuses.

    Rolls *rng()* against a chance that starts at *base_chance* and
    climbs by *chance_per_stat* per point of stat past 5, capped at
    *cap_chance*. On a hit, returns a random int in *amount_range*
    (inclusive ``(low, high)`` tuple, same as ``random.randint`` args);
    on a miss, returns 0. *rng* is injectable for deterministic tests.
    """
    chance = base_chance + max(0.0, stat_value - 5.0) * chance_per_stat
    chance = min(cap_chance, chance)
    if rng() > chance:
        return 0
    low, high = amount_range
    return random.randint(low, high)


def physical_exhaustion_amount(hours, stat_value, *, per_hour_base):
    """Exhaustion added for a physical shift; VIT-like *stat_value* softens it.

    The softening multiplier floors at 0.55 (even a very high stat
    shift still tires you some) and starts easing at 1.0 for anyone at
    or below stat 5. Returns 0.0 for a non-positive *hours*.
    """
    try:
        hours_val = float(hours or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if hours_val <= 0.0:
        return 0.0
    mult = max(0.55, 1.0 - max(0.0, stat_value - 5.0) * 0.008)
    return per_hour_base * hours_val * mult
