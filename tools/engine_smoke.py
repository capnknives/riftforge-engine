"""
engine_smoke.py — two-repo purity Phase 4 gate.

CI job ``engine-only-smoke`` renames ``supers/`` out of the tree, then runs
this script. Exit 0 means the lean engine surface still works with SUPERS
physically absent (not merely blocked via meta_path).

What this proves (and what it deliberately does NOT):

    - Proves: ``engine/`` has zero SUPERS imports; lean ``Character``; lean
      ``who`` / ``idlemode`` stubs; hook no-op defaults; ``engine.command_support``
      + ``engine.persistence``; root ``world`` facade; ``maps.load_all_maps()``.
    - Proves Phase 4b: ``import commands`` / ``import server`` with SUPERS
      absent; lean ``Game`` constructs; ``COMMANDS`` is engine-only.
    - Does NOT run the full telnet loop or gateway (see tools/gateway_smoke.py).

Run only with the ``supers`` package absent from the checkout (and from
``sys.path``). Local simulation::

    Rename-Item supers supers.off
    py -3.13 tools/engine_smoke.py
    Rename-Item supers.off supers

Never imported by ``server.py``. Stdlib only.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys


def _repo_root():
    """Absolute path to the monorepo root (parent of tools/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_repo_on_path():
    """Put the checkout root first on sys.path so root facades import."""
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    # Always chdir to the repo so maps.py finds content/maps relative paths.
    os.chdir(root)


def _require_supers_absent():
    """Fail loudly if the supers package is still importable.

    CI hides the tree with ``mv supers supers.off``. A developer who forgets
    that step should get a clear error instead of a false green.
    """
    # Drop any cached supers modules from a prior import in this process.
    for name in list(sys.modules):
        if name == "supers" or name.startswith("supers."):
            del sys.modules[name]

    spec = importlib.util.find_spec("supers")
    if spec is not None:
        origin = getattr(spec, "origin", None) or getattr(spec, "submodule_search_locations", None)
        print(
            "FAIL: supers is still importable "
            f"(find_spec origin={origin!r}).\n"
            "Rename or remove the supers/ directory before running "
            "tools/engine_smoke.py (CI does: mv supers supers.off).",
            file=sys.stderr,
        )
        sys.exit(1)


def _scan_for_supers_imports(package_dir):
    """Return ``path:lineno: line`` hits for module-level supers imports."""
    hits = []
    # Same pattern as smoke_test._scan_for_supers_imports — word boundary so
    # ``from supersomething`` does not false-positive.
    pattern = re.compile(r"^\s*(from supers\b|import supers\b)")
    for root, _dirs, files in os.walk(package_dir):
        if "__pycache__" in root:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, "r", encoding="utf-8") as f:
                for lineno, line in enumerate(f, start=1):
                    if pattern.match(line):
                        hits.append(f"{path}:{lineno}: {line.strip()}")
    return hits


class _FakeSession:
    """Minimal Session stand-in: collect send() lines for verb stubs."""

    def __init__(self):
        self.lines = []

    def send(self, message):
        """Record outbound text the way a real Session would emit it."""
        self.lines.append(message)


class _FakeGame:
    """Minimal Game stand-in for lean who/idlemode handlers."""

    sessions = []


def main():
    """Run the Phase 4 engine-only assertions; exit 0 on success."""
    _ensure_repo_on_path()
    _require_supers_absent()

    engine_dir = os.path.join(_repo_root(), "engine")
    hits = _scan_for_supers_imports(engine_dir)
    assert not hits, (
        "Phase 2 two-repo purity violation — SUPERS import(s) under engine/:\n"
        + "\n".join(hits)
    )

    from world import Character, Item
    from engine import hooks
    from engine.verbs.basic import cmd_idlemode, cmd_who

    c = Character("LeanEngine")
    assert c.key == "LeanEngine"
    assert not hasattr(c, "origin"), c.__dict__.keys()
    assert not hasattr(c, "stats"), c.__dict__.keys()

    c.session = _FakeSession()
    cmd_who(c, "", _FakeGame())
    assert c.session.lines, "lean cmd_who should still send something"
    cmd_idlemode(c, "on", _FakeGame())
    assert "installed" in c.session.lines[-1].lower(), c.session.lines

    # Hook defaults with no game registered.
    assert hooks.eclipse_ambient_line(_FakeGame()) == ""
    assert hooks.vampire_fear_message(c, None) is None
    assert hooks.look_quirk(c, c) is None
    assert hooks.move_gate_block(c, None, None, _FakeGame()) is None
    assert hooks.make_relic_item("anything") is None
    assert hooks.loot_room_line("A", "B", c) == "A takes LeanEngine from B."
    assert hooks.get_dispatch() is None

    assert hooks.can_see_spirit(c, c) is True
    assert hooks.can_see_spirit(c, Character("Other")) is False
    assert hooks.before_relocate(c) is None
    hooks.after_arrive(c, None, _FakeGame(), False)
    hooks.encounter_check(_FakeGame(), None)
    hooks.ensure_game_defaults(_FakeGame())
    hooks.recompute_hp(c)
    assert hooks.upgrade_legacy_container(c) is False

    seed = hooks.make_world_item({"key": "a rock", "description": "A rock."})
    assert isinstance(seed, Item) and seed.key == "a rock"

    import engine.command_support as ecs
    assert ecs._can_see_spirit(c, c) is True
    assert ecs._find_item("rock", [seed]) is seed

    import engine.persistence as epers
    conn = epers.connect(":memory:")
    assert epers.is_seeded(conn) is False

    import world as world_mod
    assert world_mod.Character is Character
    try:
        world_mod.make_wilderness_hostile
        raise AssertionError("SUPERS-only world.X should need supers")
    except ImportError:
        pass

    # Extra vs purity subprocess: maps load without a game registered.
    import maps
    rooms, start_room, seed_items = maps.load_all_maps()
    assert isinstance(rooms, dict) and rooms, "maps.load_all_maps should build rooms"
    assert start_room is not None, "at least one map should mark is_start"
    assert isinstance(seed_items, list)

    # Phase 4b: soft-optional commands + server with SUPERS absent.
    import commands as commands_mod
    assert "look" in commands_mod.COMMANDS
    assert "attack" not in commands_mod.COMMANDS, (
        "SUPERS verbs should be absent when supers is missing"
    )
    assert hooks.get_dispatch() is commands_mod.dispatch

    import server as server_mod
    assert server_mod._HAS_SUPERS is False
    # Lean Game: maps + persistence, no Cadence seed.
    lean_game = server_mod.Game(db_path=":memory:")
    assert lean_game.start_room is not None
    assert lean_game.find_character("a training dummy") is None
    lean_game.db.close()

    print("engine_smoke_ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
