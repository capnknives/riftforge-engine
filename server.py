"""
server.py — the entry point. Run this file:  python server.py
Then connect from another terminal:          telnet localhost 4000

This owns two things running side by side on one asyncio event loop:
  1. the network server (accepting player connections), OR an IPC client
     to engine.gateway when RIFTFORGE_GATEWAY=1
  2. the tick loop (the game's heartbeat)

SUPERS is soft-optional: with the supers package absent, lean maps + engine
verbs still boot (two-repo Phase 4b). Full Cadence / Origin content needs
supers installed.
"""

import asyncio                        # Python's built-in async networking library
import os
import time
import traceback
from collections import deque

import persistence
from engine import bug_webhook  # noqa: F401 -- loads webhook helpers (GM squashbugs)
from engine import copyover
from world import build_world, Character
from engine.connection import Session

# Soft-optional SUPERS: register hooks / ticks only when the game package
# is installed. Lean engine smoke renames supers/ aside and still imports us.
_HAS_SUPERS = False
register_all_hooks = None
register_default_ticks = None
run_ticks = None
make_training_dummy = None

try:
    from supers.bootstrap import register_all_hooks as _register_all_hooks
    from supers.tick_bootstrap import register_default_ticks as _register_default_ticks
    from supers.tick_registry import run_ticks as _run_ticks
    from world import make_training_dummy as _make_training_dummy

    register_all_hooks = _register_all_hooks
    register_default_ticks = _register_default_ticks
    run_ticks = _run_ticks
    make_training_dummy = _make_training_dummy
    _HAS_SUPERS = True
except ImportError:
    pass

# Game registers Character attach, persist blob, chargen, and help before any
# Character is constructed (docs/ENGINE_CONSUMER.md).
if _HAS_SUPERS and register_all_hooks is not None:
    register_all_hooks()

# Milestone E (a live player suggestion, section 4-E's pacing follow-up):
# a compressed in-game clock so the world has a sense of elapsed time
# distinct from real time. 28,800 ticks/real-day (3s/tick) / 3 game-days
# per real day = 9,600 ticks per game-day -- an 8-real-hour game-day.
# Purely additive: training's actual stamina/fatigue/gain math stays
# tick-based and untouched (already validated by balance_sim.py in
# real-world terms); this clock is new state + display/flavor only.
TICKS_PER_GAME_DAY = 9600
# Full SQLite snapshot cadence: every N heartbeats (3s each). 20 → ~60s.
# Immediate saves still run on connect/disconnect/shutdown.
AUTOSAVE_EVERY_TICKS = 20


class Game:
    """Holds all shared game state: the world, the database, and live sessions."""

    def __init__(self, db_path="riftforge.db"):
        # db_path is a parameter so the smoke test can point it at a throwaway
        # file (or ":memory:") instead of the real save file.
        # Live Character roster (engine/char_index.py). RoomMap stamps
        # room.game on every insert so Room.add/remove keep the set
        # truthful (including procedural dungeons and smoke ad-hoc rooms).
        # Must land BEFORE load_world / seeding so move_to registers Echoes.
        from engine.char_index import RoomMap
        raw_rooms, self.start_room, seed_items = build_world()
        self.characters = set()
        self.rooms = RoomMap(self)
        self.rooms.update(raw_rooms)
        # Map catalog metadata from maps.LAST_MAP_REGISTRY (realm/plane/
        # pocket hubs per map id) -- copied after load so tooling/GM verbs
        # can inspect without re-reading JSON.
        import maps as maps_module
        self.map_registry = dict(maps_module.LAST_MAP_REGISTRY)
        self.sessions = []                # every connected Session (starts empty)
        # Wall-clock unix time of this process boot -- MSSP UPTIME (engine/mssp.py).
        # Not persisted; resets on every restart / copyover process spawn.
        self.started_at = time.time()
        # Global OOC ring buffer: bare `ooc` shows the last 20 channel lines.
        # In-memory only -- clears on restart / copyover (not a persistent log).
        self.ooc_history = deque(maxlen=20)
        # Where bug_reports.log / suggestions.log / help_misses.log live.
        # Same directory as the save file so Docker's host volume keeps
        # reports across rebuilds (reports.py / commands.py).
        # dirname("riftforge.db") is "" -- use ".".
        self.report_dir = os.path.dirname(db_path) or "."

        self.db = persistence.connect(db_path)
        # Milestone E: the compressed clock -- 0 for a fresh world, or
        # wherever a returning world left off (reused for both branches
        # below, so it's loaded once here rather than duplicated in each).
        self.game_time_ticks = persistence.load_game_time(self.db)
        # Gregorian display epoch (2015-10-15): absolute game-day that
        # maps to that date. Fresh worlds use 0. Upgraded worlds missing
        # the key rebase so "today" becomes 2015-10-15 without resetting
        # game_time_ticks (cooldowns stay valid). Then the clock keeps
        # advancing at 3x forever.
        from engine import game_calendar
        stored_epoch = persistence.load_calendar_epoch_day(self.db)
        if stored_epoch is None:
            self.calendar_epoch_day = (
                self.game_time_ticks // TICKS_PER_GAME_DAY
            )
            persistence.save_calendar_epoch_day(
                self.db, self.calendar_epoch_day
            )
        else:
            self.calendar_epoch_day = max(0, int(stored_epoch))
        game_calendar.set_active_epoch_day_offset(self.calendar_epoch_day)
        # Lean / pre-seed defaults so the first save() (fresh world) never
        # trips AttributeError in persistence.save_moral_state. SUPERS boot
        # overwrites these from DB / balance.ensure_game_defaults below.
        self.vampire_townsfolk_kills = 0
        self.moral_balance = 0
        self.eclipse_until_tick = 0
        self.moral_event_cooldown_until = 0
        self.moral_maxed_side = None
        self.moral_maxed_since_tick = 0
        self.moral_last_casualty_tick = 0
        self.moral_scout_cooldown_until = 0
        self.rumor_boards = {}

        if persistence.is_seeded(self.db):
            # A returning world: restore every character (as an Echo) and every
            # item to wherever they were when the server last saved.
            persistence.load_world(self.db, self)
        else:
            # Brand-new world: place the starter items, then record that we did
            # so they're never placed again (see build_world's docstring).
            for item, room_key in seed_items:
                self.rooms[room_key].add(item)
            persistence.mark_seeded(self.db)
            self.save()

        if _HAS_SUPERS:
            self._seed_supers_content()
        else:
            # Lean engine: rumor boards from meta only (no Cadence seed).
            self.rumor_boards = persistence.load_rumor_boards(self.db)

    def _seed_supers_content(self):
        """Idempotent SUPERS backfills (dummy, Cadence, immersion, ticks).

        Only called when the supers package imported successfully at boot.
        """
        # Milestone 5b: backfill the training dummy idempotently, NOT as
        # first-boot seed content -- a database seeded before this feature
        # existed (e.g. the real riftforge.db) already took the "if not
        # seeded" branch above and would otherwise never get one. Checking
        # "does it already exist" instead of "is this a fresh database"
        # handles both a brand-new world AND an old save the same way.
        dummy = self.find_character("a training dummy")
        if not dummy:
            dummy = make_training_dummy()
            dummy.move_to(self.start_room)
            self.save()
        elif not dummy.spar_only:
            # Self-healing (same spirit as persistence.py's float-drift
            # round() fix): a dummy saved before Milestone F added
            # spar_only loads with the field defaulted to False, which
            # would silently let a real lethal 'attack' through against
            # it instead of redirecting to sparring. Patch it back once.
            dummy.spar_only = True
            self.save()

        # GM/admin tooling: the same idempotent-backfill shape as the training
        # dummy above, for the same reason -- a database seeded (or even just
        # played on) before this feature existed will never take a "first
        # Head-GM bootstrap: if nobody holds head_gm yet and a character
        # named "Wits" exists without a rank, grant her head_gm. Immersion
        # cast members may already hold ordinary "gm" -- that must not block
        # the head-GM backfill (they are catalog staff, not the live head).
        all_characters = list(self.characters)
        has_head = any(
            getattr(c, "gm_rank", None) == "head_gm" for c in all_characters
        )
        if not has_head:
            wits = self.find_character("Wits")
            if wits is not None and not wits.gm_rank:
                wits.gm_rank = "head_gm"
                self.save()

        # Cadence town simulation (docs/SYSTEMS_DESIGN.md D33 slice): place
        # every rostered town NPC (content/npcs/*.json) not already in the
        # world. Same idempotent-backfill shape as the training dummy above,
        # for the same reason -- this must also backfill an existing save
        # that predates (or just hasn't yet authored) a given townsperson.
        from supers import cadence
        from supers import lodging
        cadence.ensure_town_npcs(self)
        # Immersion GM cast (Buffy / Constantine / …): git-tracked JSON
        # under content/immersion/ -- create-or-update on every boot so
        # live/local DBs no longer need per-character seed scripts.
        from supers import immersion as immersion_mod
        immersion_mod.ensure_all(self)
        # Awakened Nature homezone tutorial mentors (Marrow/Seraphiel/
        # Ashmouth/Gorge/Emberwake): same idempotent-backfill shape as
        # immersion cast -- seed once, skip if already present.
        from supers import tutorial as tutorial_mod
        tutorial_mod.ensure_mentors(self)
        # Mission givers (Bobby, …): validate portal rooms + stamp
        # mission_giver onto rostered NPCs after Cadence seeds them.
        from supers import missions as missions_mod
        missions_mod.ensure_givers(self)
        # Singer House: vampire_safe + salt/iron/devil's-trap wards.
        from supers import singer_house as singer_house_mod
        singer_house_mod.ensure_singer_house_wards(self)
        # Lodging: backfill bed furniture on long-lived DBs, then stamp
        # roster home beds so Cadence prefers owned beds.
        lodging.ensure_beds(self)
        lodging.assign_roster_bed_owners(self)
        # Retroactive home_zone / home_room_key heal for players + Echoes
        # created before path stamps (Vagan/Velan wilderness stuck case).
        cadence.heal_all_home_anchors(self)
        # Home grocery stock: backfill refrigerator furniture on is_house
        # rooms (long-lived DBs / maps that predate fridge seed_items).
        from supers import grocery as grocery_mod
        grocery_mod.ensure_fridges(self)
        # Buffy/Angel easter egg: mutual lovers + Angel's mausoleum home
        # when both characters exist (idempotent; no-op otherwise).
        # Faith: mutual friends with Buffy and Angel when she exists.
        # (ensure_all already runs bond helpers; keep these for older
        # call-order safety if ensure_all is ever skipped.)
        from supers import relationships as relationships_mod
        relationships_mod.ensure_buffy_angel_bond(self)
        relationships_mod.ensure_faith_bonds(self)
        relationships_mod.ensure_illyria_bonds(self)
        relationships_mod.ensure_winchester_bonds(self)
        relationships_mod.ensure_constantine_bonds(self)
        # Cadence #50: Vampire townsfolk-kill counter for hunter escalation.
        self.vampire_townsfolk_kills = 0
        # Evil Strikes Back: world Good/Evil meter + eclipse (meta table).
        from supers import balance as balance_module
        balance_module.ensure_game_defaults(self)
        moral = persistence.load_moral_state(self.db)
        self.moral_balance = moral["moral_balance"]
        self.eclipse_until_tick = moral["eclipse_until_tick"]
        self.moral_event_cooldown_until = moral[
            "moral_event_cooldown_until"
        ]
        self.moral_maxed_side = moral.get("moral_maxed_side")
        self.moral_maxed_since_tick = moral.get(
            "moral_maxed_since_tick", 0
        )
        self.moral_last_casualty_tick = moral.get(
            "moral_last_casualty_tick", 0
        )
        self.moral_scout_cooldown_until = moral.get(
            "moral_scout_cooldown_until", 0
        )
        # D63 player rumor boards (meta JSON; separate from Cadence gossip).
        self.rumor_boards = persistence.load_rumor_boards(self.db)
        self.save()
        register_default_ticks(self)

    def find_character(self, name):
        """Find a character anywhere in the world by name (case-insensitive).

        Uses the live ``game.characters`` roster (players, Echoes, NPCs)
        instead of walking every room -- the map is ~12k cells.
        """
        needle = name.lower()
        for obj in self.characters:
            if obj.key.lower() == needle:
                return obj
        return None

    def broadcast_all(self, message):
        """Send a line to every connected session (world-wide announcement)."""
        for session in list(self.sessions):
            session.send(message)

    def game_day(self):
        """Milestone E: which compressed in-game day it currently is
        (TICKS_PER_GAME_DAY ticks = 1 game-day). Day 0 is the world's
        very first tick."""
        return self.game_time_ticks // TICKS_PER_GAME_DAY

    def calendar(self):
        """Gregorian calendar stack from game_time_ticks (display/flavor).

        Training math does NOT read this -- tick deadlines stay absolute.
        calendar_epoch_day shifts labels so new/rebased worlds start on
        2015-10-15; see engine.game_calendar.
        """
        from engine import game_calendar
        return game_calendar.breakdown(
            self.game_time_ticks,
            ticks_per_day=TICKS_PER_GAME_DAY,
            epoch_day_offset=getattr(self, "calendar_epoch_day", 0),
        )

    def save(self):
        """Snapshot the whole world to the database (see persistence.py)."""
        persistence.save_world(self.db, self)
        persistence.save_game_time(self.db, self.game_time_ticks)
        persistence.save_calendar_epoch_day(
            self.db, getattr(self, "calendar_epoch_day", 0)
        )
        persistence.save_moral_state(self.db, self)
        persistence.save_rumor_boards(self.db, self)

    async def tick_loop(self):
        """The heartbeat. Fires every 3 seconds, forever."""
        while True:
            await asyncio.sleep(3)        # pause 3s WITHOUT freezing the server
            try:
                self.on_tick()
            except Exception:
                print("[tick_loop] a tick raised an exception -- skipping it, "
                      "heartbeat continues:")
                traceback.print_exc()

    def on_tick(self):
        # Advance the compressed clock, then run the ordered handler pipeline
        # registered in supers.tick_bootstrap.register_default_ticks (when
        # SUPERS is installed). Lean engine: clock + autosave only.
        self.game_time_ticks += 1
        if _HAS_SUPERS and run_ticks is not None:
            run_ticks(self)
        # Autosave every AUTOSAVE_EVERY_TICKS heartbeats (~60s at 3s/tick),
        # not every tick: wipe+rewrite SQLite across ~12k rooms blocked the
        # single asyncio thread and felt like command lag. Connect /
        # disconnect / shutdown still call save() immediately.
        if self.game_time_ticks % AUTOSAVE_EVERY_TICKS == 0:
            self.save()


async def handle_client(reader, writer, game):
    """Called once per new connection. Each client runs its own Session coroutine
    concurrently — that's how many players share one single-threaded loop."""
    session = Session(reader, writer, game)
    try:
        await session.run()               # run this player's whole session
    except (ConnectionResetError, BrokenPipeError):
        session.disconnect()
    except Exception:
        traceback.print_exc()
        try:
            session.disconnect()
        except Exception:
            traceback.print_exc()
            session.alive = False
            if session.character is not None:
                session.character.session = None
            if session in game.sessions:
                game.sessions.remove(session)
            try:
                session.writer.close()
            except Exception:
                pass


async def main():
    game = Game()
    from engine.gateway_client import GatewayBridge, gateway_enabled

    # Start the heartbeat as its own background task so it runs ALONGSIDE the
    # server (create_task schedules it without waiting for it to finish).
    asyncio.create_task(game.tick_loop())

    if gateway_enabled():
        # Level 3 gateway: do not bind :4000; speak IPC and reattach held clients.
        # In-process copyover is unused here — watch_and_run restarts the game.
        print(
            "SUPERS engine behind gateway "
            f"(IPC {os.environ.get('RIFTFORGE_GATEWAY_IPC', '127.0.0.1:4001')})",
            flush=True,
        )

        def _session_factory(reader, writer, g, gateway_session_id=None):
            return Session(
                reader, writer, g, gateway_session_id=gateway_session_id
            )

        bridge = GatewayBridge(game, _session_factory)
        try:
            await bridge.connect_and_run()
        finally:
            game.save()
            game.db.close()
        return

    # Direct telnet (RIFTFORGE_GATEWAY=0): bind :4000 + optional copyover.
    # start_server listens for connections. The lambda is a tiny inline function:
    # asyncio hands it (reader, writer) for each new client, and we add `game`.
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, game),
        host="0.0.0.0",                   # accept connections on any network interface
        port=int(os.environ.get("RIFTFORGE_PORT", "4000")),
    )
    print("SUPERS engine listening on port 4000  (telnet localhost 4000)")

    # Copyover (see copyover.py): SIGUSR1 triggers a hot in-place reload that
    # keeps every connected client's socket open across it -- distinct from
    # SIGINT/Ctrl-C below, which is a real shutdown. install_signal_handler
    # is a no-op on Windows (no POSIX signals there). resume() is also a
    # no-op UNLESS this process was just exec'd BY a copyover -- in which
    # case it reattaches every preserved connection to its character here,
    # now that game.sessions/find_character are ready to be used.
    copyover.install_signal_handler(game)
    await copyover.resume(game)

    try:
        async with server:                # keep the server open...
            await server.serve_forever()  # ...and run until the program is stopped
    finally:
        # Runs even when Ctrl-C cancels us: one last save so nothing typed in
        # the final seconds (since the last tick's autosave) is lost.
        game.save()
        game.db.close()


if __name__ == "__main__":
    # This block runs only when you execute `python server.py` directly.
    # asyncio.run() starts the event loop and runs main() until it finishes.
    try:
        asyncio.run(main())
    except KeyboardInterrupt:              # Ctrl-C
        print("\nShutting down.")
