"""press_beat.py -- Reporter verbs for basegame."""

from __future__ import annotations

from engine import snoop
from engine.systems import press_beat as pb
from command_support import _find_character


def _relay(character, game, ok, msg, room_line):
    if msg:
        snoop.tell(character, msg)
    if ok and room_line:
        room = getattr(character, "location", None)
        if room is not None:
            room.broadcast(room_line, exclude=character)


def cmd_photograph(character, args, game):
    ok, msg, room_line = pb.photograph(character, game)
    _relay(character, game, ok, msg, room_line)


def cmd_photos(character, args, game):
    character.session.send("\r\n".join(pb.photos_lines(character)))


def cmd_sellphoto(character, args, game):
    pick = (args or "").strip() or None
    ok, msg, room_line = pb.sellphoto(character, game, index=pick)
    _relay(character, game, ok, msg, room_line)


def cmd_copydesk(character, args, game):
    ok, msg, room_line = pb.copydesk(character, game)
    _relay(character, game, ok, msg, room_line)


def cmd_storyboard(character, args, game):
    room = getattr(character, "location", None)
    if not pb.is_news_desk_room(room):
        character.session.send("The storyboard is at the News Office.")
        return
    character.session.send(pb.board_lines(game, character))


def cmd_takestory(character, args, game):
    pick = (args or "").strip() or None
    ok, msg, room_line = pb.takestory(character, game, pick=pick)
    _relay(character, game, ok, msg, room_line)


def cmd_interview(character, args, game):
    query = (args or "").strip()
    if not query:
        character.session.send("Interview whom? Usage: interview <name>")
        return
    room = getattr(character, "location", None)
    if room is None:
        character.session.send("You are nowhere.")
        return
    candidates = [c for c in room.characters() if c is not character]
    target = _find_character(query, candidates)
    if not target:
        character.session.send("You do not see them here.")
        return
    ok, msg, room_line = pb.interview(character, target, game)
    _relay(character, game, ok, msg, room_line)


def cmd_reportstory(character, args, game):
    ok, msg, room_line = pb.reportstory(character, game)
    _relay(character, game, ok, msg, room_line)


def cmd_abandonstory(character, args, game):
    ok, msg, room_line = pb.abandonstory(character, game)
    _relay(character, game, ok, msg, room_line)
