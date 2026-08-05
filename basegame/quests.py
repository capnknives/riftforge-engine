"""
quests.py -- basegame quest catalog registration and simple grants.

Registers ``basegame/content/quests/`` on the engine loader and wires
cash/item grants for the fetch_pebble demo (no SUPERS items catalog).
"""

from __future__ import annotations

import os

from engine.systems import quests as quests_engine
from engine.systems.quests_loader import set_quests_dirs
from engine.world import Item
from engine.systems import economy as economy_wallet

_CONTENT_DIR = os.path.join(os.path.dirname(__file__), "content")
_QUESTS_DIR = os.path.join(_CONTENT_DIR, "quests")


def register_quest_hooks():
    """Register basegame quest dirs and grant handlers."""
    set_quests_dirs([_QUESTS_DIR])
    quests_engine.set_quest_grant_handler(_apply_grant)
    quests_engine.set_quest_completion_reward_handler(_apply_rewards)


def _apply_grant(character, grant, game=None):
    if not grant or not isinstance(grant, dict):
        return
    cash = None
    if "dollars" in grant or "cents" in grant:
        cash = {
            "dollars": grant.get("dollars", 0),
            "cents": grant.get("cents", 0),
        }
    elif "coins" in grant:
        cash = grant.get("coins")
    if cash is not None:
        economy_wallet.apply_cash_reward(character, cash)
        if character.session and economy_wallet.money_to_cents(cash):
            character.session.send(
                f"You receive {economy_wallet.format_money(cash)}."
            )
    item_id = grant.get("item") or grant.get("item_id")
    if item_id:
        item = Item("a smooth pebble", "A small training pebble.")
        item.catalog_id = str(item_id)
        character.inventory.append(item)
        if character.session:
            character.session.send(f"You receive {item.key}.")


def _apply_rewards(character, data, game=None):
    lines = []
    for reward in data.get("rewards") or []:
        rtype = reward.get("type")
        if economy_wallet.is_cash_loot_type(rtype):
            amount = reward.get("amount", 0)
            economy_wallet.apply_cash_reward(character, amount)
            lines.append(economy_wallet.format_money(amount))
    return lines
