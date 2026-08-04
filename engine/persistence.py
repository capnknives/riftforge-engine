"""
engine/persistence.py -- saving and loading the world with SQLite (two-repo
purity Phase 3: docs/plans/two_repo_purity.md).

This is the milestone-2 layer: characters (and the items they carry, and items
lying in rooms) survive a server restart. It uses Python's built-in sqlite3
module -- no external database, no dependencies, just a single .db file.

Design notes:

- engine/world.py stays free of storage concerns, exactly the way it stays free
  of networking. Rooms/Characters/Items don't know a database exists; this
  module reads their attributes and writes rows, and builds objects back
  from rows.
- We save a FULL SNAPSHOT every time (wipe the tables, rewrite everything).
  Characters come from game.characters (see engine/char_index.py); loose
  room items still walk the room dict once. Autosave is throttled in
  server.Game.on_tick (every AUTOSAVE_INTERVAL_SECONDS of wall-clock time)
  so the wipe+rewrite does not stall the asyncio loop every heartbeat.
  EXTENSION POINT: switch to dirty-tracking if the world ever gets huge.
- Rooms themselves are NOT stored. The map is still built in code by
  build_world(); the database records which room each character/item is IN,
  keyed by the room's name. EXTENSION POINT: move the map itself into the DB.
- "Logout is not deletion" (systems doc section 4-E): a character who logs out
  stays in the world as an invulnerable Echo, so the characters table is the
  full roster -- online players AND echoes alike.

Two SUPERS-specific spots (Evil Strikes Back's moral-balance meter, and
re-deriving max HP after un-spiriting a character whose body was lost) go
through engine.hooks (`ensure_game_defaults`, `recompute_hp`) instead of a
direct `from supers import balance/stats` -- zero SUPERS imports here, same
as engine/world.py. Root persistence.py is now a thin re-export facade over
this module.
"""

import json                # stats will be stored as a JSON blob (milestone 3)
import sqlite3
import time
import zlib

# Blob codec + the two SUPERS side-effects below come from engine.hooks
# (SUPERS registers its implementations at boot). No direct supers import
# here -- purity gate / docs/ENGINE_CONSUMER.md.
from engine.hooks import (
    apply_character_blob,
    character_to_blob,
    ensure_game_defaults,
    recompute_hp,
    upgrade_legacy_container,
)
from engine.world import Character, Item, Room, note_item_created_seq


def _resolve_saved_room(game, room_key, character_name):
    """Return the Room for a saved character ``room_key``.

    The map is rebuilt from JSON every boot -- rooms themselves are not in
    SQLite. When the saved key is missing (stale checkout, deferred map not
    restored, protect-skipped overlay, …) the old code dumped the character
    on ``game.start_room`` (Central Plaza). Plaza is ``no_loiter``, so the
    next tick spilled sessionless Echoes onto North Avenue -- which looked
    like "copyover teleported me to town" even though the DB still had the
    real ``room_key``.

    Fix: keep the saved key. Register a stub Room under that key so the
    character stays put until the real map content is on disk. Loud log so
    staff see the map lag.

    **Prevention:** persistable runtime rooms (vehicle interiors, charter
    cabins, …) must register a pre-load ensure in
    ``engine/runtime_rooms.py`` via ``supers/runtime_rooms.py`` so the
    real room exists before this runs. See CONTENT_AUTHORING.md.
    """
    if not room_key:
        print(
            f"[persistence] {character_name!r} has empty room_key -- "
            f"using start room {getattr(game.start_room, 'key', None)!r}",
            flush=True,
        )
        return game.start_room
    room = game.rooms.get(room_key)
    if room is not None:
        return room
    print(
        f"[persistence] saved room {room_key!r} missing for "
        f"{character_name!r} -- keeping a stub so they are not dumped "
        f"to {getattr(game.start_room, 'key', None)!r}. Update map JSON "
        f"(or restore runtime maps) and restart.",
        flush=True,
    )
    stub = Room(
        room_key,
        "The space you remember is thin here -- the map that held this "
        "place is not loaded. You have not moved; the world around you "
        "has not finished reforming. Staff: restore the missing map "
        f"file that defines {room_key!r}.",
    )
    # Mark so look / GM where can tell authored rooms from recovery stubs.
    stub.map_missing_stub = True
    stub.no_combat = True
    stub.wilderness = False
    game.rooms[room_key] = stub
    return stub


def _safe_relocation_room(game, character):
    """Pick an authored room for boot-heal relocation off a persistence stub."""
    from engine.room_vnum import lookup_room

    rooms = getattr(game, "rooms", None) or {}
    start = getattr(game, "start_room", None)

    def _usable(key):
        if not key:
            return None
        room = lookup_room(game, key)
        if room is None or getattr(room, "map_missing_stub", False):
            return None
        return room

    for key in (
        getattr(character, "home_room_key", None),
        getattr(character, "body_room_key", None),
    ):
        room = _usable(key)
        if room is not None:
            return room
    return start


def heal_map_missing_stub_occupants(game):
    """Boot heal: move bodies off persistence ``map_missing_stub`` rooms.

    ``_resolve_saved_room`` can register a thin stub when the saved
    ``room_key`` is absent from map JSON at load. Vehicle ensure and pit
    reaping replace many of those keys later in boot, but stale DB rows
    (old Purgatory pits, removed vehicle interiors, …) can still leave
    Echoes/NPCs on stubs every restart until their ``room_key`` is healed.
    """
    if game is None:
        return 0
    rooms = getattr(game, "rooms", None) or {}
    moved = 0
    emptied_stub_keys = []
    roster = getattr(game, "characters", None) or []
    if isinstance(roster, dict):
        roster = roster.values()
    for character in list(roster):
        loc = getattr(character, "location", None)
        if loc is None or not getattr(loc, "map_missing_stub", False):
            continue
        dest = _safe_relocation_room(game, character)
        if dest is None:
            continue
        stub_key = getattr(loc, "key", None)
        try:
            character.move_to(dest)
        except Exception:
            character.location = dest
        moved += 1
        if stub_key and stub_key not in emptied_stub_keys:
            emptied_stub_keys.append(stub_key)
    for stub_key in emptied_stub_keys:
        room = rooms.get(stub_key)
        if room is None or not getattr(room, "map_missing_stub", False):
            continue
        if list(room.characters()):
            continue
        rooms.pop(stub_key, None)
    if moved:
        print(
            f"[persistence] healed {moved} occupant(s) off map_missing_stub "
            f"rooms",
            flush=True,
        )
    return moved


# Everything the database needs to exist. "IF NOT EXISTS" makes this safe to
# run every startup: it creates the tables on first boot and does nothing after.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    name        TEXT PRIMARY KEY,          -- character names are unique
    description TEXT NOT NULL,
    room_key    TEXT NOT NULL,             -- the Room.key they were last in
    stats       TEXT NOT NULL DEFAULT '{}' -- JSON blob; the stat spine lands here
);
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY,       -- SQLite auto-assigns rowids
    key         TEXT NOT NULL,
    description TEXT NOT NULL,
    -- 'gear' = job kit bag (Character.gear_bag), not surface inventory.
    holder_type TEXT NOT NULL CHECK (holder_type IN ('room', 'character', 'gear')),
    holder_key  TEXT NOT NULL,             -- room key or character name
    container   TEXT NOT NULL DEFAULT '{}' -- JSON: {"locked": bool, "loot": [...]}
);
CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,          -- tiny key/value store for flags
    value       TEXT NOT NULL
);
-- D41 overland gather nodes + wilds homestead pockets (survive reboot).
CREATE TABLE IF NOT EXISTS homestead_plots (
    plot_id       TEXT PRIMARY KEY,
    owner_name    TEXT NOT NULL UNIQUE,
    cell_room_key TEXT NOT NULL UNIQUE,
    enter_name    TEXT,
    hub_room_key  TEXT,
    -- Homestead v2: JSON blob (micro coords, ledger, tier, residents, …).
    meta_json     TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS homestead_rooms (
    room_key     TEXT PRIMARY KEY,
    plot_id      TEXT NOT NULL,
    description  TEXT NOT NULL,
    flags_json   TEXT NOT NULL,
    exits_json   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gather_nodes (
    room_key         TEXT NOT NULL,
    resource         TEXT NOT NULL,
    remaining        INTEGER NOT NULL,
    capacity         INTEGER NOT NULL,
    respawn_at_tick  INTEGER,
    PRIMARY KEY (room_key, resource)
);
-- Personal Heaven / Hell pockets (docs/plans/personal_afterlife.md).
CREATE TABLE IF NOT EXISTS personal_realms (
    realm_id     TEXT PRIMARY KEY,
    owner_name   TEXT NOT NULL UNIQUE,
    aspect       TEXT NOT NULL,
    hub_room_key TEXT,
    seed_json    TEXT NOT NULL,
    editors_json TEXT NOT NULL,
    rules_json   TEXT NOT NULL,
    guests_json  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS personal_realm_rooms (
    room_key     TEXT PRIMARY KEY,
    realm_id     TEXT NOT NULL,
    description  TEXT NOT NULL,
    flags_json   TEXT NOT NULL,
    exits_json   TEXT NOT NULL
);
-- God demesnes (docs/plans/god_demesne_creation.md) -- SQLite, not git maps.
CREATE TABLE IF NOT EXISTS demesnes (
    demesne_id     TEXT PRIMARY KEY,
    owner_name     TEXT NOT NULL UNIQUE,
    host_plane     TEXT NOT NULL,
    host_hub_key   TEXT NOT NULL,
    hub_room_key   TEXT,
    macro_size     INTEGER NOT NULL DEFAULT 3,
    sealed         INTEGER NOT NULL DEFAULT 0,
    unmade         INTEGER NOT NULL DEFAULT 0,
    meta_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS demesne_rooms (
    room_key     TEXT PRIMARY KEY,
    demesne_id   TEXT NOT NULL,
    description  TEXT NOT NULL,
    flags_json   TEXT NOT NULL,
    exits_json   TEXT NOT NULL
);
-- Hard gm fold vault: zlib-compressed player Echo payload (not in live world).
CREATE TABLE IF NOT EXISTS character_vault (
    name       TEXT PRIMARY KEY,
    room_key   TEXT NOT NULL,
    folded_at  REAL NOT NULL,
    folded_by  TEXT,
    payload    BLOB NOT NULL
);
-- Hot-editable help overlay (engine/help_db.py; docs/plans/
-- helpfile_editing_system.md). primary_keyword is a natural key, same
-- style as characters.name -- SQLite still keeps an implicit rowid for it
-- (not WITHOUT ROWID), which the help_fts triggers below rely on.
CREATE TABLE IF NOT EXISTS helpfiles (
    primary_keyword TEXT PRIMARY KEY,
    category        TEXT NOT NULL DEFAULT '',
    gm_only         INTEGER NOT NULL DEFAULT 0,
    is_ic           INTEGER NOT NULL DEFAULT 0,
    syntax_block    TEXT NOT NULL DEFAULT '',
    body_text       TEXT NOT NULL DEFAULT '',
    author          TEXT NOT NULL,
    last_modified   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS help_aliases (
    alias           TEXT PRIMARY KEY,
    primary_keyword TEXT NOT NULL REFERENCES helpfiles(primary_keyword)
);
-- Engine-level player accounts (above Characters). name is the normalized
-- login key; password_hash is account-auth (characters keep their own);
-- data is a JSON blob of character_keys / gm_rank / prefs / totals.
CREATE TABLE IF NOT EXISTS accounts (
    name           TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    password_hash  TEXT NOT NULL DEFAULT '',
    data           TEXT NOT NULL DEFAULT '{}'
);
-- Player-owned civic shop fixtures (street enter mouths; P0 player_shops).
CREATE TABLE IF NOT EXISTS player_shops (
    shop_id        TEXT PRIMARY KEY,
    owner_key      TEXT NOT NULL,
    host_room_key  TEXT NOT NULL,
    enter_alias    TEXT NOT NULL,
    display_name   TEXT NOT NULL,
    amenity_type   TEXT NOT NULL DEFAULT 'retail',
    hp             INTEGER NOT NULL DEFAULT 100,
    hp_max         INTEGER NOT NULL DEFAULT 100,
    wrecked        INTEGER NOT NULL DEFAULT 0,
    meta_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS player_shop_rooms (
    room_key       TEXT PRIMARY KEY,
    shop_id        TEXT NOT NULL,
    description    TEXT NOT NULL,
    flags_json     TEXT NOT NULL,
    exits_json     TEXT NOT NULL
);
-- Player-founded townships (docs/plans/player_towns.md).
CREATE TABLE IF NOT EXISTS township_plots (
    town_id        TEXT PRIMARY KEY,
    town_name      TEXT NOT NULL,
    founder_name   TEXT NOT NULL UNIQUE,
    mouth_room_key TEXT NOT NULL,
    macro_x        INTEGER NOT NULL,
    macro_y        INTEGER NOT NULL,
    enter_name     TEXT,
    hub_room_key   TEXT,
    meta_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS township_rooms (
    room_key       TEXT PRIMARY KEY,
    town_id        TEXT NOT NULL,
    description    TEXT NOT NULL,
    flags_json     TEXT NOT NULL,
    exits_json     TEXT NOT NULL
);
-- External-content FTS5 index (keyword + body only -- aliases already get
-- exact-match coverage via help_aliases, see help_db.get_entry). Kept in
-- sync by the three triggers below rather than the app re-indexing itself.
CREATE VIRTUAL TABLE IF NOT EXISTS help_fts USING fts5(
    primary_keyword, body_text,
    content='helpfiles', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS helpfiles_ai AFTER INSERT ON helpfiles BEGIN
    INSERT INTO help_fts(rowid, primary_keyword, body_text)
    VALUES (new.rowid, new.primary_keyword, new.body_text);
END;
CREATE TRIGGER IF NOT EXISTS helpfiles_ad AFTER DELETE ON helpfiles BEGIN
    INSERT INTO help_fts(help_fts, rowid, primary_keyword, body_text)
    VALUES('delete', old.rowid, old.primary_keyword, old.body_text);
END;
CREATE TRIGGER IF NOT EXISTS helpfiles_au AFTER UPDATE ON helpfiles BEGIN
    INSERT INTO help_fts(help_fts, rowid, primary_keyword, body_text)
    VALUES('delete', old.rowid, old.primary_keyword, old.body_text);
    INSERT INTO help_fts(rowid, primary_keyword, body_text)
    VALUES (new.rowid, new.primary_keyword, new.body_text);
END;
"""


def _sqlite_journal_mode():
    """Journal mode for on-disk DBs.

    Default is **DELETE** (not WAL). This MUD is single-writer; WAL's
    concurrent-reader benefit is unused, and Docker Desktop bind-mounts of
    ``riftforge.db`` on Windows have repeatedly corrupted under WAL
    (``database disk image is malformed`` / btree errors during
    ``DELETE FROM characters`` world saves). That left sessions
    outbound-only (combat spam, dead commands).

    Override with ``RIFTFORGE_SQLITE_JOURNAL`` (``WAL`` / ``DELETE`` /
    ``TRUNCATE`` / ``MEMORY`` / ``OFF``). Live Linux can set ``WAL`` if
    desired; DELETE remains correct and safer as the default.
    """
    import os

    override = (os.environ.get("RIFTFORGE_SQLITE_JOURNAL") or "").strip().upper()
    if override:
        return override
    return "DELETE"


def connect(path):
    """Open (or create) the database at `path` and ensure the tables exist.

    `path` may also be ":memory:" -- SQLite's built-in throwaway mode, which
    the smoke test uses so test runs never touch a real file. Journal modes
    only make sense for a real file -- ":memory:" has no journal to speak
    of and rejects the pragma with an OperationalError.
    """
    conn = sqlite3.connect(path)
    if path != ":memory:":
        mode = _sqlite_journal_mode()
        try:
            conn.execute(f"PRAGMA journal_mode={mode}")
        except sqlite3.Error as exc:
            # Bad override / exotic build — fall back rather than refuse boot.
            print(
                f"[persistence] PRAGMA journal_mode={mode} failed ({exc!r}); "
                "trying WAL",
                flush=True,
            )
            conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)   # executescript runs several statements at once
    _migrate(conn)
    return conn


# Columns added to a table after its original CREATE TABLE already shipped
# ('IF NOT EXISTS' in _SCHEMA above only covers whole tables, not new columns
# on an existing one). Each entry is (version, sql) -- version numbers must
# be sequential starting at 1, since _migrate below applies every entry
# greater than the database's current schema_version, in order.
#
# `items.container` (lockbox locked/loot state, added alongside 'open') was
# the first column added to an existing table after the fact; it used to be
# its own hardcoded try/except ALTER TABLE. Now it's just migration #1 --
# the next real column addition (whenever one lands) is a new tuple appended
# here instead of new bespoke boilerplate.
_MIGRATIONS = [
    (1, "ALTER TABLE items ADD COLUMN container TEXT NOT NULL DEFAULT '{}'"),
    # D41: CREATE IF NOT EXISTS so existing DBs (schema_version=1) catch up;
    # fresh DBs already have these from _SCHEMA.
    (2, """CREATE TABLE IF NOT EXISTS homestead_plots (
    plot_id TEXT PRIMARY KEY,
    owner_name TEXT NOT NULL UNIQUE,
    cell_room_key TEXT NOT NULL UNIQUE,
    enter_name TEXT,
    hub_room_key TEXT
)"""),
    # Homestead v2 meta blob (ledger, micro coords, tier, residents, …).
    # Fresh DBs also get meta_json from _SCHEMA; ALTER covers older trees.
    (3, """CREATE TABLE IF NOT EXISTS homestead_rooms (
    room_key TEXT PRIMARY KEY,
    plot_id TEXT NOT NULL,
    description TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    exits_json TEXT NOT NULL
)"""),
    (4, """CREATE TABLE IF NOT EXISTS gather_nodes (
    room_key TEXT NOT NULL,
    resource TEXT NOT NULL,
    remaining INTEGER NOT NULL,
    capacity INTEGER NOT NULL,
    respawn_at_tick INTEGER,
    PRIMARY KEY (room_key, resource)
)"""),
    # Job gear_bag rows use holder_type 'gear'. Older DBs only allowed
    # room/character -- save_world then IntegrityError'd on login (killing
    # Session.run before play()) and on tick autosave. SQLite cannot ALTER
    # a CHECK; rebuild the table (callable migration -- see _migrate).
    (5, "_migrate_items_holder_gear"),
    # Personal Heaven/Hell pockets (docs/plans/personal_afterlife.md).
    (6, """CREATE TABLE IF NOT EXISTS personal_realms (
    realm_id TEXT PRIMARY KEY,
    owner_name TEXT NOT NULL UNIQUE,
    aspect TEXT NOT NULL,
    hub_room_key TEXT,
    seed_json TEXT NOT NULL,
    editors_json TEXT NOT NULL,
    rules_json TEXT NOT NULL,
    guests_json TEXT NOT NULL
)"""),
    (7, """CREATE TABLE IF NOT EXISTS personal_realm_rooms (
    room_key TEXT PRIMARY KEY,
    realm_id TEXT NOT NULL,
    description TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    exits_json TEXT NOT NULL
)"""),
    # Hard gm fold: extract offline Echo to vault (survives save_world wipe).
    (8, """CREATE TABLE IF NOT EXISTS character_vault (
    name TEXT PRIMARY KEY,
    room_key TEXT NOT NULL,
    folded_at REAL NOT NULL,
    folded_by TEXT,
    payload BLOB NOT NULL
)"""),
    # Hot-editable help overlay (engine/help_db.py). Several statements
    # (two tables, one FTS5 virtual table, three triggers) -- conn.execute()
    # only runs one statement at a time, so this is a callable migration
    # like #5, not a single SQL string.
    (9, "_migrate_add_help_tables"),
    # Engine-level accounts (login identity above Characters).
    (10, """CREATE TABLE IF NOT EXISTS accounts (
    name           TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    password_hash  TEXT NOT NULL DEFAULT '',
    data           TEXT NOT NULL DEFAULT '{}'
)"""),
    # Homestead v2: plot meta_json (ledger, micro, tier, residents, …).
    (11, "ALTER TABLE homestead_plots ADD COLUMN meta_json TEXT NOT NULL DEFAULT '{}'"),
    # Player shops P0: civic fixture mouths + pocket hubs.
    (12, """CREATE TABLE IF NOT EXISTS player_shops (
    shop_id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    host_room_key TEXT NOT NULL,
    enter_alias TEXT NOT NULL,
    display_name TEXT NOT NULL,
    amenity_type TEXT NOT NULL DEFAULT 'retail',
    hp INTEGER NOT NULL DEFAULT 100,
    hp_max INTEGER NOT NULL DEFAULT 100,
    wrecked INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT NOT NULL DEFAULT '{}'
)"""),
    (13, """CREATE TABLE IF NOT EXISTS player_shop_rooms (
    room_key TEXT PRIMARY KEY,
    shop_id TEXT NOT NULL,
    description TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    exits_json TEXT NOT NULL
)"""),
    # God demesnes skeleton (docs/plans/god_demesne_creation.md).
    (14, """CREATE TABLE IF NOT EXISTS demesnes (
    demesne_id TEXT PRIMARY KEY,
    owner_name TEXT NOT NULL UNIQUE,
    host_plane TEXT NOT NULL,
    host_hub_key TEXT NOT NULL,
    hub_room_key TEXT,
    macro_size INTEGER NOT NULL DEFAULT 3,
    sealed INTEGER NOT NULL DEFAULT 0,
    unmade INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT NOT NULL DEFAULT '{}'
)"""),
    (15, """CREATE TABLE IF NOT EXISTS demesne_rooms (
    room_key TEXT PRIMARY KEY,
    demesne_id TEXT NOT NULL,
    description TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    exits_json TEXT NOT NULL
)"""),
    (16, """CREATE TABLE IF NOT EXISTS township_plots (
    town_id TEXT PRIMARY KEY,
    town_name TEXT NOT NULL,
    founder_name TEXT NOT NULL UNIQUE,
    mouth_room_key TEXT NOT NULL,
    macro_x INTEGER NOT NULL,
    macro_y INTEGER NOT NULL,
    enter_name TEXT,
    hub_room_key TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}'
)"""),
    (17, """CREATE TABLE IF NOT EXISTS township_rooms (
    room_key TEXT PRIMARY KEY,
    town_id TEXT NOT NULL,
    description TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    exits_json TEXT NOT NULL
)"""),
]


def _schema_version(conn):
    """The database's current migration level (0 for a database that
    predates schema_version entirely -- every _MIGRATIONS entry runs)."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row[0]) if row else 0


def _migrate_items_holder_gear(conn):
    """Rebuild ``items`` so CHECK allows holder_type ``gear``.

    Called as migration #5. Uses discrete ``execute`` calls (not
    ``executescript``) so we stay inside the outer ``with conn``
    transaction -- ``executescript`` would COMMIT mid-migration.
    """
    # Leftover from a crash between CREATE and RENAME -- drop and retry.
    conn.execute("DROP TABLE IF EXISTS items__gear_chk")
    conn.execute(
        """
        CREATE TABLE items__gear_chk (
            id          INTEGER PRIMARY KEY,
            key         TEXT NOT NULL,
            description TEXT NOT NULL,
            holder_type TEXT NOT NULL
                CHECK (holder_type IN ('room', 'character', 'gear')),
            holder_key  TEXT NOT NULL,
            container   TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    # Older DBs may lack container (migration 1 not applied yet should not
    # happen -- we run in order -- but SELECT * is fragile if columns drift).
    cols = [
        row[1]
        for row in conn.execute("PRAGMA table_info(items)").fetchall()
    ]
    if "container" in cols:
        conn.execute(
            """
            INSERT INTO items__gear_chk
                (id, key, description, holder_type, holder_key, container)
            SELECT id, key, description, holder_type, holder_key, container
            FROM items
            """
        )
    else:
        conn.execute(
            """
            INSERT INTO items__gear_chk
                (id, key, description, holder_type, holder_key, container)
            SELECT id, key, description, holder_type, holder_key, '{}'
            FROM items
            """
        )
    conn.execute("DROP TABLE items")
    conn.execute("ALTER TABLE items__gear_chk RENAME TO items")


def _migrate_add_help_tables(conn):
    """Add the hot-editable help overlay tables/index/triggers (migration
    #9) to a database created before they existed. Identical statements to
    the ones in ``_SCHEMA`` -- every ``IF NOT EXISTS`` makes this a no-op
    for a fresh database that already got them from ``_SCHEMA`` directly.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS helpfiles (
            primary_keyword TEXT PRIMARY KEY,
            category        TEXT NOT NULL DEFAULT '',
            gm_only         INTEGER NOT NULL DEFAULT 0,
            is_ic           INTEGER NOT NULL DEFAULT 0,
            syntax_block    TEXT NOT NULL DEFAULT '',
            body_text       TEXT NOT NULL DEFAULT '',
            author          TEXT NOT NULL,
            last_modified   REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS help_aliases (
            alias           TEXT PRIMARY KEY,
            primary_keyword TEXT NOT NULL REFERENCES helpfiles(primary_keyword)
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS help_fts USING fts5(
            primary_keyword, body_text,
            content='helpfiles', content_rowid='rowid'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS helpfiles_ai AFTER INSERT ON helpfiles BEGIN
            INSERT INTO help_fts(rowid, primary_keyword, body_text)
            VALUES (new.rowid, new.primary_keyword, new.body_text);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS helpfiles_ad AFTER DELETE ON helpfiles BEGIN
            INSERT INTO help_fts(help_fts, rowid, primary_keyword, body_text)
            VALUES('delete', old.rowid, old.primary_keyword, old.body_text);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS helpfiles_au AFTER UPDATE ON helpfiles BEGIN
            INSERT INTO help_fts(help_fts, rowid, primary_keyword, body_text)
            VALUES('delete', old.rowid, old.primary_keyword, old.body_text);
            INSERT INTO help_fts(rowid, primary_keyword, body_text)
            VALUES (new.rowid, new.primary_keyword, new.body_text);
        END
        """
    )


# Name -> callable for migrations that cannot be a single SQL statement.
_MIGRATION_CALLABLES = {
    "_migrate_items_holder_gear": _migrate_items_holder_gear,
    "_migrate_add_help_tables": _migrate_add_help_tables,
}


def _migrate(conn):
    """Bring the database up to the latest schema by applying every
    migration newer than its recorded schema_version, in order, and
    recording the new version after each -- so a boot that dies partway
    through resumes from the last completed migration instead of redoing
    (or skipping) one. Runs on every boot; a database already at the latest
    version does nothing.

    Each ``_MIGRATIONS`` entry is ``(version, sql_string_or_callable_name)``.
    Callable names resolve through ``_MIGRATION_CALLABLES`` (table rebuilds
    that need several statements inside one transaction).

    SQLite has no 'ADD COLUMN IF NOT EXISTS', so the try/except below stays
    as a safety net (not the primary mechanism) for one specific case: a
    database that already has `items.container` from the OLD, pre-versioned
    code path (a single hardcoded try/except ALTER TABLE) but has never
    recorded a schema_version -- without it, that database would hit
    "duplicate column" and crash instead of just catching up its version
    number.
    """
    current = _schema_version(conn)
    with conn:
        for version, step in _MIGRATIONS:
            if version <= current:
                continue
            try:
                if isinstance(step, str) and step in _MIGRATION_CALLABLES:
                    _MIGRATION_CALLABLES[step](conn)
                else:
                    conn.execute(step)
            except sqlite3.OperationalError:
                pass   # already applied by the old pre-versioned code path
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES "
                "('schema_version', ?)",
                (str(version),),
            )


def is_seeded(conn):
    """Has this database ever been populated with the starter world?

    First boot: False -> the caller places the starter items and calls
    mark_seeded(). Every later boot: True -> load what the players left behind
    instead of re-placing starter items (otherwise a picked-up sword would
    respawn in the plaza on every restart AND stay in the player's bag).
    """
    row = conn.execute("SELECT value FROM meta WHERE key = 'seeded'").fetchone()
    return row is not None


def mark_seeded(conn):
    """Record that the starter world has been placed (see is_seeded)."""
    with conn:   # 'with conn' wraps this in a transaction and commits on success
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('seeded', '1')")


def load_game_time(conn):
    """How many ticks of the compressed game-time clock have elapsed
    (Milestone E, section 4-E's pacing follow-up) -- reuses the same
    generic `meta` table as is_seeded, no schema change needed. 0 if this
    save predates the feature (a fresh world starts its clock at day 0)."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'game_time_ticks'"
    ).fetchone()
    return int(row[0]) if row else 0


def save_game_time(conn, ticks):
    """Persist the current game-time tick count (see load_game_time)."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('game_time_ticks', ?)",
            (str(ticks),),
        )


def load_calendar_epoch_day(conn):
    """Gregorian display epoch: absolute game-day that maps to 2015-10-15.

    Returns None when the key is missing so Game can rebase an upgraded
    world (set to current day) or leave a fresh world at 0. See
    engine.game_calendar and server.Game.__init__.
    """
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'calendar_epoch_day'"
    ).fetchone()
    if not row:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def save_calendar_epoch_day(conn, epoch_day):
    """Persist the Gregorian calendar_epoch_day offset (see load)."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('calendar_epoch_day', ?)",
            (str(int(epoch_day)),),
        )


def load_moral_state(conn):
    """Load Evil Strikes Back world meter + eclipse from meta.

    Returns a dict with moral_balance, eclipse_until_tick,
    moral_event_cooldown_until, moral_maxed_side, moral_maxed_since_tick,
    moral_last_casualty_tick, moral_scout_cooldown_until, holy_war_active,
    rank_titles_visible (defaults when keys are missing).
    """
    def _int_meta(key, default=0):
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return default

    def _str_meta(key, default=None):
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if not row or row[0] in (None, "", "None"):
            return default
        return str(row[0])

    def _float_meta(key, default=0.0):
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return float(row[0])
        except (TypeError, ValueError):
            return default

    maxed_side = _str_meta("moral_maxed_side", None)
    # Only 'evil' / 'good' are valid hold sides.
    if maxed_side not in ("evil", "good"):
        maxed_side = None

    return {
        "moral_balance": _int_meta("moral_balance", 0),
        "eclipse_until_tick": _int_meta("eclipse_until_tick", 0),
        "moral_event_cooldown_until": _int_meta(
            "moral_event_cooldown_until", 0
        ),
        "moral_maxed_side": maxed_side,
        "moral_maxed_since_tick": _int_meta("moral_maxed_since_tick", 0),
        "moral_last_casualty_tick": _int_meta(
            "moral_last_casualty_tick", 0
        ),
        "moral_scout_cooldown_until": _int_meta(
            "moral_scout_cooldown_until", 0
        ),
        "moral_last_centering_tick": _int_meta(
            "moral_last_centering_tick", 0
        ),
        "moral_good_window_start_tick": _int_meta(
            "moral_good_window_start_tick", 0
        ),
        "moral_good_steps_in_window": _int_meta(
            "moral_good_steps_in_window", 0
        ),
        # Host/Infernal war global (gm holywar); 0=off 1=on. Default off.
        "holy_war_active": bool(_int_meta("holy_war_active", 0)),
        # Rank flavor on score (gm titles); 1=on 0=off. Default on.
        "rank_titles_visible": bool(_int_meta("rank_titles_visible", 1)),
        "roadtrip_minutes": _float_meta("roadtrip_minutes", 30.0),
        "vehicle_pvp_enabled": bool(_int_meta("vehicle_pvp_enabled", 0)),
        "tow_dispatch_enabled": bool(_int_meta("tow_dispatch_enabled", 1)),
    }


def save_moral_state(conn, game):
    """Persist moral_balance / eclipse / hold timers / scout cooldown."""
    ensure_game_defaults(game)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_balance', ?)",
            (str(int(game.moral_balance)),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('eclipse_until_tick', ?)",
            (str(int(game.eclipse_until_tick or 0)),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_event_cooldown_until', ?)",
            (str(int(game.moral_event_cooldown_until or 0)),),
        )
        # Maxed-hold arming -- must survive Docker restart or the ±100
        # wall timer resets every bounce.
        side = getattr(game, "moral_maxed_side", None) or ""
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_maxed_side', ?)",
            (str(side),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_maxed_since_tick', ?)",
            (str(int(getattr(game, "moral_maxed_since_tick", 0) or 0)),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_last_casualty_tick', ?)",
            (str(int(getattr(game, "moral_last_casualty_tick", 0) or 0)),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_scout_cooldown_until', ?)",
            (str(int(getattr(game, "moral_scout_cooldown_until", 0) or 0)),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_last_centering_tick', ?)",
            (
                str(
                    int(getattr(game, "moral_last_centering_tick", 0) or 0)
                ),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_good_window_start_tick', ?)",
            (
                str(
                    int(
                        getattr(game, "moral_good_window_start_tick", 0) or 0
                    )
                ),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('moral_good_steps_in_window', ?)",
            (
                str(
                    int(
                        getattr(game, "moral_good_steps_in_window", 0) or 0
                    )
                ),
            ),
        )
        # Holy war (Host vs Infernal) -- survives restart; default off.
        holy = 1 if bool(getattr(game, "holy_war_active", False)) else 0
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('holy_war_active', ?)",
            (str(holy),),
        )
        # Rank titles on score -- survives restart; default on.
        titles = 1 if bool(getattr(game, "rank_titles_visible", True)) else 0
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('rank_titles_visible', ?)",
            (str(titles),),
        )
        road_min = float(getattr(game, "roadtrip_minutes", 30.0) or 30.0)
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('roadtrip_minutes', ?)",
            (str(road_min),),
        )
        vpvp = 1 if bool(getattr(game, "vehicle_pvp_enabled", False)) else 0
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('vehicle_pvp_enabled', ?)",
            (str(vpvp),),
        )
        tdisp = 1 if bool(getattr(game, "tow_dispatch_enabled", True)) else 0
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('tow_dispatch_enabled', ?)",
            (str(tdisp),),
        )



def load_plane_soul_counts(conn):
    """Load heaven_soul_count / hell_soul_count from meta (default 0)."""
    def _int_meta(key, default=0):
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return default

    return {
        "heaven_soul_count": _int_meta("heaven_soul_count", 0),
        "hell_soul_count": _int_meta("hell_soul_count", 0),
        "next_phone_seq": _int_meta("next_phone_seq", 1000),
    }


def save_plane_soul_counts(conn, game):
    """Persist plane soul banks on Game (no SUPERS import)."""
    heaven = int(getattr(game, "heaven_soul_count", 0) or 0)
    hell = int(getattr(game, "hell_soul_count", 0) or 0)
    phone_seq = int(getattr(game, "next_phone_seq", 1000) or 1000)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('heaven_soul_count', ?)",
            (str(heaven),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('hell_soul_count', ?)",
            (str(hell),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('next_phone_seq', ?)",
            (str(phone_seq),),
        )


def _load_meta_dict(conn, key):
    """Load a JSON dict override blob from ``meta[key]`` (missing/bad = {}).

    Shared by every dict-shaped meta blob (tuning tables, hue courts,
    death beacons, Cadence overrides, …). Empty / blank values fail open
    to ``{}`` the same way a missing key does so boot never raises on a
    hand-edited meta row.
    """
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (key,)
    ).fetchone()
    # ``not row[0]`` also catches empty-string values left by a wipe.
    if not row or not row[0]:
        return {}
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def load_meta_json(conn, key):
    """Public opaque-KV read: JSON dict at ``meta[key]`` (missing = {}).

    T3 persistence-api: games should prefer this (or named wrappers) over
    inventing new SQL. SUPERS Game meta orchestration lives in
    ``supers.persist_meta`` via ``engine.hooks.set_game_meta_codec``.
    """
    return _load_meta_dict(conn, key)


def _save_meta_dict(conn, key, game, attr):
    """Persist ``game.<attr>`` (a dict) into ``meta[key]``.

    Non-dict / missing attributes coerce to ``{}`` so a corrupt in-memory
    blob cannot write junk JSON that later boot would have to heal.
    """
    blob = getattr(game, attr, None)
    if not isinstance(blob, dict):
        blob = {}
    save_meta_json(conn, key, blob)


def save_meta_json(conn, key, value):
    """Public opaque-KV write: store ``value`` (dict) at ``meta[key]``."""
    if not isinstance(value, dict):
        value = {}
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )


def load_hue_courts(conn):
    """Load celestial hue court seats from meta (default empty dict)."""
    return _load_meta_dict(conn, "hue_courts")


def save_hue_courts(conn, game):
    """Persist game.hue_courts JSON (Celestial Prince / Grigori seats).

    Serializes whatever dict is on the Game. SUPERS may pre-normalize via
    celestial_court.serialize_hue_courts before save; this layer stays
    engine-pure (no supers import).
    """
    _save_meta_dict(conn, "hue_courts", game, "hue_courts")


def load_death_beacons(conn):
    """Load death_beacons JSON dict from meta (default empty)."""
    return _load_meta_dict(conn, "death_beacons")


def save_death_beacons(conn, game):
    """Persist death_beacons so copyover keeps the Reaper queue."""
    _save_meta_dict(conn, "death_beacons", game, "death_beacons")


def load_author_mantle_event(conn):
    """Load Chuck Author mantle-resume event dict from meta (default {}).

    Missing / malformed key → empty dict so ``ensure_event`` can fill
    idle defaults. Engine-pure: no SUPERS imports.
    """
    return _load_meta_dict(conn, "author_mantle_event")


def save_author_mantle_event(conn, game):
    """Persist game.author_mantle_event so copyover keeps Chuck's event.

    Phase / until_tick / foe_key / stat_backup / auto_bout survive the
    process swap; SUPERS ``restore_after_boot`` re-reveals Chuck and
    respawns a missing Unmade shade quietly.
    """
    _save_meta_dict(conn, "author_mantle_event", game, "author_mantle_event")


def load_chuck_heaven_claim(conn):
    """Load Heaven-claim Author reaction state from meta (default {})."""
    return _load_meta_dict(conn, "chuck_heaven_claim")


def save_chuck_heaven_claim(conn, game):
    """Persist ``game.chuck_heaven_claim`` across copyover."""
    _save_meta_dict(conn, "chuck_heaven_claim", game, "chuck_heaven_claim")


# Cap matches channel_history ring sizes (bare replay last 20 lines).
OOC_HISTORY_MAXLEN = 20
WIZNET_HISTORY_MAXLEN = 20


def load_ooc_history(conn):
    """Load the global OOC ring buffer from meta (see ``channel_history``)."""
    from engine import channel_history

    return channel_history.load_channel(conn, "ooc")


def save_ooc_history(conn, game):
    """Persist ``game.ooc_history`` (see ``channel_history``)."""
    from engine import channel_history

    channel_history.save_channel(conn, game, "ooc")


def load_wiznet_history(conn):
    """Load the global wiznet ring buffer from meta (see ``channel_history``)."""
    from engine import channel_history

    return channel_history.load_channel(conn, "wiznet")


def save_wiznet_history(conn, game):
    """Persist ``game.wiznet_history`` (see ``channel_history``)."""
    from engine import channel_history

    channel_history.save_channel(conn, game, "wiznet")


def save_accounts(conn, game):
    """Write every Account on ``game.accounts`` into the accounts table.

    Full wipe-and-rewrite (same snapshot style as characters) so deleted
    accounts do not linger. Called from ``Game.save`` beside the world
    snapshot.
    """
    from engine.accounts import Account, ensure_accounts_dict

    accounts = ensure_accounts_dict(game)
    with conn:
        if not accounts:
            # Safety: never wipe persisted accounts when the in-memory dict
            # was not loaded yet (boot-order bug) or was accidentally cleared.
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM accounts"
                ).fetchone()
                existing = int(row[0]) if row else 0
            except sqlite3.OperationalError:
                existing = 0
            if existing:
                print(
                    "[persistence] save_accounts skipped: game.accounts "
                    f"empty but {existing} row(s) remain in SQLite",
                    flush=True,
                )
                return
        conn.execute("DELETE FROM accounts")
        for account in accounts.values():
            if not isinstance(account, Account):
                continue
            name = (account.name or "").strip()
            if not name:
                continue
            display = (account.display_name or name).strip() or name
            blob = json.dumps(account.to_blob())
            conn.execute(
                "INSERT INTO accounts "
                "(name, display_name, password_hash, data) "
                "VALUES (?, ?, ?, ?)",
                (name, display, account.password_hash or "", blob),
            )


def load_accounts(conn, game):
    """Rebuild ``game.accounts`` from the accounts table.

    Safe on pre-feature DBs (empty table / missing table → empty dict).
    Called once at boot after ``load_world`` so character back-pointers
    can be reconciled against live Echoes.
    """
    from engine.accounts import Account, account_lookup_key, ensure_accounts_dict

    accounts = ensure_accounts_dict(game)
    accounts.clear()
    try:
        rows = conn.execute(
            "SELECT name, display_name, password_hash, data FROM accounts"
        )
    except sqlite3.OperationalError:
        # Migration not yet applied / very old DB -- leave empty.
        return accounts
    for name, display_name, password_hash, data in rows:
        cleaned = (name or "").strip()
        if not cleaned:
            continue
        account = Account(
            cleaned,
            password_hash=password_hash or "",
            display_name=(display_name or cleaned).strip() or cleaned,
        )
        try:
            blob = json.loads(data) if data else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            blob = {}
        account.apply_blob(blob)
        accounts[account_lookup_key(account.name)] = account
    return accounts


def load_rumor_boards(conn):
    """Load player rumor boards from meta (D63). Returns {room_key: [posts]}.

    Missing key → empty dict (pre-feature saves). Malformed JSON → empty
    dict rather than crashing boot.
    """
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'rumor_boards'"
    ).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for room_key, posts in data.items():
        if not isinstance(room_key, str) or not isinstance(posts, list):
            continue
        cleaned[room_key] = [
            p for p in posts
            if isinstance(p, dict) and isinstance(p.get("text"), str)
        ]
    return cleaned


def save_rumor_boards(conn, game):
    """Persist game.rumor_boards onto the meta table (D63)."""
    boards = getattr(game, "rumor_boards", None)
    if boards is None:
        boards = {}
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('rumor_boards', ?)",
            (json.dumps(boards),),
        )


def load_lifetime_stats(conn):
    """Load GM lifetime counters from meta (gmworld panel).

    Missing / malformed key → empty default-shaped dict so old saves boot
    cleanly. Sanitization lives in supers.world_stats.normalize_loaded
    when SUPERS is present; here we only JSON-decode.
    """
    return _load_meta_dict(conn, "lifetime_stats")


def save_lifetime_stats(conn, game):
    """Persist game.lifetime_stats onto the meta table."""
    _save_meta_dict(conn, "lifetime_stats", game, "lifetime_stats")


def load_cadence_chances(conn):
    """Load GM Cadence chance overrides from meta (empty = code defaults)."""
    return _load_meta_dict(conn, "cadence_chances")


def save_cadence_chances(conn, game):
    """Persist game.cadence_chances overrides onto the meta table."""
    _save_meta_dict(conn, "cadence_chances", game, "cadence_chances")


def load_taxi_mode(conn):
    """Load GM taxi pacing mode from meta (default testing)."""
    return _load_meta_dict(conn, "taxi_mode")


def save_taxi_mode(conn, game):
    """Persist game.taxi_mode onto the meta table."""
    from engine import hooks
    hooks.save_taxi_mode_meta(conn, game)


def load_pet_adoption(conn):
    """Load Lebanon Adoption Agency weekly board from meta."""
    return _load_meta_dict(conn, "pet_adoption")


def save_pet_adoption(conn, game):
    """Persist game.pet_adoption weekly board onto the meta table."""
    _save_meta_dict(conn, "pet_adoption", game, "pet_adoption")


def load_incap_tuning(conn):
    """Load GM incap stun tuning overrides from meta (empty = code defaults)."""
    return _load_meta_dict(conn, "incap_tuning")


def save_incap_tuning(conn, game):
    """Persist game.incap_tuning overrides onto the meta table."""
    _save_meta_dict(conn, "incap_tuning", game, "incap_tuning")


def load_game_clock_tuning(conn):
    """Load GM world-clock scale overrides from meta (empty = code defaults)."""
    return _load_meta_dict(conn, "game_clock_tuning")


def save_game_clock_tuning(conn, game):
    """Persist game.game_clock_tuning overrides onto the meta table."""
    _save_meta_dict(conn, "game_clock_tuning", game, "game_clock_tuning")


def load_outgoing_damage_tuning(conn):
    """Load GM outgoing soft-cap overrides from meta (empty = code defaults)."""
    return _load_meta_dict(conn, "outgoing_damage_tuning")


def save_outgoing_damage_tuning(conn, game):
    """Persist game.outgoing_damage_tuning overrides onto the meta table."""
    _save_meta_dict(conn, "outgoing_damage_tuning", game, "outgoing_damage_tuning")


def load_corpse_decay_tuning(conn):
    """Load GM body-decay TTL overrides from meta (empty = code defaults)."""
    return _load_meta_dict(conn, "corpse_decay_tuning")


def save_corpse_decay_tuning(conn, game):
    """Persist game.corpse_decay_tuning overrides onto the meta table."""
    _save_meta_dict(conn, "corpse_decay_tuning", game, "corpse_decay_tuning")


def load_hell_exile_tuning(conn):
    """Load GM Hell exile TTL overrides from meta (empty = code defaults)."""
    return _load_meta_dict(conn, "hell_exile_tuning")


def save_hell_exile_tuning(conn, game):
    """Persist game.hell_exile_tuning overrides onto the meta table."""
    _save_meta_dict(conn, "hell_exile_tuning", game, "hell_exile_tuning")


def load_pit_drop_tuning(conn):
    """Load GM Purgatory pit drop tuning overrides from meta."""
    return _load_meta_dict(conn, "pit_drop_tuning")


def save_pit_drop_tuning(conn, game):
    """Persist game.pit_drop_tuning overrides onto the meta table."""
    _save_meta_dict(conn, "pit_drop_tuning", game, "pit_drop_tuning")


def load_marches_rim_tuning(conn):
    """Load GM Marches fog-rim ring cap from meta."""
    return _load_meta_dict(conn, "marches_rim_tuning")


def save_marches_rim_tuning(conn, game):
    """Persist game.marches_rim_tuning onto the meta table."""
    _save_meta_dict(conn, "marches_rim_tuning", game, "marches_rim_tuning")


def load_marches_rim_state(conn):
    """Load Marches monthly rim seed/affix state from meta."""
    return _load_meta_dict(conn, "marches_rim_state")


def save_marches_rim_state(conn, game):
    """Persist game.marches_rim_state onto the meta table."""
    _save_meta_dict(conn, "marches_rim_state", game, "marches_rim_state")


def load_portal_giver_costs(conn):
    """Load GM portal vendor ticket price overrides from meta."""
    return _load_meta_dict(conn, "portal_giver_costs")


def save_portal_giver_costs(conn, game):
    """Persist game.portal_giver_costs overrides onto the meta table."""
    _save_meta_dict(conn, "portal_giver_costs", game, "portal_giver_costs")


def load_cadence_scale(conn):
    """Load GM Cadence scale / LOD overrides from meta (empty = defaults)."""
    return _load_meta_dict(conn, "cadence_scale")


def save_cadence_scale(conn, game):
    """Persist game.cadence_scale overrides onto the meta table."""
    _save_meta_dict(conn, "cadence_scale", game, "cadence_scale")


def load_winchester_failsafe(conn):
    """Load Winchester Earth-job failsafe toggle from meta (default {})."""
    return _load_meta_dict(conn, "winchester_failsafe")


def save_winchester_failsafe(conn, game):
    """Persist game.winchester_failsafe (enabled + active_job) to meta."""
    _save_meta_dict(conn, "winchester_failsafe", game, "winchester_failsafe")


def load_cadence_airport(conn):
    """Load GM Cadence airport travel toggle from meta (default off)."""
    return _load_meta_dict(conn, "cadence_airport")


def save_cadence_airport(conn, game):
    """Persist game.cadence_airport onto the meta table."""
    _save_meta_dict(conn, "cadence_airport", game, "cadence_airport")


def _loot_entry_for_json(entry):
    """Serialize one body/lockbox loot entry for the items.container blob.

    Lockboxes store dict rewards ({type: growth|relic, ...}). Combat death
    spills live Item objects onto body.loot (look in / get from) -- those
    must become plain dicts here or save_world crashes mid-tick.
    """
    if isinstance(entry, dict):
        return entry
    # Duck-type Item: combat._handle_drop spills inventory this way.
    if isinstance(entry, Item):
        blob = {
            "type": "carried",
            "key": entry.key,
            "description": entry.description,
        }
        need = getattr(entry, "need", None)
        if need:
            blob["need"] = need
        catalog_id = getattr(entry, "catalog_id", None)
        if catalog_id:
            blob["catalog_id"] = catalog_id
        if getattr(entry, "provides_light", False):
            blob["provides_light"] = True
        relic = getattr(entry, "relic", None)
        if relic:
            blob["relic"] = relic
        return blob
    return None


def _loot_for_json(loot):
    """JSON-safe list for items.container 'loot' (dicts only)."""
    out = []
    for entry in loot or []:
        blob = _loot_entry_for_json(entry)
        if blob is not None:
            out.append(blob)
    return out


def _loot_from_json(loot):
    """Restore loot list: carried blobs become Items; other dicts stay dicts."""
    out = []
    for entry in loot or []:
        if isinstance(entry, dict) and entry.get("type") == "carried":
            item = Item(
                entry["key"],
                entry.get("description", entry["key"]),
            )
            if entry.get("need"):
                item.need = entry["need"]
            if entry.get("catalog_id"):
                item.catalog_id = entry["catalog_id"]
            if entry.get("provides_light"):
                item.provides_light = True
            if entry.get("relic"):
                item.relic = entry["relic"]
            out.append(item)
        else:
            out.append(entry)
    return out


def _restore_pit_mimic_fields(item, state):
    """Reattach Purgatory pit mimic stamps from a container blob."""
    if state.get("pit_mimic"):
        item.pit_mimic = True
    if state.get("pit_mimic_tier") is not None:
        try:
            item.pit_mimic_tier = int(state["pit_mimic_tier"])
        except (TypeError, ValueError):
            pass
    if state.get("pit_mimic_floor") is not None:
        try:
            item.pit_mimic_floor = int(state["pit_mimic_floor"])
        except (TypeError, ValueError):
            pass
    if state.get("pit_run_tag"):
        item.pit_run_tag = str(state["pit_run_tag"])


def _restore_on_use_fields(item, state):
    """Reattach consumable ``on_use`` / pit potion stamps from a blob.

    Pit sustain potions (and any future on_use Items) keep their effect
    dict across restart. Older saves omitted these keys -- SUPERS boot
    heal (``heal_loaded_pit_potion`` via enrich) rebuilds them by key.
    """
    on_use = state.get("on_use")
    if isinstance(on_use, dict) and on_use:
        item.on_use = dict(on_use)
    if state.get("purgatory_pit_loot"):
        item.purgatory_pit_loot = True
    if state.get("pit_potion_id"):
        item.pit_potion_id = str(state["pit_potion_id"]).strip()


def _restore_herb_fields(item, state):
    """Reattach herb joint / loaded pipe stamps from a save blob."""
    if state.get("herb_id"):
        item.herb_id = str(state["herb_id"]).strip()
    if state.get("is_joint"):
        item.is_joint = True
    if state.get("is_pipe"):
        item.is_pipe = True
    if state.get("pipe_herb_id"):
        item.pipe_herb_id = str(state["pipe_herb_id"]).strip()
    if state.get("pipe_puffs") is not None:
        try:
            item.pipe_puffs = int(state["pipe_puffs"])
        except (TypeError, ValueError):
            pass


def _bag_contents_for_json(item):
    """Serialize nested bag rows for the container blob."""
    contents = getattr(item, "bag_contents", None) or []
    out = []
    for sub in contents:
        if not isinstance(sub, Item):
            continue
        out.append({
            "key": sub.key,
            "description": sub.description,
            "container": json.loads(_item_container_blob(sub)),
        })
    return out


def _restore_bag_fields(item, state):
    """Reattach wearable bag stamps from a container blob."""
    if state.get("is_bag"):
        item.is_bag = True
    if state.get("is_gear_bag"):
        item.is_gear_bag = True
    if state.get("bag_capacity") is not None:
        try:
            item.bag_capacity = int(state["bag_capacity"])
        except (TypeError, ValueError):
            pass
    worn = state.get("container_worn")
    if worn in ("back", "shoulder"):
        item.container_worn = worn
    raw_contents = state.get("bag_contents") or []
    restored = []
    for row in raw_contents:
        if not isinstance(row, dict):
            continue
        sub = item_from_saved_container(
            row.get("key") or "item",
            row.get("description") or row.get("key") or "item",
            row.get("container") or {},
        )
        restored.append(sub)
    item.bag_contents = restored


def _item_container_blob(item):
    """JSON for the items.container column: an Item's locked/loot state (a
    dungeon lockbox's whole reward, world.make_lockbox), same reasoning as
    characters.stats -- one JSON blob means a plain flavor Item (locked=
    False, loot=[]) and a live lockbox round-trip through the same column
    with no schema difference between them. `is_body` (section 6) rides
    the same blob for the same reason -- a body Item is just another Item
    row, no schema change needed. Lodging adds furniture / owner_key / need
    so beds survive a restart with their sleep tag and claim stamp.
    """
    return json.dumps({
        "locked": item.locked,
        "loot": _loot_for_json(item.loot),
        "is_body": item.is_body,
        "is_buried": getattr(item, "is_buried", False),
        "relic": getattr(item, "relic", None),
        "furniture": getattr(item, "furniture", False),
        "owner_key": getattr(item, "owner_key", None),
        "need": getattr(item, "need", None),
        "provides_light": bool(getattr(item, "provides_light", False)),
        "catalog_id": getattr(item, "catalog_id", None),
        "aliases": list(getattr(item, "aliases", None) or []),
        # GM where item (newest copy) -- monotonic stamp from Item.__init__.
        "created_seq": int(getattr(item, "created_seq", 0) or 0),
        # Combat gear fields -- survive logout so equip / mods round-trip.
        "slot": getattr(item, "slot", None),
        "mods": dict(getattr(item, "mods", None) or {})
        if isinstance(getattr(item, "mods", None), dict) else None,
        "materials": list(getattr(item, "materials", None) or [])
        if getattr(item, "materials", None) else None,
        "color": getattr(item, "color", None),
        "equipped": bool(getattr(item, "equipped", False)),
        # Clothing layer (cosmetic under armor) -- survive logout.
        "layer": getattr(item, "layer", None),
        "worn": bool(getattr(item, "worn", False)),
        "worn_order": (
            int(getattr(item, "worn_order"))
            if getattr(item, "worn_order", None) is not None
            else None
        ),
        "cloth_material": getattr(item, "cloth_material", None),
        "warmth": getattr(item, "warmth", None),
        "cover": list(getattr(item, "cover", None) or [])
        if getattr(item, "cover", None) else None,
        "conceal": list(getattr(item, "conceal", None) or [])
        if getattr(item, "conceal", None) else None,
        "dirty": bool(getattr(item, "dirty", False))
        if hasattr(item, "dirty") else None,
        # Home grocery stock window (fridge furniture); None / absent = empty.
        "stock_until_tick": getattr(item, "stock_until_tick", None),
        # Vampire blood-pantry window (separate from mortal food stock).
        "blood_stock_until_tick": getattr(
            item, "blood_stock_until_tick", None
        ),
        # Corpse floor age (Wendigo larder stock gate); absent = unstamped.
        "body_dropped_tick": getattr(item, "body_dropped_tick", None),
        # Abandoned floor loot grace (Cadence scavengers); absent = legacy pile.
        "floor_dropped_tick": getattr(item, "floor_dropped_tick", None),
        # Beneath Lucifer's Cage TTL; absent = stamp on next vault decay tick.
        "vault_decay_at_tick": getattr(item, "vault_decay_at_tick", None),
        # Physical phone line id (supers/phone.py); absent = not a phone.
        "phone_number": getattr(item, "phone_number", None),
        "is_phone": bool(getattr(item, "is_phone", False)),
        "is_payphone": bool(getattr(item, "is_payphone", False)),
        "is_ethereal": bool(getattr(item, "is_ethereal", False)),
        # Colt / charged weapons -- survive logout with chamber count.
        "ammo_charges": (
            int(item.ammo_charges)
            if getattr(item, "ammo_charges", None) is not None
            else None
        ),
        "max_ammo": (
            int(item.max_ammo)
            if getattr(item, "max_ammo", None) is not None
            else None
        ),
        "loaded_ammo_id": getattr(item, "loaded_ammo_id", None),
        "ammo_kind": getattr(item, "ammo_kind", None),
        "stack_charges": (
            int(item.stack_charges)
            if getattr(item, "stack_charges", None) is not None
            else None
        ),
        "weapon_voice": getattr(item, "weapon_voice", None),
        "artifact_lexicon": getattr(item, "artifact_lexicon", None),
        # Purgatory pit mimic strongboxes (pose as lockboxes until opened).
        "pit_mimic": bool(getattr(item, "pit_mimic", False)),
        "pit_mimic_tier": (
            int(item.pit_mimic_tier)
            if getattr(item, "pit_mimic_tier", None) is not None
            else None
        ),
        "pit_mimic_floor": (
            int(item.pit_mimic_floor)
            if getattr(item, "pit_mimic_floor", None) is not None
            else None
        ),
        "pit_run_tag": getattr(item, "pit_run_tag", None),
        # Consumable on_use (Purgatory pit sustain potions, etc.).
        "on_use": (
            dict(item.on_use)
            if isinstance(getattr(item, "on_use", None), dict)
            and item.on_use
            else None
        ),
        "purgatory_pit_loot": bool(
            getattr(item, "purgatory_pit_loot", False)
        ),
        "pit_potion_id": getattr(item, "pit_potion_id", None),
        # Herb joints / loaded pipes (supers/herbs.py).
        "herb_id": getattr(item, "herb_id", None),
        "is_joint": bool(getattr(item, "is_joint", False)),
        "is_pipe": bool(getattr(item, "is_pipe", False)),
        "pipe_herb_id": getattr(item, "pipe_herb_id", None),
        "pipe_puffs": (
            int(item.pipe_puffs)
            if getattr(item, "pipe_puffs", None) is not None
            else None
        ),
        "is_bag": bool(getattr(item, "is_bag", False)),
        "is_gear_bag": bool(getattr(item, "is_gear_bag", False)),
        "bag_capacity": (
            int(item.bag_capacity)
            if getattr(item, "bag_capacity", None) is not None
            else None
        ),
        "container_worn": getattr(item, "container_worn", None),
        "bag_contents": _bag_contents_for_json(item),
        "gear_condition": (
            int(item.gear_condition)
            if getattr(item, "gear_condition", None) is not None
            else None
        ),
    })


_CHAR_INSERT_SQL = (
    "INSERT INTO characters (name, description, room_key, stats) "
    "VALUES (?, ?, ?, ?)"
)
_ITEM_INSERT_SQL = (
    "INSERT INTO items "
    "(key, description, holder_type, holder_key, container) "
    "VALUES (?, ?, ?, ?, ?)"
)


def _should_skip_character_save(obj, game, seen_names):
    """Return True when this live Character must not be written to SQLite."""
    room = getattr(obj, "location", None)
    if room is None:
        return True
    if getattr(obj, "tutorial_mentor_for", None):
        return True
    if getattr(obj, "transient_soul", False):
        return True
    key_low = (getattr(obj, "key", None) or "").lower()
    if key_low.startswith("gmspirit:"):
        permanent = bool(getattr(obj, "gm_spirit_permanent", False))
        if not permanent:
            try:
                from engine.accounts import ensure_accounts_dict
                for acct in ensure_accounts_dict(game).values():
                    if acct.gm_rank not in ("gm", "head_gm"):
                        continue
                    want = (
                        acct.gm_spirit_key
                        or f"gmspirit:{acct.name}"
                    )
                    if want.lower() == key_low:
                        permanent = True
                        break
            except Exception:
                permanent = False
        if not permanent:
            return True
    if (
        getattr(obj, "gm_mode", False)
        and not key_low.startswith("gmspirit:")
    ):
        return True
    # God bilocate twin (supers/god_omnipresence.py): must survive copyover
    # save/reload even though it is an is_npc shell. Key prefix covers
    # stale in-memory flags when copyover saves before overlay bytecode
    # reloads (bug report 152).
    if getattr(obj, "god_twin", False) or key_low.startswith("twin:"):
        pass
    elif (
        obj.is_npc
        and not obj.spar_only
        and not getattr(obj, "peaceful", False)
    ):
        return True
    save_name = getattr(obj, "key", None) or ""
    if save_name in seen_names:
        print(
            f"[persistence] skip duplicate character key "
            f"{save_name!r} in {getattr(room, 'key', '?')}",
            flush=True,
        )
        return True
    return False


def _character_save_rows(game, obj, seen_names):
    """Build INSERT rows for one persistable Character (or None to skip)."""
    room = getattr(obj, "location", None)
    if _should_skip_character_save(obj, game, seen_names):
        return None
    save_name = getattr(obj, "key", None) or ""
    seen_names.add(save_name)
    if getattr(obj, "gm_staff_form", False):
        spirit_key = getattr(obj, "gm_spirit_key", None) or (
            f"gmspirit:{obj.key}"
        )
        finder = getattr(game, "find_character", None)
        spirit = finder(spirit_key) if callable(finder) else None
        spirit_room = getattr(spirit, "location", None) if spirit else None
        if spirit_room is not None and getattr(spirit_room, "key", None):
            obj.gm_spirit_room_key = spirit_room.key
    blob = json.dumps(character_to_blob(obj))
    save_room = room
    if getattr(obj, "djinn_captive", False):
        real = getattr(obj, "djinn_real_room", None)
        real_key = getattr(obj, "djinn_real_room_key", None)
        if real is not None:
            save_room = real
        elif real_key and real_key in game.rooms:
            save_room = game.rooms[real_key]
    elif getattr(room, "djinn_instance_id", None):
        ret = getattr(obj, "djinn_mirage_return_room", None)
        if ret is not None:
            save_room = ret
    char_row = (obj.key, obj.description, save_room.key, blob)
    item_rows = []
    for item in obj.inventory:
        item_rows.append((
            item.key, item.description, "character", obj.key,
            _item_container_blob(item),
        ))
    for item in list(getattr(obj, "gear_bag", None) or []):
        item_rows.append((
            item.key, item.description, "gear", obj.key,
            _item_container_blob(item),
        ))
    return char_row, item_rows


def _room_floor_item_rows(game):
    """Loose floor Items as INSERT tuples (skip empty wilderness cells)."""
    rows = []
    for room in game.rooms.values():
        if not room.contents:
            continue
        for obj in room.contents:
            if not isinstance(obj, Item):
                continue
            if getattr(obj, "djinn_husk", False):
                continue
            if getattr(obj, "ephemeral_spawn_body", False):
                continue
            if getattr(obj, "decay_at_tick", None) is not None:
                continue
            rows.append((
                obj.key, obj.description, "room", room.key,
                _item_container_blob(obj),
            ))
    return rows


def _collect_world_save_snapshot(game):
    """In-memory snapshot for wipe+rewrite (sync path)."""
    from engine.char_index import iter_characters

    char_rows = []
    item_rows = []
    seen_names = set()
    for obj in iter_characters(game):
        payload = _character_save_rows(game, obj, seen_names)
        if payload is None:
            continue
        char_row, owned_items = payload
        char_rows.append(char_row)
        item_rows.extend(owned_items)
    item_rows.extend(_room_floor_item_rows(game))
    return char_rows, item_rows


async def _collect_world_save_snapshot_async(game, *, yield_every=50):
    """Cooperative snapshot build -- yields so player commands can run."""
    import asyncio
    from engine.char_index import iter_characters

    char_rows = []
    item_rows = []
    seen_names = set()
    n = 0
    for obj in iter_characters(game):
        payload = _character_save_rows(game, obj, seen_names)
        if payload is not None:
            char_row, owned_items = payload
            char_rows.append(char_row)
            item_rows.extend(owned_items)
        n += 1
        if yield_every and n % yield_every == 0:
            await asyncio.sleep(0)
    for room in game.rooms.values():
        if not room.contents:
            continue
        for obj in room.contents:
            if not isinstance(obj, Item):
                continue
            if getattr(obj, "djinn_husk", False):
                continue
            if getattr(obj, "ephemeral_spawn_body", False):
                continue
            if getattr(obj, "decay_at_tick", None) is not None:
                continue
            item_rows.append((
                obj.key, obj.description, "room", room.key,
                _item_container_blob(obj),
            ))
        n += 1
        if yield_every and n % yield_every == 0:
            await asyncio.sleep(0)
    return char_rows, item_rows


def _apply_world_save_snapshot(conn, char_rows, item_rows):
    """Wipe tables and bulk-insert a pre-built snapshot (one transaction)."""
    last_err = None
    for attempt in range(2):
        try:
            with conn:
                conn.execute("DELETE FROM characters")
                conn.execute("DELETE FROM items")
                if char_rows:
                    conn.executemany(_CHAR_INSERT_SQL, char_rows)
                if item_rows:
                    conn.executemany(_ITEM_INSERT_SQL, item_rows)
            return
        except sqlite3.OperationalError as err:
            last_err = err
            msg = str(err).lower()
            if attempt == 0 and "disk i/o" in msg:
                time.sleep(0.05)
                continue
            raise
    if last_err is not None:
        raise last_err


async def save_world_async(conn, game, *, yield_every=50):
    """Cooperative autosave: snapshot with yields, then one fast transaction."""
    char_rows, item_rows = await _collect_world_save_snapshot_async(
        game, yield_every=yield_every,
    )
    _apply_world_save_snapshot(conn, char_rows, item_rows)


def save_world(conn, game):
    """Write a full snapshot of the live world into the database.

    Characters come from ``game.characters`` (engine/char_index) so we do
    not walk ~12k empty wilderness cells every save. Loose room Items still
    need one room pass -- there is no item index yet, and item counts stay
    small. Runs inside one transaction so a crash mid-save can never leave
    the file half-written -- SQLite rolls it back.

    Production autosave uses :func:`save_world_async` so JSON/blob work can
    yield on the asyncio loop before the single bulk INSERT transaction.
    """
    char_rows, item_rows = _collect_world_save_snapshot(game)
    _apply_world_save_snapshot(conn, char_rows, item_rows)


def load_world(conn, game):
    """Rebuild the saved characters and items into the (already built) rooms.

    Called once at startup, after build_world() made the map. Every character
    comes back as an Echo -- present in their room but with session=None --
    until (unless) their player reconnects and reattaches.
    """
    # Section 6: a spirit's body/body_room can't be relinked until the
    # items loop below has placed every Item back into its room -- see the
    # fixup pass after that loop.
    pending_body_links = []
    for name, description, room_key, blob in conn.execute(
        "SELECT name, description, room_key, stats FROM characters"
    ):
        # Permanent account GM spirits load; orphan ephemeral leftovers skip.
        if (name or "").lower().startswith("gmspirit:"):
            keep = False
            try:
                blob_peek = json.loads(blob) if blob else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                blob_peek = {}
            if blob_peek.get("gm_spirit_permanent"):
                keep = True
            if not keep:
                try:
                    from engine.accounts import ensure_accounts_dict
                    low = (name or "").lower()
                    for acct in ensure_accounts_dict(game).values():
                        if acct.gm_rank not in ("gm", "head_gm"):
                            continue
                        want = (
                            acct.gm_spirit_key
                            or f"gmspirit:{acct.name}"
                        )
                        if want.lower() == low:
                            keep = True
                            break
                except Exception:
                    keep = False
            if not keep:
                continue
            # Folded staff spirits live only in character_vault until gm on.
            vault_row = vault_get(conn, name)
            if vault_row is not None and (vault_row[3] or "") == "gm-spirit-parked":
                continue
        char = Character(name, description)
        saved = json.loads(blob)
        # Restore every SUPERS field (stat spine, Origin/Path/Disciplines,
        # Cadence needs, every Path's fuel/faith/blood/instinct/soul
        # economy, ...) from the saved blob onto the freshly-built `char`.
        # supers/persist_blob.py is what actually knows the field-by-field
        # restoration logic (and every .get()-fallback pattern that lets an
        # old save missing a field just take Character's own default) --
        # this module only owns the SQL row and the room/spirit fixups
        # that need the whole-world view apply_character_blob doesn't have.
        #
        # apply_character_blob returns (body_room_key, body_key) when this
        # character is a spirit (section 6): body/body_room are live object
        # refs that can't be relinked until the items loop below has placed
        # every Item back into its room, so that pair goes on the
        # pending-links list here and gets resolved in the fixup pass after
        # that loop -- see supers/persist_blob.py's module docstring for why
        # that hand-off is the cleanest split.
        pending_link = apply_character_blob(char, saved)
        # Keep the saved room_key even when map JSON is stale -- stub rather
        # than silently dumping onto start_room / North Avenue (no_loiter).
        room = _resolve_saved_room(game, room_key, name)
        char.move_to(room)          # session stays None: this is an Echo
        if pending_link is not None:
            body_room_key, body_key = pending_link
            pending_body_links.append((char, body_room_key, body_key))

    for key, description, holder_type, holder_key, container in conn.execute(
        "SELECT key, description, holder_type, holder_key, container FROM items"
    ):
        # json.loads(container) parses the blob _item_container_blob wrote.
        # .get(..., default) means an items row saved before the 'container'
        # column existed (container == '{}', the column's DEFAULT) loads as
        # a plain, unlocked flavor item -- exactly what it was before.
        state = json.loads(container)
        item = Item(
            key, description,
            locked=state.get("locked", False),
            loot=_loot_from_json(state.get("loot", [])),
            is_body=state.get("is_body", False),
            is_buried=state.get("is_buried", False),
            relic=state.get("relic", None),
            furniture=state.get("furniture", False),
        )
        # Restore creation stamp (or keep __init__ seq) and advance the
        # global counter so later live spawns stay "newer".
        if state.get("created_seq") is not None:
            try:
                item.created_seq = int(state["created_seq"])
            except (TypeError, ValueError):
                pass
        note_item_created_seq(getattr(item, "created_seq", 0))
        if state.get("owner_key"):
            item.owner_key = state["owner_key"]
        if state.get("need"):
            item.need = state["need"]
        if state.get("provides_light"):
            item.provides_light = True
        if state.get("catalog_id"):
            item.catalog_id = state["catalog_id"]
        aliases = state.get("aliases") or []
        if isinstance(aliases, list) and aliases:
            item.aliases = [str(a) for a in aliases if a]
        # Combat gear (classic slots + mods + folklore materials + color).
        if state.get("slot"):
            item.slot = state["slot"]
        if isinstance(state.get("mods"), dict):
            item.mods = dict(state["mods"])
        if isinstance(state.get("materials"), list):
            item.materials = list(state["materials"])
        if state.get("color"):
            item.color = state["color"]
        if state.get("equipped"):
            item.equipped = True
        # Clothing layer (cosmetic under armor).
        if state.get("layer"):
            item.layer = str(state["layer"]).strip().lower()
        if state.get("worn"):
            item.worn = True
        if state.get("worn_order") is not None:
            try:
                item.worn_order = int(state["worn_order"])
            except (TypeError, ValueError):
                pass
        if state.get("cloth_material"):
            item.cloth_material = str(state["cloth_material"]).strip().lower()
        if state.get("warmth"):
            item.warmth = str(state["warmth"]).strip().lower()
        if isinstance(state.get("cover"), list):
            item.cover = [str(c).strip().lower() for c in state["cover"] if c]
        if isinstance(state.get("conceal"), list):
            item.conceal = [
                str(c).strip().lower() for c in state["conceal"] if c
            ]
        if state.get("dirty") is not None:
            item.dirty = bool(state["dirty"])
        if state.get("gear_condition") is not None:
            try:
                item.gear_condition = int(state["gear_condition"])
            except (TypeError, ValueError):
                pass
        # Fridge pantry timer (home grocery stock); absent on older saves.
        if state.get("stock_until_tick") is not None:
            try:
                item.stock_until_tick = int(state["stock_until_tick"])
            except (TypeError, ValueError):
                pass
        # Vampire blood-pantry timer; absent on older saves / empty fridges.
        if state.get("blood_stock_until_tick") is not None:
            try:
                item.blood_stock_until_tick = int(
                    state["blood_stock_until_tick"]
                )
            except (TypeError, ValueError):
                pass
        # Corpse floor age for Wendigo larder; absent on older saves.
        if state.get("body_dropped_tick") is not None:
            try:
                item.body_dropped_tick = int(state["body_dropped_tick"])
            except (TypeError, ValueError):
                pass
        # Abandoned floor loot grace / vault TTL (supers.floor_loot).
        if state.get("floor_dropped_tick") is not None:
            try:
                item.floor_dropped_tick = int(state["floor_dropped_tick"])
            except (TypeError, ValueError):
                pass
        if state.get("vault_decay_at_tick") is not None:
            try:
                item.vault_decay_at_tick = int(state["vault_decay_at_tick"])
            except (TypeError, ValueError):
                pass
        # Physical phone line (supers/phone.py); absent on older saves.
        if state.get("phone_number"):
            item.phone_number = str(state["phone_number"]).strip()
        if state.get("is_phone"):
            item.is_phone = True
        if state.get("is_payphone"):
            item.is_payphone = True
            item.furniture = True
        if state.get("is_ethereal"):
            item.is_ethereal = True
        # Colt / charged weapon ammo (absent on older saves → enrich later).
        if state.get("ammo_charges") is not None:
            try:
                item.ammo_charges = int(state["ammo_charges"])
            except (TypeError, ValueError):
                pass
        if state.get("max_ammo") is not None:
            try:
                item.max_ammo = int(state["max_ammo"])
            except (TypeError, ValueError):
                pass
        if state.get("loaded_ammo_id"):
            item.loaded_ammo_id = str(state["loaded_ammo_id"]).strip()
        if state.get("ammo_kind"):
            item.ammo_kind = str(state["ammo_kind"]).strip().lower()
        if state.get("stack_charges") is not None:
            try:
                item.stack_charges = int(state["stack_charges"])
            except (TypeError, ValueError):
                pass
        if state.get("weapon_voice"):
            item.weapon_voice = str(state["weapon_voice"]).strip().lower()
        if state.get("artifact_lexicon"):
            item.artifact_lexicon = str(state["artifact_lexicon"]).strip()
        _restore_pit_mimic_fields(item, state)
        _restore_on_use_fields(item, state)
        _restore_herb_fields(item, state)
        _restore_bag_fields(item, state)
        # bug_reports.log #21: strongboxes saved before the lockbox pass (or
        # with the default '{}' container blob) reload as flavor-only Items;
        # promote them here so `open strongbox` still pays out after a
        # reboot. Goes through engine.hooks -- the reward math (and its
        # supers.faith relic-drop chance) is SUPERS content, not engine core.
        upgrade_legacy_container(item)
        if holder_type == "room":
            # Missing holder room (map rename / unload) -- game hook picks
            # the lost-item vault; bare engine falls back to start_room.
            from engine.hooks import orphan_item_room
            sink = game.rooms.get(holder_key) or orphan_item_room(game)
            if sink is not None:
                sink.add(item)
            # Catalog gear + pit potion on_use heal (same as inventory).
            from engine import hooks
            hooks.enrich_loaded_item(item)
        elif holder_type == "gear":
            # Job kit bag -- not surface inventory (supers/gear_bag).
            owner = game.find_character(holder_key)
            if owner:
                bag = getattr(owner, "gear_bag", None)
                if bag is None or not isinstance(bag, list):
                    owner.gear_bag = []
                    bag = owner.gear_bag
                bag.append(item)
                from engine import hooks
                hooks.enrich_loaded_item(item)
        else:
            owner = game.find_character(holder_key)
            if owner:               # owner should always exist; guard anyway
                owner.inventory.append(item)
                # Enrich from catalog when only catalog_id survived.
                from engine import hooks
                hooks.enrich_loaded_item(item)

    # After all inventory rows land, rebuild equipment maps from equipped flags.
    from engine.char_index import iter_characters
    from engine import hooks
    for char in iter_characters(game):
        hooks.rebind_character_equipment(char)

    # Section 6: relink each spirit's body/body_room object refs now that
    # every Item has been placed back into its room.
    for char, body_room_key, body_key in pending_body_links:
        resolve_pending_body_link(game, char, body_room_key, body_key)


def resolve_pending_body_link(game, char, body_room_key, body_key):
    """Relink one spirit's ``body`` / ``body_room`` object refs by key.

    Shared by the boot loader (after every Item is placed back into its
    room) and ``supers.fold_vault.restore_from_vault`` (a live world has
    everything already placed, so this can run immediately there instead
    of a second pass). Living vessel husks are Characters (not floor Item
    corpses) -- resolve them via ``find_character`` so a vessel-free
    Angel/Demon is not force-unspirited while their Jimmy-style husk keeps
    walking (dual-corporeal bug). If no body can be found at all
    (corrupted save / missing room / vaulted husk never restored), the
    safe fallback is still to un-spirit rather than leave them stuck with
    no way to self-anchor -- casual death staying non-permanent matters
    more here than strict fidelity to a broken save.
    """
    room = game.rooms.get(body_room_key) if body_room_key else None
    body = None
    if room is not None:
        for obj in room.contents:
            if isinstance(obj, Item) and obj.is_body and obj.key == body_key:
                body = obj
                break
    # Living husk Character: saved body_key is husk:Mantle (or a
    # renamed mortal face). Prefer exact key, then vessel_husk_key.
    finder = getattr(game, "find_character", None)
    if body is None and callable(finder):
        for key in (body_key, getattr(char, "vessel_husk_key", None)):
            if not key:
                continue
            candidate = finder(key)
            if candidate is None or candidate is char:
                continue
            # Accept designed living husks (is_vessel_husk) owned by
            # this Mantle, or any Character whose key matched the
            # saved body pointer (pre-flag save drift).
            owner = getattr(candidate, "vessel_owner_key", None)
            if getattr(candidate, "is_vessel_husk", False):
                if owner is None or owner == char.key:
                    body = candidate
                    break
            elif key == body_key:
                body = candidate
                break
    if body is not None:
        char.body = body
        # Prefer the husk's live room over a stale body_room_key --
        # vacant Cadence husks wander after vacate.
        live_room = getattr(body, "location", None)
        char.body_room = live_room if live_room is not None else room
    else:
        char.spirit = False
        char.spirit_state = None
        char.spirit_tether = 0.0
        char.spirit_untethered_ticks = 0
        # Drop vessel_free so boot heal / Cadence do not treat this
        # Mantle as a vessel spirit with a missing husk (hybrid state
        # that used to leave Castiel corporeal + Jimmy vacant).
        if hasattr(char, "vessel_free"):
            char.vessel_free = False
        # Re-derive HP after un-spiriting a character whose body was
        # lost -- the one spot in this module that used to reach into
        # supers.stats directly; now goes through engine.hooks so this
        # module has zero SUPERS imports (Phase 3 purity).
        recompute_hp(char)


# ---------------------------------------------------------------------------
# Hard gm fold vault (opaque zlib payloads; survives save_world wipe-rewrite)
# ---------------------------------------------------------------------------


def item_from_saved_container(key, description, container):
    """Rebuild one Item from a save-shaped container blob (dict or JSON str).

    Same field set as ``load_world``'s items loop -- vault restore and live
    load share one reconstruction path so gear / phones / lockboxes do not
    drift.
    """
    if isinstance(container, str):
        state = json.loads(container or "{}")
    else:
        state = dict(container or {})
    item = Item(
        key,
        description,
        locked=state.get("locked", False),
        loot=_loot_from_json(state.get("loot", [])),
        is_body=state.get("is_body", False),
        is_buried=state.get("is_buried", False),
        relic=state.get("relic", None),
        furniture=state.get("furniture", False),
    )
    if state.get("created_seq") is not None:
        try:
            item.created_seq = int(state["created_seq"])
        except (TypeError, ValueError):
            pass
    note_item_created_seq(getattr(item, "created_seq", 0))
    if state.get("owner_key"):
        item.owner_key = state["owner_key"]
    if state.get("need"):
        item.need = state["need"]
    if state.get("provides_light"):
        item.provides_light = True
    if state.get("catalog_id"):
        item.catalog_id = state["catalog_id"]
    aliases = state.get("aliases") or []
    if isinstance(aliases, list) and aliases:
        item.aliases = [str(a) for a in aliases if a]
    if state.get("slot"):
        item.slot = state["slot"]
    if isinstance(state.get("mods"), dict):
        item.mods = dict(state["mods"])
    if isinstance(state.get("materials"), list):
        item.materials = list(state["materials"])
    if state.get("color"):
        item.color = state["color"]
    if state.get("equipped"):
        item.equipped = True
    if state.get("layer"):
        item.layer = str(state["layer"]).strip().lower()
    if state.get("worn"):
        item.worn = True
    if state.get("worn_order") is not None:
        try:
            item.worn_order = int(state["worn_order"])
        except (TypeError, ValueError):
            pass
    if state.get("cloth_material"):
        item.cloth_material = str(state["cloth_material"]).strip().lower()
    if state.get("warmth"):
        item.warmth = str(state["warmth"]).strip().lower()
    if isinstance(state.get("cover"), list):
        item.cover = [str(c).strip().lower() for c in state["cover"] if c]
    if isinstance(state.get("conceal"), list):
        item.conceal = [
            str(c).strip().lower() for c in state["conceal"] if c
        ]
    if state.get("dirty") is not None:
        item.dirty = bool(state["dirty"])
    if state.get("stock_until_tick") is not None:
        try:
            item.stock_until_tick = int(state["stock_until_tick"])
        except (TypeError, ValueError):
            pass
    if state.get("blood_stock_until_tick") is not None:
        try:
            item.blood_stock_until_tick = int(state["blood_stock_until_tick"])
        except (TypeError, ValueError):
            pass
    if state.get("body_dropped_tick") is not None:
        try:
            item.body_dropped_tick = int(state["body_dropped_tick"])
        except (TypeError, ValueError):
            pass
    if state.get("floor_dropped_tick") is not None:
        try:
            item.floor_dropped_tick = int(state["floor_dropped_tick"])
        except (TypeError, ValueError):
            pass
    if state.get("vault_decay_at_tick") is not None:
        try:
            item.vault_decay_at_tick = int(state["vault_decay_at_tick"])
        except (TypeError, ValueError):
            pass
    if state.get("phone_number"):
        item.phone_number = str(state["phone_number"]).strip()
    if state.get("is_phone"):
        item.is_phone = True
    if state.get("is_payphone"):
        item.is_payphone = True
        item.furniture = True
    if state.get("is_ethereal"):
        item.is_ethereal = True
    _restore_pit_mimic_fields(item, state)
    _restore_on_use_fields(item, state)
    _restore_bag_fields(item, state)
    upgrade_legacy_container(item)
    return item


def snapshot_held_items(character):
    """Return inventory + gear_bag rows shaped for vault / items table.

    Each entry: ``{holder_type, key, description, container}`` where
    ``container`` is a JSON-ready dict (not a string).
    """
    rows = []
    for item in list(getattr(character, "inventory", None) or []):
        rows.append({
            "holder_type": "character",
            "key": item.key,
            "description": item.description,
            "container": json.loads(_item_container_blob(item)),
        })
    for item in list(getattr(character, "gear_bag", None) or []):
        rows.append({
            "holder_type": "gear",
            "key": item.key,
            "description": item.description,
            "container": json.loads(_item_container_blob(item)),
        })
    return rows


def vault_list(conn):
    """Yield ``(name, room_key, folded_at, folded_by)`` for every vaulted mortal."""
    rows = conn.execute(
        "SELECT name, room_key, folded_at, folded_by FROM character_vault "
        "ORDER BY lower(name)"
    ).fetchall()
    return list(rows)


def vault_has(conn, name):
    """True when ``name`` (exact key) has a vault row."""
    if not name:
        return False
    row = conn.execute(
        "SELECT 1 FROM character_vault WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    return row is not None


def vault_lookup_name(conn, name):
    """Return the stored vault ``name`` matching ``name`` case-insensitively."""
    if not name:
        return None
    row = conn.execute(
        "SELECT name FROM character_vault WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    return row[0] if row else None


def vault_put(conn, name, room_key, payload_bytes, *, folded_by=None,
              folded_at=None):
    """INSERT OR REPLACE a compressed vault payload for ``name``.

    Call this **before** despawning the live body so a crash mid-fold
    cannot wipe the pfile without a vault copy.
    """
    when = float(folded_at if folded_at is not None else time.time())
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO character_vault "
            "(name, room_key, folded_at, folded_by, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, room_key or "", when, folded_by, payload_bytes),
        )


def vault_get(conn, name):
    """Return ``(name, room_key, folded_at, folded_by, payload)`` or None."""
    stored = vault_lookup_name(conn, name)
    if not stored:
        return None
    row = conn.execute(
        "SELECT name, room_key, folded_at, folded_by, payload "
        "FROM character_vault WHERE name = ?",
        (stored,),
    ).fetchone()
    return row


def vault_delete(conn, name):
    """Remove a vault row (after successful restore). Idempotent."""
    stored = vault_lookup_name(conn, name)
    if not stored:
        return False
    with conn:
        conn.execute("DELETE FROM character_vault WHERE name = ?", (stored,))
    return True


def compress_vault_envelope(envelope):
    """zlib-compress a JSON-serializable vault envelope dict -> bytes."""
    raw = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
    return zlib.compress(raw.encode("utf-8"), level=9)


def decompress_vault_envelope(payload_bytes):
    """Inverse of ``compress_vault_envelope``."""
    raw = zlib.decompress(payload_bytes)
    return json.loads(raw.decode("utf-8"))
