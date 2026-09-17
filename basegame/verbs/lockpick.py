"""lockpick.py -- basegame lockpick verb (engine kernel)."""

from __future__ import annotations

from command_support import broadcast_here
from engine.systems import locks as locks_engine
from engine.systems import utility_delay as delay_mod


def _relay(character, game, msg, room_line):
    """Send actor text and optional room broadcast."""
    if msg:
        character.session.send(msg)
    if room_line:
        room = getattr(character, "location", None)
        if room is not None:
            broadcast_here(character, room_line, exclude=character)


def cmd_lockpick(character, args, game):
    """Pick mechanical locks or clip electronic seals (help lockpick)."""
    text = (args or "").strip()
    if not text:
        character.session.send(
            "Pick what? lockpick <container> | lockpick door <direction> "
            "(see 'help lockpick')"
        )
        return
    parts = text.split(None, 1)
    head = parts[0].lower()
    tail = parts[1].strip() if len(parts) > 1 else ""
    if head == "door":
        if not tail:
            character.session.send(
                "Which door? Try: lockpick door east  (or in, out, up, …)"
            )
            return
        busy = delay_mod.begin_attempt(character, game, "thievery_lock")
        if busy:
            character.session.send(busy)
            return
        ok, msg, room_line = locks_engine.try_pick_door(character, tail, game)
        _relay(character, game, msg, room_line)
        return
    if head == "clip" and tail:
        busy = delay_mod.begin_attempt(character, game, "electronics_bypass")
        if busy:
            character.session.send(busy)
            return
        ok, msg, room_line = locks_engine.try_pick_container(
            character, tail, game, force_kind="electronic",
        )
        _relay(character, game, msg, room_line)
        return
    busy = delay_mod.begin_attempt(character, game, "thievery_lock")
    if busy:
        character.session.send(busy)
        return
    ok, msg, room_line = locks_engine.try_pick_container(character, text, game)
    _relay(character, game, msg, room_line)
