"""
material_instances.py -- typed ore/ingot instance fields on Items.

Catalog rows declare ``material_id`` defaults; instances carry purity,
contamination, and depth_band at harvest/smelt time. Stacks group by
(material_id, purity band) via stack_key.
"""

from __future__ import annotations

PURITY_BAND_SIZE = 10


def purity_band(purity):
    """Bucket purity 0-100 into stack bands."""
    try:
        p = int(purity)
    except (TypeError, ValueError):
        p = 50
    p = max(0, min(100, p))
    return (p // PURITY_BAND_SIZE) * PURITY_BAND_SIZE


def ore_stack_key(material_id, purity):
    """stack_key for typed ore stacks."""
    return f"ore:{material_id}:{purity_band(purity)}"


def stamp_ore_instance(item, *, material_id, purity=50, contamination=0, depth_band=0):
    """Stamp instance fields on a catalog ore Item."""
    item.material_id = str(material_id)
    item.purity = max(0, min(100, int(purity)))
    item.contamination = max(0, min(100, int(contamination)))
    item.depth_band = max(0, int(depth_band))
    item.stack_key = ore_stack_key(item.material_id, item.purity)
    return item


def stamp_ingot_instance(item, *, material_id, purity=80, folklore_tags=None):
    """Stamp instance fields on a smelted ingot."""
    item.material_id = str(material_id)
    item.purity = max(0, min(100, int(purity)))
    item.folklore_tags = list(folklore_tags or [])
    item.stack_key = f"ingot:{material_id}:{purity_band(item.purity)}"
    return item


def instance_weight(item, default=1.0):
    """Per-item weight from catalog + instance."""
    try:
        base = float(getattr(item, "weight", None) or default)
    except (TypeError, ValueError):
        base = default
    return base


def carried_weight(character):
    """Sum weight of open inventory + worn bags."""
    from engine.systems import containers as containers_mod
    from engine import hooks

    total = 0.0
    for item in containers_mod.iter_carried_items(character):
        total += instance_weight(item)
    return total


def can_carry_weight(character, extra_weight):
    """True when adding extra_weight stays under cap."""
    from engine import hooks

    cap = hooks.mine_carry_weight_cap(character)
    if cap is None:
        return True
    try:
        cap_f = float(cap)
    except (TypeError, ValueError):
        return True
    return carried_weight(character) + float(extra_weight) <= cap_f


def refuse_carry_message():
    from engine.systems.containers import WEIGHT_CAPACITY_FULL
    return WEIGHT_CAPACITY_FULL


def find_carried_item(character, needle):
    """Find first inventory item matching needle substring in key."""
    needle = (needle or "").strip().lower()
    for item in getattr(character, "inventory", None) or []:
        key = (getattr(item, "key", "") or "").lower()
        if not needle or needle in key:
            return item
    return None


def iter_carried_items(character):
    """Yield open inventory items."""
    yield from getattr(character, "inventory", None) or []


def remove_from_inventory(character, item):
    inv = getattr(character, "inventory", None) or []
    if item in inv:
        inv.remove(item)
        return True
    return False


def try_give_item(character, item):
    """Append item if carry weight allows."""
    w = instance_weight(item)
    if not can_carry_weight(character, w):
        return False, refuse_carry_message()
    character.inventory.append(item)
    return True, ""
