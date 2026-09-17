"""Targeted smoke: engine pocket-grid chassis (Wave 6 folklore peel).

Run: python3.13 tools/pocket_grid_smoke.py

Stdlib + engine only -- never imports supers.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_IMPORT_SUPERS = re.compile(r"^\s*(from|import)\s+supers\b", re.MULTILINE)
_TOUCHED_ENGINE = (
    "engine/hooks.py",
    "engine/systems/pocket_grid.py",
)


class _Session:
    """Minimal session stub; live Session.send must accept str only."""

    def __init__(self):
        self.sent = []

    def send(self, message):
        assert isinstance(message, str), type(message)
        self.sent.append(message)


class _GameStub:
    def __init__(self):
        self.demesne_rooms = {}
        self.demesne_macro = {}
        self.demesne_ground = {}


def test_cell_key_and_parse():
    from engine.systems.pocket_grid import cell_key, parse_cell_coords

    assert cell_key(0, 1) == "0,1"
    assert parse_cell_coords("0,1") == (0, 1)
    assert parse_cell_coords("0 1") == (0, 1)
    assert parse_cell_coords("garbage") is None


def test_micro_key_roundtrip():
    from engine.systems.pocket_grid import micro_room_key, parse_micro_key

    key = micro_room_key("demesne:x", 1, 2, 3, 4)
    assert parse_micro_key(key) == ("demesne:x", 1, 2, 3, 4)


def test_get_or_create_default_stamp():
    from engine.systems.pocket_grid import (
        FOUNDING_MACRO_SIZE,
        get_or_create_pocket_micro,
    )

    game = _GameStub()
    pocket = {"demesne_id": "demesne:test"}
    room = get_or_create_pocket_micro(game, pocket, 0, 0, 5, 5)
    assert room is not None
    assert room.demesne_id == "demesne:test"
    assert room.virtual_overland is True
    assert room.overland_macro == (0, 0)
    assert room.overland_micro == (5, 5)
    assert get_or_create_pocket_micro(game, pocket, 9, 9, 0, 0) is None


def test_neighbor_wrap_and_frontier():
    from engine.systems.pocket_grid import FOUNDING_MACRO_SIZE, neighbor_coord

    size = FOUNDING_MACRO_SIZE
    assert neighbor_coord(0, 0, 9, 5, "east", size) == (1, 0, 0, 5)
    assert neighbor_coord(0, 0, 0, 0, "west", size) is None


def test_expand_and_shrink():
    from engine.systems.pocket_grid import (
        FOUNDING_MACRO_SIZE,
        expand_grid,
        get_or_create_pocket_micro,
        shrink_frontier,
    )

    game = _GameStub()
    pocket = {"demesne_id": "demesne:grid", "macro_size": FOUNDING_MACRO_SIZE}
    get_or_create_pocket_micro(game, pocket, 4, 4, 5, 5)

    ok, old_size, new_size = expand_grid(game, pocket, 5)
    assert ok
    assert old_size == FOUNDING_MACRO_SIZE
    assert new_size == 5
    assert pocket["macro_size"] == 5

    get_or_create_pocket_micro(game, pocket, 4, 4, 5, 5)
    ok2, _, new_sz, removed = shrink_frontier(game, pocket, 3)
    assert ok2
    assert new_sz == FOUNDING_MACRO_SIZE
    assert removed >= 1

    ok3, _, _, _ = shrink_frontier(game, pocket, 1)
    assert not ok3
    assert pocket["macro_size"] >= FOUNDING_MACRO_SIZE


def test_engine_files_stay_supers_free():
    for rel in _TOUCHED_ENGINE:
        path = os.path.join(ROOT, rel)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        if _IMPORT_SUPERS.search(text):
            raise AssertionError(f"supers import in {rel}")


def test_session_send_str():
    _Session().send("ok")


def main():
    test_cell_key_and_parse()
    test_micro_key_roundtrip()
    test_get_or_create_default_stamp()
    test_neighbor_wrap_and_frontier()
    test_expand_and_shrink()
    test_engine_files_stay_supers_free()
    test_session_send_str()
    print("pocket_grid_smoke: all ok")


if __name__ == "__main__":
    main()
