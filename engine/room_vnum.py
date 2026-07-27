"""
room_vnum.py -- Hand-room vnum helpers (letter prefix + 5 digits).

Hand-authored rooms (map/zone ``rooms[]``) get a stable human id like
``CA00001`` (first + last A–Z of the display name, both uppercase, plus a
zero-padded sequence under that prefix). Storage keys stay unchanged;
GMCP ``Room.Info.num`` uses :func:`pack_vnum` so Mudlet stock mappers get
an integer.

Grid / wilderness cells never receive a vnum (``grid_prefix`` set).
Engine-pure: no ``supers`` imports.
"""

from __future__ import annotations

import re

# Human form: two A–Z letters + exactly five decimal digits.
_VNUM_RE = re.compile(r"^([A-Z]{2})(\d{5})$")
# Strip map-qualified keys and collision suffixes from display names.
_MAP_PREFIX_RE = re.compile(r"^[^:]+:\s*")
_HASH_SUFFIX_RE = re.compile(r"\s+#\d+$")

# Fallback when the display name has no A–Z letters at all.
_FALLBACK_PREFIX = "XX"

# Per-prefix sequence ceiling (5 digits).
_MAX_SEQ = 99999


def is_hand_room(room) -> bool:
    """True when this Room was not built from a procedural grid cell.

    Same idea as ``map_store.is_grid_room`` inverted: grid cells stamp
    ``grid_prefix``; hand rooms leave it ``None``.
    """
    return getattr(room, "grid_prefix", None) is None


def bare_key_name(key: str) -> str:
    """Strip ``map_id:`` qualify and trailing `` #N`` collision suffixes.

    ``lebanon:Apartment Floor C`` → ``Apartment Floor C``;
    ``Central Plaza #2`` → ``Central Plaza``.
    """
    text = str(key or "").strip()
    if not text:
        return ""
    text = _MAP_PREFIX_RE.sub("", text, count=1).strip()
    text = _HASH_SUFFIX_RE.sub("", text).strip()
    return text


def display_name_for_vnum(key: str, title=None) -> str:
    """Player-facing name used to derive the letter prefix.

    Authored ``title`` wins when non-empty; otherwise the bare storage key.
    """
    if title is not None:
        cleaned = str(title).strip()
        if cleaned:
            return cleaned
    return bare_key_name(key)


def letter_prefix(name: str) -> str:
    """First and last A–Z letters of ``name``, both uppercase.

    Single letter → that letter twice. No letters → ``XX``.
    """
    letters = [ch.upper() for ch in str(name or "") if ch.isalpha() and ch.isascii()]
    # Keep only A–Z (isalpha alone would allow accented letters).
    letters = [ch for ch in letters if "A" <= ch <= "Z"]
    if not letters:
        return _FALLBACK_PREFIX
    if len(letters) == 1:
        return letters[0] + letters[0]
    return letters[0] + letters[-1]


def format_vnum(prefix: str, n: int) -> str:
    """Build ``CA00001`` from a two-letter prefix and sequence number."""
    pref = str(prefix or "").strip().upper()
    if len(pref) != 2 or not pref.isalpha() or not pref.isascii():
        raise ValueError(f"vnum prefix must be two A–Z letters, got {prefix!r}")
    if not isinstance(n, int) or n < 1 or n > _MAX_SEQ:
        raise ValueError(f"vnum sequence must be 1..{_MAX_SEQ}, got {n!r}")
    return f"{pref}{n:05d}"


def parse_vnum(s) -> tuple[str, int] | None:
    """Return ``(prefix, n)`` for a valid vnum string, else ``None``."""
    if s is None:
        return None
    text = str(s).strip().upper()
    match = _VNUM_RE.fullmatch(text)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def validate_vnum(s) -> str:
    """Normalize and validate; raise ``ValueError`` if malformed."""
    parsed = parse_vnum(s)
    if parsed is None:
        raise ValueError(
            f"invalid room vnum {s!r} -- expected two A–Z letters + 5 digits "
            f"(e.g. CA00001)"
        )
    prefix, n = parsed
    return format_vnum(prefix, n)


def pack_vnum(s) -> int:
    """Encode ``CA00001`` → unique positive int for GMCP ``Room.Info.num``.

    Formula::

        num = ((ord(A)-65)*26 + (ord(B)-65)) * 100000 + int(digits)

    Example: ``CA00001`` → ``5200001``. Reversible via :func:`unpack_vnum`.
    """
    text = validate_vnum(s)
    prefix, n = parse_vnum(text)
    assert prefix is not None
    a = ord(prefix[0]) - 65
    b = ord(prefix[1]) - 65
    return (a * 26 + b) * 100000 + n


def unpack_vnum(i: int) -> str:
    """Inverse of :func:`pack_vnum` — integer → ``CA00001``."""
    if not isinstance(i, int) or i < 1:
        raise ValueError(f"packed vnum must be a positive int, got {i!r}")
    digits = i % 100000
    if digits < 1:
        raise ValueError(f"packed vnum has invalid digit part: {i!r}")
    code = i // 100000
    if code < 0 or code > 26 * 26 - 1:
        raise ValueError(f"packed vnum has invalid letter code: {i!r}")
    a, b = divmod(code, 26)
    prefix = chr(65 + a) + chr(65 + b)
    return format_vnum(prefix, digits)


def next_vnum(prefix: str, taken: set[str]) -> str:
    """Allocate the next free ``PREFIX#####`` under ``prefix``.

    ``taken`` holds already-used vnum strings (any casing; compared upper).
    Raises ``ValueError`` if the 5-digit space is exhausted.
    """
    pref = str(prefix or "").strip().upper()
    if len(pref) != 2 or not all("A" <= ch <= "Z" for ch in pref):
        raise ValueError(f"vnum prefix must be two A–Z letters, got {prefix!r}")
    used = {str(v).strip().upper() for v in (taken or ()) if v}
    for n in range(1, _MAX_SEQ + 1):
        candidate = format_vnum(pref, n)
        if candidate not in used:
            return candidate
    raise ValueError(
        f"no free vnum left under prefix {pref!r} (1..{_MAX_SEQ} exhausted)"
    )


def collect_taken_vnums(rooms_or_dicts) -> set[str]:
    """Gather validated vnum strings from Room objects or room dicts."""
    taken: set[str] = set()
    for item in rooms_or_dicts or ():
        raw = None
        if isinstance(item, dict):
            raw = item.get("vnum")
        else:
            raw = getattr(item, "vnum", None)
        if raw is None or str(raw).strip() == "":
            continue
        taken.add(validate_vnum(raw))
    return taken


def allocate_vnum_for_name(key: str, title=None, *, taken: set[str]) -> str:
    """Derive prefix from display name and return the next free vnum."""
    name = display_name_for_vnum(key, title)
    prefix = letter_prefix(name)
    return next_vnum(prefix, taken)


def room_name(room) -> str:
    """Official **ROOM NAME** -- what everyone sees for a place.

    Alias of :func:`player_room_name`. Prefer this name in new code and
    docs (see ``docs/plans/room_vnum_identity_migration.md``).
    """
    return player_room_name(room)


def player_room_name(room) -> str:
    """ROOM NAME for look / who / walk / player prose.

    Uses ``look_title()`` when present (authored title / mirage look_key /
    display fallback). Never invents ``Name[VNUM]`` chrome.
    """
    if room is None:
        return ""
    if hasattr(room, "look_title"):
        return room.look_title() or ""
    return getattr(room, "key", "") or ""


def staff_room_label(room) -> str:
    """GM / mapper prose: ``ROOM NAME[VNUM]`` when a hand-room vnum exists.

    Players must never see this form -- use :func:`room_name` for ordinary
    look. Dig tools that still need the graph id use
    :func:`internal_room_key` (compat until Phase 3).
    """
    name = room_name(room) or (getattr(room, "key", "") if room else "")
    if room is None:
        return name
    raw = getattr(room, "vnum", None)
    if raw is None or str(raw).strip() == "":
        return name
    try:
        code = validate_vnum(raw)
    except ValueError:
        code = str(raw).strip()
    return f"{name}[{code}]"


def describe_room(room, *, staff: bool = False) -> str:
    """Prose place label: ROOM NAME, or NAME[VNUM] when ``staff``.

    Never returns a bare internal dig key when a ROOM NAME or VNUM exists.
    Empty / missing room → ``\"?\"``. Prefer this over ``location.key`` in
    any player or GM message. Opaque Cadence dig keys
    (``unowned amenity12``, ``unowned shop4``, …) are never returned --
    ``look_title`` / flag generics win first.
    """
    if room is None:
        return "?"
    if staff:
        label = staff_room_label(room)
    else:
        label = room_name(room)
    # Defense in depth: if a caller stamped an opaque dig key as title,
    # scrub it rather than teach players storage ids.
    from engine.room_naming import (
        generic_title_from_flags,
        is_opaque_storage_key,
    )
    if label and is_opaque_storage_key(label):
        label = generic_title_from_flags(room) or ""
    if label and not is_opaque_storage_key(label):
        return label
    # Authored title missing and look_title fell through empty -- last
    # resort for staff is the raw vnum string (never teach dig keys).
    raw = getattr(room, "vnum", None)
    if raw is not None and str(raw).strip():
        try:
            return validate_vnum(raw)
        except ValueError:
            return str(raw).strip()
    # Still opaque / empty -- invent Civic Building / Shop from flags.
    generic = generic_title_from_flags(room)
    if generic:
        return generic
    return "?"


def describe_room_key(game, key, *, staff: bool = False, fallback="somewhere"):
    """Player/GM place label from a *stored* room key string.

    Case tips, haunt boards, contracts, and hunt summaries often hold
    ``scene_room_key`` / ``home_room_key`` as graph ids. Call this before
    interpolating into player prose so ``unowned amenity12`` becomes
    ``Lebanon - Town Park`` (authored title) or a flag-based generic --
    never the dig key.

    Missing room + opaque key → ``fallback`` (default ``somewhere``).
    Missing room + ordinary key → bare unscoped key (or ``fallback``).
    """
    text = str(key or "").strip()
    if not text:
        return fallback
    rooms = getattr(game, "rooms", None) if game is not None else None
    room = None
    if isinstance(rooms, dict):
        room = rooms.get(text)
    if room is not None:
        label = describe_room(room, staff=staff)
        if label and label != "?":
            return label
    from engine.room_naming import bare_key, is_opaque_storage_key
    # Never echo dig keys when the room is missing from the live graph.
    if is_opaque_storage_key(text):
        return fallback
    bare = bare_key(text)
    return bare or fallback


def describe_actor_room(actor, *, staff: bool = False) -> str:
    """``describe_room`` for ``actor.location`` (or ``\"?\"`` if unplaced)."""
    return describe_room(getattr(actor, "location", None), staff=staff)


def internal_room_key(room) -> str:
    """Graph / persistence id for a room (Phase 1 = ``room.key``).

    Phase 3 flips this to return the VNUM string and rekeys
    ``game.rooms``. Call sites that *store* exit targets or ``home_room``
    should go through this helper so the flip is localized. Never show
    this string to players; staff dig tips may show it until Phase 3.
    """
    if room is None:
        return ""
    return getattr(room, "key", "") or ""


def find_room_by_vnum(game, vnum_text):
    """Return the Room whose ``vnum`` matches ``vnum_text``, or None.

    Primary lookup hook for Phase 3. Case-insensitive; validates shape
    when possible.
    """
    if game is None or not vnum_text:
        return None
    raw = str(vnum_text).strip()
    if not raw:
        return None
    try:
        want = validate_vnum(raw)
    except ValueError:
        want = raw.upper()
    rooms = getattr(game, "rooms", None) or {}
    for room in rooms.values():
        got = getattr(room, "vnum", None)
        if not got:
            continue
        try:
            if validate_vnum(got) == want:
                return room
        except ValueError:
            if str(got).strip().upper() == want:
                return room
    return None


def resolve_room(game, query, *, allow_internal_key=True):
    """Resolve a staff/query string to a Room (Phase 1 identity bridge).

    Order:
      1. Exact VNUM (``CA00001``).
      2. Exact ROOM NAME (case-insensitive ``look_title`` / title).
      3. Exact internal key (``room.key``) -- silent compat until Phase 3;
         disable with ``allow_internal_key=False`` to rehearse the cutover.

    Returns ``(room_or_None, how)`` where ``how`` is
    ``\"vnum\"`` / ``\"room_name\"`` / ``\"internal_key\"`` /
    ``\"ambiguous_name\"`` / ``None``. Ambiguous ROOM NAME returns
    ``(None, \"ambiguous_name\")`` -- use :func:`resolve_room_or_error`
    for a staff-facing tip that lists VNUMs.
    """
    if game is None:
        return None, None
    text = (query or "").strip()
    if not text:
        return None, None
    by_vnum = find_room_by_vnum(game, text)
    if by_vnum is not None:
        return by_vnum, "vnum"
    rooms = getattr(game, "rooms", None) or {}
    lowered = text.lower()
    name_hits = []
    for room in rooms.values():
        if room_name(room).lower() == lowered:
            name_hits.append(room)
    if len(name_hits) == 1:
        return name_hits[0], "room_name"
    if len(name_hits) > 1:
        # Shared ROOM NAMES must use VNUM -- do not fall through to a
        # coincidental internal-key match and silently pick the wrong room.
        return None, "ambiguous_name"
    if allow_internal_key:
        # Direct dict hit (qualified keys, legacy dig targets).
        hit = rooms.get(text)
        if hit is not None:
            return hit, "internal_key"
        for room in rooms.values():
            if (getattr(room, "key", "") or "").lower() == lowered:
                return room, "internal_key"
    return None, None


def _ambiguous_name_tip(game, query) -> str:
    """Staff tip listing VNUMs for every room that shares ``query`` as name."""
    text = (query or "").strip()
    lowered = text.lower()
    rooms = getattr(game, "rooms", None) or {}
    hits = [
        room for room in rooms.values()
        if room_name(room).lower() == lowered
    ]
    labels = []
    for room in hits[:12]:
        label = staff_room_label(room)
        if label:
            labels.append(label)
    more = ""
    if len(hits) > 12:
        more = f" (+{len(hits) - 12} more)"
    listed = ", ".join(labels) if labels else "(none)"
    return (
        f"Several rooms share ROOM NAME {text!r}. "
        f"Use a unique VNUM: {listed}{more}."
    )


def resolve_room_or_error(game, query, *, allow_internal_key=True):
    """Resolve for staff verbs -- returns ``(room, None)`` or ``(None, err)``.

    Staff addressing is **ROOM NAME** and **VNUM** (internal key remains
    silent compat when ``allow_internal_key`` is True). Prefer this over
    bare :func:`resolve_room` when the caller sends a tip to a GM.
    """
    text = (query or "").strip()
    if not text:
        return None, "Name a room by VNUM (e.g. MT00002) or unique ROOM NAME."
    room, how = resolve_room(
        game, text, allow_internal_key=allow_internal_key,
    )
    if room is not None:
        return room, None
    if how == "ambiguous_name":
        return None, _ambiguous_name_tip(game, text)
    return None, (
        f"No room matching VNUM or ROOM NAME {text!r}. "
        "Try `gm where room <text>` or `gm goto <vnum>`."
    )
