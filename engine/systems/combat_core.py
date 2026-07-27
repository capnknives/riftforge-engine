"""combat_core.py -- the engine's generic weighted-outcome roll mechanism.

Every combat system (or any contest that needs to pick one outcome from
several competing chances -- a skill check, a persuasion roll) eventually
needs the same shape: a handful of named outcomes each with a 0..1 chance,
one guaranteed "ordinary" default so a heavily outmatched contestant never
sees their normal outcome crowded out entirely, and a single roll that picks
exactly one. That mechanism -- not any particular game's idea of "miss",
"dodge", "critical", or what feeds those chances -- is what lives here.

SUPERS' own hit/dodge/block/critical formulas (``supers/combat.py``'s
``_roll_reaction``) compute the weights from FOC/FIN/POW/RES-derived
accuracy/evasion/crit/block chances -- 100% game content, staying exactly
where it is (docs/plans/two_repo_purity.md Phase 7 Stage 7's boundary rule:
tuned game formulas don't move, just like ``accuracy()``/``evasion()``
stayed in ``supers/stats.py`` after the six-primary spine moved to
``engine/stats.py`` in Stage A1). This module only owns the roll itself.

Pure math + a single RNG call: no networking, no database, no game loop,
zero ``supers`` imports.
"""

from __future__ import annotations

import random


def roll_weighted_outcome(weights, *, default="hit", reserve=0.0, rng=None):
    """Roll one outcome from ordered ``(name, weight)`` pairs.

    Weights are proportionally rescaled if their sum would eat more than
    ``1 - reserve`` of the roll -- guaranteeing ``default`` always keeps at
    least ``reserve`` share of the roll, so a contestant who looks
    completely outmatched on paper still has *some* chance of an ordinary
    result. ``rng`` defaults to ``random.random``; pass a seeded callable
    (e.g. a fixed sequence) for deterministic tests.

    Returns ``default`` if no weighted bucket claims the roll -- the normal
    outcome when the weights don't sum to the full unit interval.
    """
    roll_fn = rng if rng is not None else random.random
    # A plain accumulation loop, not the `sum()` builtin: CPython's `sum()`
    # uses compensated (Neumaier) summation for floats, which is *more*
    # precise than naive sequential `+` but not bit-identical to it -- and
    # callers migrating an existing naive total += weight tally onto this
    # helper need the roll to land on the exact same side of a threshold
    # it always has, not a more-correct-but-different one.
    total = 0.0
    for _name, weight in weights:
        total += weight
    room = 1.0 - reserve
    scale = (room / total) if total > room and total > 0 else 1.0

    roll = roll_fn()
    cumulative = 0.0
    for name, weight in weights:
        cumulative += weight * scale
        if roll < cumulative:
            return name
    return default
