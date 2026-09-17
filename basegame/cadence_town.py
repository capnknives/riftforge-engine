"""cadence_town.py -- idempotent Notbigville townsfolk for basegame Cadence demo.

Seeds folklore mortals (no Winchester / Castiel names) with hunger/thirst
meters so ``plan_tick`` can walk them to resource rooms. They appear on
``Room.Occupants`` as ``kind=npc`` because ``is_npc=True`` and they carry
no ``password_hash``.
"""

from __future__ import annotations

import json
import os

from world import Character

_CONTENT_DIR = os.path.join(os.path.dirname(__file__), "content")
_NPCS_PATH = os.path.join(_CONTENT_DIR, "npcs", "notbigville.json")


def _load_townsfolk_catalog():
    """Load the static townsfolk roster JSON (empty list when missing)."""
    try:
        with open(_NPCS_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError:
        return []
    npcs = data.get("npcs")
    return list(npcs) if isinstance(npcs, list) else []


def ensure_townsfolk(game):
    """Idempotent: spawn every catalog townsfolk at their home room.

    Returns the list of Character keys seeded this boot (including ones
    that already existed). Stores ``game._basegame_townsfolk_keys`` for the
    tick driver.
    """
    from basegame import needs as needs_mod

    rooms = getattr(game, "rooms", None) or {}
    catalog = _load_townsfolk_catalog()
    keys = []
    for row in catalog:
        if not isinstance(row, dict):
            continue
        key = (row.get("key") or "").strip()
        if not key:
            continue
        keys.append(key)
        for obj in list(getattr(game, "characters", ()) or ()):
            if getattr(obj, "key", None) == key:
                break
        else:
            given = (row.get("given_name") or key).strip()
            surname = (row.get("surname") or "").strip()
            desc = (row.get("description") or "A local resident.").strip()
            npc = Character(key, desc)
            npc.is_npc = True
            npc.given_name = given
            npc.surname = surname
            needs_mod.attach_character(npc)
            from engine.room_vnum import lookup_room

            home_key = (row.get("home_room") or "").strip()
            home = lookup_room(game, home_key) if home_key else None
            if home is not None:
                npc.move_to(home)
            elif rooms:
                npc.move_to(next(iter(rooms.values())))
    game._basegame_townsfolk_keys = tuple(keys)
    return keys
