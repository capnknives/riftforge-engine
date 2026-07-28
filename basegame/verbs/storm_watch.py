"""storm_watch.py -- Tornado Hunter verbs for basegame."""

from __future__ import annotations

from engine import snoop
from engine.systems import storm_chase as sw


def _relay(character, game, ok, msg, room_line):
    if msg:
        snoop.tell(character, msg)
    if ok and room_line:
        room = getattr(character, "location", None)
        if room is not None:
            room.broadcast(room_line, exclude=character)


def cmd_research(character, args, game):
    ok, msg, room_line = sw.research(character, game)
    _relay(character, game, ok, msg, room_line)


def cmd_radar(character, args, game):
    ok, msg, room_line = sw.radar(character, game)
    _relay(character, game, ok, msg, room_line)


def cmd_chaseboard(character, args, game):
    room = getattr(character, "location", None)
    if not sw.is_storm_desk_room(room):
        character.session.send("The chase board is at Storm Watch Office.")
        return
    character.session.send(sw.board_lines(game, character))


def cmd_takechase(character, args, game):
    ok, msg, room_line = sw.takechase(character, game)
    _relay(character, game, ok, msg, room_line)


def cmd_track_chase(character, args, game):
    ok, msg, room_line = sw.track_chase(character, game)
    _relay(character, game, ok, msg, room_line)


def cmd_probe(character, args, game):
    ok, msg, room_line = sw.probe(character, game)
    _relay(character, game, ok, msg, room_line)


def cmd_reportchase(character, args, game):
    ok, msg, room_line = sw.reportchase(character, game)
    _relay(character, game, ok, msg, room_line)


def cmd_abandonchase(character, args, game):
    ok, msg, room_line = sw.abandonchase(character, game)
    _relay(character, game, ok, msg, room_line)
