"""shop.py -- buy/sell at civic vendor rooms (engine civic_shop kit)."""

from engine.systems import civic_shop as shop_mod


def _vendor_room(character):
    """Return the character's room when it hosts vendor stock, else None."""
    room = getattr(character, "location", None)
    if room is None:
        return None
    resources = tuple(getattr(room, "resources", None) or ())
    stock = getattr(room, "shop_stock", None)
    if "vendor" not in resources or not stock:
        return None
    return room


def cmd_list(character, args, game):
    """List wares for sale at a vendor counter."""
    del args, game
    room = _vendor_room(character)
    if room is None:
        character.session.send("There is nothing for sale here.")
        return
    lines = shop_mod.list_stock(room.shop_stock)
    if not lines:
        character.session.send("The shelves are bare.")
        return
    character.session.send("For sale:")
    character.session.send("\r\n".join(lines))


def cmd_buy(character, args, game):
    """Buy a ware from the vendor counter."""
    room = _vendor_room(character)
    if room is None:
        character.session.send("There is nothing for sale here.")
        return
    name = (args or "").strip()
    if not name:
        character.session.send("Buy what? (see 'list')")
        return
    ware = shop_mod.find_ware(room.shop_stock, name)
    if ware is None:
        character.session.send("They do not sell that here.")
        return
    ok, msg = shop_mod.buy(character, room.shop_stock, ware, game=game)
    character.session.send(msg)


def cmd_sell(character, args, game):
    """Sell an inventory item back to the vendor counter."""
    room = _vendor_room(character)
    if room is None:
        character.session.send("There is no buyer here.")
        return
    name = (args or "").strip()
    if not name:
        character.session.send("Sell what?")
        return
    ware = shop_mod.find_ware(room.shop_stock, name)
    if ware is None:
        character.session.send("They are not buying that.")
        return
    try:
        payout = shop_mod.buyback_cents(ware)
    except ValueError:
        character.session.send("They are not buying that.")
        return
    ok, msg = shop_mod.sell(
        character, room.shop_stock, name, payout, game=game,
    )
    character.session.send(msg)
