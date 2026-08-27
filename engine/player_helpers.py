"""Player helper flag helpers (account-first, character fallback).

Staff grant helper access with ``gm set <who> helper on|off``. When the
target is a linked character, the **account** is flagged so every PC on
that login keeps helper tools across boots. Unlinked bodies get a per-character
``player_helper`` blob flag instead.

Helpers may use the ``answers`` channel and close ``query`` tickets; staff
always have helper powers for channel audience and query moderation.
"""

from __future__ import annotations

from engine.accounts import (
    account_for_character,
    account_lookup_key,
    ensure_accounts_dict,
    find_account,
    normalize_account_name,
)
from engine.command_support import _is_staff_gm


def is_player_helper(game, character) -> bool:
    """True when *character* is a tagged helper or online staff GM."""
    if character is None:
        return False
    if _is_staff_gm(character):
        return True
    account = account_for_character(game, character)
    if account is not None and bool(getattr(account, "player_helper", False)):
        return True
    return bool(getattr(character, "player_helper", False))


def resolve_helper_target(game, name):
    """Resolve a staff target name to account and/or character.

    Returns ``(account, character, error_or_None)``.
    """
    raw = (name or "").strip()
    if not raw:
        return None, None, "Set helper on or off for whom?"
    low = raw.lower()

    char = game.find_character(raw) if game is not None else None
    if char is None and game is not None:
        finder = getattr(game, "find_login_character", None)
        if callable(finder):
            char = finder(raw)
    if char is not None and not getattr(char, "is_npc", False):
        acct = account_for_character(game, char)
        if acct is not None:
            return acct, char, None
        return None, char, None

    cleaned, err = normalize_account_name(raw)
    if err:
        acct = find_account(game, raw)
        if acct is None:
            acct = find_account(game, cleaned)
    else:
        acct = find_account(game, cleaned)
    if acct is not None:
        return acct, None, None

    if game is not None:
        for candidate in list(getattr(game, "characters", None) or []):
            if getattr(candidate, "is_npc", False):
                continue
            key = (getattr(candidate, "key", None) or "")
            if key.lower() == low:
                acct = account_for_character(game, candidate)
                if acct is not None:
                    return acct, candidate, None
                return None, candidate, None
    return None, None, f"No account or character named '{raw}' found."


def _find_playable_key(game, key):
    if game is None or not key:
        return None
    ch = game.find_character(key)
    if ch is not None and not getattr(ch, "is_npc", False):
        return ch
    return None


def helper_label(game, *, account=None, character=None) -> str:
    if account is not None:
        return account.display_name or account.name
    if character is not None:
        return character.key
    return "?"


def set_helper_on(game, *, account=None, character=None) -> str:
    """Grant helper tag. Returns a status sentence."""
    if account is not None:
        account.player_helper = True
        for key in list(account.character_keys):
            ch = _find_playable_key(game, key)
            if ch is not None:
                ch.player_helper = False
        label = account.display_name or account.name
        return f"Player helper enabled for account '{label}'."
    if character is not None:
        character.player_helper = True
        return f"Player helper enabled for {character.key} (unlinked body)."
    return "No target."


def set_helper_off(game, *, account=None, character=None) -> str:
    """Revoke helper tag. Returns a status sentence."""
    if account is not None:
        account.player_helper = False
        for key in list(account.character_keys):
            ch = _find_playable_key(game, key)
            if ch is not None:
                ch.player_helper = False
        label = account.display_name or account.name
        return f"Player helper disabled for account '{label}'."
    if character is not None:
        character.player_helper = False
        return f"Player helper disabled for {character.key}."
    return "No target."


def format_helper_list(game) -> str:
    """Staff listing of accounts flagged as helpers."""
    ensure_accounts_dict(game)
    lines = ["Player helpers (account flag):"]
    flagged = []
    for acct in sorted(
        (game.accounts or {}).values(),
        key=lambda a: (getattr(a, "name", "") or "").lower(),
    ):
        if bool(getattr(acct, "player_helper", False)):
            flagged.append(acct.display_name or acct.name)
    if not flagged:
        lines.append("  (none)")
    else:
        for name in flagged:
            lines.append(f"  {name}")
    return "\r\n".join(lines)


def account_lookup_key_safe(game, character):
    account = account_for_character(game, character)
    if account is not None:
        return account_lookup_key(account.name)
    return (getattr(character, "key", None) or "").strip()
