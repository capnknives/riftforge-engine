"""
containers.py -- worn kit/loot bags, open-inventory stack cap, home stash.

Players wear up to three bag Items (back + shoulder + hip): one designated
gear bag (job kit), one general loot bag, and one profession satchel.
Open inventory holds at most OPEN_INVENTORY_CAPACITY distinct *stacks*
(ammo piles count as one).

Pure logic: no networking.
"""

from __future__ import annotations

from collections import OrderedDict

from engine.style import strip_ansi
from engine import hooks as hooks_mod

OPEN_INVENTORY_CAPACITY = 20
CONTAINER_SLOTS = ("back", "shoulder", "hip")
HIP_SLOT = "hip"
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
    f"({OPEN_INVENTORY_CAPACITY} stacks). Stow something, wear a backpack "
    "or satchel, or drop gear."
)
LOOT_BAG_HANDS_FULL = (
    "Your hands are full "
    f"({OPEN_INVENTORY_CAPACITY} stacks). Stow something or drop gear "
    "before picking that up."
)
LOOT_BAG_FULL = (
    "Your backpack is full. Make room or stash overflow at home "
    "(help stash)."
)
LOOT_BAG_TOO_LARGE = (
    "That is too bulky for a backpack -- carry it in your hands or stash "
    "it at home."
)
WEIGHT_CAPACITY_FULL = (
    "You cannot carry that much weight."
)
VOLUME_CAPACITY_FULL = (
    "You cannot carry that much bulk."
)
NO_LOOT_BAG = (
    "Your hands are full and you are not wearing a backpack for overflow "
    "(see 'help backpacks')."
)
NO_GEAR_BAG = (
    "You need a kit bag on your back or shoulder for that "
    "(see 'help gear')."
)
BAG_SLOT_FULL = "That slot is already taken by another bag."
BAG_NOT_WORN = "You aren't wearing that bag."
BAG_WRONG_SLOT = "Bags go on your back, shoulder, or hip."
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


def empty_containers_map():
    """Fresh back / shoulder / hip map (wallet pocket is separate)."""
    return {slot: None for slot in CONTAINER_SLOTS}


def ensure_containers_map(character):
    """Return ``character.containers`` as a back/shoulder/hip dict."""
    if character is None:
        return empty_containers_map()
    raw = getattr(character, "containers", None)
    if not isinstance(raw, dict):
        character.containers = empty_containers_map()
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


def _item_nostack(item):
    """True when this row must not share a display/drop stack with copies."""
    if item is None:
        return False
    if getattr(item, "nostack", False):
        return True
    catalog_id = getattr(item, "catalog_id", None)
    if catalog_id:
        spec = hooks_mod.get_item_spec(str(catalog_id))
        if isinstance(spec, dict) and (
            spec.get("nostack") or spec.get("unique")
        ):
            return True
    return False


def stack_key(item, viewer):
    """Grouping key for open-inventory cap (matches display stacks)."""
    if item is None:
        return ""
    if _item_nostack(item):
        return f"row:{id(item)}"
    catalog_id = getattr(item, "catalog_id", None)
    if catalog_id:
        return f"cat:{str(catalog_id).strip().lower()}"
    painted = hooks_mod.item_display_key(item, viewer)
    return strip_ansi(painted).strip().lower()


def item_weight(item):
    """Return optional item weight in abstract units (0 when unset).

    Reads ``item.weight`` or catalog ``weight``. Missing fields mean the
    item contributes nothing to weight math (backward compatible).
    """
    if item is None:
        return 0.0
    explicit = getattr(item, "weight", None)
    if explicit is not None:
        try:
            return max(0.0, float(explicit))
        except (TypeError, ValueError):
            pass
    catalog_id = getattr(item, "catalog_id", None)
    if catalog_id:
        spec = hooks_mod.get_item_spec(str(catalog_id))
        if isinstance(spec, dict) and spec.get("weight") is not None:
            try:
                return max(0.0, float(spec["weight"]))
            except (TypeError, ValueError):
                pass
    return 0.0


def item_volume(item):
    """Return optional item volume in abstract units (0 when unset)."""
    if item is None:
        return 0.0
    explicit = getattr(item, "volume", None)
    if explicit is not None:
        try:
            return max(0.0, float(explicit))
        except (TypeError, ValueError):
            pass
    catalog_id = getattr(item, "catalog_id", None)
    if catalog_id:
        spec = hooks_mod.get_item_spec(str(catalog_id))
        if isinstance(spec, dict) and spec.get("volume") is not None:
            try:
                return max(0.0, float(spec["volume"]))
            except (TypeError, ValueError):
                pass
    return 0.0


def _max_weight_limit(holder):
    """Optional max weight on a character or bag Item; None = unlimited."""
    if holder is None:
        return None
    raw = getattr(holder, "max_weight", None)
    if raw is None and is_bag_item(holder):
        catalog_id = getattr(holder, "catalog_id", None)
        if catalog_id:
            spec = hooks_mod.get_item_spec(str(catalog_id))
            if isinstance(spec, dict):
                raw = spec.get("max_weight")
    if raw is None:
        return None
    try:
        val = float(raw)
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def _max_volume_limit(holder):
    """Optional max volume on a character or bag Item; None = unlimited."""
    if holder is None:
        return None
    raw = getattr(holder, "max_volume", None)
    if raw is None and is_bag_item(holder):
        catalog_id = getattr(holder, "catalog_id", None)
        if catalog_id:
            spec = hooks_mod.get_item_spec(str(catalog_id))
            if isinstance(spec, dict):
                raw = spec.get("max_volume")
    if raw is None:
        return None
    try:
        val = float(raw)
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def _items_in_container(holder, viewer):
    """Every Item row carried inside ``holder`` (open inv + worn bags)."""
    if holder is None:
        return []
    if is_bag_item(holder):
        return list(bag_contents(holder))
    out = list(_surface_items(holder))
    for bag in worn_bags(holder):
        out.extend(bag_contents(bag))
    # Worn kit bags already contributed their contents above. Only add the
    # legacy virtual ``gear_bag`` list when no worn kit bag exists.
    if designated_gear_bag(holder) is None:
        gear = hooks_mod.containers_ensure_gear_bag(holder)
        out.extend(list(gear or []))
    return out


def container_weight_used(holder, viewer=None):
    """Sum of ``item_weight`` for everything inside ``holder``."""
    viewer = viewer or holder
    total = 0.0
    for piece in _items_in_container(holder, viewer):
        total += item_weight(piece)
    return total


def container_volume_used(holder, viewer=None):
    """Sum of ``item_volume`` for everything inside ``holder``."""
    viewer = viewer or holder
    total = 0.0
    for piece in _items_in_container(holder, viewer):
        total += item_volume(piece)
    return total


def weight_volume_refusal(holder, item, *, viewer=None):
    """Refusal when ``item`` would exceed weight/volume caps; else None."""
    if holder is None or item is None:
        return None
    viewer = viewer or holder
    max_w = _max_weight_limit(holder)
    if max_w is not None:
        used = container_weight_used(holder, viewer)
        if used + item_weight(item) > max_w + 1e-9:
            return WEIGHT_CAPACITY_FULL
    max_v = _max_volume_limit(holder)
    if max_v is not None:
        used = container_volume_used(holder, viewer)
        if used + item_volume(item) > max_v + 1e-9:
            return VOLUME_CAPACITY_FULL
    return None


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
    """Worn general loot/backpack Item (not kit or hip satchel), or None."""
    for slot in CONTAINER_SLOTS:
        bag = ensure_containers_map(character).get(slot)
        if bag is None:
            continue
        if is_gear_bag_item(bag):
            continue
        try:
            satchel_item = hooks_mod.containers_is_profession_bag_item(bag)
        except Exception:
            satchel_item = False
        if satchel_item:
            continue
        try:
            weave_item = hooks_mod.containers_is_weave_bag_item(bag)
        except Exception:
            weave_item = False
        if weave_item:
            continue
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
    if hooks_mod.containers_is_generic_weave_query(raw):
        return hooks_mod.containers_active_weave_bag(character)
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
    if try_merge_carried_stack(character, item, dest="loot"):
        inv.remove(item)
        return True, f"You stow {item.key} in {bag.key}."
    inv.remove(item)
    bag_contents(bag).append(item)
    consolidate_loot_bag_stacks(character)
    return True, f"You stow {item.key} in {bag.key}."


def stow_all_in_loot_bag(character, *, loot_bag=None, predicate=None):
    """Stow every stowable loose inventory row into the loot backpack.

    ``predicate(item)`` when set limits bulk stows (e.g. weapons-only).
    """
    bag = loot_bag if loot_bag is not None else designated_loot_bag(character)
    if bag is None:
        return False, NO_LOOT_BAG
    inv = getattr(character, "inventory", None) or []
    candidates = [
        piece
        for piece in list(inv)
        if not hooks_mod.containers_item_worn_on_body(character, piece)
        and not is_bag_item(piece)
    ]
    if predicate is not None:
        candidates = [piece for piece in candidates if predicate(piece)]
    if not candidates:
        if predicate is not None:
            return False, "You aren't carrying anything like that to stow."
        return False, "You aren't carrying anything to stow."
    names = []
    for piece in candidates:
        ok, _msg = stow_in_loot_bag(character, piece, loot_bag=bag)
        if ok:
            names.append(piece.key)
    if not names:
        return False, "Nothing in your inventory fits in your backpack."
    from command_support import format_item_tally

    return True, f"You stow in {bag.key}: {format_item_tally(names)}."


def stow_count_in_loot_bag(character, count, *, loot_bag=None, predicate=None):
    """Stow up to *count* loose inventory rows into the loot backpack.

    Same gates as ``stow_all_in_loot_bag``. ``predicate(item)`` limits
    which rows count (name match). Used by ``put 10 salt in backpack``.
    """
    try:
        want = int(count)
    except (TypeError, ValueError):
        want = 1
    want = max(1, want)
    bag = loot_bag if loot_bag is not None else designated_loot_bag(character)
    if bag is None:
        return False, NO_LOOT_BAG
    inv = getattr(character, "inventory", None) or []
    candidates = [
        piece
        for piece in list(inv)
        if not hooks_mod.containers_item_worn_on_body(character, piece)
        and not is_bag_item(piece)
    ]
    if predicate is not None:
        candidates = [piece for piece in candidates if predicate(piece)]
    if not candidates:
        if predicate is not None:
            return False, "You aren't carrying anything like that to stow."
        return False, "You aren't carrying anything to stow."
    names = []
    for piece in candidates[:want]:
        ok, _msg = stow_in_loot_bag(character, piece, loot_bag=bag)
        if ok:
            names.append(piece.key)
    if not names:
        return False, "Nothing in your inventory fits in your backpack."
    from command_support import format_item_tally

    return True, f"You stow in {bag.key}: {format_item_tally(names)}."


def unstow_from_loot_bag(character, item, *, loot_bag=None):
    """Move ``item`` from the loot backpack onto open inventory.

    Does not dump overflow onto the belt: ``get all from backpack`` must
    stop at the open-inventory cap instead of stuffing every row into
    hands (bug report 1280). Items that would only fit back in the bag
    stay in the bag.
    """
    if character is None or item is None:
        return False, STOW_NOT_IN_LOOT_BAG
    bag = loot_bag if loot_bag is not None else designated_loot_bag(character)
    if bag is None:
        return False, NO_LOOT_BAG
    contents = bag_contents(bag)
    if item not in contents:
        return False, STOW_NOT_IN_LOOT_BAG
    dest = placement_destination(character, item)
    if dest is None or dest == "loot":
        refusal = open_inventory_refusal(character, item)
        if dest == "loot":
            return False, OPEN_FULL
        if refusal:
            return False, refusal
        return False, OPEN_FULL
    open_merge_target = None
    if _item_stack_merge_eligible(item):
        open_merge_target = _find_matching_stack_item(
            _surface_items(character), item, character,
        )
    contents.remove(item)
    tuck = route_acquired_item(character, item)
    if _item_carried_by_character(character, item):
        if tuck:
            return True, tuck
        return True, f"You pull {item.key} from {bag.key}."
    # Folded into an existing open stack -- ``item`` is consumed, not carried.
    if open_merge_target is not None:
        if tuck:
            return True, tuck
        return True, f"You pull {item.key} from {bag.key}."
    bag_contents(bag).append(item)
    return False, OPEN_FULL


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
        contents = bag_contents(bag)
        if contents:
            return False, OPEN_FULL
        return False, "Your backpack is empty."
    from command_support import format_item_tally

    return True, f"You pull from {bag.key}: {format_item_tally(names)}."


def unstow_count_from_loot_bag(character, count, *, loot_bag=None, needle=""):
    """Pull up to *count* items from the loot backpack onto open inventory.

    ``needle`` is a name fragment (``salt``). Empty needle takes the first
    rows in bag order. Used by ``get 10 from backpack``.
    """
    try:
        want = int(count)
    except (TypeError, ValueError):
        want = 1
    want = max(1, want)
    bag = loot_bag if loot_bag is not None else designated_loot_bag(character)
    if bag is None:
        return False, NO_LOOT_BAG
    frag = (needle or "").strip()
    names = []
    for _attempt in range(want):
        if frag:
            piece = find_in_loot_bag(character, frag, loot_bag=bag)
        else:
            contents = bag_contents(bag)
            piece = contents[0] if contents else None
        if piece is None:
            break
        ok, msg = unstow_from_loot_bag(character, piece, loot_bag=bag)
        if not ok:
            if not names:
                return False, msg
            break
        names.append(piece.key)
    if not names:
        if frag:
            return False, f"You don't find that in {bag.key}."
        return False, "Your backpack is empty."
    from command_support import format_item_tally

    return True, f"You pull from {bag.key}: {format_item_tally(names)}."


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
    cap_refusal = weight_volume_refusal(bag, item, viewer=character)
    if cap_refusal:
        return cap_refusal
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
    # Handheld bags (satchel in hand, not worn) still hold real rows.
    for holder in inv:
        if not is_bag_item(holder):
            continue
        if item not in bag_contents(holder):
            continue
        if is_gear_bag_item(holder):
            return "gear"
        return "loot"
    equipment = getattr(character, "equipment", None)
    if isinstance(equipment, dict) and item in equipment.values():
        return "wielded"
    return None


def _item_carried_by_character(character, item):
    """True when ``item`` is on the character (bags, open inv, or equipment)."""
    if character is None or item is None:
        return False
    if _item_resting_place(character, item) is not None:
        return True
    equipment = getattr(character, "equipment", None)
    if isinstance(equipment, dict) and item in equipment.values():
        return True
    return False


def _ensure_open_inventory_holds(character, item):
    """Last-resort tuck into open inventory so pickups never orphan Items."""
    if character is None or item is None:
        return
    if try_merge_carried_stack(character, item):
        return
    if pickup_needs_new_open_stack(character, item):
        if open_inventory_stack_count(character) >= OPEN_INVENTORY_CAPACITY:
            return
    inv = getattr(character, "inventory", None)
    if inv is None:
        character.inventory = []
        inv = character.inventory
    if item not in inv:
        inv.append(item)


def is_stackable_reward_box(item):
    """True for locked sealed strongboxes that may share one inventory row.

    Each unit keeps its own loot table on ``loot_payloads``. Corpses, bags,
    furniture, and pit mimics never qualify (bug reports 488 / 1058 / 1288).
    Unlocked lockpicked boxes stay separate so flavor leftovers cannot
    swallow a live table (bug report 1058).
    """
    if item is None:
        return False
    if getattr(item, "is_body", False) or getattr(item, "furniture", False):
        return False
    if getattr(item, "pit_mimic", False):
        return False
    if is_bag_item(item):
        return False
    if not getattr(item, "locked", False):
        return False
    if getattr(item, "loot", None):
        return True
    payloads = getattr(item, "loot_payloads", None)
    return bool(isinstance(payloads, list) and payloads)


def _copy_loot_table(loot):
    """Shallow-copy one lockbox loot list (dicts stay dicts)."""
    out = []
    for entry in loot or []:
        if isinstance(entry, dict):
            out.append(dict(entry))
        else:
            out.append(entry)
    return out


def _reward_box_payloads(item):
    """One loot table per stacked strongbox unit."""
    n = _stack_unit_count(item)
    payloads = getattr(item, "loot_payloads", None)
    if isinstance(payloads, list) and payloads:
        out = [_copy_loot_table(p) for p in payloads]
        while len(out) < n:
            out.append(_copy_loot_table(getattr(item, "loot", None)))
        return out
    loot = _copy_loot_table(getattr(item, "loot", None))
    return [list(loot) for _ in range(max(1, n))]


def peel_open_reward_box(item):
    """Pay one stacked strongbox unit. Returns (loot_rows, consumed).

    ``consumed`` True means the Item row should leave its holder (the last
    unit, or any non-stacked container). Remaining units keep their loot.
    """
    if item is None:
        return [], True
    count = _stack_unit_count(item)
    payloads = getattr(item, "loot_payloads", None)
    stacked = count > 1 or (
        isinstance(payloads, list) and len(payloads) > 1
    )
    if not stacked:
        return list(getattr(item, "loot", None) or []), True
    pays = _reward_box_payloads(item)
    this_loot = _copy_loot_table(pays.pop(0))
    item.stack_charges = count - 1
    item.loot_payloads = pays
    item.loot = _copy_loot_table(pays[0]) if pays else []
    return this_loot, False


def _item_stack_merge_eligible(item):
    """True when duplicate pickups may fold into one open-inventory stack row.

    Reagents and gather crumbs stack. Weapons and armor never merge -- even
    when catalog metadata is thin after map spawn (bug reports 329 / 350).
    Reward containers with loot tables (locked pit strongboxes, lockpicked
    boxes, corpses) must stay separate rows -- merging folds ``stack_charges``
    onto one Item and discards the merged copy's loot (bug reports 488 / 1058).
    Sealed locked strongboxes are the exception: they stack and keep each
    unit's loot on ``loot_payloads`` so a full belt can still pick up another
    box (bug report 1288). Corpses and pit mimics never fold.
    """
    if item is None:
        return False
    if getattr(item, "is_body", False):
        return False
    if getattr(item, "pit_mimic", False):
        return False
    if getattr(item, "loot", None) or getattr(item, "loot_payloads", None):
        if not is_stackable_reward_box(item):
            return False
    # Unique quest keys (Rowena tower keys, …) must not fold by name.
    if getattr(item, "nostack", False):
        return False
    if getattr(item, "portal_tower_key", False):
        return False
    if item_carry_size(item) != "small":
        return False
    catalog_id = getattr(item, "catalog_id", None)
    slot = getattr(item, "slot", None)
    if catalog_id:
        spec = hooks_mod.get_item_spec(str(catalog_id))
        if isinstance(spec, dict):
            slot = slot or spec.get("slot")
    if slot in ("weapon", "offhand") or slot in _ARMOR_SLOTS:
        return False
    if hooks_mod.weapon_grip_for(item):
        return False
    if getattr(item, "weapon_voice", None):
        return False
    return True


def route_acquired_item(character, item):
    """Place one newly acquired item (gear bag, hands, or loot backpack).

    Idempotent when the item already sits in the right place. Returns an
    optional player-visible tuck line.
    """
    if character is None or item is None:
        return None
    hooks_mod.enrich_loaded_item(item)
    dest = placement_destination(character, item)
    if dest is None:
        # ``before_acquire_item`` should refuse first; never orphan floor loot.
        _ensure_open_inventory_holds(character, item)
        return None
    if try_merge_carried_stack(character, item, dest=dest):
        # Merged pickups must not leave a ghost row on the belt when a caller
        # pre-appended before routing (cmd_open strongbox loot, etc.).
        _remove_carried_item(character, item)
        return None
    if _item_resting_place(character, item) == dest:
        return None
    inv = getattr(character, "inventory", None)
    if inv is None:
        character.inventory = []
        inv = character.inventory
    if dest == "profession":
        bag_item = hooks_mod.containers_matching_profession_bag(character, item)
        if bag_item is None:
            _ensure_open_inventory_holds(character, item)
            return None
        if item in inv:
            inv.remove(item)
        bag_contents(bag_item).append(item)
        return (
            f"You tuck {item.key} into your satchel. Type 'satchel' to look."
        )
    if dest == "gear":
        bag_item = designated_gear_bag(character)
        if bag_item is None:
            _ensure_open_inventory_holds(character, item)
            return None
        if item in inv:
            inv.remove(item)
        bag_contents(bag_item).append(item)
        consolidate_gear_bag_stacks(character)
        return (
            f"You tuck {item.key} into your gear bag. Type 'gear' to look."
        )
    if dest == "loot":
        loot = designated_loot_bag(character)
        if loot is None:
            _ensure_open_inventory_holds(character, item)
            return None
        if item in inv:
            inv.remove(item)
        bag_contents(loot).append(item)
        consolidate_loot_bag_stacks(character)
        return f"You tuck {item.key} into {loot.key}."
    if dest == "hands":
        if item not in inv:
            inv.append(item)
    if not _item_carried_by_character(character, item):
        _ensure_open_inventory_holds(character, item)
    return None


def placement_destination(character, item):
    """Where a pickup would land: profession, gear, hands, loot, or None."""
    if character is None or item is None:
        return None
    if hooks_mod.containers_matching_profession_bag(character, item) is not None:
        return "profession"
    if hooks_mod.containers_is_gear_item(item):
        if designated_gear_bag(character) is None:
            return None
        return "gear"
    if not pickup_needs_new_open_stack(character, item):
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
        # Worn bags are already hidden from surface inventory; loose bags are
        # containers, not pocket stacks — they must not consume the open cap
        # (bug report 616: drop kit bag, hands full, cannot get it back).
        if is_bag_item(piece):
            continue
        keys.setdefault(stack_key(piece, character), True)
    return keys


def open_inventory_stack_count(character):
    """How many open-inventory stacks the character carries."""
    count = len(open_stack_keys(character))
    try:
        from engine.systems.economy import loose_cash_inventory_stacks
        count += loose_cash_inventory_stacks(character)
    except ImportError:
        pass
    return count


def open_inventory_would_add_stack(character, item):
    """True when picking up ``item`` needs a new open stack slot."""
    key = stack_key(item, character)
    if not key:
        return True
    return key not in open_stack_keys(character)


def pickup_needs_new_open_stack(character, item):
    """True when ``item`` cannot fold into an existing open-inventory row.

    Stack keys can match across weapons and armor that intentionally stay
    separate rows (bug reports 329 / 350). Those pickups still need a free
    stack slot even when ``open_inventory_would_add_stack`` is false.
    """
    if is_bag_item(item):
        return False
    if open_inventory_would_add_stack(character, item):
        return True
    return not _item_stack_merge_eligible(item)


def count_catalog_units(items, catalog_id):
    """How many units of ``catalog_id`` sit in an item list (stack-aware).

    Merged rows use ``stack_charges`` — count units, not rows. A naive
    ``len(...)`` on a 100-loaf bread stack would report 1 and a stock
    quota would never fill.
    """
    want = str(catalog_id or "").strip()
    if not want:
        return 0
    total = 0
    for piece in list(items or []):
        cid = str(getattr(piece, "catalog_id", None) or "").strip()
        if cid != want:
            continue
        total += _stack_unit_count(piece)
    return total


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
    catalog_id = getattr(item, "catalog_id", None)
    if catalog_id:
        spec = hooks_mod.get_item_spec(str(catalog_id))
        if isinstance(spec, dict) and spec.get("ammo"):
            try:
                cap = int(spec.get("stack_charges") or 0)
                if cap > 0:
                    return cap
            except (TypeError, ValueError):
                pass
    return 1


def _merge_stack_items(target, incoming):
    """Fold ``incoming`` into ``target``; ``incoming`` is discarded after."""
    t_count = _stack_unit_count(target)
    i_count = _stack_unit_count(incoming)
    t_loot = bool(
        getattr(target, "loot", None) or getattr(target, "loot_payloads", None)
    )
    i_loot = bool(
        getattr(incoming, "loot", None)
        or getattr(incoming, "loot_payloads", None)
    )
    if t_loot or i_loot:
        merged = _reward_box_payloads(target) + _reward_box_payloads(incoming)
        target.loot_payloads = merged
        target.loot = _copy_loot_table(merged[0]) if merged else []
        if getattr(incoming, "locked", False):
            target.locked = True
    target.stack_charges = t_count + i_count


def _find_matching_stack_item(items, item, viewer):
    """First row in ``items`` sharing ``item``'s stack key, or None."""
    key = stack_key(item, viewer)
    if not key:
        return None
    for piece in items or []:
        # Never fold a row into itself (``give`` pre-appends before merge).
        if piece is item:
            continue
        # A nostack / unique key row is never a merge target.
        if not _item_stack_merge_eligible(piece):
            continue
        if stack_key(piece, viewer) == key:
            return piece
    return None


def _remove_carried_item(character, item):
    """Drop ``item`` from open inventory, bags, gear bag, and wielded slots.

    A piece can sit in inventory *and* a weapon slot after grant_and_equip;
    peel/destroy must clear every copy or eat leaves a ghost in hand.
    """
    if character is None or item is None:
        return
    inv = getattr(character, "inventory", None) or []
    if item in inv:
        inv.remove(item)
    for bag in worn_bags(character):
        contents = bag_contents(bag)
        if item in contents:
            contents.remove(item)
    gear = hooks_mod.containers_ensure_gear_bag(character)
    if item in gear:
        gear.remove(item)
    equipment = getattr(character, "equipment", None)
    if isinstance(equipment, dict):
        for slot, held in list(equipment.items()):
            if held is item:
                equipment.pop(slot, None)
                setattr(item, "equipped", False)


def _plain_carried_item_text(item, attr, fallback):
    """Return a safe plain string for stack peels and persistence."""
    val = getattr(item, attr, None)
    if isinstance(val, str):
        text = val.strip()
        if text and not (
            text.startswith("<") and " object at 0x" in text
        ):
            return text
    if attr == "key":
        painted = hooks_mod.item_display_key(item, None)
        text = strip_ansi(painted).strip() if painted else ""
        if text and not (text.startswith("<") and " object at 0x" in text):
            return text
    if attr == "description":
        desc = getattr(item, "description", None)
        if isinstance(desc, str):
            text = desc.strip()
            if text and not (
                text.startswith("<") and " object at 0x" in text
            ):
                return text
    return fallback


def _spawn_single_stack_unit(item):
    """Fresh one-unit Item cloned from a stacked row (sell/drop peel)."""
    catalog_id = getattr(item, "catalog_id", None)
    if catalog_id:
        unit = hooks_mod.make_world_item(
            {"item": str(catalog_id).strip()},
            where="stack peel",
        )
        hooks_mod.enrich_loaded_item(unit)
        if getattr(item, "stack_charges", None) is not None:
            unit.stack_charges = 1
        return unit
    from world import Item

    plain_key = _plain_carried_item_text(item, "key", "an unknown scrap")
    plain_desc = _plain_carried_item_text(
        item, "description", plain_key,
    )
    unit = Item(plain_key, plain_desc)
    for attr in (
        "catalog_id",
        "slot",
        "equipped",
        "need",
        "aliases",
        "weapon_voice",
        "grip",
        "relic",
        "relic_tier",
        "color",
    ):
        if hasattr(item, attr):
            val = getattr(item, attr)
            if val is not None:
                setattr(unit, attr, val)
    unit.stack_charges = 1
    return unit


def return_carried_unit(character, item):
    """Put a peeled unit back after a failed sell or payout.

    Prefers merging into an existing stack (hands or worn bags). If nothing
    matches, appends to open inventory even when the stack cap is full --
    destroying the ware is worse than a temporary overflow.
    """
    if character is None or item is None:
        return
    if try_merge_carried_stack(character, item):
        return
    inv = getattr(character, "inventory", None)
    if inv is None:
        character.inventory = []
        inv = character.inventory
    if item not in inv:
        inv.append(item)


def peel_one_carried_unit(character, item):
    """Remove one logical unit from a carried row; return the detached Item.

    When ``stack_charges`` is greater than one, decrement the source row and
    return a single-unit clone so ``sell`` / ``drop`` / ``give`` only move
    one piece (bug report 329: merged equipment must not vanish in one verb).
    """
    if character is None or item is None:
        return None
    if _item_resting_place(character, item) is None:
        return None
    count = _stack_unit_count(item)
    if count <= 1:
        _remove_carried_item(character, item)
        return item
    pays = None
    if getattr(item, "loot", None) or getattr(item, "loot_payloads", None):
        pays = _reward_box_payloads(item)
    item.stack_charges = count - 1
    unit = _spawn_single_stack_unit(item)
    if pays:
        unit.loot = _copy_loot_table(pays.pop(0))
        unit.locked = bool(getattr(item, "locked", False))
        unit.loot_payloads = None
        item.loot_payloads = pays
        item.loot = _copy_loot_table(pays[0]) if pays else []
    return unit


def try_merge_carried_stack(character, item, *, dest=None):
    """Merge a duplicate-stack pickup into an existing row.

    Returns True when ``item`` was absorbed (caller must not append a new
    row). Used by ``get``, autoloot, and loot routing so the open stack cap
    cannot be bypassed by duplicate catalog ids.

    Equipment and other medium/large pieces stay separate rows so each copy
    can be sold or dropped on its own (bug report 329).
    """
    if character is None or item is None:
        return False
    if not _item_stack_merge_eligible(item):
        return False
    if dest is None:
        dest = placement_destination(character, item)
    # Open-inventory slot math applies only to surface ``hands`` routing.
    # Gear and loot bags merge against their own contents — otherwise every
    # reagent/ammo pickup spawns a new row (Dean's kit bag bloat).
    if dest not in ("gear", "loot", "profession") and open_inventory_would_add_stack(
        character, item,
    ):
        return False
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
    if dest == "profession":
        bag = hooks_mod.containers_matching_profession_bag(character, item)
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
    if is_bag_item(item):
        cap_refusal = weight_volume_refusal(character, item, viewer=character)
        if cap_refusal:
            return cap_refusal
        return None
    cap_refusal = weight_volume_refusal(character, item, viewer=character)
    if cap_refusal:
        return cap_refusal
    if placement_destination(character, item) is not None:
        return None
    if item_carry_size(item) == "large":
        if (
            pickup_needs_new_open_stack(character, item)
            and open_inventory_stack_count(character) >= OPEN_INVENTORY_CAPACITY
        ):
            return LOOT_BAG_HANDS_FULL
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


def resolve_worn_bag_slot(character, bag_item):
    """Return ``back`` or ``shoulder`` for a worn bag Item.

    Prefer ``container_worn``; fall back to the live containers map when
    strip/heal cleared the flag but left a stale slot ref (bug report 602).
    Syncs ``container_worn`` when the map is authoritative.
    """
    if character is None or bag_item is None:
        return None
    worn = getattr(bag_item, "container_worn", None)
    if worn in CONTAINER_SLOTS:
        return worn
    containers = ensure_containers_map(character)
    for slot in CONTAINER_SLOTS:
        if containers.get(slot) is bag_item:
            bag_item.container_worn = slot
            return slot
    return None


def designated_gear_bag(character):
    """Worn gear-bag Item, or None."""
    for slot in CONTAINER_SLOTS:
        bag = ensure_containers_map(character).get(slot)
        if bag is not None and is_gear_bag_item(bag):
            return bag
    return None


def designated_profession_bag(character):
    """Worn profession satchel, or None.

    Game policy (which items count as satchels) lives on the hook
    ``containers_is_profession_bag_item`` so this engine helper never
    imports SUPERS. Prefers the hip slot, then any other worn bag slot.
    """
    containers = ensure_containers_map(character)
    hip = containers.get(HIP_SLOT)
    if hip is not None and hooks_mod.containers_is_profession_bag_item(hip):
        return hip
    for slot in CONTAINER_SLOTS:
        bag = containers.get(slot)
        if bag is not None and hooks_mod.containers_is_profession_bag_item(bag):
            return bag
    return None


def worn_bags(character):
    """List of worn bag Items (back, shoulder, then hip)."""
    out = []
    for slot in CONTAINER_SLOTS:
        bag = ensure_containers_map(character).get(slot)
        if bag is not None:
            out.append(bag)
    return out


def iter_carried_items(character):
    """Yield open inventory, worn bag contents, and virtual gear-bag rows.

    When a kit bag is worn, ``containers_ensure_gear_bag`` returns that
    bag's contents — the same list already yielded from ``worn_bags``.
    Skip the third pass in that case so haul-weight math does not
    double-count salt, ammo, and other kit rows.
    """
    for item in list(getattr(character, "inventory", None) or []):
        yield item
    for bag in worn_bags(character):
        for item in bag_contents(bag):
            yield item
    if designated_gear_bag(character) is None:
        for item in hooks_mod.containers_ensure_gear_bag(character):
            yield item


def make_starter_kit_bag(where="kit"):
    """Catalog starter kit bag Item."""
    return hooks_mod.make_world_item(
        {"item": STARTER_KIT_BAG_ID},
        where=where,
    )


def wear_bag(character, bag_item, slot):
    """Wear ``bag_item`` on ``slot`` ('back', 'shoulder', or 'hip').

    Profession satchels belong on hip. Kit bags stay back/shoulder.
    Permanent bound-pocket bags may use any of the three slots.

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
    is_satchel = hooks_mod.containers_is_profession_bag_item(bag_item)
    is_extradim = hooks_mod.containers_is_extradim_bag_item(bag_item)
    if is_satchel and slot != HIP_SLOT and not is_extradim:
        return False, (
            "Profession satchels sling on your hip. Try 'wear <satchel>' "
            "or 'restring hip' (help satchels)."
        )
    if is_gear_bag_item(bag_item) and slot == HIP_SLOT:
        return False, (
            "Kit bags stay on your back or shoulder (help gear)."
        )
    containers = ensure_containers_map(character)
    # Only one gear bag per character.
    if is_gear_bag_item(bag_item):
        other = designated_gear_bag(character)
        if other is not None and other is not bag_item:
            return False, "You already wear a kit bag -- remove it first."
    if is_satchel:
        other = designated_profession_bag(character)
        if other is not None and other is not bag_item:
            return False, "You already wear a satchel -- remove it first."
    occupied = containers.get(slot)
    if occupied is not None and occupied is not bag_item:
        return False, BAG_SLOT_FULL
    # Clear old slot if this bag was worn elsewhere.
    for name in CONTAINER_SLOTS:
        if containers.get(name) is bag_item:
            containers[name] = None
    containers[slot] = bag_item
    bag_item.container_worn = slot
    if slot == HIP_SLOT:
        return True, f"You sling {bag_item.key} at your hip."
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
    """Restring a worn bag between back, shoulder, and hip."""
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
    containers = empty_containers_map()
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
    try:
        from engine.systems import wallet_container as wallet_mod

        wallet_mod.rebind_wallet_from_inventory(character)
    except ImportError:
        pass


def grant_and_wear_starter_kit_bag(character, *, where="kit"):
    """Create starter kit bag, add to inventory, wear on back (or shoulder)."""
    if character is None:
        return None
    existing = designated_gear_bag(character)
    if existing is not None:
        migrate_virtual_gear_bag(character)
        return existing
    inv = getattr(character, "inventory", None)
    if inv is None:
        character.inventory = []
        inv = character.inventory
    # Reuse an unworn spare before minting another empty bag (bug report 867:
    # scavenger kit grants only checked the worn slot, spamming floor drops).
    bag = None
    for item in inv:
        if is_gear_bag_item(item):
            bag = item
            break
    if bag is None:
        bag = make_starter_kit_bag(where=where)
        if bag is None:
            return None
        inv.append(bag)
    for slot in ("back", "shoulder"):
        ok, _msg = wear_bag(character, bag, slot)
        if ok:
            break
    migrate_virtual_gear_bag(character)
    collapse_duplicate_kit_bags(character)
    rebind_containers_from_inventory(character)
    return designated_gear_bag(character) or bag


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


def collapse_duplicate_kit_bags(character):
    """Keep one kit bag (prefer worn); merge duplicate bag contents in place."""
    if character is None:
        return 0
    inv = getattr(character, "inventory", None)
    if not inv:
        return 0
    extras = [item for item in list(inv) if is_gear_bag_item(item)]
    if not extras:
        return 0
    primary = designated_gear_bag(character)
    if primary is None:
        primary = extras[0]
        wear_bag(character, primary, "back")
    removed = 0
    containers = ensure_containers_map(character)
    for bag in extras:
        if bag is primary:
            continue
        for piece in list(bag_contents(bag)):
            bag_contents(primary).append(piece)
        if bag in inv:
            inv.remove(bag)
        for slot in CONTAINER_SLOTS:
            if containers.get(slot) is bag:
                containers[slot] = None
        removed += 1
    if removed:
        rebind_containers_from_inventory(character)
        consolidate_gear_bag_stacks(character)
    return removed


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
    collapse_duplicate_kit_bags(character)
    rebind_containers_from_inventory(character)
    bag_item = designated_gear_bag(character)
    if bag_item is not None:
        for piece in list(bag_contents(bag_item)):
            hooks_mod.enrich_loaded_item(piece)
    for piece in list(getattr(character, "gear_bag", None) or []):
        hooks_mod.enrich_loaded_item(piece)
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


def _consolidate_stackable_item_list(items, viewer):
    """Fold duplicate stack-eligible rows in ``items`` (mutates in place).

    Returns how many redundant rows were absorbed. Idempotent when already
    tidy. Ammo boxes sum ``stack_charges`` into one row per catalog id.
    """
    if not items or len(items) < 2:
        return 0
    groups = OrderedDict()
    passthrough = []
    for piece in list(items):
        if _item_stack_merge_eligible(piece):
            groups.setdefault(stack_key(piece, viewer), []).append(piece)
        else:
            passthrough.append(piece)
    merged = 0
    rebuilt = list(passthrough)
    for pieces in groups.values():
        if len(pieces) == 1:
            rebuilt.append(pieces[0])
            continue
        sample = pieces[0]
        catalog_id = getattr(sample, "catalog_id", None)
        spec = None
        if catalog_id:
            spec = hooks_mod.get_item_spec(str(catalog_id))
        if isinstance(spec, dict) and spec.get("ammo") and catalog_id:
            merged_item = hooks_mod.containers_consolidate_ammo_stack(
                pieces, str(catalog_id),
            )
            if merged_item is not None:
                rebuilt.append(merged_item)
                merged += len(pieces) - 1
                continue
        target = pieces[0]
        for incoming in pieces[1:]:
            _merge_stack_items(target, incoming)
            merged += 1
        rebuilt.append(target)
    if merged <= 0:
        return 0
    items[:] = rebuilt
    return merged


def consolidate_gear_bag_stacks(character):
    """Fold duplicate stack-eligible rows inside the worn kit bag."""
    if character is None:
        return 0
    bag_item = designated_gear_bag(character)
    if bag_item is None:
        return 0
    return _consolidate_stackable_item_list(
        bag_contents(bag_item), character,
    )


def consolidate_loot_bag_stacks(character):
    """Fold duplicate stack-eligible rows inside the worn loot backpack."""
    if character is None:
        return 0
    bag_item = designated_loot_bag(character)
    if bag_item is None:
        return 0
    return _consolidate_stackable_item_list(
        bag_contents(bag_item), character,
    )


def consolidate_home_stash_stacks(character):
    """Fold duplicate stack-eligible rows in the claimed-home stash."""
    if character is None:
        return 0
    return _consolidate_stackable_item_list(
        ensure_home_stash(character), character,
    )


def consolidate_item_list_stacks(items, viewer):
    """Public wrapper for any mutable item list (pit stash, chest, …)."""
    if items is None:
        return 0
    return _consolidate_stackable_item_list(items, viewer)


def heal_all_gear_bag_stack_consolidation(game):
    """One-time boot sweep: collapse duplicate gear-bag reagent/ammo rows."""
    from engine.char_index import iter_characters

    total = 0
    for char in iter_characters(game):
        total += consolidate_gear_bag_stacks(char)
    try:
        total += hooks_mod.containers_heal_folded_gear_bag_stacks(game)
    except Exception:
        pass
    return total


def _stash_location_ok(character, game, *, action="stash"):
    """True when the actor is at claimed home, or named-archangel remote."""
    room = getattr(character, "location", None)
    if hooks_mod.containers_room_is_character_home(character, room, game):
        return True, ""
    return hooks_mod.containers_remote_stash_gate(character, action)


def _persist_touch_property_stash(character, game=None):
    """Queue property-stash sidecar writes after a player stash mutation."""
    game = game or getattr(character, "game", None)
    if game is None or character is None:
        return
    hooks_mod.mark_property_stash_heavy_dirty(
        game, character, allow_empty_write=True,
    )


def stash_at_home(character, item, game):
    """Move one carried item into ``character.home_stash`` at home."""
    if character is None or item is None:
        return False, "You aren't carrying that."
    ok, msg = _stash_location_ok(character, game, action="stash")
    if not ok:
        return False, msg or "You can only stash things at your claimed home."
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
    consolidate_home_stash_stacks(character)
    _persist_touch_property_stash(character, game)
    return True, f"You stash {item.key} at home."


def retrieve_from_stash(character, needle, game):
    """Pull one item from home stash into open inventory (cap-checked)."""
    from command_support import _find_item

    ok, msg = _stash_location_ok(character, game, action="retrieve")
    if not ok:
        return False, msg or "You can only retrieve stash at your claimed home."
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
    _persist_touch_property_stash(character, game)
    return True, msg


def stash_list_lines(character):
    """Lines for ``stash list`` / bare ``stash``."""
    stash = ensure_home_stash(character)
    consolidate_home_stash_stacks(character)
    lines = ["Home stash (claimed residence only)."]
    if not stash:
        lines.append("Loose pile: empty.")
    else:
        lines.append("Loose pile:")
        lines.extend(hooks_mod.containers_stacked_carry_lines(stash, character))
    boxes = ensure_home_boxes(character)
    if not boxes:
        lines.append(
            "No labeled crates yet. Type stash label <name> to start one "
            "(help stash)."
        )
        return lines
    lines.append(f"Labeled crates ({len(boxes)}/{MAX_HOME_BOXES}):")
    for box in boxes:
        label = box_label(box)
        count = len(box_contents(box))
        lines.append(f"  {label} -- {count} item(s)")
    lines.append("Type stash list <crate> to look inside one crate.")
    return lines


# ---------------------------------------------------------------------------
# Labeled home crates + important-tag routing (Slice 3+ leftover)
# ---------------------------------------------------------------------------

MAX_HOME_BOXES = 8
HOME_BOX_CAPACITY = 100


def ensure_home_boxes(character):
    """Mutable list of labeled crate dicts on the character."""
    if character is None:
        return []
    boxes = getattr(character, "home_boxes", None)
    if boxes is None or not isinstance(boxes, list):
        character.home_boxes = []
        return character.home_boxes
    # Mutate in place -- callers may keep this list and append after a
    # find_home_box pass, which also calls ensure. Replacing the list
    # would drop those appends.
    kept = []
    for raw in list(boxes):
        if not isinstance(raw, dict):
            continue
        raw.setdefault("key", f"box_{len(kept) + 1}")
        raw.setdefault("label", "crate")
        contents = raw.get("contents")
        if contents is None or not isinstance(contents, list):
            raw["contents"] = []
        kept.append(raw)
    boxes[:] = kept
    return boxes


def box_label(box):
    """Player-facing crate name."""
    if not isinstance(box, dict):
        return "crate"
    text = str(box.get("label") or "crate").strip()
    return text or "crate"


def box_contents(box):
    """Mutable item list inside one crate dict."""
    if not isinstance(box, dict):
        return []
    contents = box.get("contents")
    if contents is None or not isinstance(contents, list):
        box["contents"] = []
    return box["contents"]


def _fold_box_label(text):
    """Fold spaces and hyphens so players can type crate names aloud."""
    raw = " ".join(str(text or "").strip().lower().split())
    return raw.replace("-", " ")


def find_home_box(character, needle):
    """Match a crate by label (spaces and hyphens fold the same)."""
    want = _fold_box_label(needle)
    if not want:
        return None
    for box in ensure_home_boxes(character):
        if _fold_box_label(box_label(box)) == want:
            return box
    for box in ensure_home_boxes(character):
        label = _fold_box_label(box_label(box))
        if label.startswith(want) or want in label.split():
            return box
    return None


def create_home_box(character, label):
    """Start a new labeled crate. Returns (ok, message, box)."""
    text = " ".join(str(label or "").strip().split())
    if not text:
        return False, "Name the crate -- try stash label hunt kit.", None
    if len(text) > 24:
        return False, "Crate names stay short (24 letters or fewer).", None
    boxes = ensure_home_boxes(character)
    if find_home_box(character, text) is not None:
        return False, f"You already have a crate labeled {text}.", None
    if len(boxes) >= MAX_HOME_BOXES:
        return False, (
            f"You only have room for {MAX_HOME_BOXES} labeled crates. "
            "Empty and drop one before starting another."
        ), None
    box = {
        "key": f"box_{len(boxes) + 1}",
        "label": text,
        "contents": [],
    }
    boxes.append(box)
    _persist_touch_property_stash(character, getattr(character, "game", None))
    return True, f"You set aside a crate and label it {text}.", box


def relabel_home_box(character, needle, new_label):
    """Rename an existing crate."""
    box = find_home_box(character, needle)
    if box is None:
        return False, "You do not have a crate by that name."
    text = " ".join(str(new_label or "").strip().split())
    if not text:
        return False, "Rename it to what?"
    if len(text) > 24:
        return False, "Crate names stay short (24 letters or fewer)."
    other = find_home_box(character, text)
    if other is not None and other is not box:
        return False, f"You already have a crate labeled {text}."
    old = box_label(box)
    box["label"] = text
    _persist_touch_property_stash(character, getattr(character, "game", None))
    return True, f"You scratch out {old} and write {text}."


def is_important_item(item):
    """True when the player tagged this piece to send home first."""
    return bool(item is not None and getattr(item, "important", False))


def set_item_important(item, flagged):
    """Stamp or clear the important tag on a live Item."""
    if item is None:
        return False
    item.important = bool(flagged)
    return True


def _iter_markable_items(character):
    """Carried, bagged, equipped, and loose-stash pieces the player can tag."""
    seen = []
    for piece in list(getattr(character, "inventory", None) or []):
        if piece is not None and piece not in seen:
            seen.append(piece)
            yield piece
    for bag in worn_bags(character):
        for piece in list(bag_contents(bag)):
            if piece is not None and piece not in seen:
                seen.append(piece)
                yield piece
    equipment = getattr(character, "equipment", None) or {}
    if isinstance(equipment, dict):
        for piece in equipment.values():
            if piece is not None and piece not in seen:
                seen.append(piece)
                yield piece
    for piece in list(ensure_home_stash(character)):
        if piece is not None and piece not in seen:
            seen.append(piece)
            yield piece
    for box in ensure_home_boxes(character):
        for piece in list(box_contents(box)):
            if piece is not None and piece not in seen:
                seen.append(piece)
                yield piece


def find_markable_item(character, needle):
    """Resolve a mark/unmark target from carry, bags, wear, or home crates."""
    from command_support import _find_item

    pool = list(_iter_markable_items(character))
    return _find_item(needle, pool, character=character)


def mark_item_important(character, needle, flagged=True):
    """Tag or untag a piece the player can currently reach."""
    item = find_markable_item(character, needle)
    if item is None:
        return False, "You do not have that to mark."
    set_item_important(item, flagged)
    if flagged:
        return True, f"You mark {item.key} as important -- stash important sends it home first."
    return True, f"You clear the important mark from {item.key}."


def stash_in_box(character, item, box, game):
    """Move one carried or loose-stash item into a labeled crate."""
    if character is None or item is None or box is None:
        return False, "You aren't carrying that."
    ok, msg = _stash_location_ok(character, game, action="stash")
    if not ok:
        return False, msg or "You can only stash things at your claimed home."
    refuse = hooks_mod.containers_on_body_carry_refusal(character, item)
    if refuse:
        return False, refuse
    if getattr(item, "container_worn", None):
        return False, "Remove the bag from your shoulder first."
    contents = box_contents(box)
    if len(contents) >= HOME_BOX_CAPACITY:
        return False, f"The {box_label(box)} crate is full."
    removed = False
    inv = getattr(character, "inventory", None) or []
    if item in inv:
        inv.remove(item)
        removed = True
    if not removed:
        for bag in worn_bags(character):
            if is_gear_bag_item(bag):
                continue
            bag_list = bag_contents(bag)
            if item in bag_list:
                bag_list.remove(item)
                removed = True
                break
    if not removed:
        loose = ensure_home_stash(character)
        if item in loose:
            loose.remove(item)
            removed = True
    if not removed:
        bag_list = hooks_mod.containers_ensure_gear_bag(character)
        if item in bag_list:
            bag_list.remove(item)
            removed = True
    if not removed:
        return False, "You aren't carrying that."
    contents.append(item)
    consolidate_item_list_stacks(contents, character)
    _persist_touch_property_stash(character, game)
    return True, f"You tuck {item.key} into the {box_label(box)} crate."


def retrieve_from_box(character, needle, box, game):
    """Pull one item from a labeled crate into open inventory."""
    from command_support import _find_item

    ok, msg = _stash_location_ok(character, game, action="retrieve")
    if not ok:
        return False, msg or "You can only retrieve stash at your claimed home."
    contents = box_contents(box)
    item = _find_item(needle, contents)
    if item is None:
        return False, f"That is not in the {box_label(box)} crate."
    refusal = acquire_refusal(character, item)
    if refusal:
        return False, refusal
    contents.remove(item)
    inv = getattr(character, "inventory", None)
    if inv is None:
        character.inventory = []
        inv = character.inventory
    inv.append(item)
    from engine import hooks
    stow_msg = hooks.after_acquire_item(character, item)
    msg = f"You retrieve {item.key} from the {box_label(box)} crate."
    if stow_msg:
        msg = f"{msg} {stow_msg}"
    _persist_touch_property_stash(character, game)
    return True, msg


def box_list_lines(character, box):
    """Lines for ``stash list <crate>``."""
    consolidate_item_list_stacks(box_contents(box), character)
    contents = box_contents(box)
    lines = [f"Crate: {box_label(box)} ({len(contents)}/{HOME_BOX_CAPACITY})."]
    if not contents:
        lines.append("Empty.")
        return lines
    lines.append("Contents:")
    lines.extend(hooks_mod.containers_stacked_carry_lines(contents, character))
    return lines


def stash_important_at_home(character, game):
    """Send tagged pieces from hands and loot bag into the loose home pile."""
    ok, msg = _stash_location_ok(character, game, action="stash")
    if not ok:
        return False, msg or "You can only stash things at your claimed home."
    moved = 0
    skipped = 0
    candidates = []
    for piece in list(getattr(character, "inventory", None) or []):
        if is_important_item(piece):
            candidates.append(piece)
    for bag in worn_bags(character):
        if is_gear_bag_item(bag):
            continue
        for piece in list(bag_contents(bag)):
            if is_important_item(piece):
                candidates.append(piece)
    for piece in candidates:
        refuse = hooks_mod.containers_on_body_carry_refusal(character, piece)
        if refuse:
            skipped += 1
            continue
        ok, _msg = stash_at_home(character, piece, game)
        if ok:
            moved += 1
        else:
            skipped += 1
    if moved and skipped:
        return True, (
            f"You stash {moved} important thing(s) at home "
            f"({skipped} still on you -- unequip or unstow first)."
        )
    if moved:
        return True, f"You stash {moved} important thing(s) at home."
    if skipped:
        return False, (
            "Those important pieces are still on your body. "
            "Unequip or unstow them, then try stash important again."
        )
    return False, "Nothing marked important is loose in your hands or backpack."
