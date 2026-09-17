"""
locks.py -- engine lockpick kernel (Wave 2 folklore peel).

Mechanical picks and electronic bypass attempts live here. Effort delays,
gear-bag kit consumption, lodging private-home gates, and Hunter Arts XP
stay in the game facade (supers/locks.py) or player verbs.
"""

from __future__ import annotations

from world import Item

from command_support import _display_name, _find_item_prefer_locked
from engine import hooks
from engine.systems import doors as doors_engine
from engine.systems import containers as containers_mod

MECHANICAL_DC = 30.0
MECHANICAL_DOOR_DC = 35.0
ELECTRONIC_DC = 40.0


def item_lock_kind(item):
    """Return ``mechanical`` or ``electronic`` for a locked target."""
    if item is None:
        return "mechanical"
    kind = getattr(item, "lock_kind", None)
    if isinstance(kind, str) and kind.strip():
        return kind.strip().lower()
    catalog_id = getattr(item, "catalog_id", None)
    if catalog_id:
        spec = hooks.get_item_spec(catalog_id) or {}
        cat_kind = spec.get("lock_kind")
        if isinstance(cat_kind, str) and cat_kind.strip():
            return cat_kind.strip().lower()
    return "mechanical"


def _item_label(item, viewer=None):
    """Painted item name for player-visible strings (never raw ``item.key``)."""
    label = hooks.item_display_key(item, viewer)
    if label:
        return label
    return getattr(item, "key", "the lock") or "the lock"


def _dc_for(kind):
    """Resolve difficulty for mechanical, electronic, or door picks."""
    hooked = hooks.lock_dc(kind)
    if hooked is not None:
        return float(hooked)
    if kind == "electronic":
        return ELECTRONIC_DC
    if kind == "mechanical_door":
        return MECHANICAL_DOOR_DC
    return MECHANICAL_DC


def find_locked_item(character, target_name):
    """Resolve a locked container in inventory, bags, gear bag, or floor."""
    room = getattr(character, "location", None)
    if room is None:
        return None, "You are nowhere."
    if not target_name:
        return None, "Pick what lock? Try: lockpick <container>"
    holders = []
    inv = getattr(character, "inventory", None) or []
    holders.append([o for o in inv if isinstance(o, Item)])
    for bag in containers_mod.worn_bags(character):
        contents = containers_mod.bag_contents(bag)
        holders.append([o for o in contents if isinstance(o, Item)])
    gear = hooks.containers_ensure_gear_bag(character)
    if gear:
        holders.append([o for o in gear if isinstance(o, Item)])
    holders.append([o for o in room.contents if isinstance(o, Item)])

    locked_item = None
    seen_probe = None
    for items in holders:
        item = _find_item_prefer_locked(
            target_name,
            items,
            character=character,
        )
        if item is None:
            continue
        if seen_probe is None:
            seen_probe = item
        if getattr(item, "locked", False):
            locked_item = item
            break
    if locked_item is not None:
        return locked_item, None
    if seen_probe is None:
        return None, "You do not see that here."
    hooks.upgrade_legacy_container(seen_probe)
    if getattr(seen_probe, "locked", False):
        return seen_probe, None
    probe_label = _item_label(seen_probe, character)
    return None, f"{probe_label} is not locked."


def try_pick_container(character, target_name, game=None, *, force_kind=None):
    """
    Pick or clip a locked container.

    Returns ``(ok, actor_message, room_line)``. Does not stamp effort delay
    or consume bypass kits -- the facade owns those steps.
    """
    item, err = find_locked_item(character, target_name)
    if err:
        return False, err, None
    kind = force_kind or item_lock_kind(item)
    item_label = _item_label(item, character)
    actor_name = _display_name(character)
    if kind == "electronic":
        dc = _dc_for("electronic")
        skill_id = "electronics"
    else:
        dc = _dc_for("mechanical")
        skill_id = "thievery"
    if hooks.skill_check(character, skill_id, dc):
        item.locked = False
        room_line = (
            f"{actor_name} works a pick at {item_label} until it clicks free."
        )
        if kind == "electronic":
            room_line = (
                f"{actor_name} jury-rigs a lock bypass on {item_label} "
                "with wire clips."
            )
            actor = (
                f"You bridge the lock leads with a tap clip until "
                f"{item_label} clicks free."
            )
        else:
            actor = f"You work the lock on {item_label} until it clicks free."
        return True, actor, room_line
    if kind == "electronic":
        return (
            False,
            (
                "The lock fights the clip -- leads spark and the bypass fails. "
                "Train electronics or try a mechanical pick if the seal is old iron."
            ),
            f"{actor_name} sparks a failed bypass on {item_label}.",
        )
    return (
        False,
        (
            "The lock resists you -- your picks slip. "
            "Train thievery (hide, sneak, steal) or force it with open."
        ),
        f"{actor_name} fumbles at a lock on {item_label}.",
    )


def try_pick_door(character, direction, game=None):
    """
    Thievery pick on a persisted structure / interior door lock.

    Private-home hospitality thresholds stay in the game facade. Cell doors
    refuse here so jail sentences cannot be trivially picked.
    """
    room = getattr(character, "location", None)
    if room is None:
        return False, "You are nowhere.", None
    direction = str(direction or "").strip().lower()
    if not direction:
        return (
            False,
            "Which door? Try: lockpick door <direction>  (east, in, out, …)",
            None,
        )
    dest = (room.exits or {}).get(direction)
    if dest is None:
        return False, f"No exit '{direction}' here.", None
    if getattr(room, "is_cell", False):
        return False, "The cell door is barred for your sentence.", None
    if not doors_engine.exit_is_door(room, direction, dest):
        return False, f"There is no closable door to the {direction}.", None
    if not doors_engine.is_exit_locked(game, room, direction, dest):
        return False, f"The {direction} door is not locked.", None
    dc = _dc_for("mechanical_door")
    actor_name = _display_name(character)
    if hooks.skill_check(character, "thievery", dc):
        doors_engine.set_exit_locked(game, room, direction, False)
        room_line = (
            f"{actor_name} works a pick at the {direction} door until "
            "the latch gives."
        )
        return True, f"You slip the {direction} door latch free.", room_line
    return (
        False,
        (
            f"The {direction} door lock fights your picks. "
            "Keep practicing thievery or find another way through."
        ),
        f"{actor_name} fumbles at the {direction} door lock.",
    )
