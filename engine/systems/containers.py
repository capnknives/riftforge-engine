"""
containers.py -- worn kit/loot bags, open-inventory stack cap, home stash.

Players wear up to two bag Items (back + shoulder): one designated gear
bag (job kit) and one general loot bag. Open inventory holds at most
OPEN_INVENTORY_CAPACITY distinct *stacks* (ammo piles count as one).

Pure logic: no networking.
"""

from __future__ import annotations

from collections import OrderedDict

from engine.style import strip_ansi
from engine import hooks as hooks_mod

OPEN_INVENTORY_CAPACITY = 20
CONTAINER_SLOTS = ("back", "shoulder")
STARTER_KIT_BAG_ID = "starter_kit_bag"
CANVAS_LOOT_BAG_ID = "canvas_loot_bag"
DEFAULT_BAG_CAPACITY = 20

# Hunter ammo auto-stows like gear crumbs even before every row is tagged.
AMMO_GEAR_IDS = frozenset({
    "rock_salt_shells",
    "silver_rounds",
    "colt_original_bullet",
    "box_12ga_fmj",
    "box_9mm_fmj",
    "box_45acp_fmj",
    "box_44mag_fmj",
    "box_308_fmj",
    "box_556_fmj",
})

OPEN_FULL = (
    "Your hands are full "
    f"({OPEN_INVENTORY_CAPACITY} stacks). Stow something, wear a backpack, "
    "or drop gear."
)
LOOT_BAG_FULL = (
    "Your backpack is full. Make room or stash overflow at home "
    "(help stash)."
)
LOOT_BAG_TOO_LARGE = (
    "That is too bulky for a backpack -- carry it in your hands or stash "
    "it at home."
)
NO_LOOT_BAG = (
    "Your hands are full and you are not wearing a backpack for overflow "
    "(see 'help backpacks')."
)
NO_GEAR_BAG = (
    "You need a kit bag on your back or shoulder for that "
    "(see 'help gear')."
)
BAG_SLOT_FULL = "That shoulder is already taken by another bag."
BAG_NOT_WORN = "You aren't wearing that bag."
BAG_WRONG_SLOT = "Bags only go on your back or shoulder."
STOW_NOT_CARRIED = "You aren't carrying that."
STOW_NOT_IN_LOOT_BAG = "That isn't in your backpack."
STOW_NOT_GEAR_LOOT = (
    "That belongs in your gear bag. Try 'gear stow' or 'gear stow all'."
)
STOW_NEST_BAG = "You can't pack a bag inside another bag."

# Bare ``backpack`` / ``loot bag`` tokens for get/put parsing (not ``bag`` --
# groceries and the kit bag collide).
GENERIC_LOOT_BAG_WORDS = frozenset({
    "backpack",
    "backpacks",
    "lootbag",
    "loot-bag",
    "loot bag",
})

CARRY_SIZES = frozenset({"small", "medium", "large"})
_ARMOR_SLOTS = frozenset({
    "shield", "head", "about", "neck", "body", "arms", "hands",
    "finger", "waist", "legs", "feet",
})


def ensure_containers_map(character):
    """Return ``character.containers`` as a back/shoulder dict."""
    if character is None:
        return {"back": None, "shoulder": None}
    raw = getattr(character, "containers", None)
    if not isinstance(raw, dict):
        character.containers = {"back": None, "shoulder": None}
        return character.containers
    for slot in CONTAINER_SLOTS:
        raw.setdefault(slot, None)
    return raw


def ensure_home_stash(character):
    """Mutable list for claimed-home overflow storage."""
    if character is None:
        return []
    stash = getattr(character, "home_stash", None)
    if stash is None or not isinstance(stash, list):
        character.home_stash = []
    return character.home_stash


def is_bag_item(item):
    """True when ``item`` is a wearable container bag."""
    if item is None:
        return False
    if getattr(item, "is_bag", False):
        return True
    catalog_id = getattr(item, "catalog_id", None)
    if not catalog_id:
        return False
    spec = hooks_mod.get_item_spec(str(catalog_id))
    return bool(isinstance(spec, dict) and spec.get("is_bag"))


def is_gear_bag_item(item):
    """True when this bag is the designated job kit bag."""
    if not is_bag_item(item):
        return False
    if getattr(item, "is_gear_bag", False):
        return True
    catalog_id = getattr(item, "catalog_id", None)
    if not catalog_id:
        return False
    spec = hooks_mod.get_item_spec(str(catalog_id))
    return bool(isinstance(spec, dict) and spec.get("is_gear_bag"))


def bag_capacity(item):
    """Max rows inside one bag Item."""
    cap = getattr(item, "bag_capacity", None)
    if cap is not None:
        try:
            return max(1, int(cap))
        except (TypeError, ValueError):
            pass
    catalog_id = getattr(item, "catalog_id", None)
    if catalog_id:
        spec = hooks_mod.get_item_spec(str(catalog_id))
        if isinstance(spec, dict) and spec.get("bag_capacity") is not None:
            try:
                return max(1, int(spec["bag_capacity"]))
            except (TypeError, ValueError):
                pass
    return DEFAULT_BAG_CAPACITY


def bag_contents(item):
    """Mutable list of Items inside a bag."""
    if item is None:
        return []
    contents = getattr(item, "bag_contents", None)
    if contents is None or not isinstance(contents, list):
        item.bag_contents = []
    return item.bag_contents


def stack_key(item, viewer):
    """Grouping key for open-inventory cap (matches display stacks)."""
    if item is None:
        return ""
    catalog_id = getattr(item, "catalog_id", None)
    if catalog_id:
        return f"cat:{str(catalog_id).strip().lower()}"
    painted = hooks_mod.item_display_key(item, viewer)
    return strip_ansi(painted).strip().lower()


def item_carry_size(item):
    """Return ``small``, ``medium``, or ``large`` for bag slot math.

    Catalog may set ``carry_size``. Otherwise infer: two-hand weapons and
    bulky armor are large (no backpack); one-hand weapons and worn armor
  are medium (one slot each); crumbs / reagents default small (stackable).
    """
    if item is None:
        return "medium"
    explicit = getattr(item, "carry_size", None)
    if isinstance(explicit, str):
        key = explicit.strip().lower()
        if key in CARRY_SIZES:
            return key
    catalog_id = getattr(item, "catalog_id", None)
    if catalog_id:
        spec = hooks_mod.get_item_spec(str(catalog_id))
        if isinstance(spec, dict):
            raw = spec.get("carry_size")
            if isinstance(raw, str) and raw.strip().lower() in CARRY_SIZES:
                return raw.strip().lower()
    grip = hooks_mod.weapon_grip_for(item)
    if grip == "two_hand":
        return "large"
    slot = getattr(item, "slot", None)
    if not slot and catalog_id:
        spec = hooks_mod.get_item_spec(str(catalog_id))
        if isinstance(spec, dict):
            slot = spec.get("slot")
    if slot in ("weapon", "offhand"):
        return "medium"
    if slot in _ARMOR_SLOTS:
        return "medium"
    if getattr(item, "is_bag", False):
        return "large"
    return "small"


def bag_stack_keys(contents, viewer):
    """Distinct stack keys in a bag list (small items stack; medium does not)."""
    keys = OrderedDict()
    for piece in contents or []:
        if item_carry_size(piece) != "small":
            keys[f"row:{id(piece)}"] = True
            continue
        keys.setdefault(stack_key(piece, viewer), True)
    return keys


def bag_slots_used(contents, viewer):
    """How many bag slots ``contents`` consumes (stack-aware)."""
    return len(bag_stack_keys(contents, viewer))


def bag_would_add_slot(contents, item, viewer, capacity):
    """True when adding ``item`` needs a new slot in a size-aware bag."""
    if item is None:
        return True
    if item_carry_size(item) == "large":
        return True
    if item_carry_size(item) == "small":
        key = stack_key(item, viewer)
        for piece in contents or []:
            if (
                item_carry_size(piece) == "small"
                and stack_key(piece, viewer) == key
            ):
                return False
    used = bag_slots_used(contents, viewer)
    return used >= capacity


def designated_loot_bag(character):
    """Worn general loot/backpack Item (not the job kit bag), or None."""
    for slot in CONTAINER_SLOTS:
        bag = ensure_containers_map(character).get(slot)
        if bag is not None and not is_gear_bag_item(bag):
            return bag
    return None


def is_generic_loot_bag_query(query):
    """True when ``query`` names the worn loot bag, not a specific item."""
    text = (query or "").strip().lower()
    if not text:
        return True
    if text in GENERIC_LOOT_BAG_WORDS:
        return True
    if text.endswith(" backpack") or text.endswith(" back pack"):
        return True
    return False


def resolve_loot_bag(character, query):
    """Return a loot/backpack Item for ``query``, or None when not a bag name.

    Generic tokens (``backpack``, ``loot bag``, …) resolve to the worn loot
    bag. A specific inventory key resolves when it matches a non-kit bag.
    """
    if character is None:
        return None
    raw = (query or "").strip()
    if not raw or is_generic_loot_bag_query(raw):
        return designated_loot_bag(character)
    inv = list(getattr(character, "inventory", None) or [])
    from command_support import _find_item

    piece = _find_item(raw, inv, character=character)
    if piece is not None and is_bag_item(piece) and not is_gear_bag_item(piece):
        return piece
    return None


def find_in_loot_bag(character, needle, *, loot_bag=None):
    """Find one Item inside the loot bag by catalog id or name fragment."""
    bag = loot_bag if loot_bag is not None else designated_loot_bag(character)
    if bag is None:
        return None
    from command_support import _find_item

    return _find_item(needle, bag_contents(bag), character=character)


def stow_in_loot_bag(character, item, *, loot_bag=None):
    """Move ``item`` from open inventory into the loot backpack."""
    if character is None or item is None:
        return False, STOW_NOT_CARRIED
    bag = loot_bag if loot_bag is not None else designated_loot_bag(character)
    if bag is None:
        return False, NO_LOOT_BAG
    inv = getattr(character, "inventory", None)
    if inv is None or item not in inv:
        return False, STOW_NOT_CARRIED
    refuse = hooks_mod.containers_on_body_carry_refusal(character, item)
    if refuse:
        return False, refuse
    if is_bag_item(item):
        return False, STOW_NEST_BAG
    if hooks_mod.containers_is_gear_item(item):
        return False, STOW_NOT_GEAR_LOOT
    loot_refusal = loot_bag_refusal(character, item, loot_bag=bag)
    if loot_refusal:
        return False, loot_refusal
    inv.remove(item)
    bag_contents(bag).append(item)
    return True, f"You stow {item.key} in {bag.key}."


def stow_all_in_loot_bag(character, *, loot_bag=None):
    """Stow every stowable loose inventory row into the loot backpack."""
    bag = loot_bag if loot_bag is not None else designated_loot_bag(character)
    if bag is None:
        return False, NO_LOOT_BAG
    inv = getattr(character, "inventory", None) or []
    from engine import hooks as hooks_mod

    candidates = [
        piece
        for piece in list(inv)
        if not hooks_mod.containers_item_worn_on_body(character, piece)
        and not is_bag_item(piece)
    ]
    if not candidates:
        return False, "You aren't carrying anything to stow."
    names = []
    for piece in candidates:
        ok, _msg = stow_in_loot_bag(character, piece, loot_bag=bag)
        if ok:
            names.append(piece.key)
    if not names:
        return False, "Nothing in your inventory fits in your backpack."
    return True, f"You stow in {bag.key}: " + ", ".join(names) + "."


def unstow_from_loot_bag(character, item, *, loot_bag=None):
    """Move ``item`` from the loot backpack onto open inventory."""
    if character is None or item is None:
        return False, STOW_NOT_IN_LOOT_BAG
    bag = loot_bag if loot_bag is not None else designated_loot_bag(character)
    if bag is None:
        return False, NO_LOOT_BAG
    contents = bag_contents(bag)
    if item not in contents:
        return False, STOW_NOT_IN_LOOT_BAG
    refusal = open_inventory_refusal(character, item)
    if refusal:
        return False, refusal
    inv = getattr(character, "inventory", None)
    if inv is None:
        character.inventory = []
        inv = character.inventory
    contents.remove(item)
    inv.append(item)
    return True, f"You pull {item.key} from {bag.key}."


def unstow_all_from_loot_bag(character, *, loot_bag=None):
    """Pull every item from the loot backpack onto open inventory."""
    bag = loot_bag if loot_bag is not None else designated_loot_bag(character)
    if bag is None:
        return False, NO_LOOT_BAG
    contents = bag_contents(bag)
    if not contents:
        return False, "Your backpack is empty."
    pieces = list(contents)
    names = []
    for piece in pieces:
        ok, _msg = unstow_from_loot_bag(character, piece, loot_bag=bag)
        if ok:
            names.append(piece.key)
    if not names:
        return False, "Your backpack is empty."
    return True, f"You pull from {bag.key}: " + ", ".join(names) + "."


def loot_bag_refusal(character, item, *, loot_bag=None):
    """Refusal when ``item`` cannot enter the worn loot bag; else None."""
    if character is None or item is None:
        return None
    bag = loot_bag if loot_bag is not None else designated_loot_bag(character)
    if bag is None:
        return NO_LOOT_BAG
    if item_carry_size(item) == "large":
        return LOOT_BAG_TOO_LARGE
    contents = bag_contents(bag)
    cap = bag_capacity(bag)
    if bag_would_add_slot(contents, item, character, cap):
        return LOOT_BAG_FULL
    return None


def _item_resting_place(character, item):
    """Where ``item`` currently lives on the character, or None."""
    if item is None or character is None:
        return None
    if item in hooks_mod.containers_ensure_gear_bag(character):
        return "gear"
    for bag in worn_bags(character):
        if item in bag_contents(bag):
            if is_gear_bag_item(bag):
                return "gear"
            return "loot"
    inv = getattr(character, "inventory", None) or []
    if item in inv:
        return "hands"
    return None


def route_acquired_item(character, item):
    """Place one newly acquired item (gear bag, hands, or loot backpack).

    Idempotent when the item already sits in the right place. Returns an
    optional player-visible tuck line.
    """
    if character is None or item is None:
        return None
    dest = placement_destination(character, item)
    if dest is None:
        return None
    if try_merge_carried_stack(character, item, dest=dest):
        return None
    if _item_resting_place(character, item) == dest:
        return None
    inv = getattr(character, "inventory", None)
    if inv is None:
        character.inventory = []
        inv = character.inventory
    if dest == "gear":
        bag_item = designated_gear_bag(character)
        if bag_item is None:
            return None
        if item in inv:
            inv.remove(item)
        bag_contents(bag_item).append(item)
        return (
            f"You tuck {item.key} into your gear bag. Type 'gear' to look."
        )
    if dest == "loot":
        loot = designated_loot_bag(character)
        if loot is None:
            return None
        if item in inv:
            inv.remove(item)
        bag_contents(loot).append(item)
        return f"You tuck {item.key} into {loot.key}."
    if dest == "hands":
        if item not in inv:
            inv.append(item)
        return None
    return None


def placement_destination(character, item):
    """Where a pickup would land: ``gear``, ``hands``, ``loot``, or None."""
    if character is None or item is None:
        return None
    if hooks_mod.containers_is_gear_item(item):
        if designated_gear_bag(character) is None:
            return None
        return "gear"
    if not open_inventory_would_add_stack(character, item):
        return "hands"
    if open_inventory_stack_count(character) < OPEN_INVENTORY_CAPACITY:
        return "hands"
    if loot_bag_refusal(character, item) is None:
        return "loot"
    if item_carry_size(item) == "large":
        return None
    if designated_loot_bag(character) is None:
        return None
    return None


def _surface_items(character):
    return hooks_mod.containers_surface_inventory_items(character)


def open_stack_keys(character):
    """Distinct stack keys currently in open inventory."""
    keys = OrderedDict()
    for piece in _surface_items(character):
        keys.setdefault(stack_key(piece, character), True)
    return keys


def open_inventory_stack_count(character):
    """How many open-inventory stacks the character carries."""
    return len(open_stack_keys(character))


def open_inventory_would_add_stack(character, item):
    """True when picking up ``item`` needs a new open stack slot."""
    key = stack_key(item, character)
    if not key:
        return True
    return key not in open_stack_keys(character)


def _stack_unit_count(item):
    """How many units one Item row represents (``stack_charges`` or 1)."""
    raw = getattr(item, "stack_charges", None)
    if raw is not None:
        try:
            count = int(raw)
            if count > 0:
                return count
        except (TypeError, ValueError):
            pass
    return 1


def _merge_stack_items(target, incoming):
    """Fold ``incoming`` into ``target``; ``incoming`` is discarded after."""
    total = _stack_unit_count(target) + _stack_unit_count(incoming)
    target.stack_charges = total


def _find_matching_stack_item(items, item, viewer):
    """First row in ``items`` sharing ``item``'s stack key, or None."""
    key = stack_key(item, viewer)
    if not key:
        return None
    for piece in items or []:
        if stack_key(piece, viewer) == key:
            return piece
    return None


def try_merge_carried_stack(character, item, *, dest=None):
    """Merge a duplicate-stack pickup into an existing row.

    Returns True when ``item`` was absorbed (caller must not append a new
    row). Used by ``get``, autoloot, and loot routing so the open stack cap
    cannot be bypassed by duplicate catalog ids.
    """
    if character is None or item is None:
        return False
    if open_inventory_would_add_stack(character, item):
        return False
    if dest is None:
        dest = placement_destination(character, item)
    if dest == "gear":
        bag = designated_gear_bag(character)
        if bag is None:
            return False
        existing = _find_matching_stack_item(
            bag_contents(bag), item, character,
        )
        if existing is None:
            return False
        _merge_stack_items(existing, item)
        return True
    if dest == "loot":
        bag = designated_loot_bag(character)
        if bag is None:
            return False
        existing = _find_matching_stack_item(
            bag_contents(bag), item, character,
        )
        if existing is None:
            return False
        _merge_stack_items(existing, item)
        return True
    existing = _find_matching_stack_item(
        _surface_items(character), item, character,
    )
    if existing is None:
        return False
    _merge_stack_items(existing, item)
    return True


def open_inventory_refusal(character, item):
    """Refusal when hands and loot bag cannot accept ``item``; else None."""
    if character is None or item is None:
        return None
    if hooks_mod.containers_is_gear_item(item):
        return hooks_mod.containers_gear_acquire_refusal(character, item)
    if placement_destination(character, item) is not None:
        return None
    if item_carry_size(item) == "large":
        if (
            open_inventory_would_add_stack(character, item)
            and open_inventory_stack_count(character) >= OPEN_INVENTORY_CAPACITY
        ):
            return LOOT_BAG_TOO_LARGE
        return None
    if designated_loot_bag(character) is None:
        return NO_LOOT_BAG
    loot_refusal = loot_bag_refusal(character, item)
    if loot_refusal:
        return loot_refusal
    return OPEN_FULL


def acquire_refusal(character, item):
    """Pre-pickup refusal for ``get`` / vendor take (gear, hands, loot bag)."""
    relic_refusal = hooks_mod.containers_relic_acquire_refusal(character, item)
    if relic_refusal:
        return relic_refusal
    return open_inventory_refusal(character, item)


def designated_gear_bag(character):
    """Worn gear-bag Item, or None."""
    for slot in CONTAINER_SLOTS:
        bag = ensure_containers_map(character).get(slot)
        if bag is not None and is_gear_bag_item(bag):
            return bag
    return None


def worn_bags(character):
    """List of worn bag Items (back then shoulder)."""
    out = []
    for slot in CONTAINER_SLOTS:
        bag = ensure_containers_map(character).get(slot)
        if bag is not None:
            out.append(bag)
    return out


def make_starter_kit_bag(where="kit"):
    """Catalog starter kit bag Item."""
    return hooks_mod.make_world_item(
        {"item": STARTER_KIT_BAG_ID},
        where=where,
    )


def wear_bag(character, bag_item, slot):
    """Wear ``bag_item`` on ``slot`` ('back' or 'shoulder').

    Returns (ok, message).
    """
    if character is None or bag_item is None:
        return False, "You aren't carrying that."
    slot = str(slot or "").strip().lower()
    if slot not in CONTAINER_SLOTS:
        return False, BAG_WRONG_SLOT
    if not is_bag_item(bag_item):
        return False, "That isn't a bag you can sling on."
    inv = getattr(character, "inventory", None) or []
    if bag_item not in inv:
        return False, "You aren't carrying that."
    containers = ensure_containers_map(character)
    # Only one gear bag per character.
    if is_gear_bag_item(bag_item):
        other = designated_gear_bag(character)
        if other is not None and other is not bag_item:
            return False, "You already wear a kit bag -- remove it first."
    occupied = containers.get(slot)
    if occupied is not None and occupied is not bag_item:
        return False, BAG_SLOT_FULL
    # Clear old slot if this bag was worn elsewhere.
    for name in CONTAINER_SLOTS:
        if containers.get(name) is bag_item:
            containers[name] = None
    containers[slot] = bag_item
    bag_item.container_worn = slot
    return True, f"You sling {bag_item.key} over your {slot}."


def remove_bag(character, bag_item):
    """Take a worn bag off; it stays in inventory."""
    if bag_item is None:
        return False, BAG_NOT_WORN
    containers = ensure_containers_map(character)
    worn = False
    for slot in CONTAINER_SLOTS:
        if containers.get(slot) is bag_item:
            containers[slot] = None
            worn = True
    if not worn:
        return False, BAG_NOT_WORN
    bag_item.container_worn = None
    return True, f"You slip {bag_item.key} off and carry it in hand."


def move_bag_slot(character, bag_item, slot):
    """Restring a worn bag between back and shoulder."""
    if bag_item is None:
        return False, BAG_NOT_WORN
    if getattr(bag_item, "container_worn", None) is None:
        ok, msg = wear_bag(character, bag_item, slot)
        return ok, msg
    return wear_bag(character, bag_item, slot)


def rebind_containers_from_inventory(character):
    """Rebuild ``character.containers`` from ``container_worn`` flags."""
    if character is None:
        return
    containers = {"back": None, "shoulder": None}
    for piece in list(getattr(character, "inventory", None) or []):
        if not is_bag_item(piece):
            continue
        slot = getattr(piece, "container_worn", None)
        if slot not in CONTAINER_SLOTS:
            piece.container_worn = None
            continue
        if containers[slot] is not None:
            piece.container_worn = None
            continue
        containers[slot] = piece
    character.containers = containers


def grant_and_wear_starter_kit_bag(character, *, where="kit"):
    """Create starter kit bag, add to inventory, wear on back (or shoulder)."""
    if character is None:
        return None
    existing = designated_gear_bag(character)
    if existing is not None:
        migrate_virtual_gear_bag(character)
        return existing
    bag = make_starter_kit_bag(where=where)
    if bag is None:
        return None
    inv = getattr(character, "inventory", None)
    if inv is None:
        character.inventory = []
        inv = character.inventory
    inv.append(bag)
    slot = "back"
    if ensure_containers_map(character).get("back") is not None:
        slot = "shoulder"
    wear_bag(character, bag, slot)
    migrate_virtual_gear_bag(character)
    return bag


def migrate_virtual_gear_bag(character):
    """Move legacy ``character.gear_bag`` rows into the worn kit bag."""
    if character is None:
        return
    virtual = list(getattr(character, "gear_bag", None) or [])
    if not virtual:
        return
    bag = designated_gear_bag(character)
    if bag is None:
        return
    contents = bag_contents(bag)
    for piece in virtual:
        if piece in contents:
            continue
        contents.append(piece)
    character.gear_bag = []


def heal_character_kit_bag(character):
    """Idempotent: every character gets a worn starter kit bag + migration."""
    if character is None:
        return False
    if getattr(character, "is_npc", False):
        return False
    if designated_gear_bag(character) is None:
        grant_and_wear_starter_kit_bag(character)
    else:
        migrate_virtual_gear_bag(character)
    rebind_containers_from_inventory(character)
    return True


def heal_all_kit_bags(game):
    """Boot heal: stamp kit bags on every persisted player + folded vault."""
    from engine.char_index import iter_characters

    count = 0
    for char in iter_characters(game):
        if heal_character_kit_bag(char):
            count += 1
    # Folded vault blobs (offline gm fold) -- heal JSON payloads in place.
    try:
        count += hooks_mod.containers_heal_folded_kit_bags(game)
    except Exception:
        pass
    return count


def stash_at_home(character, item, game):
    """Move one carried item into ``character.home_stash`` at home."""
    if character is None or item is None:
        return False, "You aren't carrying that."
    room = getattr(character, "location", None)
    if not hooks_mod.containers_room_is_character_home(character, room, game):
        return False, "You can only stash things at your claimed home."
    refuse = hooks_mod.containers_on_body_carry_refusal(character, item)
    if refuse:
        return False, refuse
    if getattr(item, "container_worn", None):
        return False, "Remove the bag from your shoulder first."
    inv = getattr(character, "inventory", None) or []
    removed = False
    if item in inv:
        inv.remove(item)
        removed = True
    else:
        # Inside a worn loot bag?
        for bag in worn_bags(character):
            if is_gear_bag_item(bag):
                continue
            contents = bag_contents(bag)
            if item in contents:
                contents.remove(item)
                removed = True
                break
    if not removed:
        bag_list = hooks_mod.containers_ensure_gear_bag(character)
        if item in bag_list:
            bag_list.remove(item)
            removed = True
    if not removed:
        return False, "You aren't carrying that."
    ensure_home_stash(character).append(item)
    return True, f"You stash {item.key} at home."


def retrieve_from_stash(character, needle, game):
    """Pull one item from home stash into open inventory (cap-checked)."""
    from command_support import _find_item

    room = getattr(character, "location", None)
    if not hooks_mod.containers_room_is_character_home(character, room, game):
        return False, "You can only retrieve stash at your claimed home."
    stash = ensure_home_stash(character)
    item = _find_item(needle, stash)
    if item is None:
        return False, "You don't have that in your home stash."
    refusal = acquire_refusal(character, item)
    if refusal:
        return False, refusal
    stash.remove(item)
    inv = getattr(character, "inventory", None)
    if inv is None:
        character.inventory = []
        inv = character.inventory
    inv.append(item)
    from engine import hooks
    stow_msg = hooks.after_acquire_item(character, item)
    msg = f"You retrieve {item.key} from your home stash."
    if stow_msg:
        msg = f"{msg} {stow_msg}"
    return True, msg


def stash_list_lines(character):
    """Lines for ``stash list`` / bare ``stash``."""
    stash = ensure_home_stash(character)
    lines = ["Home stash (claimed residence only)."]
    if not stash:
        lines.append("Empty.")
        return lines
    lines.append("Contents:")
    lines.extend(hooks_mod.containers_stacked_carry_lines(stash, character))
    return lines
