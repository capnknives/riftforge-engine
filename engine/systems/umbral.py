"""
umbral.py -- night-shroud stealth toggle for the public engine demo.

Structurally mirrors ``aerial.py``'s ``ensure_stellar_defaults`` /
``is_stellar`` shape, but the mechanic itself is a stealth toggle, not a
flight-tier ladder. Umbral's job is to be the first real consumer of the
engine's existing ``stealth_active`` / ``hooks.can_notice_stealth``
presence hook (see ``engine/command_support.py`` -- the comment there
already names "umbral lurk"). Games wire the hook from their own
bootstrap; this module only stamps fields and drains charge.

Stdlib only; zero ``supers`` imports.
"""

from __future__ import annotations

# Drain per tick while shrouded -- same ballpark as aerial's globe step
# so a full charge lasts ~20 heartbeats (~a short real-world minute at
# typical tick pacing), enough to demo the loop without hanging forever.
UMBRAL_CHARGE_STEP = 0.05


def ensure_umbral_defaults(character):
    """Attach Umbral fields if missing (safe to call repeatedly)."""
    if not hasattr(character, "bg_umbral"):
        character.bg_umbral = False
    if not hasattr(character, "umbral_charge"):
        character.umbral_charge = 1.0
    if not hasattr(character, "umbral_shrouded"):
        character.umbral_shrouded = False


def is_umbral(character):
    """True when this character took the Umbral demo path."""
    ensure_umbral_defaults(character)
    return bool(getattr(character, "bg_umbral", False))


def add_umbral_charge(character, delta):
    """Adjust demo Umbral Charge (0..1)."""
    ensure_umbral_defaults(character)
    character.umbral_charge = max(
        0.0, min(1.0, float(character.umbral_charge) + float(delta))
    )


def clear_shroud(character):
    """Drop shroud state without messaging (tick auto-clear, disconnect)."""
    ensure_umbral_defaults(character)
    character.umbral_shrouded = False
    character.stealth_active = False


def _day_period(game):
    """Return the calendar day_period string for ``game``, or ``"day"``."""
    from engine import game_calendar

    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    return game_calendar.breakdown(ticks).get("day_period", "day")


def cmd_shroud(character, args, game):
    """Wrap yourself in night -- hide from look / presence while charge lasts.

    Requires the Umbral path, night or dusk on the game calendar, and a
    positive ``umbral_charge``. Sets both ``stealth_active`` (the generic
    engine flag ``command_support`` already reads) and ``umbral_shrouded``
    (so the basegame ``can_notice_stealth`` hook can tell Umbral apart
    from any future mundane hide verb).
    """
    del args  # no args for this verb
    ensure_umbral_defaults(character)
    if not is_umbral(character):
        character.session.send(
            "Only Umbral characters can pull the night around them."
        )
        return
    if character.umbral_shrouded:
        character.session.send("You are already shrouded in shadow.")
        return
    period = _day_period(game)
    if period not in ("night", "dusk"):
        character.session.send(
            "There is too much light to shroud -- wait for dusk or night."
        )
        return
    if float(character.umbral_charge) <= 0.0:
        character.session.send(
            "Your Umbral Charge is spent -- you cannot shroud right now."
        )
        return
    character.stealth_active = True
    character.umbral_shrouded = True
    character.session.send(
        "Shadows gather around you. You fade from ordinary sight."
    )


def cmd_unshroud(character, args, game):
    """Drop the night shroud and become visible again."""
    del args, game
    ensure_umbral_defaults(character)
    if not character.umbral_shrouded and not getattr(
        character, "stealth_active", False
    ):
        character.session.send("You are not shrouded.")
        return
    clear_shroud(character)
    character.session.send("The shadows fall away. You stand in plain sight.")


def tick(game):
    """Drain Umbral Charge for shrouded characters; auto-unshroud at 0.

    Same ``iter_characters`` walk as ``clinic.tick`` / ``justice.tick`` --
    one pass per heartbeat, no threads.
    """
    from engine.char_index import iter_characters

    for character in list(iter_characters(game)):
        ensure_umbral_defaults(character)
        if not character.umbral_shrouded:
            continue
        add_umbral_charge(character, -UMBRAL_CHARGE_STEP)
        if float(character.umbral_charge) <= 0.0:
            clear_shroud(character)
            if character.session:
                character.session.send(
                    "Your Umbral Charge runs dry -- the shroud collapses."
                )
