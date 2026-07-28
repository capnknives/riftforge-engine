"""basegame_smoke.py -- two-repo purity Phase 7 gate.

Proves the reference game (basegame/) is a complete, playable loop on top
of the generic RiftForge engine: boot with RIFTFORGE_GAME=basegame, run
chargen through all four paths, walk Notbigville + America overland, and
check help -- all without ever importing supers.

Stdlib only. Never imported by server.py.
"""

from __future__ import annotations

import asyncio
import os
import sys


def _repo_root():
    """Absolute path to the monorepo root (parent of tools/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_repo_on_path():
    """Put the checkout root first on sys.path so root facades import."""
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    os.chdir(root)


class _FakeSession:
    """Minimal Session stand-in: scripted replies in, sent lines out."""

    def __init__(self, replies):
        self.lines = []
        self._replies = list(replies)

    def send(self, message):
        self.lines.append(message)

    async def read_line(self):
        if not self._replies:
            raise AssertionError(
                "FakeSession ran out of scripted replies -- chargen asked "
                "more questions than the test expected:\n" + "\n".join(self.lines[-10:])
            )
        return self._replies.pop(0)


def _chargen_replies_for(path_index):
    from engine import stats as engine_stats
    return [str(path_index)] + ["0"] * len(engine_stats.STAT_NAMES) + ["0"]


async def _run_chargen_for_path(game, path_index, path_id):
    from world import Character
    from engine import hooks

    char = Character(f"Smoke{path_id.capitalize()}")
    session = _FakeSession(_chargen_replies_for(path_index))
    char.session = session
    ok = await hooks.run_chargen(session, char)
    assert ok, f"chargen for path {path_id!r} should not report disconnect"
    assert char.bg_path == path_id, (
        f"expected bg_path={path_id!r}, got {char.bg_path!r}"
    )
    from basegame import stats as stats_module
    char.hp = stats_module.max_hp(char)
    char.move_to(game.start_room)
    return char


def main():
    """Run the Phase 7 basegame assertions; exit 0 on success."""
    _ensure_repo_on_path()
    os.environ["RIFTFORGE_GAME"] = "basegame"

    import game_select
    game_select._reset_for_tests()
    assert game_select.game_name() == "basegame"
    assert "supers" not in sys.modules

    import server as server_mod
    assert server_mod._HAS_SUPERS is False

    import commands as commands_mod
    assert "look" in commands_mod.COMMANDS
    assert "attack" not in commands_mod.COMMANDS

    from engine import hooks
    topics = hooks.get_help_topics()
    assert "paths" in topics

    game = server_mod.Game(db_path=":memory:")
    assert game.start_room is not None
    assert game.start_room.key == "NB00001", (
        f"Notbigville Main Street vnum should be start, got {game.start_room.key!r}"
    )
    assert getattr(game, "overland_atlas", None) is not None, (
        "basegame boot should stamp overland atlas"
    )

    from basegame.chargen import PATH_ORDER
    placed = []
    for index, path_id in enumerate(PATH_ORDER, start=1):
        char = asyncio.run(_run_chargen_for_path(game, index, path_id))
        placed.append(char)
    assert len(placed) == 4

    walker = placed[0]
    walker.session = _FakeSession([])
    dispatch = hooks.get_dispatch()
    dispatch(walker, "look", game)
    assert any("Main Street" in line or "Notbigville" in line for line in walker.session.lines)

    dispatch(walker, "south", game)
    assert walker.location.key == "NB00007", walker.location.key
    dispatch(walker, "north", game)
    assert walker.location.key == "NB00001", walker.location.key
    dispatch(walker, "exit", game)
    assert walker.location is not None
    from engine.systems import overland as overland_mod
    assert overland_mod.overland_mode(walker) == "on_foot", (
        f"exit from town should place on virtual overland, got {overland_mod.overland_mode(walker)}"
    )
    macro = overland_mod._parse_pos_pair(walker.macro_pos)
    assert macro == (35, 10), macro

    dispatch(walker, "enter notbigville", game)
    assert walker.location.key == "NB00001", walker.location.key

    from engine.systems import weather as weather_module
    outdoor_clause = weather_module.look_clause(walker.location, game)
    assert outdoor_clause, "Main Street is outdoor and should get a weather clause"
    walker.move_to(game.rooms["NB00008"])
    outdoor_clause = weather_module.look_clause(walker.location, game)
    assert outdoor_clause, "Observatory should get generic weather clause for now"

    walker.session = _FakeSession([])
    dispatch(walker, "score", game)
    sheet = " ".join(walker.session.lines)
    assert "Detective" in sheet

    assert "Post Office" in game.rooms or "NB00006" in game.rooms
    post_key = "NB00006" if "NB00006" in game.rooms else "Post Office"
    peer = placed[1]
    walker.move_to(game.rooms[post_key])
    walker.session = _FakeSession([])
    dispatch(walker, f"mail send {peer.key} hello from square", game)
    assert peer.mail_inbox and peer.mail_inbox[0]["text"] == "hello from square"

    server_mod.run_ticks(game)
    game.db.close()

    print("basegame_smoke_ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
