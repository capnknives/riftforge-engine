"""
status_conditions.py -- generic combat status-effect ledger (engine kernel).

A "condition" here is nothing more than an id the game layer chose (its
catalog gives that id a label, a multiplier or two, and a flavor hint) plus
a rounds-remaining counter stored in a plain dict on the character. This
module owns the *mechanism* only -- store an id, decay it by one every
round, read back a product-of-multipliers for some named stat key -- and
never looks at what any particular id means. The game layer supplies its
own catalog dict (id -> {label, hint, and zero or more "<stat>_mult" keys})
on every call that needs one, instead of this module holding a hardcoded
catalog global.

Composed onto Character as a plain dict (``combat_conditions``: id ->
rounds remaining), never a subclass -- same "compose data onto Character"
rule as the rest of the engine. This is transient combat state, not
persisted lore: a caller decides when to tick it (end of a combat round)
and when to clear it (fight end / retarget). No networking, no world
model, no character-specific side effects -- stdlib only.

Design: docs/plans/engine_liquid_flavor.md (Wave 3 Part A).
"""

from __future__ import annotations


def ensure_conditions(character):
    """Return character.combat_conditions, creating the dict if absent."""
    conds = getattr(character, "combat_conditions", None)
    # A stray non-dict value (e.g. leftover from a bad save) is treated the
    # same as "missing" -- always hand callers back a real dict to mutate.
    if not isinstance(conds, dict):
        conds = {}
        character.combat_conditions = conds
    return conds


def active(character):
    """A shallow copy of the character's live conditions (id -> rounds)."""
    # A copy, not the live dict, so a caller iterating the result can't
    # accidentally mutate the ledger mid-loop.
    return dict(getattr(character, "combat_conditions", None) or {})


def has(character, condition_id):
    """True when `condition_id` is currently active on the character."""
    conds = getattr(character, "combat_conditions", None) or {}
    return condition_id in conds


def apply(character, condition_id, rounds, *, catalog):
    """Inflict `condition_id` for `rounds` full rounds (refreshing, not stacking).

    A caller that ticks conditions at the END of every round (rather than
    the start) should store one extra round of duration so the same-round
    end tick does not immediately eat the round the condition was applied
    in -- that bookkeeping is the caller's responsibility to add before
    calling this function if it wants that behavior; this kernel just
    stores whatever `rounds` value it is given (refreshing the counter up
    to the higher of the current and new value, never stacking past that).

    Unknown ids (not present in `catalog`) are silently ignored, so a
    stale or mistyped condition id can never raise mid-fight.
    """
    if condition_id not in catalog:
        return
    conds = ensure_conditions(character)
    stored = max(1, int(rounds))
    # Refresh to the longer of "already active" vs "newly applied" -- a
    # second hit while the mark is still up extends it, it never stacks
    # two counters on top of each other.
    conds[condition_id] = max(int(conds.get(condition_id, 0)), stored)


def tick(character):
    """Decay every active condition by one round; drop the expired ones.

    Meant to be called once per character at a fixed point in the combat
    round (the caller decides where -- e.g. after all of that round's
    outcomes are already applied) so a condition inflicted earlier in the
    round is still active for anything that checks it later the same
    round, then ages here.
    """
    conds = getattr(character, "combat_conditions", None)
    if not conds:
        return
    # Iterate over a snapshot of the keys (list(conds)) because the loop
    # body deletes from `conds` while walking it.
    for cid in list(conds):
        conds[cid] = int(conds[cid]) - 1
        if conds[cid] <= 0:
            del conds[cid]


def clear(character):
    """Wipe all active conditions (fight end / retarget)."""
    if getattr(character, "combat_conditions", None):
        character.combat_conditions = {}


def condition_mult(character, key, *, catalog):
    """Product of every active condition's `key` multiplier (1.0 if none).

    `key` names one of the catalog's optional "<stat>_mult" fields (e.g.
    an accuracy or damage multiplier) -- this module has no opinion on
    what stats exist. A condition that does not define `key` simply does
    not contribute a factor.
    """
    conds = getattr(character, "combat_conditions", None)
    if not conds:
        return 1.0
    total = 1.0
    for cid in conds:
        spec = catalog.get(cid)
        if spec and key in spec:
            total *= spec[key]
    return total


def compact_label(character, *, catalog):
    """First active condition's catalog label, or "" when none are active.

    Meant for a compact single-glyph prompt slot -- picks one condition
    deterministically (lowest id, alphabetically) rather than trying to
    summarize every active condition.
    """
    conds = active(character)
    if not conds:
        return ""
    cid = sorted(conds)[0]
    spec = catalog.get(cid, {})
    return str(spec.get("label", cid))


def status_line(character, *, catalog, screenreader=False):
    """Plain-text one-liner of active conditions, or "" when none.

    Never color-only (accessibility): the label and remaining-rounds count
    carry the state on their own, so a screenreader client reads the same
    information a sighted one sees. `screenreader` is accepted for call-
    site parity with other format_* helpers; the output is already plain
    text either way.
    """
    conds = active(character)
    if not conds:
        return ""
    parts = []
    for cid in sorted(conds):
        spec = catalog.get(cid, {})
        label = spec.get("label", cid)
        parts.append(f"{label} ({conds[cid]})")
    return "Afflicted: " + ", ".join(parts)
