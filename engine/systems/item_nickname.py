"""
item_nickname.py -- per-owner item nicknames (generic, engine-side).

Nicknames live on the Item (``nickname`` persist field). First consumer is
``phone primary <nick>``; any inventory item can be labeled with the
``nickname`` player verb.
"""

from __future__ import annotations

import re

from engine.command_support import _find_item

MAX_NICKNAME_LEN = 24
_NICK_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")


def normalize_nickname(raw):
    """Canonical nickname string for compare/store, or None if empty."""
    text = (raw or "").strip().lower()
    return text or None


def _inventory(character):
    return list(getattr(character, "inventory", None) or [])


def find_item_by_nickname(character, nick):
    """Return the sole inventory item with this nickname, or None."""
    want = normalize_nickname(nick)
    if not want:
        return None
    hits = []
    for piece in _inventory(character):
        if normalize_nickname(getattr(piece, "nickname", None)) == want:
            hits.append(piece)
    if len(hits) == 1:
        return hits[0]
    return None


def find_item_in_inventory(character, query, *, include_nicknames=True):
    """Resolve an inventory item by nickname (exact) then key/alias match."""
    text = (query or "").strip()
    if not text:
        return None
    if include_nicknames:
        by_nick = find_item_by_nickname(character, text)
        if by_nick is not None:
            return by_nick
    return _find_item(text, _inventory(character), character=character)


def set_item_nickname(character, item_query, nick_raw):
    """Label one carried item. Returns (ok, message)."""
    item = find_item_in_inventory(character, item_query, include_nicknames=False)
    if item is None:
        return False, f"You are not carrying '{item_query}'."
    nick = normalize_nickname(nick_raw)
    if not nick:
        return False, "Nickname cannot be empty."
    if not _NICK_RE.match(nick):
        return (
            False,
            "Nickname must start with a letter "
            f"(up to {MAX_NICKNAME_LEN} letters, digits, _ or -).",
        )
    for other in _inventory(character):
        if other is item:
            continue
        if normalize_nickname(getattr(other, "nickname", None)) == nick:
            return False, f"You already nicknamed something '{nick}'."
    item.nickname = nick
    label = getattr(item, "key", "item")
    return True, f"You nickname {label} as '{nick}'."


def clear_item_nickname(character, item_query):
    """Drop a nickname from one carried item. Returns (ok, message)."""
    item = find_item_in_inventory(character, item_query)
    if item is None:
        return False, f"You are not carrying '{item_query}'."
    if not normalize_nickname(getattr(item, "nickname", None)):
        return False, "That item has no nickname."
    item.nickname = None
    label = getattr(item, "key", "item")
    return True, f"You drop the nickname on {label}."
