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


def _merge_wallet_into(target, donor):
    """Merge donor wallet cash and papers into ``target``; clear ``donor``."""
    if target is None or donor is None or target is donor:
        return
    spare_cash = wallet_total_cents(donor)
    if spare_cash > 0:
        credit_wallet_cash(target, spare_cash)
        set_wallet_cash(donor, 0, 0)
    spare_contents = wallet_contents(donor)
    worn_contents = wallet_contents(target)
    cap = wallet_capacity(target)
    for inner in list(spare_contents):
        if len(worn_contents) >= cap:
            break
        spare_contents.remove(inner)
        worn_contents.append(inner)


def safeguard_wallet_item_destruction(character, wallet_item):
    """Spill cash and stowed papers before a wallet Item leaves play.

    Possess / spill / duplicate consolidation must never zero migrated cash.
    """
    if character is None or wallet_item is None:
        return
    cash = wallet_total_cents(wallet_item)
    if cash > 0:
        try:
            d0 = int(getattr(character, "dollars", 0) or 0)
            c0 = int(getattr(character, "cents", 0) or 0)
        except (TypeError, ValueError):
            d0, c0 = 0, 0
        total = d0 * 100 + c0 + cash
        ld, lc = divmod(total, 100)
        character.dollars = ld
        character.cents = lc
        set_wallet_cash(wallet_item, 0, 0)
    inv = getattr(character, "inventory", None)
    if inv is None:
        character.inventory = []
        inv = character.inventory
    for piece in list(wallet_contents(wallet_item)):
        wallet_contents(wallet_item).remove(piece)
        if piece not in inv:
            inv.append(piece)
    wallet_item.container_worn = None
    containers = ensure_containers_map(character)
    if containers.get(WALLET_SLOT) is wallet_item:
        containers[WALLET_SLOT] = None


def transfer_worn_wallet_to_character(dest, source):
    """Move the worn pocket wallet from ``source`` onto ``dest``.

    Celestial discorporate leaves mortal wallet + cash on the living husk;
    re-embody pulls it back onto the Mantle. Cash is never destroyed.
    """
    if dest is None or source is None:
        return None
    wallet = designated_wallet(source)
    if wallet is None:
        return None

    src_containers = ensure_containers_map(source)
    src_containers[WALLET_SLOT] = None
    src_inv = getattr(source, "inventory", None) or []
    if wallet in src_inv:
        src_inv.remove(wallet)

    dest_inv = getattr(dest, "inventory", None)
    if dest_inv is None:
        dest.inventory = []
        dest_inv = dest.inventory

    dest_wallet = designated_wallet(dest)
    if dest_wallet is not None and dest_wallet is not wallet:
        _merge_wallet_into(dest_wallet, wallet)
        safeguard_wallet_item_destruction(source, wallet)
        if wallet in src_inv:
            src_inv.remove(wallet)
        rebind_wallet_from_inventory(source)
        rebind_wallet_from_inventory(dest)
        return dest_wallet

    dest_containers = ensure_containers_map(dest)
    dest_containers[WALLET_SLOT] = wallet
    wallet.container_worn = WALLET_SLOT
    if wallet not in dest_inv:
        dest_inv.append(wallet)
    rebind_wallet_from_inventory(source)
    rebind_wallet_from_inventory(dest)
    return wallet


def recover_orphan_wallet_cash(character):
    """Re-wear a pocket wallet from inventory or merge its cash to loose bills."""
    if character is None:
        return False
    if designated_wallet(character) is not None:
        migrate_character_cash_to_wallet(character)
        return False
    wallets = _inventory_wallets(character)
    if not wallets:
        return False
    richest = max(wallets, key=lambda piece: (wallet_total_cents(piece), id(piece)))
    if wallet_total_cents(richest) > 0 or wallet_contents(richest):
        wear_wallet(character, richest)
        migrate_character_cash_to_wallet(character)
        consolidate_duplicate_wallets(character)
        return True
    wear_wallet(character, richest)
    return True


def consolidate_duplicate_wallets(character):
    """Merge spare wallets into the worn one and drop empty duplicates."""
    wallet_item = designated_wallet(character)
    if wallet_item is None:
        recover_orphan_wallet_cash(character)
        wallet_item = designated_wallet(character)
    if wallet_item is None:
        return 0
    inv = getattr(character, "inventory", None) or []
    removed = 0
    for piece in list(inv):
        if piece is wallet_item or not is_wallet_item(piece):
            continue
        _merge_wallet_into(wallet_item, piece)
        if wallet_total_cents(piece) == 0 and not wallet_contents(piece):
            safeguard_wallet_item_destruction(character, piece)
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
