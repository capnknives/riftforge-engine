"""
anatomy.py -- generic body-region model for located combat.

Adds a *where* axis to a swing without inventing a new taxonomy per game:

  * Canonical REGIONS -- the mechanical coordinate system (head-to-foot).
  * Fine TARGETS (temple, liver, lead calf, ...) -- the prose + rider
    granularity. Each maps to exactly one region and one attack LINE
    (high / body / low / grapple), so a defense answering a swing can
    stay geometrically coherent (a dodge answering a low sweep hops back;
    it never "ducks under").

Real human anatomy and universal combat vocabulary, not any one game's
lore -- passes the promotion test (docs/plans/riftforge_core_expansion.md
"another fantasy MUD could use it without Supernatural names"). Peeled
from ``supers/anatomy.py`` under that charter's Phase 5b; that module's
own SUPERS-specific bridges (which armor slot / tattoo body-part overlaps
a region -- ``items.py``/``tattoo.py`` concepts this module has no
opinion on) stay there as thin wrappers.

Nothing here touches a live Character, the network, or combat math -- pure
data + lookups. stdlib only.
"""

from __future__ import annotations

import random

# Dedicated RNG stream for auto-targeting, off any caller's seeded global
# stream (a game running deterministic combat sims must not have target
# picks shift every other seeded draw). Tests that need a fixed pick pass
# ``rng=`` explicitly to ``choose_target``.
_AUTO_RNG = random.Random()


# --- Canonical regions (mechanical layer) -----------------------------------
# Left/right-agnostic on purpose: "lead calf" / "left temple" is prose
# flavor, not a mechanical axis. Ordered head-to-foot for stable display.
REGIONS = (
    "head",
    "neck",
    "torso",
    "arms",
    "hands",
    "legs",
    "feet",
)

# Attack "lines". "grapple" is reserved for a future pass (no grapple
# TARGETS are seeded by default) but named here so a reaction-pool
# fallback can already recognize it.
LINES = ("high", "body", "low", "grapple")


# --- Fine targets (prose + rider layer) -------------------------------------
# Authored compactly as (region, line, noun, rider) tuples, then expanded
# into TARGETS below. rider is the located-condition id a solid landed hit
# here inflicts, or None -- only "vital" targets carry one; interpreting
# and applying a rider id is entirely the game's own business (this module
# just carries the id through).
_TARGET_SPECS = {
    # High line -- head / neck, concussive.
    "temple": ("head", "high", "temple", "staggered"),
    "jaw": ("head", "high", "jaw", "staggered"),
    "chin": ("head", "high", "chin", "staggered"),
    "nose": ("head", "high", "nose", None),
    "cheekbone": ("head", "high", "cheekbone", None),
    "skull": ("head", "high", "skull", "staggered"),
    "ear": ("head", "high", "ear", None),
    "throat": ("neck", "high", "throat", "winded"),
    # Body line -- torso, stamina / organ trauma.
    "solar_plexus": ("torso", "body", "solar plexus", "winded"),
    "liver": ("torso", "body", "liver", "winded"),
    "floating_ribs": ("torso", "body", "floating ribs", "winded"),
    "ribs": ("torso", "body", "ribs", None),
    "sternum": ("torso", "body", "sternum", None),
    "kidney": ("torso", "body", "kidney", "winded"),
    "abdomen": ("torso", "body", "abdomen", None),
    # Low line -- legs / feet, mobility.
    "lead_calf": ("legs", "low", "lead calf", "hobbled"),
    "thigh": ("legs", "low", "thigh", "hobbled"),
    "knee": ("legs", "low", "knee", "hobbled"),
    "shin": ("legs", "low", "shin", None),
    "ankle": ("legs", "low", "ankle", "hobbled"),
    "instep": ("feet", "low", "instep", None),
}

TARGETS = {
    tid: {"region": region, "line": line, "noun": noun, "rider": rider}
    for tid, (region, line, noun, rider) in _TARGET_SPECS.items()
}


# --- Auto-target weighting ---------------------------------------------------
# Base line distribution for an ordinary swing: body strikes are the bread
# and butter, head shots less often, leg kicks least.
_DEFAULT_LINE_WEIGHTS = {"high": 0.30, "body": 0.45, "low": 0.25}
# A committed swing (press / critical / an exposed foe) reaches for the
# head -- the fight state has opened a concussive window.
_AGGRESSIVE_LINE_WEIGHTS = {"high": 0.50, "body": 0.35, "low": 0.15}
# A cautious attacker (defensive stance) works the safer body/leg lines.
_DEFENSIVE_LINE_WEIGHTS = {"high": 0.20, "body": 0.50, "low": 0.30}

# Precomputed target-id lists per line (grapple has none seeded by default).
_TARGETS_BY_LINE = {
    line: [tid for tid, spec in TARGETS.items() if spec["line"] == line]
    for line in LINES
}


def region_for_target(target):
    """Canonical region for a fine target id, or None if unknown."""
    spec = TARGETS.get(target)
    return spec["region"] if spec else None


def line_for_target(target):
    """Attack line ("high"/"body"/"low") for a target, or None if unknown."""
    spec = TARGETS.get(target)
    return spec["line"] if spec else None


def target_noun(target):
    """Display noun for a target id (e.g. "solar plexus"), or None."""
    spec = TARGETS.get(target)
    return spec["noun"] if spec else None


def rider_for_target(target):
    """The located-condition id a solid hit on this target inflicts, or
    None. Only vital targets carry a rider; whether a given landed hit is
    solid enough to actually apply it is entirely the caller's call.
    """
    spec = TARGETS.get(target)
    return spec["rider"] if spec else None


def resolve_called_shot(value):
    """Normalize a player's aim string to a concrete target id, or None.

    Accepts a specific target id ("liver"), a target's display noun
    ("solar plexus"), or a whole region ("torso"/"head"/"legs"). A region
    resolves to a representative target within it, so `aim torso` behaves
    without the player memorizing organ names. Unknown input returns None
    (the caller falls back to auto-targeting).
    """
    if not value:
        return None
    key = str(value).strip().lower().replace(" ", "_")
    if key in TARGETS:
        return key
    for tid, spec in TARGETS.items():
        if spec["noun"].replace(" ", "_") == key:
            return tid
    if key in REGIONS:
        in_region = [
            tid for tid, spec in TARGETS.items() if spec["region"] == key
        ]
        if not in_region:
            return None
        with_rider = [tid for tid in in_region if TARGETS[tid]["rider"]]
        return (with_rider or in_region)[0]
    return None


def _line_weights(reaction=None, press=False, feint_exposed=False,
                  stance="balanced"):
    """Which line-weight table this swing draws from.

    A committed/opened swing (press, critical, or an already-exposed foe)
    reaches high; a defensive attacker plays it safe; otherwise the
    default body-heavy spread.
    """
    if press or feint_exposed or reaction == "critical":
        return _AGGRESSIVE_LINE_WEIGHTS
    if stance == "defensive":
        return _DEFENSIVE_LINE_WEIGHTS
    return _DEFAULT_LINE_WEIGHTS


def choose_target(reaction=None, press=False, feint_exposed=False,
                  stance="balanced", called_shot=None, rng=None):
    """Pick (target_id, region) for one swing.

    A `called_shot` (already a raw player aim string) wins when it
    resolves to a real target; otherwise the target is auto-picked by
    attack-line weights that shift with the fight state (see
    `_line_weights`). `rng` defaults to this module's dedicated
    `_AUTO_RNG`; tests pass an explicit seeded `rng` to pin the choice.

    Never returns None: if the seeded tables were somehow empty, falls
    back to a plain torso hit so a fight can always narrate a location.
    """
    if rng is None:
        rng = _AUTO_RNG
    called = resolve_called_shot(called_shot)
    if called:
        return called, TARGETS[called]["region"]

    weights = _line_weights(
        reaction=reaction, press=press,
        feint_exposed=feint_exposed, stance=stance,
    )
    lines = [ln for ln in weights if _TARGETS_BY_LINE.get(ln)]
    if not lines:
        return "abdomen", "torso"
    chosen_line = rng.choices(
        lines, weights=[weights[ln] for ln in lines], k=1
    )[0]
    target = rng.choice(_TARGETS_BY_LINE[chosen_line])
    return target, TARGETS[target]["region"]
