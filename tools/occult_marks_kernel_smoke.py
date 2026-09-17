"""Targeted smoke: engine occult_marks kernel (Wave 3 folklore peel).

Run: python3.13 tools/occult_marks_kernel_smoke.py

Stdlib + engine only -- never imports supers.
"""

from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_VNUM_RE = re.compile(r"[A-Z]{2}\d{5}")


class _Session:
    """Minimal session stub; live Session.send must accept str only."""

    def __init__(self):
        self.sent = []

    def send(self, message):
        assert isinstance(message, str), type(message)
        self.sent.append(message)


def test_bare_room_stamp_clear():
    from world import Room
    from engine.systems import occult_marks as occult_marks_mod

    room = Room("yard", "Dusty yard")
    assert occult_marks_mod.room_is_devils_trap(room) is False
    occult_marks_mod.stamp_devils_trap(room)
    assert occult_marks_mod.room_is_devils_trap(room) is True
    occult_marks_mod.clear_devils_trap(room)
    assert occult_marks_mod.room_is_devils_trap(room) is False


def test_temporary_hook():
    from world import Room
    from engine import hooks
    from engine.systems import occult_marks as occult_marks_mod

    room = Room("hall", "Empty hall")
    hooks.set_temporary_devils_trap(lambda _r, _t: True)
    assert occult_marks_mod.room_is_devils_trap(room, 0) is True
    hooks.set_temporary_devils_trap(None)
    assert occult_marks_mod.room_is_devils_trap(room, 0) is False
    hooks.set_temporary_salt_line(lambda _r, _t: True)
    assert occult_marks_mod.room_has_salt_line(room, 0) is True
    hooks.set_temporary_salt_line(None)
    assert occult_marks_mod.room_has_salt_line(room, 0) is False


def test_occult_mark_blocks_hook():
    from world import Room, Character
    from engine import hooks
    from engine.systems import occult_marks as occult_marks_mod

    room = Room("cell", "Holding cell")
    ch = Character("Walker")
    occult_marks_mod.stamp_devils_trap(room)
    hooks.set_occult_mark_blocks(lambda _c, _r: "held")
    assert occult_marks_mod.occult_mark_blocks(ch, room) == "held"
    hooks.set_occult_mark_blocks(None)
    assert occult_marks_mod.occult_mark_blocks(ch, room) is None


def test_salt_iron_holy_stamps():
    from world import Room
    from engine.systems import occult_marks as occult_marks_mod

    room = Room("porch", "Porch")
    occult_marks_mod.stamp_salt_line(room)
    assert occult_marks_mod.room_has_salt_line(room)
    occult_marks_mod.stamp_iron_ward(room)
    assert occult_marks_mod.room_has_iron_ward(room)
    occult_marks_mod.stamp_holy_water_ward(room)
    assert occult_marks_mod.room_has_holy_water_ward(room)
    marks = occult_marks_mod.room_marks_for_hud(room, 0)
    assert marks == ["salt_line", "iron_ward", "holy_water_ward"]


def test_zone_dto_marks():
    from world import Room
    from engine import zone_hud
    from engine.systems import occult_marks as occult_marks_mod

    room = Room("saloon", "Saloon floor")
    room.zone = "demo"
    room.layout_x = 2
    room.layout_y = 3
    game = type("G", (), {"game_time_ticks": 0})()
    dto = zone_hud._room_dto(room, game, "demo")
    assert isinstance(dto, dict)
    assert "marks" not in dto
    occult_marks_mod.stamp_devils_trap(room)
    dto2 = zone_hud._room_dto(room, game, "demo")
    assert dto2.get("marks") == ["devils_trap"]
    dumped = json.dumps(dto2)
    assert not _VNUM_RE.search(dumped), dumped
    occult_marks_mod.clear_devils_trap(room)
    dto3 = zone_hud._room_dto(room, game, "demo")
    assert "marks" not in dto3


def test_zone_payload_player_safe():
    from world import Room
    from engine import map_hud
    from engine import zone_hud
    from engine.systems import occult_marks as occult_marks_mod

    room = Room("porch", "Front porch")
    room.zone = "demo"
    room.layout_x = 0
    room.layout_y = 0
    occult_marks_mod.stamp_devils_trap(room)
    game = type("G", (), {"game_time_ticks": 0, "rooms": {room.key: room}})()
    dto = zone_hud._room_dto(room, game, "demo")
    payload = {
        "hidden": False,
        "title": "Demo",
        "z": 0,
        "you": [0, 0],
        "rooms": [dto],
        "who": [],
    }
    map_hud._assert_player_safe(payload)


def test_session_send_str():
    sess = _Session()
    sess.send("ok")
    assert sess.sent == ["ok"]


def main() -> int:
    test_bare_room_stamp_clear()
    test_salt_iron_holy_stamps()
    test_temporary_hook()
    test_occult_mark_blocks_hook()
    test_zone_dto_marks()
    test_zone_payload_player_safe()
    test_session_send_str()
    print("occult_marks_kernel_smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
