"""
items.py -- thin basegame item catalog for engine fishing / shop demos.

Loads ``basegame/content/items.json`` and exposes ``get`` + ``make_world_item``
for ``engine.hooks.set_item_catalog_get`` / ``set_make_world_item``.
"""

from __future__ import annotations

import json
import os

_CONTENT_PATH = os.path.join(os.path.dirname(__file__), "content", "items.json")
_ITEMS = None


def _load():
    """Load the JSON catalog once per process."""
    global _ITEMS
    if _ITEMS is not None:
        return _ITEMS
    with open(_CONTENT_PATH, encoding="utf-8") as handle:
        _ITEMS = json.load(handle)
    return _ITEMS


def clear_cache():
    """Drop cached catalog (tests / copyover)."""
    global _ITEMS
    _ITEMS = None


def get(item_id):
    """Return one catalog row dict, or None when the id is unknown."""
    return _load().get(item_id)


def make_world_item(data, where="item ref"):
    """Build a world.Item from a catalog ref ``{"item": "<id>"}``."""
    from world import Item

    if not isinstance(data, dict):
        return None
    item_id = data.get("item")
    if not item_id:
        return None
    spec = get(item_id)
    if not spec:
        return None
    item = Item(
        spec["key"],
        spec.get("description", "You see nothing special."),
        locked=bool(spec.get("locked", False)),
        furniture=bool(spec.get("furniture", False)),
    )
    aliases = spec.get("aliases")
    if isinstance(aliases, list) and aliases:
        item.aliases = [str(a).strip() for a in aliases if str(a).strip()]
    item.catalog_id = item_id
    raw_price = spec.get("price", spec.get("shop_price"))
    if raw_price is not None:
        try:
            price = int(raw_price)
        except (TypeError, ValueError):
            price = 0
        if price > 0:
            item.price = price
    return item
