"""
civic_shop.py -- generic ware/stock/buy/sell shell for civic vendors.

Prices use integer cents via ``engine.systems.economy`` (no float rounding).
Room stock lives on ``room.shop_stock`` -- distinct from SUPERS'
``vendor_stock`` (a different shape checked by engine_smoke purity).

Stdlib only; zero ``supers`` imports.
"""

from __future__ import annotations

from engine import hooks
from engine.systems import economy as economy_mod

# Ware dict keys validated at use time.
WARE_KEY = "key"
WARE_DESC = "description"
WARE_PRICE = "price_cents"
WARE_QTY = "qty"


def _normalize_ware(ware):
    """Return a ware dict with required keys or raise ValueError."""
    if not isinstance(ware, dict):
        raise ValueError("ware must be a dict")
    key = str(ware.get(WARE_KEY) or "").strip()
    if not key:
        raise ValueError("ware key is required")
    try:
        price_cents = int(ware.get(WARE_PRICE, 0) or 0)
    except (TypeError, ValueError):
        raise ValueError("ware price_cents must be an int") from None
    if price_cents < 0:
        raise ValueError("ware price_cents must be non-negative")
    qty = ware.get(WARE_QTY)
    if qty is not None:
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            raise ValueError("ware qty must be int or None") from None
        if qty < 0:
            raise ValueError("ware qty must be non-negative")
    return {
        WARE_KEY: key,
        WARE_DESC: str(ware.get(WARE_DESC) or key),
        WARE_PRICE: price_cents,
        WARE_QTY: qty,
    }


def find_ware(wares, name_fragment):
    """Return the first ware whose key contains ``name_fragment`` (case-insensitive).

    ``wares`` is a list of ware dicts (typically ``room.shop_stock``).
    Returns ``None`` when nothing matches.
    """
    needle = str(name_fragment or "").strip().lower()
    if not needle:
        return None
    for raw in wares or []:
        try:
            ware = _normalize_ware(raw)
        except ValueError:
            continue
        if needle in ware[WARE_KEY].lower():
            return raw
    return None


def _spawn_purchased_item(ware):
    """Build an inventory Item for a bought ware."""
    normalized = _normalize_ware(ware)
    return hooks.make_world_item(
        {
            "key": normalized[WARE_KEY],
            "description": normalized[WARE_DESC],
        },
        where="shop",
    )


def buy(character, wares, ware, *, game=None):
    """Debit wallet, decrement finite stock, append item to inventory.

    Returns ``(ok: bool, message: str)``. Mutates ``ware`` qty in place
    when stock is finite. ``game`` is accepted for tick/reason parity with
    economy helpers but is optional.
    """
    del game  # reserved for ledger tick stamps via economy when wired
    try:
        normalized = _normalize_ware(ware)
    except ValueError as exc:
        return False, str(exc)

    price = normalized[WARE_PRICE]
    qty = normalized[WARE_QTY]
    if qty is not None and qty <= 0:
        return False, "That item is out of stock."

    if not economy_mod.can_afford(character, 0, cents=price):
        d, c = economy_mod.cents_to_parts(price)
        return False, (
            f"You cannot afford {normalized[WARE_KEY]} "
            f"({economy_mod.format_money(d, c)})."
        )

    if not economy_mod.debit_wallet(
        character,
        0,
        cents=price,
        reason=f"Buy {normalized[WARE_KEY]}",
    ):
        return False, "Payment failed."

    if qty is not None:
        ware[WARE_QTY] = qty - 1

    item = _spawn_purchased_item(ware)
    inventory = getattr(character, "inventory", None)
    if inventory is None:
        character.inventory = []
        inventory = character.inventory
    inventory.append(item)
    d, c = economy_mod.cents_to_parts(price)
    return True, (
        f"You buy {normalized[WARE_KEY]} for "
        f"{economy_mod.format_money(d, c)}."
    )


def sell(character, wares, ware_key, price_cents, *, game=None):
    """Credit wallet and remove one matching inventory item; optional restock.

    ``ware_key`` is the inventory item key to match (case-insensitive exact
    or substring). ``price_cents`` is what the shop pays. When ``wares`` is
    a mutable stock list and a matching ware entry exists, its qty is
    incremented by one (restock). Returns ``(ok, message)``.
    """
    del game
    needle = str(ware_key or "").strip().lower()
    if not needle:
        return False, "Sell what?"

    try:
        payout = int(price_cents or 0)
    except (TypeError, ValueError):
        return False, "Invalid sell price."
    if payout < 0:
        return False, "Invalid sell price."

    inventory = list(getattr(character, "inventory", None) or [])
    sold_item = None
    for item in inventory:
        key = str(getattr(item, "key", "") or "").lower()
        if key == needle or needle in key:
            sold_item = item
            break
    if sold_item is None:
        return False, "You are not carrying that."

    inventory.remove(sold_item)
    character.inventory = inventory
    economy_mod.credit_wallet(
        character,
        0,
        cents=payout,
        reason=f"Sell {getattr(sold_item, 'key', 'item')}",
    )

    restocked = find_ware(wares, getattr(sold_item, "key", ""))
    if restocked is not None:
        rqty = restocked.get(WARE_QTY)
        if rqty is not None:
            restocked[WARE_QTY] = int(rqty) + 1

    return True, (
        f"You sell {sold_item.key} for "
        f"{economy_mod.format_money(*economy_mod.cents_to_parts(payout))}."
    )


def ware_price_cents(ware):
    """Return the shelf price in cents for one ware dict."""
    return _normalize_ware(ware)[WARE_PRICE]


def buyback_cents(ware):
    """Default vendor buy-back price (half shelf, whole cents)."""
    return max(0, ware_price_cents(ware) // 2)


def format_stock_line(ware):
    """One display line for ``list`` output."""
    normalized = _normalize_ware(ware)
    d, c = economy_mod.cents_to_parts(normalized[WARE_PRICE])
    price = economy_mod.format_money(d, c)
    qty = normalized[WARE_QTY]
    if qty is None:
        stock = "plenty"
    elif qty <= 0:
        stock = "out"
    else:
        stock = str(qty)
    return f"  {normalized[WARE_KEY]} -- {price} ({stock})"


def list_stock(wares):
    """Return formatted lines for every ware in stock."""
    lines = []
    for raw in wares or []:
        try:
            lines.append(format_stock_line(raw))
        except ValueError:
            continue
    return lines
