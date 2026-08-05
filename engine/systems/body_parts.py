"""
body_parts.py -- generic per-region structural HP state machine.

Dual-pool, not a replacement for aggregate HP: a game's own `hp` field
stays whatever authority it already is (KO, death, clinic admission);
this module tracks a PARALLEL structural pool per `anatomy.REGIONS` entry
on `character.body_parts` (a plain dict, composition over inheritance --
never a subclass). Damage routing is limb-first with overflow: a located
hit's damage is offered to the struck region's own pool first, and only
the surplus a nearly-broken limb can no longer absorb reaches the
caller's own aggregate-damage path (`plan_region_damage` is pure math;
`apply_region_damage` commits the frozen result).

Peeled from ``supers/body_parts.py`` under
docs/plans/riftforge_core_expansion.md Phase 5b -- design SoT
docs/plans/body_parts_system.md. That module keeps every gate check
(``RIFTFORGE_BODY_PARTS``, "is this a limb-tracked actor"), every
SUPERS-specific consumer (Hunter throat finishers, dual-wield blocking,
engagement pacing, Echo spar persistence, anatomy-tree sync, persist blob
codec) as a thin policy layer over this module's pure state mechanism.

One real cross-game dependency: a region's HP ceiling is a fraction of
the character's own aggregate max HP, and "how do I compute a character's
max HP" is 100% game-specific (SUPERS: VIT/growth/tier formula). Register
it once via ``set_max_hp_resolver`` before calling anything here that
needs a region cap (``max_part_hp`` and everything built on it).

stdlib only.
"""

from __future__ import annotations

import random

TIER_HEALTHY = "healthy"
TIER_BRUISED = "bruised"
TIER_WOUNDED = "wounded"
TIER_DISABLED = "disabled"

# Ordered worst-first for status-line-style sorting.
TIER_SEVERITY = (TIER_DISABLED, TIER_WOUNDED, TIER_BRUISED, TIER_HEALTHY)


def tier_for_ratio(ratio):
    """healthy / bruised / wounded / disabled from a region's hp/max ratio.

    100% is healthy (not bruised) -- an exact boundary, not "close to
    full". 0% or below is disabled. Everything else splits at the 50%
    line: (50%, 100%) bruised, (0%, 50%] wounded.
    """
    if ratio >= 1.0:
        return TIER_HEALTHY
    if ratio <= 0.0:
        return TIER_DISABLED
    if ratio <= 0.50:
        return TIER_WOUNDED
    return TIER_BRUISED


# --- Region weight templates -------------------------------------------
# A humanoid_standard split of aggregate max HP across regions -- must sum
# to 1.0 (a body cannot structurally hold more or less HP than its own
# aggregate ceiling). A game is free to register its own template set
# instead of using these defaults; nothing here requires them.
REGION_WEIGHTS = {
    "torso": 0.35,
    "head": 0.15,
    "legs": 0.18,
    "arms": 0.12,
    "neck": 0.08,
    "hands": 0.06,
    "feet": 0.06,
}
assert abs(sum(REGION_WEIGHTS.values()) - 1.0) < 1e-9, (
    "REGION_WEIGHTS must sum to 1.0 -- a body cannot structurally hold "
    "more or less HP than its own aggregate ceiling"
)

# A bilateral variant splitting arms/hands/legs into independent left/
# right pools -- each side gets exactly half of the standard template's
# weight for that limb; torso/head/neck/feet are untouched. A NEW,
# coexisting template -- nothing here requires a character to use it.
BILATERAL_REGION_WEIGHTS = {
    "torso": 0.35,
    "head": 0.15,
    "neck": 0.08,
    "feet": 0.06,
    "left_arm": 0.06,
    "right_arm": 0.06,
    "left_hand": 0.03,
    "right_hand": 0.03,
    "left_leg": 0.09,
    "right_leg": 0.09,
}
assert abs(sum(BILATERAL_REGION_WEIGHTS.values()) - 1.0) < 1e-9, (
    "BILATERAL_REGION_WEIGHTS must sum to 1.0 -- same structural budget "
    "as REGION_WEIGHTS, just split across more pools"
)

# logical (humanoid_standard) region -> its two bilateral-template sides.
BILATERAL_PAIRS = {
    "arms": ("left_arm", "right_arm"),
    "hands": ("left_hand", "right_hand"),
    "legs": ("left_leg", "right_leg"),
}

BILATERAL_TEMPLATE_ID = "humanoid_bilateral_v2"

# Own RNG stream for resolve_struck_region's left/right pick, off any
# caller's seeded global stream (combat sims that seed `random` globally
# must not have side picks shift every other seeded draw).
_SIDE_RNG = random.Random()


def _character_template_id(character):
    """`character`'s anatomy-template id, or None when unset.

    Reads the plain ``character.anatomy`` dict attribute -- no game
    import, just an optional convention a game may or may not use.
    """
    anatomy_state = getattr(character, "anatomy", None)
    if not isinstance(anatomy_state, dict):
        return None
    return anatomy_state.get("template_id")


def character_region_weights(character):
    """REGION_WEIGHTS, unless `character` is on the bilateral template.

    The ONE seam every other function in this module resolves its region
    set through.
    """
    if _character_template_id(character) == BILATERAL_TEMPLATE_ID:
        return BILATERAL_REGION_WEIGHTS
    return REGION_WEIGHTS


def character_regions(character):
    """Tuple of region ids `character` actually tracks, in
    ``character_region_weights``' insertion order.
    """
    return tuple(character_region_weights(character).keys())


def resolve_struck_region(character, region):
    """Map a located hit's LOGICAL region (e.g. "arms") to the actual
    body_parts region `character` tracks. Identity for the standard
    template and for any region that isn't bilateral-split (torso/head/
    neck/feet) -- only arms/hands/legs differ, and only for a character
    on the bilateral template, where this randomly picks a side (own RNG
    stream -- see `_SIDE_RNG` above).
    """
    weights = character_region_weights(character)
    if region in weights:
        return region
    sides = BILATERAL_PAIRS.get(region)
    if not sides:
        return region
    available = [side for side in sides if side in weights]
    if not available:
        return region
    return _SIDE_RNG.choice(available)


def _worst_tier_for_logical_region(character, logical_region):
    """part_tier for `logical_region` (e.g. "legs"), worst-of-both-sides
    when `character` is on the bilateral template and that region is
    split. Lets a caller ask about "legs"/"arms"/"hands" without knowing
    whether this character has one pool or two for that limb.
    """
    weights = character_region_weights(character)
    if logical_region in weights:
        return part_tier(character, logical_region)
    sides = BILATERAL_PAIRS.get(logical_region)
    if not sides:
        return TIER_HEALTHY
    tiers = {
        part_tier(character, side) for side in sides if side in weights
    }
    if not tiers:
        return TIER_HEALTHY
    for tier in TIER_SEVERITY:  # worst-first
        if tier in tiers:
            return tier
    return TIER_HEALTHY


# --- Aggregate-HP dependency ---------------------------------------------

_max_hp_resolver = None


def set_max_hp_resolver(fn):
    """Register fn(character) -> float, the aggregate HP ceiling a
    region's own cap is a fraction of. Required before `max_part_hp` (and
    anything built on it -- `ensure_body_parts`, `part_hp`, `part_tier`,
    `plan_region_damage`, `apply_region_damage`) can be called. Pass None
    to clear (test-only; production boots register once and leave it).
    """
    global _max_hp_resolver
    _max_hp_resolver = fn


def _character_max_hp(character):
    if _max_hp_resolver is None:
        raise RuntimeError(
            "engine.systems.body_parts.set_max_hp_resolver(...) must be "
            "registered before computing a region's HP cap"
        )
    return float(_max_hp_resolver(character))


def max_part_hp(character, region):
    """Structural HP ceiling for one region, derived from CURRENT
    aggregate max HP (via the registered resolver).

    Always live-derived (never itself persisted) so a temporary max-HP
    buff/debuff immediately reshapes every region's ceiling -- callers
    that read `ensure_body_parts` afterward see current HP clamped down
    to match on next read.
    """
    weight = character_region_weights(character).get(region)
    if not weight:
        return 0
    return max(1, int(_character_max_hp(character) * weight))


# --- State (character.body_parts) -----------------------------------------

def ensure_body_parts(character):
    """Return character.body_parts, creating/repairing it if absent.

    Idempotent and safe to call on every read: a missing or malformed
    dict is rebuilt at full health for every region; an existing dict is
    left alone except for the buff-expiry clamp -- a region's hp never
    reports above the CURRENT `max_part_hp`.
    """
    parts = getattr(character, "body_parts", None)
    if not isinstance(parts, dict):
        parts = {}
        character.body_parts = parts
    for region in character_regions(character):
        cap = max_part_hp(character, region)
        part = parts.get(region)
        if not isinstance(part, dict) or not isinstance(
            part.get("hp"), (int, float)
        ):
            parts[region] = {"hp": cap}
            continue
        if part["hp"] > cap:
            part["hp"] = cap
    return parts


def part_hp(character, region):
    """Current structural HP for one region (post buff-expiry clamp)."""
    parts = ensure_body_parts(character)
    part = parts.get(region)
    return int(part["hp"]) if part else 0


def part_tier(character, region):
    """Tier label for one region right now."""
    if region not in character_region_weights(character):
        return TIER_HEALTHY
    cap = max(1, max_part_hp(character, region))
    return tier_for_ratio(part_hp(character, region) / cap)


def plan_region_damage(character, region, amount):
    """Pure math: split `amount` incoming damage between the struck
    region's structural pool and the caller's own aggregate/overflow
    path.

    Limb-first with disabling-hit overflow: the region absorbs up to its
    own current HP; only the part a broken limb can no longer soak
    bleeds through (the caller decides what "bleeds through" means for
    its own aggregate pool). A region already at 0 absorbs nothing
    further -- the whole hit bleeds through. Never mutates `character`;
    `apply_region_damage` commits the frozen result later.
    """
    amount = max(0, int(amount))
    if region not in character_region_weights(character):
        return {
            "region": region,
            "hp_before": 0,
            "hp_after": 0,
            "max_hp": 0,
            "limb_damage": 0,
            "limb_bleed_to_agg": amount,
            "tier_before": TIER_HEALTHY,
            "tier_after": TIER_HEALTHY,
        }
    cap = max(1, max_part_hp(character, region))
    before = min(part_hp(character, region), cap)
    limb_damage = min(before, amount)
    bleed = amount - limb_damage
    after = before - limb_damage
    return {
        "region": region,
        "hp_before": before,
        "hp_after": after,
        "max_hp": cap,
        "limb_damage": limb_damage,
        "limb_bleed_to_agg": bleed,
        "tier_before": tier_for_ratio(before / cap),
        "tier_after": tier_for_ratio(after / cap),
    }


# --- Hook registry -------------------------------------------------------
# A game registers callbacks for these named points; nothing here knows
# what a game does with a tier change or a disabled limb.

HOOK_NAMES = (
    "after_region_damage",  # (character, region, plan)
    "on_limb_disabled",     # (character, region)
    "on_limb_healed",       # (character, region)
    "on_tier_change",       # (character, region, old_tier, new_tier)
    "origin_heal_mult",     # (character) -> float multiplier
)

HOOKS = {name: [] for name in HOOK_NAMES}

DEFAULT_HOOK_PRIORITY = 50


def register_hook(name, callback, priority=DEFAULT_HOOK_PRIORITY):
    """Register `callback` for hook `name`.

    Lower `priority` runs first; hooks sort by priority at FIRE time, not
    registration time, so import/registration order never matters.
    Registering the exact same (priority, callback) pair twice is a
    no-op, so a bootstrap routine documented safe to call more than once
    does not double-register.
    """
    if name not in HOOKS:
        raise ValueError(f"unknown body_parts hook: {name!r}")
    entry = (int(priority), callback)
    if entry in HOOKS[name]:
        return
    HOOKS[name].append(entry)


def _fire_hooks(name, *args):
    for _priority, callback in sorted(
        HOOKS.get(name, ()), key=lambda pair: pair[0]
    ):
        callback(*args)


def origin_heal_mult(character):
    """Passive-regen multiplier for `character` from every registered
    "origin_heal_mult" hook. 1.0 (baseline) with nothing registered or
    applicable. Multiple hooks multiply together.
    """
    mult = 1.0
    for _priority, callback in sorted(
        HOOKS.get("origin_heal_mult", ()), key=lambda pair: pair[0]
    ):
        result = callback(character)
        if result:
            mult *= float(result)
    return mult


def apply_incoming_damage(
    character,
    amount,
    region=None,
    *,
    limb_actor_check=None,
):
    """Limb-first routing for one located hit.

    When ``set_max_hp_resolver`` is registered and ``region`` is a valid
    logical aim zone (``head``, ``torso``, ``arms``, …), soaks damage on
    the struck pool and returns only the overflow for aggregate HP.
    Otherwise returns the full ``amount`` unchanged.

    ``limb_actor_check(character)`` may veto routing (basegame: non-NPC
    only). When omitted, NPCs are skipped.
    """
    amount = max(0, int(amount))
    if amount <= 0 or _max_hp_resolver is None or not region:
        return amount
    if limb_actor_check is not None:
        if not limb_actor_check(character):
            return amount
    elif getattr(character, "is_npc", False):
        return amount
    weights = character_region_weights(character)
    if region not in weights and region not in BILATERAL_PAIRS:
        return amount
    struck = resolve_struck_region(character, region)
    plan = plan_region_damage(character, struck, amount)
    apply_region_damage(character, plan)
    return int(plan.get("limb_bleed_to_agg") or 0)


def apply_region_damage(character, plan):
    """Commit a frozen `plan_region_damage`-shaped result onto `character`.

    Writes the `hp_after` value directly (no re-subtraction) -- trust the
    frozen plan, the same pattern a caller's own brief-apply step uses
    for its aggregate HP write. Fires hooks off the tier transition the
    plan already computed.
    """
    region = plan.get("region")
    if region not in character_region_weights(character):
        return
    parts = ensure_body_parts(character)
    old_tier = plan.get("tier_before") or TIER_HEALTHY
    new_tier = plan.get("tier_after") or TIER_HEALTHY
    parts[region] = {"hp": max(0, int(plan.get("hp_after") or 0))}
    _fire_hooks("after_region_damage", character, region, plan)
    if new_tier == TIER_DISABLED and old_tier != TIER_DISABLED:
        _fire_hooks("on_limb_disabled", character, region)
    if new_tier != old_tier:
        _fire_hooks("on_tier_change", character, region, old_tier, new_tier)


def heal_region(character, region, amount):
    """Restore up to `amount` structural HP to `region`; returns the
    amount actually healed (0..amount, clamped to the region's ceiling).
    Fires the healed/tier-change hooks when `amount` actually moved.

    Pure application helper -- the caller decides `amount`; this
    function only knows how to apply it safely.
    """
    if region not in character_region_weights(character):
        return 0
    cap = max_part_hp(character, region)
    before = min(part_hp(character, region), cap)
    old_tier = tier_for_ratio(before / max(1, cap))
    after = min(cap, before + max(0, int(amount)))
    # Rest / treat / knit drips must not leave a 1-HP sliver that still
    # tiers as bruised while score rounds the limb to 100% (bug #337).
    if after > before and after < cap and (cap - after) <= 1:
        after = cap
    parts = ensure_body_parts(character)
    parts[region] = {"hp": after}
    healed = after - before
    if healed > 0:
        new_tier = tier_for_ratio(after / max(1, cap))
        _fire_hooks("on_limb_healed", character, region)
        if new_tier != old_tier:
            _fire_hooks(
                "on_tier_change", character, region, old_tier, new_tier,
            )
    return healed
