"""
skill_ranks.py -- character-agnostic craft / skill rank math (engine kernel).

Pure numeric helpers for utility profession ranks: clamping, diminishing
gains near cap, success-chance curves, bench recipe gates, odds voice,
and display scaling. No catalogs, affinities, or game-specific stat names.

The game layer keeps catalog loading, character/background affinity,
primary-stat synergy, and sheet formatting; it delegates the math here.
Design: docs/plans/engine_liquid_flavor.md (Wave 1 Part A).
"""

from __future__ import annotations

# Shared craft curve (any workshop/bench profession). Meeting DC is
# "likely" (~85%), not a coin flip. Extra skill still climbs toward
# the 0.95 cap. Non-craft skill checks keep the older 50% at-DC curve
# via SKILL_CHECK_AT_DC_CHANCE -- do not point those at CRAFT_*.
CRAFT_AT_DC_CHANCE = 0.85
SKILL_CHECK_AT_DC_CHANCE = 0.50
CRAFT_CHANCE_PER_MARGIN = 0.01
CRAFT_CHANCE_FLOOR = 0.05
CRAFT_CHANCE_CEILING = 0.95

# Workshop / bench recipes at this DC or below always walk out with
# the piece. Skill still trains. Explicit catalog `always_succeed` and
# `hard_bench` flags override this either way (see below).
SIMPLE_BENCH_ALWAYS_DC = 25.0

# Odds-line band thresholds (chance -> spoken tier, not raw percent).
CRAFT_SURE_MIN = 0.90
CRAFT_LIKELY_MIN = 0.70

# Generic bench odds voice -- reusable by smithing, tailoring, alchemy.
_CRAFT_SURE_LINE = "sure -- you know this job"
_CRAFT_LIKELY_LINE = "likely -- you'll walk out with it"
_CRAFT_LONG_SHOT_LINE = "a long shot"


def clamp_rank(value, cap):
    """Clamp a raw rank into ``0..cap``, coercing bad input to 0.

    Utility skills store floats on a 0-100 (or catalog cap) scale.
    Load-time merges and player data can arrive as None or garbage;
    clamping keeps every consumer on the same safe band.
    """
    try:
        raw = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(float(cap), raw))


def diminishing_gain_mult(before, cap):
    """Slow XP near the skill cap so masters do not sprint the last ranks.

    The catalog uses two bands: past 50% of cap gains are 0.65x, past
    80% they drop to 0.35x. Craft benches pass ``diminishing=False``
    because they already band gains in ``craft_attempt_gain``.
    """
    if before >= cap * 0.8:
        return 0.35
    if before >= cap * 0.5:
        return 0.65
    return 1.0


def clamped_chance(
    margin,
    at_dc,
    *,
    per_margin=CRAFT_CHANCE_PER_MARGIN,
    floor=CRAFT_CHANCE_FLOOR,
    ceiling=CRAFT_CHANCE_CEILING,
):
    """Map skill-minus-DC margin onto a clamped success probability.

    *at_dc* is the chance when margin is exactly zero (meet the DC).
    Each point of margin adds *per_margin*, still bounded by *floor*
    and *ceiling* so novices keep a sliver of hope and masters are not
    guaranteed.
    """
    chance = at_dc + margin * per_margin
    return max(floor, min(ceiling, chance))


def bench_recipe_always_succeeds(spec, *, simple_bench_dc=SIMPLE_BENCH_ALWAYS_DC):
    """True when a workshop / sew recipe always walks out with the piece.

    Order: ``hard_bench`` never auto-succeeds; catalog ``always_succeed``
    (blowgun / kit refill) always does; otherwise DC at or below
    *simple_bench_dc* is treated as simple bench work. Cook and forge
    do not call this helper.
    """
    spec = spec or {}
    if spec.get("hard_bench") is True:
        return False
    if spec.get("always_succeed") is True:
        return True
    try:
        dc = float(spec.get("difficulty", 20) or 20)
    except (TypeError, ValueError):
        dc = 20.0
    return dc <= simple_bench_dc


def bench_recipe_scrap_quality(spec):
    """True when rank is a quality tell (explicit scrap flag only).

    Some always-succeed recipes still earn an early quality mod (fast
    kit refills); ordinary always-succeed recipes make either way but
    do not inherit that bump -- they keep the usual primary-stat
    quality gate in the game layer.
    """
    return bool((spec or {}).get("always_succeed"))


def _band_from_chance(chance, *, sure_min, likely_min):
    """Map a 0..1 chance onto the sure / likely / a long shot band line."""
    if chance >= sure_min:
        return _CRAFT_SURE_LINE
    if chance >= likely_min:
        return _CRAFT_LIKELY_LINE
    return _CRAFT_LONG_SHOT_LINE


def _band_word_from_chance(chance, *, sure_min, likely_min):
    """Short sure / likely / a long shot word for a hard-bench list line.

    Appending the miss-waste clause to the full likely line ("you'll
    walk out with it -- a miss burns half the kit") fights itself, so
    hard-bench recipes use the short word instead of the full line.
    """
    if chance >= sure_min:
        return "sure"
    if chance >= likely_min:
        return "likely"
    return _CRAFT_LONG_SHOT_LINE


def odds_line_from_chance(
    chance,
    *,
    always_succeed=False,
    hard_bench=False,
    miss_waste=None,
    sure_min=CRAFT_SURE_MIN,
    likely_min=CRAFT_LIKELY_MIN,
):
    """Spoken sure / likely / a long shot -- never a percent.

    Scrap (always-succeed) reads as a sure job with no waste line.
    Hard-bench recipes (shotgun / patch kit) use the same chance band
    as other rolls, then warn that a miss burns half the kit.
    Everything else bands on the shared chance curve.
    """
    if always_succeed:
        return _CRAFT_SURE_LINE
    waste = str(miss_waste or "").strip()
    if hard_bench:
        token = waste or "kit"
        word = _band_word_from_chance(chance, sure_min=sure_min, likely_min=likely_min)
        return f"{word} -- a miss burns half the {token}"
    band = _band_from_chance(chance, sure_min=sure_min, likely_min=likely_min)
    if waste and band == _CRAFT_LONG_SHOT_LINE:
        return f"{band} -- a miss burns half the {waste}"
    return band


def display_value(raw, scale):
    """Convert a 0-cap raw rank to the player-facing 0-5 display scale."""
    if scale <= 0:
        return 0.0
    return round(float(raw or 0.0) / scale, 1)


def progress_bar(value, cap, width=10):
    """ASCII progress bar for profession sheet rows (``[#---]`` style)."""
    if cap <= 0:
        return "[" + "-" * width + "]"
    filled = int(round((float(value) / float(cap)) * width))
    filled = max(0, min(width, filled))
    return "[" + "#" * filled + "-" * (width - filled) + "]"
