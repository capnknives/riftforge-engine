"""wearables.py -- the engine's generic stacked clothing-layer map.

Combat gear is one piece per slot on ``character.equipment``. Cosmetic
clothes are N layers per slot on ``character.clothing`` (innermost →
outermost). That stacked-map mechanism -- not any particular game's
garment catalog, restring tags, or immersion outfits -- is what lives
here.

SUPERS' ``supers/clothing.py`` keeps catalog stamp/validate, restring,
outfits, look lines, and thin wrappers that inject
``is_clothing`` / ``slot_for`` / ``display_key`` callbacks
(docs/plans/two_repo_purity.md Phase 7 Stage 9). Item persistence fields
(``layer``, ``worn``, ``worn_order``, …) already round-trip in
``engine/persistence.py``.

Pure attribute + list math: zero ``supers`` imports.
"""

from __future__ import annotations

# Classic wear slots that may hold clothing. Weapon / shield stay combat-only.
CLOTHING_SLOTS = frozenset({
    "head",
    "body",
    "arms",
    "hands",
    "legs",
    "feet",
    "about",
    "neck",
    "finger",
    "waist",
})

# Stable look order (top-down, matching worn gear display).
CLOTHING_LOOK_ORDER = (
    "head", "about", "neck", "body", "arms", "hands",
    "finger", "waist", "legs", "feet",
)

# Soft cap so a slot cannot grow forever (innermost → outermost).
MAX_LAYERS_PER_SLOT = 6


def ensure_clothing_map(character):
    """Return ``character.clothing`` dict, creating an empty map if needed.

    Values are ordered lists (innermost → outermost), never a lone Item.
    Migrates any legacy single-Item slot values into one-element lists.
    """
    clothing = getattr(character, "clothing", None)
    if not isinstance(clothing, dict):
        character.clothing = {}
        clothing = character.clothing
    for slot, value in list(clothing.items()):
        if value is None:
            clothing.pop(slot, None)
        elif not isinstance(value, list):
            clothing[slot] = [value]
    return clothing


def slot_stack(character, slot):
    """Return the live layer list for a slot (may be empty)."""
    clothing = ensure_clothing_map(character)
    stack = clothing.get(slot)
    if not isinstance(stack, list):
        stack = []
        clothing[slot] = stack
    return stack


def renumber_stack(stack):
    """Stamp ``worn_order`` 0..n-1 (innermost → outermost) on a slot stack."""
    for i, piece in enumerate(stack):
        if piece is not None:
            piece.worn_order = i


def detach_from_stacks(character, piece):
    """Remove ``piece`` from every clothing stack (no-op if not worn)."""
    clothing = ensure_clothing_map(character)
    for slot, stack in list(clothing.items()):
        if not isinstance(stack, list):
            continue
        if piece in stack:
            stack[:] = [p for p in stack if p is not piece]
            renumber_stack(stack)
            if not stack:
                clothing.pop(slot, None)


def iter_worn_clothing(character):
    """Yield ``(slot, piece)`` for every worn clothing piece (inner→outer)."""
    clothing = ensure_clothing_map(character)
    for slot in CLOTHING_LOOK_ORDER:
        stack = clothing.get(slot) or []
        if not isinstance(stack, list):
            continue
        for piece in stack:
            if piece is not None:
                yield slot, piece


def wear_piece(
    character, piece, *, under=False,
    is_clothing=None, slot_for=None, display_key=None,
):
    """Stack a clothing item onto its slot. Returns (ok, message).

    ``is_clothing(piece)``, ``slot_for(piece)``, and
    ``display_key(piece, character)`` are injected by the game package
    (catalog-aware). By default the piece becomes the outermost layer;
    ``under=True`` slides it in as innermost.
    """
    if is_clothing is None or slot_for is None or display_key is None:
        raise TypeError(
            "wear_piece requires is_clothing, slot_for, and display_key"
        )
    if character is None or piece is None:
        return False, "Wear what?"
    if not is_clothing(piece):
        shown = display_key(piece, character)
        return False, (
            f"{shown} is not clothing -- use 'equip' for armor and weapons."
        )
    slot = slot_for(piece)
    if slot is None:
        shown = display_key(piece, character)
        return False, f"{shown} isn't something you can wear."
    inv = list(getattr(character, "inventory", ()) or ())
    if piece not in inv:
        return False, "You don't have that."
    stack = slot_stack(character, slot)
    if piece in stack:
        shown = display_key(piece, character)
        return True, f"You are already wearing {shown}."
    detach_from_stacks(character, piece)
    stack = slot_stack(character, slot)
    if len(stack) >= MAX_LAYERS_PER_SLOT:
        return False, (
            f"Your {slot} clothing is already {MAX_LAYERS_PER_SLOT} layers "
            "deep -- remove something first."
        )
    if under:
        stack.insert(0, piece)
    else:
        stack.append(piece)
    renumber_stack(stack)
    piece.worn = True
    if getattr(piece, "equipped", False):
        piece.equipped = False
        equipment = getattr(character, "equipment", None)
        if isinstance(equipment, dict):
            for s, p in list(equipment.items()):
                if p is piece:
                    equipment.pop(s, None)
    shown = display_key(piece, character)
    depth = len(stack)
    if depth == 1:
        return True, f"You wear {shown}."
    where = "under" if under else "over"
    return True, f"You wear {shown} ({where} other {slot} layers; {depth} deep)."


def remove_piece(
    character, args, *,
    is_clothing=None, slot_for=None, display_key=None, find_item=None,
):
    """Remove worn clothing by item name or slot.

    ``remove <slot>`` peels the outermost layer only. Returns
    (ok, message, piece). Injected callables match ``wear_piece``;
    ``find_item(name, candidates)`` resolves by substring.
    """
    if (
        is_clothing is None or slot_for is None
        or display_key is None or find_item is None
    ):
        raise TypeError(
            "remove_piece requires is_clothing, slot_for, display_key, "
            "and find_item"
        )
    raw = (args or "").strip().lower()
    if not raw:
        return False, "Remove what?", None
    clothing = ensure_clothing_map(character)
    piece = None
    slot = None
    if raw in CLOTHING_SLOTS:
        slot = raw
        stack = clothing.get(slot) or []
        if isinstance(stack, list) and stack:
            piece = stack[-1]
    else:
        worn_bits = [
            p for p in (getattr(character, "inventory", ()) or ())
            if getattr(p, "worn", False) and is_clothing(p)
        ]
        for s, p in iter_worn_clothing(character):
            if p not in worn_bits:
                worn_bits.append(p)
        piece = find_item(args.strip(), worn_bits)
        if piece is not None:
            for s, stack in list(clothing.items()):
                if isinstance(stack, list) and piece in stack:
                    slot = s
                    break
            if slot is None:
                slot = slot_for(piece)
    if piece is None or slot is None:
        return False, "You aren't wearing that.", None
    stack = clothing.get(slot) or []
    if isinstance(stack, list) and piece in stack:
        stack.remove(piece)
        renumber_stack(stack)
        if not stack:
            clothing.pop(slot, None)
    setattr(piece, "worn", False)
    if hasattr(piece, "worn_order"):
        piece.worn_order = None
    shown = display_key(piece, character)
    return True, f"You remove {shown}.", piece


def rebind_clothing_from_inventory(
    character, *, is_clothing=None, slot_for=None, stamp_defaults=None,
):
    """Rebuild ``character.clothing`` stacks from inventory Items flagged worn.

    Within each slot, pieces sort by ``worn_order`` (innermost first).
    ``stamp_defaults(piece)`` is optional (catalog enrich); callables
    ``is_clothing`` / ``slot_for`` are required.
    """
    if is_clothing is None or slot_for is None:
        raise TypeError(
            "rebind_clothing_from_inventory requires is_clothing and slot_for"
        )
    if character is None:
        return
    buckets = {slot: [] for slot in CLOTHING_SLOTS}
    seq = 0
    for piece in list(getattr(character, "inventory", None) or []):
        if stamp_defaults is not None:
            stamp_defaults(piece)
        if not getattr(piece, "worn", False):
            continue
        if not is_clothing(piece):
            continue
        slot = slot_for(piece)
        if slot not in CLOTHING_SLOTS:
            continue
        order = getattr(piece, "worn_order", None)
        try:
            order_key = int(order) if order is not None else seq
        except (TypeError, ValueError):
            order_key = seq
        buckets[slot].append((order_key, seq, piece))
        seq += 1
    clothing = {}
    for slot, entries in buckets.items():
        if not entries:
            continue
        entries.sort(key=lambda triple: (triple[0], triple[1]))
        stack = [piece for _, _, piece in entries[:MAX_LAYERS_PER_SLOT]]
        renumber_stack(stack)
        clothing[slot] = stack
    character.clothing = clothing
