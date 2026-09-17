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
    "brow": ("head", "high", "brow", "dazed"),
    "eye": ("head", "high", "eye", "blinded"),
    "mouth": ("head", "high", "mouth", "gagged"),
    "throat": ("neck", "high", "throat", "winded"),
    "nape": ("neck", "high", "nape", "dazed"),
    "collarbone": ("torso", "high", "collarbone", "splinted"),
    # Body line -- torso, stamina / organ trauma.
    "solar_plexus": ("torso", "body", "solar plexus", "winded"),
    "liver": ("torso", "body", "liver", "winded"),
    # Bottom of the ribcage (ribs 11-12). Print "lower ribs" -- "floating
    # ribs" is textbook jargon most players do not use (bug report 1196).
    "lower_ribs": ("torso", "body", "lower ribs", "winded"),
    "ribs": ("torso", "body", "ribs", None),
    "sternum": ("torso", "body", "sternum", None),
    "kidney": ("torso", "body", "kidney", "winded"),
    "abdomen": ("torso", "body", "abdomen", None),
    "heart": ("torso", "body", "heart", "shaken"),
    "gut": ("torso", "body", "gut", "cramped"),
    # Arms / hands -- guard height (body line); distinct located marks.
    "shoulder": ("arms", "body", "shoulder", "hampered"),
    "upper_arm": ("arms", "body", "upper arm", "hampered"),
    "elbow": ("arms", "body", "elbow", "hampered"),
    "forearm": ("arms", "body", "forearm", "deadened"),
    "wrist": ("hands", "body", "wrist", "deadened"),
    "knuckles": ("hands", "body", "knuckles", "deadened"),
    "fingers": ("hands", "body", "fingers", "deadened"),
    # Low line -- legs / feet, mobility.
    "hip": ("legs", "low", "hip", "unsteady"),
    "lead_calf": ("legs", "low", "calf", "hobbled"),
    "thigh": ("legs", "low", "thigh", "hobbled"),
    "knee": ("legs", "low", "knee", "hobbled"),
    "shin": ("legs", "low", "shin", None),
    "ankle": ("legs", "low", "ankle", "hobbled"),
    "instep": ("feet", "low", "instep", None),
    "heel": ("feet", "low", "heel", "unsteady"),
}

# Within-line auto-pick weights: arms/hands targets are rarer than torso
# vitals so Dean does not auto-spam forearm every swing. Called shots bypass
# this table entirely (resolve_called_shot wins in choose_target).
_TARGET_AUTO_WEIGHTS = {
    # High -- head/neck vitals dominate; brow/eye/mouth/nape are seasoning.
    "temple": 3, "jaw": 3, "chin": 3, "skull": 2, "nose": 1, "cheekbone": 1,
    "ear": 1, "brow": 1, "eye": 1, "mouth": 1, "throat": 2, "nape": 1,
    "collarbone": 1,
    # Body -- torso bread-and-butter; limbs lighter.
    "solar_plexus": 3, "liver": 2, "lower_ribs": 2, "kidney": 2,
    "ribs": 2, "sternum": 1, "abdomen": 2, "heart": 1, "gut": 2,
    "shoulder": 1, "upper_arm": 1, "elbow": 1, "forearm": 1,
    "wrist": 1, "knuckles": 1, "fingers": 1,
    # Low -- classic leg targets; hip/heel seasoning.
    "thigh": 3, "knee": 3, "lead_calf": 2, "ankle": 2, "shin": 1,
    "hip": 1, "instep": 1, "heel": 1,
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
    if key == "core":
        return "gut"
    # Old textbook name for lower ribs -- still aim if someone types it.
    if key in ("floating_ribs", "floating_rib"):
        return "lower_ribs"
    if key in ("lower_rib",):
        return "lower_ribs"
    if key in ("calf", "lead_calf"):
        return "lead_calf"
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


def _target_in_pool(target_id, exclude_targets=None, allowed_regions=None):
    """True when ``target_id`` may be auto-picked under the given filters.

    ``exclude_targets`` drops named fine marks (lower ribs on a ghost).
    ``allowed_regions`` keeps only marks whose mechanical region exists on
    this body (no throat hit on a template with no neck). Called shots still
    win in ``choose_target`` and are remapped by the game layer if illegal.
    """
    spec = TARGETS.get(target_id)
    if spec is None:
        return False
    if exclude_targets and target_id in exclude_targets:
        return False
    if allowed_regions is not None and spec["region"] not in allowed_regions:
        return False
    return True


def choose_target(reaction=None, press=False, feint_exposed=False,
                  stance="balanced", called_shot=None, rng=None,
                  exclude_targets=None, allowed_regions=None):
    """Pick (target_id, region) for one swing.

    A `called_shot` (already a raw player aim string) wins when it
    resolves to a real target; otherwise the target is auto-picked by
    attack-line weights that shift with the fight state (see
    `_line_weights`). `rng` defaults to this module's dedicated
    `_AUTO_RNG`; tests pass an explicit seeded `rng` to pin the choice.

    ``exclude_targets`` / ``allowed_regions`` filter the auto-pick pool so a
    non-humanoid defender is not rolled a liver or lower-ribs mark.
    Called shots still return as resolved -- the caller legalizes them.

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

    def _pool_for(line):
        return [
            tid for tid in (_TARGETS_BY_LINE.get(line) or ())
            if _target_in_pool(tid, exclude_targets, allowed_regions)
        ]

    lines = [ln for ln in weights if _pool_for(ln)]
    if not lines:
        # Filtered tables empty (mist body, odd template) -- still land
        # somewhere narratable rather than crashing the swing.
        for fallback in ("abdomen", "gut", "skull", "thigh"):
            if _target_in_pool(fallback, exclude_targets, allowed_regions):
                return fallback, TARGETS[fallback]["region"]
        for tid, spec in TARGETS.items():
            if _target_in_pool(tid, exclude_targets, allowed_regions):
                return tid, spec["region"]
        return "abdomen", "torso"
    chosen_line = rng.choices(
        lines, weights=[weights[ln] for ln in lines], k=1
    )[0]
    pool = _pool_for(chosen_line)
    pick_weights = [_TARGET_AUTO_WEIGHTS.get(tid, 1) for tid in pool]
    target = rng.choices(pool, weights=pick_weights, k=1)[0]
    return target, TARGETS[target]["region"]
