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
        self.features_suggested = 0
        # Prefs: which face OOC uses; whether GM form appends (Account).
        self.ooc_identity = OOC_IDENTITY_CHARACTER
        self.gm_see_accounts = False

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


def account_for_character(game, character):
    """Resolve the Account linked to ``character``, or None."""
    if character is None:
        return None
    name = (getattr(character, "account", None) or "").strip()
    if not name:
        return None
    return find_account(game, name)


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


def reconcile_accounts(game):
    """Boot heal: keep account.character_keys ↔ character.account honest.

    Idempotent. Returns a small stats dict for logs / smoke.
    """
    accounts = ensure_accounts_dict(game)
    linked = 0
    repaired_back = 0
    dropped_stale = 0
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
                # Character folded/vaulted/gone -- drop from roster.
                dropped_stale += 1
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
    }


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


def playable_link_target(game, character):
    """Resolve the playable body for account create/link.

    When Session is on a GM spirit (``gmspirit:…`` / ``gm_spirit``), link
    the left-behind Cadence body instead. Returns ``(body, error_or_None)``.
    """
    if character is None:
        return None, "Nothing to link."
    key_low = (getattr(character, "key", None) or "").lower()
    is_spirit = (
        bool(getattr(character, "gm_spirit", False))
        or key_low.startswith("gmspirit:")
    )
    if not is_spirit:
        return character, None
    # Prefer the live body pointer stamped by ``gm on``.
    body = getattr(character, "gm_mode_body", None)
    if body is None:
        body_key = getattr(character, "gm_body_key", None)
        if body_key and game is not None:
            finder = getattr(game, "find_character", None)
            body = finder(body_key) if callable(finder) else None
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


def account_login_choices(game, account):
    """Build account-login pick list: owned PCs, then cast if staff.

    Returns a list of ``(section, character)`` where ``section`` is
    ``"character"`` or ``"cast"``.
    """
    choices = []
    if account is None or game is None:
        return choices
    seen = set()
    for key in list(account.character_keys):
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
