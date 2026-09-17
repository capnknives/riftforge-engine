"""
room_vnum.py -- Hand-room vnum helpers (letter prefix + 5 digits).

Hand-authored rooms (map/zone ``rooms[]``) get a stable human id like
``CA00001`` (first + last A–Z of the display name, both uppercase, plus a
zero-padded sequence under that prefix). New missing VNUMs use
:func:`scramble_vnum` (hash-seeded neighborhood band) instead of always
``00001``; :func:`next_vnum` remains the lowest-free fallback. After
Phase 3 the JSON ``key`` / ``game.rooms`` identity **is** that VNUM;
leftover dig names live on ``legacy_key``. GMCP ``Room.Info.num`` uses
:func:`pack_vnum` so Mudlet stock mappers get an integer.

Grid / wilderness cells never receive a vnum (``grid_prefix`` set).
Engine-pure: no ``supers`` imports.
"""

from __future__ import annotations

import hashlib
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

# Scramble band for first-of-prefix picks (house-number analog).
_SCRAMBLE_BAND_MIN = 10000
_SCRAMBLE_BAND_MAX = 89999
# Child rooms step off an anchor hub like porch numbers off a street hub.
_CHILD_OFFSET_STEP = 2


def is_hand_room(room) -> bool:
    """True when this Room was not built from a procedural grid cell.

    Same idea as ``map_store.is_grid_room`` inverted: grid cells stamp
    ``grid_prefix``; hand rooms leave it ``None``.
    """
    return getattr(room, "grid_prefix", None) is None


def hand_room_wants_vnum(room) -> bool:
    """True when a hand room should receive a persistent VNUM identity.

    Ephemeral procedural pockets (Pit floors, God demesne grids, etc.)
    stay key-only until they are torn down -- stamping them would leak
    mapper ids and collapse parallel instances that share a template
    title (see ``heal_duplicate_hand_room_titles``).
    """
    if not is_hand_room(room):
        return False
    if getattr(room, "purgatory_pit", False):
        return False
    if getattr(room, "pit_run_tag", None):
        return False
    # God demesne micro/hub rooms: per-owner procedural pocket (pit parallel).
    if getattr(room, "demesne_id", None):
        return False
    return True


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


def _stable_hash_int(text: str) -> int:
    """Deterministic 32-bit int from ``text`` (stable across runs/machines)."""
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big")


def _normalize_taken(taken) -> set[str]:
    """Upper-case set of claimed vnum strings."""
    return {str(v).strip().upper() for v in (taken or ()) if v}


def _validate_prefix(prefix: str) -> str:
    """Return normalized two-letter A–Z prefix or raise."""
    pref = str(prefix or "").strip().upper()
    if len(pref) != 2 or not all("A" <= ch <= "Z" for ch in pref):
        raise ValueError(f"vnum prefix must be two A–Z letters, got {prefix!r}")
    return pref


def _child_seq_candidates(anchor_n: int, *, go_high: bool):
    """Yield free sequence slots stepped by 2 off an anchor hub."""
    if go_high:
        for i in range(1, _MAX_SEQ):
            n = anchor_n + _CHILD_OFFSET_STEP * i
            if n > _MAX_SEQ:
                break
            yield n
    else:
        for i in range(1, _MAX_SEQ):
            n = anchor_n - _CHILD_OFFSET_STEP * i
            if n < 1:
                break
            yield n


def scramble_vnum(
    prefix: str,
    taken: set[str],
    *,
    anchor: str | None = None,
    seed: str | None = None,
    sequential: bool = False,
) -> str:
    """Pick a collision-free ``PREFIX#####`` in a neighborhood band.

  Default (not ``sequential``):

  - **No anchor:** hash-seeded slot in ``10000``–``89999``, walking forward
    on collision (deterministic for a given ``seed``).
  - **Anchor hub:** child offsets ``+2`` / ``-2`` from the anchor digits
    when the anchor shares the same letter prefix (populate porch analog).

  Falls back to :func:`next_vnum` when the band is exhausted.
  ``sequential=True`` skips scramble and uses lowest-free digits only.
    """
    if sequential:
        return next_vnum(prefix, taken)

    pref = _validate_prefix(prefix)
    used = _normalize_taken(taken)

    parsed_anchor = parse_vnum(anchor) if anchor else None
    if parsed_anchor is not None:
        anchor_pref, anchor_n = parsed_anchor
        if anchor_pref == pref:
            seed_text = str(seed or anchor or pref)
            go_high = (_stable_hash_int(seed_text) % 2) == 0
            for high_first in (go_high, not go_high):
                for n in _child_seq_candidates(anchor_n, go_high=high_first):
                    candidate = format_vnum(pref, n)
                    if candidate not in used:
                        return candidate

    seed_text = str(seed if seed is not None else pref)
    span = _SCRAMBLE_BAND_MAX - _SCRAMBLE_BAND_MIN + 1
    start = _SCRAMBLE_BAND_MIN + (_stable_hash_int(seed_text) % span)
    for offset in range(span):
        n = _SCRAMBLE_BAND_MIN + ((start - _SCRAMBLE_BAND_MIN + offset) % span)
        candidate = format_vnum(pref, n)
        if candidate not in used:
            return candidate

    return next_vnum(pref, used)


def next_vnum(prefix: str, taken: set[str]) -> str:
    """Allocate the lowest free ``PREFIX#####`` under ``prefix``.

    Lowest-free fallback for scramble exhaustion, tests, and
    ``--sequential`` tooling. ``taken`` holds already-used vnum strings
    (any casing; compared upper). Raises ``ValueError`` if exhausted.
    """
    pref = _validate_prefix(prefix)
    used = _normalize_taken(taken)
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


def allocate_vnum_for_name(
    key: str,
    title=None,
    *,
    taken: set[str],
    anchor: str | None = None,
    seed: str | None = None,
    sequential: bool = False,
) -> str:
    """Derive prefix from display name and return a scrambled free vnum.

    ``seed`` stabilizes hash picks (batch tools: ``filename:key``; runtime:
    ``key:title``). ``anchor`` clusters child rooms off a parent hub vnum.
    ``sequential=True`` uses lowest-free digits only (legacy / diffs).
    """
    name = display_name_for_vnum(key, title)
    prefix = letter_prefix(name)
    if seed is None:
        seed = f"{key}:{name}"
    return scramble_vnum(
        prefix,
        taken,
        anchor=anchor,
        seed=seed,
        sequential=sequential,
    )


def stamp_hand_room(
    game,
    room,
    *,
    taken: set[str] | None = None,
    anchor: str | None = None,
) -> str:
    """Allocate a VNUM when missing and register the room under it.

    Mutates ``room.key``, ``room.vnum``, ``room.legacy_key`` (when the
    pre-stamp key differed), ``game.rooms``, and ``game.room_aliases``.
    Idempotent when the room is already keyed by its VNUM. Grid cells and
    ephemeral Pit rooms are left unchanged.

    Returns the canonical identity string (VNUM for stamped hand rooms,
    else ``room.key``).
    """
    if room is None or not hand_room_wants_vnum(room):
        return getattr(room, "key", "") or ""

    rooms = getattr(game, "rooms", None) if game is not None else None
    aliases = getattr(game, "room_aliases", None) if game is not None else None
    if game is not None and aliases is None:
        game.room_aliases = {}
        aliases = game.room_aliases

    old_key = (getattr(room, "key", "") or "").strip()
    raw = getattr(room, "vnum", None)
    parsed_existing = parse_vnum(old_key)
    if parsed_existing is not None:
        # Runtime pockets reload from SQL under an existing VNUM (homestead
        # remodel, vehicle interior, …). Keep the stable id even when
        # ``room.vnum`` was not persisted on the row (bug report 655).
        vnum = old_key.upper()
    elif raw is not None and str(raw).strip():
        try:
            vnum = validate_vnum(raw)
        except ValueError:
            if taken is None:
                taken = collect_taken_vnums(
                    rooms.values() if isinstance(rooms, dict) else ()
                )
            vnum = allocate_vnum_for_name(
                old_key,
                getattr(room, "title", None),
                taken=taken,
                anchor=anchor,
            )
    else:
        if taken is None:
            taken = collect_taken_vnums(
                rooms.values() if isinstance(rooms, dict) else ()
            )
        vnum = allocate_vnum_for_name(
            old_key,
            getattr(room, "title", None),
            taken=taken,
            anchor=anchor,
        )
    room.vnum = vnum
    if taken is not None:
        taken.add(vnum)

    ensure_title_before_rekey(room)
    leg = getattr(room, "legacy_key", None)
    if not leg and old_key and old_key != vnum:
        room.legacy_key = old_key
        leg = old_key

    room.key = vnum
    if isinstance(rooms, dict):
        existing = rooms.get(vnum)
        if existing is not None and existing is not room:
            # Persistence may register a map_missing_stub under this VNUM
            # when characters load before runtime vehicle interiors exist.
            if getattr(existing, "map_missing_stub", False):
                from engine.world import safe_place
                for who in list(existing.characters()):
                    safe_place(who, room)
                for dk, dr in list(rooms.items()):
                    if dr is existing:
                        rooms.pop(dk, None)
        for dk, dr in list(rooms.items()):
            if dr is room and dk != vnum:
                rooms.pop(dk, None)
        rooms[vnum] = room
        for alias in (leg, old_key):
            note_room_alias(game, alias, vnum)
    return vnum


def stamp_json_room_identity(entry, *, taken):
    """Make a rooms[] dict's JSON key the VNUM.

    Allocates a missing VNUM (or reallocates a colliding stamped one).
    Leftover leftover-name keys move to ``legacy_key``. ROOM NAME stays
    on ``title``. Mutates ``entry`` and ``taken``. Returns the VNUM
    string, or empty when the entry has no key/title to derive from.
    """
    if not isinstance(entry, dict):
        return ""
    leftover = str(entry.get("key") or "").strip()
    title = str(entry.get("title") or "").strip()
    if not leftover and not title:
        return ""

    raw = entry.get("vnum")
    vnum = None
    if raw is not None and str(raw).strip():
        try:
            vnum = validate_vnum(raw)
        except ValueError:
            vnum = None
        else:
            # Keep a room's own VNUM even when ``taken`` already lists it
            # (Studio re-apply). Reallocate when another room owns it.
            own = leftover == vnum
            if vnum in taken and not own:
                vnum = None
    if vnum is None:
        vnum = allocate_vnum_for_name(
            leftover or title,
            title or None,
            taken=taken,
        )
    entry["vnum"] = vnum
    taken.add(vnum)

    if leftover and leftover != vnum:
        if not str(entry.get("legacy_key") or "").strip():
            entry["legacy_key"] = leftover
        if not title:
            face = bare_key_name(leftover)
            if face:
                entry["title"] = face
        entry["key"] = vnum
    elif not leftover:
        entry["key"] = vnum
    return vnum


def rekey_json_room_batch(entries, *, taken, extra_exits=None):
    """Stamp VNUM identity on a rooms[] batch and remap leftover tokens.

    Exit targets, ``main_homeroom``, and optional ``extra_exits`` leftover
    names become VNUMs. ``extra_exits`` is mutated in place. Returns the
    leftover→VNUM alias map (also includes each VNUM→itself).
    """
    aliases = {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        leftover = str(entry.get("key") or "").strip()
        vnum = stamp_json_room_identity(entry, taken=taken)
        if not vnum:
            continue
        if leftover:
            aliases[leftover] = vnum
        aliases[vnum] = vnum
        leg = str(entry.get("legacy_key") or "").strip()
        if leg:
            aliases[leg] = vnum

    def remap(token):
        mapped, _changed = _remap_identity_token(token, aliases)
        return mapped

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        exits = entry.get("exits")
        if isinstance(exits, dict):
            for direction, dest in list(exits.items()):
                exits[direction] = remap(dest)
        if "main_homeroom" in entry:
            entry["main_homeroom"] = remap(entry.get("main_homeroom"))
    if extra_exits is not None:
        remapped = {}
        for from_key, exits in list(extra_exits.items()):
            new_from = remap(from_key)
            new_exits = {
                direction: remap(dest)
                for direction, dest in (exits or {}).items()
            }
            bucket = remapped.setdefault(new_from, {})
            bucket.update(new_exits)
        extra_exits.clear()
        extra_exits.update(remapped)
    return aliases


def _remap_identity_token(text, aliases):
    """Map a stored room-key string through ``room_aliases``."""
    if not text or not aliases:
        return text, False
    key = str(text).strip()
    if not key:
        return text, False
    mapped = aliases.get(key)
    if mapped and mapped != key:
        return mapped, True
    return text, False


# ---------------------------------------------------------------------------
# Room ownership identity (engine-pure; no supers imports)
# ---------------------------------------------------------------------------

# Attrs mirroring persisted ``*_rooms`` SQL tables (``engine.persistence``).
_ROOM_OWNER_ID_ATTRS = (
    ("homestead_plot_id", "homestead_plot"),
    ("personal_realm_id", "personal_realm"),
    ("player_shop_id", "player_shop"),
    ("demesne_id", "demesne"),
    ("township_id", "township"),
    ("dream_pocket_id", "dream_pocket"),
    ("realm_town_id", "realm_town"),
    ("homestead_owner", "homestead_owner"),
    ("djinn_instance_id", "djinn_instance"),
    ("dungeon_party_run_id", "dungeon_party"),
    ("gabriel_pocket_instance_id", "gabriel_pocket"),
    ("mine_room_id", "mine_room"),
    ("rowena_portal_run_id", "rowena_portal"),
)

# Runtime instance / claim markers — not in every SQL table but block title fold.
_ROOM_INSTANCE_BOOL_ATTRS = (
    "is_vehicle_interior",
    "virtual_mine",
    "dungeon_instance",
    "is_hotel_room",
    "is_prison_cell",
    "purgatory_pit",
)

# Shared title among multiple owners — alias guard treats as poison-prone.
_AMBIGUOUS_TITLE_OWNER = ("ambiguous_title", "*")


def _nonempty_owner_id(value):
    """Normalize owner/id stamps: ``None`` / ``""`` / ``0`` are unset."""
    if value is None or value is False:
        return None
    text = str(value).strip()
    if not text or text == "0":
        return None
    return text


def _room_attr_owner_token(room):
    """Owner token from room attrs only (no ``game`` side indexes)."""
    if room is None:
        return None
    for attr, kind in _ROOM_OWNER_ID_ATTRS:
        ident = _nonempty_owner_id(getattr(room, attr, None))
        if ident:
            return (kind, ident)
    for attr in _ROOM_INSTANCE_BOOL_ATTRS:
        if getattr(room, attr, False):
            marker = getattr(room, "key", None) or attr
            return (attr, str(marker))
    # ``lodging.claim_house`` stamps is_house/is_home without homestead_plot_id.
    if getattr(room, "is_home", False) and (
        getattr(room, "is_house", False) or getattr(room, "private_home", False)
    ):
        owner = _nonempty_owner_id(getattr(room, "homestead_owner", None))
        if owner:
            return ("homestead_owner", owner)
        ident = internal_room_key(room) or getattr(room, "key", "") or "claimed_home"
        return ("claimed_home", ident)
    return None


def _index_game_owner_keys(index, game):
    """Fill ``index["key"]`` from plot/shop/town side dicts on ``game``."""
    if game is None:
        return

    def _stamp(key, tok):
        text = str(key or "").strip()
        if text:
            index["key"][text] = tok

    for plot in (getattr(game, "homestead_plots", None) or {}).values():
        if not isinstance(plot, dict):
            continue
        pid = _nonempty_owner_id(plot.get("plot_id"))
        if not pid:
            continue
        tok = ("homestead_plot", pid)
        for field in ("hub_room_key", "cell_room_key"):
            _stamp(plot.get(field), tok)
        meta = plot.get("meta")
        if isinstance(meta, dict):
            for field in ("yard_shop_room_key", "garage_pad_room_key"):
                _stamp(meta.get(field), tok)

    for shop in (getattr(game, "player_shops", None) or {}).values():
        if not isinstance(shop, dict):
            continue
        sid = _nonempty_owner_id(shop.get("shop_id"))
        if not sid:
            continue
        tok = ("player_shop", sid)
        for field in ("host_room_key", "hub_room_key"):
            _stamp(shop.get(field), tok)

    for town in (getattr(game, "townships", None) or {}).values():
        if not isinstance(town, dict):
            continue
        tid = _nonempty_owner_id(town.get("town_id"))
        if not tid:
            continue
        tok = ("township", tid)
        for field in ("hub_room_key", "mouth_room_key"):
            _stamp(town.get(field), tok)

    for realm in (getattr(game, "personal_realms", None) or {}).values():
        if not isinstance(realm, dict):
            continue
        rid = _nonempty_owner_id(realm.get("realm_id"))
        if not rid:
            continue
        tok = ("personal_realm", rid)
        _stamp(realm.get("hub_room_key"), tok)

    for pocket in (getattr(game, "dream_pockets", None) or {}).values():
        if not isinstance(pocket, dict):
            continue
        pid = _nonempty_owner_id(pocket.get("pocket_id"))
        if not pid:
            continue
        tok = ("dream_pocket", pid)
        for field in ("hub_room_key", "anchor_room_key"):
            _stamp(pocket.get(field), tok)

    for demesne in (getattr(game, "demesnes", None) or {}).values():
        if not isinstance(demesne, dict):
            continue
        did = _nonempty_owner_id(demesne.get("demesne_id"))
        if not did:
            continue
        tok = ("demesne", did)
        for field in ("hub_room_key", "host_hub_key"):
            _stamp(demesne.get(field), tok)


def _build_room_owner_index(game):
    """Cache maps for key/legacy/title → owner token (per boot heal pass).

    Keyed on rooms/plots identity *and* length plus a generation counter,
    because ``id(game.rooms)`` stays the same while stamp inserts a hall
    (shared-title fail-open) and because ``load_homesteads`` stamps
    ``homestead_plot_id`` onto rooms that already sit in the dict.
    """
    if game is None:
        return {"key": {}, "legacy": {}, "title_owners": {}}
    stamp = _owner_index_stamp(game)
    cached = getattr(game, "_room_owner_index_cache", None)
    if isinstance(cached, dict) and cached.get("_stamp") == stamp:
        return cached
    index = {"key": {}, "legacy": {}, "title_owners": {}, "_stamp": stamp}
    rooms = getattr(game, "rooms", None) or {}
    for room in list((rooms or {}).values()):
        _index_room_owner_token(index, room)
    _index_game_owner_keys(index, game)
    game._room_owner_index_cache = index
    return index


def _index_room_owner_token(index, room):
    """Fold one room's owner token into ``index`` (no-op when unowned).

    Split out of :func:`_build_room_owner_index` so a bulk insert can add
    rooms one at a time (:class:`RoomAliasBatch`) and get byte-identical
    maps to a full rebuild.
    """
    if room is None:
        return
    tok = _room_attr_owner_token(room)
    if tok is None:
        return
    for candidate in (
        getattr(room, "key", None),
        internal_room_key(room),
        getattr(room, "vnum", None),
    ):
        text = str(candidate or "").strip()
        if text:
            index["key"][text] = tok
    leg = str(getattr(room, "legacy_key", None) or "").strip()
    if leg:
        index["legacy"][leg] = tok
    title = (room_name(room) or "").strip().lower()
    if title:
        index["title_owners"].setdefault(title, set()).add(tok)


def _owner_index_stamp(game):
    """Identity of the live rooms/plots maps plus an explicit generation."""
    rooms = getattr(game, "rooms", None)
    plots = getattr(game, "homestead_plots", None)
    return (
        getattr(game, "_room_owner_index_gen", 0),
        id(rooms),
        len(rooms) if isinstance(rooms, dict) else 0,
        id(plots),
        len(plots) if isinstance(plots, dict) else 0,
    )


def invalidate_room_owner_index(game):
    """Drop the owner-index cache after plot_id stamps or alias purges."""
    if game is None:
        return
    game._room_owner_index_gen = getattr(game, "_room_owner_index_gen", 0) + 1
    game._room_owner_index_cache = None


def room_owner_identity_token(room, game=None, *, index=None):
    """Return ``(kind, id)`` when ``room`` belongs to an owned pocket."""
    if room is None:
        return None
    tok = _room_attr_owner_token(room)
    if tok is not None:
        return tok
    if game is None:
        return None
    if index is None:
        index = _build_room_owner_index(game)
    ident = internal_room_key(room) or getattr(room, "key", "") or ""
    return index["key"].get(ident)


def room_has_owner_identity(room, game=None) -> bool:
    """True when ``room`` is owner-scoped (attrs or ``game`` side indexes)."""
    return room_owner_identity_token(room, game) is not None


def hand_room_title_fold_eligible(game, room) -> bool:
    """True only for positively unowned map-loaded hand rooms.

    Conservative default: if we cannot prove the room is generic world
    content, title-collapse must not touch it.
    """
    if room is None or not is_hand_room(room):
        return False
    if room_owner_identity_token(room, game) is not None:
        return False
    if game is not None:
        index = _build_room_owner_index(game)
        ident = internal_room_key(room) or getattr(room, "key", "") or ""
        if ident and index["key"].get(ident):
            return False
        leg = str(getattr(room, "legacy_key", None) or "").strip()
        if leg and index["legacy"].get(leg):
            return False
    return True


def build_room_direct_index(game):
    """Pre-build ``key`` / ``legacy_key`` → Room for leftover resolution.

    Replaces :func:`_resolve_room_direct`'s fallback ``rooms.values()`` scan
    with a dict hit. First writer wins so a duplicate key/legacy resolves to
    the same room the scan returned (dict iteration is insertion order).

    Deliberately **not** cached on ``game``: a stale key→Room map would hand
    a boot heal a room that has since been deleted or rekeyed. Build it for
    one pass, then drop it.
    """
    direct = {}
    rooms = getattr(game, "rooms", None) if game is not None else None
    if not isinstance(rooms, dict):
        return direct
    for room in rooms.values():
        _index_room_direct(direct, room)
    return direct


def _index_room_direct(direct, room):
    """Fold one room's key / legacy_key into a ``build_room_direct_index`` map."""
    if room is None:
        return
    key = str(getattr(room, "key", "") or "").strip()
    if key:
        direct.setdefault(key, room)
    leg = str(getattr(room, "legacy_key", None) or "").strip()
    if leg:
        direct.setdefault(leg, room)


def _resolve_room_direct(game, token, *, direct=None):
    """Resolve a token to a Room without following ``room_aliases``.

    ``direct`` is an optional pre-built map from
    :func:`build_room_direct_index`; when given it replaces the fallback
    ``rooms.values()`` scan with a dict hit.
    """
    if game is None or not token:
        return None
    text = str(token).strip()
    if not text:
        return None
    rooms = getattr(game, "rooms", None)
    if not isinstance(rooms, dict):
        return None
    hit = rooms.get(text)
    if hit is not None:
        return hit
    parsed = parse_vnum(text)
    if parsed is not None:
        vnum = format_vnum(*parsed)
        hit = rooms.get(vnum)
        if hit is not None:
            return hit
    if direct is not None:
        return direct.get(text)
    for room in list((rooms or {}).values()):
        if room is None:
            continue
        if str(getattr(room, "key", "") or "").strip() == text:
            return room
        leg = str(getattr(room, "legacy_key", None) or "").strip()
        if leg == text:
            return room
    return None


def _owner_for_leftover_token(game, token, *, index=None, direct=None):
    """Owner token for a leftover name — never follows alias under test."""
    if game is None or not token:
        return None
    text = str(token).strip()
    if not text:
        return None
    if index is None:
        index = _build_room_owner_index(game)
    if text in index["key"]:
        return index["key"][text]
    if text in index["legacy"]:
        return index["legacy"][text]
    room = _resolve_room_direct(game, text, direct=direct)
    if room is not None:
        tok = room_owner_identity_token(room, game, index=index)
        if tok is not None:
            return tok
    title_key = text.lower()
    owners = index["title_owners"].get(title_key)
    if owners:
        if len(owners) > 1:
            return _AMBIGUOUS_TITLE_OWNER
        return next(iter(owners))
    return None


def _owners_conflict(src_owner, tgt_owner) -> bool:
    """True when a remap/alias would cross an ownership boundary.

    Unowned→unowned (both ``None``) is the title-fold path and is allowed.
    Owned leftover onto an unowned VNUM (Town Park, etc.) is not — that
    is how a poisoned dual-read rewrote ``home_room_key`` off the plot.
    """
    if src_owner == _AMBIGUOUS_TITLE_OWNER:
        return True
    if src_owner is None and tgt_owner is None:
        return False
    if src_owner is None or tgt_owner is None:
        return True
    return src_owner != tgt_owner


def room_has_stamped_vnum(room) -> bool:
    """True when ``room`` already carries its own VNUM identity."""
    if room is None:
        return False
    key = getattr(room, "key", None) or ""
    if parse_vnum(key) is not None:
        return True
    raw = getattr(room, "vnum", None)
    if raw is None or not str(raw).strip():
        return False
    try:
        validate_vnum(raw)
        return True
    except ValueError:
        return False


def _ownership_boundary_blocks_merge(left, right, *, label: str, log=True, game=None):
    """Refuse graph merges / content moves across different owner pockets."""
    left_tok = room_owner_identity_token(left, game)
    right_tok = room_owner_identity_token(right, game)
    if left_tok is None and right_tok is None:
        return False
    if left_tok == right_tok:
        return False
    if log:
        left_label = getattr(left, "key", None) or "?"
        right_label = getattr(right, "key", None) or "?"
        print(
            f"[boot heal] REFUSE {label}: ownership boundary "
            f"{left_label!r} ({left_tok}) vs {right_label!r} ({right_tok})",
            flush=True,
        )
    return True


def _alias_crosses_owner_boundary(game, leftover, vnum, *,
                                  index=None, direct=None) -> bool:
    """True when ``leftover`` → ``vnum`` would poison dual-read.

    ``index`` / ``direct`` let a bulk caller (:class:`RoomAliasBatch`) supply
    maps it maintains itself instead of paying a rebuild and a room scan.
    """
    if game is None:
        return False
    if index is None:
        index = _build_room_owner_index(game)
    src_owner = _owner_for_leftover_token(
        game, leftover, index=index, direct=direct,
    )
    tgt_room = _resolve_room_direct(game, vnum, direct=direct)
    if tgt_room is None:
        return False
    tgt_owner = room_owner_identity_token(tgt_room, game, index=index)
    if not _owners_conflict(src_owner, tgt_owner):
        return False
    print(
        f"[boot heal] REFUSE room alias: {leftover!r} -> {vnum!r} "
        f"(source owner {src_owner!r}, target owner {tgt_owner!r})",
        flush=True,
    )
    return True


def _self_loop_heal_skips_room(room) -> bool:
    """Rooms whose self-loops must survive engine heal.

    Homestead plots: ``supers.homestead.repair_homestead_plot_graph`` rewires
    looping east from reverse evidence (bug reports 1523 / 1525).

    Demesne micro cells: placeholder self-loops are intentional —
    ``try_demesne_move`` depends on them.

    Shop / township / dream / realm / street-home rooms have no sibling
    repair — engine heal must still drop accidental self-loops there.
    """
    if _nonempty_owner_id(getattr(room, "homestead_plot_id", None)):
        return True
    if _nonempty_owner_id(getattr(room, "demesne_id", None)):
        return True
    return False


def _remap_identity_token_guarded(game, text, aliases, *, expect_owner=None):
    """Like :func:`_remap_identity_token` but refuse cross-owner alias hops."""
    new, ch = _remap_identity_token(text, aliases)
    if not ch or game is None:
        return new, ch
    target = lookup_room(game, new)
    if target is None:
        return new, ch
    index = _build_room_owner_index(game)
    src_owner = _owner_for_leftover_token(game, text, index=index)
    tgt_owner = room_owner_identity_token(target, game, index=index)
    if _owners_conflict(src_owner, tgt_owner):
        print(
            f"[boot heal] REFUSE string-ref remap {text!r} -> {new!r}: "
            f"source owner {src_owner!r} vs target owner {tgt_owner!r}"
            + (
                f" (expected {expect_owner!r})"
                if expect_owner is not None
                else ""
            ),
            flush=True,
        )
        return text, False
    if expect_owner is not None and tgt_owner is not None and tgt_owner != expect_owner:
        print(
            f"[boot heal] REFUSE string-ref remap {text!r} -> {new!r}: "
            f"expected owner {expect_owner}, target has {tgt_owner}",
            flush=True,
        )
        return text, False
    return new, ch


def heal_hand_room_string_refs(game) -> int:
    """Remap ``main_homeroom`` / homestead plot keys via ``room_aliases``.

    Idempotent. Returns how many fields were rewritten. Skips remaps that
    would follow a poisoned alias across an ownership boundary (a prior bad
    title-fold could map one plot's dig key at another player's VNUM).
    """
    if game is None:
        return 0
    aliases = getattr(game, "room_aliases", None) or {}
    if not aliases:
        return 0
    changed = 0
    for room in (getattr(game, "rooms", None) or {}).values():
        if room is None:
            continue
        raw = getattr(room, "main_homeroom", None)
        expect = room_owner_identity_token(room, game)
        new, ch = _remap_identity_token_guarded(
            game, raw, aliases, expect_owner=expect,
        )
        if ch:
            room.main_homeroom = new
            changed += 1
    plots = getattr(game, "homestead_plots", None) or {}
    for plot in plots.values():
        if not isinstance(plot, dict):
            continue
        plot_id = str(plot.get("plot_id") or "").strip()
        expect = ("homestead_plot", plot_id) if plot_id else None
        for field in ("hub_room_key", "cell_room_key"):
            new, ch = _remap_identity_token_guarded(
                game, plot.get(field), aliases, expect_owner=expect,
            )
            if ch:
                plot[field] = new
                changed += 1
        meta = plot.get("meta")
        if not isinstance(meta, dict):
            continue
        for field in ("yard_shop_room_key", "garage_pad_room_key"):
            new, ch = _remap_identity_token_guarded(
                game, meta.get(field), aliases, expect_owner=expect,
            )
            if ch:
                meta[field] = new
                changed += 1
    return changed


def heal_unstamped_hand_rooms(game) -> dict:
    """Boot heal: stamp VNUMs on every persistent hand room and rekey.

    Returns ``{"stamped": n, "rekeyed": m, "string_refs": k}``.
    """
    stats = {"stamped": 0, "rekeyed": 0, "string_refs": 0}
    if game is None:
        return stats
    rooms = getattr(game, "rooms", None) or {}
    taken = collect_taken_vnums(rooms.values())
    for room in list(rooms.values()):
        if room is None or not hand_room_wants_vnum(room):
            continue
        had_vnum = bool(
            getattr(room, "vnum", None) and str(room.vnum).strip()
        )
        old_key = getattr(room, "key", "") or ""
        stamp_hand_room(game, room, taken=taken)
        if not had_vnum:
            stats["stamped"] += 1
        if old_key != getattr(room, "key", ""):
            stats["rekeyed"] += 1
    stats["string_refs"] = heal_hand_room_string_refs(game)
    return stats


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


def fold_room_needle(text) -> str:
    """Staff query fold: underscores and hyphens count as spaces.

    ``goto fort_laramie`` must find Fort Laramie rooms. Bare ``-`` / ``_``
    folds to empty and matches nothing (never ``'' in title``).
    """
    raw = str(text or "").strip().lower().replace("_", " ").replace("-", " ")
    return " ".join(raw.split())


def staff_match_label(room) -> str:
    """Staff disambiguation line: NAME[VNUM] plus map id when stamped.

    Grid mouths often have no VNUM -- the map id is what tells America
    Overland apart from a second atlas that reused a similar ROOM NAME.
    """
    label = staff_room_label(room)
    map_id = str(getattr(room, "map_id", None) or "").strip()
    if map_id:
        return f"{label} ({map_id})"
    return label


def staff_room_label(room) -> str:
    """GM / mapper prose: ``ROOM NAME[VNUM]`` when a hand-room vnum exists.

    Players must never see this form -- use :func:`room_name` for ordinary
    look. Dig tools that still need the graph id use
    :func:`internal_room_key` (VNUM for stamped hand rooms).
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


def label_is_bare_vnum(text) -> bool:
    """True when ``text`` is only a hand-room VNUM (``JG00001``), not prose."""
    return parse_vnum(str(text or "").strip().upper()) is not None


def describe_room(room, *, staff: bool = False) -> str:
    """Prose place label: ROOM NAME, or NAME[VNUM] when ``staff``.

    Never returns a bare internal dig key when a ROOM NAME or VNUM exists.
    Empty / missing room → ``\"?\"``. Prefer this over ``location.key`` in
    any player or GM message. Opaque Cadence dig keys
    (``unowned amenity12``, ``unowned shop4``, …) are never returned --
    ``look_title`` / flag generics win first. Players never see a bare
    VNUM string -- staff may use ``NAME[VNUM]`` or the raw code alone.
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
        derive_room_title_from_context,
        generic_title_from_flags,
        is_internal_place_key,
        is_opaque_storage_key,
    )
    if label and is_internal_place_key(label):
        derived = derive_room_title_from_context(room)
        if derived:
            return derived
        label = generic_title_from_flags(room) or ""
    if label and is_opaque_storage_key(label):
        label = generic_title_from_flags(room) or ""
    if label and not is_opaque_storage_key(label) and not is_internal_place_key(label):
        if not staff and label_is_bare_vnum(label):
            if getattr(room, "private_home", False) or getattr(room, "is_home", False):
                return "Private Home"
            label = generic_title_from_flags(room) or ""
        else:
            return label
    # Authored title missing -- staff may fall back to the raw vnum string.
    raw = getattr(room, "vnum", None)
    if staff and raw is not None and str(raw).strip():
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
    room = lookup_room(game, text)
    if room is not None:
        label = describe_room(room, staff=staff)
        if label and label != "?":
            return label
    from engine.room_naming import bare_key, is_opaque_storage_key
    # Never echo dig keys when the room is missing from the live graph.
    if is_opaque_storage_key(text):
        return fallback
    bare = bare_key(text)
    # Missing room + VNUM-shaped key used to return RK00004 to players.
    if not staff and bare and label_is_bare_vnum(bare):
        return fallback
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
        derive_room_title_from_context,
        generic_title_from_flags,
        is_internal_place_key,
        is_opaque_storage_key,
    )
    title = getattr(room, "title", None)
    key = getattr(room, "key", "") or ""
    if authored_title_is_usable(title, key):
        return
    derived = derive_room_title_from_context(room)
    if derived:
        room.title = derived
        return
    if is_opaque_storage_key(key) or is_internal_place_key(key):
        generic = generic_title_from_flags(room)
        if generic:
            room.title = generic
        return
    face = bare_key(key) if key else ""
    if face and not is_internal_place_key(face):
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


def merge_map_additive(
    rooms: dict, filename: str, data: dict,
) -> tuple[dict, list]:
    """Filter a map/zone JSON doc down to rooms genuinely missing from
    ``rooms``.

    On-demand loaders (easter-egg homesteads, runtime hot-loads) that call
    ``world_maps.create_rooms_from_map_data`` straight into a staging copy
    of an already-populated ``game.rooms`` dict crash with "already used
    by another map file" the moment *any* room in the doc was loaded
    before -- including a room this very same file put there on an
    earlier boot pass (e.g. the file also ``autoload``s normally, and a
    caller's own index of "did my chambers load" went stale for some
    unrelated reason). That is the same additive-merge problem
    ``engine.map_heal.merge_missing_from_backup`` solves for on-disk
    backup JSON, just for an in-memory ``rooms`` dict.

    A JSON room is treated as "already loaded" (silently dropped from the
    result) when its ``key`` or ``vnum`` already resolves to a live Room
    whose ``legacy_key`` (or bare ``key``) matches this room's own
    ``legacy_key`` -- i.e. it is the *same* authored chamber, not a
    coincidence. A ``key``/``vnum`` claimed by a live Room that does
    **not** match is a genuine authoring collision (two different rooms
    wanting the same identity) and is reported back as a conflict string
    instead of being silently dropped -- callers should raise loud with
    those, the same "fail loud on a real collision" spirit as
    ``world_maps._add_room``.

    Returns ``(filtered_data, conflicts)``. ``filtered_data`` is a shallow
    copy of ``data`` whose ``rooms[]`` holds only the missing entries (safe
    to pass straight to ``create_rooms_from_map_data`` / ``link_map_data``);
    ``conflicts`` is empty on a clean merge. Grid cells (``data["grid"]``)
    are not touched -- this only guards hand-authored ``rooms[]`` entries.
    Never raises itself; the caller decides how to report ``conflicts``.
    """
    live_vnum_owner: dict[str, object] = {}
    for room in (rooms or {}).values():
        raw_v = getattr(room, "vnum", None)
        if raw_v and str(raw_v).strip():
            live_vnum_owner[str(raw_v).strip().upper()] = room

    missing = []
    conflicts = []
    for room_data in data.get("rooms", []) or []:
        key = str(room_data.get("key") or "").strip()
        vnum = str(room_data.get("vnum") or "").strip().upper()
        legacy = str(room_data.get("legacy_key") or "").strip()
        existing = rooms.get(key) if key else None
        if existing is None and vnum:
            existing = live_vnum_owner.get(vnum)
        if existing is None:
            missing.append(room_data)
            continue
        existing_legacy = str(
            getattr(existing, "legacy_key", "") or "",
        ).strip()
        existing_key = str(getattr(existing, "key", "") or "").strip()
        if legacy:
            same_room = legacy in (existing_legacy, existing_key)
        else:
            # Neither side authored a legacy_key -- only an exact key match
            # is safe to call "the same room" (matches the common case of
            # an idempotent reload of an unchanged file).
            same_room = bool(key) and key == existing_key
        if same_room:
            continue
        ident = key or vnum
        conflicts.append(
            f"{filename}: room {ident!r} collides with "
            f"{staff_room_label(existing)!r} ({existing_key!r}) -- "
            "different content wants the same key/vnum"
        )
    filtered = dict(data)
    filtered["rooms"] = missing
    return filtered, conflicts


def _rewrite_hand_room_refs_in_doc(data, old_key, new_key):
    """Rewrite exit / pocket / portal targets inside one map JSON doc."""
    if not data or not old_key or old_key == new_key:
        return
    for room in data.get("rooms") or []:
        if room.get("key") == old_key:
            room["key"] = new_key
        exits = room.get("exits") or {}
        for direction, dest in list(exits.items()):
            if dest == old_key:
                exits[direction] = new_key
    for pocket in data.get("pockets") or []:
        if pocket.get("hub_room") == old_key:
            pocket["hub_room"] = new_key
        if pocket.get("entry_room") == old_key:
            pocket["entry_room"] = new_key
    if data.get("runtime_hub") == old_key:
        data["runtime_hub"] = new_key
    for field in ("entry_room", "start_room", "hub_room"):
        if data.get(field) == old_key:
            data[field] = new_key
    grid = data.get("grid")
    if isinstance(grid, dict):
        for portal in grid.get("portals") or []:
            if portal.get("to_room") == old_key:
                portal["to_room"] = new_key


def normalize_hand_room_identities_in_map_docs(map_files) -> list[str]:
    """Pre-pass for ``load_all_maps``: globally unique hand-room storage keys.

    Walks every ``(filename, data)`` pair before Pass 1 creates live
    ``Room`` objects. When two map files (or two entries in one file)
    claim the same storage ``key`` for different chambers, the *later*
    entry in stable ``(filename, key)`` order is remapped to the next
    free vnum under its ROOM NAME prefix. The colliding room's ``key``
    becomes that vnum (Phase 3 identity), the former key is preserved as
    ``legacy_key`` when missing, and exit / pocket / portal strings
    **inside that file only** are rewritten.

    Duplicate **vnums** with different keys are left to ``assign_room_vnums``
    / CI (first authored vnum wins; the duplicate field is dropped) and
    Pass 1.5 fill — this pass does not allocate missing vnums (that
    would steal a slot before an explicit ``CP00002`` row is registered).

    Mutates ``map_files`` docs in place. Returns human-readable log lines
    for each remap (caller prints as ``[boot heal]``). Idempotent when
    content is already clean.
    """
    if not map_files:
        return []

    from engine.world_maps import ensure_hand_room_identity

    records = []
    for file_ord, (filename, data) in enumerate(map_files):
        if not isinstance(data, dict):
            continue
        for idx, room in enumerate(data.get("rooms") or []):
            if not isinstance(room, dict):
                continue
            records.append((file_ord, filename, data, idx, room))

    taken: set[str] = set()
    key_owner: dict[str, tuple[int, int]] = {}
    logs: list[str] = []

    # Pass 1: claim explicit unique vnums (first in load order wins).
    for file_ord, filename, data, idx, room in records:
        ensure_hand_room_identity(
            room, where=f"{filename} rooms[{idx}]",
        )
        raw_v = room.get("vnum")
        if raw_v is None or not str(raw_v).strip():
            continue
        try:
            vnum = validate_vnum(raw_v)
        except ValueError:
            room.pop("vnum", None)
            continue
        if vnum in taken:
            room.pop("vnum", None)
            continue
        taken.add(vnum)

    # Pass 2: resolve duplicate storage keys (the boot crash surface).
    for file_ord, filename, data, idx, room in records:
        key = str(room.get("key") or "").strip()
        title = room.get("title")
        owner = (file_ord, idx)

        if key in key_owner and key_owner[key] != owner:
            old_key = key
            new_vnum = allocate_vnum_for_name(old_key, title, taken=taken)
            leg = str(room.get("legacy_key") or "").strip()
            if not leg or leg == old_key:
                room["legacy_key"] = old_key
            room["vnum"] = new_vnum
            room["key"] = new_vnum
            _rewrite_hand_room_refs_in_doc(data, old_key, new_vnum)
            key = new_vnum
            taken.add(new_vnum)
            logs.append(
                f"{filename}: remapped hand room {old_key!r} -> {new_vnum!r} "
                "(global key collision)"
            )

        key_owner[key] = owner

    return logs


def _aliases_table(game):
    """Return ``game.room_aliases``, creating the dict when missing."""
    aliases = getattr(game, "room_aliases", None)
    if aliases is None:
        game.room_aliases = {}
        aliases = game.room_aliases
    return aliases if isinstance(aliases, dict) else {}


def _ensure_alias_fold(game):
    """Live leftover→VNUM fold. Rebuilds only when the alias table is replaced.

    Do **not** key this on ``_static_room_graph_gen``. Stamp/insert bumps that
    gen on every authored room; rebuilding the fold each time is O(aliases)
    per insert (boot quadratic). :func:`note_room_alias` updates the live
    map in O(1).
    """
    aliases = getattr(game, "room_aliases", None) or {}
    if not isinstance(aliases, dict):
        return {}
    fold = getattr(game, "_room_alias_fold", None)
    bound = getattr(game, "_room_alias_fold_id", None)
    if not isinstance(fold, dict) or bound != id(aliases):
        fold = {str(aka).lower(): vnum for aka, vnum in aliases.items()}
        game._room_alias_fold = fold
        game._room_alias_fold_id = id(aliases)
    return fold


def _alias_would_poison_owner_boundary(game, leftover, vnum, *,
                                       index=None, direct=None) -> bool:
    """True when recording ``leftover`` → ``vnum`` crosses owner pockets."""
    return _alias_crosses_owner_boundary(
        game, leftover, vnum, index=index, direct=direct,
    )


def find_poisoned_room_aliases(game) -> list[dict]:
    """Return poisoned alias rows with owner detail (does not mutate).

    Resolves the leftover **without** following the alias under test so
    legacy-name / shared-title poison is visible even when ``lookup_room``
    would already return the wrong room.
    """
    if game is None:
        return []
    aliases = getattr(game, "room_aliases", None) or {}
    if not isinstance(aliases, dict):
        return []
    index = _build_room_owner_index(game)
    # One leftover→Room map for the whole table: the per-alias fallback is a
    # full rooms scan, which made this heal O(aliases × rooms).
    direct = build_room_direct_index(game)
    poisoned: list[dict] = []
    for leftover, vnum in aliases.items():
        if not leftover or not vnum or leftover == vnum:
            continue
        src_owner = _owner_for_leftover_token(
            game, leftover, index=index, direct=direct,
        )
        tgt_room = _resolve_room_direct(game, vnum, direct=direct)
        if tgt_room is None:
            continue
        tgt_owner = room_owner_identity_token(tgt_room, game, index=index)
        if not _owners_conflict(src_owner, tgt_owner):
            continue
        poisoned.append(
            {
                "leftover": str(leftover),
                "target_vnum": str(vnum),
                "source_owner": src_owner,
                "target_owner": tgt_owner,
            }
        )
    return poisoned


def drop_poisoned_room_aliases(game) -> list[dict]:
    """Remove cross-owner alias rows; return the rows dropped.

    Always rebuilds the owner index first so a load that just stamped
    ``homestead_plot_id`` onto existing rooms is visible.
    """
    invalidate_room_owner_index(game)
    rows = find_poisoned_room_aliases(game)
    for row in rows:
        drop_room_alias(game, row["leftover"])
    return rows


class RoomAliasBatch:
    """Incremental owner/direct indexes for a bulk room insert.

    ``note_room_alias`` runs an ownership-poison guard, and on a miss that
    guard costs two full passes over ``game.rooms``: a rebuild of the owner
    index (its cache key includes ``len(game.rooms)``, so a *growing* dict
    never hits) plus the fallback room scan in ``_resolve_room_direct``.
    Calling it once per room while filling ``game.rooms`` is therefore
    O(N²) -- on live (~19k rooms) that was ~190s of the ~280s boot, and
    every auto-deploy boot probe paid it a second time.

    This keeps both maps and folds each room in as it is inserted, so the
    guard still sees **exactly** the rooms present at that moment. The
    decisions are unchanged; only the cost is. Batching them to decide
    against the *finished* room set would be cheaper still, but it is not
    the same answer -- shared-title rooms flip to ambiguous once their twin
    lands -- so this keeps insert-time semantics.

    Scoped to one bulk insert and then dropped; nothing is cached on
    ``game``, so there is no stale-index window.
    """

    def __init__(self, game):
        """Snapshot the pre-existing rooms, then fold new ones in."""
        self.game = game
        # Deep-enough copy: folding must not mutate the cache other callers
        # hold. title_owners values are sets, so copy those too.
        base = _build_room_owner_index(game)
        self.index = {
            "key": dict(base.get("key") or {}),
            "legacy": dict(base.get("legacy") or {}),
            "title_owners": {
                title: set(toks)
                for title, toks in (base.get("title_owners") or {}).items()
            },
            "_stamp": None,
        }
        self.direct = build_room_direct_index(game)
        # A full rebuild applies the plot/shop/township side keys *after*
        # every room, so those win on a key collision. Replay them onto any
        # room key we fold in later to keep that precedence.
        side = {"key": {}, "legacy": {}, "title_owners": {}}
        _index_game_owner_keys(side, game)
        self._side_keys = side["key"]
        self.index["key"].update(self._side_keys)

    def add_room(self, room):
        """Fold a just-inserted room into both maps."""
        _index_room_owner_token(self.index, room)
        _index_room_direct(self.direct, room)
        if not self._side_keys:
            return
        for candidate in (
            getattr(room, "key", None),
            internal_room_key(room),
            getattr(room, "vnum", None),
        ):
            text = str(candidate or "").strip()
            if text and text in self._side_keys:
                self.index["key"][text] = self._side_keys[text]

    def note(self, leftover, vnum):
        """Record one alias using the maintained maps (no rebuild, no scan)."""
        note_room_alias(
            self.game, leftover, vnum, index=self.index, direct=self.direct,
        )


def note_room_alias(game, leftover, vnum, *, index=None, direct=None):
    """Record leftover dig name → VNUM for O(1) dual-read (tick-safe).

    Call sites that still write ``room_aliases[name] = vnum`` by hand
    keep exact ``rooms.get`` working; this helper also keeps the
    case-folded index in step so Cadence homeward hops stay O(1).

    Refuses aliases that would map one owner's room key at another
    owner's VNUM (title-fold poisoning).
    """
    if game is None:
        return
    text = str(leftover or "").strip()
    ident = str(vnum or "").strip()
    if not text or not ident or text == ident:
        return
    if _alias_would_poison_owner_boundary(
        game, text, ident, index=index, direct=direct,
    ):
        return
    aliases = _aliases_table(game)
    aliases[text] = ident
    fold = _ensure_alias_fold(game)
    fold[text.lower()] = ident


def drop_room_alias(game, leftover):
    """Drop a leftover name from the alias table and fold (room delete)."""
    if game is None or not leftover:
        return
    text = str(leftover).strip()
    if not text:
        return
    aliases = getattr(game, "room_aliases", None)
    if isinstance(aliases, dict):
        aliases.pop(text, None)
    fold = getattr(game, "_room_alias_fold", None)
    if isinstance(fold, dict):
        fold.pop(text.lower(), None)


def lookup_room(game, key):
    """Find a Room by VNUM identity key or leftover storage key.

    O(1): ``game.rooms`` (RoomMap resolves leftover names, VNUM case-fold,
    and the live alias fold), then the leftover ``room_aliases`` table for
    plain-dict stubs. Does **not** walk ``rooms.values()`` -- that miss
    scan nested inside Cadence homeward BFS after Phase 3 wired every hop
    through this helper. Staff ``goto`` still uses :func:`resolve_room`
    for ROOM NAME scans. Call sites keep using this helper / ``room_keys_match``
    -- do not revert them to raw ``==`` on dig names.
    """
    if game is None or not key:
        return None
    text = str(key).strip()
    if not text:
        return None
    rooms = getattr(game, "rooms", None)
    if not isinstance(rooms, dict):
        return None
    hit = rooms.get(text)
    if hit is not None:
        return hit
    # Production Game uses RoomMap -- get() already resolved leftover
    # names, VNUM case-fold, and the fold. Plain-dict smoke stubs need
    # the same steps without walking rooms.values().
    aliases = getattr(game, "room_aliases", None) or {}
    if isinstance(aliases, dict):
        mapped = aliases.get(text)
        if mapped:
            hit = rooms.get(mapped)
            if hit is not None:
                return hit
    parsed = parse_vnum(text)
    if parsed is not None:
        vnum = format_vnum(*parsed)
        if vnum != text:
            hit = rooms.get(vnum)
            if hit is not None:
                return hit
    mapped = _ensure_alias_fold(game).get(text.lower())
    if mapped:
        hit = rooms.get(mapped)
        if hit is not None:
            return hit
    return None


def canonical_persisted_room_key(game, token) -> str:
    """VNUM identity for a stored room token, else the original string.

    Phase 3 dual-write: boot heal and persist blobs should store the live
    ``internal_room_key`` (VNUM for stamped hand rooms) so the next load
    does not depend on leftover dig names. Unresolvable tokens stay as-is
    (grid cells, missing maps, ephemeral pockets).

    Refuses to hop across an ownership boundary — a poisoned alias must
    not rewrite ``home_room_key`` / ``main_homeroom`` onto another
    player's VNUM on save (bug reports 1531–1533).
    """
    text = str(token or "").strip()
    if not text:
        return token if token is None else text
    index = _build_room_owner_index(game) if game is not None else None
    src_owner = (
        _owner_for_leftover_token(game, text, index=index)
        if game is not None
        else None
    )
    room = lookup_room(game, text)
    if room is None:
        return text
    ident = internal_room_key(room) or (getattr(room, "key", "") or "")
    if not ident or ident == text:
        return text
    tgt_owner = room_owner_identity_token(room, game, index=index)
    if _owners_conflict(src_owner, tgt_owner):
        print(
            f"[boot heal] REFUSE persist canonicalize {text!r} -> {ident!r}: "
            f"source owner {src_owner!r} vs target owner {tgt_owner!r}",
            flush=True,
        )
        return text
    return ident


def room_keys_match(game, left, right) -> bool:
    """True when two room key strings denote the same live Room.

    After Phase-3 VNUM rekey, ``room.key`` is ``BS00002`` while authored
    quests/tutorials still name ``Bunker Library Stacks`` (``legacy_key`` /
    ``room_aliases``). Compare identity, not raw strings.

    Pathfind ``is_goal`` calls this once per BFS node. Both sides must
    stay O(1) -- a miss must not scan the atlas.
    """
    if left is None or right is None:
        return False
    left_s = str(left).strip()
    right_s = str(right).strip()
    if not left_s or not right_s:
        return False
    if left_s == right_s:
        return True
    if game is None:
        return False
    left_room = lookup_room(game, left_s)
    if left_room is None:
        return False
    # Same object via leftover_key / VNUM without a second dict probe when
    # the caller already passed this room's identity or leftover name.
    if right_s == (getattr(left_room, "key", "") or ""):
        return True
    leg = getattr(left_room, "legacy_key", None)
    if leg and str(leg).strip() == right_s:
        return True
    right_room = lookup_room(game, right_s)
    return right_room is not None and left_room is right_room


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

    **Opt-in for unowned map rooms only.** Owned pockets (homestead plots,
    demesnes, shops, townships, realms, dream pockets, street-home owners)
    legitimately reuse template ROOM NAMEs -- never merge them. Two stamped
    VNUM identities are always distinct rooms. Contents never move across an
    ownership boundary; the whole fold is skipped when that would happen.
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
        # Conservative: only positively unowned map hand rooms may fold.
        if not hand_room_title_fold_eligible(game, room):
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
        if not room_has_stamped_vnum(canonical):
            # Canonical must be the VNUM hub we fold into.
            continue
        canon_key = internal_room_key(canonical)
        if not canon_key:
            continue
        for stale, score in scored[1:]:
            if stale is canonical:
                continue
            # Two stamped VNUM identities are different rooms even when the
            # ROOM NAME matches. Only fold leftover dig-key shells into the
            # VNUM hub (The Waystation → TN00001).
            if room_has_stamped_vnum(stale):
                continue
            if not hand_room_title_fold_eligible(game, stale):
                continue
            if _ownership_boundary_blocks_merge(
                canonical, stale, label="hand-room title fold", game=game,
            ):
                continue
            for obj in list(getattr(stale, "contents", ()) or ()):
                if hasattr(obj, "move_to"):
                    obj.move_to(canonical)
                    stats["moved"] += 1
            from engine.char_index import iter_characters
            from engine.world import safe_place

            for occupant in list(iter_characters(game)):
                if getattr(occupant, "location", None) is stale:
                    safe_place(occupant, canonical)
                    stats["moved"] += 1
            _rewire_room_graph_pointers(game, stale, canonical, stats)
            stale_key = getattr(stale, "key", None)
            for dk, dr in list(rooms.items()):
                if dr is stale:
                    rooms.pop(dk, None)
            if stale_key:
                note_room_alias(game, stale_key, canon_key)
            leg = getattr(stale, "legacy_key", None)
            if leg:
                note_room_alias(game, leg, canon_key)
            title = room_name(stale)
            if title:
                note_room_alias(game, title, canon_key)
            stats["removed"] += 1

    return stats


def heal_hand_room_self_loop_exits(game) -> int:
    """Drop compass / in / out exits that point at the room itself.

    Overland virtual cells use placeholder self-loops on purpose -- skip
    those. Homestead and demesne self-loops are also skipped — see
    :func:`_self_loop_heal_skips_room`.
    """
    if game is None:
        return 0
    fixed = 0
    for room in list((getattr(game, "rooms", None) or {}).values()):
        if room is None or not is_hand_room(room):
            continue
        if _self_loop_heal_skips_room(room):
            continue
        if getattr(room, "virtual_overland", False):
            continue
        exits = getattr(room, "exits", None)
        if not isinstance(exits, dict):
            continue
        for direction, dest in list(exits.items()):
            if dest is room:
                exits.pop(direction, None)
                fixed += 1
    return fixed


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
    # Persist dual-read first (leftover dig names still find the room).
    # Staff-facing resolve_room no longer accepts leftover names.
    if name:
        hit = lookup_room(game, name)
        if hit is not None:
            return hit
        room, _ = resolve_room(game, name)
        if room is not None:
            return room
    if vnum:
        hit = lookup_room(game, vnum)
        if hit is not None:
            return hit
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
            note_room_alias(game, name, vnum)
            added += 1
    return added


def heal_homestead_internal_room_titles(game) -> int:
    """Boot heal: replace leaked homestead dig titles like ``tuck:living``.

    VNUM stamp used to copy bare ``owner:room`` keys into ``room.title``
    before rekey (bug report 1139). Idempotent -- skips rooms whose title
    is already a real ROOM NAME.
    """
    if game is None:
        return 0
    from engine.room_naming import (
        authored_title_is_usable,
        derive_room_title_from_context,
        is_internal_place_key,
    )

    healed = 0
    for room in (getattr(game, "rooms", None) or {}).values():
        if room is None or not is_hand_room(room):
            continue
        title = getattr(room, "title", None)
        key = getattr(room, "key", "") or ""
        if not is_internal_place_key(str(title or "")):
            continue
        derived = derive_room_title_from_context(room)
        if not derived or not authored_title_is_usable(derived, key):
            continue
        room.title = derived
        healed += 1
    return healed


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
        # Owned / instance rooms legitimately share template ROOM NAMEs.
        if not hand_room_title_fold_eligible(game, room):
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

    def _remap(value):
        if not value:
            return value, False
        text = str(value).strip()
        new = canonical_persisted_room_key(game, text)
        return new, new != text

    blob_fields = (
        ("home_room_key", "home"),
        ("workplace_room_key", "workplace"),
        ("last_sleep_room_key", "other"),
        ("logout_room_key", "other"),
        ("zone_entry_hub_key", "other"),
        ("haunt_room_key", "other"),
        ("chapel_room_key", "other"),
        ("exile_room_key", "other"),
        ("body_room_key", "other"),
        ("gm_spirit_room_key", "other"),
        ("ashen_origin_room_key", "other"),
        ("gordon_heat_room_key", "other"),
        ("chargen_start_room_key", "other"),
        ("trickster_decoy_room_key", "other"),
        ("site_threshold_room_key", "other"),
        ("worksite_hq_room_key", "other"),
        ("cultivator_rival_last_room_key", "other"),
        ("riftcrash_anchor_room_key", "other"),
        ("mission_portal_key", "other"),
        ("limbo_arrival_origin_key", "other"),
        ("cultivator_cave_heaven_key", "other"),
        ("djinn_real_room_key", "other"),
        ("pet_home_room_key", "other"),
        ("commute_ready_key", "other"),
        ("prison_cell_key", "other"),
        ("site_mask_hub_key", "other"),
        ("fae_banish_return_key", "other"),
        ("dream_return_key", "other"),
        ("arachne_nest_home_key", "other"),
        ("last_earth_loc", "other"),
        ("cultivator_veil_death_key", "other"),
        ("worksite_hq_highway_key", "other"),
        ("worksite_hq_office_key", "other"),
    )
    from engine.char_index import iter_characters

    for char in list(iter_characters(game)):
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
        brief = getattr(char, "mission_brief", None)
        if isinstance(brief, dict) and brief:
            for field in (
                "portal_room",
                "home_room_key",
                "scene_room_key",
                "nest_room_key",
            ):
                raw = brief.get(field)
                new, changed = _remap(raw)
                if changed:
                    brief[field] = new
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
        visited = getattr(char, "visited_room_keys", None)
        if isinstance(visited, list) and visited:
            rebuilt_v = []
            changed_v = False
            for item in visited:
                new, ch = _remap(item)
                rebuilt_v.append(new)
                changed_v = changed_v or ch
            if changed_v:
                char.visited_room_keys = rebuilt_v
                stats["other"] += 1
        shrines = getattr(char, "player_shrine_room_keys", None)
        if isinstance(shrines, list) and shrines:
            rebuilt_s = []
            changed_s = False
            for item in shrines:
                new, ch = _remap(item)
                rebuilt_s.append(new)
                changed_s = changed_s or ch
            if changed_s:
                char.player_shrine_room_keys = rebuilt_s
                stats["other"] += 1
        plots = getattr(char, "farm_plots", None)
        if isinstance(plots, dict) and plots:
            rebuilt_p = {}
            changed_f = False
            for key, plot in plots.items():
                row = dict(plot) if isinstance(plot, dict) else plot
                if isinstance(row, dict) and row.get("room_key"):
                    new_rk, ch = _remap(row.get("room_key"))
                    if ch:
                        row["room_key"] = new_rk
                        changed_f = True
                text = str(key or "")
                room_part, sep, slot = text.partition("#")
                if sep:
                    new_room, ch = _remap(room_part)
                    new_key = f"{new_room}#{slot}"
                    changed_f = changed_f or ch
                else:
                    new_key, ch = _remap(text)
                    changed_f = changed_f or ch
                rebuilt_p[new_key] = row
            if changed_f:
                char.farm_plots = rebuilt_p
                stats["other"] += 1
        assign = getattr(char, "dungeon_assignment", None)
        if isinstance(assign, dict) and assign:
            for field in ("return_room", "portal_room", "portal_entry_key"):
                raw = assign.get(field)
                new, changed = _remap(raw)
                if changed:
                    assign[field] = new
                    stats["other"] += 1
        wild = getattr(char, "wilderness_dungeon_run", None)
        if isinstance(wild, dict) and wild:
            raw = wild.get("origin_room_key")
            new, changed = _remap(raw)
            if changed:
                wild["origin_room_key"] = new
                stats["other"] += 1
    return stats


def heal_persisted_room_key_blobs(game) -> dict:
    """Boot heal: remap legacy room keys on game-level SQLite blobs.

    Extends :func:`heal_character_room_keys` to ``gather_nodes``,
    ``player_shops``, ``townships``, and ``rumor_boards`` tables that store
    ``room_key`` strings outside Character fields.
    """
    stats = {
        "gather_nodes": 0,
        "player_shops": 0,
        "townships": 0,
        "rumor_boards": 0,
    }
    if game is None:
        return stats

    def _remap(value):
        if not value:
            return value, False
        text = str(value).strip()
        new = canonical_persisted_room_key(game, text)
        return new, new != text

    store = getattr(game, "gather_nodes", None)
    if isinstance(store, dict) and store:
        rebuilt = {}
        changed = False
        for room_key, nodes in store.items():
            new_key, ch = _remap(room_key)
            rebuilt[new_key] = nodes
            changed = changed or ch
        if changed:
            game.gather_nodes = rebuilt
            stats["gather_nodes"] = len(rebuilt)

    shops = getattr(game, "player_shops", None)
    if isinstance(shops, dict):
        rooms = getattr(game, "rooms", None) or {}

        def _civic_zone(owner_key):
            owner = str(owner_key or "")
            if owner.startswith("@civic:"):
                return owner[7:]
            return None

        for shop in shops.values():
            if not isinstance(shop, dict):
                continue
            want_zone = _civic_zone(shop.get("owner_key"))
            for field in ("host_room_key", "hub_room_key"):
                raw = shop.get(field)
                new, ch = _remap(raw)
                if not ch:
                    continue
                if want_zone:
                    target = rooms.get(new)
                    if target is not None and (getattr(target, "zone", None) or "") != want_zone:
                        continue
                shop[field] = new
                stats["player_shops"] += 1

    towns = getattr(game, "townships", None)
    if isinstance(towns, dict):
        for town in towns.values():
            if not isinstance(town, dict):
                continue
            for field in ("hub_room_key", "mouth_room_key"):
                raw = town.get(field)
                new, ch = _remap(raw)
                if ch:
                    town[field] = new
                    stats["townships"] += 1
    boards = getattr(game, "rumor_boards", None)
    if isinstance(boards, dict) and boards:
        rebuilt_b = {}
        changed_r = False
        for room_key, posts in boards.items():
            new_key, ch = _remap(room_key)
            rebuilt_b[new_key] = posts
            changed_r = changed_r or ch
        if changed_r:
            game.rumor_boards = rebuilt_b
            stats["rumor_boards"] = len(rebuilt_b)
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
        # Leftover dig names are not VNUMs. ``RoomMap.get`` would still
        # alias them -- that is persist dual-read, not staff addressing.
        return None
    rooms = getattr(game, "rooms", None) or {}
    # Identity keys *are* VNUMs. Use dict.get so RoomMap leftover aliases
    # cannot satisfy a staff VNUM lookup.
    hit = dict.get(rooms, want) if isinstance(rooms, dict) else None
    if hit is not None:
        return hit
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

    Staff ``where`` / partial ``goto`` share this matcher. Underscores
    and hyphens in the query fold to spaces so ``fort_laramie`` hits
    Fort Laramie. Staff addressing is ROOM NAME and VNUM only;
    ``match_key`` stays off on those verbs. Persist dual-read still
    uses :func:`lookup_room` / ``legacy_key`` (never teach leftover names).
    """
    if room is None or not needle:
        return False
    want_raw = str(needle).strip().lower()
    if not want_raw:
        return False
    want = fold_room_needle(needle)
    title_raw = (room_name(room) or "").lower()
    title_folded = fold_room_needle(room_name(room) or "")
    # Folded first: fort_laramie ⊂ Fort Laramie Approach.
    if want and want in title_folded:
        return True
    if want_raw in title_raw:
        return True
    raw_v = getattr(room, "vnum", None)
    if raw_v is not None and str(raw_v).strip():
        try:
            code = validate_vnum(raw_v).lower()
        except ValueError:
            code = str(raw_v).strip().lower()
        if want_raw in code:
            return True
    if match_key:
        key_raw = (getattr(room, "key", "") or "").lower()
        if want_raw in key_raw:
            return True
        key_folded = fold_room_needle(key_raw)
        if want and want in key_folded:
            return True
    return False


def room_on_plane(room, plane) -> bool:
    """True when *room* is on *plane* (empty plane = any)."""
    want = str(plane or "").strip().lower()
    if not want:
        return True
    if room is None:
        return False
    got = str(getattr(room, "plane", None) or "earth").strip().lower()
    return got == want


def iter_scope_rooms(game, *, plane=None):
    """Yield live rooms, optionally restricted to one plane."""
    rooms = getattr(game, "rooms", None) or {}
    for room in rooms.values():
        if room_on_plane(room, plane):
            yield room


def iter_rooms_matching(game, needle, *, match_key=False, plane=None):
    """Every live room matching *needle* (ROOM NAME / VNUM substring).

    Sorted by ROOM NAME then storage key for stable staff lists.
    Optional ``plane`` limits the search (staff ``goto 1861 laramie``).
    """
    want = (needle or "").strip()
    if not want or game is None:
        return []
    hits = [
        room for room in iter_scope_rooms(game, plane=plane)
        if room_matches_needle(room, want, match_key=match_key)
    ]

    def _sort_key(room):
        return (
            (room_name(room) or "").lower(),
            (getattr(room, "key", "") or "").lower(),
        )

    hits.sort(key=_sort_key)
    return hits


def resolve_room(game, query, *, allow_internal_key=False, plane=None):
    """Resolve a staff/query string to a Room (VNUM / ROOM NAME).

    Order:
      1. Exact VNUM (``CA00001``).
      2. Exact ROOM NAME (case-insensitive ``look_title`` / title;
         underscores/hyphens fold to spaces). A unique exact title that
         is also a substring of another ROOM NAME is still ambiguous
         (``Laramie Approach`` vs ``Fort Laramie Approach``).
      3. Unique partial ROOM NAME / VNUM substring (case-insensitive).
      4. Exact leftover dig name / ``legacy_key`` -- **off** for staff
         (default ``allow_internal_key=False``). Persist and Cadence
         pass ``True`` so old saved homes still resolve.

    Optional ``plane`` scopes every step (staff ``goto 1861 laramie``).

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
    if by_vnum is not None and room_on_plane(by_vnum, plane):
        return by_vnum, "vnum"
    rooms = getattr(game, "rooms", None) or {}
    lowered = text.lower()
    # A well-formed bare VNUM that find_room_by_vnum just missed cannot be a
    # ROOM NAME -- player-facing names never contain a raw code (hard rule 7).
    # Without this, every lookup of a vnum in an unloaded/deferred zone paid
    # two full world scans (exact title, then partial) to find nothing: 26
    # such calls cost ~3s of boot. Steps 2-3 are skipped; the internal-key
    # step below still runs, so a room whose key / legacy_key / alias is
    # literally that code resolves exactly as before.
    query_is_bare_vnum = label_is_bare_vnum(text)
    folded_query = "" if query_is_bare_vnum else fold_room_needle(text)
    name_hits = []
    if folded_query:
        for room in iter_scope_rooms(game, plane=plane):
            if fold_room_needle(room_name(room)) == folded_query:
                name_hits.append(room)
    if len(name_hits) == 1:
        # Exact unique title still collides when a longer name contains
        # it -- staff typed "laramie approach" meaning the fort mouth.
        siblings = [
            room for room in iter_rooms_matching(
                game, text, match_key=False, plane=plane,
            )
            if room is not name_hits[0]
        ]
        if siblings:
            return None, "ambiguous_name"
        return name_hits[0], "room_name"
    if len(name_hits) > 1:
        # Shared ROOM NAMES must use VNUM -- do not fall through to a
        # coincidental internal-key match and silently pick the wrong room.
        return None, "ambiguous_name"
    # Unique partial ROOM NAME / VNUM substring (goto Town Park, etc.).
    if not query_is_bare_vnum:
        partial_hits = iter_rooms_matching(
            game, text, match_key=False, plane=plane,
        )
        if len(partial_hits) == 1:
            return partial_hits[0], "partial_name"
        if len(partial_hits) > 1:
            return None, "ambiguous_name"
    if allow_internal_key:
        # Direct dict hit (qualified keys, legacy dig targets, RoomMap aliases).
        hit = rooms.get(text)
        if hit is not None and room_on_plane(hit, plane):
            return hit, "internal_key"
        for room in iter_scope_rooms(game, plane=plane):
            if (getattr(room, "key", "") or "").lower() == lowered:
                return room, "internal_key"
            leg = getattr(room, "legacy_key", None)
            if leg and str(leg).lower() == lowered:
                return room, "internal_key"
        # Game-level alias table (Phase 3 dual-read).
        aliases = getattr(game, "room_aliases", None) or {}
        mapped = aliases.get(text)
        if mapped and mapped in rooms:
            aliased = rooms[mapped]
            if room_on_plane(aliased, plane):
                return aliased, "internal_key"
        for aka, vnum in aliases.items():
            if str(aka).lower() == lowered and vnum in rooms:
                aliased = rooms[vnum]
                if room_on_plane(aliased, plane):
                    return aliased, "internal_key"
    return None, None


def _ambiguous_name_tip(game, query, *, plane=None) -> str:
    """Staff tip listing VNUMs / map ids for rooms matching *query*."""
    text = (query or "").strip()
    # List every substring hit, not exact-only -- a unique exact title can
    # still collide with a longer name (Laramie Approach vs Fort Laramie
    # Approach). Exact-only would hide the longer mouth.
    hits = iter_rooms_matching(game, text, match_key=False, plane=plane)
    labels = []
    for room in hits[:12]:
        label = staff_match_label(room)
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


def resolve_room_or_error(game, query, *, allow_internal_key=False, plane=None):
    """Resolve for staff verbs -- returns ``(room, None)`` or ``(None, err)``.

    Staff addressing is **ROOM NAME** and **VNUM** (partial name OK when
    unique). Leftover dig names are not a staff address; persist dual-read
    stays on :func:`lookup_room`. Prefer this over bare
    :func:`resolve_room` when the caller sends a tip to a GM.
    Optional ``plane`` scopes the search like :func:`resolve_room`.
    """
    text = (query or "").strip()
    if not text:
        return None, "Name a room by VNUM (e.g. MT00002) or unique ROOM NAME."
    room, how = resolve_room(
        game, text, allow_internal_key=allow_internal_key, plane=plane,
    )
    if room is not None:
        return room, None
    if how == "ambiguous_name":
        return None, _ambiguous_name_tip(game, text, plane=plane)
    return None, (
        f"No room matching VNUM or ROOM NAME {text!r}. "
        "Try `gm where room <text>` or `gm goto <vnum>`."
    )
