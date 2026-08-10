"""verbs/noticeboard.py -- basegame community board (engine noticeboard kit)."""

from engine.systems import noticeboard as noticeboard_mod


def cmd_notice(character, args, game):
    """Post or read notices on a room-tagged community board."""
    room = getattr(character, "location", None)
    raw = (args or "").strip()
    if not raw:
        character.session.send("\r\n".join(noticeboard_mod.read(room)))
        return
    parts = raw.split(maxsplit=1)
    verb = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    ticks = int(getattr(game, "game_time_ticks", 0) or 0) if game else 0

    if verb == "read":
        ok, msg = noticeboard_mod.read_one(room, rest)
        character.session.send(msg)
        return
    if verb in ("remove", "rm", "pull"):
        ok, msg = noticeboard_mod.remove(room, rest)
        character.session.send(msg)
        return
    if verb == "post":
        ok, msg = noticeboard_mod.post(
            room, character, rest, now_tick=ticks,
        )
        character.session.send(msg)
        return
    # Bare text after ``notice`` is a shorthand post.
    ok, msg = noticeboard_mod.post(room, character, raw, now_tick=ticks)
    character.session.send(msg)
