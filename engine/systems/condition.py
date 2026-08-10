"""condition.py -- generic item durability (HP + break-on-zero).

Mirrors the *tier vocabulary* from ``engine/systems/body_parts.py`` at a
much smaller scale: one ``current_hp`` / ``max_hp`` pair per Item, plus a
``broken`` flag when HP hits zero. No anatomical regions -- just "this
object can wear out."

Games attach condition via ``ensure_condition(item)`` before applying damage.
Items without condition fields behave as always-intact (backward compatible).

stdlib only.
"""

from __future__ import annotations

from engine.systems.body_parts import (
    TIER_BRUISED,
    TIER_DISABLED,
    TIER_HEALTHY,
    TIER_WOUNDED,
    tier_for_ratio,
)

DEFAULT_ITEM_MAX_HP = 10


def ensure_condition(item):
    """Return ``item.condition`` dict, seeding defaults when absent.

    Shape: ``{"current_hp": int, "max_hp": int, "broken": bool}``.
    """
    if item is None:
        return {
            "current_hp": DEFAULT_ITEM_MAX_HP,
            "max_hp": DEFAULT_ITEM_MAX_HP,
            "broken": False,
        }
    cond = getattr(item, "condition", None)
    if not isinstance(cond, dict):
        cond = {}
        item.condition = cond
    max_hp = cond.get("max_hp")
    if max_hp is None:
        max_hp = getattr(item, "max_hp", None)
    if max_hp is None:
        max_hp = DEFAULT_ITEM_MAX_HP
    try:
        max_hp = max(1, int(max_hp))
    except (TypeError, ValueError):
        max_hp = DEFAULT_ITEM_MAX_HP
    cond["max_hp"] = max_hp
    cur = cond.get("current_hp")
    if cur is None:
        cur = max_hp
    try:
        cur = int(cur)
    except (TypeError, ValueError):
        cur = max_hp
    cond["current_hp"] = max(0, min(max_hp, cur))
    cond["broken"] = bool(cond.get("broken")) or cond["current_hp"] <= 0
    if cond["broken"]:
        cond["current_hp"] = 0
    return cond


def ratio(item):
    """Current HP divided by max HP (0.0 when broken)."""
    cond = ensure_condition(item)
    max_hp = cond["max_hp"]
    if max_hp <= 0:
        return 0.0
    return cond["current_hp"] / float(max_hp)


def tier(item):
    """healthy / bruised / wounded / disabled for display."""
    if is_broken(item):
        return TIER_DISABLED
    return tier_for_ratio(ratio(item))


def is_broken(item):
    """True when the item is broken (HP at zero)."""
    if item is None:
        return False
    cond = ensure_condition(item)
    return bool(cond.get("broken"))


def damage(item, amount):
    """Apply ``amount`` damage; set ``broken`` when HP reaches zero.

    Returns the HP actually removed (int).
    """
    if item is None or amount is None:
        return 0
    try:
        dmg = max(0, int(amount))
    except (TypeError, ValueError):
        return 0
    if dmg <= 0:
        return 0
    cond = ensure_condition(item)
    before = cond["current_hp"]
    after = max(0, before - dmg)
    cond["current_hp"] = after
    if after <= 0:
        cond["broken"] = True
        cond["current_hp"] = 0
    return before - after


def repair(item, amount):
    """Restore ``amount`` HP (does not clear ``broken`` unless HP > 0).

    Returns HP actually restored (int). When repair brings HP above zero,
    ``broken`` clears automatically.
    """
    if item is None or amount is None:
        return 0
    try:
        heal = max(0, int(amount))
    except (TypeError, ValueError):
        return 0
    if heal <= 0:
        return 0
    cond = ensure_condition(item)
    before = cond["current_hp"]
    after = min(cond["max_hp"], before + heal)
    cond["current_hp"] = after
    if after > 0:
        cond["broken"] = False
    return after - before


def status_line(item):
    """One-line durability summary for look/inventory, or None when pristine."""
    cond = ensure_condition(item)
    if cond["current_hp"] >= cond["max_hp"] and not cond.get("broken"):
        return None
    label = tier(item)
    if label == TIER_HEALTHY:
        return None
    if label == TIER_DISABLED:
        return "[broken]"
    return f"[{label} {cond['current_hp']}/{cond['max_hp']}]"
