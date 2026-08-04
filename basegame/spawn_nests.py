"""
spawn_nests.py -- critter dens for the basegame spawn peel demo.

One nest kind (``critter``) tops up from ``content/nests.json`` via the
generic nest-AI hook in ``engine/systems/spawn/nest_ai.py``. No SUPERS
wilderness / vampire / corpse-eater paths -- just prairie critters from
``basegame/bestiary.py``.
"""

from __future__ import annotations

import json
import os
import random

from engine.systems import spawn as spawn_engine
from world import Character

_NESTS_PATH = os.path.join(os.path.dirname(__file__), "content", "nests.json")
_CATALOG = None


def _load_catalog():
    """Load nests.json once (fail loud on bad schema)."""
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG
    with open(_NESTS_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    nests = data.get("nests")
    if not isinstance(nests, dict) or not nests:
        raise AssertionError("nests.json: nests must be a non-empty object")
    known_ai = spawn_engine.known_nest_ai()
    for key, spec in nests.items():
        if not isinstance(spec, dict):
            raise AssertionError(f"nests.json: nests.{key} must be an object")
        if "cap_per_nest" not in spec:
            raise AssertionError(f"nests.json: nests.{key} needs cap_per_nest")
        ai = spec.get("ai", "hostile")
        if ai not in known_ai:
            raise AssertionError(
                f"nests.json: nests.{key}.ai {ai!r} has no registered nest AI"
            )
        if not spec.get("bestiary_categories"):
            raise AssertionError(
                f"nests.json: nests.{key} needs bestiary_categories"
            )
    _CATALOG = nests
    return _CATALOG


def nest_type_for_room(room):
    """Return nest catalog key for this room, or None."""
    if room is None:
        return None
    explicit = getattr(room, "spawn_nest", None)
    if explicit:
        key = str(explicit).strip().lower()
        return key or None
    return None


def list_nest_rooms(game):
    """Every room with a resolved nest type."""
    rooms = getattr(game, "rooms", None) or {}
    n = len(rooms)
    cache = getattr(game, "_nest_rooms_cache", None)
    if cache is not None and cache[0] == n:
        return list(cache[1])
    out = []
    for room in rooms.values():
        kind = nest_type_for_room(room)
        if kind:
            out.append((room, kind))
    game._nest_rooms_cache = (n, list(out))
    return out


def _is_live_nest_spawn(obj):
    """True for a fightable ephemeral nest hostile still in the world."""
    if obj is None:
        return False
    if not getattr(obj, "_spawn_nest", None):
        return False
    if not getattr(obj, "is_npc", False):
        return False
    if float(getattr(obj, "hp", 0) or 0) <= 0:
        return False
    if getattr(obj, "location", None) is None:
        return False
    return True


def _nest_occupants(room):
    """Ephemeral nest spawns currently standing in this den room."""
    found = []
    for obj in list(getattr(room, "contents", []) or []):
        if _is_live_nest_spawn(obj):
            found.append(obj)
    return found


def _hostile_from_creature(creature, tier):
    """Build (but don't place) one critter NPC from a bestiary template."""
    from basegame import bestiary as bestiary_mod
    from basegame import stats as stats_module

    hostile = Character(creature["name"], creature["description"])
    hostile.stats = bestiary_mod.roll_stats(creature)
    hostile.tier = tier
    hostile.is_npc = True
    hostile.hp = stats_module.max_hp(hostile)
    creature_id = creature.get("id")
    if creature_id:
        hostile.creature_id = str(creature_id)
    category = creature.get("category")
    if category:
        hostile.bestiary_category = str(category).strip().lower()
    return hostile


def _make_critter_nest_hostile(game, room, spec):
    """Pick a prairie critter from the room/catalog pool and build an NPC."""
    room_cats = [
        str(c).strip().lower()
        for c in (getattr(room, "bestiary_categories", None) or [])
        if str(c).strip()
    ]
    cats = room_cats or list(spec.get("bestiary_categories") or [])
    from basegame import bestiary as bestiary_mod

    pool = bestiary_mod.get_pool(cats, 0, room=room)
    if not pool:
        return None
    creature = random.choice(pool)
    return _hostile_from_creature(creature, 0)


spawn_engine.register_nest_ai("critter", _make_critter_nest_hostile)


def _spawn_hostile(game, room, spec):
    """Build and place one nest hostile. Returns the Character or None."""
    ai = spec.get("ai", "critter")
    hostile = spawn_engine.make_nest_hostile(ai, game, room, spec)
    if hostile is None:
        return None
    hostile._spawn_nest = nest_type_for_room(room) or "unknown"
    hostile._nest_spawned_at_tick = int(
        getattr(game, "game_time_ticks", 0) or 0
    )
    hostile.home_zone = getattr(room, "zone", None)
    hostile.home_room_key = getattr(room, "key", None)
    if hasattr(hostile, "move_to"):
        hostile.move_to(room)
    else:
        room.add(hostile)
        hostile.location = room
    room.broadcast(
        f"{hostile.key} stirs in the shed.",
        blank_after=True,
    )
    return hostile


def tick_nests(game):
    """Top up live dens under cap (registered on basegame tick_bootstrap)."""
    catalog = _load_catalog()
    for room, kind in list_nest_rooms(game):
        spec = catalog.get(kind)
        if spec is None:
            continue
        cap = int(spec.get("cap_per_nest", 2) or 2)
        if len(_nest_occupants(room)) >= cap:
            continue
        chance = float(spec.get("spawn_chance_per_tick", 0.03) or 0.0)
        if chance <= 0 or random.random() >= chance:
            continue
        _spawn_hostile(game, room, spec)
