"""Playtester flag helpers (account-first, character fallback).

Staff grant playtester access with ``playtester add <name>``. When the
target is a linked character, the **account** is flagged so every PC on
that login keeps the tools across boots. Unlinked bodies get a per-character
``playtester`` blob flag instead.

Players use ``playtest`` to list and run registered diagnostic tools
(see ``supers/playtest_tools.py``).
"""

from __future__ import annotations

from engine.accounts import (
    account_for_character,
    account_lookup_key,
    ensure_accounts_dict,
    find_account,
    normalize_account_name,
)


def is_playtester(game, character):
    """True when ``character`` may use ``playtest`` tools."""
    if character is None:
        return False
    account = account_for_character(game, character)
    if account is not None and bool(getattr(account, "playtester", False)):
        return True
    return bool(getattr(character, "playtester", False))


def resolve_playtester_target(game, name):
    """Resolve a staff target name to account and/or character.

    Returns ``(account, character, error_or_None)``. Prefers the linked
  account when a playable character matches; otherwise tries account name.
    """
    raw = (name or "").strip()
    if not raw:
        return None, None, "Add or remove whom?"
    low = raw.lower()

    # 1) Exact character key (world roster).
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

    # 2) Account name (normalized).
    cleaned, err = normalize_account_name(raw)
    if err:
        # Loose account lookup (display names / odd casing).
        acct = find_account(game, raw)
        if acct is None:
            acct = find_account(game, cleaned)
    else:
        acct = find_account(game, cleaned)
    if acct is not None:
        return acct, None, None

    # 3) Loose character match on presence face / substring.
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


def set_playtester_on(game, *, account=None, character=None):
    """Grant playtester tools. Returns a status sentence."""
    if account is not None:
        account.playtester = True
        # Clear redundant per-body flag on linked PCs.
        for key in list(account.character_keys):
            ch = _find_playable_key(game, key)
            if ch is not None:
                ch.playtester = False
        label = account.display_name or account.name
        return f"Playtester tools enabled for account '{label}'."
    if character is not None:
        character.playtester = True
        return f"Playtester tools enabled for {character.key} (unlinked body)."
    return "No target."


def set_playtester_off(game, *, account=None, character=None):
    """Revoke playtester tools. Returns a status sentence."""
    _ = game
    if account is not None:
        account.playtester = False
        label = account.display_name or account.name
        return f"Playtester tools removed from account '{label}'."
    if character is not None:
        character.playtester = False
        return f"Playtester tools removed from {character.key}."
    return "No target."


def _find_playable_key(game, key):
    if game is None or not key:
        return None
    finder = getattr(game, "find_login_character", None)
    char = finder(key) if callable(finder) else None
    if char is None:
        char = game.find_character(key)
    if char is None or getattr(char, "is_npc", False):
        return None
    return char


def format_playtester_list(game):
    """Multi-line roster of playtester accounts and unlinked bodies."""
    lines = ["Playtesters (persist across boots):"]
    accounts = list((ensure_accounts_dict(game) or {}).values())
    flagged_accounts = sorted(
        (a for a in accounts if getattr(a, "playtester", False)),
        key=lambda a: (a.display_name or a.name).lower(),
    )
    if flagged_accounts:
        lines.append("  Accounts:")
        for acct in flagged_accounts:
            keys = ", ".join(acct.character_keys) or "(no linked PCs)"
            lines.append(
                f"    {acct.display_name or acct.name}  PCs: {keys}"
            )
    else:
        lines.append("  Accounts: (none)")

    bodies = []
    for char in list(getattr(game, "characters", None) or []):
        if getattr(char, "is_npc", False):
            continue
        if not getattr(char, "playtester", False):
            continue
        acct = account_for_character(game, char)
        if acct is not None and getattr(acct, "playtester", False):
            continue
        bodies.append(char.key)
    if bodies:
        lines.append("  Unlinked bodies:")
        for key in sorted(bodies, key=str.lower):
            lines.append(f"    {key}")
    elif not flagged_accounts:
        lines.append("  (none)")
    lines.append("")
    lines.append("Grant: playtester add <account|character>")
    lines.append("Revoke: playtester remove <account|character>")
    return "\r\n".join(lines)


def playtester_label(game, account=None, character=None):
    """Short label for staff confirm lines."""
    if account is not None:
        return f"account {account.display_name or account.name}"
    if character is not None:
        acct = account_for_character(game, character)
        if acct is not None:
            return (
                f"account {acct.display_name or acct.name} "
                f"(via {character.key})"
            )
        return f"character {character.key}"
    return "target"


def account_playtester_keys(game):
    """Lowercase account keys flagged playtester (boot heal / audits)."""
    return {
        account_lookup_key(a.name)
        for a in (ensure_accounts_dict(game) or {}).values()
        if getattr(a, "playtester", False)
    }
