"""treat.py -- medic path clinic admit/heal (engine clinic kit)."""

from engine.systems import clinic as clinic_mod


def _medic(character):
    return getattr(character, "bg_path", None) == "medic"


def _clinic_room(character):
    room = getattr(character, "location", None)
    if room is None or not getattr(room, "hospital", False):
        return None
    return room


def cmd_treat(character, args, game):
    """Medic: admit a downed patient or discharge someone ready to leave."""
    if not _medic(character):
        character.session.send("Only medics know how to run ward care.")
        return
    room = _clinic_room(character)
    if room is None:
        character.session.send("There is no clinic ward here.")
        return

    target_name = (args or "").strip()
    if not target_name:
        # Self-discharge when stable.
        if getattr(character, "hospitalized", False):
            clinic_mod.discharge(character)
            character.session.send("You sign yourself out of the ward.")
            return
        character.session.send("Treat whom? (name, or bare treat to sign out)")
        return

    target = game.find_character(target_name)
    if target is None or getattr(target, "location", None) is not room:
        character.session.send("They are not here in the ward.")
        return

    if clinic_mod.is_ko(target):
        if clinic_mod.admit(target, room, game=game):
            character.session.send(f"You haul {target.key} onto a cot.")
            if target.session:
                target.session.send(
                    f"{character.key} settles you onto a clinic cot."
                )
        else:
            character.session.send("You cannot admit them here.")
        return

    if getattr(target, "hospitalized", False):
        clinic_mod.discharge(target)
        character.session.send(f"You discharge {target.key} from the ward.")
        if target.session:
            target.session.send(
                f"{character.key} signs you out -- steady on your feet."
            )
        return

    character.session.send(f"{target.key} does not need ward care.")
