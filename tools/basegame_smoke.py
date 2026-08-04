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

    from basegame import gates as gates_mod
    gate_rooms = gates_mod.all_gate_rooms(game)
    assert len(gate_rooms) == 4, (
        f"expected four rift_gate mouths, got {len(gate_rooms)}"
    )
    open_keys = set(getattr(game, gates_mod.NEXUS.open_attr) or ())
    assert len(open_keys) == gates_mod.OPEN_GATE_COUNT, (
        f"boot should open {gates_mod.OPEN_GATE_COUNT} mouths, got {open_keys!r}"
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

    dispatch(walker, "exit", game)
    macro = overland_mod._parse_pos_pair(walker.macro_pos)
    assert macro == (35, 10), macro
    for _ in range(10):
        dispatch(walker, "east", game)
    macro = overland_mod._parse_pos_pair(walker.macro_pos)
    micro = overland_mod._parse_pos_pair(walker.micro_pos)
    assert macro == (36, 10), macro
    assert micro == (5, 5), micro
    dispatch(walker, "enter rift nexus", game)
    assert walker.location.key == "RN00001", walker.location.key

    hub = walker.location
    closed_dirs = [
        direction
        for direction, dest in hub.exits.items()
        if getattr(dest, "rift_gate", False)
        and dest.key not in open_keys
    ]
    visible_dirs = {
        direction
        for direction, _dest in gates_mod.visible_exits(hub, game)
    }
    assert closed_dirs, "need at least one closed mouth for visibility check"
    for direction in closed_dirs:
        assert direction not in visible_dirs, (
            f"closed mouth exit {direction!r} should be hidden"
        )

    rotate_at = int(getattr(game, gates_mod.NEXUS.rotate_at_attr, 0) or 0)
    game.game_time_ticks = rotate_at
    gates_mod.tick_rotation(game)
    rotated_keys = set(getattr(game, gates_mod.NEXUS.open_attr) or ())
    assert rotated_keys != open_keys, (
        f"tick rotation should change open set; still {rotated_keys!r}"
    )

    dispatch(walker, "exit", game)
    assert overland_mod.overland_mode(walker) == "on_foot", (
        "exit from Rift Nexus should return to overland"
    )
    macro = overland_mod._parse_pos_pair(walker.macro_pos)
    assert macro == (36, 10), macro

    walker.move_to(game.rooms["NB00001"])
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

    from engine.systems import needs as needs_engine
    from basegame import needs as basegame_needs

    assert walker.hunger == 0.0 and walker.thirst == 0.0, (
        "fresh characters should start with satisfied hunger/thirst"
    )
    registered = needs_engine.registered_meters()
    assert "hunger" in registered and "thirst" in registered, registered
    for _ in range(10):
        basegame_needs.tick_demo_needs(game)
    assert walker.hunger > 0.0 and walker.thirst > 0.0, (
        "demo needs tick should raise hunger/thirst"
    )
    needs_engine.satisfy(walker, "hunger")
    assert walker.hunger == 0.0 and walker.thirst > 0.0
    needs_engine.satisfy(walker, "thirst")
    assert walker.thirst == 0.0

    from engine.systems import spawn as spawn_engine
    from basegame import bestiary as bestiary_mod
    from basegame import spawn_nests as spawn_nests_mod

    pool = bestiary_mod.get_pool(["prairie-critter"], 0)
    assert len(pool) >= 2, (
        f"prairie-critter pool should list both templates, got {len(pool)}"
    )
    assert "critter" in spawn_engine.known_nest_ai(), (
        "basegame boot should register critter nest AI"
    )

    den = game.rooms.get("NB00009")
    assert den is not None, "grain elevator shed (NB00009) missing from world"
    assert getattr(den, "spawn_nest", None) == "critter", (
        f"NB00009 should be a critter den, got spawn_nest={getattr(den, 'spawn_nest', None)!r}"
    )

    catalog = spawn_nests_mod._load_catalog()
    spec = catalog["critter"]
    old_chance = spec["spawn_chance_per_tick"]
    spec["spawn_chance_per_tick"] = 1.0
    game._nest_rooms_cache = None
    try:
        spawn_nests_mod.tick_nests(game)
    finally:
        spec["spawn_chance_per_tick"] = old_chance

    occupants = spawn_nests_mod._nest_occupants(den)
    assert len(occupants) >= 1, (
        "critter nest tick should top up at least one hostile in the shed"
    )
    assert all(getattr(n, "is_npc", False) for n in occupants), (
        "nest occupants must be NPCs"
    )

    from basegame import combat as combat_mod

    dummy = placed[2]
    walker.hp = 100.0
    dummy.hp = 100.0
    walker.target = dummy

    # rng=lambda: 0.0 always lands the first weighted bucket ("critical")
    # -- deterministic single-swing check, no flaky random.random() rolls.
    result = combat_mod.resolve_swing(walker, dummy, rng=lambda: 0.0)
    assert result["outcome"] == "critical", result
    assert dummy.hp == 100.0 - combat_mod.DAMAGE_PER_CRITICAL, dummy.hp

    # A guaranteed-miss rng (1.0, past every weighted bucket) proves the
    # tick-registered resolve_round path also runs cleanly with no damage.
    dummy.hp = 100.0
    combat_mod.resolve_round(game, rng=lambda: 1.0)
    assert dummy.hp == 100.0, "guaranteed-miss round should not damage dummy"
    walker.target = None

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
