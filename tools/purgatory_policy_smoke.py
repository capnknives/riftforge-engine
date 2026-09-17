"""Targeted smoke: engine corporeal-prison plane policy (Wave 5c folklore peel).

Run: python3.13 tools/purgatory_policy_smoke.py

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
    "engine/systems/planes/policy.py",
    "engine/systems/planes/__init__.py",
)


class _Session:
    """Minimal session stub; live Session.send must accept str only."""

    def __init__(self):
        self.sent = []

    def send(self, message):
        assert isinstance(message, str), type(message)
        self.sent.append(message)


def test_default_plane_predicate():
    from engine.systems.planes import policy as policy_mod

    assert policy_mod.is_corporeal_prison_plane("purgatory") is True
    assert policy_mod.is_corporeal_prison_plane("Purgatory ") is True
    for plane in ("heaven", "hell", "earth", "", None):
        assert policy_mod.is_corporeal_prison_plane(plane) is False


def test_hook_override_and_restore():
    from engine import hooks
    from engine.systems.planes import policy as policy_mod

    hooks.set_is_corporeal_prison_plane(lambda p: str(p).lower() == "demo")
    assert policy_mod.is_corporeal_prison_plane("demo") is True
    assert policy_mod.is_corporeal_prison_plane("purgatory") is False
    hooks.set_is_corporeal_prison_plane(None)
    assert policy_mod.is_corporeal_prison_plane("purgatory") is True
    assert policy_mod.is_corporeal_prison_plane("demo") is False


def test_room_predicate():
    from world import Room
    from engine.systems.planes import policy as policy_mod

    prison = Room("pit", "Ash floor")
    prison.plane = "purgatory"
    earth = Room("street", "Main Street")
    earth.plane = "earth"
    assert policy_mod.room_is_corporeal_prison(prison) is True
    assert policy_mod.room_is_corporeal_prison(earth) is False
    assert policy_mod.room_is_corporeal_prison(None) is False


def test_engine_files_stay_supers_free():
    for rel in _TOUCHED_ENGINE:
        path = os.path.join(ROOT, rel)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        if _IMPORT_SUPERS.search(text):
            raise AssertionError(f"supers import in {rel}")


def test_session_send_str():
    sess = _Session()
    sess.send("ok")
    assert sess.sent == ["ok"]


def main() -> int:
    test_default_plane_predicate()
    test_hook_override_and_restore()
    test_room_predicate()
    test_engine_files_stay_supers_free()
    test_session_send_str()
    print("purgatory_policy_smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
