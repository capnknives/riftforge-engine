"""
clothing_pockets.py -- small items tucked in worn or carried clothes.

Wallet cash and ID papers stay in the pants-pocket wallet
(``wallet_container``). Phones and other pocketable crumbs go in the
garment itself: jeans, cargo pants, a hoodie kangaroo pocket. Players
type put / get / look in -- there is no smashed ``pocketput`` verb.

Stdlib only; no supers imports. Catalog ``layer: clothing`` plus optional
``pocket_capacity`` drive the math. Slot defaults cover jeans without
authoring every row.
"""

from __future__ import annotations

from engine import hooks as hooks_mod
from engine.systems.containers import item_carry_size, open_inventory_refusal
from engine.systems.wearables import iter_worn_clothing

# Slot defaults when the catalog omits pocket_capacity. Undergarments
# (wear_depth skin / undergarment) stay 0 even on the legs slot.
SLOT_POCKET_DEFAULTS = {
    "legs": 2,
    "about": 2,
    "over": 2,
    "waist": 1,
}
NO_POCKET_DEPTHS = frozenset({"skin", "undergarment"})

STOW_NOT_CLOTHING = "That is not clothing."
NO_POCKETS = "Those clothes have no pockets."
POCKETS_FULL = "Those pockets are full."
STOW_TOO_BULKY = "That is too bulky for a clothing pocket."
STOW_NOT_POCKET_ITEM = "That does not fit in a clothing pocket."
STOW_NOT_IN_POCKET = "That isn't in those pockets."
STOW_NOT_CARRYING = "You aren't carrying that."


def _strip_articles(query):
    """Drop leading the/a/an/my/your so 'my jeans' matches jeans."""
    text = (query or "").strip().lower()
    prefixes = ("the ", "a ", "an ", "my ", "your ")
    changed = True
    while changed and text:
        changed = False
        for prefix in prefixes:
            if text.startswith(prefix):
                text = text[len(prefix):].lstrip()
                changed = True
    return text


def is_clothing_item(item):
    """True when this Item is clothing-layer (live stamp or catalog)."""
    if item is None:
        return False
    layer = getattr(item, "layer", None)
    if isinstance(layer, str) and layer.strip().lower() == "clothing":
        return True
    catalog_id = getattr(item, "catalog_id", None)
    if not catalog_id:
        return False
    spec = hooks_mod.get_item_spec(str(catalog_id))
    if not isinstance(spec, dict):
        return False
    return str(spec.get("layer") or "").strip().lower() == "clothing"


def _catalog_spec(item):
    """Catalog dict for this Item, or None."""
    catalog_id = getattr(item, "catalog_id", None)
    if not catalog_id:
        return None
    spec = hooks_mod.get_item_spec(str(catalog_id))
    return spec if isinstance(spec, dict) else None


def _int_capacity(raw):
    """Parse a non-negative capacity, or None when missing/junk."""
    if raw is None:
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def pocket_capacity(item):
    """How many small items this garment can hold (0 = no pockets)."""
    if item is None or not is_clothing_item(item):
        return 0
    live = _int_capacity(getattr(item, "pocket_capacity", None))
    if live is not None:
        return live
    spec = _catalog_spec(item) or {}
    nested = spec.get("clothing") if isinstance(spec.get("clothing"), dict) else {}
    catalog = _int_capacity(spec.get("pocket_capacity"))
    if catalog is None:
        catalog = _int_capacity(nested.get("pocket_capacity"))
    if catalog is not None:
        return catalog
    depth = str(
        getattr(item, "wear_depth", None)
        or spec.get("wear_depth")
        or nested.get("wear_depth")
        or ""
    ).strip().lower()
    if depth in NO_POCKET_DEPTHS:
        return 0
    slot = str(
        getattr(item, "slot", None) or spec.get("slot") or ""
    ).strip().lower()
    return int(SLOT_POCKET_DEFAULTS.get(slot, 0))


def pocket_contents(item):
    """Mutable list of Items inside a clothing garment."""
    if item is None:
        return []
    contents = getattr(item, "clothing_contents", None)
    if contents is None or not isinstance(contents, list):
        item.clothing_contents = []
        contents = item.clothing_contents
    return contents


def look_in_pocket_lines(item):
    """Player lines for ``look in <clothes>``, or None if not clothing."""
    if item is None or not is_clothing_item(item):
        return None
    cap = pocket_capacity(item)
    name = getattr(item, "key", None) or "those clothes"
    if cap <= 0 and not pocket_contents(item):
        return [f"{name} has no pockets."]
    contents = pocket_contents(item)
    if not contents:
        return [f"You look in {name} -- the pockets are empty."]
    lines = [f"You look in {name}:"]
    for piece in contents:
        lines.append(f"  {piece.key}")
    return lines


def find_in_pocket(garment, query):
    """Resolve one nested item by partial name, or None."""
    if garment is None or not query:
        return None
    from engine.command_support import _find_item

    return _find_item(_strip_articles(query), pocket_contents(garment))


def find_pocket_garment(character, query, *, include_floor=False):
    """Match worn, carried, or (optionally) floor clothing by name."""
    if character is None or not query:
        return None
    from engine.command_support import _find_item
    from engine.world import Item

    seen = set()
    candidates = []

    def _add(piece):
        if piece is None or id(piece) in seen:
            return
        if not is_clothing_item(piece):
            return
        seen.add(id(piece))
        candidates.append(piece)

    for _slot, piece in iter_worn_clothing(character):
        _add(piece)
    for piece in list(getattr(character, "inventory", None) or []):
        _add(piece)
    if include_floor:
        room = getattr(character, "location", None)
        if room is not None:
            for obj in list(getattr(room, "contents", None) or []):
                if isinstance(obj, Item):
                    _add(obj)
    return _find_item(_strip_articles(query), candidates, character=character)


def _is_bag_like(item):
    """True for loot bags, kit bags, wallets -- they are not pocket crumbs."""
    if item is None:
        return False
    if getattr(item, "is_bag", False) or getattr(item, "is_gear_bag", False):
        return True
    if getattr(item, "is_wallet", False):
        return True
    return False


def stow_in_pocket(character, item, garment):
    """Move ``item`` from inventory into ``garment`` pockets.

    Returns (ok, message). Phones are allowed (unlike the wallet).
    """
    if character is None or item is None or garment is None:
        return False, STOW_NOT_CARRYING
    if item is garment:
        return False, "You can't put that in itself."
    if not is_clothing_item(garment):
        return False, STOW_NOT_CLOTHING
    inv = getattr(character, "inventory", None)
    if inv is None or item not in inv:
        return False, STOW_NOT_CARRYING
    cap = pocket_capacity(garment)
    if cap <= 0:
        return False, NO_POCKETS
    if is_clothing_item(item):
        return False, "Clothes don't go in clothes -- wear them."
    if _is_bag_like(item):
        return False, STOW_NOT_POCKET_ITEM
    if item_carry_size(item) != "small":
        return False, STOW_TOO_BULKY
    contents = pocket_contents(garment)
    if len(contents) >= cap:
        return False, POCKETS_FULL
    inv.remove(item)
    contents.append(item)
    return True, f"You tuck {item.key} into {garment.key}."


def unstow_from_pocket(character, item, garment):
    """Pull one nested item back to open inventory."""
    if character is None or item is None or garment is None:
        return False, STOW_NOT_IN_POCKET
    contents = pocket_contents(garment)
    if item not in contents:
        return False, STOW_NOT_IN_POCKET
    refuse = open_inventory_refusal(character, item)
    if refuse:
        return False, refuse
    contents.remove(item)
    inv = getattr(character, "inventory", None)
    if inv is None:
        character.inventory = []
        inv = character.inventory
    inv.append(item)
    return True, f"You take {item.key} out of {garment.key}."
