"""
char_identity.py -- Given name, surname, CNUM, and ordinal targeting.

Player identity is ``given_name`` + ``surname`` (storage ``Character.key``
stays a unique immutable PK). CNUM is staff-only. Ordinal query peel
(``2.carl``, ``other carl``, ``second carl``) lives here so room and
world resolvers share one parser.

Engine-pure: no ``supers`` imports.
"""

from __future__ import annotations

import re

from engine.char_cnum import allocate_cnum, collect_taken_cnums

# Same bounds as login first names (engine/connection.py).
SURNAME_MIN = 2
SURNAME_MAX = 16

# Public who / introduce order when surname is visible (``bond`` command).
PUBLIC_NAME_ORDER_GIVEN_FIRST = "given_first"
PUBLIC_NAME_ORDER_SURNAME_FIRST = "surname_first"


def _strip_prefix(name):
    """Lazy import -- avoids circular import with command_support."""
    from engine.command_support import strip_ephemeral_storage_prefix
    return strip_ephemeral_storage_prefix(name)

# ``2.carl`` / ``3.name`` ordinal prefix.
_DOT_ORDINAL_RE = re.compile(r"^(\d+)\.(.+)$", re.IGNORECASE)

# Word ordinals → 1-based index. ``other`` means the second match.
_WORD_ORDINALS = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "other": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
    "sixth": 6,
    "6th": 6,
    "seventh": 7,
    "7th": 7,
    "eighth": 8,
    "8th": 8,
    "ninth": 9,
    "9th": 9,
    "tenth": 10,
    "10th": 10,
}


def normalize_surname(raw: str):
    """Validate a surname the same way login names are validated.

    Returns ``(cleaned, error_or_None)``. Empty input → error (new chars
    must pick a surname). Letters only, 2–16, first letter uppercased.
    """
    text = (raw or "").strip()
    if not text:
        return None, "Surnames are 2-16 letters (no digits)."
    if not text.isalpha():
        return None, "Surnames are 2-16 letters (no digits)."
    if len(text) < SURNAME_MIN or len(text) > SURNAME_MAX:
        return None, "Surnames are 2-16 letters (no digits)."
    cleaned = text[0].upper() + text[1:]
    return cleaned, None


def character_given_name(char) -> str:
    """Resolved given name for a Character (legacy falls back to key face)."""
    if char is None:
        return ""
    given = getattr(char, "given_name", None)
    if isinstance(given, str) and given.strip():
        return given.strip()
    return _strip_prefix(getattr(char, "key", None) or "")


def character_surname(char) -> str:
    """Resolved surname string (may be empty for legacy bodies)."""
    if char is None:
        return ""
    sur = getattr(char, "surname", None)
    if isinstance(sur, str):
        return sur.strip()
    return ""


def public_name_order(char) -> str:
    """Resolved public-name order pref (``bond given|surname``)."""
    raw = getattr(char, "public_name_order", None) or PUBLIC_NAME_ORDER_GIVEN_FIRST
    if raw == PUBLIC_NAME_ORDER_SURNAME_FIRST:
        return PUBLIC_NAME_ORDER_SURNAME_FIRST
    return PUBLIC_NAME_ORDER_GIVEN_FIRST


def legal_public_name(char, *, force_surname=False) -> str:
    """Given + optional surname for who / greet when no assumed face.

    Respects ``surname_visible`` unless ``force_surname`` (GM tooling).
    When visible and ``public_name_order`` is surname-first, formats
    ``Surname, Given`` (Bond / Crowley desk style).
    """
    given = character_given_name(char) or "?"
    sur = character_surname(char)
    if not sur:
        return given
    visible = bool(getattr(char, "surname_visible", True))
    if not (force_surname or visible):
        return given
    if public_name_order(char) == PUBLIC_NAME_ORDER_SURNAME_FIRST:
        return f"{sur}, {given}"
    return f"{given} {sur}"


def identity_match_needles(char) -> list[str]:
    """Lowercase strings that should match this character in targeting."""
    needles = []
    key = (getattr(char, "key", None) or "").lower()
    if key:
        needles.append(key)
        # Peel ephemeral prefix for bare-name match.
        peeled = _strip_prefix(key).lower()
        if peeled and peeled != "?" and peeled not in needles:
            needles.append(peeled)
    given = character_given_name(char).lower()
    if given and given not in needles:
        needles.append(given)
    sur = character_surname(char).lower()
    if given and sur:
        full = f"{given} {sur}"
        if full not in needles:
            needles.append(full)
        rev = f"{sur}, {given}"
        if rev.lower() not in needles:
            needles.append(rev.lower())
        mash = f"{given}{sur}"
        if mash not in needles:
            needles.append(mash)
    face = (
        getattr(char, "assumed_face", None)
        or getattr(char, "husk_display_name", None)
        or ""
    )
    if face:
        face_l = str(face).strip().lower()
        if face_l and face_l not in needles:
            needles.append(face_l)
    return needles


def parse_target_ordinal(query: str):
    """Peel ``2.carl`` / ``other carl`` / ``second carl`` from a query.

    Returns ``(ordinal_or_None, remaining_query)``. Ordinal is 1-based.
    Remaining query is stripped; may still be empty.
    """
    raw = (query or "").strip()
    if not raw:
        return None, ""
    # Dot form: 2.carl / 2.Carl Winchester
    m = _DOT_ORDINAL_RE.match(raw)
    if m:
        try:
            n = int(m.group(1))
        except ValueError:
            n = 0
        rest = (m.group(2) or "").strip()
        if n >= 1 and rest:
            return n, rest
    # Word form: other carl / second carl
    parts = raw.split(None, 1)
    if len(parts) == 2:
        word = parts[0].lower()
        if word in _WORD_ORDINALS:
            return _WORD_ORDINALS[word], parts[1].strip()
    return None, raw


def pick_ordinal(matches, ordinal):
    """Return the ordinal-th match (1-based), or None if out of range."""
    if not matches:
        return None
    if ordinal is None:
        # Unambiguous single hit only -- caller handles multi without ordinal.
        if len(matches) == 1:
            return matches[0]
        return None
    idx = int(ordinal) - 1
    if idx < 0 or idx >= len(matches):
        return None
    return matches[idx]


def is_login_player_body(char) -> bool:
    """True for corporeal player / Echo bodies that can own a login slot.

    Skips NPCs, vessel husks, and ephemeral ``husk:`` / ``gmspirit:`` keys.
    """
    if char is None:
        return False
    if getattr(char, "is_npc", False):
        return False
    if getattr(char, "is_vessel_husk", False):
        return False
    key = (getattr(char, "key", None) or "").lower()
    if key.startswith("husk:") or key.startswith("gmspirit:"):
        return False
    return True


def find_players_by_given_name(game, given_name: str) -> list:
    """All login-capable bodies whose given name matches (case-insensitive)."""
    needle = (given_name or "").strip().lower()
    if not needle:
        return []
    out = []
    for char in list(getattr(game, "characters", None) or []):
        if not is_login_player_body(char):
            continue
        if character_given_name(char).lower() == needle:
            out.append(char)
    return out


def find_unique_given_login(game, given_name: str):
    """Return the only login body with this given name, or None.

    When exactly one player / Echo uses that first name, connection login
    may accept either:

    * first name → password (classic MUD)
    * first name → surname → password

    Shared first names still require the surname prompt to disambiguate.
    """
    matches = find_players_by_given_name(game, given_name)
    if len(matches) == 1:
        return matches[0]
    return None


def interpret_unique_given_second_line(char, raw_line, *, password_hash, verify_fn):
    """Classify the line after a unique first name (password or surname).

    Check password first so a password that happens to look like a surname
    still logs in. Returns one of:

    * ``("ok", None)`` -- ``raw_line`` verified as the password
    * ``("surname_match", cleaned)`` -- typed this body's surname; ask password next
    * ``("new_surname", cleaned)`` -- different valid surname; new-character path
    * ``("fail", None)`` -- wrong password and not a usable surname
    """
    text = (raw_line or "").strip()
    if not text:
        return "fail", None
    # Password wins when both interpretations could apply.
    if password_hash and callable(verify_fn) and verify_fn(text, password_hash):
        return "ok", None
    cleaned, err = normalize_surname(text)
    if err is None and cleaned:
        body_sur = character_surname(char).lower()
        if body_sur and cleaned.lower() == body_sur:
            return "surname_match", cleaned
        # Empty-surname legacy body, or a different family name → new char.
        if cleaned.lower() != body_sur:
            return "new_surname", cleaned
    return "fail", None


def find_legacy_name_login(game, given_name: str):
    """Return a pre-surname body loginable by first name alone, or None.

    When exactly one login body has this given name and that body has no
    surname on file, connection login may skip the surname prompt and go
    straight to password (legacy single-name saves). Prefer
    ``find_unique_given_login`` for the modern unique-given short path.
    """
    hit = find_unique_given_login(game, given_name)
    if hit is None:
        return None
    if character_surname(hit):
        return None
    return hit


def legacy_surname_login_notice(char) -> str | None:
    """Post-login hint when a body still has no surname on file."""
    if char is None or character_surname(char):
        return None
    return (
        "[ALERT] You have no surname on file yet. "
        "Visit a sheriff's office and type: namechange YourSurname "
        "(see help namechange). "
        "Use surname on|off to show or hide it on who. "
        "See help surname."
    )


def find_player_by_given_surname(game, given_name: str, surname: str):
    """Exact (given, surname) match among login-capable bodies, or None."""
    g = (given_name or "").strip().lower()
    s = (surname or "").strip().lower()
    if not g:
        return None
    for char in find_players_by_given_name(game, given_name):
        if character_surname(char).lower() == s:
            return char
    return None


def given_surname_taken(game, given_name: str, surname: str, *, exclude=None) -> bool:
    """True if another login body already owns this (given, surname) pair."""
    hit = find_player_by_given_surname(game, given_name, surname)
    if hit is None:
        return False
    if exclude is not None and hit is exclude:
        return False
    return True


def key_is_taken(game, key: str) -> bool:
    """True if any Character already uses this storage key (any casing)."""
    needle = (key or "").strip().lower()
    if not needle:
        return True
    for char in list(getattr(game, "characters", None) or []):
        if (getattr(char, "key", None) or "").lower() == needle:
            return True
    return False


def allocate_storage_key(game, given_name: str, surname: str) -> str:
    """Pick an immutable unique ``Character.key`` for a new body.

    Prefer bare given name when free; else letters-only ``GivenSurname``;
    else append a numeric suffix.
    """
    given = (given_name or "").strip()
    sur = (surname or "").strip()
    if not given:
        raise ValueError("given_name required for storage key")
    if not key_is_taken(game, given):
        return given
    mash = "".join(ch for ch in (given + sur) if ch.isalpha())
    if mash and not key_is_taken(game, mash):
        # Title-case the mash the same way login names are cased.
        mash = mash[0].upper() + mash[1:]
        if not key_is_taken(game, mash):
            return mash
    base = mash or given
    for n in range(2, 10000):
        candidate = f"{base}{n}"
        if not key_is_taken(game, candidate):
            return candidate
    raise ValueError(f"could not allocate storage key for {given!r}")


def stamp_new_identity(char, game, given_name: str, surname: str) -> None:
    """Set given/surname/visibility/CNUM on a brand-new Character."""
    char.given_name = given_name
    char.surname = surname
    char.surname_visible = True
    char.public_name_order = PUBLIC_NAME_ORDER_GIVEN_FIRST
    taken = collect_taken_cnums(getattr(game, "characters", None) or [])
    # Include any CNUM already on this char (should be none for brand-new).
    existing = getattr(char, "cnum", None)
    if existing:
        try:
            from engine.char_cnum import validate_cnum
            taken.add(validate_cnum(existing))
        except ValueError:
            pass
    char.cnum = allocate_cnum(given_name, taken=taken)


def heal_mashed_given_surname(char) -> bool:
    """Split legacy ``GivenSurname`` storage keys into given + surname.

    Older bodies kept ``Character.key`` like ``ZackMarkson`` while
    ``namechange`` only stamped ``surname`` -- ``legal_public_name``
    then read as one word. Idempotent boot / desk repair.
    """
    if char is None:
        return False
    given = character_given_name(char)
    sur = character_surname(char)
    if not given or not sur:
        return False
    mash = "".join(ch for ch in (given + sur) if ch.isalpha())
    if not mash:
        return False
    if given.lower() != mash.lower() and given.lower() != sur.lower():
        key_face = _strip_prefix(getattr(char, "key", None) or "")
        if given.lower() != key_face.lower():
            return False
    if len(given) <= len(sur) or not given.lower().endswith(sur.lower()):
        return False
    peeled = given[: -len(sur)]
    if not peeled:
        return False
    char.given_name = peeled[0].upper() + peeled[1:] if len(peeled) > 1 else peeled.upper()
    return True


def ensure_character_identity(char, game=None) -> bool:
    """Backfill given_name / surname / CNUM on a loaded body. Returns changed.

    Legacy: given_name ← stripped key face, surname empty, allocate CNUM.
    """
    if char is None:
        return False
    changed = False
    given = getattr(char, "given_name", None)
    if not isinstance(given, str) or not given.strip():
        face = _strip_prefix(getattr(char, "key", None) or "")
        char.given_name = face if face and face != "?" else "Unknown"
        changed = True
    if not hasattr(char, "surname") or char.surname is None:
        char.surname = ""
        changed = True
    elif not isinstance(char.surname, str):
        char.surname = str(char.surname)
        changed = True
    if not hasattr(char, "surname_visible") or char.surname_visible is None:
        char.surname_visible = True
        changed = True
    if not hasattr(char, "public_name_order") or not char.public_name_order:
        char.public_name_order = PUBLIC_NAME_ORDER_GIVEN_FIRST
        changed = True
    elif char.public_name_order not in (
        PUBLIC_NAME_ORDER_GIVEN_FIRST,
        PUBLIC_NAME_ORDER_SURNAME_FIRST,
    ):
        char.public_name_order = PUBLIC_NAME_ORDER_GIVEN_FIRST
        changed = True
    if heal_mashed_given_surname(char):
        changed = True
    cnum = getattr(char, "cnum", None)
    if not isinstance(cnum, str) or not str(cnum).strip():
        # Only allocate when we have a game roster (avoids collisions in
        # a multi-char heal pass). heal_all_character_identities does a
        # second pass with the full taken set.
        if game is not None:
            taken = collect_taken_cnums(getattr(game, "characters", None) or [])
            char.cnum = allocate_cnum(character_given_name(char), taken=taken)
            changed = True
    return changed


def heal_all_character_identities(game) -> dict:
    """Boot heal: ensure every roster body has given/surname/CNUM.

    Idempotent. Returns a small stats dict for smoke / logs.
    """
    stats = {"healed": 0, "cnums_stamped": 0}
    if game is None:
        return stats
    roster = list(getattr(game, "characters", None) or [])
    # Pass 1: given_name / surname / visibility (no CNUM yet).
    for char in roster:
        if ensure_character_identity(char, game=None):
            stats["healed"] += 1
    # Pass 2: allocate missing CNUMs against the full taken set.
    taken = collect_taken_cnums(roster)
    for char in roster:
        cnum = getattr(char, "cnum", None)
        if isinstance(cnum, str) and cnum.strip():
            continue
        char.cnum = allocate_cnum(character_given_name(char), taken=taken)
        taken.add(char.cnum)
        stats["cnums_stamped"] += 1
        stats["healed"] += 1
    return stats


def is_sheriff_office_room(room) -> bool:
    """True when ``room`` is a sheriff's office lobby (not lot / cell).

    Matches Lebanon / Lawrence SoT keys ending in ``Sheriff's Office``
    (and legacy dig typo ``Sherrif's Office``) without Cell / Lot suffixes.
    """
    if room is None:
        return False
    key = getattr(room, "key", None) or ""
    if not isinstance(key, str):
        return False
    # Bare key after optional map qualify.
    bare = key.split(":", 1)[-1].strip()
    low = bare.lower()
    if "cell" in low or "lot" in low:
        return False
    return low in ("sheriff's office", "sherrif's office")
