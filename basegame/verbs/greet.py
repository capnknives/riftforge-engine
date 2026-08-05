"""greet.py -- persona flavor line demo (H7c)."""

from basegame import personas as personas_mod


def cmd_greet(character, args, game):
    """Greet someone here and surface persona-tinted flavor."""
    del game
    name = (args or "").strip()
    if not name:
        character.session.send("Greet who?")
        return
    room = getattr(character, "location", None)
    if room is None:
        character.session.send("You are nowhere.")
        return
    target = None
    for occupant in room.characters():
        if occupant.key.lower() == name.lower():
            target = occupant
            break
    if target is None:
        character.session.send(f"You don't see '{name}' here.")
        return
    line = personas_mod.flavor_line(target, "greet")
    if line:
        character.session.send(line)
    else:
        character.session.send(f"You greet {target.key}.")
