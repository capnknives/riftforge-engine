"""
world.py -- thin re-export facade (two-repo purity Phase 3:
docs/plans/two_repo_purity.md).

The lean, game-agnostic world core -- GameObject, Room, Item, Character,
make_body, break_follows -- now lives in engine/world.py, with zero SUPERS
imports, so a bare engine install boots with nothing here. The SUPERS-only
spawn/encounter game content (wilderness hostiles, procedural dungeons,
lockboxes, the training dummy) lives in supers/world_ext.py instead.

This module exists purely so every existing `from world import X` /
`import world; world.X` callsite across the codebase keeps working
unchanged -- nobody outside this file, engine/world.py, and
supers/world_ext.py needs to know the split happened. build_world() stays
here (not engine/world.py) because it reaches into maps.py, which is
SUPERS *content* (content/maps/*.json) even though the maps.py module
itself is generic in shape -- see AGENTS.md's "Where things live".

The SUPERS names below are re-exported LAZILY, via the module-level
`__getattr__` at the bottom (PEP 562) -- NOT a plain `from
supers.world_ext import ...` at the top. That distinction matters: engine/
connection.py (and engine/verbs/basic.py, and this test itself:
smoke_test.py's engine_hooks_purity_tests subprocess) does `from world
import Character` while `supers` is completely uninstalled/blocked --
`import world` itself, and every ENGINE-provided name on it (Character,
Item, Room, GameObject, make_body, break_follows), must keep working with
no SUPERS on the path at all. Only actually touching a SUPERS-only name
(make_wilderness_hostile, DUNGEON_ENCOUNTER_CHANCE, ...) needs SUPERS
installed, and only at the moment you touch it.
"""

from engine.world import (
    GameObject,
    Room,
    Item,
    Character,
    make_body,
    break_follows,
)

# Every public (and the few underscore-prefixed but externally-referenced --
# see their own modules) name supers/world_ext.py defines. Listed explicitly
# (not discovered via dir()) so a typo/omission here fails loud the first
# time something tries to reach it, rather than silently returning nothing.
_SUPERS_WORLD_EXT_NAMES = frozenset((
    "WILDERNESS_ENCOUNTER_CHANCE",
    "DUNGEON_ENCOUNTER_CHANCE",
    "LOCKBOX_GROWTH_MIN",
    "LOCKBOX_GROWTH_MAX",
    "STRONGBOX_KEY",
    "_DUNGEON_LAYOUT",
    "_DUNGEON_LINK_DIRECTIONS",
    "_OPPOSITE_DIRECTION",
    "_hostile_from_creature",
    "make_training_dummy",
    "make_wilderness_hostile",
    "check_wilderness_encounter",
    "make_lockbox",
    "is_legacy_strongbox",
    "upgrade_legacy_strongbox",
    "make_procedural_dungeon",
    "check_dungeon_encounter",
    "check_hostile_aggro",
    "encounter_check",
))


def __getattr__(name):
    """PEP 562 module `__getattr__`: only import supers.world_ext (and pay
    SUPERS' import cost) the moment something actually asks for one of its
    names, never just from `import world`/`from world import Character`.

    This is also why `world.WILDERNESS_ENCOUNTER_CHANCE = X` (GM
    `setdungeonchance`, smoke_test.py) must never be used to retune the
    live spawn odds -- every access here reads straight through to
    world_ext's OWN module global (no copy lives in this module's
    namespace), but an ASSIGNMENT through this facade would only ever
    create/overwrite a plain attribute on `world` itself, never touching
    world_ext's global. Mutate `world_ext.X` directly instead -- see
    supers/world_ext.py's module docstring.
    """
    if name in _SUPERS_WORLD_EXT_NAMES:
        from supers import world_ext
        return getattr(world_ext, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_world():
    """Create the starter map and return (rooms, start_room, seed_items).

    - rooms: a dict of every Room keyed by its name, so persistence can turn a
      saved room name back into the actual Room object.
    - start_room: where new characters spawn.
    - seed_items: (item, room_key) pairs the caller should place ONLY on the
      very first boot. We don't place them here because on every later boot
      the database knows where items really are (someone may have picked the
      sword up), and re-placing it would duplicate it.

    The map itself lives as DATA now, not code: every content/maps/*.json
    file describes one map (a procedural grid, some hand-authored rooms, or
    both), and maps.load_all_maps() reads all of them and wires their exits
    together -- including exits that cross from one file's rooms into
    another's, which is how a second plane (e.g. content/maps/
    cinder_reach.json) can be reached from the Wastes' Central Plaza.
    See maps.py's module docstring for the full schema and rationale.

    Imported here, not at module level, to dodge a circular import --
    maps.py imports Room/Item FROM this module (well, from engine.world by
    way of this facade), so this module can't import maps.py back until
    this function actually runs (same trick Character.__init__ already
    uses for stats.py/training.py).

    The training dummy (milestone 5b) is NOT seed content -- see
    make_training_dummy()'s docstring for why.
    """
    import maps as maps_module
    return maps_module.load_all_maps()
