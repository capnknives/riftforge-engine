"""
combat_pursuit.py -- auto-follow when an active-combat target leaves the room.

Pursuit is never a manual target command. When a body you are fighting in
``active`` mode leaves the room (walk, flee, throw, slam, breach eject),
and they are your current ``target``, you automatically trail them via the
engine follow bond (``start_following``).

Stdlib only; zero ``supers`` imports.
"""

from __future__ import annotations

from engine.systems import fight as fight_mod


def _name(character):
    return (
        getattr(character, "key", None)
        or getattr(character, "name", None)
        or "someone"
    )


def notify_character_relocated(mover, old_room, new_room, game):
    """Start pursuit for engaged fighters whose target just left ``old_room``.

    Called after ``mover`` has already arrived in ``new_room``. No-op when
    rooms are the same, either is missing, or ``mover`` is not in an active
    combat bout.
    """
    if mover is None or old_room is None or new_room is None:
        return
    if old_room is new_room:
        return
    fight = fight_mod.get_fight(mover)
    if fight is None or fight.combat_mode != fight_mod.MODE_ACTIVE:
        return
    from engine.command_support import start_following

    for pursuer in list(fight.members):
        if pursuer is mover:
            continue
        if getattr(pursuer, "location", None) is not old_room:
            continue
        if getattr(pursuer, "target", None) is not mover:
            continue
        if getattr(pursuer, "following", None) is mover:
            continue
        if not start_following(pursuer, mover):
            continue
        session = getattr(pursuer, "session", None)
        if session is not None:
            session.send(
                f"You pursue {_name(mover)} as they leave the fight."
            )
        broadcast = getattr(old_room, "broadcast", None)
        if callable(broadcast):
            msg = (
                f"{_name(pursuer)} gives chase after {_name(mover)}."
            )
            try:
                broadcast(msg, exclude=pursuer)
            except TypeError:
                broadcast(msg)
