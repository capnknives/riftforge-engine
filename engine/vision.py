"""
engine/vision.py -- thin light / secret-exit helpers (D66 / D67).

Generic MUD chrome: dark rooms need a carried light source to see look
contents; hidden exits stay invisible until a character searches (or
already knows them). No SUPERS imports -- Item.provides_light and
Room.dark / Room.hidden_directions are plain engine fields.
"""


def has_light_source(character):
    """True if the character carries any item marked provides_light."""
    for item in getattr(character, "inventory", None) or []:
        if getattr(item, "provides_light", False):
            return True
    return False


def can_see_room(character, room):
    """True unless the room is dark and the character has no light."""
    if room is None:
        return True
    if not getattr(room, "dark", False):
        return True
    return has_light_source(character)


def exit_is_hidden(room, direction):
    """True if `direction` is authored as a secret exit in this room."""
    hidden = getattr(room, "hidden_directions", None) or ()
    return direction in hidden


def character_knows_exit(character, room, direction):
    """True if the exit is not hidden, or this character has revealed it."""
    if not exit_is_hidden(room, direction):
        return True
    known = getattr(character, "known_exits", None) or {}
    room_key = getattr(room, "key", None)
    if not room_key:
        return False
    dirs = known.get(room_key) or []
    return direction in dirs


def reveal_hidden_exits(character, room):
    """Mark every hidden exit in `room` as known. Return newly revealed dirs.

    Idempotent: already-known directions are skipped in the return list
    but stay in known_exits. Mutates character.known_exits in place.
    """
    if not hasattr(character, "known_exits") or character.known_exits is None:
        character.known_exits = {}
    room_key = room.key
    hidden = list(getattr(room, "hidden_directions", None) or ())
    if not hidden:
        return []
    already = list(character.known_exits.get(room_key) or [])
    newly = []
    for direction in hidden:
        if direction not in already:
            already.append(direction)
            newly.append(direction)
    character.known_exits[room_key] = already
    return newly
