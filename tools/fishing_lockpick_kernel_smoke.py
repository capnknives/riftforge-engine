"""Targeted smoke: engine fishing + lockpick kernels (Wave 2 folklore peel).

Run: python3.13 tools/fishing_lockpick_kernel_smoke.py

Stdlib + engine only -- never imports supers.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from world import Character, Item, Room
from engine import hooks
from engine.systems import doors as doors_engine
from engine.systems import fishing as fishing_engine
from engine.systems import locks as locks_engine


class _Game:
    def __init__(self):
        self.rooms = {}
        self.door_lock_state = {}
        self.game_time_ticks = 0


def _register_hooks():
    """Minimal basegame catalog + fishing tables for engine-only tests."""
    from basegame import items as items_mod

    hooks.set_item_catalog_get(items_mod.get)
    hooks.set_make_world_item(items_mod.make_world_item)
    tables_path = os.path.join(
        ROOT, "basegame", "content", "fishing_tables.json",
    )
    with open(tables_path, encoding="utf-8") as handle:
        tables = json.load(handle)

    hooks.set_fishing_tables(lambda: tables)
    hooks.set_fishing_skill(lambda _ch: 50.0)
    hooks.set_aboard_water_craft(lambda _ch: False)
    hooks.set_skill_check(lambda _ch, _skill, _dc: True)


def test_room_can_fish():
    pier = Room("pier", "Town pier")
    pier.resources = ["fish_pier"]
    assert fishing_engine.room_can_fish(pier)
    dry = Room("dry", "Dusty lot")
    assert not fishing_engine.room_can_fish(dry)


def test_try_fish_four_tuple_catch():
    game = _Game()
    pier = Room("pier", "Town pier")
    pier.resources = ["fish_pier"]
    ch = Character("Angler")
    ch.move_to(pier)
    ok, actor, room_line, catch = fishing_engine.try_fish(ch, game)
    assert isinstance(ok, bool)
    assert isinstance(actor, str)
    assert ok, actor
    assert isinstance(room_line, str)
    assert isinstance(catch, dict)
    assert catch.get("item_id")
    assert "item_name" in catch
    assert "RK" not in room_line


def test_try_fish_dry_room():
    game = _Game()
    dry = Room("dry", "Dusty lot")
    ch = Character("Angler")
    ch.move_to(dry)
    ok, actor, room_line, catch = fishing_engine.try_fish(ch, game)
    assert not ok
    assert isinstance(actor, str)
    assert room_line is None
    assert catch is None


def test_try_fish_boat_refuse():
    game = _Game()
    deep = Room("deep", "Open ocean")
    deep.resources = ["fish_offshore"]
    ch = Character("Angler")
    ch.move_to(deep)
    ok, actor, _room, _catch = fishing_engine.try_fish(ch, game)
    assert not ok
    assert "boat" in actor.lower()


def test_pick_container_unlock_no_vnum():
    game = _Game()
    room = Room("yard", "Yard")
    game.rooms[room.key] = room
    ch = Character("Ash")
    ch.key = "RK00004"
    ch.given_name = "Ash"
    ch.public_mononym = True
    ch.move_to(room)
    room.contents.append(ch)
    box = Item("a strongbox", "Sealed.", locked=True)
    room.contents.append(box)
    hooks.set_skill_check(lambda _c, _s, _d: True)
    ok, _msg, room_line = locks_engine.try_pick_container(ch, "strongbox", game)
    assert ok
    assert not box.locked
    assert isinstance(room_line, str)
    assert "RK00004" not in room_line
    assert "Ash" in room_line


def test_pick_container_skill_fail_stays_locked():
    game = _Game()
    room = Room("shop", "Back room")
    ch = Character("Thief")
    ch.move_to(room)
    box = Item("a crate", "Locked.", locked=True)
    room.contents.append(box)
    hooks.set_skill_check(lambda _c, _s, _d: False)
    ok, msg, _line = locks_engine.try_pick_container(ch, "crate", game)
    assert not ok
    assert box.locked
    assert isinstance(msg, str)


def test_pick_container_electronic_force_kind():
    game = _Game()
    room = Room("vault", "Vault")
    ch = Character("Clip")
    ch.move_to(room)
    box = Item("an electronic seal", "Keyed pad.", locked=True)
    box.lock_kind = "electronic"
    room.contents.append(box)
    hooks.set_skill_check(lambda _c, _s, _d: True)
    ok, msg, room_line = locks_engine.try_pick_container(
        ch, "seal", game, force_kind="electronic",
    )
    assert ok
    assert not box.locked
    assert isinstance(msg, str)
    assert isinstance(room_line, str)
    assert locks_engine.item_lock_kind(box) == "electronic"


def test_try_pick_door():
    game = _Game()
    hall = Room("hall", "Hall")
    office = Room("office", "Office")
    hall.is_house = True
    office.is_house = True
    hall.main_homeroom = hall.key
    office.main_homeroom = hall.key
    hall.exits = {"east": office}
    office.exits = {"west": hall}
    game.rooms[hall.key] = hall
    game.rooms[office.key] = office
    doors_engine.set_exit_locked(game, hall, "east", True)
    ch = Character("Thief")
    ch.move_to(hall)
    hall.contents.append(ch)
    hooks.set_skill_check(lambda _c, _s, _d: True)
    ok, msg, room_line = locks_engine.try_pick_door(ch, "east", game)
    assert ok, msg
    assert not doors_engine.is_exit_locked(game, hall, "east", office)
    assert isinstance(room_line, str)
    assert "door" in room_line.lower()


if __name__ == "__main__":
    _register_hooks()
    test_room_can_fish()
    test_try_fish_four_tuple_catch()
    test_try_fish_dry_room()
    test_try_fish_boat_refuse()
    test_pick_container_unlock_no_vnum()
    test_pick_container_skill_fail_stays_locked()
    test_pick_container_electronic_force_kind()
    test_try_pick_door()
    print("fishing_lockpick_kernel_smoke: ok")
