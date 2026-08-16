"""
account_login.py -- async login prompts for engine Accounts.

Account-first flow: register or log into an account, pick/create/link a
character, then return the body for ``connection.py`` to attach.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from engine import accounts as accounts_mod
from engine import auth
from engine import hooks
from engine import account_chargen_draft as draft_mod
from engine.command_support import _presence_face


# Legacy keyword at the old name prompt (deprecated; connection no longer uses).
ACCOUNT_LOGIN_KEYWORD = "account"
ACCOUNT_NEW_KEYWORD = "new"

# Menu sentinel objects (identity compared with ``is``).
CREATE_NEW_MARKER = object()
LINK_EXISTING_MARKER = object()


async def _read_player_line(session, *, password=False):
    """Read one non-empty line from the client. None on disconnect."""
    while True:
        if password:
            line = await session.read_password_line()
        else:
            line = await session.read_line()
        if line is None:
            return None
        line = (line or "").strip()
        if line:
            return line


def _parse_menu_choice(pick, tagged):
    """Map menu input to a 1-based index, or None when invalid."""
    pick = (pick or "").strip()
    if not pick:
        return None
    match = re.match(r"^(\d+)", pick)
    if match:
        return int(match.group(1))
    low = pick.lower()
    if low in ("create", "new", "c"):
        for i, (_section, entry) in enumerate(tagged, start=1):
            if entry is CREATE_NEW_MARKER:
                return i
    if low == "link" or low.startswith("link "):
        for i, (_section, entry) in enumerate(tagged, start=1):
            if entry is LINK_EXISTING_MARKER:
                return i
    return None


def _send_character_menu(session, game, account, tagged):
    """Paint the roster menu once (caller handles re-prompts)."""
    if len(tagged) == 2:
        session.send("This account has no characters yet.")
    if len(tagged) == 1:
        entry = tagged[0][1]
        session.send(
            f"Logging in as {_menu_face(game, entry)} "
            "(only character on this account)."
        )
        return
    session.send("Choose:")
    last_section = None
    for i, (section, entry) in enumerate(tagged, start=1):
        if section != last_section:
            if section == "character":
                session.send("  -- Characters --")
            elif section == "cast":
                session.send("  -- Cast --")
            else:
                session.send("  -- Actions --")
            last_section = section
        face = _menu_face(game, entry)
        online = ""
        if (
            section in ("character", "cast")
            and entry not in (CREATE_NEW_MARKER, LINK_EXISTING_MARKER)
            and not getattr(entry, "is_vaulted_menu_entry", False)
            and getattr(entry, "session", None)
        ):
            online = " (online)"
        session.send(f"  {i}. {face}{online}")


async def _persist_account_change(game, *, reason):
    """Persist account-table changes during login without a full world snapshot.

    Account register/link only touch ``game.accounts`` (and in-memory
    character back-pointers). ``save_accounts`` is enough to survive a crash
    before the menu continues. Do **not** queue a full world save here --
    even a background ``save_async`` blocks the single asyncio thread during
    the SQLite bulk rewrite and freezes the next login prompt.
    """
    from engine import persistence

    try:
        persistence.save_accounts(game.db, game)
    except Exception as exc:
        print(
            f"[account_login] save_accounts failed ({reason}): {exc!r}",
            flush=True,
        )


def is_account_login_keyword(raw):
    """True when the line is the legacy account route keyword."""
    return (raw or "").strip().lower() == ACCOUNT_LOGIN_KEYWORD


def is_account_new_keyword(raw):
    """True when the player wants to register a new account."""
    return (raw or "").strip().lower() == ACCOUNT_NEW_KEYWORD


@dataclass
class AccountLoginResult:
    """Outcome of ``run_account_login_flow`` for ``connection.py``."""

    character: object
    is_new: bool = False
    takeover: bool = False


def build_account_menu(game, account):
    """Owned PCs (live + vaulted) + cast + create/link actions."""
    tagged = list(accounts_mod.account_login_choices(game, account))
    live_low = {
        (getattr(ch, "key", None) or "").lower()
        for _sec, ch in tagged
        if ch is not None
    }
    tagged.extend(hooks.account_menu_vault_rows(game, account, live_low))
    tagged.append(("action", CREATE_NEW_MARKER))
    tagged.append(("action", LINK_EXISTING_MARKER))
    return tagged


def _menu_face(game, entry):
    if entry is CREATE_NEW_MARKER:
        return "Create a new character"
    if entry is LINK_EXISTING_MARKER:
        return "Link an existing character"
    if getattr(entry, "is_vaulted_menu_entry", False):
        tag = getattr(entry, "identity_tag", None) or ""
        if draft_mod.is_chargen_draft_vault(game, getattr(entry, "key", "")):
            return f"{entry.face} (resume chargen)"
        if tag:
            return f"{entry.face} ({tag}) (folded)"
        return f"{entry.face} (folded)"
    face = hooks.account_roster_label_for(
        game, getattr(entry, "key", None), entry,
    )
    if draft_mod.is_chargen_draft_character(game, entry):
        return f"{face} (resume chargen)"
    return face


def _resolve_menu_pick(game, entry):
    """Turn a menu row into a live Character (restore vault rows)."""
    if entry in (CREATE_NEW_MARKER, LINK_EXISTING_MARKER):
        return entry
    if getattr(entry, "is_vaulted_menu_entry", False):
        return hooks.restore_vault_menu_entry(game, entry)
    return entry


async def _prompt_account_name(session, *, allow_back=False):
    """Read account name or ``new``. Returns (name, is_new) or sentinels."""
    while True:
        session.send("By what account name are you known?")
        session.send(
            "(New here? Type 'new' to register an account.)"
        )
        raw = await session.read_line()
        if raw is None:
            return None, False
        from engine import mssp
        if mssp.is_text_probe(raw):
            mssp.reply_text_probe(session)
            session.close()
            return None, False
        text = (raw or "").strip()
        if not text:
            continue
        if is_account_new_keyword(text):
            return None, True
        cleaned, err = accounts_mod.normalize_account_name(text)
        if err:
            session.send(err + " Try again:")
            continue
        return cleaned, False


async def _prompt_create_account(session, game):
    """Register a new account. Returns Account or None on disconnect."""
    while True:
        limit_err = draft_mod.account_create_rate_limit_error(session)
        if limit_err:
            session.send(limit_err)
            return None
        session.send("Choose an account name (2-16 letters):")
        while True:
            raw = await _read_player_line(session)
            if raw is None:
                return None
            cleaned, err = accounts_mod.normalize_account_name(raw)
            if err:
                session.send(err + " Try again:")
                continue
            if accounts_mod.find_account(game, cleaned) is not None:
                session.send("That account name is taken. Try again:")
                continue
            break
        min_len = auth.MIN_PASSWORD_LEN
        session.send(
            f"Choose an account password (at least {min_len} characters):"
        )
        while True:
            password = await _read_player_line(session, password=True)
            if password is None:
                return None
            policy_err = auth.password_policy_error(password, for_gm=False)
            if policy_err:
                session.send(f"{policy_err} Try again:")
                session.send(
                    f"Choose an account password (at least {min_len} characters):"
                )
                continue
            account, err = accounts_mod.create_account(
                game, cleaned, password,
            )
            if err:
                session.send(f"{err} Try again:")
                session.send(
                    f"Choose an account password (at least {min_len} characters):"
                )
                continue
            draft_mod.note_account_created(session)
            break
        await _persist_account_change(game, reason="account_register")
        session.send(
            f"Account '{account.display_name}' created. "
            "You can add characters from the menu next."
        )
        return account


async def _prompt_account_password(session, account):
    """Read and verify account password. Returns True / False / None."""
    session.send("Account password:")
    password = await session.read_password_line()
    if password is None:
        return None
    password = password or ""
    return accounts_mod.verify_account_password(account, password)


async def _authenticate_account_password(session, account):
    """Prompt for account password with one retry, then send player back.

    Returns ``True`` on success, ``False`` when both tries failed (caller
    should return to the account-name prompt), or ``None`` on disconnect.
    """
    for attempt in range(2):
        ok = await _prompt_account_password(session, account)
        if ok is None:
            return None
        if ok:
            return True
        if attempt == 0:
            session.send("Incorrect password. Try again:")
        else:
            session.send(
                "Incorrect password. Returning to account name -- "
                "type your account again or 'new' to register."
            )
    return False


async def _verify_character_password(session, body):
    """Prove character password for link-existing (one retry).

    Returns the password string, ``False`` after two failures, or ``None``
    on disconnect.
    """
    stored = getattr(body, "password_hash", None) or ""
    for attempt in range(2):
        password = await _prompt_character_password(session, create=False)
        if password is None:
            return None
        if auth.verify_password(password, stored):
            return password
        if attempt == 0:
            session.send("Incorrect character password. Try again:")
        else:
            session.send(
                "Incorrect character password. Returning to the menu."
            )
    return False


async def _prompt_character_menu(session, game, account):
    """Pick create/link/character. Returns resolved pick or None."""
    tagged = build_account_menu(game, account)
    if len(tagged) == 1:
        return _resolve_menu_pick(game, tagged[0][1])

    _send_character_menu(session, game, account, tagged)
    while True:
        session.send(
            "Enter a number (e.g. 1), or type create / link:"
        )
        pick = await _read_player_line(session)
        if pick is None:
            return None
        idx = _parse_menu_choice(pick, tagged)
        if idx is None:
            session.send(
                "Pick the menu number (e.g. 1), or type create or link."
            )
            continue
        if idx < 1 or idx > len(tagged):
            session.send("Out of range.")
            continue
        return _resolve_menu_pick(game, tagged[idx - 1][1])


async def _prompt_given_name(session, game, *, prompt):
    """Letters-only given name for create/link."""
    from engine.connection import normalize_login_name

    while True:
        session.send(prompt)
        raw = await session.read_line()
        if raw is None:
            return None
        name, err, stripped = normalize_login_name(raw)
        if err:
            session.send(err + " Try again:")
            continue
        if hooks.is_reserved_login_name(name):
            session.send(
                "That name is reserved for the immersion cast. Try again:"
            )
            continue
        return name


async def _prompt_surname_for_create(session, game, given_name):
    """Surname for a new body -- optional when the given name is unique."""
    from engine import char_identity as identity_mod

    if identity_mod.surname_required_for_create(game, given_name):
        session.send(
            f"Another character already uses the name {given_name}. "
            "What is your surname? (2-16 letters, required)"
        )
    else:
        session.send(
            "What is your surname? "
            "(2-16 letters, or press Enter to skip)"
        )
    while True:
        raw = await session.read_line()
        if raw is None:
            return None
        raw = (raw or "").strip()
        if identity_mod.surname_required_for_create(game, given_name):
            cleaned, err = identity_mod.normalize_surname(raw)
        else:
            cleaned, err = identity_mod.normalize_surname_optional(raw)
        if err:
            session.send(err + " Try again:")
            continue
        err = identity_mod.validate_new_character_identity(
            game, given_name, cleaned,
        )
        if err:
            session.send(err + " Try again:")
            continue
        return cleaned


async def _prompt_character_password(session, *, create=False):
    """Read a character password (set or verify)."""
    min_len = auth.MIN_PASSWORD_LEN
    if create:
        session.send(
            f"Choose a password for this character "
            f"(at least {min_len} characters):"
        )
    else:
        session.send("Character password:")
    while True:
        password = await session.read_password_line()
        if password is None:
            return None
        password = password or ""
        if create:
            policy_err = auth.password_policy_error(password, for_gm=False)
            if policy_err is None:
                return password
            session.send(f"{policy_err} Try again:")
            continue
        return password


def _find_unlinked_body(game, given_name, surname):
    """Resolve a live or vaulted unlinked body for the link-existing flow."""
    from engine import char_identity as identity_mod

    body = identity_mod.find_player_by_given_surname(
        game, given_name, surname,
    )
    if body is not None and not (getattr(body, "account", None) or "").strip():
        return body
    restored = hooks.try_restore_folded_login_identity(
        game, given_name, surname,
    )
    if restored is not None and not (
        getattr(restored, "account", None) or ""
    ).strip():
        return restored
    return None


async def _finish_new_character_after_chargen(session, game, account, char):
    """Place a body that just finished create-flow chargen."""
    from engine import char_identity as identity_mod
    from engine import gm_notify

    start_key = getattr(char, "chargen_start_room_key", None)
    start_room = None
    if start_key:
        from engine.room_vnum import lookup_room

        start_room = lookup_room(game, start_key)
        if start_room is None and start_key in game.rooms:
            start_room = game.rooms[start_key]
    if start_room is None:
        start_room = game.start_room
    char.chargen_start_room_key = None
    char.chargen_draft = False
    draft_mod.finish_chargen_draft(account, game)
    char.move_to(start_room)
    session._promote_to_sessions()
    legal = identity_mod.legal_public_name(char, force_surname=True)
    start_room.broadcast(f"{legal} materializes.", exclude=char)
    gm_notify.ping_gms(
        game,
        f"{legal} finished chargen and entered the world{{from}}.",
        exclude=char,
        peer_session=session,
    )
    session.send(
        f"\r\nWelcome, {legal}! Type 'help newbie' to get started "
        f"(or 'help' for the topic list)."
    )
    hooks.after_new_character(char, game)
    hooks.after_session_attach(char, game)
    return AccountLoginResult(character=char, is_new=True)


async def _flow_resume_chargen(session, game, account, char):
    """Resume create-flow chargen for a draft body."""
    from engine import char_identity as identity_mod
    from engine import gm_notify

    char.session = session
    session.character = char
    session._set_creating()
    legal = identity_mod.legal_public_name(char, force_surname=True)
    session.send(f"Resuming character creation for {legal}...")
    gm_notify.ping_gms(
        game,
        f"{legal} is resuming chargen{{from}}.",
        exclude=char,
        peer_session=session,
    )
    if not await hooks.run_chargen(session, char):
        draft_mod.vault_chargen_draft(char, game, account)
        return None
    return await _finish_new_character_after_chargen(
        session, game, account, char,
    )


async def _flow_create_character(session, game, account):
    """Create + chargen + place a new body on ``account``."""
    from engine import char_identity as identity_mod
    from engine.world import Character

    given = await _prompt_given_name(
        session, game, prompt="What is your character's first name?",
    )
    if given is None:
        return None
    surname = await _prompt_surname_for_create(session, game, given)
    if surname is None:
        return None
    password = await _prompt_character_password(session, create=True)
    if password is None:
        return None

    storage_key = identity_mod.allocate_storage_key(game, given, surname)
    char = Character(storage_key)
    identity_mod.stamp_new_identity(char, game, given, surname)
    char.password_hash = auth.hash_password(password)
    link_err = accounts_mod.link_character(game, account, char)
    if link_err:
        session.send(link_err)
        return None
    char.session = session
    session.character = char
    session._set_creating()
    draft_mod.stamp_chargen_draft(account, char, game)

    from engine import gm_notify

    legal = identity_mod.legal_public_name(char, force_surname=True)
    gm_notify.ping_gms(
        game,
        f"{legal} has connected{{from}} and is making a character...",
        exclude=char,
        peer_session=session,
    )
    if not await hooks.run_chargen(session, char):
        draft_mod.vault_chargen_draft(char, game, account)
        return None

    return await _finish_new_character_after_chargen(
        session, game, account, char,
    )


async def _flow_link_existing(session, game, account):
    """Prove ownership of an unlinked body and attach it to ``account``."""
    from engine import char_identity as identity_mod

    given = await _prompt_given_name(
        session, game,
        prompt="First name of the character to link:",
    )
    if given is None:
        return None
    if identity_mod.surname_required_for_create(game, given):
        session.send(
            "What is that character's surname? (required to find them)"
        )
        raw = await session.read_line()
        if raw is None:
            return None
        surname, err = identity_mod.normalize_surname(raw)
        if err:
            session.send(err)
            return None
    else:
        session.send(
            "Surname? (press Enter if they have none on file)"
        )
        raw = await session.read_line()
        if raw is None:
            return None
        surname, err = identity_mod.normalize_surname_optional(raw)
        if err:
            session.send(err)
            return None

    body = _find_unlinked_body(game, given, surname)
    if body is None:
        session.send(
            "No unlinked character matches that name, or they are already "
            "on an account."
        )
        return False
    if getattr(body, "account", None):
        session.send("That character is already linked to an account.")
        return False
    password = await _verify_character_password(session, body)
    if password is None:
        return None
    if password is False:
        return False
    link_err = accounts_mod.link_character(game, account, body)
    if link_err:
        session.send(link_err)
        return False
    await _persist_account_change(game, reason="account_link")
    session.send(
        f"Linked {_presence_face(body)} to account "
        f"'{account.display_name}'."
    )
    return body


async def run_account_login_flow(session, *, known_account=None):
    """Account-first login. Returns ``AccountLoginResult`` or None."""
    game = session.game
    use_known = known_account

    while True:
        if use_known:
            cleaned, err = accounts_mod.normalize_account_name(use_known)
            if err:
                session.send(err)
                return None
            account = accounts_mod.find_account(game, cleaned)
            if account is None:
                session.send("No such account.")
                return None
            session.send(
                f"Account {account.display_name} -- enter password:"
            )
            is_new_account = False
            use_known = None
        else:
            acct_name, is_new_account = await _prompt_account_name(session)
            if acct_name is None and not is_new_account:
                return None
            if is_new_account:
                account = await _prompt_create_account(session, game)
                if account is None:
                    return None
            else:
                account = accounts_mod.find_account(game, acct_name)
                if account is None:
                    session.send(
                        "No such account. Type 'new' to register, "
                        "or check the spelling."
                    )
                    continue

        if not is_new_account:
            ok = await _authenticate_account_password(session, account)
            if ok is None:
                return None
            if not ok:
                continue

        if accounts_mod.account_is_staff(account):
            session.staff_account = account.name

        while True:
            picked = await _prompt_character_menu(session, game, account)
            if picked is None:
                return None
            if picked is False:
                continue
            if picked is CREATE_NEW_MARKER:
                result = await _flow_create_character(session, game, account)
                if result is None:
                    return None
                return result
            if picked is LINK_EXISTING_MARKER:
                linked = await _flow_link_existing(session, game, account)
                if linked is None:
                    return None
                if linked is False:
                    continue
                picked = linked
            if picked is None:
                session.send("Could not restore that character.")
                continue

            if draft_mod.is_chargen_draft_character(game, picked):
                result = await _flow_resume_chargen(
                    session, game, account, picked,
                )
                if result is None:
                    return None
                return result

            live_holder = picked if getattr(picked, "session", None) else None
            takeover = False
            if live_holder is None:
                sk = getattr(picked, "gm_spirit_key", None)
                if not sk and getattr(picked, "gm_staff_form", False):
                    from engine.command_support import (
                        strip_ephemeral_storage_prefix,
                    )
                    sk = (
                        "gmspirit:"
                        + strip_ephemeral_storage_prefix(picked.key)
                    )
                if sk:
                    spirit = game.find_character(sk)
                    if (
                        spirit is not None
                        and getattr(spirit, "session", None) is not None
                    ):
                        live_holder = spirit
            if live_holder is not None:
                session._take_over_session(live_holder)
                takeover = True
            return AccountLoginResult(
                character=picked, is_new=False, takeover=takeover,
            )


# --- Legacy offer (deprecated; kept for migration tooling only) ------------

def character_needs_account_link_offer(character):
    """Deprecated -- account-first create auto-links at birth."""
    return False


async def offer_account_link(session, character):
    """Deprecated no-op."""
    return True


async def login_via_account(session, known_account=None):
    """Deprecated wrapper -- use ``run_account_login_flow``."""
    result = await run_account_login_flow(session, known_account=known_account)
    if result is None:
        return None
    return result.character
