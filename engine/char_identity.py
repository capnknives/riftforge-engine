"""
char_identity.py -- Given name, surname, CNUM, and ordinal targeting.

Player identity is ``given_name`` + ``surname``. Storage ``Character.key``
is still the SQLite ``characters.name`` PRIMARY KEY (Phase B dual-write:
UNIQUE ``cnum`` column, name stays the PK). CNUM is staff-only. Ordinal
query peel (``2.carl``, ``other carl``, ``second carl``) lives here so
room and world resolvers share one parser.

Engine-pure: no ``supers`` imports.
"""

from __future__ import annotations

import json
import re

from engine.char_cnum import (
    allocate_cnum,
    collect_taken_cnums,
    next_cnum,
    parse_cnum,
    validate_cnum,
)

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


def normalize_surname_optional(raw: str):
    """Validate surname when empty is allowed (unique given names).

    Returns ``(cleaned, error_or_None)`` where ``cleaned`` may be ``""``.
    """
    text = (raw or "").strip()
    if not text:
        return "", None
    return normalize_surname(text)


def surname_required_for_create(game, given_name: str, *, origin=None) -> bool:
    """True when another login body already uses this given name.

    Immortal Origins never require a mortal surname, even when the given
    name collides with another login body.
    """
    if origin in ("celestial", "cosmic"):
        return False
    return len(find_players_by_given_name(game, given_name)) > 0



def can_drop_surname(game, char) -> bool:
    """True when this body may clear surname and keep a unique given-name login.

    Same rule as skipping surname at creation: no other login body shares
    the given name.
    """
    if char is None:
        return False
    given = character_given_name(char)
    if not given:
        return False
    others = [
        body
        for body in find_players_by_given_name(game, given)
        if body is not char
    ]
    return len(others) == 0


# Tokens for ``namechange`` when the player wants a mononym (no surname).
_NAMECHANGE_DROP_SURNAME_TOKENS = frozenset({
    "none",
    "clear",
    "remove",
    "drop",
    "mononym",
    "-",
    "(none)",
    "no",
    "nosurname",
    "no surname",
})


def is_namechange_drop_surname(raw: str) -> bool:
    """True when ``namechange`` args mean remove surname, not set a new one."""
    text = (raw or "").strip().lower()
    if not text:
        return False
    return text in _NAMECHANGE_DROP_SURNAME_TOKENS


def validate_new_character_identity(game, given_name: str, surname: str):
    """Return a player-facing error string, or None when identity is valid."""
    given = (given_name or "").strip()
    if not given:
        return "A first name is required."
    sur = (surname or "").strip()
    if surname_required_for_create(game, given):
        if not sur:
            return (
                f"Another character already uses the name {given}. "
                "Pick a surname to tell them apart."
            )
        cleaned, err = normalize_surname(sur)
        if err:
            return err
        if given_surname_taken(game, given, cleaned):
            return (
                f"{given} {cleaned} is already taken. "
                "Choose a different name."
            )
        return None
    if sur:
        cleaned, err = normalize_surname(sur)
        if err:
            return err
        sur = cleaned
        if given_surname_taken(game, given, sur):
            return (
                f"{given} {sur} is already taken. "
                "Choose a different name."
            )
    else:
        if key_is_taken(game, given):
            return f"The name {given} is already taken."
    return None


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


def surname_is_targetable(char) -> bool:
    """True when given+surname may be a find / look / kit needle.

    Hidden surnames and ``public_mononym`` faces (Daniel the god, Ash)
    stay on file for staff inspect but must not collect as
    ``Daniel Miller``. That fuzzy needle is what let a parlor djinn
    kit overwrite the god.
    """
    if char is None:
        return False
    if not character_surname(char):
        return False
    if bool(getattr(char, "public_mononym", False)):
        return False
    return bool(getattr(char, "surname_visible", True))


def legal_public_name(char, *, force_surname=False) -> str:
    """Given + optional surname for who / greet when no assumed face.

    Respects ``surname_visible`` unless ``force_surname`` (GM tooling).
    When visible and ``public_name_order`` is surname-first, formats
    ``Surname, Given`` (desk-roster style, e.g. ``Hartwell, Mara``).

    Civic ``title`` (Sheriff, Deputy, Agent, Doctor, …) replaces the
    given name in ordinary prose so players see ``Sheriff Calder`` while
    the file still stores a real first name. ``public_mononym`` keeps
    famous one-word faces (Ash) as given-only in player text.
    """
    given = character_given_name(char) or "?"
    sur = character_surname(char)
    title = (getattr(char, "title", None) or "").strip()
    if title and sur:
        return f"{title} {sur}"
    if title:
        return f"{title} {given}"
    if bool(getattr(char, "public_mononym", False)) and not force_surname:
        return given
    if not sur:
        return given
    visible = bool(getattr(char, "surname_visible", True))
    if not (force_surname or visible):
        return given
    if public_name_order(char) == PUBLIC_NAME_ORDER_SURNAME_FIRST:
        return f"{sur}, {given}"
    return f"{given} {sur}"


_CAMEL_HUMP_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")


def storage_key_has_camel_mash(key_face: str) -> bool:
    """True when ``key_face`` has a lower→upper boundary (``CapnKnives``)."""
    peeled = (key_face or "").strip()
    if not peeled:
        return False
    base = re.sub(r"\d+$", "", peeled) or peeled
    return bool(_CAMEL_HUMP_RE.search(base))


def storage_key_matches_account_handle(game, char, key_face: str) -> bool:
    """True when the storage key is the linked account login/display handle.

    Account brands like ``CapnKnives`` are intentional mononyms -- they must
    not be CamelCase-split into fake given+surname pairs (``Capn`` + ``Knives``).
    """
    if not key_face or char is None:
        return False
    needle = key_face.strip().lower()
    linked = (getattr(char, "account", None) or "").strip().lower()
    if linked and linked == needle:
        return True
    if game is None:
        return False
    try:
        from engine.accounts import account_for_character

        acct = account_for_character(game, char)
        if acct is None:
            return False
        for candidate in (acct.name, acct.display_name):
            if candidate and str(candidate).strip().lower() == needle:
                return True
    except Exception:
        pass
    return False


def heal_account_mononym_identity(char, game=None) -> bool:
    """Undo a mistaken CamelCase split on an account-handle mononym.

    Bug 748 mash heal treated ``CapnKnives`` like ``ZackMarkson``. When the
    storage key matches the linked account name, peel the invented surname
    and restore the whole handle as ``given_name``.
    """
    if char is None:
        return False
    key_face = _strip_prefix(getattr(char, "key", None) or "")
    if not key_face or not storage_key_has_camel_mash(key_face):
        return False
    if not storage_key_matches_account_handle(game, char, key_face):
        return False
    given = character_given_name(char)
    sur = character_surname(char)
    mash_parts = split_mashed_key_identity(key_face)
    if mash_parts is not None and sur and (given, sur) == mash_parts:
        char.given_name = key_face
        char.surname = ""
        return True
    if not sur and given.lower() != key_face.lower():
        char.given_name = key_face
        return True
    return False


def humanize_storage_key(raw_key) -> str:
    """Best-effort public label when only a storage key string is known.

    Inserts spaces at lower→Upper boundaries so mashed legacy keys like
    ``ZackMarkson`` read as ``Zack Markson``. Does not invent surnames for
    single-token keys (``Hartwell`` stays ``Hartwell``). Ephemeral prefixes
    are stripped first. Prefer ``legal_public_name`` / ``reporter_display_name``
    when a live body is available.
    """
    from engine.command_support import strip_ephemeral_storage_prefix

    peeled = strip_ephemeral_storage_prefix(str(raw_key or "").strip())
    if not peeled or peeled == "?":
        return peeled or "?"
    # Drop trailing digits from allocate_storage_key suffixes (ZackMarkson2).
    base = re.sub(r"\d+$", "", peeled) or peeled
    # ``ZackMarkson`` → ``Zack Markson``; leave already-spaced faces alone.
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base)
    return spaced if spaced else "?"


def split_mashed_key_identity(raw_key):
    """Split a GivenSurname storage mash into ``(given, surname)`` or None.

    Only when there is exactly one lower→Upper boundary (``ZackMarkson`` →
    Zack + Markson). Multi-hump keys (``MaryAnneSmith``) and single-token
    keys (``Hartwell``, ``McDonald``) return None so we do not invent a
    wrong surname. Trailing numeric suffixes from ``allocate_storage_key``
    are stripped first (``ZackMarkson2`` → Zack + Markson).
    """
    from engine.command_support import strip_ephemeral_storage_prefix

    peeled = strip_ephemeral_storage_prefix(str(raw_key or "").strip())
    if not peeled or peeled == "?":
        return None
    base = re.sub(r"\d+$", "", peeled) or peeled
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base).strip()
    parts = [p for p in spaced.split() if p]
    if len(parts) != 2:
        return None
    given, sur = parts[0], parts[1]
    if not given.isalpha() or not sur.isalpha():
        return None
    if len(given) < 1 or len(sur) < SURNAME_MIN:
        return None
    return given[0].upper() + given[1:], sur[0].upper() + sur[1:]


def staff_face(char) -> str:
    """Staff / promote / freeze toast label for a live Character body.

    Always force-surname so mashed storage keys never appear in GM
    confirmation lines (``Zack Markson is now a GM``, not ``ZackMarkson``).
    """
    if char is None:
        return "?"
    try:
        return legal_public_name(char, force_surname=True)
    except Exception:
        return humanize_storage_key(getattr(char, "key", None))


def reporter_display_name(game, reporter_key) -> str:
    """Staff-facing reporter label for bug/suggest/help lists.

    Reports store the immutable ``Character.key`` for stability. Display
    looks up the live/Echo body and shows ``legal_public_name`` so mashed
    keys like ``ZackMarkson`` read as ``Zack Markson``. Missing bodies
    fall back to a humanized key string (CamelCase split).
    """
    raw = str(reporter_key or "").strip()
    if not raw:
        return "?"
    char = None
    if game is not None:
        find = getattr(game, "find_character", None)
        if callable(find):
            char = find(raw)
        if char is None:
            roster = getattr(game, "characters", None) or []
            for body in roster:
                if getattr(body, "key", None) == raw:
                    char = body
                    break
    if char is not None:
        try:
            return legal_public_name(char, force_surname=True)
        except Exception:
            pass
    return humanize_storage_key(raw)

# Articles / glue in a setshort face -- never look-at-the / look-a.
_SHORT_DESC_SKIP = frozenset({
    "a", "an", "the", "of", "and", "or", "in", "on", "at", "to", "for",
    "with", "from",
})

# Same tokenizer as item names (engine.command_support._ITEM_NAME_TOKEN_SPLIT).
# Kept here so identity matching does not import command_support at module
# load (that module lazy-imports this one for needles).
_IDENTITY_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _phrase_match_needles(raw) -> list[str]:
    """Full phrase plus each ≥3-letter word, skipping articles.

    ``look injunction`` must hit ``The Overturned Injunction`` the same
    way ``look appeal`` hits ``a Remanded Appeal`` and ``look sunburn``
    hits ``a sunburned man``. Catalog keys, legal names, and setshort
    faces all go through this splitter -- not only short_desc_override.
    """
    face = " ".join(str(raw or "").split()).strip().lower()
    if not face:
        return []
    out = [face]
    for part in face.replace("-", " ").split():
        clean = part.strip(".,;:'\"()[]").strip()
        if len(clean) < 3:
            continue
        if clean in _SHORT_DESC_SKIP:
            continue
        if clean not in out:
            out.append(clean)
    return out


def _short_desc_match_needles(char) -> list[str]:
    """Words from ``short_desc_override`` (setshort / hunt faces).

    Room listing shows this string to strangers. ``look`` / ``attack``
    must accept the same words -- ``look sunburn`` and ``look man`` for
    ``a sunburned man`` -- not only the storage key. Staff pierce still
    displays the legal name; the override still has to target.
    """
    raw = getattr(char, "short_desc_override", None)
    if not raw:
        return []
    return _phrase_match_needles(raw)


def identity_query_matches(query, labels) -> bool:
    """True when ``query`` hits any identity needle at word boundaries.

    Shared by look / consider / kill / fight (``_collect_character_matches``).
    Multi-word queries are a contiguous phrase (``overturned injunction``).
    Single tokens prefix-match whole words only (``injunction`` in
    ``The Overturned Injunction``; ``er`` in ``Erin``). One-letter queries
    must equal a whole token. Substring-in-the-middle (``unc`` in
    ``injunction``) does not count -- same rule as item names.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return False
    for raw in labels or ():
        text = str(raw or "").strip().lower()
        if not text:
            continue
        if " " in needle:
            pat = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
            if re.search(pat, text):
                return True
            continue
        tokens = [tok for tok in _IDENTITY_TOKEN_SPLIT.split(text) if tok]
        if not tokens:
            continue
        if len(needle) == 1:
            if needle in tokens:
                return True
            continue
        if any(tok.startswith(needle) for tok in tokens):
            return True
    return False


def identity_match_needles(char) -> list[str]:
    """Lowercase strings that should match this character in targeting."""
    needles = []

    def _add_phrase(raw):
        for extra in _phrase_match_needles(raw):
            if extra not in needles:
                needles.append(extra)

    key = (getattr(char, "key", None) or "").lower()
    raw_key = getattr(char, "key", None) or ""
    if key:
        _add_phrase(key)
        # Peel ephemeral prefix for bare-name match.
        peeled = _strip_prefix(key).lower()
        if peeled and peeled != "?":
            _add_phrase(peeled)
        spaced = humanize_storage_key(raw_key).lower()
        if spaced and spaced != peeled:
            _add_phrase(spaced)
    given = character_given_name(char).lower()
    if given:
        _add_phrase(given)
    sur = character_surname(char).lower()
    if given and sur and surname_is_targetable(char):
        _add_phrase(f"{given} {sur}")
        _add_phrase(f"{sur}, {given}")
        mash = f"{given}{sur}"
        if mash not in needles:
            needles.append(mash)
    face = (
        getattr(char, "assumed_face", None)
        or getattr(char, "husk_display_name", None)
        or ""
    )
    if face:
        _add_phrase(str(face).strip())
    for extra in _short_desc_match_needles(char):
        if extra not in needles:
            needles.append(extra)
    # Possessive keys (gabriel's appaloosa) -- also match without the mark
    # so shell-quote peeling and typed "gabriels appaloosa" still hit.
    folded = []
    for label in needles:
        stripped = (
            label.replace("'", "").replace("\u2019", "").replace("\u2018", "")
        )
        if stripped and stripped not in needles and stripped not in folded:
            folded.append(stripped)
    needles.extend(folded)
    return needles


def parse_target_ordinal(query: str):
    """Peel ``2.carl`` / ``other carl`` / ``second carl`` from a query.

    Returns ``(ordinal_or_None, remaining_query)``. Ordinal is 1-based.
    Remaining query is stripped; may still be empty.
    """
    raw = (query or "").strip()
    if not raw:
        return None, ""
    # Dot form: 2.carl / 2.Carl Hartwell
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


def is_account_linked_body(char) -> bool:
    """True when this body belongs to a login account.

    This is the kit / persist lock. Do **not** use ``is_npc`` here -- a
    heal that flips ``is_npc`` on a player must not make the body look
    like spawn fodder (CNUM ``DN00001`` / Daniel was kitted as a parlor
    djinn that way). Spirits, husks, and guests still fail this check.
    """
    if char is None:
        return False
    key_low = (getattr(char, "key", None) or "").lower()
    if key_low.startswith("gmspirit:") or key_low.startswith("husk:"):
        return False
    if getattr(char, "is_guest", False) or getattr(char, "transient_soul", False):
        return False
    return bool((getattr(char, "account", None) or "").strip())


def is_login_player_body(char) -> bool:
    """True for corporeal player / Echo bodies that can own a login slot.

    Skips NPCs, vessel husks, and ephemeral ``husk:`` / ``gmspirit:`` keys.

    After an ``is_npc`` flip this returns False -- use
    ``is_account_linked_body`` for kit/restamp/persist guards.
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


def stats_blob_is_login_pfile(stats) -> bool:
    """True when a persisted stats blob is a passworded / account PC.

    CNUM clash heals keep this body and remint the NPC occupant. Empty
    ``account`` still counts when a password hash is present (Andy /
    AD00002 vs the town NPC that reused the tag).
    """
    if not isinstance(stats, dict):
        return False
    if (stats.get("account") or "").strip():
        return True
    if stats.get("is_npc") is True:
        return False
    pw = (stats.get("password_hash") or stats.get("password") or "").strip()
    return bool(pw)


def is_passworded_roster_body(char) -> bool:
    """True for a login PC even when the account field is still empty.

    ``is_account_linked_body`` misses passworded hunters whose blob never
    got ``account`` stamped. Clash heals must still prefer them over NPCs.
    """
    if is_account_linked_body(char):
        return True
    if char is None:
        return False
    key_low = (getattr(char, "key", None) or "").lower()
    if key_low.startswith("gmspirit:") or key_low.startswith("husk:"):
        return False
    if getattr(char, "is_guest", False) or getattr(char, "transient_soul", False):
        return False
    if getattr(char, "is_npc", False):
        return False
    return bool((getattr(char, "password_hash", None) or "").strip())


def rewrite_stats_blob_cnum(blob, new_cnum):
    """Return a stats JSON string with ``cnum`` set to ``new_cnum``."""
    data = {}
    if isinstance(blob, str) and blob.strip():
        try:
            parsed = json.loads(blob)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = {}
        if isinstance(parsed, dict):
            data = parsed
    elif isinstance(blob, dict):
        data = dict(blob)
    data["cnum"] = new_cnum
    return json.dumps(data)


def remint_cnum_clash_for_restore(
    conn,
    *,
    keep_name,
    keep_cnum,
    keep_stats=None,
    game=None,
):
    """Free ``keep_cnum`` for a restore/persist write. Remint the loser.

    Passworded / account PCs keep the archived tag; NPCs move to the next
    free number under the same prefix. Two live PCs on one tag: the
    restoree takes the next free number so we never steal another player's
    CNUM. Returns ``(cnum_for_keep_name, notes)``.
    """
    notes = []
    name = (keep_name or "").strip()
    if not name:
        return None, notes
    wanted = None
    if isinstance(keep_cnum, str) and keep_cnum.strip():
        try:
            wanted = validate_cnum(keep_cnum)
        except ValueError:
            wanted = None
    if wanted is None:
        return None, notes
    keep_is_pc = stats_blob_is_login_pfile(keep_stats)

    occupant = None
    if conn is not None:
        occupant = conn.execute(
            "SELECT name, stats FROM characters "
            "WHERE cnum = ? AND name != ? COLLATE NOCASE",
            (wanted, name),
        ).fetchone()
    ram_occupant = None
    if game is not None:
        ram_occupant = find_character_by_cnum(game, wanted)
        if ram_occupant is not None:
            ram_key = (getattr(ram_occupant, "key", None) or "").strip()
            if ram_key.lower() == name.lower():
                ram_occupant = None

    if occupant is None and ram_occupant is None:
        return wanted, notes

    occ_name = occupant[0] if occupant else (getattr(ram_occupant, "key", None) or "")
    occ_blob = occupant[1] if occupant else None
    occ_stats = {}
    if isinstance(occ_blob, str) and occ_blob.strip():
        try:
            parsed = json.loads(occ_blob)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = {}
        if isinstance(parsed, dict):
            occ_stats = parsed
    elif ram_occupant is not None:
        occ_stats = {
            "account": getattr(ram_occupant, "account", None),
            "password_hash": getattr(ram_occupant, "password_hash", None),
            "is_npc": getattr(ram_occupant, "is_npc", False),
        }
    occ_is_pc = stats_blob_is_login_pfile(occ_stats) or is_passworded_roster_body(
        ram_occupant
    )

    remint_keep = False
    if keep_is_pc and not occ_is_pc:
        remint_keep = False
    elif occ_is_pc and not keep_is_pc:
        remint_keep = True
    elif keep_is_pc and occ_is_pc:
        remint_keep = True
    else:
        remint_keep = False

    parsed = parse_cnum(wanted)
    prefix = parsed[0] if parsed else "XX"
    taken = set()
    if conn is not None:
        for row in conn.execute(
            "SELECT cnum FROM characters "
            "WHERE cnum IS NOT NULL AND cnum != ''"
        ):
            raw = row[0]
            if isinstance(raw, str) and raw.strip():
                try:
                    taken.add(validate_cnum(raw))
                except ValueError:
                    continue
        # Leftover sidecar shards (bug report 1776 keeps homestead
        # crates after a character row is gone) still occupy a CNUM.
        # Skipping those numbers is what lets remint avoid a UNIQUE
        # abort on character_heavy_blobs.
        taken.update(_taken_cnums_from_heavy_blobs(conn))
    if game is not None:
        taken.update(collect_taken_cnums(getattr(game, "characters", None)))
    taken.add(wanted)

    if remint_keep:
        new = next_cnum(prefix, taken)
        notes.append(f"restoree {name} {wanted} -> {new} (live PC holds tag)")
        print(
            f"[cnum] restore remint {name} {wanted} -> {new} "
            f"(occupant {occ_name} is a login pfile)",
            flush=True,
        )
        return new, notes

    new = next_cnum(prefix, taken)
    notes.append(f"occupant {occ_name} {wanted} -> {new}")
    print(
        f"[cnum] remint occupant {occ_name} {wanted} -> {new} "
        f"so {name} can keep the tag",
        flush=True,
    )
    if conn is not None and occ_name:
        new_blob = rewrite_stats_blob_cnum(occ_blob or "{}", new)
        conn.execute(
            "UPDATE characters SET cnum = ?, stats = ? "
            "WHERE name = ? COLLATE NOCASE",
            (new, new_blob, occ_name),
        )
        # Keep homestead crates / journals on the lasting tag the PC
        # still holds. Occupant items move by holder_key; heavy blobs
        # on ``wanted`` belong to the player, not the NPC.
        _retarget_cnum_sidecars(
            conn, wanted, new, occ_name, move_heavy=False,
        )
    if ram_occupant is not None:
        ram_occupant.cnum = new
        if game is not None:
            try:
                from engine.persistence import mark_character_dirty

                mark_character_dirty(game, ram_occupant, force=True)
            except Exception:
                pass
    return wanted, notes


def _retarget_cnum_sidecars(
    conn, old_cnum, new_cnum, owner_name, *, move_heavy=True,
):
    """Move item / homestead / heavy-blob CNUM pointers with a remint.

    ``move_heavy=False`` when an NPC yields a tag a login PC still
    wears -- crates and journals on that CNUM belong to the player
    (bug report 1846). Occupant inventory still remaps by holder_key.
    """
    if conn is None or not old_cnum or not new_cnum:
        return
    item_cols = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    if "holder_cnum" in item_cols:
        conn.execute(
            "UPDATE items SET holder_cnum = ? "
            "WHERE holder_cnum = ? AND holder_key = ? COLLATE NOCASE",
            (new_cnum, old_cnum, owner_name),
        )
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if move_heavy and "character_heavy_blobs" in tables:
        _move_heavy_blob_cnum(conn, old_cnum, new_cnum)
    if "homestead_plots" in tables:
        plot_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(homestead_plots)")
        }
        # Plot owner_key stays on the login PC; only retarget plots
        # when we are moving the owner themselves (heavy move).
        if move_heavy and "owner_cnum" in plot_cols:
            conn.execute(
                "UPDATE homestead_plots SET owner_cnum = ? WHERE owner_cnum = ?",
                (new_cnum, old_cnum),
            )


def _taken_cnums_from_heavy_blobs(conn):
    """CNUM strings that still have sidecar rows, including orphan crates."""
    taken = set()
    if conn is None:
        return taken
    try:
        rows = conn.execute(
            "SELECT DISTINCT cnum FROM character_heavy_blobs "
            "WHERE cnum IS NOT NULL AND cnum != ''",
        )
    except Exception:
        return taken
    for row in rows:
        raw = row[0] if row else None
        if isinstance(raw, str) and raw.strip():
            try:
                taken.add(validate_cnum(raw))
            except ValueError:
                continue
    return taken


def _sidecar_payload_json(payload):
    """Normalize a sidecar cell to the JSON text SQLite stores."""
    if isinstance(payload, dict):
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if payload is None:
        return "{}"
    return payload


def _parse_heavy_blob_payload(payload):
    """Dict from a sidecar cell. Hooks codec first; JSON fallback for tests."""
    parsed = None
    try:
        from engine import hooks

        parsed = hooks.parse_heavy_shard_payload(payload)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str) and payload.strip():
        try:
            loaded = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _merge_heavy_blob_payloads(shard, dest_payload, src_payload):
    """Combine two shards for the same (cnum, shard) instead of aborting.

    Homestead crates and Echo journals must not vanish when a remint lands
    on a leftover sidecar. Other shards prefer the live source payload.
    """
    from engine import hooks

    shard_name = str(shard or "")
    dest = _parse_heavy_blob_payload(dest_payload)
    src = _parse_heavy_blob_payload(src_payload)
    if shard_name == "property_stash":
        # Reattach / remint must UNION piles. Key-wise replace would
        # drop 134 loose items when a later remint hop only saved four
        # fish (bug report 1846).
        return _sidecar_payload_json(_union_property_stash_payloads(dest, src))
    if shard_name == "helper_roster":
        return _sidecar_payload_json(_union_helper_roster_payloads(dest, src))
    if shard_name == "life_log":
        dest_empty = hooks.life_log_payload_empty(dest)
        src_empty = hooks.life_log_payload_empty(src)
        if src_empty and not dest_empty:
            return _sidecar_payload_json(dest)
        if dest_empty and not src_empty:
            return _sidecar_payload_json(src)
        # Both nonempty: keep old days, fill missing diary pages, append
        # events that are not already on dest (bug report 1846 reattach).
        return _sidecar_payload_json(_union_life_log_payloads(dest, src))
    if src:
        return _sidecar_payload_json(src)
    return _sidecar_payload_json(dest)


def _move_heavy_blob_cnum(conn, old_cnum, new_cnum):
    """Retarget sidecar shards one row at a time.

    A bulk ``UPDATE ... SET cnum = new WHERE cnum = old`` IntegrityError's
    when ``new`` already has that shard -- leftover property_stash from a
    retired body (kept on purpose so crates survive force-full wipe).
    Copyover used to cancel the whole Veil rewrite on that UNIQUE clash.
    """
    old_cnum = str(old_cnum or "").strip()
    new_cnum = str(new_cnum or "").strip()
    if not old_cnum or not new_cnum or old_cnum == new_cnum:
        return
    try:
        rows = conn.execute(
            "SELECT shard, payload FROM character_heavy_blobs WHERE cnum=?",
            (old_cnum,),
        ).fetchall()
    except Exception:
        return
    for shard, payload in rows:
        dest = conn.execute(
            "SELECT payload FROM character_heavy_blobs "
            "WHERE cnum=? AND shard=?",
            (new_cnum, shard),
        ).fetchone()
        if dest is None:
            conn.execute(
                "UPDATE character_heavy_blobs SET cnum=? "
                "WHERE cnum=? AND shard=?",
                (new_cnum, old_cnum, shard),
            )
            continue
        merged = _merge_heavy_blob_payloads(shard, dest[0], payload)
        conn.execute(
            "UPDATE character_heavy_blobs SET payload=? "
            "WHERE cnum=? AND shard=?",
            (merged, new_cnum, shard),
        )
        conn.execute(
            "DELETE FROM character_heavy_blobs WHERE cnum=? AND shard=?",
            (old_cnum, shard),
        )


def _stash_row_inum(row):
    """Instance id for a persisted item dict, or empty if none."""
    if not isinstance(row, dict):
        return ""
    container = row.get("container")
    if not isinstance(container, dict):
        container = {}
    inum = str(container.get("inum") or row.get("inum") or "").strip()
    return inum


def _union_item_rows(left, right):
    """Concatenate stash rows; skip duplicate INUMs we already kept."""
    out = []
    seen = set()
    for row in list(left or []) + list(right or []):
        if not isinstance(row, dict):
            continue
        inum = _stash_row_inum(row)
        if inum:
            if inum in seen:
                continue
            seen.add(inum)
        out.append(row)
    return out


def _union_home_boxes(left, right):
    """Merge labeled crates by label; union contents inside each crate."""
    by_label = {}
    order = []
    for box in list(left or []) + list(right or []):
        if not isinstance(box, dict):
            continue
        label = str(box.get("label") or "crate").strip() or "crate"
        key = label.lower()
        if key not in by_label:
            by_label[key] = {
                "key": str(box.get("key") or f"box_{len(order) + 1}"),
                "label": label,
                "contents": list(box.get("contents") or []),
            }
            order.append(key)
            continue
        by_label[key]["contents"] = _union_item_rows(
            by_label[key].get("contents"),
            box.get("contents"),
        )
    return [by_label[k] for k in order]


def _union_property_stash_payloads(dest, src):
    """Keep every crate and loose item from both remint hops."""
    dest = dest if isinstance(dest, dict) else {}
    src = src if isinstance(src, dict) else {}
    out = dict(dest)
    out["home_stash_items"] = _union_item_rows(
        dest.get("home_stash_items"), src.get("home_stash_items"),
    )
    out["home_boxes"] = _union_home_boxes(
        dest.get("home_boxes"), src.get("home_boxes"),
    )
    out["pit_stash_items"] = _union_item_rows(
        dest.get("pit_stash_items"), src.get("pit_stash_items"),
    )
    out["prison_property_items"] = _union_item_rows(
        dest.get("prison_property_items"), src.get("prison_property_items"),
    )
    out["bugworld_property_items"] = _union_item_rows(
        dest.get("bugworld_property_items"), src.get("bugworld_property_items"),
    )
    look = {}
    for source in (dest.get("bugworld_look_stash"), src.get("bugworld_look_stash")):
        if isinstance(source, dict):
            look.update(source)
    out["bugworld_look_stash"] = look or None
    return out


def _union_helper_roster_payloads(dest, src):
    """Keep crew rows from both remint hops (unique npc_key / given_name)."""
    dest = dest if isinstance(dest, dict) else {}
    src = src if isinstance(src, dict) else {}
    rows = []
    seen = set()
    for row in list(dest.get("helper_roster") or []) + list(
        src.get("helper_roster") or []
    ):
        if not isinstance(row, dict):
            continue
        ident = (
            str(row.get("npc_key") or "").strip().lower()
            or str(row.get("given_name") or "").strip().lower()
        )
        if ident:
            if ident in seen:
                continue
            seen.add(ident)
        rows.append(row)
    return {"helper_roster": rows}


def _union_life_log_payloads(dest, src):
    """Keep dest journal pages; add src days and events dest does not have."""
    out = dict(dest or {})
    side = src if isinstance(src, dict) else {}
    dest_events = list(out.get("life_log_events") or [])
    src_events = list(side.get("life_log_events") or [])
    seen = set()
    merged_events = []
    for event in dest_events + src_events:
        if not isinstance(event, dict):
            continue
        key = json.dumps(event, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        merged_events.append(event)
    if merged_events:
        out["life_log_events"] = merged_events
    for key in ("life_log_diary", "life_log_daily"):
        merged = {}
        for source in (out.get(key), side.get(key)):
            if not isinstance(source, dict):
                continue
            for day, page in source.items():
                day_key = str(day)
                if day_key in merged or not isinstance(page, dict):
                    continue
                merged[day_key] = dict(page)
        if merged:
            out[key] = merged
    meta = dict(side.get("life_log_meta") or {})
    meta.update(out.get("life_log_meta") or {})
    if merged_events:
        meta["raw_event_count"] = len(merged_events)
    diary = out.get("life_log_diary")
    if isinstance(diary, dict):
        meta["diary_day_count"] = len(diary)
    if meta:
        out["life_log_meta"] = meta
    return out


def _property_stash_rank(parsed, payload):
    """Order leftover stashes: labeled crates first, then item count, then bytes.

    Payload length alone would pick a later remint hop of four fish over
    the homestead crate sidecar (bug report 1846).
    """
    parsed = parsed if isinstance(parsed, dict) else {}
    boxes = parsed.get("home_boxes")
    if not isinstance(boxes, list):
        boxes = []
    n_contents = 0
    for box in boxes:
        if not isinstance(box, dict):
            continue
        contents = box.get("contents")
        if isinstance(contents, list):
            n_contents += len(contents)
    n_loose = 0
    for key in (
        "home_stash_items",
        "pit_stash_items",
        "prison_property_items",
        "bugworld_property_items",
    ):
        rows = parsed.get(key)
        if isinstance(rows, list):
            n_loose += len(rows)
    size = len(payload) if isinstance(payload, (str, bytes)) else 0
    return (len(boxes), n_contents + n_loose, size)


def reattach_orphaned_login_sidecars(game, conn=None):
    """Walk login PCs back onto unheld prefix tags and fold leftover shards.

    Persist snapshot-clash reminted passworded PCs and left homestead
    sidecars on every old CNUM (bug report 1846). Player tags are lasting:
    if exactly one login PC uses a prefix, restore that body onto the
    largest crate tag and UNION every other unheld same-prefix shard
    (loose piles, later remint hops, journals, helper rosters).
    """
    from engine.char_cnum import (
        cnum_prefix,
        parse_cnum,
        validate_cnum,
    )
    from engine.char_index import iter_characters
    from engine import hooks as hooks_mod

    notes = []
    conn = conn or (getattr(game, "db", None) if game is not None else None)
    if game is None or conn is None:
        return notes

    held = set()
    try:
        for row in conn.execute(
            "SELECT cnum FROM characters "
            "WHERE cnum IS NOT NULL AND cnum != ''"
        ):
            try:
                held.add(validate_cnum(row[0]))
            except (TypeError, ValueError):
                continue
    except Exception:
        return notes
    held.update(collect_taken_cnums(iter_characters(game)))

    # Persist remint hops leave shards on every old tag -- crates on one
    # CNUM, a later pile of fish on another, journals on a third. Fold
    # every unheld same-prefix shard; do not keep only the largest crate.
    stash_by_prefix = {}
    shards_by_prefix = {}
    try:
        rows = conn.execute(
            "SELECT cnum, shard, payload FROM character_heavy_blobs "
            "WHERE cnum IS NOT NULL AND cnum != ''",
        ).fetchall()
    except Exception:
        return notes
    for raw_cnum, shard, payload in rows:
        try:
            tag = validate_cnum(raw_cnum)
        except (TypeError, ValueError):
            continue
        if tag in held:
            continue
        parsed_tag = parse_cnum(tag)
        if not parsed_tag:
            continue
        prefix = parsed_tag[0]
        shards_by_prefix.setdefault(prefix, set()).add(tag)
        if str(shard) != "property_stash":
            continue
        parsed = hooks_mod.parse_heavy_shard_payload(payload)
        if hooks_mod.property_stash_payload_empty(parsed):
            continue
        rank = _property_stash_rank(parsed, payload)
        stash_by_prefix.setdefault(prefix, []).append((tag, rank))

    login_by_prefix = {}
    for char in list(iter_characters(game)):
        if not is_passworded_roster_body(char):
            continue
        prefix = None
        raw = getattr(char, "cnum", None)
        if isinstance(raw, str) and raw.strip():
            try:
                prefix = parse_cnum(validate_cnum(raw))[0]
            except (TypeError, ValueError):
                prefix = None
        if not prefix:
            prefix = cnum_prefix(character_given_name(char))
        login_by_prefix.setdefault(prefix, []).append(char)

    for prefix, pcs in login_by_prefix.items():
        candidates = stash_by_prefix.get(prefix) or []
        extras = shards_by_prefix.get(prefix) or set()
        if not candidates and not extras:
            continue
        if len(pcs) != 1:
            print(
                f"[cnum] skip reattach prefix {prefix}: "
                f"{len(pcs)} login PCs, will not guess",
                flush=True,
            )
            continue
        pc = pcs[0]
        current = None
        raw = getattr(pc, "cnum", None)
        if isinstance(raw, str) and raw.strip():
            try:
                current = validate_cnum(raw)
            except (TypeError, ValueError):
                current = None
        current_rank = (0, 0, 0)
        if current:
            row = conn.execute(
                "SELECT payload FROM character_heavy_blobs "
                "WHERE cnum=? AND shard=?",
                (current, "property_stash"),
            ).fetchone()
            if row:
                parsed = hooks_mod.parse_heavy_shard_payload(row[0])
                current_rank = _property_stash_rank(parsed, row[0])
        if candidates:
            winner, win_rank = max(candidates, key=lambda item: item[1])
        else:
            winner, win_rank = current, current_rank
        if not winner:
            continue
        lasting = winner
        if current and current_rank >= win_rank and current_rank > (0, 0, 0):
            lasting = current
        extra = sorted(
            tag for tag in extras
            if tag and tag != lasting and tag != current
        )
        if lasting == current and not extra:
            continue
        _restore_login_pc_cnum(
            game, conn, pc, lasting=lasting, old=current, extra_cnums=extra,
        )
        notes.append(
            f"{getattr(pc, 'key', '?')} {current} -> {lasting} "
            f"(reattach leftover house-tag shards)"
        )
    return notes



def retarget_orphan_stash_sidecars(game, conn=None, *, backup_db_path=None):
    """Move homestead/pit sidecars onto the live PC CNUM for that name.

    Force-full keep preserves ``property_stash`` on the *old* tag while
    the character row is inserted under a reminted CNUM (live 2026-09-20:
    Ayla AL00002 crates vs AL00038 body). ``stash`` then looks empty.
    The one-shot ``STASH_SIDECAR_CNUM_REATTACH_V1`` already stamped, and
    it skipped tags still held by an NPC (a cast NPC on DN00001).

    This walk is idempotent: passworded bodies that already hold their
    sidecar are a no-op. Does not remint the PC back onto the old tag.
    """
    conn = conn or (getattr(game, "db", None) if game is not None else None)
    if conn is None:
        return []
    from engine.char_index import iter_characters

    notes = []
    name_by_cnum = _stash_owner_names_from_backup(backup_db_path)
    passworded = {}
    if game is not None:
        for char in list(iter_characters(game)):
            if not is_passworded_roster_body(char):
                continue
            key = (getattr(char, "key", None) or "").strip()
            if key:
                passworded[key.lower()] = char
    # SQLite fallback when called from surgical restore (no Game()).
    if not passworded:
        try:
            for name, cnum, blob in conn.execute(
                "SELECT name, cnum, stats FROM characters"
            ):
                stats = {}
                if blob:
                    try:
                        parsed = json.loads(blob)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        parsed = {}
                    if isinstance(parsed, dict):
                        stats = parsed
                fake = type("Row", (), {
                    "key": name,
                    "cnum": cnum,
                    "account": stats.get("account") or "",
                    "password_hash": stats.get("password_hash") or "",
                    "is_npc": bool(stats.get("is_npc")),
                    "is_guest": bool(stats.get("is_guest")),
                })()
                if is_passworded_roster_body(fake):
                    passworded[(name or "").strip().lower()] = fake
        except Exception:
            return notes

    live_cnum_by_name = {}
    for key, char in passworded.items():
        raw = getattr(char, "cnum", None)
        if isinstance(raw, str) and raw.strip():
            try:
                live_cnum_by_name[key] = validate_cnum(raw)
            except (TypeError, ValueError):
                continue

    prefix_uniques = {}
    for key, tag in live_cnum_by_name.items():
        parsed = parse_cnum(tag)
        if not parsed:
            continue
        prefix_uniques.setdefault(parsed[0], []).append(key)

    try:
        rows = conn.execute(
            "SELECT cnum FROM character_heavy_blobs "
            "WHERE shard='property_stash' AND cnum IS NOT NULL AND cnum != ''"
        ).fetchall()
    except Exception:
        return notes

    for (raw_cnum,) in rows:
        try:
            old = validate_cnum(raw_cnum)
        except (TypeError, ValueError):
            continue
        try:
            payload_row = conn.execute(
                "SELECT payload FROM character_heavy_blobs "
                "WHERE cnum=? AND shard=?",
                (old, "property_stash"),
            ).fetchone()
        except Exception:
            payload_row = None
        parsed_stash = _parse_heavy_blob_payload(
            payload_row[0] if payload_row else None,
        )
        stash_rank = _property_stash_rank(
            parsed_stash, payload_row[0] if payload_row else "",
        )
        if stash_rank[0] == 0 and stash_rank[1] == 0:
            # Empty celestial NPC leftovers (143B of []) must not fold
            # onto a unique-prefix login PC.
            continue
        holder_name = None
        try:
            row = conn.execute(
                "SELECT name, stats FROM characters WHERE cnum=?",
                (old,),
            ).fetchone()
        except Exception:
            row = None
        if row:
            holder_name = (row[0] or "").strip()
            holder_stats = {}
            if row[1]:
                try:
                    parsed = json.loads(row[1])
                except (TypeError, ValueError, json.JSONDecodeError):
                    parsed = {}
                if isinstance(parsed, dict):
                    holder_stats = parsed
            holder = type("Row", (), {
                "key": holder_name,
                "cnum": old,
                "account": holder_stats.get("account") or "",
                "password_hash": holder_stats.get("password_hash") or "",
                "is_npc": bool(holder_stats.get("is_npc")),
                "is_guest": False,
            })()
            if is_passworded_roster_body(holder):
                continue
        owner_key = (name_by_cnum.get(old) or "").strip().lower()
        if not owner_key:
            parsed = parse_cnum(old)
            prefix = parsed[0] if parsed else None
            names = prefix_uniques.get(prefix) or []
            if len(names) == 1:
                owner_key = names[0]
        if not owner_key:
            continue
        new = live_cnum_by_name.get(owner_key)
        if not new or new == old:
            continue
        _move_heavy_blob_cnum(conn, old, new)
        pc = passworded.get(owner_key)
        if game is not None and pc is not None:
            _apply_property_sidecar_to_character(pc, conn, new)
        notes.append(f"{owner_key} {old} -> {new}")
        print(
            f"[cnum] retarget stash sidecar {owner_key!r} {old} -> {new}",
            flush=True,
        )
    return notes


def _stash_owner_names_from_backup(backup_db_path=None):
    """Map CNUM -> storage name from a backup DB (newest nightly default)."""
    paths = []
    if backup_db_path:
        paths.append(backup_db_path)
    else:
        try:
            from engine.world_backup import backups_root
            import os
            root = backups_root()
            if root and os.path.isdir(root):
                dates = sorted(
                    (
                        name for name in os.listdir(root)
                        if len(name) == 10 and name[4] == "-" and name[7] == "-"
                    ),
                    reverse=True,
                )
                for date in dates:
                    candidate = os.path.join(root, date, "riftforge.db")
                    if os.path.isfile(candidate):
                        paths.append(candidate)
                        break
                pre = os.path.join(root, "pre-deploy")
                if os.path.isdir(pre):
                    stems = sorted(os.listdir(pre), reverse=True)
                    for stem in stems:
                        candidate = os.path.join(pre, stem, "riftforge.db")
                        if os.path.isfile(candidate):
                            paths.append(candidate)
                            break
        except Exception:
            pass
    out = {}
    import sqlite3
    for path in paths:
        try:
            bak = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            for name, cnum in bak.execute(
                "SELECT name, cnum FROM characters "
                "WHERE cnum IS NOT NULL AND cnum != ''"
            ):
                tag = str(cnum or "").strip()
                key = (name or "").strip()
                if tag and key and tag not in out:
                    out[tag] = key
        except sqlite3.Error:
            pass
        finally:
            bak.close()
        if out:
            break
    return out


def _restore_login_pc_cnum(
    game, conn, pc, *, lasting, old, extra_cnums=(),
):
    """Move a login PC onto ``lasting`` and merge every remint-hop sidecar."""
    name = getattr(pc, "key", None) or ""
    pc.cnum = lasting
    for extra in extra_cnums or ():
        if extra and extra != lasting:
            _move_heavy_blob_cnum(conn, extra, lasting)
    if old and old != lasting:
        _move_heavy_blob_cnum(conn, old, lasting)
        _retarget_cnum_sidecars(
            conn, old, lasting, name, move_heavy=False,
        )
    stats = None
    try:
        row = conn.execute(
            "SELECT stats FROM characters WHERE name=? COLLATE NOCASE",
            (name,),
        ).fetchone()
        stats = row[0] if row else None
    except Exception:
        stats = None
    conn.execute(
        "UPDATE characters SET cnum = ?, stats = ? "
        "WHERE name = ? COLLATE NOCASE",
        (lasting, rewrite_stats_blob_cnum(stats or "{}", lasting), name),
    )
    try:
        from engine.persistence import mark_character_dirty

        mark_character_dirty(game, pc, force=True)
    except Exception:
        pass
    try:
        from engine.id_reserve import note_character_cnum

        note_character_cnum(pc)
    except Exception:
        pass
    _apply_property_sidecar_to_character(pc, conn, lasting)
    print(
        f"[cnum] reattach login {name!r} {old} -> {lasting}",
        flush=True,
    )


def _apply_property_sidecar_to_character(pc, conn, cnum):
    """Load persisted property_stash / life_log shards onto the live body."""
    from engine import hooks as hooks_mod

    try:
        rows = conn.execute(
            "SELECT shard, payload FROM character_heavy_blobs WHERE cnum=?",
            (cnum,),
        ).fetchall()
    except Exception:
        return
    saved = {}
    for shard, payload in rows:
        parsed = hooks_mod.parse_heavy_shard_payload(payload)
        if isinstance(parsed, dict):
            saved.update(parsed)
    if not saved:
        return
    try:
        hooks_mod.apply_character_blob(pc, saved)
    except Exception as exc:
        print(
            f"[cnum] reattach apply blob skipped {getattr(pc, 'key', '?')}: "
            f"{exc!r}",
            flush=True,
        )


def find_character_exact_key(game, key):
    """Exact ``Character.key`` match (case-insensitive). No substring / CNUM.

    Kit and roster lookups must use this (or ``find_kit_body``) -- never
    ``Game.find_character``, which is a player-facing fuzzy resolver.
    """
    needle = (key or "").strip().lower()
    if not needle or game is None:
        return None
    from engine.char_index import iter_characters

    for obj in iter_characters(game):
        if (getattr(obj, "key", None) or "").lower() == needle:
            return obj
    return None


def find_character_by_cnum(game, cnum):
    """Exact CNUM match. CNUM is the character identity tag, not a name."""
    from engine.char_cnum import validate_cnum
    from engine.char_index import iter_characters

    if game is None:
        return None
    try:
        want = validate_cnum(cnum)
    except ValueError:
        return None
    for obj in iter_characters(game):
        raw = getattr(obj, "cnum", None)
        if not raw:
            continue
        try:
            if validate_cnum(raw) == want:
                return obj
        except ValueError:
            continue
    return None


def persist_cnum_text(obj):
    """Validated CNUM string for dual-write columns, or None."""
    raw = getattr(obj, "cnum", None)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return validate_cnum(raw)
    except ValueError:
        return None


def resolve_held_character(game, *, holder_cnum=None, holder_key=None):
    """Inventory/gear persist lookup: CNUM first, then exact storage key.

    Never ``Game.find_character`` -- that is the fuzzy player resolver
    that kitted Daniel the god as a parlor djinn.
    """
    if holder_cnum:
        found = find_character_by_cnum(game, holder_cnum)
        if found is not None:
            return found
    if holder_key:
        return find_character_exact_key(game, holder_key)
    return None


def find_kit_body(game, key):
    """Exact storage key for NPC/mentor/roster kits.

    Returns None when missing **or** when that key is an account-linked
    login body. Callers must spawn under a new key rather than restamp
    a player.
    """
    obj = find_character_exact_key(game, key)
    if obj is None:
        return None
    if is_account_linked_body(obj):
        return None
    return obj


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


def identity_skips_surname(char) -> bool:
    """True when surnames are not part of this body's identity fiction.

    Celestial and Cosmic Origins (Angels, Gods, Reapers, Cosmic beings) are
    not mortal registry bodies -- they do not need sheriff-desk surnames for
    login disambiguation or who-list flavor.
    """
    if char is None:
        return False
    origin = getattr(char, "origin", None)
    return origin in ("celestial", "cosmic")


def surname_alert_suppressed(char) -> bool:
    """True when the player opted out of the no-surname login reminder."""
    return bool(getattr(char, "suppress_surname_alert", False))


def legacy_surname_login_notice(char) -> str | None:
    """Post-login hint when a body still has no surname on file."""
    if char is None or character_surname(char):
        return None
    if identity_skips_surname(char) or surname_alert_suppressed(char):
        return None
    return (
        "[ALERT] You have no surname on file yet. "
        "Visit a sheriff's office and type: namechange YourSurname "
        "(see help namechange). "
        "Use surname on|off to show or hide it on who. "
        "See help surname. "
        "Type config surname alert off to silence this reminder."
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
    """Set given/surname/visibility/CNUM on a brand-new Character.

    Always stores the two legal-name fields separately. ``Character.key``
    may be a GivenSurname mash when the bare first name was taken -- that
    mash must never be copied into ``given_name``.
    """
    given = (given_name or "").strip()
    sur = (surname or "").strip()
    if not given:
        raise ValueError("given_name required for stamp_new_identity")
    # Refuse to stamp a SingleWord mash as the given name when a surname
    # was provided (would recreate the ZackMarkson display bug on save).
    if sur:
        parts = split_mashed_key_identity(given)
        if parts is not None and parts[1].lower() == sur.lower():
            given = parts[0]
        elif (
            given.lower().endswith(sur.lower())
            and len(given) > len(sur)
            and " " not in given
        ):
            # given is ZackMarkson with surname Markson (no camel if all one case)
            peeled = given[: -len(sur)]
            if peeled and given.lower() == (peeled + sur).lower():
                key_face = _strip_prefix(getattr(char, "key", None) or "")
                if given.lower() == key_face.lower() or split_mashed_key_identity(
                    given
                ):
                    given = (
                        peeled[0].upper() + peeled[1:]
                        if len(peeled) > 1
                        else peeled.upper()
                    )
    char.given_name = given
    char.surname = sur
    char.surname_visible = True
    char.public_name_order = PUBLIC_NAME_ORDER_GIVEN_FIRST
    taken = collect_taken_cnums(getattr(game, "characters", None) or [])
    # Include any CNUM already on this char (should be none for brand-new).
    existing = getattr(char, "cnum", None)
    if existing:
        try:
            taken.add(validate_cnum(existing))
        except ValueError:
            pass
    char.cnum = allocate_cnum(given, taken=taken)


def heal_mashed_given_surname(char, game=None) -> bool:
    """Split legacy ``GivenSurname`` mash out of ``given_name``.

    Covers:
      0. Mononym storage key (``Pierre``) + CamelCase or spaced mash in
         ``given_name`` while ``surname`` is already on file (login roster
         vs in-game face desync -- bug report 748).
      1. ``given_name=ZackMarkson`` + ``surname=Markson`` (desk / heal peel)
      2. ``given_name=ZackMarkson`` (or empty) + empty surname when the
         storage key is a one-hump mash -- invent surname from the key so
         saves never keep a SingleWord legal name for duplicate-first-name
         bodies.

    Account-handle mononyms (``CapnKnives`` on the ``CapnKnives`` account)
    are never split -- the whole key is the public brand.

    Idempotent boot / desk / pre-save repair. Does not rename ``Character.key``.
    """
    if char is None:
        return False
    if heal_account_mononym_identity(char, game=game):
        return True
    key_face = _strip_prefix(getattr(char, "key", None) or "")
    given = character_given_name(char)
    sur = character_surname(char)
    changed = False

    # Case 0: bare storage key + mashed/spaced given while surname exists.
    if key_face and given:
        parts = split_mashed_key_identity(given)
        if parts is not None and sur:
            peeled_given, peeled_sur = parts
            if (
                peeled_given.lower() == key_face.lower()
                and sur.lower() == peeled_sur.lower()
            ):
                char.given_name = peeled_given
                return True
        if " " in given.strip():
            bits = given.strip().split(None, 1)
            if len(bits) == 2:
                g_part, s_raw = bits[0], bits[1]
                cleaned, err = normalize_surname(s_raw)
                if (
                    err is None
                    and cleaned
                    and g_part.lower() == key_face.lower()
                ):
                    char.given_name = (
                        g_part[0].upper() + g_part[1:]
                        if len(g_part) > 1
                        else g_part.upper()
                    )
                    if not sur:
                        char.surname = cleaned
                    elif sur.lower() != cleaned.lower():
                        return False
                    return True

    # Case 2 first when surname is missing: split the key (or mashed given).
    if not sur:
        split_src = None
        if given and given.lower() == key_face.lower():
            split_src = key_face or given
        elif not given or not str(getattr(char, "given_name", None) or "").strip():
            split_src = key_face
        elif split_mashed_key_identity(given) is not None and given.lower() == (
            "".join(ch for ch in given if ch.isalpha()).lower()
        ):
            # given itself is a CamelCase mash with no surname on file.
            split_src = given
        if split_src and not storage_key_matches_account_handle(
            game, char, split_src
        ):
            parts = split_mashed_key_identity(split_src)
            if parts is not None:
                char.given_name, char.surname = parts
                return True

    given = character_given_name(char)
    sur = character_surname(char)
    if not given or not sur:
        return changed
    mash = "".join(ch for ch in (given + sur) if ch.isalpha())
    if not mash:
        return changed
    if given.lower() != mash.lower() and given.lower() != sur.lower():
        if given.lower() != key_face.lower():
            return changed
    if len(given) <= len(sur) or not given.lower().endswith(sur.lower()):
        return changed
    peeled = given[: -len(sur)]
    if not peeled:
        return changed
    char.given_name = (
        peeled[0].upper() + peeled[1:] if len(peeled) > 1 else peeled.upper()
    )
    return True


def ensure_character_identity(char, game=None) -> bool:
    """Backfill given_name / surname / CNUM on a loaded body. Returns changed.

    Legacy empty given: prefer splitting a GivenSurname storage mash into
    two fields; only fall back to the whole key face when it is a single
    token (true one-name bodies). Never leave ``given_name=ZackMarkson``.
    """
    if char is None:
        return False
    changed = False
    given = getattr(char, "given_name", None)
    if not isinstance(given, str) or not given.strip():
        face = _strip_prefix(getattr(char, "key", None) or "")
        parts = split_mashed_key_identity(face) if face and face != "?" else None
        if parts is not None and not storage_key_matches_account_handle(
            game, char, face
        ):
            char.given_name, char.surname = parts
        else:
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
    if heal_mashed_given_surname(char, game=game):
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


def _cnum_collision_winner(chars):
    """Keep the login PC when two roster rows share a CNUM.

    Account-linked bodies win first. Passworded hunters with an empty
    ``account`` field still beat NPCs (Andy / AD00002). Ties break on
    storage key so the choice is stable across boots.
    """
    linked = [c for c in chars if is_account_linked_body(c)]
    if linked:
        pool = linked
    else:
        passworded = [c for c in chars if is_passworded_roster_body(c)]
        pool = passworded if passworded else list(chars)
    return sorted(
        pool,
        key=lambda c: (getattr(c, "key", None) or "").lower(),
    )[0]


def heal_unique_character_cnums(game) -> dict:
    """Make every roster CNUM valid and unique. Collisions keep the prefix.

    Account-linked bodies keep the colliding tag; the other body gets the
    next free number under that prefix (DN00001 vs DN00002). Invalid tags
    (legacy blob junk) are treated as missing.

    Idempotent. Returns ``stamped`` / ``reallocated`` / ``changed`` (the
    Character objects whose ``cnum`` moved) so persist can dirty them.
    """
    stats = {"stamped": 0, "reallocated": 0, "changed": []}
    if game is None:
        return stats
    roster = list(getattr(game, "characters", None) or [])
    groups = {}
    prefer_prefix = {}
    for char in roster:
        raw = getattr(char, "cnum", None)
        parsed = None
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = validate_cnum(raw)
            except ValueError:
                parsed = None
        if parsed is None:
            if raw not in (None, ""):
                char.cnum = None
            continue
        if parsed != str(raw).strip().upper():
            char.cnum = parsed
            stats["changed"].append(char)
        groups.setdefault(parsed, []).append(char)

    taken = set()
    for cnum, chars in groups.items():
        if len(chars) == 1:
            taken.add(cnum)
            continue
        winner = _cnum_collision_winner(chars)
        parsed = parse_cnum(cnum)
        prefix = parsed[0] if parsed else None
        taken.add(cnum)
        for char in chars:
            if char is winner:
                char.cnum = cnum
                continue
            char.cnum = None
            if prefix:
                prefer_prefix[id(char)] = prefix
            stats["changed"].append(char)

    for char in roster:
        raw = getattr(char, "cnum", None)
        parsed = None
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = validate_cnum(raw)
            except ValueError:
                parsed = None
        if parsed:
            continue
        prefix = prefer_prefix.get(id(char))
        if prefix:
            new = next_cnum(prefix, taken)
            stats["reallocated"] += 1
        else:
            new = allocate_cnum(character_given_name(char), taken=taken)
            stats["stamped"] += 1
        char.cnum = new
        taken.add(new)
        if char not in stats["changed"]:
            stats["changed"].append(char)
    return stats


def heal_all_character_identities(game) -> dict:
    """Boot heal: ensure every roster body has given/surname/unique CNUM.

    Idempotent. Returns a small stats dict for smoke / logs.
    """
    stats = {"healed": 0, "cnums_stamped": 0, "cnums_reallocated": 0}
    if game is None:
        return stats
    roster = list(getattr(game, "characters", None) or [])
    # Pass 1: given_name / surname / visibility (no CNUM yet).
    for char in roster:
        if ensure_character_identity(char, game=None):
            stats["healed"] += 1
    # Pass 2: unique CNUMs (missing, invalid, collisions).
    unique = heal_unique_character_cnums(game)
    stats["cnums_stamped"] = unique.get("stamped", 0)
    stats["cnums_reallocated"] = unique.get("reallocated", 0)
    stats["healed"] += unique.get("stamped", 0) + unique.get("reallocated", 0)
    return stats


def _sheriff_office_bare_label(label) -> str:
    """Strip instance / map prefixes from a room identity string.

    Djinn mirage clones store ``djinn:<id>:<source_key>``. Map-qualified
    keys are ``lebanon:Sheriff's Office``. One ``split(':', 1)`` leaves
    ``<id>:SE00008`` on clones, so peel the instance prefix first.
    """
    if not label or not isinstance(label, str):
        return ""
    text = label.strip()
    if not text:
        return ""
    low = text.lower()
    # ``djinn:<12-hex>:<source_key>`` -- keep the source key intact
    # (it may itself be map-qualified or a VNUM).
    if low.startswith("djinn:") and text.count(":") >= 2:
        text = text.split(":", 2)[2].strip()
    if ":" in text:
        text = text.split(":", 1)[-1].strip()
    return text


def _sheriff_office_name_match(bare) -> bool:
    """True for the lobby name itself, not a lot / cell / street near it."""
    low = str(bare or "").strip().lower()
    if not low:
        return False
    if "cell" in low or "lot" in low:
        return False
    return low in ("sheriff's office", "sherrif's office")


def _sheriff_office_identity_match(label) -> bool:
    """True when a room key or legacy_key names the sheriff desk lobby."""
    return _sheriff_office_name_match(_sheriff_office_bare_label(label))


def _sheriff_office_title_match(label) -> bool:
    """True for authored lobby titles (not lot, cell, or 'near the office').

    ``Lebanon - Sheriff's Office - Lobby`` and ``Lebanon - Sheriff's Office``
    count. ``… - Cell A``, ``… - Lot``, and ``Near the Sheriff's Office``
    do not. Used when VNUM / instance keys have no legacy_key (Djinn clones
    copy title but used to omit legacy_key).
    """
    if not label or not isinstance(label, str):
        return False
    parts = [
        part.strip()
        for part in label.replace("–", "-").split(" - ")
        if part.strip()
    ]
    if not parts:
        return False
    last = parts[-1].lower()
    if last in ("lobby",):
        room = parts[-2].lower() if len(parts) >= 2 else ""
    else:
        room = last
    return _sheriff_office_name_match(room)


def is_sheriff_office_room(room) -> bool:
    """True when ``room`` is a sheriff's office lobby (not lot / cell).

    Matches Lebanon / Lawrence SoT keys ending in ``Sheriff's Office``
    (and legacy dig typo ``Sherrif's Office``) without Cell / Lot suffixes.
    After VNUM rekey the live ``room.key`` may be ``SE00008`` while
    ``legacy_key`` stays ``lebanon:Sheriff's Office`` -- check both.
    Instance clones (``djinn:<id>:SE00008``) and title-only lobbies also
    match so desk verbs do not tell you to walk to a desk you are on.
    """
    if room is None:
        return False
    for attr in ("key", "legacy_key", "vnum"):
        if _sheriff_office_identity_match(getattr(room, attr, None)):
            return True
    for attr in ("title", "look_key"):
        label = getattr(room, attr, None)
        if _sheriff_office_title_match(label):
            return True
        if _sheriff_office_identity_match(label):
            return True
    return False


def is_justice_desk_room(room) -> bool:
    """True at courthouse clerk / registry desks (payfine parity)."""
    if room is None:
        return False
    if bool(getattr(room, "id_registry", False)):
        return True
    title = str(getattr(room, "title", "") or "").lower()
    return "clerk window" in title
