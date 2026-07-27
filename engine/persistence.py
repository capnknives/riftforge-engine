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
  server.Game.on_tick (every AUTOSAVE_EVERY_TICKS) so the wipe+rewrite
  does not stall the asyncio loop every heartbeat.
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
    hub_room_key  TEXT
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


def connect(path):
    """Open (or create) the database at `path` and ensure the tables exist.

    `path` may also be ":memory:" -- SQLite's built-in throwaway mode, which
    the smoke test uses so test runs never touch a real file. WAL (crash
    safety + readers that don't block on a writer) only makes sense for a
    real file -- ":memory:" has no journal to speak of and rejects the
    pragma with an OperationalError.
    """
    conn = sqlite3.connect(path)
    if path != ":memory:":
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
    moral_last_casualty_tick, moral_scout_cooldown_until, holy_war_active
    (defaults when keys are missing).
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
        # Host/Infernal war global (gm holywar); 0=off 1=on. Default off.
        "holy_war_active": bool(_int_meta("holy_war_active", 0)),
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
        # Holy war (Host vs Infernal) -- survives restart; default off.
        holy = 1 if bool(getattr(game, "holy_war_active", False)) else 0
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('holy_war_active', ?)",
            (str(holy),),
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


def _save_meta_dict(conn, key, game, attr):
    """Persist ``game.<attr>`` (a dict) into ``meta[key]``.

    Non-dict / missing attributes coerce to ``{}`` so a corrupt in-memory
    blob cannot write junk JSON that later boot would have to heal.
    """
    blob = getattr(game, attr, None)
    if not isinstance(blob, dict):
        blob = {}
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, json.dumps(blob)),
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


# Cap matches Game.ooc_history maxlen / bare-`ooc` replay (last 20 lines).
OOC_HISTORY_MAXLEN = 20


def load_ooc_history(conn):
    """Load the global OOC ring buffer from meta (default empty list).

    Bare ``ooc`` replays these plain ``((OOC)) [Name]: …`` lines. Missing
    or malformed key → ``[]`` so a fresh world / pre-feature save boots
    cleanly. Only string entries are kept; the list is truncated to the
    last ``OOC_HISTORY_MAXLEN`` lines (oldest dropped first).
    """
    import json

    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", ("ooc_history",)
    ).fetchone()
    if not row or not row[0]:
        return []
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    # Keep only plain strings; drop junk from a hand-edited meta row.
    lines = [line for line in data if isinstance(line, str)]
    if len(lines) > OOC_HISTORY_MAXLEN:
        lines = lines[-OOC_HISTORY_MAXLEN:]
    return lines


def save_ooc_history(conn, game):
    """Persist ``game.ooc_history`` so copyover / restart keep recent OOC.

    Copyover (classic execv and gateway exit) always calls ``game.save()``
    first, so the ring buffer survives the process swap the same way
    death_beacons / author_mantle_event do. Still a short ring — not a
    forever chat log.
    """
    import json

    history = getattr(game, "ooc_history", None) or []
    # deque and list both iterate in oldest→newest order.
    lines = [line for line in history if isinstance(line, str)]
    if len(lines) > OOC_HISTORY_MAXLEN:
        lines = lines[-OOC_HISTORY_MAXLEN:]
    payload = json.dumps(lines, separators=(",", ":"))
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES "
            "('ooc_history', ?)",
            (payload,),
        )


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
        # Physical phone line id (supers/phone.py); absent = not a phone.
        "phone_number": getattr(item, "phone_number", None),
        "is_phone": bool(getattr(item, "is_phone", False)),
        "is_payphone": bool(getattr(item, "is_payphone", False)),
        "is_ethereal": bool(getattr(item, "is_ethereal", False)),
    })


def save_world(conn, game):
    """Write a full snapshot of the live world into the database.

    Characters come from ``game.characters`` (engine/char_index) so we do
    not walk ~12k empty wilderness cells every save. Loose room Items still
    need one room pass -- there is no item index yet, and item counts stay
    small. Runs inside one transaction so a crash mid-save can never leave
    the file half-written -- SQLite rolls it back.
    """
    from engine.char_index import iter_characters

    with conn:
        # Wipe and rewrite: the snapshot approach described in the module docstring.
        conn.execute("DELETE FROM characters")
        conn.execute("DELETE FROM items")
        seen_names = set()
        for obj in iter_characters(game):
            room = getattr(obj, "location", None)
            if room is None:
                continue
            # Wilderness hostiles (is_npc, not spar_only, not
            # peaceful) are deliberately ephemeral -- never
            # persisted, so a restart clears whatever happens to
            # be out. Peaceful townsfolk are lethal-capable under
            # the afterlife stub (spar_only False) but MUST still
            # persist. Tutorial mentors are re-seeded each boot and
            # may exist in multiple rooms under the same key, so
            # they stay out of the characters table. Shared hostile
            # keys ("a feral wastes-lurker") would also collide on
            # `name TEXT PRIMARY KEY` if two existed at once.
            if getattr(obj, "tutorial_mentor_for", None):
                continue
            # Guild / Motel / plane hub spawns -- reboot clears them.
            if getattr(obj, "transient_soul", False):
                continue
            # Ephemeral GM staff spirits (gm on) -- never persist. Intent
            # lives on the login body as gm_staff_form in the blob.
            key_low = (getattr(obj, "key", None) or "").lower()
            if (
                getattr(obj, "gm_spirit", False)
                or getattr(obj, "gm_mode", False)
                or key_low.startswith("gmspirit:")
            ):
                continue
            if (
                obj.is_npc
                and not obj.spar_only
                and not getattr(obj, "peaceful", False)
            ):
                continue
            # Duplicate live keys (e.g. Mantle + peel-bug husk both named
            # Crowley) must not UNIQUE-crash the whole save / boot.
            save_name = getattr(obj, "key", None) or ""
            if save_name in seen_names:
                print(
                    f"[persistence] skip duplicate character key "
                    f"{save_name!r} in {getattr(room, 'key', '?')}",
                    flush=True,
                )
                continue
            seen_names.add(save_name)
            # Snapshot live spirit watch-room onto the body before blobbing
            # so copyover / autosave restore staff where they were watching,
            # not over wherever Cadence walked the Echo.
            if getattr(obj, "gm_staff_form", False):
                spirit_key = getattr(obj, "gm_spirit_key", None) or (
                    f"gmspirit:{obj.key}"
                )
                finder = getattr(game, "find_character", None)
                spirit = finder(spirit_key) if callable(finder) else None
                spirit_room = getattr(spirit, "location", None) if spirit else None
                if spirit_room is not None and getattr(spirit_room, "key", None):
                    obj.gm_spirit_room_key = spirit_room.key
            # The whole stat spine (plus every other SUPERS-composed
            # field) rides in one JSON blob. A blob (vs a column per
            # stat) means adding a stat never needs a schema
            # migration -- old saves just lack the key and get
            # defaults. character_to_blob (supers/persist_blob.py)
            # is what actually knows the field list -- this module
            # only knows it's "the opaque character extras dict".
            blob = json.dumps(character_to_blob(obj))
            # Jinn mirage pockets are runtime-only. Persist the captive /
            # tormenting Jinn as if already awake in the real world so a
            # restart force-releases (docs/plans/jinn_path.md).
            save_room = room
            if getattr(obj, "jinn_captive", False):
                real = getattr(obj, "jinn_real_room", None)
                real_key = getattr(obj, "jinn_real_room_key", None)
                if real is not None:
                    save_room = real
                elif real_key and real_key in game.rooms:
                    save_room = game.rooms[real_key]
            elif getattr(room, "jinn_instance_id", None):
                ret = getattr(obj, "jinn_mirage_return_room", None)
                if ret is not None:
                    save_room = ret
            conn.execute(
                "INSERT INTO characters (name, description, room_key, stats) "
                "VALUES (?, ?, ?, ?)",
                # The ? placeholders are sqlite3's safe way to pass values.
                (obj.key, obj.description, save_room.key, blob),
            )
            for item in obj.inventory:
                conn.execute(
                    "INSERT INTO items "
                    "(key, description, holder_type, holder_key, container) "
                    "VALUES (?, ?, 'character', ?, ?)",
                    (item.key, item.description, obj.key,
                     _item_container_blob(item)),
                )
            # Job gear bag (supers/gear_bag) -- same Item rows, distinct
            # holder_type so load puts them back in gear_bag not inventory.
            for item in list(getattr(obj, "gear_bag", None) or []):
                conn.execute(
                    "INSERT INTO items "
                    "(key, description, holder_type, holder_key, container) "
                    "VALUES (?, ?, 'gear', ?, ?)",
                    (item.key, item.description, obj.key,
                     _item_container_blob(item)),
                )
        # Loose items on the floor -- one O(rooms) pass; skip empty cells
        # so the 100x100 Wastes does not dominate autosave cost.
        for room in game.rooms.values():
            if not room.contents:
                continue
            for obj in room.contents:
                if isinstance(obj, Item):
                    # Living Jinn husks are runtime props -- never persist
                    # orphan "sleeping form" corpses across reboot.
                    if getattr(obj, "jinn_husk", False):
                        continue
                    # Nest / hub spawn husks (decay-stamped) -- Characters
                    # already skip save for transient_soul; without this,
                    # orphan "the body of Nico …" piles survive reboot.
                    if getattr(obj, "ephemeral_spawn_body", False):
                        continue
                    if getattr(obj, "decay_at_tick", None) is not None:
                        continue
                    conn.execute(
                        "INSERT INTO items "
                        "(key, description, holder_type, holder_key, container) "
                        "VALUES (?, ?, 'room', ?, ?)",
                        (obj.key, obj.description, room.key,
                         _item_container_blob(obj)),
                    )


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
        # Older saves may still have zombie gmspirit: rows; never rebuild them.
        if (name or "").lower().startswith("gmspirit:"):
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
    if state.get("phone_number"):
        item.phone_number = str(state["phone_number"]).strip()
    if state.get("is_phone"):
        item.is_phone = True
    if state.get("is_payphone"):
        item.is_payphone = True
        item.furniture = True
    if state.get("is_ethereal"):
        item.is_ethereal = True
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
