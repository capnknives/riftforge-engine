"""lodging.py -- basegame inn rent + sleep (engine lodging primitives)."""

from engine.systems import economy as economy_mod
from engine.systems import lodging as lodging_mod

INN_NIGHT_CENTS = 500  # $5.00 demo rate


def _inn_room(character):
    """True when standing in a hotel/lodging room with beds."""
    room = getattr(character, "location", None)
    if room is None:
        return None
    if not lodging_mod.is_lodging_unit(room):
        return None
    return room


def cmd_rent_bed(character, args, game):
    """Pay for a night at the inn and reserve a bunk."""
    del args
    room = _inn_room(character)
    if room is None:
        character.session.send("You can only rent a bed at an inn.")
        return
    if lodging_mod.claimants_of(game, room.key):
        character.session.send("Someone already has this room tonight.")
        return
    if economy_mod.wallet_total_cents(character) < INN_NIGHT_CENTS:
        character.session.send(
            f"You need ${INN_NIGHT_CENTS / 100:.2f} for a night here."
        )
        return
    economy_mod.debit_wallet(
        character, cents=INN_NIGHT_CENTS, reason="Inn rent",
    )
    character.home_room_key = room.key
    lodging_mod.stamp_home_basics(room)
    bed, err = lodging_mod.pick_bed(room, character)
    if bed is not None:
        bed.owner_key = character.key
    character.session.send(
        "You pay for the night and claim a bunk. Type 'sleep' when ready."
    )


def cmd_sleep(character, args, game):
    """Sleep on an available bed in this lodging room."""
    del args, game
    room = getattr(character, "location", None)
    if room is None:
        character.session.send("You are nowhere.")
        return
    if getattr(character, "asleep", False):
        character.session.send("You are already asleep.")
        return
    if not lodging_mod.is_safe_sleep_venue(room, character):
        character.session.send("You cannot sleep safely here.")
        return
    bed, err = lodging_mod.pick_bed(room, character)
    if bed is None:
        character.session.send(err or "There is no bed here.")
        return
    if not lodging_mod.bed_available_to(character, bed, room):
        character.session.send("That bed is not available.")
        return
    character.asleep = True
    character.resting = False
    character.sleep_bed_id = id(bed)
    character.public_sleep = False
    character.session.send("You stretch out and fall asleep.")


def cmd_wake(character, args, game):
    """Wake from sleep."""
    del args, game
    if not getattr(character, "asleep", False):
        character.session.send("You are not asleep.")
        return
    character.asleep = False
    character.sleep_bed_id = None
    character.public_sleep = False
    character.session.send("You wake up and sit up on the edge of the bed.")
