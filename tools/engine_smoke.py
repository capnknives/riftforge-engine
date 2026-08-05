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


# Repo root must win over any unrelated ``engine`` install on sys.path before
# the module-level economy import below (CI has no collision; local dev may).
_ensure_repo_on_path()
import engine.systems.economy as economy_wallet


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

    from world import Character, Item, Room
    from engine import hooks
    from engine.verbs.basic import cmd_idlemode, cmd_who

    c = Character("LeanEngine")
    assert c.key == "LeanEngine"
    assert c.origin == "mundane", c.origin
    # stats/tier are generic engine content now (engine/stats.py) -- a bare
    # Character gets the real default spine, not a SUPERS-only extra.
    assert c.stats == {
        "POW": 5.0, "VIT": 5.0, "FOC": 5.0, "FIN": 5.0, "RES": 5.0, "PRE": 5.0,
    }, c.stats
    assert c.tier == 0

    # Stage 8 two-repo purity: Room sheds its SUPERS-shaped defaults the
    # same way Character already had (attach_room hook, engine/hooks.py).
    # A bare engine Room must not carry any SUPERS room-flavor field...
    r = Room("a lean room")
    for supers_only in (
        "croatoan_blood", "devils_gate", "vampire_nest", "city_name",
        "hospital", "consecrated", "is_house", "vendor_stock",
    ):
        assert not hasattr(r, supers_only), (supers_only, r.__dict__.keys())
    # ...but keeps every generic engine-owned Room field with its original
    # default (engine/verbs/basic.py, engine/vision.py, engine/game_calendar.py,
    # engine/systems/weather.py, engine/persistence.py all read these directly).
    assert r.gravity == 1.0
    assert r.wilderness is False
    assert r.outdoor is False
    assert r.area_type == "plains"
    assert r.dark is False
    assert r.hidden_directions == ()
    assert r.no_combat is False

    # Stage G: map-JSON stamper hook defaults to no-op (lean boot ignores
    # SUPERS-only room keys). Registering a fake stamper must fire once.
    seen = []

    def _fake_stamp(room, room_data, *, filename=None):
        seen.append((room.key, room_data.get("vampire_safe"), filename))

    hooks.set_map_room_stamper(_fake_stamp)
    hooks.stamp_map_room(r, {"vampire_safe": True}, filename="t.json")
    assert seen == [("a lean room", True, "t.json")], seen
    hooks.set_map_room_stamper(None)
    # Cleared stamper must not raise / mutate.
    hooks.stamp_map_room(r, {"vampire_safe": False}, filename="t2.json")
    assert seen == [("a lean room", True, "t.json")], seen

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

    # Extra vs purity subprocess: lean maps (T3 lean-demo) — one room, not
    # the full monorepo content/maps tree. Configure before load_all_maps.
    os.environ["RIFTFORGE_GAME"] = "none"
    import game_select as game_select_mod
    game_select_mod._reset_for_tests()
    assert game_select_mod.game_name() == "none"
    import maps
    rooms, start_room, seed_items = maps.load_all_maps()
    assert isinstance(rooms, dict) and rooms, "maps.load_all_maps should build rooms"
    assert start_room is not None, "at least one map should mark is_start"
    assert isinstance(seed_items, list)
    assert len(rooms) == 1, (
        f"lean demo should be one room, got {len(rooms)} "
        f"(maps_dir={maps.get_maps_dir()!r})"
    )
    assert start_room.key in ("Demo Start", "DT00001"), start_room.key

    # Phase 4b: soft-optional commands + server with SUPERS absent.
    import commands as commands_mod
    assert "look" in commands_mod.COMMANDS
    assert "attack" not in commands_mod.COMMANDS, (
        "SUPERS verbs should be absent when supers is missing"
    )
    assert hooks.get_dispatch() is commands_mod.dispatch

    import server as server_mod
    assert server_mod._HAS_SUPERS is False
    # Lean Game: maps + persistence, no Cadence seed; still one-room demo.
    lean_game = server_mod.Game(db_path=":memory:")
    assert lean_game.start_room is not None
    assert lean_game.start_room.key in ("Demo Start", "DT00001"), lean_game.start_room.key
    assert len(lean_game.rooms) == 1, len(lean_game.rooms)
    assert lean_game.find_character("a training dummy") is None

    # Stage 1 two-repo purity: the tick pipeline is generic engine
    # infrastructure now, not gated behind SUPERS presence -- a lean boot
    # should run a (empty) heartbeat with zero registered handlers. No game
    # has registered anything yet, so `_tick_handlers` may not even exist
    # (register_tick lazily creates it) -- run_ticks tolerates that via
    # getattr(..., ()).
    server_mod.run_ticks(lean_game)
    assert getattr(lean_game, "_tick_handlers", []) == []
    assert len(lean_game._tick_stats) == 1

    import asyncio
    asyncio.run(server_mod.run_ticks_async(lean_game))
    assert len(lean_game._tick_stats) == 2
    lean_game.db.close()

    # Catalog-loader foundation: engine.content_store / content_validate
    # moved out of supers/ in Stage 1 -- prove they work with SUPERS absent.
    import tempfile
    from engine import content_store, content_validate

    content_validate.require_keys({"id": "a", "name": "b"}, ["id", "name"], "test")
    content_validate.unique_ids([{"id": "a"}, {"id": "b"}], "test")
    try:
        content_validate.unique_ids([{"id": "a"}, {"id": "a"}], "test")
        raise AssertionError("duplicate ids should raise")
    except AssertionError as exc:
        assert "duplicate" in str(exc)

    content_store.require_snake_id("a_valid_id")
    try:
        content_store.require_snake_id("Not Valid")
        raise AssertionError("bad snake_case id should raise")
    except ValueError:
        pass

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = os.path.join(tmpdir, "roundtrip.json")
        content_store.save_json(tmp_path, {"a": 1})
        assert content_store.load_json(tmp_path) == {"a": 1}

    from engine import tick_registry

    fired = []
    tick_registry.register_tick(lean_game, lambda g: fired.append(g), order=5, name="probe")
    tick_registry.run_ticks(lean_game)
    assert fired == [lean_game]
    tick_registry.clear_ticks(lean_game)
    assert lean_game._tick_handlers == []

    # Stage 4 two-repo purity: the capped 0-1 meter kit is generic engine
    # content now (engine/systems/needs.py) -- any object can carry any
    # named meter, with zero SUPERS meter names (hunger/thirst/...) baked in.
    from engine.systems import needs as needs_engine

    class _MeterBlob:
        pass

    blob = _MeterBlob()
    needs_engine.attach_meters(blob, ("mock_a", "mock_b"))
    assert blob.mock_a == 0.0 and blob.mock_b == 0.0

    rate = needs_engine.seek_rate(10)
    assert rate == needs_engine.SEEK_THRESHOLD / 10
    for _ in range(10):
        needs_engine.advance(blob, "mock_a", rate)
    assert abs(blob.mock_a - needs_engine.SEEK_THRESHOLD) < 1e-9
    assert needs_engine.is_critical(blob, "mock_a") is False

    blob.mock_a = 1.0
    assert needs_engine.is_critical(blob, "mock_a") is True
    assert needs_engine.most_urgent(blob, ("mock_a", "mock_b")) == ("mock_a", 1.0)
    assert needs_engine.most_urgent(
        blob, ("mock_a", "mock_b"), skip=("mock_a",),
    ) is None

    needs_engine.satisfy(blob, "mock_a")
    assert blob.mock_a == 0.0
    blob.mock_b = 0.5
    level = needs_engine.sate_ambient(blob, "mock_b", 0.2)
    assert level == 0.3 and blob.mock_b == 0.3

    assert needs_engine.level_phrase(0.0) == "a little"
    assert needs_engine.level_phrase(0.99) == "critically"

    dumped = needs_engine.dump_meters(blob, ("mock_a", "mock_b"))
    assert dumped == {"mock_a": 0.0, "mock_b": 0.3}
    needs_engine.load_meters(blob, ("mock_a", "mock_b"), {"mock_a": 0.42})
    assert blob.mock_a == 0.42 and blob.mock_b == 0.0
    needs_engine.clamp_meters(blob, ("mock_a",))
    assert blob.mock_a == 0.42

    # Stage 7 two-repo purity: the weighted-outcome roll mechanism behind
    # SUPERS' hit/dodge/block/critical reaction roll is generic engine
    # content now (engine/systems/combat_core.py) -- the mechanism doesn't
    # know or care what the outcome names mean.
    from engine.systems import combat_core

    # Weights sum well under the reserve -- no rescale, first bucket wins
    # at roll=0.0, falls through to default past the last bucket.
    weights = [("a", 0.2), ("b", 0.3)]
    assert combat_core.roll_weighted_outcome(weights, default="z", rng=lambda: 0.0) == "a"
    assert combat_core.roll_weighted_outcome(weights, default="z", rng=lambda: 0.25) == "b"
    assert combat_core.roll_weighted_outcome(weights, default="z", rng=lambda: 0.9) == "z"

    # Weights sum past (1 - reserve) -- proportional rescale keeps `default`
    # guaranteed at least `reserve` share: a roll just under 1.0 still falls
    # through to default even though raw weights summed to 1.0.
    big_weights = [("a", 0.5), ("b", 0.5)]
    assert combat_core.roll_weighted_outcome(
        big_weights, default="z", reserve=0.10, rng=lambda: 0.99,
    ) == "z"
    assert combat_core.roll_weighted_outcome(
        big_weights, default="z", reserve=0.10, rng=lambda: 0.0,
    ) == "a"

    # Stage 6 two-repo purity: the room-graph BFS mechanism is generic
    # engine content now (engine/pathfind.py) -- an injected edge_ok
    # callback decides passability; the mechanism doesn't know evil_zone,
    # lodging, or pocket enter aliases.
    from engine import pathfind as pathfind_engine

    a = Room("a")
    b = Room("b")
    c = Room("c")
    a.exits = {"north": b}
    b.exits = {"north": c, "south": a}
    c.exits = {"south": b}

    def _edge_ok(_from_room, neighbor):
        return neighbor is not None

    assert pathfind_engine.path_directions_to(
        a, lambda r: r is c, edge_ok=_edge_ok,
    ) == ["north", "north"]
    assert pathfind_engine.next_step_toward(
        a, lambda r: r is c, edge_ok=_edge_ok,
    ) == "north"
    assert pathfind_engine.path_to_room(a, c, edge_ok=_edge_ok) == [
        "north", "north",
    ]
    # Already at goal -> empty path / None first hop.
    assert pathfind_engine.path_directions_to(
        a, lambda r: r is a, edge_ok=_edge_ok,
    ) == []
    assert pathfind_engine.next_step_toward(
        a, lambda r: r is a, edge_ok=_edge_ok,
    ) is None
    # max_nodes cap: start expands one neighbor (seen=2); limit 2 stops
    # before reaching c two hops away.
    assert pathfind_engine.path_directions_to(
        a, lambda r: r is c, edge_ok=_edge_ok, max_nodes=2,
    ) == []

    # Stage 5 two-repo purity: the wallet / bank ledger is generic engine
    # content now (engine/systems/economy.py) -- format_money, deposit /
    # withdraw, can_afford. Vendor stock, gig work, and Cadence stipends
    # stay in supers.
    from engine.systems import economy as economy_engine

    assert economy_engine.format_money(0) == "$0"
    assert economy_engine.format_money(15) == "$15"
    assert economy_engine.format_money(-3) == "-$3"
    assert economy_engine.money_noun() == "dollars"
    assert economy_engine.money_noun(plural=False) == "dollar"
    assert economy_engine.money_score_label() == "Cash"

    class _WalletBlob:
        pass

    purse = _WalletBlob()
    economy_wallet.set_wallet(purse, 40, 0)
    purse.bank_dollars = 10
    assert economy_engine.wallet_balance(purse) == 40
    assert economy_engine.bank_balance(purse) == 10
    assert economy_engine.can_afford(purse, 40) is True
    assert economy_engine.can_afford(purse, 41) is False
    ok, _msg = economy_engine.deposit(purse, 15)
    assert ok and economy_wallet.wallet_dollars(purse) == 25 and purse.bank_dollars == 25
    ok, _msg = economy_engine.withdraw(purse, 5)
    assert ok and economy_wallet.wallet_dollars(purse) == 30 and purse.bank_dollars == 20
    ok, _msg = economy_engine.deposit(purse, 999)
    assert ok is False

    # Stage 9 two-repo purity: text mail inbox, canned-social catalog
    # perform, and stacked clothing wear map are generic engine content
    # now. SUPERS keeps ship/courier, socials.json copy, and restring.
    from engine.systems import mail as mail_engine
    from engine.systems import social_catalog as social_engine
    from engine.systems import wearables as wear_engine
    from world import Item

    class _MailBlob:
        pass

    alice = _MailBlob()
    alice.key = "Alice"
    alice.session = None
    alice.location = Room("post")
    alice.location.resources = ("mail",)
    bob = _MailBlob()
    bob.key = "Bob"
    bob.session = None
    bob.mail_inbox = []

    class _MailGame:
        def find_character(self, name):
            if name.lower() == "bob":
                return bob
            return None

        game_time_ticks = 0

    ok, msg = mail_engine.send_mail(alice, "Bob", "hello there", _MailGame())
    assert ok and bob.mail_inbox[0]["text"] == "hello there", msg
    assert "Alice" in mail_engine.format_list(bob)[1]
    ok, body = mail_engine.read_letter(bob, 1)
    assert ok and "hello there" in body
    ok, _ = mail_engine.discard_letter(bob, "1")
    assert ok and bob.mail_inbox == []

    catalog = {
        "wave": {
            "help": "wave [name]",
            "solo": {
                "self": "You wave.",
                "others": "{actor} waves.",
            },
            "targeted": {
                "self": "You wave at {target}.",
                "target": "{actor} waves at you.",
                "others": "{actor} waves at {target}.",
            },
        },
    }
    social_engine.validate_social_catalog(catalog)
    assert social_engine.resolve_verb("wave", catalog) == "wave"

    class _SocialRoom:
        def __init__(self):
            self.lines = []

        def characters(self):
            return []

        def broadcast(self, text, exclude=None):
            self.lines.append(text)

    actor = Character("WaveActor")
    actor.location = _SocialRoom()
    ok, line = social_engine.perform(
        actor, catalog, "wave", "", None,
        find_in_room=lambda _n, _c: None,
    )
    assert ok and line == "You wave."
    assert actor.location.lines and "waves." in actor.location.lines[0]
    assert "WaveActor" in actor.location.lines[0]

    wearer = Character("Wearer")
    tee = Item("a tee", "A plain tee.")
    tee.layer = "clothing"
    tee.slot = "body"
    wearer.inventory = [tee]

    def _is_cloth(p):
        return getattr(p, "layer", None) == "clothing"

    def _slot(p):
        return getattr(p, "slot", None) if _is_cloth(p) else None

    def _name(p, _c=None):
        return p.key

    ok, msg = wear_engine.wear_piece(
        wearer, tee, is_clothing=_is_cloth, slot_for=_slot, display_key=_name,
    )
    assert ok and tee.worn is True, msg
    assert list(wear_engine.iter_worn_clothing(wearer)) == [("body", tee)]
    ok, msg, removed = wear_engine.remove_piece(
        wearer, "body",
        is_clothing=_is_cloth, slot_for=_slot, display_key=_name,
        find_item=lambda _n, cands: cands[0] if cands else None,
    )
    assert ok and removed is tee and tee.worn is False, msg

    # ---- combat engine plugin architecture ----
    from engine.systems import combat_engine
    from engine.systems import combat_martial_arts
    from engine.systems import combat_mundane
    from engine.systems import combat_osr

    known = combat_engine.known_combat_engines()
    assert {"mundane", "martial_arts", "osr"}.issubset(known), known

    class _SwingPair:
        """Throwaway attacker/defender stand-ins for engine combat smokes."""

        def __init__(self, key, hp=100.0):
            self.key = key
            self.hp = hp

    mundane_attacker = _SwingPair("MundaneA")
    mundane_defender = _SwingPair("MundaneD", hp=100.0)
    # rng=0.0 lands the first weighted bucket -> critical (16 damage).
    mundane_res = combat_engine.resolve_swing(
        "mundane", mundane_attacker, mundane_defender, rng=lambda: 0.0,
    )
    assert mundane_res is not None, mundane_res
    assert mundane_res["result"]["outcome"] == "critical", mundane_res
    assert mundane_res["result"]["damage"] == combat_mundane.DAMAGE_PER_CRITICAL
    assert mundane_defender.hp == (
        100.0 - combat_mundane.DAMAGE_PER_CRITICAL
    ), mundane_defender.hp

    # rng=1.0 falls past every weighted bucket -> miss, zero damage.
    mundane_defender.hp = 100.0
    miss_res = combat_engine.resolve_swing(
        "mundane", mundane_attacker, mundane_defender, rng=lambda: 1.0,
    )
    assert miss_res["result"]["outcome"] == "miss", miss_res
    assert miss_res["result"]["damage"] == 0.0
    assert mundane_defender.hp == 100.0

    ma_attacker = _SwingPair("MaA")
    ma_defender = _SwingPair("MaD", hp=100.0)
    ma_attacker.martial_stance = "strike"
    ma_defender.martial_stance = "grapple"
    ma_res = combat_engine.resolve_swing(
        "martial_arts", ma_attacker, ma_defender, rng=lambda: 0.0,
    )
    assert ma_res is not None, ma_res
    assert ma_res["result"]["outcome"] == "advantage", ma_res
    expected_ma_damage = (
        combat_martial_arts.BASE_DAMAGE
        + combat_martial_arts.COMBO_BONUS_PER_STACK * 1
    )
    assert ma_res["result"]["damage"] == expected_ma_damage, ma_res
    assert ma_res["result"]["combo"] == 1
    assert ma_attacker.martial_combo == 1
    assert ma_defender.hp == 100.0 - expected_ma_damage

    assert combat_engine.resolve_swing(
        "totally-unknown-id", mundane_attacker, mundane_defender,
    ) is None

    osr_attacker = _SwingPair("OsrA")
    osr_defender = _SwingPair("OsrD", hp=100.0)
    osr_attacker.osr_attack_bonus = 5
    osr_attacker.osr_damage_die = 6
    osr_attacker.osr_damage_bonus = 2
    osr_defender.armor_class = 10
    # rng=0.95 -> d20=20 (nat 20 crit) on a 20-sided roll.
    osr_res = combat_engine.resolve_swing(
        "osr", osr_attacker, osr_defender, rng=lambda: 0.95,
    )
    assert osr_res is not None, osr_res
    assert osr_res["brief"]["engine"] == "osr", osr_res
    assert osr_res["result"]["outcome"] == "critical", osr_res
    assert osr_res["result"]["damage"] >= 4, osr_res
    assert osr_defender.hp < 100.0

    osr_defender.hp = 100.0
    miss_osr = combat_engine.resolve_swing(
        "osr", osr_attacker, osr_defender, rng=lambda: 0.0,
    )
    assert miss_osr["result"]["outcome"] == "miss", miss_osr
    assert miss_osr["result"]["damage"] == 0.0
    assert osr_defender.hp == 100.0

    # ---- civic shop framework ----
    from engine.systems import civic_shop

    wares = [
        {
            "key": "a bolt",
            "description": "A hex bolt.",
            "price_cents": 199,
            "qty": 2,
        },
        {
            "key": "a nail",
            "description": "A box of nails.",
            "price_cents": 50,
            "qty": None,
        },
    ]
    assert civic_shop.find_ware(wares, "bolt") is wares[0]
    assert civic_shop.find_ware(wares, "nail") is wares[1]
    assert civic_shop.find_ware(wares, "missing") is None

    class _Shopper:
        pass

    shopper = _Shopper()
    economy_wallet.set_wallet(shopper, 1, 0)
    shopper.inventory = []
    ok, msg = civic_shop.buy(shopper, wares, wares[0])
    assert ok is False and "afford" in msg.lower(), (ok, msg)

    economy_wallet.set_wallet(shopper, 5, 0)
    ok, msg = civic_shop.buy(shopper, wares, wares[0])
    assert ok and shopper.inventory and wares[0]["qty"] == 1, (ok, msg, wares[0])

    wares[0]["qty"] = 0
    ok, msg = civic_shop.buy(shopper, wares, wares[0])
    assert ok is False and "stock" in msg.lower(), (ok, msg)

    ok, msg = civic_shop.sell(shopper, wares, "bolt", 99)
    assert ok, (ok, msg)
    assert economy_wallet.wallet_total_cents(shopper) == 301 + 99, (
        economy_wallet.wallet_total_cents(shopper)
    )
    assert wares[0]["qty"] == 1, wares[0]

    # ---- clinic framework ----
    from engine.systems import clinic
    from world import Room

    class _Patient:
        pass

    patient = _Patient()
    patient.key = "Patient"
    patient.hp = 0.0
    patient.hp_cap = 10.0
    patient.location = Room("street")
    patient.hospitalized = False
    patient.hospital_until_tick = 0
    patient.downed = False
    patient.downed_until_tick = 0

    ward = Room("ward")
    ward.hospital = True

    class _ClinicGame:
        game_time_ticks = 0
        rooms = {"ward": ward}

    clinic.enter_ko(patient, game=_ClinicGame())
    assert clinic.is_ko(patient)
    assert clinic.admit(patient, ward, game=_ClinicGame())
    assert patient.hospitalized and patient.location is ward
    clinic.discharge(patient)
    assert not patient.hospitalized

    from world import Character

    cg = _ClinicGame()
    cg.characters = set()
    tick_patient = Character("TickPatient")
    tick_patient.hp = 0.0
    tick_patient.hp_cap = 10.0
    tick_patient.location = Room("street")
    clinic.enter_ko(tick_patient, until_tick=1, game=cg)
    cg.characters.add(tick_patient)
    cg.game_time_ticks = 2
    clinic.tick(cg)
    assert tick_patient.hospitalized, tick_patient.__dict__

    # ---- justice framework ----
    from engine.systems import justice
    from engine import hooks

    class _Suspect:
        pass

    suspect = _Suspect()
    suspect.key = "Suspect"
    suspect.wanted = False
    suspect.fine_owed_cents = 0
    suspect.jail_until_tick = None
    suspect.location = Room("cell")
    suspect.location.is_cell = True
    economy_wallet.set_wallet(suspect, 10, 0)

    class _JusticeGame:
        game_time_ticks = 0
        rooms = {"cell": suspect.location}

    justice.mark_wanted(suspect)
    assert justice.is_wanted(suspect)
    assert justice.jail(suspect, suspect.location, until_tick=5, game=_JusticeGame())
    assert justice.is_jailed(suspect, _JusticeGame())
    block = justice.move_gate_block(
        suspect, suspect.location, Room("outside"), _JusticeGame(),
    )
    assert block
    ok, _msg = justice.pay_fine(suspect, game=_JusticeGame())
    assert ok
    jg = _JusticeGame()
    jg.game_time_ticks = 6
    justice.tick(jg)
    assert not justice.is_jailed(suspect, jg)

    # ---- breach framework ----
    from engine.systems import breach

    saloon = Room("saloon")
    saloon.layout_x = 0
    saloon.layout_y = 0
    saloon.layout_z = 0
    saloon.slam_targets = [
        {
            "id": "wall",
            "label": "the wall",
            "direction": "east",
            "hp_max": 8,
            "tags": ["wall"],
        }
    ]
    alley = Room("alley")
    alley.layout_x = 1
    alley.layout_y = 0
    alley.layout_z = 0

    class _BreachGame:
        rooms = {"saloon": saloon, "alley": alley}

    bg = _BreachGame()
    picked = breach.pick_slam_target(saloon, rng=lambda: 0.0)
    assert picked and picked["id"] == "wall"
    res = breach.apply_slam_damage(bg, saloon, "wall", 8)
    assert res["wrecked"], res
    from world import Character
    bruiser = Character("Bruiser")
    bruiser.location = saloon
    assert breach.breach_eject(bruiser, saloon, picked, game=bg)
    assert bruiser.location is alley

    # ---- origin registry + Alien self-registration ----
    from engine.systems import origin_registry
    from engine.systems import origin_alien  # noqa: F401 -- self-registers
    from engine.systems import umbral as umbral_mod

    assert "alien" in origin_registry.known_origins(), (
        origin_registry.known_origins()
    )
    alien = origin_registry.get_origin("alien")
    assert alien is not None
    assert alien["name"] == "Alien"
    assert callable(alien["chargen_step"])
    assert callable(alien["on_attach"])
    # Mundane is never registered -- it is the engine default only.
    assert "mundane" not in origin_registry.known_origins()

    umbral_char = Character("UmbralSmoke")
    assert umbral_char.origin == "mundane"
    umbral_mod.ensure_umbral_defaults(umbral_char)
    umbral_char.bg_umbral = True
    assert umbral_mod.is_umbral(umbral_char)
    umbral_char.session = _FakeSession()
    umbral_char.umbral_charge = 1.0

    class _NightGame:
        game_time_ticks = 0  # midnight -> night on the calendar

        def __init__(self):
            # iter_characters prefers a set roster when present.
            self.characters = {umbral_char}

    night = _NightGame()
    umbral_mod.cmd_shroud(umbral_char, "", night)
    assert umbral_char.umbral_shrouded is True
    assert umbral_char.stealth_active is True
    # Daylight refusal.
    day = _NightGame()
    day.game_time_ticks = 12 * 400  # noon (TICKS_PER_HOUR = 400)
    umbral_char.umbral_shrouded = False
    umbral_char.stealth_active = False
    umbral_mod.cmd_shroud(umbral_char, "", day)
    assert umbral_char.umbral_shrouded is False
    # Night again, then drain-to-clear via tick.
    umbral_mod.cmd_shroud(umbral_char, "", night)
    assert umbral_char.umbral_shrouded is True
    umbral_char.umbral_charge = umbral_mod.UMBRAL_CHARGE_STEP
    umbral_mod.tick(night)
    assert umbral_char.umbral_shrouded is False
    assert umbral_char.stealth_active is False

    from engine.systems import sheet as sheet_mod

    profile = sheet_mod.sheet_profile()
    assert profile.get("id") == "sheet.engine"
    sheet_mod.register_field_hook(
        "hp",
        lambda ctx: f"  HP: {getattr(ctx.target, 'hp', 0)}",
    )
    text = sheet_mod.render_score(
        sheet_mod.SheetContext(target=c, viewer=c)
    )
    assert "POW" in text and "HP:" in text

    print("engine_smoke_ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
