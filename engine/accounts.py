"""
accounts.py -- engine-level Account store (owns characters + staff prefs).

An Account is a login identity above Characters:

* Own name + password (independent of per-character passwords).
* Owns zero or more character storage keys (``character_keys``).
* Optional staff ``gm_rank`` (``""`` / ``"gm"`` / ``"head_gm"``) and a
  durable GM spirit key (``gm_spirit_key``).
* Contribution totals (bugs squashed / features suggested) and prefs
  (OOC identity, GM see-accounts).

This module is pure domain: no networking, no SQLite. Persistence wires
through ``engine/persistence.py`` (``accounts`` table); login / link
prompts live in ``engine/connection.py``.

Characters keep a back-pointer ``character.account`` (normalized account
name, or ``""`` when unlinked). Boot heal ``reconcile_accounts`` keeps
the two sides honest.
"""

from __future__ import annotations

from typing import Optional

from engine import auth

# Mirror character login name bounds (engine/connection.LOGIN_NAME_*).
ACCOUNT_NAME_MIN = 2
ACCOUNT_NAME_MAX = 16

# OOC speaker label: account display name vs character presence face.
OOC_IDENTITY_ACCOUNT = "account"
OOC_IDENTITY_CHARACTER = "character"
OOC_IDENTITY_CHOICES = (OOC_IDENTITY_ACCOUNT, OOC_IDENTITY_CHARACTER)

# Valid staff ranks on an account (same vocabulary as Character.gm_rank).
GM_RANKS = ("", "gm", "head_gm")


class Account:
    """One player account: credentials, owned characters, prefs, totals.

    ``name`` is the normalized storage key (title-cased first letter,
    letters only) -- same shape as a login character key. ``display_name``
    is what OOC / reports show when the account identity is chosen
    (defaults to ``name``; may later diverge for casing polish).
    """

    def __init__(self, name, password_hash="", display_name=None):
        """Build a blank Account; callers fill fields or apply a blob."""
        cleaned = (name or "").strip()
        self.name = cleaned
        # Public OOC / report face; never shown in IC room text.
        self.display_name = (display_name or cleaned).strip() or cleaned
        self.password_hash = password_hash or ""
        # Storage keys of Characters linked to this account.
        self.character_keys = []
        # Staff rank lives HERE (authoritative). Legacy per-character
        # gm_rank is only a migration fallback until heal moves it.
        self.gm_rank = ""
        # Durable staff spirit Character.key (gmspirit:{account}).
        self.gm_spirit_key = None
        # Contribution tallies (recomputed from report logs on heal).
        self.bugs_squashed = 0
        # Resolved suggestions shipped from player ideas (log is source of truth).
        self.features_suggested = 0
        # Prefs: which face OOC uses; whether GM form appends (Account).
        self.ooc_identity = OOC_IDENTITY_CHARACTER
        self.gm_see_accounts = False
        # Staff "wizard invisibility": when True (default), this account's
        # GM form (`gm on`) is visible only to other GM-form staff. Toggle
        # off (`wizinvis off`) to let ALL characters see the form as
        # ``Accountname(GM)`` -- a deliberate staff reveal. Parked spirits
        # (`gm off`) are always invisible regardless of this flag.
        self.wizinvis = True
        # Playtester diagnostic tools (``playtest`` / ``playtester add``).
        self.playtester = False
        # Visit-ceiling for in-game ``changes`` (new since last acknowledgment).
        # Legacy ``last_seen_changelog_id`` is uplifted once on read.
        self.last_seen_changelog_sort_ts = ""
        self.changelog_visit_migrated = False
        # Legacy numeric watermark (pre Aug 2026 timestamp-only feed).
        self.last_seen_changelog_id = 0
        # Phase 5: storage key of a body mid create-flow chargen (resume draft).
        self.chargen_draft_key = ""
        # Account names blocked on OOC + player public channels (not IC say/tell).
        self.ooc_blocked_accounts = []

    def to_blob(self):
        """JSON-serializable extras for the accounts.data column."""
        return {
            "character_keys": list(self.character_keys),
            "gm_rank": self.gm_rank or "",
            "gm_spirit_key": self.gm_spirit_key,
            "bugs_squashed": int(self.bugs_squashed or 0),
            "features_suggested": int(self.features_suggested or 0),
            "ooc_identity": (
                self.ooc_identity
                if self.ooc_identity in OOC_IDENTITY_CHOICES
                else OOC_IDENTITY_CHARACTER
            ),
            "gm_see_accounts": bool(self.gm_see_accounts),
            "wizinvis": bool(self.wizinvis),
            "playtester": bool(self.playtester),
            "last_seen_changelog_id": int(
                getattr(self, "last_seen_changelog_id", 0) or 0
            ),
            "last_seen_changelog_sort_ts": str(
                getattr(self, "last_seen_changelog_sort_ts", "") or ""
            ),
            "changelog_visit_migrated": bool(
                getattr(self, "changelog_visit_migrated", False)
            ),
            "chargen_draft_key": str(
                getattr(self, "chargen_draft_key", "") or ""
            ),
            "ooc_blocked_accounts": list(
                getattr(self, "ooc_blocked_accounts", None) or []
            ),
        }

    def apply_blob(self, data):
        """Restore extras from a saved JSON dict (missing keys = defaults)."""
        if not isinstance(data, dict):
            return
        keys = data.get("character_keys") or []
        if isinstance(keys, (list, tuple)):
            cleaned = []
            seen = set()
            for raw in keys:
                k = str(raw or "").strip()
                if not k or k.lower() in seen:
                    continue
                seen.add(k.lower())
                cleaned.append(k)
            self.character_keys = cleaned
        rank = data.get("gm_rank", "") or ""
        self.gm_rank = rank if rank in ("gm", "head_gm") else ""
        spirit = data.get("gm_spirit_key")
        self.gm_spirit_key = (
            str(spirit).strip() if isinstance(spirit, str) and spirit.strip()
            else None
        )
        self.bugs_squashed = int(data.get("bugs_squashed", 0) or 0)
        self.features_suggested = int(data.get("features_suggested", 0) or 0)
        ooc = (data.get("ooc_identity") or OOC_IDENTITY_CHARACTER).strip().lower()
        self.ooc_identity = (
            ooc if ooc in OOC_IDENTITY_CHOICES else OOC_IDENTITY_CHARACTER
        )
        self.gm_see_accounts = bool(data.get("gm_see_accounts", False))
        # Missing key = default staff-invisible (old saves keep prior behavior).
        self.wizinvis = bool(data.get("wizinvis", True))
        self.playtester = bool(data.get("playtester", False))
        self.last_seen_changelog_id = int(
            data.get("last_seen_changelog_id", 0) or 0
        )
        self.last_seen_changelog_sort_ts = str(
            data.get("last_seen_changelog_sort_ts", "") or ""
        )
        self.changelog_visit_migrated = bool(
            data.get("changelog_visit_migrated", False)
        )
        self.chargen_draft_key = str(data.get("chargen_draft_key", "") or "")
        blocked = data.get("ooc_blocked_accounts") or []
        cleaned_blocked = []
        seen_blocked = set()
        if isinstance(blocked, (list, tuple)):
            for raw in blocked:
                key = account_lookup_key(str(raw or ""))
                if not key or key in seen_blocked:
                    continue
                seen_blocked.add(key)
                cleaned_blocked.append(key)
        self.ooc_blocked_accounts = cleaned_blocked


def normalize_account_name(raw):
    """Clean and validate an account name.

    Returns ``(cleaned_name, error_or_None)``. Same letter/length rules as
    character login names so the keyword route cannot collide with odd
    punctuation identities.
    """
    stripped = (raw or "").strip()
    if (
        not stripped
        or not stripped.isalpha()
        or not (ACCOUNT_NAME_MIN <= len(stripped) <= ACCOUNT_NAME_MAX)
    ):
        return (
            stripped,
            (
                f"Account names are {ACCOUNT_NAME_MIN}-{ACCOUNT_NAME_MAX} "
                "letters (no digits or spaces)."
            ),
        )
    # Title-case leading letter only (matches normalize_login_name).
    cleaned = stripped[0].upper() + stripped[1:]
    return cleaned, None


def account_lookup_key(name):
    """Case-insensitive dict key for ``game.accounts``."""
    return (name or "").strip().lower()


def find_account(game, name):
    """Return the Account for ``name``, or None.

    Looks up ``game.accounts`` by case-insensitive key.
    """
    if game is None or not name:
        return None
    accounts = getattr(game, "accounts", None)
    if not isinstance(accounts, dict):
        return None
    return accounts.get(account_lookup_key(name))


def ensure_accounts_dict(game):
    """Make sure ``game.accounts`` is a dict; return it."""
    accounts = getattr(game, "accounts", None)
    if not isinstance(accounts, dict):
        accounts = {}
        game.accounts = accounts
    return accounts


def register_account(game, account):
    """Insert ``account`` into ``game.accounts`` (overwrites same key)."""
    accounts = ensure_accounts_dict(game)
    accounts[account_lookup_key(account.name)] = account
    try:
        from engine.persistence import mark_account_dirty
        mark_account_dirty(game, account)
    except Exception:
        pass
    return account


def create_account(game, name, password, *, display_name=None):
    """Mint a new Account, hash the password, and register it.

    Returns ``(account, error_or_None)``. Does not link any character.
    """
    cleaned, err = normalize_account_name(name)
    if err:
        return None, err
    if find_account(game, cleaned) is not None:
        return None, "That account name is already taken."
    # Also refuse if a live character already uses the same storage key
    # as the account name would (reduces login-prompt confusion).
    finder = getattr(game, "find_character", None)
    if callable(finder):
        clash = finder(cleaned)
        if clash is not None and not getattr(clash, "is_npc", False):
            # Allow if that character will be the first link -- still OK
            # to share the spelling; login routes are distinct (keyword
            # ``account`` vs character name). No hard clash.
            pass
    policy = auth.password_policy_error(password, for_gm=False)
    if policy:
        return None, policy
    account = Account(
        cleaned,
        password_hash=auth.hash_password(password),
        display_name=display_name or cleaned,
    )
    register_account(game, account)
    return account, None


def verify_account_password(account, password):
    """True when ``password`` matches the account hash."""
    if account is None:
        return False
    return auth.verify_password(password or "", account.password_hash or "")


def link_character(game, account, character):
    """Attach ``character`` to ``account`` (both directions).

    Returns an error string on failure, else None. Unlinks from any
    previous account first.
    """
    if account is None or character is None:
        return "Nothing to link."
    key = getattr(character, "key", None) or ""
    if not key:
        return "That character has no storage key."
    if getattr(character, "is_npc", False):
        return "NPCs cannot be linked to an account."
    if getattr(character, "immersion", False):
        return "Immersion cast cannot be linked to an account."
    # Refuse husks / gm spirits -- only real login bodies.
    key_low = key.lower()
    if key_low.startswith("husk:") or key_low.startswith("gmspirit:"):
        return "Only playable characters can be linked to an account."
    # Drop from a previous account if any.
    prev_name = (getattr(character, "account", None) or "").strip()
    if prev_name:
        prev = find_account(game, prev_name)
        if prev is not None and prev is not account:
            unlink_character(game, prev, character, clear_back_pointer=False)
    # Add to this account's roster (case-insensitive dedupe).
    existing_low = {k.lower() for k in account.character_keys}
    if key_low not in existing_low:
        account.character_keys.append(key)
    character.account = account.name
    try:
        from engine.persistence import mark_account_dirty
        mark_account_dirty(game, account)
        if prev_name:
            prev = find_account(game, prev_name)
            if prev is not None and prev is not account:
                mark_account_dirty(game, prev)
    except Exception:
        pass
    return None


def unlink_character(game, account, character, *, clear_back_pointer=True):
    """Remove ``character`` from ``account.character_keys``.

    When ``clear_back_pointer`` is True, also clears ``character.account``.
    """
    if account is None or character is None:
        return
    key = getattr(character, "key", None) or ""
    key_low = key.lower()
    account.character_keys = [
        k for k in account.character_keys if k.lower() != key_low
    ]
    if clear_back_pointer:
        # Only clear if the back-pointer still names this account.
        if account_lookup_key(getattr(character, "account", "")) == (
            account_lookup_key(account.name)
        ):
            character.account = ""
    try:
        from engine.persistence import mark_account_dirty
        mark_account_dirty(game, account)
    except Exception:
        pass


def account_for_character(game, character):
    """Resolve the Account linked to ``character``, or None."""
    if character is None:
        return None
    name = (getattr(character, "account", None) or "").strip()
    if not name:
        return None
    return find_account(game, name)


def account_name_for_character_key(game, speaker_key) -> Optional[str]:
    """Linked account storage name for a character key, or None."""
    if not speaker_key or game is None:
        return None
    finder = getattr(game, "find_character", None)
    if not callable(finder):
        return None
    speaker = finder(speaker_key)
    if speaker is None:
        return None
    linked = (getattr(speaker, "account", None) or "").strip()
    return linked or None


def resolve_block_target_account(game, name):
    """Resolve ``name`` to an Account for OOC/public-channel blocking.

    Accepts an account name or a character name (online or Echo). Returns
    ``(account, error_message)``; error is set when lookup fails.
    """
    raw = (name or "").strip()
    if not raw:
        return None, "Usage: block <account|character>"
    acct = find_account(game, raw)
    if acct is not None:
        return acct, None
    finder = getattr(game, "find_character", None)
    if callable(finder):
        character = finder(raw)
        if character is not None:
            acct = account_for_character(game, character)
            if acct is not None:
                return acct, None
            return None, (
                f"{character.key} is not linked to an account yet — "
                "use the account name instead."
            )
    return None, f"No account or character named '{raw}'."


def ooc_is_blocked(listener_account, speaker_account_name) -> bool:
    """True when *listener_account* blocks *speaker_account_name* on OOC nets."""
    if listener_account is None or not speaker_account_name:
        return False
    key = account_lookup_key(speaker_account_name)
    if not key:
        return False
    blocked = getattr(listener_account, "ooc_blocked_accounts", None) or []
    return key in {account_lookup_key(x) for x in blocked}


def ooc_should_hide_from_viewer(viewer, game, speaker_key) -> bool:
    """True when *viewer* has blocked the speaker's account on OOC/public nets."""
    if viewer is None or game is None:
        return False
    listener = account_for_character(game, viewer)
    if listener is None:
        return False
    speaker_account = account_name_for_character_key(game, speaker_key)
    if not speaker_account:
        return False
    return ooc_is_blocked(listener, speaker_account)


def ooc_block_account(game, listener_account, target_name):
    """Block an account on OOC/public channels. Returns ``(ok, message)``."""
    target, err = resolve_block_target_account(game, target_name)
    if target is None:
        return False, err or "Could not resolve that name."
    if listener_account is None:
        return False, "You need a linked account to block someone."
    if account_lookup_key(target.name) == account_lookup_key(listener_account.name):
        return False, "You cannot block yourself."
    blocked = list(getattr(listener_account, "ooc_blocked_accounts", None) or [])
    key = account_lookup_key(target.name)
    if key in {account_lookup_key(x) for x in blocked}:
        return False, f"You already block {target.display_name or target.name} on OOC channels."
    blocked.append(target.name)
    listener_account.ooc_blocked_accounts = blocked
    try:
        from engine.persistence import mark_account_dirty
        mark_account_dirty(game, listener_account)
    except Exception:
        pass
    label = target.display_name or target.name
    return True, (
        f"You will no longer hear {label} on OOC or player public channels. "
        "In-character say, tell, phone, and radio are unchanged."
    )


def ooc_unblock_account(game, listener_account, target_name):
    """Clear one OOC/public-channel block. Returns ``(ok, message)``."""
    raw = (target_name or "").strip()
    if not raw:
        return False, "Usage: unblock <account|character>"
    if listener_account is None:
        return False, "You need a linked account to manage blocks."
    target_key = account_lookup_key(raw)
    blocked = list(getattr(listener_account, "ooc_blocked_accounts", None) or [])
    kept = []
    removed_label = None
    for entry in blocked:
        if account_lookup_key(entry) == target_key:
            acct = find_account(game, entry)
            removed_label = (
                (acct.display_name or acct.name) if acct is not None else entry
            )
            continue
        kept.append(entry)
    if removed_label is None:
        target, err = resolve_block_target_account(game, raw)
        if target is None:
            return False, err or f"You are not blocking '{raw}'."
        kept = [
            x for x in blocked
            if account_lookup_key(x) != account_lookup_key(target.name)
        ]
        removed_label = target.display_name or target.name
    listener_account.ooc_blocked_accounts = kept
    try:
        from engine.persistence import mark_account_dirty
        mark_account_dirty(game, listener_account)
    except Exception:
        pass
    return True, f"You can hear {removed_label} on OOC and player public channels again."


def ooc_list_blocked(game, listener_account):
    """Return display labels for blocked accounts (may be empty)."""
    if listener_account is None:
        return []
    blocked = getattr(listener_account, "ooc_blocked_accounts", None) or []
    labels = []
    for entry in blocked:
        acct = find_account(game, entry) if game is not None else None
        if acct is not None:
            labels.append(acct.display_name or acct.name)
        else:
            labels.append(str(entry or "").strip())
    return labels


def effective_gm_rank(game, character):
    """Authoritative staff rank for ``character``.

    Prefers the linked account's ``gm_rank``. Falls back to the legacy
    per-character ``gm_rank`` during migration (characters not yet on an
    account, or heal not yet run).
    """
    account = account_for_character(game, character)
    if account is not None:
        rank = (account.gm_rank or "").strip()
        if rank in ("gm", "head_gm"):
            return rank
        # Account exists but has no rank -- still allow legacy body rank
        # until migration heal clears it (idempotent heal moves it up).
    legacy = (getattr(character, "gm_rank", None) or "").strip()
    if legacy in ("gm", "head_gm"):
        return legacy
    return ""


def gm_spirit_key_for_account(account):
    """Stable storage key for an account's permanent GM spirit."""
    if account is None:
        return None
    existing = getattr(account, "gm_spirit_key", None)
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    return f"gmspirit:{account.name}"


def _vaulted_character_keys_lower(game):
    """Lowercase storage keys in ``character_vault`` (folded offline bodies).

    Folded characters are intentionally absent from ``game.characters`` at
    boot. ``reconcile_accounts`` must not drop their roster slots or account
    login / ``gm off <name>`` will think they do not exist until someone
    manually relinks.
    """
    db = getattr(game, "db", None)
    if db is None:
        return set()
    try:
        from engine import persistence

        return {
            (row[0] or "").strip().lower()
            for row in persistence.vault_list(db)
            if row and (row[0] or "").strip()
        }
    except Exception:
        return set()


def _playable_account_link_key(char):
    """Storage key for account roster membership, or None when not linkable."""
    if char is None or getattr(char, "is_npc", False):
        return None
    if getattr(char, "immersion", False):
        return None
    key = (getattr(char, "key", None) or "").strip()
    if not key:
        return None
    key_low = key.lower()
    if key_low.startswith("husk:") or key_low.startswith("gmspirit:"):
        return None
    return key


def list_orphan_characters(game, *, limit=50):
    """Playable bodies with no ``character.account`` link (live roster only).

    Returns a list of dicts ``{key, face, location, vaulted}`` sorted by key.
    """
    if game is None:
        return []
    from engine.command_support import _presence_face

    rows = []
    seen = set()
    for char in list(getattr(game, "characters", None) or []):
        key = _playable_account_link_key(char)
        if not key:
            continue
        if (getattr(char, "account", None) or "").strip():
            continue
        low = key.lower()
        if low in seen:
            continue
        seen.add(low)
        room = getattr(char, "location", None)
        rows.append({
            "key": key,
            "face": _presence_face(char),
            "location": getattr(room, "key", None) or "(nowhere)",
            "vaulted": False,
        })
    rows.sort(key=lambda row: row["key"].lower())
    if limit and len(rows) > limit:
        return rows[:limit]
    return rows


def reconcile_accounts(game):
    """Boot heal: keep account.character_keys ↔ character.account honest.

    Idempotent. Returns a small stats dict for logs / smoke.
    """
    accounts = ensure_accounts_dict(game)
    linked = 0
    repaired_back = 0
    dropped_stale = 0
    vaulted_kept = 0
    roster_repaired = 0
    cast_unlinked = 0
    vaulted_keys = _vaulted_character_keys_lower(game)
    # Index characters by lower key for fast lookup.
    by_key = {}
    for char in list(getattr(game, "characters", None) or []):
        k = getattr(char, "key", None) or ""
        if k:
            by_key[k.lower()] = char

    for account in list(accounts.values()):
        kept = []
        seen = set()
        for raw_key in list(account.character_keys):
            k = (raw_key or "").strip()
            if not k:
                continue
            low = k.lower()
            if low in seen:
                continue
            seen.add(low)
            char = by_key.get(low)
            if char is None:
                # Folded bodies live in character_vault, not the live roster.
                if low in vaulted_keys:
                    kept.append(k)
                    vaulted_kept += 1
                    continue
                # Truly gone -- drop from roster.
                dropped_stale += 1
                continue
            if getattr(char, "immersion", False):
                # Cast bodies ride the staff menu only -- never account rosters.
                dropped_stale += 1
                if (getattr(char, "account", None) or "").strip():
                    char.account = ""
                    repaired_back += 1
                    cast_unlinked += 1
                continue
            kept.append(getattr(char, "key", k) or k)
            # Force back-pointer to this account.
            if (getattr(char, "account", None) or "") != account.name:
                char.account = account.name
                repaired_back += 1
            linked += 1
        account.character_keys = kept
        # Ensure gm_spirit_key is stamped when ranked.
        if account.gm_rank in ("gm", "head_gm"):
            account.gm_spirit_key = gm_spirit_key_for_account(account)

    # Live bodies may carry ``character.account`` without a roster row when
    # a prior boot dropped them while vaulted (old reconcile) or link only
    # stamped the back-pointer.
    cast_unlinked = 0
    for char in by_key.values():
        key = _playable_account_link_key(char)
        if not key:
            # Immersion cast must never ride account rosters.
            if getattr(char, "immersion", False) and (
                getattr(char, "account", None) or ""
            ).strip():
                char.account = ""
                repaired_back += 1
                cast_unlinked += 1
            continue
        claimed = (getattr(char, "account", None) or "").strip()
        if not claimed:
            continue
        account = find_account(game, claimed)
        if account is None:
            char.account = ""
            repaired_back += 1
            continue
        existing_low = {k.lower() for k in account.character_keys}
        if key.lower() not in existing_low:
            account.character_keys.append(key)
            roster_repaired += 1
            linked += 1

    # Characters that claim an account which does not exist -- clear.
    for char in by_key.values():
        claimed = (getattr(char, "account", None) or "").strip()
        if not claimed:
            continue
        if find_account(game, claimed) is None:
            char.account = ""
            repaired_back += 1

    return {
        "accounts": len(accounts),
        "links": linked,
        "repaired": repaired_back,
        "dropped": dropped_stale,
        "vaulted_kept": vaulted_kept,
        "roster_repaired": roster_repaired,
        "cast_unlinked": cast_unlinked,
    }


# Known player accounts wiped by a shutdown-save race (empty in-memory
# accounts dict rewritten over SQLite). Boot heal restores them once so
# a single game-only restart is enough -- no offline DB race.
_RESTORED_PLAYER_ACCOUNTS = (
    {
        "name": "Matt",
        "character_keys": ("Daniel", "Vorath"),
        # Temporary until Matt changes it; character login still uses
        # each body's own password_hash.
        "temp_password": "Restore2026",
    },
)


def heal_restored_player_accounts(game):
    """Boot heal: recreate known wiped player accounts and relink bodies.

    Idempotent. Runs after ``load_accounts`` / ``reconcile_accounts`` so the
    in-memory dict already has staff rows; creating Matt here means the
    next ``save_accounts`` keeps everyone. Returns how many accounts were
    created or re-linked.
    """
    accounts = ensure_accounts_dict(game)
    finder = getattr(game, "find_character", None)
    if not callable(finder):
        return {"created": 0, "linked": 0}

    created = 0
    linked = 0
    for spec in _RESTORED_PLAYER_ACCOUNTS:
        name = (spec.get("name") or "").strip()
        if not name:
            continue
        account = find_account(game, name)
        if account is None:
            temp = (spec.get("temp_password") or "").strip() or "Restore2026"
            # Prefer copying a linked body's password hash when present so
            # the player can reuse a password they already know; otherwise
            # hash the documented temp password.
            pw_hash = ""
            for key in spec.get("character_keys") or ():
                char = finder(key)
                if char is None:
                    continue
                body_hash = (getattr(char, "password_hash", None) or "").strip()
                if body_hash:
                    pw_hash = body_hash
                    break
            used_body_hash = bool(pw_hash)
            if not pw_hash:
                pw_hash = auth.hash_password(temp)
            account = Account(name, password_hash=pw_hash, display_name=name)
            account.ooc_identity = OOC_IDENTITY_ACCOUNT
            register_account(game, account)
            created += 1
            how = (
                "copied a linked character password_hash"
                if used_body_hash
                else f"temp password {temp!r} (player should reset)"
            )
            print(
                f"[accounts] boot-heal restored account {name!r} ({how})",
                flush=True,
            )

        for key in spec.get("character_keys") or ():
            char = finder(key)
            if char is None:
                continue
            err = link_character(game, account, char)
            if err is None:
                linked += 1
            elif err:
                print(
                    f"[accounts] boot-heal link {key!r} -> {name!r}: {err}",
                    flush=True,
                )

    return {"created": created, "linked": linked, "accounts": len(accounts)}


def _roster_password_hash(game, key):
    """Best-effort password_hash for a roster key (live, login, or vault)."""
    finder = getattr(game, "find_character", None)
    if callable(finder):
        char = finder(key)
        if char is None:
            login_finder = getattr(game, "find_login_character", None)
            if callable(login_finder):
                char = login_finder(key)
        if char is not None:
            body_hash = (getattr(char, "password_hash", None) or "").strip()
            if body_hash:
                return body_hash
    db = getattr(game, "db", None)
    if db is None:
        return ""
    from engine import persistence

    row = persistence.vault_get(db, key)
    if row is None:
        return ""
    try:
        envelope = persistence.decompress_vault_envelope(row[4])
        stats = envelope.get("stats") or {}
        return (stats.get("password_hash") or "").strip()
    except Exception:
        return ""


def heal_empty_account_passwords(game):
    """Boot heal: copy a linked character hash when account password is blank.

    Pre-account-first saves often registered account rows without ever setting
    ``password_hash`` (login was character-name + body password only). Those
    accounts are unrecoverable until we copy the first linked body's hash.
    """
    accounts = ensure_accounts_dict(game)
    if getattr(game, "find_character", None) is None and getattr(game, "db", None) is None:
        return {"healed": 0}

    healed = 0
    for account in accounts.values():
        if (account.password_hash or "").strip():
            continue
        for key in account.character_keys:
            body_hash = _roster_password_hash(game, key)
            if not body_hash:
                continue
            account.password_hash = body_hash
            healed += 1
            try:
                from engine.persistence import mark_account_dirty
                mark_account_dirty(game, account)
            except Exception:
                pass
            print(
                f"[accounts] boot-heal copied password_hash onto account "
                f"{account.name!r} from character {key!r}",
                flush=True,
            )
            break
    return {"healed": healed}


def migrate_legacy_gm_ranks(game):
    """Move per-character ``gm_rank`` onto linked (or auto-made) accounts.

    For each non-NPC character with a legacy ``gm_rank``:

    * If already on an account, copy rank onto the account (max of the
      two: head_gm wins) and clear the character field once the account
      holds it.
    * If unlinked, leave the character rank alone -- link offer at login
      (feature B) will attach them; a second heal after link finishes
      the move. (We do NOT auto-create accounts here -- that would invent
      passwords.)

    Returns how many accounts gained a rank from a character body.
    """
    moved = 0
    rank_order = {"": 0, "gm": 1, "head_gm": 2}
    for char in list(getattr(game, "characters", None) or []):
        if getattr(char, "is_npc", False):
            continue
        legacy = (getattr(char, "gm_rank", None) or "").strip()
        if legacy not in ("gm", "head_gm"):
            continue
        account = account_for_character(game, char)
        if account is None:
            continue
        current = (account.gm_rank or "").strip()
        if rank_order.get(legacy, 0) > rank_order.get(current, 0):
            account.gm_rank = legacy
            moved += 1
        account.gm_spirit_key = gm_spirit_key_for_account(account)
        # Character rank is no longer authoritative once the account has it.
        if account.gm_rank in ("gm", "head_gm"):
            char.gm_rank = ""
    return moved


def unregister_account(game, account):
    """Remove ``account`` from ``game.accounts`` (rollback after failed link)."""
    if game is None or account is None:
        return
    accounts = getattr(game, "accounts", None)
    if not isinstance(accounts, dict):
        return
    key = account_lookup_key(account.name)
    if key in accounts and accounts[key] is account:
        del accounts[key]
        try:
            from engine.persistence import mark_account_dirty
            mark_account_dirty(game, account)
        except Exception:
            pass


def playable_link_target(game, character):
    """Resolve the playable body for account create/link.

    When Session is on a GM spirit (``gmspirit:…`` / ``gm_spirit``), link
    the left-behind Cadence body instead. Returns ``(body, error_or_None)``.
    """
    if character is None:
        return None, "Nothing to link."
    key_low = (getattr(character, "key", None) or "").lower()
    from engine import hooks
    is_spirit = hooks.is_gm_spirit(character)
    if not is_spirit:
        return character, None
    body = hooks.resolve_gm_body(character, game)
    if body is None:
        return None, (
            "Leave GM form (`gm off`) or reconnect as your character "
            "before linking an account."
        )
    body_key = (getattr(body, "key", None) or "").lower()
    if body_key.startswith("husk:") or body_key.startswith("gmspirit:"):
        return None, "Only playable characters can be linked to an account."
    if getattr(body, "is_npc", False):
        return None, "NPCs cannot be linked to an account."
    return body, None


def list_immersion_cast(game):
    """Live immersion-cast bodies (non-NPC) for staff account secondary roster.

    Same gate as ``castpass``: ``immersion=True``, not ``is_npc``. Sorted by
    presence face / key for stable menus.
    """
    found = []
    if game is None:
        return found
    for char in list(getattr(game, "characters", None) or []):
        if getattr(char, "is_npc", False):
            continue
        if not getattr(char, "immersion", False):
            continue
        key_low = (getattr(char, "key", None) or "").lower()
        if key_low.startswith("husk:") or key_low.startswith("gmspirit:"):
            continue
        found.append(char)
    def _sort_key(ch):
        return (getattr(ch, "key", None) or "").lower()
    found.sort(key=_sort_key)
    return found


def account_is_staff(account):
    """True when ``account`` holds ordinary or head GM rank."""
    if account is None:
        return False
    return (account.gm_rank or "").strip() in ("gm", "head_gm")


def roster_character_keys(game, account):
    """All PC storage keys tied to ``account`` (explicit list + ``.account``)."""
    if account is None:
        return []
    keys = []
    seen = set()
    for key in list(getattr(account, "character_keys", None) or []):
        low = (key or "").strip().lower()
        if not low or low in seen:
            continue
        seen.add(low)
        keys.append(key)
    acct_name = (getattr(account, "name", None) or "").strip().lower()
    if acct_name and game is not None:
        for char in getattr(game, "characters", None) or []:
            if getattr(char, "is_npc", False):
                continue
            if (getattr(char, "account", None) or "").strip().lower() != acct_name:
                continue
            low = (getattr(char, "key", None) or "").strip().lower()
            if low and low not in seen:
                seen.add(low)
                keys.append(char.key)
    return keys


def account_login_choices(game, account):
    """Build account-login pick list: owned PCs, then cast if staff.

    Returns a list of ``(section, character)`` where ``section`` is
    ``"character"`` or ``"cast"``. Vaulted-only roster keys are omitted
  here -- ``account_login.build_account_menu`` adds them for the menu.
    """
    choices = []
    if account is None or game is None:
        return choices
    seen = set()
    for key in roster_character_keys(game, account):
        finder = getattr(game, "find_login_character", None)
        char = finder(key) if callable(finder) else None
        if char is None:
            char = game.find_character(key)
        if char is None or getattr(char, "is_npc", False):
            continue
        low = (getattr(char, "key", None) or "").lower()
        if low in seen:
            continue
        seen.add(low)
        choices.append(("character", char))
    if account_is_staff(account):
        for cast in list_immersion_cast(game):
            low = (getattr(cast, "key", None) or "").lower()
            if low in seen:
                continue
            # Cast linked as a PC stays in Characters only.
            seen.add(low)
            choices.append(("cast", cast))
    return choices


def transfer_head_gm(game, account):
    """Make ``account`` the sole ``head_gm``; demote every other head.

    Clears legacy ``character.gm_rank == head_gm`` on all bodies and
    demotes other accounts from ``head_gm`` to ``gm`` (or ``""`` if they
    had no other staff reason — demote to ``gm`` keeps ordinary staff).

    Returns a short human summary string.
    """
    if account is None:
        return "No such account."
    demoted_accounts = []
    for other in list(ensure_accounts_dict(game).values()):
        if other is account:
            continue
        if (other.gm_rank or "") == "head_gm":
            other.gm_rank = "gm"
            demoted_accounts.append(other.display_name or other.name)
    cleared_bodies = 0
    for char in list(getattr(game, "characters", None) or []):
        if getattr(char, "is_npc", False):
            continue
        if (getattr(char, "gm_rank", None) or "") == "head_gm":
            char.gm_rank = ""
            cleared_bodies += 1
    account.gm_rank = "head_gm"
    account.gm_spirit_key = gm_spirit_key_for_account(account)
    bits = [f"Account '{account.display_name}' is now head GM."]
    if demoted_accounts:
        bits.append(
            "Demoted former heads: " + ", ".join(demoted_accounts) + "."
        )
    if cleared_bodies:
        bits.append(
            f"Cleared legacy head_gm on {cleared_bodies} character body(ies)."
        )
    return " ".join(bits)


def resolve_staff_switch_target(game, account, name):
    """Resolve ``gm off <name>`` against account PCs then immersion cast.

    Returns ``(character, error_or_None)``. Refuses unrelated PCs / NPCs.
    """
    raw = (name or "").strip()
    if not raw:
        return None, "Switch to whom?"
    if account is None:
        return None, "No staff account on this Session."
    low = raw.lower()
    # 1) Owned playable characters (exact key or presence-face contains).
    owned = []
    for key in list(account.character_keys):
        finder = getattr(game, "find_login_character", None)
        char = finder(key) if callable(finder) else None
        if char is None:
            char = game.find_character(key) if game else None
        if char is None or getattr(char, "is_npc", False):
            continue
        owned.append(char)
    for char in owned:
        key = (getattr(char, "key", None) or "")
        if key.lower() == low:
            return char, None
    # Loose match on owned keys / given names.
    matches = []
    for char in owned:
        key = (getattr(char, "key", None) or "").lower()
        given = (getattr(char, "given_name", None) or "").lower()
        if low in key or (given and low in given):
            matches.append(char)
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        names = ", ".join(getattr(c, "key", "?") for c in matches)
        return None, f"Ambiguous -- matches: {names}."
    # 2) Immersion cast (staff accounts only).
    if not account_is_staff(account):
        return None, f"No account character named '{raw}'."
    cast_matches = []
    for cast in list_immersion_cast(game):
        key = (getattr(cast, "key", None) or "")
        if key.lower() == low:
            return cast, None
        given = (getattr(cast, "given_name", None) or "").lower()
        if low in key.lower() or (given and low in given):
            cast_matches.append(cast)
    if len(cast_matches) == 1:
        return cast_matches[0], None
    if len(cast_matches) > 1:
        names = ", ".join(getattr(c, "key", "?") for c in cast_matches)
        return None, f"Ambiguous cast -- matches: {names}."
    return None, (
        f"'{raw}' is not on your account roster or immersion cast."
    )


def is_guest_character(character):
    """True for legacy ephemeral guest bodies (no new logins mint these)."""
    return bool(getattr(character, "is_guest", False))

