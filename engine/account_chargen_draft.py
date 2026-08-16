"""
account_chargen_draft.py -- account-scoped mid-chargen resume (Phase 5).

When create flow links a body then ``hooks.run_chargen`` aborts (disconnect),
the partial body is vaulted with ``CHARGEN_DRAFT_VAULT_TAG`` and the owning
account keeps ``chargen_draft_key``. Re-login offers the same roster row with
a resume label; picking it restores and re-enters chargen instead of treating
the row like a finished character or a generic fold vault.
"""

from __future__ import annotations

import time

from engine import hooks

# Vault ``folded_by`` tag for incomplete create-flow bodies.
CHARGEN_DRAFT_VAULT_TAG = "chargen-draft"

# Rolling window for ``create_account`` rate limits (per peer + global).
ACCOUNT_CREATE_WINDOW_SEC = 3600
ACCOUNT_CREATE_MAX_PER_PEER = 5
ACCOUNT_CREATE_MAX_GLOBAL = 40

# peer key -> list of monotonic timestamps (process-local; resets on restart).
_account_create_times: dict[str, list[float]] = {}
_account_create_global: list[float] = []


def _session_peer(session) -> str:
    """Best-effort client key for rate limiting."""
    writer = getattr(session, "writer", None)
    if writer is not None:
        try:
            peername = writer.get_extra_info("peername")
            if peername and peername[0]:
                return str(peername[0])
        except Exception:
            pass
    gw = getattr(session, "gateway_session_id", None)
    if gw:
        return f"gw:{gw}"
    return getattr(session, "_log_session_id", None) or "unknown"


def _prune_times(times: list[float], *, now: float, window: float) -> list[float]:
    """Drop entries older than ``window`` seconds."""
    cutoff = now - window
    return [t for t in times if t >= cutoff]


def account_create_rate_limit_error(session) -> str | None:
    """Return a player-facing refusal when create is rate-limited, else None."""
    peer = _session_peer(session)
    now = time.monotonic()
    global _account_create_global

    peer_times = _prune_times(
        _account_create_times.get(peer, []),
        now=now,
        window=ACCOUNT_CREATE_WINDOW_SEC,
    )
    _account_create_times[peer] = peer_times
    _account_create_global = _prune_times(
        _account_create_global,
        now=now,
        window=ACCOUNT_CREATE_WINDOW_SEC,
    )

    if len(peer_times) >= ACCOUNT_CREATE_MAX_PER_PEER:
        return (
            "Too many new accounts from your connection recently. "
            "Wait a while and try again."
        )
    if len(_account_create_global) >= ACCOUNT_CREATE_MAX_GLOBAL:
        return (
            "Account registration is temporarily busy. "
            "Try again in a few minutes."
        )
    return None


def note_account_created(session) -> None:
    """Record a successful account registration for rate limiting."""
    peer = _session_peer(session)
    now = time.monotonic()
    global _account_create_global

    peer_times = _prune_times(
        _account_create_times.get(peer, []),
        now=now,
        window=ACCOUNT_CREATE_WINDOW_SEC,
    )
    peer_times.append(now)
    _account_create_times[peer] = peer_times
    _account_create_global = _prune_times(
        _account_create_global,
        now=now,
        window=ACCOUNT_CREATE_WINDOW_SEC,
    )
    _account_create_global.append(now)


def get_chargen_draft_key(account) -> str:
    """Storage key for the in-progress create body, or ``""``."""
    if account is None:
        return ""
    return (getattr(account, "chargen_draft_key", None) or "").strip()


def set_chargen_draft_key(account, key) -> None:
    """Stamp or clear the account's in-progress create key."""
    if account is None:
        return
    cleaned = (key or "").strip()
    account.chargen_draft_key = cleaned


def clear_chargen_draft(account, game) -> None:
    """Clear draft pointer and persist account row."""
    if account is None:
        return
    if not get_chargen_draft_key(account):
        return
    set_chargen_draft_key(account, "")
    try:
        from engine.persistence import mark_account_dirty

        mark_account_dirty(game, account)
    except Exception:
        pass


def is_chargen_draft_vault(game, storage_key) -> bool:
    """True when ``storage_key`` is vaulted for mid-chargen resume."""
    if not storage_key:
        return False
    tag = hooks.vault_folded_by(game, storage_key)
    if tag is None:
        return False
    return tag == CHARGEN_DRAFT_VAULT_TAG


def is_chargen_draft_character(game, character) -> bool:
    """True when ``character`` still owes create-flow chargen."""
    if character is None:
        return False
    key = (getattr(character, "key", None) or "").strip()
    if not key:
        return False
    if is_chargen_draft_vault(game, key):
        return True
    if getattr(character, "chargen_draft", False):
        return True
    # Linked body with no Path yet and explicit account draft pointer.
    account_name = (getattr(character, "account", None) or "").strip()
    if not account_name:
        return False
    if getattr(character, "path", None):
        return False
    from engine import accounts as accounts_mod

    account = accounts_mod.find_account(game, account_name)
    if account is None:
        return False
    draft_key = get_chargen_draft_key(account)
    return draft_key.lower() == key.lower()


def stamp_chargen_draft(account, character, game) -> None:
    """Mark ``character`` as the account's in-progress create body."""
    if account is None or character is None:
        return
    key = (getattr(character, "key", None) or "").strip()
    if not key:
        return
    character.chargen_draft = True
    set_chargen_draft_key(account, key)
    try:
        from engine.persistence import mark_account_dirty

        mark_account_dirty(game, account)
    except Exception:
        pass


def vault_chargen_draft(character, game, account) -> tuple[bool, str]:
    """Vault a partial create body for later resume."""
    if character is None or game is None:
        return False, "Nothing to vault."
    # Detach session without leaving a stray Echo in the world.
    character.session = None
    if getattr(character, "location", None) is not None:
        try:
            character.location.remove(character)
        except Exception:
            pass
        character.location = None
    ok, msg = hooks.extract_to_vault(
        character,
        game,
        folded_by=CHARGEN_DRAFT_VAULT_TAG,
    )
    if ok and account is not None:
        stamp_chargen_draft(account, character, game)
    return ok, msg


def finish_chargen_draft(account, game) -> None:
    """Clear draft flags after successful chargen."""
    if account is None:
        return
    clear_chargen_draft(account, game)
