"""
utility_delay.py -- per-character reuse gates for profession / utility verbs.

Centralizes spam control for skill-training loops (gather, craft, research, …).
Domain modules call ``check`` before work and ``stamp`` after a committed attempt.

Persisted on the character blob as ``utility_verb_delays`` (verb_key -> until_tick).
Scoped keys (``grill:<name>``) share the family delay from ``DEFAULT_DELAY_TICKS``.
"""

from __future__ import annotations

# Default delays as reference ticks at the legacy 3s/tick pace -- verb
# reuse pacing, not a calendar quantity. stamp() converts to actual
# game_time_ticks via ticks_for_wall_seconds at the live gm clock scale
# so cooldowns keep their real-world length at any pace. Tune via audit
# doc.
DEFAULT_DELAY_TICKS = {
    # P0 — gather / craft / research
    "gather": 10,
    "herbalism_pick": 12,
    "workshop_craft": 20,
    "tailor_sew": 18,
    "cook": 15,
    "diner_ticket": 12,
    "library_research": 15,
    "storm_research": 30,
    # P1 — investigation / subterfuge / medic / mechanic
    "investigate": 12,
    "thievery_lock": 15,
    "electronics_bypass": 15,
    "first_aid_treat": 12,
    # Ally KO revive (aid) -- slightly longer hands than treat-stabilize.
    "first_aid_aid": 12,
    # Per-body cool-down after a successful aid stand (scoped via stamp).
    "aid_stood": 10,
    "stealth": 8,
    "mechanic_mend": 20,
    "mechanic_roadside": 25,
    "mechanic_field_gear": 25,
    # ~30s wall at stock 3s heartbeat (reference_ticks * HEARTBEAT_SECONDS).
    "mechanic_field_practice": 10,
    "track": 8,
    "disguise": 4,
    # P2 — kit verbs
    "chemistry_kit": 15,
    "electronics_kit": 15,
    # Social / lifestyle (migrated from ad-hoc cooldowns)
    "grill": 12,
    "haggle": 8,
    "herb_smoke": 40,
}

# Player-facing verb family labels for refusal lines.
VERB_LABELS = {
    "gather": "gathering",
    "herbalism_pick": "picking herbs",
    "workshop_craft": "bench craft",
    "tailor_sew": "tailor bench",
    "cook": "cooking",
    "diner_ticket": "diner tickets",
    "library_research": "library research",
    "storm_research": "storm desk research",
    "investigate": "investigation",
    "thievery_lock": "lock work",
    "electronics_bypass": "electronics bypass",
    "first_aid_treat": "first aid",
    "first_aid_aid": "aiding a fallen ally",
    "aid_stood": "another field revive",
    "stealth": "stealth",
    "mechanic_mend": "gear repair",
    "mechanic_roadside": "roadside repair",
    "mechanic_field_gear": "field gear patch",
    "mechanic_field_practice": "field gear practice",
    "track": "tracking",
    "disguise": "disguise",
    "chemistry_kit": "chemistry kits",
    "electronics_kit": "electronics kits",
    "grill": "grilling a witness",
    "haggle": "haggling",
    "herb_smoke": "smoking herbs",
}


def _ticks(game):
    """Current game heartbeat counter."""
    if game is None:
        return 0
    return int(getattr(game, "game_time_ticks", 0) or 0)


def ensure_defaults(character):
    """Backfill delay dict on older characters."""
    if character is None:
        return
    if not hasattr(character, "utility_verb_delays") or (
        character.utility_verb_delays is None
    ):
        character.utility_verb_delays = {}


def _base_key(verb_key):
    """Family key for scoped gates (e.g. ``grill:Dean`` -> ``grill``)."""
    return str(verb_key or "").split(":", 1)[0]


def delay_ticks(verb_key):
    """Configured delay for ``verb_key`` (falls back to 12 ticks)."""
    base = _base_key(verb_key)
    try:
        raw = DEFAULT_DELAY_TICKS.get(base)
        if raw is None:
            raw = DEFAULT_DELAY_TICKS.get(verb_key, 12)
        return int(raw or 12)
    except (TypeError, ValueError):
        return 12


def until_tick(character, verb_key):
    """Return the stored until-tick for ``verb_key``, or 0."""
    ensure_defaults(character)
    raw = (character.utility_verb_delays or {}).get(verb_key, 0)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def ticks_remaining(character, game, verb_key):
    """How many ticks until ``verb_key`` is ready (0 when clear)."""
    now = _ticks(game)
    left = until_tick(character, verb_key) - now
    return max(0, int(left))


def check(character, game, verb_key):
    """Return (ready: bool, ticks_remaining: int)."""
    left = ticks_remaining(character, game, verb_key)
    return left <= 0, left


def stamp(character, game, verb_key, *, extra_ticks=0):
    """Start the reuse gate for ``verb_key`` from the current tick."""
    ensure_defaults(character)
    now = _ticks(game)
    reference_ticks = delay_ticks(verb_key) + max(0, int(extra_ticks or 0))
    from engine import game_clock_tuning as clock_mod
    duration = clock_mod.ticks_for_wall_seconds(
        reference_ticks * clock_mod.HEARTBEAT_SECONDS, game,
    )
    character.utility_verb_delays[verb_key] = now + duration
    return character.utility_verb_delays[verb_key]


def clear(character, verb_key=None):
    """Clear one gate or the whole map (tests / GM heal)."""
    ensure_defaults(character)
    if verb_key is None:
        character.utility_verb_delays = {}
        return
    character.utility_verb_delays.pop(verb_key, None)


def refusal_message(verb_key, ticks_left, *, screenreader=False, game=None):
    """Short player message when a verb is still on cooldown."""
    _ = screenreader
    base = _base_key(verb_key)
    label = VERB_LABELS.get(base, base.replace("_", " "))
    # Keep copy plain; clients wrap lines.
    if ticks_left <= 0:
        return f"You need a moment before {label} again."
    from engine import game_clock_tuning as clock_mod
    eta = clock_mod.format_tick_cooldown_eta(ticks_left, game)
    return f"You need a moment before {label} again ({eta})."


def gate(character, game, verb_key):
    """Convenience for domain code: (blocked_msg or None, ticks_left).

    When the gate is clear, returns (None, 0). When blocked, returns a
    ready-to-send refusal string and ticks remaining.
    """
    ready, left = check(character, game, verb_key)
    if ready:
        return None, 0
    return refusal_message(verb_key, left, game=game), left


def scoped_key(verb_key, scope):
    """Build a per-target delay key (e.g. grill on one witness)."""
    token = str(scope or "").strip()
    if not token:
        return str(verb_key)
    return f"{verb_key}:{token}"


def begin_attempt(character, game, verb_key):
    """Gate a committed attempt: refuse if busy, else stamp and return None.

    Call at the start of work that costs time whether the roll succeeds or
    fails. Listing verbs / usage errors should return before this.
    """
    blocked, _left = gate(character, game, verb_key)
    if blocked:
        return blocked
    stamp(character, game, verb_key)
    return None
