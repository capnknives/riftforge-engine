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
from engine import discord_bridge  # noqa: F401 -- tagged Discord radio bridge
from engine import copyover
from world import build_world, Character
from engine.connection import Session
# Tick pipeline is generic engine infrastructure (two-repo purity Stage 1):
# a lean engine boot gets a working (empty) tick loop even with SUPERS absent.
from engine.tick_registry import run_ticks, run_ticks_async
# game_select is the single choke point that picks SUPERS, basegame, or a
# lean engine (RIFTFORGE_GAME) -- see game_select.py's docstring. Nothing
# else in server.py imports `supers` or `basegame` directly.
import game_select

# _HAS_SUPERS specifically means "SUPERS is the active game" -- it still
# gates every SUPERS-only meta load below (plane souls, hue courts, …)
# exactly as before, since none of those have a basegame equivalent yet.
# Boot seed (Cadence / immersion / heals) routes through
# game_select.seed_content → supers.boot_seed (Phase 7 Stage G).
_HAS_SUPERS = game_select.game_name() == "supers"

register_all_hooks = game_select.register_all_hooks
register_default_ticks = game_select.register_default_ticks

# Game registers Character attach, persist blob, chargen, and help before any
# Character is constructed (docs/ENGINE_CONSUMER.md).
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
        # Prove life before the potentially long build_world / load_world
        # path so hang recovery does not treat a slow boot as a freeze.
        from engine import game_heartbeat
        game_heartbeat.touch_heartbeat("game_init")
        # Live Character roster (engine/char_index.py). RoomMap stamps
        # room.game on every insert so Room.add/remove keep the set
        # truthful (including procedural dungeons and smoke ad-hoc rooms).
        # Must land BEFORE load_world / seeding so move_to registers Echoes.
        from engine.char_index import RoomMap
        raw_rooms, self.start_room, seed_items = build_world()
        self.characters = set()
        self.rooms = RoomMap(self)
        self.rooms.update(raw_rooms)
        # Phase 3 dual-read: legacy dig / JSON keys → VNUM identity.
        import maps as maps_module
        self.room_aliases = dict(
            getattr(maps_module, "LAST_ROOM_ALIASES", None) or {}
        )
        # Map catalog metadata from maps.LAST_MAP_REGISTRY (realm/plane/
        # pocket hubs per map id) -- copied after load so tooling/GM verbs
        # can inspect without re-reading JSON.
        self.map_registry = dict(maps_module.LAST_MAP_REGISTRY)
        self.sessions = []                # every connected Session (starts empty)
        # Mid-login / mid-chargen sockets that are NOT on `sessions` yet.
        # `gm users` lists these with flags=login|creating; public `who`
        # and room broadcasts still use `sessions` only (no half-made Echo).
        self.connecting_sessions = []
        # Wall-clock unix time of this process boot -- MSSP UPTIME (engine/mssp.py).
        # Not persisted; resets on every restart / copyover process spawn.
        self.started_at = time.time()
        # Global OOC ring buffer: bare `ooc` shows the last 20 channel lines.
        # Kept in meta via save_ooc_history so copyover / restart keep recent
        # chat (still a short ring — not a forever log). Refilled after db
        # connect below.
        self.ooc_history = deque(maxlen=20)
        # Engine-level Accounts (above Characters). Filled by
        # persistence.load_accounts after the world load; empty until then.
        self.accounts = {}
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
        # trips AttributeError in persistence.save_moral_state. Persisted
        # meta is loaded immediately below -- BEFORE any save() -- so boot
        # backfills (training dummy, head_gm, …) cannot wipe plane banks,
        # World Tide, gmworld lifetime tallies, or death beacons.
        self.vampire_townsfolk_kills = 0
        self.moral_balance = 0
        self.heaven_soul_count = 0
        self.hell_soul_count = 0
        # Physical phone number allocator (supers/phone.py); 555-XXXX.
        self.next_phone_seq = 1000
        self.hue_courts = {}
        self.death_beacons = {}
        # Chuck Author mantle-resume (meta JSON; idle until load/restore).
        self.author_mantle_event = {}
        self.eclipse_until_tick = 0
        self.moral_event_cooldown_until = 0
        self.moral_maxed_side = None
        self.moral_maxed_since_tick = 0
        self.moral_last_casualty_tick = 0
        self.moral_scout_cooldown_until = 0
        # Host/Infernal holy war (gm holywar) -- default OFF until staff
        # flips it; later becomes a scheduled world event outside Tide.
        self.holy_war_active = False
        self.rumor_boards = {}
        # GM gmworld lifetime counters (haunts / missions / tips / …).
        self.lifetime_stats = {}
        # Lebanon Adoption Agency weekly board (supers/pet.py).
        self.pet_adoption = {}
        # GM Cadence chance overrides (``gm chances``) -- empty = defaults.
        self.cadence_chances = {}
        # GM Cadence scale / LOD / priority (``gm cadence``) -- empty = defaults.
        self.cadence_scale = {}
        # GM immortal incap stun overrides (``gm incap``) -- empty = defaults.
        self.incap_tuning = {}
        # GM outgoing damage soft-cap (``gm outgoing``) -- empty = defaults.
        self.outgoing_damage_tuning = {}
        # GM nest/hub body decay TTLs (``gm bodydecay``) -- empty = defaults.
        self.corpse_decay_tuning = {}
        # GM Hell exile TTL (``gm hellexile``) -- empty = defaults.
        self.hell_exile_tuning = {}
        # GM Purgatory pit loot knobs (``gm pit drops``) -- empty = defaults.
        self.pit_drop_tuning = {}
        # GM runtime verb blocks (``gm disable <verb>``) -- in-memory toggle set.
        self.disabled_verbs = set()
        self.homestead_plots = {}
        self.gather_nodes = {}
        self.personal_realms = {}
        self._load_persisted_meta()

        # Hot-loaded deferred maps (gm maps load) — restore BEFORE
        # load_world so characters saved inside Lebanon / etc. still have
        # rooms. See supers/map_runtime.py + runtime_maps.json.
        # ensure_deferred_maps_for_saved_rooms covers the Area Studio case
        # where zone JSON exists but runtime_maps.json was never written.
        if _HAS_SUPERS:
            from supers import map_runtime as map_runtime_mod
            map_runtime_mod.restore_runtime_maps(self)
            map_runtime_mod.ensure_deferred_maps_for_saved_rooms(self)

        # D41: rebuild homestead pockets + gather nodes BEFORE load_world
        # so character room_key / floor items inside a shack resolve.
        if _HAS_SUPERS:
            from supers import homestead as homestead_mod
            from supers import gathering as gathering_mod
            homestead_mod.load_homesteads(self.db, self)
            gathering_mod.load_gather_nodes(self.db, self)
            from supers import personal_realm as personal_realm_mod
            personal_realm_mod.load_personal_realms(self.db, self)

        if persistence.is_seeded(self.db):
            # A returning world: restore every character (as an Echo) and every
            # item to wherever they were when the server last saved.
            persistence.load_world(self.db, self)
            # Drop any zombie gmspirit: rows that older saves still had
            # (load also skips them; this covers in-memory leftovers).
            if _HAS_SUPERS:
                try:
                    from supers import cadence as cadence_mod
                    cadence_mod.purge_orphan_gm_spirits(self)
                except Exception:
                    # Boot cleanup is best-effort, but a failure here means
                    # zombie gmspirit rows may linger -- log it instead of
                    # hiding it so staff can see the world came up dirty.
                    print(
                        "[server] purge_orphan_gm_spirits failed during load:",
                        flush=True,
                    )
                    traceback.print_exc()
        else:
            # Brand-new world: place the starter items, then record that we did
            # so they're never placed again (see build_world's docstring).
            for item, room_key in seed_items:
                self.rooms[room_key].add(item)
            persistence.mark_seeded(self.db)
            self.save()

        # Engine accounts: load after characters so back-pointers reconcile.
        persistence.load_accounts(self.db, self)
        try:
            from engine import accounts as accounts_mod
            accounts_mod.reconcile_accounts(self)
            accounts_mod.migrate_legacy_gm_ranks(self)
        except Exception:
            print("[server] account reconcile/migrate failed:", flush=True)
            traceback.print_exc()

        # Game-package boot seed (SUPERS Cadence/heals, or basegame stub).
        # Lean engine ("none"): no-op — meta already loaded above.
        game_select.seed_content(self)

    def _load_persisted_meta(self):
        """Load Game meta counters from SQLite before any boot save().

        Engine-generic clock fields are loaded in ``__init__`` above.
        SUPERS-shaped Tide / Cadence / tuning / rumor / OOC meta rides
        ``engine.hooks.load_game_meta`` (``supers.persist_meta``). Lean
        boots leave the hook unset and keep the ``__init__`` defaults.
        """
        from engine import hooks as hooks_mod
        hooks_mod.load_game_meta(self, self.db)

    def find_character(self, name):
        """Find a character anywhere in the world by name (case-insensitive).

        Uses the live ``game.characters`` roster (players, Echoes, NPCs)
        instead of walking every room -- the map is ~12k cells.

        Supports ordinals: ``2.carl``, ``other carl``, ``second carl``.

        Resolution order (first hit wins when unambiguous):
          1. Exact ``Character.key`` match (so bare ``Wits`` prefers the
             mortal body over ``gmspirit:Wits`` when both exist -- set
             iteration used to return the spirit first and break
             ``where`` / ``goto`` / ``who`` tooling)
          2. Exact match on ``husk:Name`` / ``gmspirit:Name`` when the
             query is bare ``Name`` and no exact bare key existed (so
             ``hakai Albert`` still finds ``husk:Albert`` when Albert is
             gone)
          3. Exact ``assumed_face`` / ``husk_display_name`` / given_name
             (and given+surname) match
          4. Substring / ordinal collect via identity needles
        """
        from engine.char_identity import (
            character_given_name,
            character_surname,
            parse_target_ordinal,
            pick_ordinal,
        )
        from engine.command_support import _collect_character_matches

        raw = (name or "").strip()
        if not raw:
            return None
        ordinal, rest = parse_target_ordinal(raw)
        needle = (rest or "").strip().lower()
        if not needle:
            return None

        def _parked_gm_spirit(obj):
            """True for an off-grid, sessionless staff GM spirit.

            A staffer who is not in GM form leaves their permanent spirit
            parked in the vault beneath Lucifer's Cage (see
            ``supers.verbs.gm.presence._park_gm_spirit``). Per design, a
            parked GM is *not in the game*: it must not surface from
            name / face / given-name lookups (``summon``, ``snoop``,
            ``where``, ``goto`` by name). Exact full-key lookups
            (``gmspirit:Zhayl``) still resolve below so ``gm on`` can
            reattach without minting a new Character.
            """
            return (
                getattr(obj, "gm_spirit", False)
                and getattr(obj, "session", None) is None
                and not getattr(obj, "gm_mode", False)
            )

        # With an ordinal, collect all substring matches and pick. Parked
        # staff spirits are filtered out (not part of the live world).
        if ordinal is not None:
            pool = [o for o in self.characters if not _parked_gm_spirit(o)]
            matches = _collect_character_matches(rest, pool)
            return pick_ordinal(matches, ordinal)

        # Two-pass exact: never let an ephemeral prefix steal a bare key.
        face_hit = None
        given_hits = []
        ephemeral_hit = None
        for obj in self.characters:
            key = (getattr(obj, "key", None) or "").lower()
            if key == needle:
                return obj
            # Skip parked staff spirits for every fuzzy (non exact-key)
            # match so a bare ``zhayl`` never grabs the off-grid ghost.
            if _parked_gm_spirit(obj):
                continue
            if ephemeral_hit is None and ":" in key:
                prefix, _, rest_key = key.partition(":")
                if prefix in ("husk", "gmspirit") and rest_key == needle:
                    ephemeral_hit = obj
            face = (
                getattr(obj, "assumed_face", None)
                or getattr(obj, "husk_display_name", None)
                or ""
            )
            if face_hit is None and face and face.lower() == needle:
                face_hit = obj
            given = character_given_name(obj).lower()
            sur = character_surname(obj).lower()
            if given == needle:
                given_hits.append(obj)
            elif sur and f"{given} {sur}" == needle:
                given_hits.append(obj)
            elif sur and f"{given}{sur}" == needle:
                given_hits.append(obj)
        if ephemeral_hit is not None:
            return ephemeral_hit
        if face_hit is not None:
            return face_hit
        if len(given_hits) == 1:
            return given_hits[0]
        if len(given_hits) > 1:
            # Ambiguous without ordinal -- first hit (legacy). Prefer
            # callers that peel ``2.name`` before reaching here.
            return given_hits[0]
        return None

    def find_login_character(self, name):
        """Resolve a password / gateway login to the corporeal body only.

        Never returns ``husk:Name`` or ``gmspirit:Name`` -- those are
        ephemeral / vessel storage keys. Login attach must hit the real
        player Character so we never dual-control a husk or zombie spirit.

        Matches exact storage key OR unique given_name (legacy single Carl).
        Duplicate given names must disambiguate via surname in connection.py.
        """
        from engine.char_identity import (
            character_given_name,
            is_login_player_body,
        )
        from engine.command_support import strip_ephemeral_storage_prefix

        needle = strip_ephemeral_storage_prefix(name or "").strip().lower()
        if not needle or needle == "?":
            return None
        key_hit = None
        given_hits = []
        for obj in self.characters:
            if not is_login_player_body(obj):
                continue
            key = (getattr(obj, "key", None) or "")
            key_low = key.lower()
            if key_low == needle:
                key_hit = obj
                break
            if character_given_name(obj).lower() == needle:
                given_hits.append(obj)
        if key_hit is not None:
            return key_hit
        if len(given_hits) == 1:
            return given_hits[0]
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
        from engine import hooks as hooks_mod

        persistence.save_world(self.db, self)
        persistence.save_game_time(self.db, self.game_time_ticks)
        persistence.save_calendar_epoch_day(
            self.db, getattr(self, "calendar_epoch_day", 0)
        )
        persistence.save_accounts(self.db, self)
        # SUPERS Tide / Cadence / tuning / rumor / OOC (no-op when lean).
        hooks_mod.save_game_meta(self, self.db)
        # D41 wilds homestead + gather nodes (same save pulse as the world).
        if _HAS_SUPERS:
            try:
                from supers import homestead as homestead_mod
                from supers import gathering as gathering_mod
                homestead_mod.save_homesteads(self.db, self)
                gathering_mod.save_gather_nodes(self.db, self)
                from supers import personal_realm as personal_realm_mod
                personal_realm_mod.save_personal_realms(self.db, self)
            except Exception:
                # Never let a homestead codec abort the rest of the snapshot.
                traceback.print_exc()

    async def tick_loop(self):
        """The heartbeat. Fires every 3 seconds, forever.

        Also stamps ``.game_heartbeat`` so ``watch_and_run`` can kill a
        hung process that never exits (asyncio thread stuck in on_tick /
        autosave / etc.). Touch *before* the heartbeat so a hang inside
        the tick still leaves a last-good stamp for the age check.

        Production uses ``run_heartbeat_async`` so Cadence can yield
        between actors; smoke/tools still call sync ``on_tick``.
        """
        from engine import game_heartbeat
        game_heartbeat.touch_heartbeat("tick_loop_start")
        while True:
            await asyncio.sleep(3)        # pause 3s WITHOUT freezing the server
            # Stamp before work: if the heartbeat blocks forever, the watcher
            # sees this mtime go stale and force-restarts.
            game_heartbeat.touch_heartbeat("pre_tick")
            try:
                await self.run_heartbeat_async()
            except Exception:
                print("[tick_loop] a tick raised an exception -- skipping it, "
                      "heartbeat continues:")
                traceback.print_exc()
            # Stamp after work so a completed heavy tick resets the clock.
            game_heartbeat.touch_heartbeat("post_tick")

    async def run_heartbeat_async(self):
        """Advance clock, run the async tick pipeline, autosave when due.

        Cadence ``tick_async`` yields between actors so player commands
        can run on the same asyncio loop during a long lifestyle pass.
        """
        self.game_time_ticks += 1
        await run_ticks_async(self)
        self._maybe_autosave()

    def on_tick(self):
        """Sync heartbeat for smoke/tools -- no Cadence yields.

        Production ``tick_loop`` uses ``run_heartbeat_async`` instead.
        """
        self.game_time_ticks += 1
        run_ticks(self)
        self._maybe_autosave()

    def _maybe_autosave(self):
        """Autosave every AUTOSAVE_EVERY_TICKS heartbeats (~60s at 3s/tick).

        Wipe+rewrite SQLite across ~12k rooms blocks the single asyncio
        thread and felt like command lag. Connect / disconnect / shutdown
        still call save() immediately.
        """
        if self.game_time_ticks % AUTOSAVE_EVERY_TICKS != 0:
            return
        from engine import diag_export
        import time as _save_time
        _diag = diag_export.diag_enabled()
        _t_save = _save_time.perf_counter() if _diag else None
        self.save()
        if _diag and _t_save is not None:
            diag_export.append_event(
                "D",
                "server.py:on_tick:autosave",
                "autosave_ms",
                {
                    "save_ms": round(
                        (_save_time.perf_counter() - _t_save) * 1000.0, 2
                    ),
                    "game_time_ticks": self.game_time_ticks,
                    "n_rooms": len(getattr(self, "rooms", {}) or {}),
                    "n_chars": len(
                        getattr(self, "characters", ()) or ()
                    ),
                },
            )

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
            connecting = getattr(game, "connecting_sessions", None)
            if connecting is not None and session in connecting:
                connecting.remove(session)
            try:
                session.writer.close()
            except Exception:
                pass


async def main():
    # Prove the child is alive during long Game() / world load so the
    # watcher boot-grace clock has a fresh stamp once import finishes.
    from engine import game_heartbeat
    game_heartbeat.touch_heartbeat("main_enter")
    game = Game()
    game_heartbeat.touch_heartbeat("game_ready")
    from engine.gateway_client import GatewayBridge, gateway_enabled

    if gateway_enabled():
        # Level 3 gateway: do not bind :4000; speak IPC and reattach held clients.
        # SIGUSR1 still runs copyover._perform: announce Veil line, save, exit
        # so watch_and_run can respawn (clients stay on the gateway).
        print(
            "SUPERS engine behind gateway "
            f"(IPC {os.environ.get('RIFTFORGE_GATEWAY_IPC', '127.0.0.1:4001')})",
            flush=True,
        )
        copyover.install_signal_handler(game)

        def _session_factory(reader, writer, g, gateway_session_id=None):
            return Session(
                reader, writer, g, gateway_session_id=gateway_session_id
            )

        bridge = GatewayBridge(game, _session_factory)
        # So copyover can CTRL ``planned_restart`` before exiting (Discord
        # must not treat every watcher/auto-deploy reload as a crash).
        game.gateway_bridge = bridge
        # tick_loop starts inside GatewayBridge after the welcome/reattach
        # CTRL (see engine/gateway_client.py) so Cadence cannot move
        # sessionless PCs before sockets rebind.
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
    #
    # Start tick_loop only AFTER resume so Cadence / no_loiter cannot move
    # reattaching PCs while they are still sessionless Echoes.
    copyover.install_signal_handler(game)
    await copyover.resume(game)
    asyncio.create_task(game.tick_loop())

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
    # Earliest stamp: covers import-time delays before async main() runs.
    try:
        from engine import game_heartbeat as _hb
        _hb.touch_heartbeat("process_start")
    except Exception:
        pass
    try:
        asyncio.run(main())
    except KeyboardInterrupt:              # Ctrl-C
        print("\nShutting down.")
