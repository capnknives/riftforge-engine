"""personas.py -- basegame persona_registry demo (H7c).

Registers a tiny traits catalog and seeds the Post Office Operator NPC
with the ``chatty`` trait so ``greet`` can surface flavor lines.
"""

from __future__ import annotations

import os
import random

from engine import hooks
from engine.systems import persona_registry as persona_registry_mod
from world import Character, Item

_OPERATOR_KEY = "Operator"
_CONTENT_DIR = os.path.join(os.path.dirname(__file__), "content")


def register_persona_hooks():
    """Point persona_registry at basegame/content/personas.json and load."""
    personas_path = os.path.join(_CONTENT_DIR, "personas.json")
    hooks.set_persona_content_path(lambda: personas_path)
    persona_registry_mod.reload()


def flavor_line(npc, event, *, rng=random):
    """Pick a trait-colored flavor line for ``npc`` and ``event``, or None."""
    persona_registry_mod._ensure_loaded()
    lines = persona_registry_mod._LINES
    speaker = getattr(npc, "key", "?")
    traits = tuple(getattr(npc, "traits", None) or ())
    pool = []
    for trait in traits:
        tagged = f"{event}_{trait}"
        if tagged in lines:
            pool.extend(lines[tagged])
    if not pool:
        pool = list(lines.get(event) or [])
    if not pool:
        return None
    return rng.choice(pool).format(name=speaker)


def ensure_demo_npc(game):
    """Idempotent: seed the Post Office Operator with a desk phone + chatty."""
    post = game.rooms.get("NB00006")
    if post is None:
        return None
    for obj in list(getattr(game, "characters", ()) or ()):
        if getattr(obj, "key", None) == _OPERATOR_KEY:
            return obj

    from engine.systems import phone as phone_mod

    operator = Character(
        _OPERATOR_KEY,
        "The post-office clerk keeps one eye on the cubbyholes.",
    )
    operator.is_npc = True
    persona_registry_mod.add_character_trait(operator, "chatty")
    handset = Item(
        "a desk phone",
        "A battered rotary at the post-office counter.",
    )
    handset.is_phone = True
    phone_mod.stamp_phone_on_spawn(handset, game)
    operator.inventory.append(handset)
    operator.move_to(post)
    game._basegame_operator_number = handset.phone_number
    return operator


def operator_phone_number(game):
    """Return the seeded Operator handset number (after ensure_demo_npc)."""
    return getattr(game, "_basegame_operator_number", None)
