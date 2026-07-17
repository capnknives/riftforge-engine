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
from engine.world import Character, Item


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
    holder_type TEXT NOT NULL CHECK (holder_type IN ('room', 'character')),
    holder_key  TEXT NOT NULL,             -- room key or character name
    container   TEXT NOT NULL DEFAULT '{}' -- JSON: {"locked": bool, "loot": [...]}
);
CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,          -- tiny key/value store for flags
    value       TEXT NOT NULL
);
"""


def connect(path):
    """Open (or create) the database at `path` and ensure the tables exist.

    `path` may also be ":memory:" -- SQLite's built-in throwaway mode, which
    the smoke test uses so test runs never touch a real file.
    """
    conn = sqlite3.connect(path)
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
]


def _schema_version(conn):
    """The database's current migration level (0 for a database that
    predates schema_version entirely -- every _MIGRATIONS entry runs)."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row[0]) if row else 0


def _migrate(conn):
    """Bring the database up to the latest schema by applying every
    migration newer than its recorded schema_version, in order, and
    recording the new version after each -- so a boot that dies partway
    through resumes from the last completed migration instead of redoing
    (or skipping) one. Runs on every boot; a database already at the latest
    version does nothing.

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
        for version, sql in _MIGRATIONS:
            if version <= current:
                continue
            try:
                conn.execute(sql)
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
    moral_last_casualty_tick, moral_scout_cooldown_until (defaults when
    keys are missing).
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
        # Home grocery stock window (fridge furniture); None / absent = empty.
        "stock_until_tick": getattr(item, "stock_until_tick", None),
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
            if (
                obj.is_npc
                and not obj.spar_only
                and not getattr(obj, "peaceful", False)
            ):
                continue
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
        # .get(room_key, ...) falls back to the start room if the map changed
        # and the saved room no longer exists -- better than crashing on boot.
        room = game.rooms.get(room_key, game.start_room)
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
        # Fridge pantry timer (home grocery stock); absent on older saves.
        if state.get("stock_until_tick") is not None:
            try:
                item.stock_until_tick = int(state["stock_until_tick"])
            except (TypeError, ValueError):
                pass
        # bug_reports.log #21: strongboxes saved before the lockbox pass (or
        # with the default '{}' container blob) reload as flavor-only Items;
        # promote them here so `open strongbox` still pays out after a
        # reboot. Goes through engine.hooks -- the reward math (and its
        # supers.faith relic-drop chance) is SUPERS content, not engine core.
        upgrade_legacy_container(item)
        if holder_type == "room":
            game.rooms.get(holder_key, game.start_room).add(item)
        else:
            owner = game.find_character(holder_key)
            if owner:               # owner should always exist; guard anyway
                owner.inventory.append(item)

    # Section 6: relink each spirit's body/body_room object refs now that
    # every Item has been placed back into its room. If the body can't be
    # found (a corrupted save, or the room it sat in no longer exists), the
    # safe fallback is to un-spirit the character rather than leave them
    # permanently stuck with no way to ever self-anchor -- casual death
    # staying non-permanent (section 6) matters more here than strict
    # fidelity to a broken save.
    for char, body_room_key, body_key in pending_body_links:
        room = game.rooms.get(body_room_key) if body_room_key else None
        body = None
        if room is not None:
            for obj in room.contents:
                if isinstance(obj, Item) and obj.is_body and obj.key == body_key:
                    body = obj
                    break
        if body is not None:
            char.body = body
            char.body_room = room
        else:
            char.spirit = False
            char.spirit_state = None
            char.spirit_tether = 0.0
            char.spirit_untethered_ticks = 0
            # Re-derive HP after un-spiriting a character whose body was
            # lost -- the one spot in this module that used to reach into
            # supers.stats directly; now goes through engine.hooks so this
            # module has zero SUPERS imports (Phase 3 purity).
            recompute_hp(char)
