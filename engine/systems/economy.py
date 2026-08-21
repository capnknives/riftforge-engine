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
        lower = text.lower()
        for suffix in (" dollars", " dollar", " bucks", " buck"):
            if lower.endswith(suffix):
                text = text[: len(text) - len(suffix)].strip()
                lower = text.lower()
                break
        # ``wallet pull 1 dollar`` / ``5 bucks`` -- first token is the amount.
        if " " in text:
            head = text.split(None, 1)[0]
            if head.replace(".", "", 1).isdigit():
                text = head
        if "." in text:
            whole, frac = text.split(".", 1)
            frac = (frac + "00")[:2]
            return int(whole or 0) * 100 + int(frac or 0)
        try:
            return int(text) * 100
        except ValueError:
            return 0
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
        try:
            tick = int(row.get("tick", 0) or 0)
        except (TypeError, ValueError):
            tick = 0
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


def heal_legacy_wallet_fields(game):
    """Boot sweep: normalize wallet attrs; count legacy blob loads.

    ``migrate_wallet_fields`` drops in-memory ``coins`` / ``bank_coins``.
    ``apply_character_blob`` stamps ``_wallet_legacy_coins_blob`` when a
    save still used the old JSON keys -- next autosave writes ``dollars``.
    """
    if game is None:
        return {"legacy_blob": 0, "legacy_attr": 0}
    from engine.char_index import iter_characters

    legacy_blob = 0
    legacy_attr = 0
    for char in iter_characters(game):
        if getattr(char, "_wallet_legacy_coins_blob", False):
            legacy_blob += 1
            try:
                del char._wallet_legacy_coins_blob
            except AttributeError:
                pass
        if getattr(char, "coins", None) is not None:
            legacy_attr += 1
        if getattr(char, "bank_coins", None) is not None:
            legacy_attr += 1
        migrate_wallet_fields(char)
    return {"legacy_blob": legacy_blob, "legacy_attr": legacy_attr}


def _pocket_wallet_item(character):
    """Worn pocket wallet Item when present (cash lives on the Item)."""
    try:
        from engine.systems import wallet_container as wallet_mod
    except ImportError:
        return None
    return wallet_mod.designated_wallet(character)


def _loose_cash_parts(character):
    """Cash carried without a worn wallet (``character.dollars/cents``)."""
    migrate_wallet_fields(character)
    if _pocket_wallet_item(character) is not None:
        return 0, 0
    return inventory_loose_cash_parts(character)


def inventory_loose_cash_parts(character):
    """Loose bills in open inventory (``character.dollars/cents``)."""
    migrate_wallet_fields(character)
    return _carry_cents(
        getattr(character, "dollars", 0),
        getattr(character, "cents", 0),
    )


def loose_cash_inventory_stacks(character):
    """Open-inventory stacks consumed by loose bills in hand."""
    d, c = inventory_loose_cash_parts(character)
    return 1 if (d > 0 or c > 0) else 0


def format_pocket_cash(character):
    """Player-facing pocket cash (wallet item or loose bills)."""
    migrate_wallet_fields(character)
    pocket = _pocket_wallet_item(character)
    if pocket is not None:
        from engine.systems import wallet_container as wallet_mod
        d, c = wallet_mod.wallet_cash_parts(pocket)
        return format_money(d, c)
    d, c = _loose_cash_parts(character)
    return format_money(d, c)


def pocket_cash_is_loose(character):
    """True when the character carries loose bills in open inventory."""
    d, c = inventory_loose_cash_parts(character)
    return d > 0 or c > 0


def carry_cash_total_cents(character):
    """All on-hand cash: worn wallet plus loose inventory bills."""
    migrate_wallet_fields(character)
    loose_d, loose_c = inventory_loose_cash_parts(character)
    total = loose_d * 100 + loose_c
    pocket = _pocket_wallet_item(character)
    if pocket is not None:
        from engine.systems import wallet_container as wallet_mod
        total += wallet_mod.wallet_total_cents(pocket)
    return total


def wallet_dollars(character):
    migrate_wallet_fields(character)
    pocket = _pocket_wallet_item(character)
    if pocket is not None:
        from engine.systems import wallet_container as wallet_mod
        d, _c = wallet_mod.wallet_cash_parts(pocket)
        return d
    d, _c = _loose_cash_parts(character)
    return d


def wallet_cents(character):
    migrate_wallet_fields(character)
    pocket = _pocket_wallet_item(character)
    if pocket is not None:
        from engine.systems import wallet_container as wallet_mod
        _d, c = wallet_mod.wallet_cash_parts(pocket)
        return c
    _d, c = _loose_cash_parts(character)
    return c


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
    pocket = _pocket_wallet_item(character)
    if pocket is not None:
        from engine.systems import wallet_container as wallet_mod
        wallet_mod.set_wallet_cash(pocket, d, c)
    else:
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
    """Add dollars/cents to on-hand pocket cash. Returns False if hands are full."""
    migrate_wallet_fields(character)
    if cents != 0:
        delta = int(dollars or 0) * 100 + int(cents)
    else:
        delta = money_to_cents(dollars)
    if delta <= 0:
        return True
    pocket = _pocket_wallet_item(character)
    if pocket is None:
        d0, c0 = _loose_cash_parts(character)
        if d0 == 0 and c0 == 0:
            from engine.systems.containers import (
                OPEN_INVENTORY_CAPACITY,
                open_stack_keys,
            )
            if len(open_stack_keys(character)) >= OPEN_INVENTORY_CAPACITY:
                from engine import log_util

                log_util.ops(
                    "wallet",
                    f"credit failed pocket full actor={getattr(character, 'key', '?')} "
                    f"delta_cents={delta} reason={reason or ''}",
                )
                return False
    total = wallet_total_cents(character) + delta
    d, c = divmod(max(0, total), 100)
    if pocket is not None:
        from engine.systems import wallet_container as wallet_mod
        wallet_mod.set_wallet_cash(pocket, d, c)
    else:
        character.dollars = d
        character.cents = c
    if reason and delta:
        record_wallet_ledger(
            character,
            delta_wallet_cents=delta,
            reason=reason,
            tick=tick,
        )
    return True


def debit_wallet(character, dollars=0, cents=0, *, reason=None, tick=None):
    """Remove dollars/cents from on-hand cash. Returns False if insufficient."""
    migrate_wallet_fields(character)
    if cents != 0:
        need = int(dollars or 0) * 100 + int(cents)
    else:
        need = money_to_cents(dollars)
    if carry_cash_total_cents(character) < need:
        if need > 0 and reason:
            from engine import log_util

            log_util.ops(
                "wallet",
                f"debit insufficient actor={getattr(character, 'key', '?')} "
                f"need_cents={need} reason={reason}",
            )
        return False
    remaining = need
    loose_d, loose_c = inventory_loose_cash_parts(character)
    loose_total = loose_d * 100 + loose_c
    if loose_total > 0 and remaining > 0:
        take_loose = min(loose_total, remaining)
        remaining -= take_loose
        new_loose = loose_total - take_loose
        ld, lc = divmod(new_loose, 100)
        character.dollars = ld
        character.cents = lc
    if remaining > 0:
        pocket = _pocket_wallet_item(character)
        if pocket is not None:
            from engine.systems import wallet_container as wallet_mod
            wallet_c = wallet_mod.wallet_total_cents(pocket)
            new_wallet = max(0, wallet_c - remaining)
            wd, wc = divmod(new_wallet, 100)
            wallet_mod.set_wallet_cash(pocket, wd, wc)
        else:
            return False
    if reason and need:
        record_wallet_ledger(
            character,
            delta_wallet_cents=-need,
            reason=reason,
            tick=tick,
        )
    return True


def transfer_wallet_to_loose(character, dollars=0, cents=0, *, reason=None, tick=None):
    """Move cash from a worn wallet into loose inventory bills.

    Returns ``(ok, message)``. Pulling the first loose bill needs a free
    open-inventory stack when hands are full.
    """
    migrate_wallet_fields(character)
    pocket = _pocket_wallet_item(character)
    if pocket is None:
        return False, "You need a pocket wallet for that."
    if cents != 0:
        need = int(dollars or 0) * 100 + int(cents)
    else:
        need = money_to_cents(dollars)
    if need <= 0:
        return False, "Pull how much? (a positive amount)"
    from engine.systems import wallet_container as wallet_mod
    wallet_c = wallet_mod.wallet_total_cents(pocket)
    if wallet_c < need:
        return False, (
            f"You only have {format_pocket_cash(character)} in your wallet."
        )
    loose_d, loose_c = inventory_loose_cash_parts(character)
    if loose_d == 0 and loose_c == 0:
        from engine.systems.containers import (
            OPEN_INVENTORY_CAPACITY,
            open_inventory_stack_count,
        )
        if open_inventory_stack_count(character) >= OPEN_INVENTORY_CAPACITY:
            return False, "Your hands are full."
    new_wallet = wallet_c - need
    wd, wc = divmod(new_wallet, 100)
    wallet_mod.set_wallet_cash(pocket, wd, wc)
    new_loose = loose_d * 100 + loose_c + need
    ld, lc = divmod(new_loose, 100)
    character.dollars = ld
    character.cents = lc
    if reason:
        record_wallet_ledger(
            character,
            delta_wallet_cents=-need,
            reason=reason,
            tick=tick,
        )
    pulled = format_money(need // 100, need % 100)
    return True, f"You pull {pulled} out of your wallet."


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
    """True when on-hand cash (wallet + loose bills) covers ``amount``."""
    if cents != 0:
        need = int(amount) * 100 + int(cents)
    else:
        need = money_to_cents(amount)
    return carry_cash_total_cents(character) >= need


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
    """Format on-hand pocket cash for player messages."""
    return format_pocket_cash(character)


def format_inventory_loose_cash(character):
    """Format loose bills in open inventory (not the worn wallet)."""
    d, c = inventory_loose_cash_parts(character)
    return format_money(d, c)


def format_carry_cash(character):
    """Format total on-hand cash (wallet + loose inventory bills)."""
    total = carry_cash_total_cents(character)
    return format_money(total // 100, total % 100)


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
        return carry_cash_total_cents(character)
    need = money_to_cents(amount)
    if need <= 0:
        return None
    return need


def deposit(character, amount, *, tick=None):
    """Move cash from wallet to bank. Returns (ok, message)."""
    cents = _parse_deposit_amount(character, amount, bank=False)
    if cents is None:
        return False, "Deposit how much? (a positive number, or 'all')"
    if carry_cash_total_cents(character) < cents:
        return False, f"You only have {format_carry_cash(character)} on you."
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
