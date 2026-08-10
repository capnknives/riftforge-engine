"""
justice_adapter.py -- read-only bridge between engine justice and game ledgers.

SUPERS live play uses ``criminal`` / ``criminal_fine``; basegame and the
engine kit use ``wanted`` / ``fine_owed_cents``. This module documents the
mapping without importing ``supers/`` (engine purity).

See docs/plans/crime_justice_adapter.md.
"""

from __future__ import annotations


def supers_fine_cents(character) -> int:
    """Whole-dollar ``criminal_fine`` as engine cents (0 when unset)."""
    dollars = int(getattr(character, "criminal_fine", 0) or 0)
    return max(0, dollars) * 100


def is_supers_wanted(character) -> bool:
    """True when the SUPERS sheriff ledger marks the character criminal."""
    return bool(getattr(character, "criminal", False))


def snapshot_supers_ledger(character) -> dict:
    """Debug snapshot of SUPERS crime fields on a character."""
    return {
        "criminal": bool(getattr(character, "criminal", False)),
        "criminal_fine": int(getattr(character, "criminal_fine", 0) or 0),
        "criminal_mark_reason": getattr(character, "criminal_mark_reason", None),
        "jail_until_tick": getattr(character, "jail_until_tick", None),
    }


def apply_supers_fine_payment(debtor, cents_paid: int) -> bool:
    """Debit SUPERS ``criminal_fine`` only (does not touch engine wallet).

    Returns True when the SUPERS ledger had enough fine remaining to absorb
  ``cents_paid``. Does **not** clear jail sentence or move rooms.
    """
    owed_cents = supers_fine_cents(debtor)
    if owed_cents <= 0:
        debtor.criminal_fine = 0
        return True
    if cents_paid < owed_cents:
        remaining_dollars = (owed_cents - cents_paid + 99) // 100
        debtor.criminal_fine = max(0, int(remaining_dollars))
        return False
    debtor.criminal_fine = 0
    return True


def would_map_to_engine_fine(character) -> int:
    """Dry-run: cents ``justice.pay_fine`` would expect from SUPERS fields."""
    return supers_fine_cents(character)


def mirror_supers_to_engine(character) -> None:
    """Fork A: mirror SUPERS sheriff ledger onto engine justice fields."""
    if is_supers_wanted(character):
        character.wanted = True
    else:
        character.wanted = False
    character.fine_owed_cents = supers_fine_cents(character)


def clear_engine_mirror(character) -> None:
    """Clear engine wanted/fine mirror when SUPERS record clears."""
    character.wanted = False
    character.fine_owed_cents = 0
