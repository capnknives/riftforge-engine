"""economy.py -- the engine's generic wallet / bank ledger kit.

Wallet storage: ``character.dollars`` (whole dollars) + ``character.cents``
(0--99). Bank mirrors ``bank_dollars`` / ``bank_cents``. Legacy saves with
``coins`` / ``bank_coins`` migrate on load in ``supers.persist_blob``.

Pure attribute math + string formatting: no networking, no database, no
game loop, zero ``supers`` imports.
"""

from __future__ import annotations

# Ring buffer size for per-character cash audit (wallet + bank moves).
WALLET_LEDGER_MAX = 40


def _carry_cents(dollars, cents):
    """Normalize (dollars, cents) so cents is always 0--99."""
    try:
        d = int(dollars or 0)
        c = int(cents or 0)
    except (TypeError, ValueError):
        return 0, 0
    if c >= 100 or c < 0:
        d += c // 100
        c = c % 100
        if c < 0:
            c += 100
            d -= 1
    return d, c


def money_to_cents(value):
    """Parse catalog / player money into total cents.

    Accepts whole dollars (``12``), floats (``12.5``), ``"$12.50"`` strings,
    or ``{"dollars": 12, "cents": 50}`` dicts. Used for prices and payouts.
    """
    if value is None:
        return 0
    if isinstance(value, dict):
        d = int(value.get("dollars", 0) or 0)
        c = int(value.get("cents", 0) or 0)
        return d * 100 + c
    if isinstance(value, str):
        text = value.strip().lstrip("$").replace(",", "")
        if not text or text.lower() == "all":
            return 0
        if "." in text:
            whole, frac = text.split(".", 1)
            frac = (frac + "00")[:2]
            return int(whole or 0) * 100 + int(frac or 0)
        return int(text) * 100
    if isinstance(value, float):
        return int(round(value * 100))
    if isinstance(value, int):
        return int(value) * 100
    try:
        return int(value) * 100
    except (TypeError, ValueError):
        return 0


def cents_to_parts(total_cents):
    """Split total cents into (dollars, cents) with carry normalization."""
    return _carry_cents(total_cents // 100, total_cents % 100)


# Loot / quest cash rewards use ``type: dollars``. ``coins`` is a deprecated
# alias for the same thing (not gold/silver coin items).
CASH_LOOT_TYPE = "dollars"
LEGACY_CASH_LOOT_TYPES = frozenset({"dollars", "coins"})


def is_cash_loot_type(reward_type):
    """True when a loot/reward entry pays wallet cash (not metal coin items)."""
    return reward_type in LEGACY_CASH_LOOT_TYPES


def apply_cash_reward(character, amount, *, reason="Cash reward", tick=None):
    """Credit wallet cash from a loot amount (int, float, or money dict)."""
    credit_wallet(
        character,
        cents=money_to_cents(amount),
        reason=reason,
        tick=tick,
    )


def wallet_parts_from_fields(
    mapping,
    *,
    dollars_key="dollars",
    cents_key="cents",
    legacy_key="coins",
):
    """Parse roster/grant dict fields into (dollars, cents) wallet parts."""
    if not isinstance(mapping, dict):
        return 0, 0
    if dollars_key in mapping or cents_key in mapping:
        total = money_to_cents(
            {dollars_key: mapping.get(dollars_key, 0), cents_key: mapping.get(cents_key, 0)}
        )
        return cents_to_parts(total)
    legacy = mapping.get(legacy_key)
    if legacy is not None:
        return cents_to_parts(money_to_cents(legacy))
    return 0, 0


def _ledger_tick(character, tick=None):
    """Best-effort game tick stamp for a ledger row."""
    if tick is not None:
        try:
            return int(tick)
        except (TypeError, ValueError):
            pass
    for attr in ("ledger_tick", "last_wallet_ledger_tick"):
        raw = getattr(character, attr, None)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    return 0


def ensure_wallet_ledger(character):
    """Return the mutable ledger list (newest entries at the end)."""
    migrate_wallet_fields(character)
    raw = getattr(character, "wallet_ledger", None)
    if not isinstance(raw, list):
        character.wallet_ledger = []
    return character.wallet_ledger


def record_wallet_ledger(
    character,
    *,
    delta_wallet_cents=0,
    delta_bank_cents=0,
    reason="Cash movement",
    tick=None,
):
    """Append one player-visible cash event (wallet and/or bank delta)."""
    dw = int(delta_wallet_cents or 0)
    db = int(delta_bank_cents or 0)
    if dw == 0 and db == 0:
        return
    row = {
        "tick": _ledger_tick(character, tick),
        "reason": str(reason or "Cash movement").strip()[:120],
        "wallet_cents": int(wallet_total_cents(character)),
        "bank_cents": int(bank_total_cents(character)),
        "delta_wallet_cents": dw,
        "delta_bank_cents": db,
    }
    ledger = ensure_wallet_ledger(character)
    ledger.append(row)
    if len(ledger) > WALLET_LEDGER_MAX:
        del ledger[: len(ledger) - WALLET_LEDGER_MAX]


def format_ledger_delta_cents(delta_cents):
    """Signed ``+$12.34`` / ``-$5`` for one bucket."""
    delta_cents = int(delta_cents or 0)
    if delta_cents == 0:
        return "$0"
    sign = "+" if delta_cents > 0 else "-"
    total = abs(delta_cents)
    return f"{sign}{format_money(total // 100, total % 100)}"


def format_wallet_ledger_lines(character, game=None, *, limit=15):
    """Player-facing ledger rows (newest first)."""
    ledger = list(getattr(character, "wallet_ledger", None) or [])
    if not ledger:
        return ["No cash movements logged yet."]
    limit = max(1, min(int(limit or 15), WALLET_LEDGER_MAX))
    rows = list(reversed(ledger[-limit:]))
    lines = [f"Cash log (last {len(rows)} entries, newest first):"]
    cal_mod = None
    if game is not None:
        try:
            from engine import game_calendar as cal_mod
        except ImportError:
            cal_mod = None
    for row in rows:
        tick = int(row.get("tick", 0) or 0)
        if cal_mod is not None and tick > 0:
            cal = cal_mod.breakdown(tick)
            stamp = (
                f"{int(cal['year']):04d}-"
                f"{int(cal['month']):02d}-"
                f"{int(cal['day']):02d}"
            )
        elif tick > 0:
            stamp = f"tick {tick}"
        else:
            stamp = "recent"
        parts = []
        dw = int(row.get("delta_wallet_cents", 0) or 0)
        db = int(row.get("delta_bank_cents", 0) or 0)
        if dw:
            parts.append(f"wallet {format_ledger_delta_cents(dw)}")
        if db:
            parts.append(f"bank {format_ledger_delta_cents(db)}")
        move = ", ".join(parts) if parts else "no change"
        wallet_after = format_money(
            int(row.get("wallet_cents", 0) or 0) // 100,
            int(row.get("wallet_cents", 0) or 0) % 100,
        )
        bank_after = format_money(
            int(row.get("bank_cents", 0) or 0) // 100,
            int(row.get("bank_cents", 0) or 0) % 100,
        )
        reason = row.get("reason") or "Cash movement"
        lines.append(
            f"  [{stamp}] {move} -- {reason} "
            f"(on hand {wallet_after}, bank {bank_after})"
        )
    lines.append("Tip: wallet log [n] for more rows (max %d)." % WALLET_LEDGER_MAX)
    return lines


def migrate_wallet_fields(character):
    """One-time in-memory: legacy ``coins`` -> ``dollars``; normalize cents."""
    legacy = getattr(character, "coins", None)
    if legacy is not None:
        character.dollars = int(legacy or 0)
        if not hasattr(character, "cents"):
            character.cents = 0
        try:
            del character.coins
        except AttributeError:
            pass
    legacy_bank = getattr(character, "bank_coins", None)
    if legacy_bank is not None:
        character.bank_dollars = int(legacy_bank or 0)
        if not hasattr(character, "bank_cents"):
            character.bank_cents = 0
        try:
            del character.bank_coins
        except AttributeError:
            pass
    d, c = _carry_cents(
        getattr(character, "dollars", 0),
        getattr(character, "cents", 0),
    )
    character.dollars = d
    character.cents = c
    bd, bc = _carry_cents(
        getattr(character, "bank_dollars", 0),
        getattr(character, "bank_cents", 0),
    )
    character.bank_dollars = bd
    character.bank_cents = bc


def wallet_dollars(character):
    migrate_wallet_fields(character)
    return int(getattr(character, "dollars", 0) or 0)


def wallet_cents(character):
    migrate_wallet_fields(character)
    return int(getattr(character, "cents", 0) or 0)


def wallet_total_cents(character):
    migrate_wallet_fields(character)
    return wallet_dollars(character) * 100 + wallet_cents(character)


def bank_dollars(character):
    migrate_wallet_fields(character)
    return int(getattr(character, "bank_dollars", 0) or 0)


def bank_cents(character):
    migrate_wallet_fields(character)
    return int(getattr(character, "bank_cents", 0) or 0)


def bank_total_cents(character):
    migrate_wallet_fields(character)
    return bank_dollars(character) * 100 + bank_cents(character)


def wallet_balance(character):
    """Whole dollars in the wallet (floor). Prefer ``wallet_total_cents``."""
    return wallet_dollars(character)


def bank_balance(character):
    """Whole dollars in the bank (floor). Prefer ``bank_total_cents``."""
    return bank_dollars(character)


def set_wallet(character, dollars, cents=0, *, reason=None, tick=None):
    """Set wallet to an exact (dollars, cents) pair."""
    before = wallet_total_cents(character)
    d, c = _carry_cents(dollars, cents)
    character.dollars = d
    character.cents = c
    if reason:
        delta = wallet_total_cents(character) - before
        if delta:
            record_wallet_ledger(
                character,
                delta_wallet_cents=delta,
                reason=reason,
                tick=tick,
            )


def set_bank(character, dollars, cents=0, *, reason=None, tick=None):
    """Set bank balance to an exact (dollars, cents) pair."""
    before = bank_total_cents(character)
    d, c = _carry_cents(dollars, cents)
    character.bank_dollars = d
    character.bank_cents = c
    if reason:
        delta = bank_total_cents(character) - before
        if delta:
            record_wallet_ledger(
                character,
                delta_bank_cents=delta,
                reason=reason,
                tick=tick,
            )


def credit_wallet(character, dollars=0, cents=0, *, reason=None, tick=None):
    """Add dollars/cents to the on-hand wallet."""
    migrate_wallet_fields(character)
    if cents != 0:
        delta = int(dollars or 0) * 100 + int(cents)
    else:
        delta = money_to_cents(dollars)
    total = wallet_total_cents(character) + delta
    d, c = divmod(max(0, total), 100)
    character.dollars = d
    character.cents = c
    if reason and delta:
        record_wallet_ledger(
            character,
            delta_wallet_cents=delta,
            reason=reason,
            tick=tick,
        )


def debit_wallet(character, dollars=0, cents=0, *, reason=None, tick=None):
    """Remove dollars/cents from wallet. Returns False if insufficient."""
    if cents != 0:
        need = int(dollars or 0) * 100 + int(cents)
    else:
        need = money_to_cents(dollars)
    if wallet_total_cents(character) < need:
        return False
    total = wallet_total_cents(character) - need
    d, c = divmod(total, 100)
    character.dollars = d
    character.cents = c
    if reason and need:
        record_wallet_ledger(
            character,
            delta_wallet_cents=-need,
            reason=reason,
            tick=tick,
        )
    return True


def credit_bank(character, dollars=0, cents=0, *, reason=None, tick=None):
    """Add dollars/cents to the bank balance."""
    migrate_wallet_fields(character)
    if cents != 0:
        delta = int(dollars or 0) * 100 + int(cents)
    else:
        delta = money_to_cents(dollars)
    total = bank_total_cents(character) + delta
    d, c = divmod(max(0, total), 100)
    character.bank_dollars = d
    character.bank_cents = c
    if reason and delta:
        record_wallet_ledger(
            character,
            delta_bank_cents=delta,
            reason=reason,
            tick=tick,
        )


def debit_bank(character, dollars=0, cents=0, *, reason=None, tick=None):
    """Remove dollars/cents from bank. Returns False if insufficient."""
    if cents != 0:
        need = int(dollars or 0) * 100 + int(cents)
    else:
        need = money_to_cents(dollars)
    if bank_total_cents(character) < need:
        return False
    total = bank_total_cents(character) - need
    d, c = divmod(total, 100)
    character.bank_dollars = d
    character.bank_cents = c
    if reason and need:
        record_wallet_ledger(
            character,
            delta_bank_cents=-need,
            reason=reason,
            tick=tick,
        )
    return True


def can_afford(character, amount, cents=0):
    """True when the on-hand wallet covers ``amount`` (any money shape)."""
    if cents != 0:
        need = int(amount) * 100 + int(cents)
    else:
        need = money_to_cents(amount)
    return wallet_total_cents(character) >= need


def format_money(dollars, cents=0):
    """Player-facing cash string from dollar/cents parts (``$12.34``)."""
    if cents == 0 and isinstance(dollars, (float, str, dict)):
        d, c = cents_to_parts(money_to_cents(dollars))
        dollars, cents = d, c
    d, c = _carry_cents(dollars, cents)
    if d < 0:
        if c:
            return f"-${abs(d)}.{c:02d}"
        return f"-${abs(d)}"
    if c:
        return f"${d}.{c:02d}"
    return f"${d}"


def format_wallet(character):
    """Format on-hand wallet for player messages."""
    migrate_wallet_fields(character)
    return format_money(character.dollars, character.cents)


def format_bank(character):
    """Format bank balance for player messages."""
    migrate_wallet_fields(character)
    return format_money(character.bank_dollars, character.bank_cents)


def money_noun(plural=True):
    """Singular/plural noun for help and prose (``dollar`` / ``dollars``)."""
    return "dollars" if plural else "dollar"


def money_score_label():
    """Score-sheet field label for the on-hand wallet."""
    return "Cash"


def _parse_deposit_amount(character, amount, *, bank=False):
    """Parse deposit/withdraw amount; return cents to move or None on error."""
    if isinstance(amount, str) and amount.strip().lower() == "all":
        if bank:
            return bank_total_cents(character)
        return wallet_total_cents(character)
    need = money_to_cents(amount)
    if need <= 0:
        return None
    return need


def deposit(character, amount, *, tick=None):
    """Move cash from wallet to bank. Returns (ok, message)."""
    cents = _parse_deposit_amount(character, amount, bank=False)
    if cents is None:
        return False, "Deposit how much? (a positive number, or 'all')"
    if wallet_total_cents(character) < cents:
        return False, f"You only have {format_wallet(character)} on you."
    debit_wallet(character, cents=cents)
    credit_bank(character, cents=cents)
    record_wallet_ledger(
        character,
        delta_wallet_cents=-cents,
        delta_bank_cents=cents,
        reason="Deposit to bank",
        tick=tick,
    )
    return True, (
        f"You deposit {format_money(cents // 100, cents % 100)}. "
        f"Wallet: {format_wallet(character)}. "
        f"Bank: {format_bank(character)}."
    )


def withdraw(character, amount, *, tick=None):
    """Move cash from bank to wallet. Returns (ok, message)."""
    cents = _parse_deposit_amount(character, amount, bank=True)
    if cents is None:
        return False, "Withdraw how much? (a positive number, or 'all')"
    if bank_total_cents(character) < cents:
        return False, f"You only have {format_bank(character)} in the bank."
    debit_bank(character, cents=cents)
    credit_wallet(character, cents=cents)
    record_wallet_ledger(
        character,
        delta_wallet_cents=cents,
        delta_bank_cents=-cents,
        reason="Withdraw from bank",
        tick=tick,
    )
    return True, (
        f"You withdraw {format_money(cents // 100, cents % 100)}. "
        f"Wallet: {format_wallet(character)}. "
        f"Bank: {format_bank(character)}."
    )
