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
    """Scripted chargen answers: path + zero stat bonuses + Mundane origin.

    Origin menu is Mundane (1) plus sorted registered origins; Mundane is
    always option 1 so existing path-smoke characters stay ordinary humans.
    """
    from engine import stats as engine_stats
    return [str(path_index)] + ["0"] * len(engine_stats.STAT_NAMES) + ["1"]


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
    from engine.__main__ import default_entry_game

    assert default_entry_game() == "basegame"
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
    assert "shop" in topics
    assert "clinic" in topics
    assert "origins" in topics
    assert "bug" in topics
    assert "hedit" in topics
    assert "helpsubmit" in topics
    assert "How you play" not in topics["bug"]  # engine meta pages are lean
    assert "hedit <keyword>" in topics["hedit"]
    assert "shroud" in commands_mod.COMMANDS
    assert "unshroud" in commands_mod.COMMANDS

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
    assert len(placed) == len(PATH_ORDER)

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

    from engine.systems import economy as economy_mod
    from engine.systems import civic_shop as civic_shop_mod

    store = game.rooms.get("NB00004")
    assert store is not None and getattr(store, "shop_stock", None), (
        "General Store should load shop_stock from zone JSON"
    )
    walker.move_to(store)
    economy_mod.credit_wallet(walker, dollars=20)
    lantern = civic_shop_mod.find_ware(store.shop_stock, "lantern")
    assert lantern is not None
    before_qty = lantern["qty"]
    before_wallet = economy_mod.wallet_total_cents(walker)
    walker.session = _FakeSession([])
    dispatch(walker, "buy lantern", game)
    assert walker.inventory, walker.session.lines
    assert lantern["qty"] == before_qty - 1, lantern
    assert economy_mod.wallet_total_cents(walker) == (
        before_wallet - lantern["price_cents"]
    ), (
        economy_mod.wallet_total_cents(walker), before_wallet, lantern
    )

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

    # Mixed combat_engine dispatch: mundane default vs martial_arts + fallback.
    from engine.systems import combat_martial_arts
    from engine.systems import combat_mundane
    from basegame import stats as stats_module

    mundane_fighter = placed[0]
    martial_fighter = placed[3]
    mundane_fighter.combat_engine = None
    martial_fighter.combat_engine = "martial_arts"
    mundane_fighter.hp = stats_module.max_hp(mundane_fighter)
    martial_fighter.hp = stats_module.max_hp(martial_fighter)
    mundane_fighter.martial_combo = 0
    martial_fighter.martial_combo = 0
    martial_fighter.martial_stance = "strike"
    mundane_fighter.martial_stance = "grapple"
    mundane_fighter.move_to(game.rooms["NB00001"])
    martial_fighter.move_to(game.rooms["NB00001"])
    mundane_fighter.target = martial_fighter
    martial_fighter.target = mundane_fighter

    mundane_hp_before = mundane_fighter.hp
    martial_hp_before = martial_fighter.hp
    combat_mod.resolve_round(game, rng=lambda: 0.0)
    # Martial fighter swings at mundane (strike beats grapple -> advantage).
    expected_ma_dmg = (
        combat_martial_arts.BASE_DAMAGE
        + combat_martial_arts.COMBO_BONUS_PER_STACK * 1
    )
    assert martial_fighter.martial_combo == 1, (
        f"martial_combo should persist on character, got {martial_fighter.martial_combo!r}"
    )
    assert mundane_fighter.hp == mundane_hp_before - expected_ma_dmg, (
        f"mundane victim hp {mundane_fighter.hp!r} vs expected "
        f"{mundane_hp_before - expected_ma_dmg!r}"
    )
    # Mundane fighter swings back (rng=0.0 -> critical on mundane engine).
    assert martial_fighter.hp == (
        martial_hp_before - combat_mundane.DAMAGE_PER_CRITICAL
    ), martial_fighter.hp

    garbage_fighter = placed[1]
    garbage_fighter.combat_engine = "nonsense"
    garbage_fighter.hp = stats_module.max_hp(garbage_fighter)
    garbage_victim = placed[2]
    garbage_victim.hp = 100.0
    garbage_fighter.target = garbage_victim
    garbage_victim.target = None
    result = combat_mod.resolve_swing(
        garbage_fighter, garbage_victim, rng=lambda: 0.0,
    )
    assert result["outcome"] == "critical", result
    assert garbage_victim.hp == (
        100.0 - combat_mod.DAMAGE_PER_CRITICAL
    ), garbage_victim.hp

    mundane_fighter.target = None
    martial_fighter.target = None
    garbage_fighter.target = None

    from engine.systems import clinic as clinic_mod

    brawler = placed[2]
    medic = placed[1]
    assert medic.bg_path == "medic"
    brawler.hp = 1.0
    brawler.hp_cap = stats_module.max_hp(brawler)
    walker.target = brawler
    brawler.target = walker
    combat_mod.resolve_round(game, rng=lambda: 0.0)
    assert clinic_mod.is_ko(brawler), getattr(brawler, "downed", None)
    for _ in range(15):
        if getattr(brawler, "hospitalized", False):
            break
        game.on_tick()
    ward = game.rooms.get("NB00005")
    assert ward is not None
    assert getattr(brawler, "hospitalized", False), (
        brawler.location.key if brawler.location else None
    )
    medic.move_to(ward)
    medic.session = _FakeSession([])
    dispatch(medic, f"treat {brawler.key}", game)
    assert not getattr(brawler, "hospitalized", False), medic.session.lines

    from engine.systems import justice as justice_mod
    from engine import hooks

    crook = placed[2]
    ranger = placed[3]
    assert ranger.bg_path == "ranger"
    crook.move_to(walker.location)
    crook.session = _FakeSession([])
    walker_cash_before = economy_mod.wallet_total_cents(walker)
    dispatch(crook, f"steal {walker.key}", game)
    assert justice_mod.is_wanted(crook), crook.session.lines
    assert economy_mod.wallet_total_cents(walker) < walker_cash_before
    cell = game.rooms.get("NB00010")
    assert cell is not None and getattr(cell, "is_cell", False)
    ranger.move_to(cell)
    crook.move_to(cell)
    ranger.session = _FakeSession([])
    dispatch(ranger, f"arrest {crook.key}", game)
    assert justice_mod.is_jailed(crook, game)
    dest = cell.exits.get("north")
    assert hooks.move_gate_block(crook, cell, dest, game)
    jail_until = int(getattr(crook, "jail_until_tick", 0) or 0)
    assert jail_until > int(game.game_time_ticks)
    game.game_time_ticks = jail_until + 1
    justice_mod.tick(game)
    assert not justice_mod.is_jailed(crook, game), (
        f"jail_until_tick={getattr(crook, 'jail_until_tick', None)!r} "
        f"game_time_ticks={game.game_time_ticks}"
    )
    economy_mod.credit_wallet(crook, dollars=20)
    crook.session = _FakeSession([])
    dispatch(crook, "payfine", game)
    assert not justice_mod.is_wanted(crook)

    saloon = game.rooms.get("NB00011")
    alley = game.rooms.get("NB00012")
    assert saloon is not None and alley is not None
    walker.move_to(saloon)
    walker.session = _FakeSession([])
    for _ in range(2):
        dispatch(walker, "slam wall", game)
    assert walker.location.key == "NB00012", walker.location.key

    # ---- Phase 5: origin registry + Alien Stellar / Umbral ----
    from engine.systems import aerial as aerial_mod
    from engine.systems import origin_registry
    from engine.systems import umbral as umbral_mod
    from engine import hooks as hooks_mod

    assert "alien" in origin_registry.known_origins()

    # Shortcut convention (same as clinic/justice smokes): stamp fields
    # directly rather than re-running full interactive chargen for every
    # Bloodline. Stellar flight gate must still honor bg_stellar.
    stellar = placed[0]
    stellar.origin = "alien"
    stellar.alien_path = "stellar"
    aerial_mod.ensure_stellar_defaults(stellar)
    stellar.bg_stellar = True
    assert aerial_mod.is_stellar(stellar)
    stellar.session = _FakeSession([])
    # Indoor refusal still works (Main Street is indoor-ish -- Observatory
    # is outdoor; use an indoor room to prove the gate, not the climb).
    indoor = game.rooms.get("NB00001")
    stellar.move_to(indoor)
    dispatch(stellar, "fly", game)
    # Either outdoor takeoff or an open-sky refusal -- never a crash, and
    # non-Stellar still refused. Prove the non-Stellar gate separately:
    mundane = placed[1]
    mundane.bg_stellar = False
    mundane.session = _FakeSession([])
    mundane.move_to(indoor)
    dispatch(mundane, "fly", game)
    assert any(
        "Only Stellar" in line for line in mundane.session.lines
    ), mundane.session.lines

    # Room hover: anyone can lift off in place; fly still Stellar-only.
    mundane.move_to(indoor)
    mundane.session = _FakeSession([])
    dispatch(mundane, "hover", game)
    assert aerial_mod.flight_tier(mundane) == aerial_mod.TIER_HOVER
    assert mundane.is_flying is True
    assert mundane.location is indoor
    dispatch(mundane, "descend", game)
    assert aerial_mod.flight_tier(mundane) == "ground"
    assert mundane.is_flying is False

    umbral = placed[2]
    umbral.origin = "alien"
    umbral.alien_path = "umbral"
    umbral_mod.ensure_umbral_defaults(umbral)
    umbral.bg_umbral = True
    umbral.umbral_charge = 1.0
    umbral.session = _FakeSession([])
    watcher = placed[3]
    watcher.session = _FakeSession([])
    shared = game.rooms.get("NB00001")
    umbral.move_to(shared)
    watcher.move_to(shared)

    # Daylight blocks shroud (noon).
    from engine.game_calendar import TICKS_PER_HOUR
    game.game_time_ticks = 12 * TICKS_PER_HOUR
    dispatch(umbral, "shroud", game)
    assert not umbral.umbral_shrouded, umbral.session.lines

    # Night allows shroud; presence hook hides the actor.
    game.game_time_ticks = 0  # midnight -> night
    umbral.session = _FakeSession([])
    dispatch(umbral, "shroud", game)
    assert umbral.umbral_shrouded is True, umbral.session.lines
    assert umbral.stealth_active is True
    assert hooks_mod.can_notice_stealth(watcher, umbral, game) is False

    umbral.session = _FakeSession([])
    dispatch(umbral, "unshroud", game)
    assert umbral.umbral_shrouded is False
    assert umbral.stealth_active is False
    assert hooks_mod.can_notice_stealth(watcher, umbral, game) is True

    # Charge drain auto-clears shroud.
    umbral.session = _FakeSession([])
    dispatch(umbral, "shroud", game)
    assert umbral.umbral_shrouded is True
    umbral.umbral_charge = umbral_mod.UMBRAL_CHARGE_STEP
    game.on_tick()
    assert umbral.umbral_shrouded is False
    assert umbral.stealth_active is False

    # ---- H5: fetch_pebble authored quest demo ----
    from engine.systems import quests as quests_mod
    from engine.systems import economy as economy_mod

    quester = placed[0]
    quester.move_to(game.rooms["NB00001"])
    quester.session = _FakeSession([])
    dispatch(quester, "questaccept fetch_pebble", game)
    assert quests_mod.has_active_quest(quester, "fetch_pebble")
    dispatch(quester, "south", game)
    assert quester.location.key == "NB00007"
    quests_mod.notify(quester, "has_item", game=game)
    assert not quests_mod.has_active_quest(quester, "fetch_pebble"), (
        quester.quest_progress
    )
    assert economy_mod.wallet_total_cents(quester) >= 500, (
        economy_mod.wallet_total_cents(quester)
    )

    # ---- H2: generic boarded cart (engine/systems/vehicles.py) ----
    from engine.systems import vehicles as vehicles_mod

    rider = placed[0]
    plaza = game.rooms["NB00001"]
    rider.session = _FakeSession([])
    vehicles_mod.ensure_game_vehicles(game)
    cart = vehicles_mod.vehicle_by_id(game, "cart")
    assert cart is not None, game.vehicles
    # Ignore stale parking_state.json from prior local smokes.
    cart["parked_room"] = plaza.key
    park_room = plaza
    rider.move_to(park_room)
    park_before = cart["parked_room"]
    dispatch(rider, "board cart", game)
    assert rider.in_vehicle == "cart", rider.session.lines
    assert rider.location.key == cart["interior_key"]
    rider.session = _FakeSession([])
    drive_dir = None
    for direction, dest in park_room.exits.items():
        dest_room = dest if not isinstance(dest, str) else game.rooms.get(dest)
        if dest_room and vehicles_mod.room_is_valid_park_spot(
            dest_room, game, character=rider,
        ):
            drive_dir = direction
            break
    assert drive_dir, (
        f"cart park room {park_room.key!r} needs a driveable neighbor exit"
    )
    dispatch(rider, f"drive {drive_dir}", game)
    assert cart["parked_room"] != park_before, (
        cart["parked_room"], park_before, rider.session.lines
    )
    rider.session = _FakeSession([])
    dispatch(rider, "unboard", game)
    assert rider.in_vehicle is None, rider.in_vehicle
    assert rider.location.key == cart["parked_room"]

    game.on_tick()

    # ---- H3: lodging rent + paced walk ----
    from engine.systems import lodging as lodging_mod
    from engine.systems import paced_travel as paced_mod

    inn = game.rooms.get("NB00014")
    assert inn is not None and lodging_mod.is_lodging_unit(inn)
    assert lodging_mod.beds_in_room(inn), "inn should have a seeded bunk bed"
    guest = placed[0]
    guest.move_to(inn)
    guest.session = _FakeSession([])
    economy_mod.credit_wallet(guest, dollars=10)
    dispatch(guest, "rent bed", game)
    assert guest.home_room_key == "NB00014", guest.home_room_key
    assert lodging_mod.claimants_of(game, "NB00014"), "rent should claim the room"
    bed, err = lodging_mod.pick_bed(inn, guest)
    assert bed is not None and err is None, (bed, err)
    assert lodging_mod.bed_available_to(guest, bed, inn)

    walker = placed[1]
    walker.move_to(game.rooms["NB00001"])
    walker.session = _FakeSession([])
    post = game.rooms.get("NB00006")
    dispatch(walker, "walk post", game)
    if walker.location is not post:
        assert paced_mod.has_walk_focus(walker), walker.session.lines
        hops = 0
        while paced_mod.has_walk_focus(walker) and hops < 30:
            game.on_tick()
            hops += 1
    assert walker.location is post, (
        f"walk post should arrive at post office, got {walker.location.key!r}"
    )

    # ---- H6: engine.map_store dig demo ----
    from engine import map_store as map_store_mod

    saloon = game.rooms.get("NB00011")
    assert saloon is not None
    zone_path, _kind, _filename, _map_id = map_store_mod.resolve_map_path(
        game, saloon,
    )
    with open(zone_path, "rb") as zone_handle:
        zone_backup = zone_handle.read()
    dig_name = "H9 Demo Alcove"
    walker.move_to(saloon)
    walker.session = _FakeSession([])
    before_keys = set(game.rooms.keys())
    try:
        dispatch(walker, f"dig up {dig_name}", game)
        assert any(
            "dug" in line.lower() or "room" in line.lower()
            for line in walker.session.lines
        ), walker.session.lines
        new_keys = set(game.rooms.keys()) - before_keys
        assert new_keys, "dig should add a live room to game.rooms"
        new_key = next(iter(new_keys))
        dispatch(walker, "up", game)
        assert walker.location.key == new_key, (
            walker.location.key, new_key, walker.session.lines
        )
    finally:
        with open(zone_path, "wb") as zone_handle:
            zone_handle.write(zone_backup)

    # ---- H7a: phone payphone + dial handset ----
    from engine.systems import phone as phone_mod
    from basegame import personas as personas_mod
    from world import Item

    operator = personas_mod.ensure_demo_npc(game)
    assert operator is not None
    assert personas_mod.operator_phone_number(game), "Operator handset number"
    assert phone_mod.room_has_payphone(post), "Post Office should have payphone"
    caller = placed[0]
    callee = placed[1]
    caller.move_to(post)
    callee.move_to(post)
    handset = Item("a flip phone", "A scratched demo handset.")
    handset.is_phone = True
    phone_mod.stamp_phone_on_spawn(handset, game)
    callee.inventory.append(handset)
    callee_number = handset.phone_number
    economy_mod.credit_wallet(caller, dollars=5)
    caller.session = _FakeSession([])
    dispatch(caller, f"dial {callee_number}", game)
    assert any("ring" in line.lower() for line in caller.session.lines), (
        caller.session.lines
    )
    callee.session = _FakeSession([])
    dispatch(callee, "answer", game)
    assert phone_mod.active_call(caller) is not None
    caller.session = _FakeSession([])
    dispatch(caller, "hangup", game)
    assert phone_mod.active_call(caller) is None

    # ---- H7b: appearance slots ----
    from engine.systems import appearance as appearance_mod

    model = placed[2]
    model.session = _FakeSession([])
    for slot, option in (
        ("hair_style", "short"),
        ("hair_color", "brown"),
        ("eye_color", "blue"),
        ("height", "average"),
        ("physique", "lean"),
        ("skin_tone", "fair"),
    ):
        dispatch(model, f"appearance {slot} {option}", game)
    assert appearance_mod.is_complete(model.appearance)
    assert "blue" in (model.description or "").lower(), model.description

    # ---- H7c: persona trait flavor ----
    greeter = placed[3]
    greeter.move_to(post)
    greeter.session = _FakeSession([])
    dispatch(greeter, "greet Operator", game)
    assert any(
        "stranger" in line.lower() or "welcome" in line.lower()
        for line in greeter.session.lines
    ), greeter.session.lines

    # ---- H7d: relationships ----
    from engine.systems import relationships as relationships_mod

    tagger = placed[0]
    buddy = placed[1]
    tagger.session = _FakeSession([])
    dispatch(tagger, f"friend {buddy.key}", game)
    assert relationships_mod.get_kind(tagger, buddy) == "friend"
    tagger.session = _FakeSession([])
    dispatch(tagger, "relate", game)
    assert any(buddy.key in line for line in tagger.session.lines), (
        tagger.session.lines
    )

    # ---- H10: procedural street-home shell (engine/systems/procedural_build) ----
    from engine.systems import procedural_build as proc_mod

    class _StreetHub:
        key = "notbigville:Demo Street"
        area_type = "city"
        zone = "notbigville"
        map_id = "notbigville"
        city_name = "Notbigville"
        title = "Notbigville - Demo Street"
        wilderness = False
        outdoor = True
        exits = {}
        layout = {"x": 0, "y": 0, "z": 0}

    hub = _StreetHub()
    rooms, patch = proc_mod._build_generic_home(
        hub, "Demo Street", 12501, rng=__import__("random").Random(7),
    )
    proc_mod.validate_home_shell(rooms, patch, street_key=hub.key)
    porch = next(r for r in rooms if r["key"].endswith(" Porch"))
    living = next(r for r in rooms if r["key"].endswith(" Living"))
    assert porch["outdoor"] and porch["private_home"]
    assert living["is_house"] and not living.get("outdoor")
    assert porch["title"] == "Notbigville - 12501 Demo Street - Porch"

    game.on_tick()
    game.db.close()

    print("basegame_smoke_ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
