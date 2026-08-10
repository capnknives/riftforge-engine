"""
wallet_container.py -- worn pocket wallet (cash + small papers).

Mirrors the gear-bag pattern: one leather wallet worn in a pants pocket
holds on-hand dollars/cents and stowed ID-sized items. Town-bank savings
stay in the bank account (``bank_dollars/cents`` on the character blob).
Without a worn wallet, pocket cash is loose and uses one open-inventory
stack. Stdlib only; no supers imports.
"""

from __future__ import annotations

from engine import hooks as hooks_mod

WALLET_SLOT = "pocket"
STARTER_WALLET_ID = "leather_wallet"
DEFAULT_WALLET_CAPACITY = 8

NO_WALLET = (
    "You need a wallet in your pants pocket for that "
    "(see 'help wallet')."
)
WALLET_FULL = "Your wallet is full."
WALLET_NOT_WORN = "You aren't carrying that wallet in your pocket."
STOW_NOT_IN_WALLET = "That isn't in your wallet."
STOW_NOT_WALLET_ITEM = "That doesn't fit in a wallet."


def ensure_containers_map(character):
    """Back/shoulder bags plus optional pocket wallet slot."""
    from engine.systems.containers import ensure_containers_map as _base

    containers = _base(character)
    containers.setdefault(WALLET_SLOT, None)
    return containers


def is_wallet_item(item):
    """True when ``item`` is a pocket wallet container."""
    if item is None:
        return False
    if getattr(item, "is_wallet", False):
        return True
    catalog_id = getattr(item, "catalog_id", None)
    if not catalog_id:
        return False
    spec = hooks_mod.get_item_spec(str(catalog_id))
    return bool(isinstance(spec, dict) and spec.get("is_wallet"))


def wallet_capacity(item):
    """Max item rows inside one wallet."""
    cap = getattr(item, "wallet_capacity", None)
    if cap is not None:
        try:
            return max(1, int(cap))
        except (TypeError, ValueError):
            pass
    catalog_id = getattr(item, "catalog_id", None)
    if catalog_id:
        spec = hooks_mod.get_item_spec(str(catalog_id))
        if isinstance(spec, dict) and spec.get("wallet_capacity") is not None:
            try:
                return max(1, int(spec["wallet_capacity"]))
            except (TypeError, ValueError):
                pass
    return DEFAULT_WALLET_CAPACITY


def wallet_contents(item):
    """Mutable list of Items inside a wallet."""
    if item is None:
        return []
    contents = getattr(item, "wallet_contents", None)
    if contents is None or not isinstance(contents, list):
        item.wallet_contents = []
    return item.wallet_contents


def wallet_cash_parts(item):
    """Return (dollars, cents) stored on a wallet Item."""
    if item is None:
        return 0, 0
    try:
        d = int(getattr(item, "wallet_dollars", 0) or 0)
        c = int(getattr(item, "wallet_cents", 0) or 0)
    except (TypeError, ValueError):
        return 0, 0
    if c >= 100 or c < 0:
        d += c // 100
        c = c % 100
        if c < 0:
            c += 100
            d -= 1
    return max(0, d), max(0, min(99, c))


def wallet_total_cents(item):
    """Cash inside a wallet Item as total cents."""
    d, c = wallet_cash_parts(item)
    return d * 100 + c


def set_wallet_cash(item, dollars, cents=0):
    """Set exact cash on a wallet Item."""
    try:
        d = int(dollars or 0)
        c = int(cents or 0)
    except (TypeError, ValueError):
        d, c = 0, 0
    if c >= 100 or c < 0:
        d += c // 100
        c = c % 100
        if c < 0:
            c += 100
            d -= 1
    item.wallet_dollars = max(0, d)
    item.wallet_cents = max(0, min(99, c))


def credit_wallet_cash(item, cents):
    """Add cents to wallet Item cash."""
    total = wallet_total_cents(item) + int(cents or 0)
    d, c = divmod(max(0, total), 100)
    set_wallet_cash(item, d, c)


def debit_wallet_cash(item, cents):
    """Remove cents from wallet Item; False when short."""
    need = int(cents or 0)
    if wallet_total_cents(item) < need:
        return False
    total = wallet_total_cents(item) - need
    d, c = divmod(total, 100)
    set_wallet_cash(item, d, c)
    return True


def designated_wallet(character):
    """Worn pocket wallet Item, or None."""
    pocket = ensure_containers_map(character).get(WALLET_SLOT)
    if pocket is not None and is_wallet_item(pocket):
        return pocket
    return None


def _inventory_wallets(character):
    """Every wallet Item in open inventory (may include unworn dupes)."""
    return [
        piece
        for piece in list(getattr(character, "inventory", None) or [])
        if is_wallet_item(piece)
    ]


def _pick_wallet_to_wear(character):
    """Choose an inventory wallet to wear (pocket-flagged first, then richest)."""
    wallets = _inventory_wallets(character)
    if not wallets:
        return None
    pocket_flagged = [
        piece
        for piece in wallets
        if getattr(piece, "container_worn", None) == WALLET_SLOT
    ]
    candidates = pocket_flagged or wallets
    return max(candidates, key=lambda piece: (wallet_total_cents(piece), id(piece)))


def make_starter_wallet(where="wallet"):
    """Catalog leather wallet Item."""
    return hooks_mod.make_world_item(
        {"item": STARTER_WALLET_ID},
        where=where,
    )


def wear_wallet(character, wallet_item):
    """Wear ``wallet_item`` in a pants pocket. Returns (ok, message)."""
    if character is None or wallet_item is None:
        return False, "You aren't carrying that."
    if not is_wallet_item(wallet_item):
        return False, "That isn't a pocket wallet."
    inv = getattr(character, "inventory", None) or []
    if wallet_item not in inv:
        return False, "You aren't carrying that."
    containers = ensure_containers_map(character)
    occupied = containers.get(WALLET_SLOT)
    if occupied is not None and occupied is not wallet_item:
        return False, "Your pants pocket already has a wallet."
    containers[WALLET_SLOT] = wallet_item
    wallet_item.container_worn = WALLET_SLOT
    migrate_character_cash_to_wallet(character)
    return True, f"You slide {wallet_item.key} into your pants pocket."


def remove_wallet(character, wallet_item):
    """Take a worn wallet out of the pocket; it stays in inventory."""
    if wallet_item is None:
        return False, WALLET_NOT_WORN
    containers = ensure_containers_map(character)
    if containers.get(WALLET_SLOT) is not wallet_item:
        return False, WALLET_NOT_WORN
    containers[WALLET_SLOT] = None
    wallet_item.container_worn = None
    return True, f"You take {wallet_item.key} out of your pocket."


def rebind_wallet_from_inventory(character):
    """Rebuild pocket slot from ``container_worn`` flags."""
    if character is None:
        return
    containers = ensure_containers_map(character)
    containers[WALLET_SLOT] = None
    for piece in list(getattr(character, "inventory", None) or []):
        if not is_wallet_item(piece):
            continue
        if getattr(piece, "container_worn", None) != WALLET_SLOT:
            continue
        if containers[WALLET_SLOT] is not None:
            piece.container_worn = None
            continue
        containers[WALLET_SLOT] = piece


def migrate_character_cash_to_wallet(character):
    """Move legacy ``character.dollars/cents`` into the worn wallet once."""
    wallet_item = designated_wallet(character)
    if wallet_item is None:
        return False
    try:
        d = int(getattr(character, "dollars", 0) or 0)
        c = int(getattr(character, "cents", 0) or 0)
    except (TypeError, ValueError):
        d, c = 0, 0
    if d == 0 and c == 0:
        return False
    credit_wallet_cash(wallet_item, d * 100 + c)
    character.dollars = 0
    character.cents = 0
    return True


def consolidate_duplicate_wallets(character):
    """Merge spare wallets into the worn one and drop empty duplicates."""
    wallet_item = designated_wallet(character)
    if wallet_item is None:
        return 0
    inv = getattr(character, "inventory", None) or []
    removed = 0
    for piece in list(inv):
        if piece is wallet_item or not is_wallet_item(piece):
            continue
        spare_cash = wallet_total_cents(piece)
        if spare_cash > 0:
            credit_wallet_cash(wallet_item, spare_cash)
            set_wallet_cash(piece, 0, 0)
        spare_contents = wallet_contents(piece)
        worn_contents = wallet_contents(wallet_item)
        cap = wallet_capacity(wallet_item)
        for inner in list(spare_contents):
            if len(worn_contents) >= cap:
                break
            spare_contents.remove(inner)
            worn_contents.append(inner)
        if wallet_total_cents(piece) == 0 and not wallet_contents(piece):
            piece.container_worn = None
            if piece in inv:
                inv.remove(piece)
            removed += 1
    return removed


def grant_and_wear_starter_wallet(character, *, where="wallet"):
    """Create starter wallet, add to inventory, wear in pocket."""
    if character is None:
        return None
    existing = designated_wallet(character)
    if existing is not None:
        migrate_character_cash_to_wallet(character)
        return existing
    orphan = _pick_wallet_to_wear(character)
    if orphan is not None:
        wear_wallet(character, orphan)
        return orphan
    wallet_item = make_starter_wallet(where=where)
    if wallet_item is None:
        return None
    inv = getattr(character, "inventory", None)
    if inv is None:
        character.inventory = []
        inv = character.inventory
    inv.append(wallet_item)
    wear_wallet(character, wallet_item)
    migrate_character_cash_to_wallet(character)
    return wallet_item


def eject_phones_from_wallet(character):
    """Move flip phones from the pocket wallet back to open inventory."""
    if character is None:
        return 0
    wallet_item = designated_wallet(character)
    if wallet_item is None:
        return 0
    from engine.systems.phone import is_portable_phone

    contents = wallet_contents(wallet_item)
    inv = getattr(character, "inventory", None)
    if inv is None:
        character.inventory = []
        inv = character.inventory
    moved = 0
    for piece in list(contents):
        if not is_portable_phone(piece):
            continue
        contents.remove(piece)
        inv.append(piece)
        moved += 1
    return moved


def heal_character_wallet(character):
    """Idempotent: player gets a worn pocket wallet + cash migration."""
    if character is None:
        return False
    if getattr(character, "is_npc", False):
        return False
    rebind_wallet_from_inventory(character)
    if designated_wallet(character) is None:
        grant_and_wear_starter_wallet(character)
    migrate_character_cash_to_wallet(character)
    consolidate_duplicate_wallets(character)
    eject_phones_from_wallet(character)
    rebind_wallet_from_inventory(character)
    return True


def heal_all_wallets(game):
    """Boot heal: stamp pocket wallets on every persisted player."""
    from engine.char_index import iter_characters

    count = 0
    for char in iter_characters(game):
        if heal_character_wallet(char):
            count += 1
    return count
