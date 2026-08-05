"""
account_login.py -- async login prompts for engine Accounts.

Kept separate from ``engine/accounts.py`` (pure domain) and from the
imperative ``Session._run_inner`` loop so the account route / link-offer
read_line flows stay readable. ``connection.py`` calls these helpers.
"""

from __future__ import annotations

from engine import accounts as accounts_mod
from engine import auth
from engine.command_support import _presence_face


# Keyword at the name prompt that starts the account route (feature C).
ACCOUNT_LOGIN_KEYWORD = "account"


def is_account_login_keyword(raw):
    """True when the name-prompt line is the account route keyword."""
    return (raw or "").strip().lower() == ACCOUNT_LOGIN_KEYWORD


async def login_via_account(session, known_account=None):
    """Account name → password → character/cast pick. Return Character or None.

    ``None`` means disconnect mid-flow or the player backed out to the
    character-name prompt (caller re-prompts ``By what name…``).
    On success the Character is NOT yet Session-attached -- caller reuses
    the same Echo-attach path as direct character login.

    Staff accounts (``gm`` / ``head_gm``) also list immersion cast as a
    secondary roster after owned characters.

    ``known_account`` (soft logout): skip the account-name prompt and use
    this account name; password is still required before the pick list.
    """
    game = session.game
    if known_account:
        cleaned, err = accounts_mod.normalize_account_name(known_account)
        if err:
            session.send(err)
            session.send("By what name are you known?")
            return False
        account = accounts_mod.find_account(game, cleaned)
        if account is None:
            session.send("No such account. By what name are you known?")
            return False
        session.send(
            f"Account {account.name} -- enter password to pick a character:"
        )
    else:
        session.send(
            "Account name (or blank to return to character login):"
        )
        raw = await session.read_line()
        if raw is None:
            return None
        raw = (raw or "").strip()
        if not raw:
            session.send("By what name are you known?")
            return False  # sentinel: back out (not disconnect)
        cleaned, err = accounts_mod.normalize_account_name(raw)
        if err:
            session.send(err)
            session.send("By what name are you known?")
            return False
        account = accounts_mod.find_account(game, cleaned)
        if account is None:
            session.send(
                "No such account. New here? Back out and type a character "
                "name instead -- you'll be offered an account once that "
                "character exists. Otherwise, check the spelling and try "
                "'account' again."
            )
            session.send("By what name are you known?")
            return False
        session.send("Account password:")
    password = await session.read_line()
    if password is None:
        return None
    from engine.connection import strip_client_session_tags
    password = strip_client_session_tags(password or "")
    if not accounts_mod.verify_account_password(account, password):
        session.send("Incorrect password. By what name are you known?")
        return False

    # Owned PCs first; immersion cast second when the account is staff.
    tagged = accounts_mod.account_login_choices(game, account)
    if not tagged:
        session.send(
            "That account has no characters linked yet. "
            "Log into a character directly, then create/link an account. "
            "By what name are you known?"
        )
        return False

    # Stamp staff account on the Session so cast picks can ``gm on`` later.
    if accounts_mod.account_is_staff(account):
        session.staff_account = account.name

    if len(tagged) == 1:
        section, chosen = tagged[0]
        label = "cast" if section == "cast" else "character"
        session.send(
            f"Logging in as {_presence_face(chosen)} "
            f"(only {label} on this account menu)."
        )
        return chosen

    session.send("Choose a character:")
    # Two visual sections for staff; flat numbered list either way.
    last_section = None
    for i, (section, char) in enumerate(tagged, start=1):
        if section != last_section:
            if section == "character":
                session.send("  -- Characters --")
            else:
                session.send("  -- Cast --")
            last_section = section
        face = _presence_face(char)
        online = " (online)" if getattr(char, "session", None) else ""
        session.send(f"  {i}. {face}{online}")
    session.send("Enter number (or blank to cancel):")
    pick = await session.read_line()
    if pick is None:
        return None
    pick = (pick or "").strip()
    if not pick:
        session.send("By what name are you known?")
        return False
    try:
        idx = int(pick)
    except ValueError:
        session.send("Not a number. By what name are you known?")
        return False
    if idx < 1 or idx > len(tagged):
        session.send("Out of range. By what name are you known?")
        return False
    return tagged[idx - 1][1]


def character_needs_account_link_offer(character):
    """True when login should pause for create/link/skip before entering play.

    Immersion cast bodies are excluded -- they are for world immersion and
    GM control, not player account rosters.
    """
    if character is None:
        return False
    if getattr(character, "is_npc", False):
        return False
    key_low = (getattr(character, "key", None) or "").lower()
    if key_low.startswith("husk:") or key_low.startswith("gmspirit:"):
        return False
    if getattr(character, "immersion", False):
        return False
    if (getattr(character, "account", None) or "").strip():
        return False
    return True


async def offer_account_link(session, character):
    """Offer create / link / skip when unlinked (feature B).

    Call **before** Session attach / welcome / room broadcasts so the player
    finishes account setup while still at the login prompt.

    No-op for NPCs, husks, gm spirits, immersion cast, or linked characters.
    Returns False on disconnect mid-offer (caller should abort play);
    True otherwise (including skip).
    """
    if not character_needs_account_link_offer(character):
        return True

    game = session.game
    face = _presence_face(character)
    session.send(
        f"\r\n{face} is not linked to an account yet.\n"
        "  1) Create a new account and link this character\n"
        "  2) Link this character to an existing account\n"
        "  3) Skip for now (you can type 'account' later)\n"
        "Choose 1, 2, or 3:"
    )
    while True:
        line = await session.read_line()
        if line is None:
            return False
        choice = (line or "").strip().lower()
        if choice in ("3", "skip", "s", ""):
            session.send(
                "Skipped. Type 'account create' or 'account link' in-game "
                "when you are ready."
            )
            return True
        if choice in ("1", "create", "c", "new"):
            ok = await _prompt_create_and_link(session, character)
            if ok is None:
                return False
            return True
        if choice in ("2", "link", "l", "existing"):
            ok = await _prompt_link_existing(session, character)
            if ok is None:
                return False
            return True
        session.send("Please choose 1, 2, or 3:")


async def _prompt_create_and_link(session, character):
    """Create a new account, link ``character``, save. None = disconnect."""
    game = session.game
    session.send("New account name (2-16 letters):")
    raw = await session.read_line()
    if raw is None:
        return None
    cleaned, err = accounts_mod.normalize_account_name(raw)
    if err:
        session.send(err + " Skipping account for now.")
        return False
    if accounts_mod.find_account(game, cleaned) is not None:
        session.send(
            "That account name is already taken. Skipping for now."
        )
        return False
    min_len = auth.MIN_PASSWORD_LEN
    session.send(f"Choose an account password (at least {min_len} characters):")
    password = await session.read_line()
    if password is None:
        return None
    from engine.connection import strip_client_session_tags
    password = strip_client_session_tags(password or "")
    account, err = accounts_mod.create_account(game, cleaned, password)
    if err:
        session.send(f"{err} Skipping for now.")
        return False
    link_err = accounts_mod.link_character(game, account, character)
    if link_err:
        accounts_mod.unregister_account(game, account)
        session.send(f"{link_err} Skipping for now.")
        return False
    # If the character carried a legacy gm_rank, migrate it now.
    accounts_mod.migrate_legacy_gm_ranks(game)
    try:
        game.save()
    except Exception as exc:
        print(f"[account_login] save after create failed: {exc!r}", flush=True)
    session.send(
        f"Account '{account.display_name}' created and linked to "
        f"{_presence_face(character)}. "
        "Next time you can type 'account' at the name prompt."
    )
    from engine import gm_notify
    who = gm_notify.public_who(character, game)
    gm_notify.ping_gms(
        game,
        f"{who} created account {account.display_name} and linked "
        f"{_presence_face(character)}{{from}}.",
        exclude=character,
        peer_session=session,
    )
    return True


async def _prompt_link_existing(session, character):
    """Link ``character`` to an existing account. None = disconnect."""
    game = session.game
    session.send("Existing account name:")
    raw = await session.read_line()
    if raw is None:
        return None
    cleaned, err = accounts_mod.normalize_account_name(raw)
    if err:
        session.send(err + " Skipping for now.")
        return False
    account = accounts_mod.find_account(game, cleaned)
    if account is None:
        session.send("No such account. Skipping for now.")
        return False
    session.send("Account password:")
    password = await session.read_line()
    if password is None:
        return None
    from engine.connection import strip_client_session_tags
    password = strip_client_session_tags(password or "")
    if not accounts_mod.verify_account_password(account, password):
        session.send("Incorrect password. Skipping for now.")
        return False
    link_err = accounts_mod.link_character(game, account, character)
    if link_err:
        session.send(f"{link_err} Skipping for now.")
        return False
    accounts_mod.migrate_legacy_gm_ranks(game)
    try:
        game.save()
    except Exception as exc:
        print(f"[account_login] save after link failed: {exc!r}", flush=True)
    session.send(
        f"Linked {_presence_face(character)} to account "
        f"'{account.display_name}'."
    )
    from engine import gm_notify
    who = gm_notify.public_who(character, game)
    gm_notify.ping_gms(
        game,
        f"{who} linked to account {account.display_name}{{from}}.",
        exclude=character,
        peer_session=session,
    )
    return True
