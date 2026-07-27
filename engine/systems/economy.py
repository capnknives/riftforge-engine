"""economy.py -- the engine's generic wallet / bank ledger kit.

Every game that uses a flat integer currency eventually needs the same
shape: on-hand balance, optional banked balance, deposit/withdraw, an
afford check, and a player-facing money string. That ledger -- not any
particular game's vendor stock, job shifts, Cadence stipends, or
need-tagged shop wares -- is what lives here.

SUPERS' town economy (``supers/economy.py``) keeps every scrap of
fiction (vendor catalogs, buy/sell/fence, gig work, thrift, stipend
tuning, ``can_afford_resource`` against room tags) and re-exports these
primitives so existing ``economy.format_money`` / ``economy.deposit``
call sites keep working unchanged (docs/plans/two_repo_purity.md Phase 7
Stage 5). Character ``coins`` / ``bank_coins`` remain optional dynamic
attributes (games attach / persist owns defaults) -- this module only
reads and writes them via ``getattr``.

Pure attribute math + string formatting: no networking, no database, no
game loop, zero ``supers`` imports.
"""

from __future__ import annotations


def wallet_balance(character):
    """Integer on-hand cash (``character.coins``), or 0 when missing."""
    return int(getattr(character, "coins", 0) or 0)


def bank_balance(character):
    """Integer banked cash (``character.bank_coins``), or 0 when missing."""
    return int(getattr(character, "bank_coins", 0) or 0)


def can_afford(character, amount):
    """True when the on-hand wallet covers ``amount`` (coerced to int)."""
    try:
        n = int(amount)
    except (TypeError, ValueError):
        return False
    return wallet_balance(character) >= n


def format_money(amount):
    """Player-facing cash string. Storage remains integer ``coins``.

    Whole dollars only (no cents). Negative values keep the minus sign
    before the dollar mark (``-$5``).
    """
    try:
        n = int(amount)
    except (TypeError, ValueError):
        n = 0
    if n < 0:
        return f"-${abs(n)}"
    return f"${n}"


def money_noun(plural=True):
    """Singular/plural noun for help and prose (``dollar`` / ``dollars``)."""
    return "dollars" if plural else "dollar"


def money_score_label():
    """Score-sheet field label for the on-hand wallet."""
    return "Cash"


def deposit(character, amount):
    """Move dollars from wallet to bank. Returns (ok, message)."""
    try:
        n = int(amount)
    except (TypeError, ValueError):
        return False, "Deposit how much? (a positive number, or 'all')"
    if n <= 0:
        return False, "Deposit a positive amount."
    wallet = wallet_balance(character)
    if n > wallet:
        return False, f"You only have {format_money(wallet)} on you."
    character.coins = wallet - n
    character.bank_coins = bank_balance(character) + n
    return True, (
        f"You deposit {format_money(n)}. "
        f"Wallet: {format_money(character.coins)}. "
        f"Bank: {format_money(character.bank_coins)}."
    )


def withdraw(character, amount):
    """Move dollars from bank to wallet. Returns (ok, message)."""
    try:
        n = int(amount)
    except (TypeError, ValueError):
        return False, "Withdraw how much? (a positive number, or 'all')"
    if n <= 0:
        return False, "Withdraw a positive amount."
    bank = bank_balance(character)
    if n > bank:
        return False, f"You only have {format_money(bank)} in the bank."
    character.bank_coins = bank - n
    character.coins = wallet_balance(character) + n
    return True, (
        f"You withdraw {format_money(n)}. "
        f"Wallet: {format_money(character.coins)}. "
        f"Bank: {format_money(character.bank_coins)}."
    )
