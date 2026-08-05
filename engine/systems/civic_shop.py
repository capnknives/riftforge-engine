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


def stock_price_cents(ware):
    """Shelf price in cents from civic ``price_cents`` or dollar ``price``."""
    if not isinstance(ware, dict):
        return 0
    if WARE_PRICE in ware:
        try:
            return int(ware.get(WARE_PRICE) or 0)
        except (TypeError, ValueError):
            return 0
    return economy_mod.money_to_cents(ware.get("price", 0))


def stock_qty(ware):
    """Finite stock count, or ``None`` when unlimited."""
    if not isinstance(ware, dict) or WARE_QTY not in ware:
        return None
    qty = ware.get(WARE_QTY)
    if qty is None:
        return None
    try:
        return int(qty)
    except (TypeError, ValueError):
        return None


def is_finite_stock(ware):
    """True when ``ware`` carries a finite ``qty`` field."""
    return stock_qty(ware) is not None


def consume_stock(ware, stock_list=None):
    """Decrement finite ``qty``; drop from ``stock_list`` when depleted.

    Unlimited wares (no ``qty`` key) are unchanged. When ``stock_list`` is
    omitted the row may remain at qty 0 (civic ``shop_stock``). When a list
    is passed, sold-out rows are removed (SUPERS ``vendor_stock``).
  """
    if not is_finite_stock(ware):
        return True
    qty = int(ware[WARE_QTY])
    ware[WARE_QTY] = qty - 1
    if ware[WARE_QTY] <= 0:
        if stock_list is not None:
            try:
                stock_list.remove(ware)
            except ValueError:
                pass
        return False
    return True


def increment_stock(ware):
    """Raise finite ``qty`` by one; unlimited rows unchanged."""
    if not is_finite_stock(ware):
        return
    ware[WARE_QTY] = int(ware[WARE_QTY]) + 1


def transfer_purchase_payment(
    buyer,
    price_cents,
    *,
    debit_reason,
    tick=None,
    payee=None,
    credit_reason=None,
):
    """Debit ``buyer``; optionally credit ``payee`` (shopkeeper).

    Returns ``(ok, error_code)`` where ``error_code`` is ``cant_afford``,
    ``payment_failed``, or ``None`` on success.
    """
    price_cents = int(price_cents or 0)
    if not economy_mod.can_afford(buyer, 0, cents=price_cents):
        return False, "cant_afford"
    if not economy_mod.debit_wallet(
        buyer,
        0,
        cents=price_cents,
        reason=debit_reason,
        tick=tick,
    ):
        return False, "payment_failed"
    if payee is not None and payee is not buyer:
        d, c = economy_mod.cents_to_parts(price_cents)
        economy_mod.credit_wallet(
            payee,
            d,
            c,
            reason=credit_reason or debit_reason,
            tick=tick,
        )
    return True, None


def transfer_sale_payout(
    seller,
    price_cents,
    *,
    credit_reason,
    tick=None,
    payer=None,
    debit_reason=None,
):
    """Credit ``seller``; optionally debit ``payer`` (vendor buy-back).

    When the payer cannot cover the payout, their wallet is zeroed (pawn
    shops still take the goods). Always credits the seller.
    """
    price_cents = int(price_cents or 0)
    d, c = economy_mod.cents_to_parts(price_cents)
    economy_mod.credit_wallet(
        seller,
        d,
        c,
        reason=credit_reason,
        tick=tick,
    )
    if payer is not None and payer is not seller:
        if not economy_mod.debit_wallet(
            payer,
            d,
            c,
            reason=debit_reason or credit_reason,
            tick=tick,
        ):
            economy_mod.set_wallet(payer, 0, 0)
    return True


def purchase_at_stock(
    buyer,
    ware,
    price_cents,
    *,
    stock_list=None,
    spawn_item,
    debit_reason,
    tick=None,
    payee=None,
    credit_reason=None,
):
    """Debit wallet, consume stock, spawn item, append to inventory.

    ``spawn_item(ware)`` builds the purchased ``Item``. Returns
    ``(ok, error_code, item)`` where ``error_code`` is ``out_of_stock``,
    ``cant_afford``, ``payment_failed``, or ``None`` on success.
    """
    qty = stock_qty(ware)
    if qty is not None and qty <= 0:
        return False, "out_of_stock", None

    ok, err = transfer_purchase_payment(
        buyer,
        price_cents,
        debit_reason=debit_reason,
        tick=tick,
        payee=payee,
        credit_reason=credit_reason,
    )
    if not ok:
        return False, err, None

    consume_stock(ware, stock_list)
    item = spawn_item(ware)
    inventory = getattr(buyer, "inventory", None)
    if inventory is None:
        buyer.inventory = []
        inventory = buyer.inventory
    inventory.append(item)
    return True, None, item


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
    del wares, game  # civic list unused; tick reserved for ledger wiring
    try:
        normalized = _normalize_ware(ware)
    except ValueError as exc:
        return False, str(exc)

    price = normalized[WARE_PRICE]
    ok, err, item = purchase_at_stock(
        character,
        ware,
        price,
        stock_list=None,
        spawn_item=_spawn_purchased_item,
        debit_reason=f"Buy {normalized[WARE_KEY]}",
    )
    if not ok:
        if err == "out_of_stock":
            return False, "That item is out of stock."
        if err == "cant_afford":
            d, c = economy_mod.cents_to_parts(price)
            return False, (
                f"You cannot afford {normalized[WARE_KEY]} "
                f"({economy_mod.format_money(d, c)})."
            )
        return False, "Payment failed."

    del item  # spawned and appended inside purchase_at_stock
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
    transfer_sale_payout(
        character,
        payout,
        credit_reason=f"Sell {getattr(sold_item, 'key', 'item')}",
    )

    restocked = find_ware(wares, getattr(sold_item, "key", ""))
    if restocked is not None:
        increment_stock(restocked)

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
