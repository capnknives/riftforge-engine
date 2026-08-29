"""
vendor_stock.py -- generic NPC roster ware-row helpers (engine kernel).

Plain-dict mechanics for the ``vendor_stock`` list shape shared by every
NPC roster entry: build one canonical ware row, format rows for a GM
listing, and resolve a player/GM selector (1-based index or item id)
back to a row index. No item catalog lookups and no game-layer imports
-- any game can hand this module its own ware dicts.

The game layer keeps the two catalog-aware helpers (one that validates
against its own item catalog, one that reads NPC ``job``/``workplace``
fields) and re-exports these three under their original names so no
call site changes. Design: docs/plans/engine_liquid_flavor.md
(Wave 2 Part A).
"""

from __future__ import annotations


def coerce_ware(item_id, price, qty=None):
    """Build one canonical roster ware dict.

    Raises ``ValueError`` on a bad shape so callers (GM ``npc stock``,
    Area Studio, roster load) fail loud instead of saving a row the
    catalog-aware validator would reject later.
    """
    item_id = (item_id or "").strip()
    if not item_id:
        raise ValueError("item id must be non-empty.")
    try:
        price_val = int(price)
    except (TypeError, ValueError) as exc:
        raise ValueError("price must be an integer.") from exc
    if price_val < 0:
        raise ValueError("price must be non-negative.")
    ware = {"item": item_id, "price": price_val}
    # "unlimited"/"none"/"-" (and friends) mean "no qty key at all", which
    # is how the rest of the vendor code spells bottomless stock.
    if qty is not None and str(qty).strip().lower() not in (
        "", "none", "null", "unlimited", "-",
    ):
        try:
            qty_val = int(qty)
        except (TypeError, ValueError) as exc:
            raise ValueError("qty must be a non-negative int or unlimited.") from exc
        if qty_val < 0:
            raise ValueError("qty must be a non-negative int or unlimited.")
        ware["qty"] = qty_val
    return ware


def format_stock_lines(stock):
    """Human-readable lines for a GM ``npc stock`` list."""
    if not stock:
        return ["  (empty)"]
    lines = []
    for i, ware in enumerate(stock, start=1):
        if not isinstance(ware, dict):
            lines.append(f"  {i}. (invalid row)")
            continue
        item_id = ware.get("item") or ware.get("key") or "?"
        price = ware.get("price", 0)
        if "qty" in ware and ware.get("qty") is not None:
            qty_txt = str(ware.get("qty"))
        else:
            qty_txt = "unlimited"
        lines.append(f"  {i}. {item_id}  price={price}  qty={qty_txt}")
    return lines


def resolve_stock_selector(stock, selector):
    """Map a 1-based index or item id to a stock list index.

    Digit input resolves by position (bounds-checked); non-digit input
    resolves by ``item``/``key`` match, and is rejected if it is
    ambiguous across more than one row.
    """
    if not stock:
        raise ValueError("vendor_stock is empty.")
    text = (selector or "").strip()
    if not text:
        raise ValueError("stock selector required (1-based index or item id).")
    if text.isdigit():
        idx = int(text)
        if idx < 1 or idx > len(stock):
            raise ValueError(
                f"stock index {idx} out of range (1-{len(stock)})."
            )
        return idx - 1
    matches = []
    for i, ware in enumerate(stock):
        if not isinstance(ware, dict):
            continue
        item_id = ware.get("item") or ware.get("key")
        if item_id == text:
            matches.append(i)
    if not matches:
        raise ValueError(f"No vendor_stock row for item {text!r}.")
    if len(matches) > 1:
        raise ValueError(
            f"Multiple vendor_stock rows for {text!r} -- use 1-based index."
        )
    return matches[0]
