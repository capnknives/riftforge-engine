"""basegame_smoke.py -- two-repo purity Phase 7 gate.

Proves the reference game (basegame/) is a complete, playable loop on top
of the generic RiftForge engine: boot with RIFTFORGE_GAME=basegame, run
chargen through all four paths, walk the demo town + wilds, and check
help -- all without ever importing supers.

Mirrors tools/engine_smoke.py's shape and intent (see that module's
docstring), but where engine_smoke proves the engine survives with SUPERS
physically absent, this proves basegame is a real second game, not just a
stub that happens to satisfy the engine's hook contract.

Two ways to run this:

    py -3.13 tools/basegame_smoke.py
        Normal repo checkout (supers/ present on disk). Asserts basegame
        was selected anyway (RIFTFORGE_GAME=basegame forces it) and that
        `supers` never actually landed in sys.modules -- proves
        game_select.py's mutual-exclusion guarantee, not just "supers
        happened to be absent".

    Rename-Item supers supers.off
    py -3.13 tools/basegame_smoke.py
    Rename-Item supers.off supers
        True-absence proof, same drill as engine_smoke.py. Both modes
        must pass; CI runs the second (basegame-with-supers-absent).

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
    """Minimal Session stand-in: scripted replies in, sent lines out.

    Mirrors engine_smoke.py's _FakeSession but adds read_line() -- chargen
    is a back-and-forth prompt loop (send, await read_line, send, ...), so
    this needs a canned answer queue, not just a sink for outbound text.
    """

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


class _FakeGame:
    """Minimal Game stand-in where a bare dispatch call doesn't need a real one."""

    sessions = []


def _chargen_replies_for(path_index):
    """One path pick + six '0' stat-bonus answers, one per engine.stats
    primary (keeps every path's chargen script identical length regardless
    of which path is chosen)."""
    from engine import stats as engine_stats
    return [str(path_index)] + ["0"] * len(engine_stats.STAT_NAMES)


async def _run_chargen_for_path(game, path_index, path_id):
    """Build a fresh Character, run basegame chargen against it, and
    return the placed Character."""
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
    assert char.stats == {
        "POW": 5.0, "VIT": 5.0, "FOC": 5.0, "FIN": 5.0, "RES": 5.0, "PRE": 5.0,
    }, (
        "declining every bonus point should leave the shared engine spine's "
        f"default 5.0 spread untouched: {char.stats!r}"
    )
    from basegame import stats as stats_module
    assert char.hp == stats_module.max_hp(char) == 30, (
        f"HP_BASE=20 + HP_PER_VIT=2 * VIT=5.0 should be 30, got {char.hp!r}"
    )
    char.move_to(game.start_room)
    return char


def main():
    """Run the Phase 7 basegame assertions; exit 0 on success."""
    _ensure_repo_on_path()
    os.environ["RIFTFORGE_GAME"] = "basegame"

    import game_select
    game_select._reset_for_tests()
    assert game_select.game_name() == "basegame", (
        f"RIFTFORGE_GAME=basegame should force basegame, got "
        f"{game_select.game_name()!r}"
    )
    assert "supers" not in sys.modules, (
        "game_select must never import supers when basegame is explicitly "
        "requested -- see game_select.py's mutual-exclusion guarantee"
    )

    import server as server_mod
    assert server_mod._HAS_SUPERS is False, (
        "server._HAS_SUPERS should be False whenever basegame (not supers) "
        "is the active game"
    )

    import commands as commands_mod
    assert "look" in commands_mod.COMMANDS, "ENGINE_COMMANDS should still merge in"
    assert "attack" not in commands_mod.COMMANDS, (
        "SUPERS-only verbs should be absent when basegame is active"
    )

    from engine import hooks
    topics = hooks.get_help_topics()
    assert "paths" in topics, "basegame help topics should register 'paths'"
    assert "detective" in topics["paths"], topics["paths"]

    game = server_mod.Game(db_path=":memory:")
    assert game.start_room is not None
    assert game.start_room.key == "Town Square", (
        f"demo_town.json's is_start room should be Town Square, got "
        f"{game.start_room.key!r}"
    )
    # Macro map loaded too, and the two files' cross-map exit resolved.
    assert "Overworld Trailhead" in game.rooms
    assert (
        game.rooms["Crossroads Trail"].exits["south"]
        is game.rooms["Overworld Trailhead"]
    ), "demo_town.json's Crossroads Trail should exit south into the macro map"

    # Chargen through all four paths -- the whole point of this gate.
    from basegame.chargen import PATH_ORDER
    placed = []
    for index, path_id in enumerate(PATH_ORDER, start=1):
        char = asyncio.run(_run_chargen_for_path(game, index, path_id))
        placed.append(char)
    assert len(placed) == 4 and len({c.bg_path for c in placed}) == 4, (
        "all four distinct paths should have chargen'd successfully"
    )

    # Walk the town via the REAL dispatch path (commands.dispatch), not a
    # direct move_to -- proves the verb table + look/move plumbing work
    # end to end for a basegame character, same as a real player types.
    walker = placed[0]
    walker.session = _FakeSession([])
    dispatch = hooks.get_dispatch()
    assert dispatch is commands_mod.dispatch
    dispatch(walker, "look", game)
    assert any("Town Square" in line for line in walker.session.lines), (
        walker.session.lines
    )
    dispatch(walker, "south", game)
    assert walker.location.key == "Inn", walker.location.key
    dispatch(walker, "south", game)
    assert walker.location.key == "Crossroads Trail", walker.location.key
    dispatch(walker, "south", game)
    assert walker.location.key == "Overworld Trailhead", walker.location.key
    assert walker.location.wilderness is True, (
        "the macro map's landmark rooms should be wilderness-tagged"
    )

    # Stage 3: the generic weather framework should render a clause on an
    # outdoor room (Overworld Trailhead) but not on an indoor one (Inn).
    from engine.systems import weather as weather_module
    outdoor_clause = weather_module.look_clause(walker.location, game)
    assert outdoor_clause, "an outdoor room should get a weather clause"
    indoor_clause = weather_module.look_clause(game.rooms["Inn"], game)
    assert indoor_clause is None, "an indoor room should get no weather clause"
    # Advancing well past ROLL_INTERVAL_TICKS should let the condition
    # change (roll again enough times that "never differs" would be a
    # statistically dead giveaway of a broken reroll, not bad luck).
    first_condition = weather_module.current_condition(game)
    seen = {first_condition}
    for _ in range(40):
        game.game_time_ticks += weather_module.ROLL_INTERVAL_TICKS
        weather_module.tick(game)
        seen.add(weather_module.current_condition(game))
    assert len(seen) > 1, (
        "weather should reroll to something else across 40 forced ticks "
        f"(stuck on {first_condition!r} -- reroll scheduling is broken)"
    )

    dispatch(walker, "north", game)
    dispatch(walker, "north", game)
    dispatch(walker, "north", game)
    assert walker.location.key == "Town Square", walker.location.key

    dispatch(walker, "help paths", game)
    joined = " ".join(walker.session.lines)
    assert "Detective" in joined and "Ranger" in joined, walker.session.lines

    # `score` -- basegame's own verb, sheet built on the shared engine spine.
    from engine import stats as engine_stats
    walker.session = _FakeSession([])
    dispatch(walker, "score", game)
    sheet = " ".join(walker.session.lines)
    assert "Detective" in sheet, walker.session.lines
    for name in engine_stats.STAT_NAMES:
        assert name in sheet, (name, walker.session.lines)
    assert "Tier" in sheet and "HP" in sheet, walker.session.lines

    # Stage 9: engine mail kit + basegame Post Office proof.
    assert "mail" in commands_mod.COMMANDS
    assert "mail" in topics
    assert "Post Office" in game.rooms
    post = game.rooms["Post Office"]
    assert "mail" in (post.resources or ())
    peer = placed[1]
    peer.session = _FakeSession([])
    walker.move_to(post)
    walker.session = _FakeSession([])
    dispatch(walker, f"mail send {peer.key} hello from square", game)
    assert any("send a letter" in line.lower() for line in walker.session.lines), (
        walker.session.lines
    )
    assert peer.mail_inbox and peer.mail_inbox[0]["text"] == "hello from square"
    peer.session = _FakeSession([])
    dispatch(peer, "mail", game)
    assert any("hello from square" in line or "Inbox" in line for line in peer.session.lines), (
        peer.session.lines
    )

    # The tick pipeline (engine-owned since Stage 1) runs cleanly with
    # basegame's own (currently empty) handler set.
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
