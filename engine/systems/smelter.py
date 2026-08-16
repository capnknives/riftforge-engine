"""
smelter.py -- smelt/forge station checks and hook delegation.

Craft math and catalog item creation register through hooks so the engine
never imports a game package.
"""

from __future__ import annotations

from engine import hooks

_smelt_handler = None
_forge_handler = None
_alloy_handler = None


def set_alloy_handler(fn):
    """Register fn(character, game, recipe_id) -> (ok, msg, item)."""
    global _alloy_handler
    _alloy_handler = fn


def alloy_at_station(character, game, recipe_id):
    """Delegate ingot/ore alloy recipes to registered handler."""
    if _alloy_handler is None:
        return False, "Alloying is not available here.", None
    return _alloy_handler(character, game, recipe_id)


def set_smelt_handler(fn):
    """Register fn(character, game, ore_item, smelt_tier) -> (ok, msg, item)."""
    global _smelt_handler
    _smelt_handler = fn


def set_forge_handler(fn):
    """Register fn(character, game, recipe_id, ingot) -> (ok, msg, item)."""
    global _forge_handler
    _forge_handler = fn


def room_has_smelter(room):
    """True when room has an installed smelter or town foundry amenity."""
    if room is None:
        return False
    if getattr(room, "foundry_amenity", False):
        return True
    for fix in getattr(room, "mine_fixtures", None) or []:
        if isinstance(fix, dict) and fix.get("kind") == "smelter":
            return True
    node_fix = _mine_node_fixtures(room)
    for fix in node_fix:
        if fix.get("kind") == "smelter":
            return True
    return False


def _mine_node_fixtures(room):
    mouth_key = getattr(room, "mouth_key", None)
    room_id = getattr(room, "mine_room_id", None)
    game = getattr(room, "game", None)
    if not mouth_key or not room_id or game is None:
        return []
    from engine.systems import mine_graph as graph_mod
    mouth = graph_mod.get_mouth(game, mouth_key)
    node = graph_mod.room_by_id(mouth, room_id) if mouth else None
    return (node or {}).get("fixtures") or []


def smelt_ore(character, game, ore_item, *, smelt_tier=1):
    """Delegate ore -> ingot to registered handler."""
    if _smelt_handler is None:
        return False, "Smelting is not available here.", None
    return _smelt_handler(character, game, ore_item, smelt_tier)


def forge_at_station(character, game, recipe_id, ingot):
    """Delegate ingot -> gear to registered handler."""
    if _forge_handler is None:
        return False, "Forging is not available here.", None
    return _forge_handler(character, game, recipe_id, ingot)
