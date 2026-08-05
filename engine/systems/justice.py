"""
justice.py -- generic wanted / fine / jail state machine.

Registers a move-gate callback via ``engine.hooks.set_move_gate`` so games
can block exits from ``room.is_cell`` while a character is jailed.

Stdlib only; zero ``supers`` imports.
"""

from __future__ import annotations

from engine.systems import economy as economy_mod

DEFAULT_JAIL_TICKS = 12
DEFAULT_FINE_CENTS = 500


def _now_tick(game):
    return int(getattr(game, "game_time_ticks", 0) or 0)


def mark_wanted(character):
    """Flag a character wanted by the law."""
    character.wanted = True


def clear_wanted(character):
    """Clear the wanted flag."""
    character.wanted = False


def is_wanted(character):
    return bool(getattr(character, "wanted", False))


def pay_fine(character, *, game=None):
    """Debit ``fine_owed_cents`` from wallet; clear wanted on success."""
    owed = int(getattr(character, "fine_owed_cents", 0) or 0)
    if owed <= 0:
        character.fine_owed_cents = 0
        clear_wanted(character)
        return True, "You have no outstanding fines."
    if not economy_mod.can_afford(character, 0, cents=owed):
        d, c = economy_mod.cents_to_parts(owed)
        return False, (
            f"You need {economy_mod.format_money(d, c)} to clear your fines."
        )
    economy_mod.debit_wallet(
        character, 0, cents=owed, reason="Pay fine",
        tick=_now_tick(game),
    )
    character.fine_owed_cents = 0
    clear_wanted(character)
    return True, "You pay your fines and are square with the law."


def jail(character, room, *, until_tick=None, game=None):
    """Sentence a character to a holding cell until ``until_tick``."""
    if room is None or not getattr(room, "is_cell", False):
        return False
    now = _now_tick(game)
    character.jail_until_tick = int(
        until_tick if until_tick is not None else now + DEFAULT_JAIL_TICKS
    )
    character.wanted = False
    mover = getattr(character, "move_to", None)
    if callable(mover):
        mover(room)
    else:
        character.location = room
    return True


def is_jailed(character, game=None):
    """True while the jail sentence timer is still running."""
    until = getattr(character, "jail_until_tick", None)
    if until is None:
        return False
    try:
        until_i = int(until)
    except (TypeError, ValueError):
        return False
    if until_i <= 0:
        return False
    now = _now_tick(game)
    return until_i > now


def release(character):
    """End a jail sentence early or on timeout."""
    character.jail_until_tick = None
    return True


def tick(game):
    """Auto-release characters whose jail timer has expired."""
    from engine.char_index import iter_characters

    now = _now_tick(game)
    for character in iter_characters(game):
        until = getattr(character, "jail_until_tick", None)
        if until is None:
            continue
        try:
            until_i = int(until)
        except (TypeError, ValueError):
            character.jail_until_tick = None
            continue
        if until_i > 0 and now >= until_i:
            release(character)
            if character.session:
                character.session.send("Your sentence is up -- the cell door opens.")


def move_gate_block(character, room, dest, game):
    """Block leaving a jail cell while sentenced. Hook for ``set_move_gate``."""
    del dest, game
    if room is None or not getattr(room, "is_cell", False):
        return None
    if is_jailed(character):
        return "The cell door is locked until your sentence ends."
    return None


def register_move_gate():
    """Install this module's jail move gate on the engine hook."""
    from engine import hooks
    hooks.set_move_gate(move_gate_block)
