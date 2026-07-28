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
    """Graph / persistence id for a room.

    Phase 3: hand rooms with a VNUM use the VNUM string; grid cells and
    unstamped rooms keep ``room.key``. Call sites that *store* exit
    targets or ``home_room`` should go through this helper. Never show
    this string to players as the address -- use :func:`staff_room_label`.
    """
    if room is None:
        return ""
    # Hand rooms: VNUM is identity when stamped.
    if is_hand_room(room):
        raw = getattr(room, "vnum", None)
        if raw is not None and str(raw).strip():
            try:
                return validate_vnum(raw)
            except ValueError:
                return str(raw).strip()
    return getattr(room, "key", "") or ""


def ensure_title_before_rekey(room) -> None:
    """Stamp authored ``title`` from the pre-rekey ROOM NAME if missing.

    After Phase 3, ``room.key`` becomes the VNUM -- look_title must not
    fall through to ``CA00001``. Opaque dig keys get a flag-based generic
    stamped into title before the key flips.
    """
    if room is None:
        return
    from engine.room_naming import (
        authored_title_is_usable,
        bare_key,
        generic_title_from_flags,
        is_opaque_storage_key,
    )
    title = getattr(room, "title", None)
    key = getattr(room, "key", "") or ""
    if authored_title_is_usable(title, key):
        return
    if is_opaque_storage_key(key):
        generic = generic_title_from_flags(room)
        if generic:
            room.title = generic
        return
    face = bare_key(key) if key else ""
    if face:
        room.title = face


def rekey_hand_rooms_to_vnum(rooms: dict) -> tuple[dict, dict]:
    """Rebuild a rooms dict so hand rooms are keyed by VNUM.

    Returns ``(new_rooms, aliases)`` where ``aliases`` maps every former
    storage key (and bare unscoped forms) to the VNUM identity key.
    Grid cells stay under their coordinate keys. Rooms without a vnum
    stay under their existing key (ephemeral / unstamped).

    Live ``room.exits`` already hold Room object refs -- only the dict
    keys and ``room.key`` change. Call after exits are linked.
    """
    if not isinstance(rooms, dict):
        return {}, {}
    new_rooms = {}
    aliases = {}
    for old_key, room in list(rooms.items()):
        if room is None:
            continue
        if not is_hand_room(room):
            new_rooms[old_key] = room
            continue
        raw = getattr(room, "vnum", None)
        if not raw or not str(raw).strip():
            new_rooms[old_key] = room
            continue
        try:
            vnum = validate_vnum(raw)
        except ValueError:
            new_rooms[old_key] = room
            continue
        ensure_title_before_rekey(room)
        # Remember the dig / JSON key for dual-read boot heal.
        leg = getattr(room, "legacy_key", None)
        if not leg:
            room.legacy_key = old_key
            leg = old_key
        room.key = vnum
        room.vnum = vnum
        if vnum in new_rooms and new_rooms[vnum] is not room:
            raise ValueError(
                f"Phase 3 rekey: duplicate VNUM {vnum!r} "
                f"({getattr(new_rooms[vnum], 'legacy_key', None)!r} vs "
                f"{old_key!r})"
            )
        new_rooms[vnum] = room
        aliases[old_key] = vnum
        if leg and str(leg) != old_key:
            aliases[str(leg)] = vnum
        # Also alias bare unscoped form of qualified keys.
        for candidate in (old_key, leg):
            if not candidate:
                continue
            bare = bare_key_name(str(candidate))
            if bare and bare != str(candidate) and bare not in aliases:
                aliases[bare] = vnum
    return new_rooms, aliases


def lookup_room(game, key):
    """Find a Room by VNUM identity key or legacy storage key.

    Prefers ``game.rooms`` (Phase 3 identity), then ``game.room_aliases``.
    """
    if game is None or not key:
        return None
    text = str(key).strip()
    if not text:
        return None
    rooms = getattr(game, "rooms", None)
    if isinstance(rooms, dict):
        hit = rooms.get(text)
        if hit is not None:
            return hit
    aliases = getattr(game, "room_aliases", None) or {}
    mapped = aliases.get(text)
    if mapped and isinstance(rooms, dict):
        hit = rooms.get(mapped)
        if hit is not None:
            return hit
    # Case-insensitive alias / key scan (legacy tips).
    lowered = text.lower()
    for aka, vnum in aliases.items():
        if str(aka).lower() == lowered and isinstance(rooms, dict):
            hit = rooms.get(vnum)
            if hit is not None:
                return hit
    if isinstance(rooms, dict):
        for room in rooms.values():
            if (getattr(room, "key", "") or "").lower() == lowered:
                return room
            leg = getattr(room, "legacy_key", None)
            if leg and str(leg).lower() == lowered:
                return room
    return None


def _hand_room_canonical_score(room) -> int:
    """Higher = prefer as the surviving hub when ROOM NAME collides."""
    if room is None or not is_hand_room(room):
        return -1
    raw = getattr(room, "vnum", None)
    if not raw or not str(raw).strip():
        return 0
    try:
        vnum = validate_vnum(raw)
    except ValueError:
        return 0
    key = getattr(room, "key", "") or ""
    if key == vnum:
        return 2
    return 1


def _rewire_room_graph_pointers(game, stale, canonical, stats):
    """Point exits / zone entries at ``canonical`` instead of ``stale``."""
    rooms = getattr(game, "rooms", None) or {}
    for room in rooms.values():
        if room is None:
            continue
        exits = getattr(room, "exits", None) or {}
        for direction, dest in list(exits.items()):
            if dest is stale:
                exits[direction] = canonical
                stats["rewired_exits"] = stats.get("rewired_exits", 0) + 1
        zone_to = getattr(room, "zone_exit_to", None)
        if zone_to is stale:
            room.zone_exit_to = canonical
            stats["rewired_exits"] = stats.get("rewired_exits", 0) + 1
        entries = getattr(room, "zone_entries", None) or {}
        for alias, hub in list(entries.items()):
            if hub is stale:
                entries[alias] = canonical
                stats["rewired_exits"] = stats.get("rewired_exits", 0) + 1


def heal_duplicate_hand_room_titles(game) -> dict:
    """Boot heal: collapse stale dig-key hand rooms that duplicate a VNUM hub.

    Live map-backup merge can leave both ``The Waystation`` (legacy dig key)
    and ``TN00001`` (VNUM identity) with the same ROOM NAME. Staff ``goto``
    then lists two matches -- one with VNUM chrome, one without.
    """
    stats = {"removed": 0, "moved": 0, "rewired_exits": 0}
    if game is None:
        return stats
    rooms = getattr(game, "rooms", None)
    if not isinstance(rooms, dict):
        return stats

    by_name = {}
    for room in rooms.values():
        if room is None or not is_hand_room(room):
            continue
        name = (room_name(room) or "").strip().lower()
        if not name:
            continue
        by_name.setdefault(name, []).append(room)

    aliases = getattr(game, "room_aliases", None)
    if aliases is None:
        game.room_aliases = {}
        aliases = game.room_aliases

    for group in by_name.values():
        if len(group) < 2:
            continue
        scored = [(r, _hand_room_canonical_score(r)) for r in group]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        canonical, best = scored[0]
        if best <= 0:
            continue
        canon_key = internal_room_key(canonical)
        if not canon_key:
            continue
        for stale, score in scored[1:]:
            if stale is canonical:
                continue
            for obj in list(getattr(stale, "contents", ()) or ()):
                if hasattr(obj, "move_to"):
                    obj.move_to(canonical)
                    stats["moved"] += 1
            _rewire_room_graph_pointers(game, stale, canonical, stats)
            stale_key = getattr(stale, "key", None)
            for dk, dr in list(rooms.items()):
                if dr is stale:
                    rooms.pop(dk, None)
            if stale_key:
                aliases[stale_key] = canon_key
            leg = getattr(stale, "legacy_key", None)
            if leg:
                aliases[str(leg)] = canon_key
            title = room_name(stale)
            if title:
                aliases[title] = canon_key
            stats["removed"] += 1

    return stats


def hub_room(game, name, vnum=None):
    """Resolve a hand-room hub by ROOM NAME and/or VNUM identity.

    Uses ``game.rooms`` alias resolution first, then :func:`lookup_room`.
    Player/staff strings like ``The Waystation`` work after VNUM rekey.
    """
    if game is None:
        return None
    rooms = getattr(game, "rooms", None)
    if isinstance(rooms, dict):
        if name:
            hit = rooms.get(name)
            if hit is not None:
                return hit
        if vnum:
            hit = rooms.get(vnum)
            if hit is not None:
                return hit
    if name:
        room, _ = resolve_room(game, name)
        if room is not None:
            return room
    if vnum:
        room, _ = resolve_room(game, vnum)
        return room
    return None


def heal_hand_room_title_aliases(game) -> int:
    """Boot heal: register unique ROOM NAME strings in ``room_aliases``.

    After VNUM rekey, ``rooms.get('The Waystation')`` only works when the
    display title is aliased to the identity VNUM. Idempotent.
    """
    if game is None:
        return 0
    aliases = getattr(game, "room_aliases", None)
    if aliases is None:
        game.room_aliases = {}
        aliases = game.room_aliases
    rooms = getattr(game, "rooms", None) or {}
    title_counts = {}
    for room in rooms.values():
        if room is None or not is_hand_room(room):
            continue
        name = (room_name(room) or "").strip()
        if name:
            key = name.lower()
            title_counts[key] = title_counts.get(key, 0) + 1
    added = 0
    for room in rooms.values():
        if room is None or not is_hand_room(room):
            continue
        raw = getattr(room, "vnum", None)
        if not raw or not str(raw).strip():
            continue
        try:
            vnum = validate_vnum(raw)
        except ValueError:
            continue
        name = (room_name(room) or "").strip()
        if not name or title_counts.get(name.lower(), 0) != 1:
            continue
        if aliases.get(name) != vnum:
            aliases[name] = vnum
            added += 1
    return added


def boot_duplicate_hand_room_titles(game, *, exclude_vehicles=True):
    """List ambiguous hand-room ROOM NAMES at boot (for smoke / audit).

    Returns ``[(title, count), ...]`` sorted by count descending. Skips
    vehicle interior titles (``Inside the …``) when ``exclude_vehicles``.
    """
    if game is None:
        return []
    by_name = {}
    for room in (getattr(game, "rooms", None) or {}).values():
        if room is None or not is_hand_room(room):
            continue
        name = (room_name(room) or "").strip()
        if not name:
            continue
        if exclude_vehicles and name.lower().startswith("inside "):
            continue
        by_name.setdefault(name.lower(), []).append(room)
    hits = [
        (rooms[0].look_title() if hasattr(rooms[0], "look_title") else name, len(rooms))
        for name, rooms in by_name.items()
        if len(rooms) > 1
    ]
    hits.sort(key=lambda pair: (-pair[1], pair[0].lower()))
    return hits


def heal_character_room_keys(game) -> dict:
    """Boot heal: remap legacy ``room_key`` / home blob fields via aliases.

    Idempotent. Returns a small stats dict for logs / smoke.
    """
    stats = {"room_key": 0, "home": 0, "workplace": 0, "other": 0}
    if game is None:
        return stats
    aliases = getattr(game, "room_aliases", None) or {}
    if not aliases:
        return stats

    def _remap(value):
        if not value:
            return value, False
        text = str(value).strip()
        if text in aliases:
            return aliases[text], True
        # Already a VNUM identity key.
        if isinstance(getattr(game, "rooms", None), dict) and text in game.rooms:
            return text, False
        return text, False

    blob_fields = (
        ("home_room_key", "home"),
        ("workplace_room_key", "workplace"),
        ("haunt_room_key", "other"),
        ("chapel_room_key", "other"),
        ("exile_room_key", "other"),
        ("body_room_key", "other"),
        ("gm_spirit_room_key", "other"),
        ("orbit_return_room", "other"),
        ("stellar_flight_return_room", "other"),
        ("vault_return_room", "other"),
    )
    for char in list(getattr(game, "characters", None) or []):
        loc = getattr(char, "location", None)
        if loc is not None:
            # Location already a Room object -- ensure key is identity.
            want = internal_room_key(loc)
            if want and getattr(loc, "key", None) != want:
                loc.key = want
        for field, bucket in blob_fields:
            raw = getattr(char, field, None)
            new, changed = _remap(raw)
            if changed:
                setattr(char, field, new)
                stats[bucket] = stats.get(bucket, 0) + 1
        # protected_rooms list
        prot = getattr(char, "protected_rooms", None)
        if isinstance(prot, list) and prot:
            rebuilt = []
            changed_p = False
            for item in prot:
                new, ch = _remap(item)
                rebuilt.append(new)
                changed_p = changed_p or ch
            if changed_p:
                char.protected_rooms = rebuilt
                stats["other"] += 1
        # known_exits dict keys
        known = getattr(char, "known_exits", None)
        if isinstance(known, dict) and known:
            rebuilt = {}
            changed_k = False
            for rk, dirs in known.items():
                new, ch = _remap(rk)
                rebuilt[new] = dirs
                changed_k = changed_k or ch
            if changed_k:
                char.known_exits = rebuilt
                stats["other"] += 1
    return stats


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


def room_matches_needle(room, needle, *, match_key=False) -> bool:
    """True when *needle* is a substring of ROOM NAME and/or VNUM.

    Staff ``where`` / partial ``goto`` share this matcher. Optional
    ``match_key`` keeps silent legacy dig-key substring matching for
    ``where`` discovery until Phase 3 teardown (never teach dig keys).
    """
    if room is None or not needle:
        return False
    want = str(needle).strip().lower()
    if not want:
        return False
    title = (room_name(room) or "").lower()
    if want in title:
        return True
    raw_v = getattr(room, "vnum", None)
    if raw_v is not None and str(raw_v).strip():
        try:
            code = validate_vnum(raw_v).lower()
        except ValueError:
            code = str(raw_v).strip().lower()
        if want in code:
            return True
    if match_key:
        key = (getattr(room, "key", "") or "").lower()
        if want in key:
            return True
    return False


def iter_rooms_matching(game, needle, *, match_key=False):
    """Every live room matching *needle* (ROOM NAME / VNUM substring).

    Sorted by ROOM NAME then storage key for stable staff lists.
    """
    want = (needle or "").strip()
    if not want or game is None:
        return []
    rooms = getattr(game, "rooms", None) or {}
    hits = [
        room for room in rooms.values()
        if room_matches_needle(room, want, match_key=match_key)
    ]

    def _sort_key(room):
        return (
            (room_name(room) or "").lower(),
            (getattr(room, "key", "") or "").lower(),
        )

    hits.sort(key=_sort_key)
    return hits


def resolve_room(game, query, *, allow_internal_key=True):
    """Resolve a staff/query string to a Room (Phase 1–2 identity bridge).

    Order:
      1. Exact VNUM (``CA00001``).
      2. Exact ROOM NAME (case-insensitive ``look_title`` / title).
      3. Unique partial ROOM NAME / VNUM substring (case-insensitive).
      4. Exact internal key (``room.key``) -- silent compat until Phase 3;
         disable with ``allow_internal_key=False`` to rehearse the cutover.

    Returns ``(room_or_None, how)`` where ``how`` is
    ``\"vnum\"`` / ``\"room_name\"`` / ``\"partial_name\"`` /
    ``\"internal_key\"`` / ``\"ambiguous_name\"`` / ``None``.
    Ambiguous exact or partial ROOM NAME returns
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
    # Unique partial ROOM NAME / VNUM substring (goto Town Park, etc.).
    partial_hits = iter_rooms_matching(game, text, match_key=False)
    if len(partial_hits) == 1:
        return partial_hits[0], "partial_name"
    if len(partial_hits) > 1:
        return None, "ambiguous_name"
    if allow_internal_key:
        # Direct dict hit (qualified keys, legacy dig targets, RoomMap aliases).
        hit = rooms.get(text)
        if hit is not None:
            return hit, "internal_key"
        for room in rooms.values():
            if (getattr(room, "key", "") or "").lower() == lowered:
                return room, "internal_key"
            leg = getattr(room, "legacy_key", None)
            if leg and str(leg).lower() == lowered:
                return room, "internal_key"
        # Game-level alias table (Phase 3 dual-read).
        aliases = getattr(game, "room_aliases", None) or {}
        mapped = aliases.get(text)
        if mapped and mapped in rooms:
            return rooms[mapped], "internal_key"
        for aka, vnum in aliases.items():
            if str(aka).lower() == lowered and vnum in rooms:
                return rooms[vnum], "internal_key"
    return None, None


def _ambiguous_name_tip(game, query) -> str:
    """Staff tip listing VNUMs for rooms matching *query* (exact or partial)."""
    text = (query or "").strip()
    lowered = text.lower()
    rooms = getattr(game, "rooms", None) or {}
    exact = [
        room for room in rooms.values()
        if room_name(room).lower() == lowered
    ]
    hits = exact if exact else iter_rooms_matching(game, text, match_key=False)
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
        f"Several rooms match {text!r}. "
        f"Use a unique VNUM or fuller ROOM NAME: {listed}{more}."
    )


def resolve_room_or_error(game, query, *, allow_internal_key=True):
    """Resolve for staff verbs -- returns ``(room, None)`` or ``(None, err)``.

    Staff addressing is **ROOM NAME** and **VNUM** (partial name OK when
    unique). Internal key remains silent compat when
    ``allow_internal_key`` is True. Prefer this over bare
    :func:`resolve_room` when the caller sends a tip to a GM.
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
