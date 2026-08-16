"""
civic_shops.py -- in-memory civic fixture registry + structural lifecycle.

Generic engine layer for enterable street fixtures (mouth wiring, curb
Item placement, HP/wrecked state on the registry record). SUPERS
``player_shops.py`` keeps deed/insurance/rent/wholesale policy and SQLite
persistence; this module mirrors shop rows in ``game.civic_fixtures`` at
runtime only (no new persist lane on live).

Stdlib only; zero ``supers`` imports.
"""

from __future__ import annotations

from engine.systems import civic_fixture as fixture_mod

REGISTRY_ATTR = "civic_fixtures"
DEFAULT_HP = 100

_on_wreck_hooks = []


def register_on_wreck(callback):
    """Register ``callback(game, record)`` after a fixture wrecks."""
    _on_wreck_hooks.append(callback)


def ensure_registry(game):
    """Return ``game.civic_fixtures`` dict, creating it when missing."""
    reg = getattr(game, REGISTRY_ATTR, None)
    if reg is None:
        reg = {}
        setattr(game, REGISTRY_ATTR, reg)
    return reg


def shop_record_to_fixture(shop):
    """Convert a SUPERS ``player_shops`` dict into a registry record."""
    meta = shop.get("meta") or {}
    fixture_id = str(shop.get("fixture_id") or shop["shop_id"])
    return {
        "fixture_id": fixture_id,
        "host_room_key": shop.get("host_room_key"),
        "hub_room_key": shop.get("hub_room_key"),
        "enter_alias": shop.get("enter_alias"),
        "display_name": shop.get("display_name"),
        "hp": int(shop.get("hp") or DEFAULT_HP),
        "hp_max": int(shop.get("hp_max") or DEFAULT_HP),
        "wrecked": bool(shop.get("wrecked")),
        "facing": meta.get("facing"),
        "for_rent": bool(meta.get("for_rent")),
    }


def register_fixture(game, record, *, replace=True):
    """Insert or replace one fixture record in the in-memory registry."""
    fid = str(record.get("fixture_id") or "").strip()
    if not fid:
        raise ValueError("fixture_id is required")
    reg = ensure_registry(game)
    if fid in reg and not replace:
        return reg[fid]
    row = dict(record)
    row["fixture_id"] = fid
    reg[fid] = row
    return row


def sync_from_shop(game, shop):
    """Upsert the engine registry from a live SUPERS shop dict."""
    return register_fixture(game, shop_record_to_fixture(shop))


def get_fixture(game, fixture_id):
    """Lookup one registry row by id."""
    return ensure_registry(game).get(str(fixture_id or ""))


def list_on_host(game, host_room):
    """All fixtures registered on ``host_room`` (by room key)."""
    host_key = getattr(host_room, "key", None) or str(host_room or "")
    return [
        row
        for row in ensure_registry(game).values()
        if row.get("host_room_key") == host_key
    ]


def wire_mouth(game, record):
    """Stamp ``zone_entries`` mouth host -> hub for one fixture record."""
    host = (getattr(game, "rooms", None) or {}).get(record.get("host_room_key"))
    hub = (getattr(game, "rooms", None) or {}).get(record.get("hub_room_key"))
    if host is None or hub is None:
        return
    alias = str(record.get("enter_alias") or "shop").lower()
    town_zone = getattr(host, "zone", None) or getattr(hub, "zone", None)
    fixture_mod.wire_mouth(host, hub, alias, record["fixture_id"])
    hub.player_shop_id = record["fixture_id"]
    if town_zone:
        hub.zone = town_zone


def unwire_mouth(game, record):
    """Remove one enter alias from the host street."""
    host = (getattr(game, "rooms", None) or {}).get(record.get("host_room_key"))
    if host is None:
        return
    alias = str(record.get("enter_alias") or "").lower()
    fixture_mod.unwire_mouth(host, alias)


def build_fixture_item(record):
    """Curb Item for a registry record (basegame / engine demos)."""
    line = fixture_mod.fixture_look_line(
        display_name=record.get("display_name") or "Shop",
        enter_alias=record.get("enter_alias") or "shop",
        wrecked=bool(record.get("wrecked")),
        facing=record.get("facing"),
        for_rent=bool(record.get("for_rent")),
    )
    item = fixture_mod.make_fixture_item(record["fixture_id"], line)
    item.shop_id = record["fixture_id"]
    item.fixture_id = record["fixture_id"]
    item.hp_max = int(record.get("hp_max") or DEFAULT_HP)
    item.hp = int(record.get("hp") or item.hp_max)
    item.wrecked = bool(record.get("wrecked"))
    item.facing = record.get("facing")
    return item


def place_fixture(game, record):
    """Ensure the curb Item for ``record`` sits on its host street."""
    host = (getattr(game, "rooms", None) or {}).get(record.get("host_room_key"))
    if host is not None:
        fixture_mod.place_fixture(host, build_fixture_item(record))


def heal_fixture(game, record):
    """Idempotent mouth + curb Item for a healthy fixture."""
    if record.get("wrecked"):
        unwire_mouth(game, record)
        place_fixture(game, record)
        return
    wire_mouth(game, record)
    place_fixture(game, record)


def wreck_fixture(game, record):
    """0-HP wreck: unwire mouth and refresh scorched curb Item."""
    record["wrecked"] = True
    record["hp"] = 0
    unwire_mouth(game, record)
    place_fixture(game, record)
    for callback in _on_wreck_hooks:
        callback(game, record)


def repair_fixture_hp(game, record, *, hp_max=None):
    """Restore full HP and reopen the mouth (no insurance policy here)."""
    max_hp = int(hp_max or record.get("hp_max") or DEFAULT_HP)
    record["hp"] = max_hp
    record["hp_max"] = max_hp
    record["wrecked"] = False
    heal_fixture(game, record)


def apply_damage_to_fixture(game, record, damage):
    """Chip HP on ``record``; wreck when it hits zero."""
    hp_max = int(record.get("hp_max") or DEFAULT_HP)
    hp, wrecked = fixture_mod.apply_damage(
        record.get("hp", hp_max), hp_max, damage,
    )
    record["hp"] = hp
    record["wrecked"] = wrecked
    if wrecked:
        wreck_fixture(game, record)
    else:
        place_fixture(game, record)
    return hp, wrecked


def ensure_demo_newsstand(game):
    """Idempotent Notbigville plaza newsstand (basegame civic fixture demo)."""
    fixture_id = "bgfixture:newsstand"
    host_key = "NB00001"
    hub_key = "BGNewsstandHub"
    host = (getattr(game, "rooms", None) or {}).get(host_key)
    if host is None:
        return None
    record = get_fixture(game, fixture_id)
    if record is None:
        record = register_fixture(
            game,
            {
                "fixture_id": fixture_id,
                "host_room_key": host_key,
                "hub_room_key": hub_key,
                "enter_alias": "newsstand",
                "display_name": "Newsstand",
                "hp": DEFAULT_HP,
                "hp_max": DEFAULT_HP,
                "wrecked": False,
                "facing": "E",
                "for_rent": False,
            },
        )
    hub = (getattr(game, "rooms", None) or {}).get(hub_key)
    if hub is None:
        hub = fixture_mod.create_hub_room(
            hub_key,
            "Racks of local papers and storm-sky tabloids. Type exit to "
            "return to the plaza.",
            fixture_id=fixture_id,
        )
        hub.title = "Notbigville - Newsstand"
        hub.resources = ["vendor"]
        hub.shop_stock = [
            {
                "key": "a local paper",
                "description": "Today's Notbigville Gazette — grain prices "
                "and storm watches.",
                "price_cents": 75,
                "qty": None,
            },
        ]
        game.rooms[hub_key] = hub
    heal_fixture(game, record)
    return record
