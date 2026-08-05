"""justice.py -- ranger arrest and fine payment (engine justice kit)."""

from engine.systems import economy as economy_mod
from engine.systems import justice as justice_mod

# Flat pickpocket take for the demo -- no skill roll, this is a shell, not
# a full crime sim (SUPERS' own supers/crime.py has the richer version).
STEAL_CENTS = 500


def _ranger(character):
    return getattr(character, "bg_path", None) == "ranger"


def cmd_steal(character, args, game):
    """Pickpocket dollars from someone in the room -- marks you wanted.

    This is the demo's only in-game path to ``wanted`` status; without it
    the ``arrest``/``payfine`` loop above has no player-reachable trigger.
    """
    name = (args or "").strip()
    if not name:
        character.session.send("Steal from whom?")
        return
    room = getattr(character, "location", None)
    target = game.find_character(name)
    if target is None or getattr(target, "location", None) is not room:
        character.session.send("You do not see them here.")
        return
    if target is character:
        character.session.send("You already have your own wallet.")
        return
    take = min(STEAL_CENTS, economy_mod.wallet_total_cents(target))
    if take <= 0:
        character.session.send(f"{target.key} is broke -- nothing to take.")
        return
    economy_mod.debit_wallet(
        target, 0, cents=take, reason=f"Pickpocketed by {character.key}",
    )
    economy_mod.credit_wallet(
        character, 0, cents=take, reason=f"Stole from {target.key}",
    )
    justice_mod.mark_wanted(character)
    character.fine_owed_cents = int(
        getattr(character, "fine_owed_cents", 0) or 0
    ) + justice_mod.DEFAULT_FINE_CENTS
    amount = economy_mod.format_money(*economy_mod.cents_to_parts(take))
    character.session.send(
        f"You lift {amount} from {target.key}'s pocket. Word gets "
        f"around -- you're wanted now."
    )
    if target.session:
        target.session.send(
            f"{character.key} bumps into you -- your wallet feels lighter!"
        )


def cmd_arrest(character, args, game):
    """Ranger: arrest a wanted character into a holding cell."""
    if not _ranger(character):
        character.session.send("Only rangers can make lawful arrests.")
        return
    room = getattr(character, "location", None)
    if room is None or not getattr(room, "is_cell", False):
        character.session.send("You need to be at the holding cell to process an arrest.")
        return
    name = (args or "").strip()
    if not name:
        character.session.send("Arrest whom?")
        return
    target = game.find_character(name)
    if target is None:
        character.session.send("You do not see them here.")
        return
    if not justice_mod.is_wanted(target):
        character.session.send(f"{target.key} is not wanted.")
        return
    if justice_mod.jail(target, room, game=game):
        target.fine_owed_cents = int(
            getattr(target, "fine_owed_cents", 0) or justice_mod.DEFAULT_FINE_CENTS
        )
        character.session.send(f"You lock {target.key} in the holding cell.")
        if target.session:
            target.session.send(
                f"{character.key} arrests you. The door clangs shut."
            )
    else:
        character.session.send("You cannot process the arrest here.")


def cmd_payfine(character, args, game):
    """Pay outstanding fines to clear wanted status."""
    del args
    ok, msg = justice_mod.pay_fine(character, game=game)
    character.session.send(msg)
