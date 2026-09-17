"""
item_inum.py -- Item instance INUM helpers (letter prefix + 5 digits).

An INUM is a staff-facing instance id like ``AD00001`` derived from an
item's **short desc** (``Item.key``): first letter + last letter, both
uppercase, plus a zero-padded sequence under that prefix. Same shape as
room VNUMs and character CNUMs, but a **separate namespace** -- room
``CA00001``, character ``CA00001``, and item ``CA00001`` may all exist.

Players never see INUMs (same as CNUM / VNUM). Do not put INUMs into
``holder_key`` (that column also stores room keys). Do not reuse phone
``Item.uid`` (hex uuid, phones-only).

Prefix source matches rooms (first + last of the display name), not
character CNUMs (first + third of the given name). Short names repeat the
letter (``A`` → ``AA``). No letters → ``XX``.

Engine-pure: no ``supers`` imports.
"""

from __future__ import annotations

import re

from engine.room_vnum import letter_prefix

# Human form: two A–Z letters + exactly five decimal digits.
_INUM_RE = re.compile(r"^([A-Z]{2})(\d{5})$")

# Fallback when the short desc has no A–Z letters at all.
_FALLBACK_PREFIX = "XX"

# Per-prefix sequence ceiling (5 digits).
_MAX_SEQ = 99999

# Process-lifetime taken set so make_world_item / persist dual-write can
# allocate without a Game. Load restore notes saved tags into this set.
_PROCESS_TAKEN: set[str] = set()

# Per-prefix "next sequence worth trying" hint for the process set above.
# Membership in ``_PROCESS_TAKEN`` still decides what is free; the cursor
# only skips numbers already proven taken. Without it, stamping a whole
# world rescans 1.. for every copy, which is quadratic (a 14-minute boot
# on the Azure playtest world before this hint existed). The set only ever
# grows in a live process, so a number below the cursor can never free up.
_PROCESS_CURSOR: dict[str, int] = {}


def process_taken() -> set[str]:
    """Mutable process-lifetime INUM set (spawn + persist dual-write)."""
    return _PROCESS_TAKEN


def inum_prefix(name: str) -> str:
    """First + last A–Z letters of ``name``, both uppercase.

    Same helper as room VNUMs (``engine.room_vnum.letter_prefix``).
    """
    prefix = letter_prefix(name)
    return prefix or _FALLBACK_PREFIX


def item_display_name(item) -> str:
    """Short desc used for prefixing -- ``Item.key``, else catalog id."""
    key = str(getattr(item, "key", None) or "").strip()
    if key:
        return key
    catalog = str(getattr(item, "catalog_id", None) or "").strip()
    if catalog:
        return catalog
    return "item"


def format_inum(prefix: str, n: int) -> str:
    """Build ``AD00001`` from a two-letter prefix and sequence number."""
    pref = str(prefix or "").strip().upper()
    if len(pref) != 2 or not pref.isalpha() or not pref.isascii():
        raise ValueError(f"inum prefix must be two A–Z letters, got {prefix!r}")
    if not isinstance(n, int) or n < 1 or n > _MAX_SEQ:
        raise ValueError(f"inum sequence must be 1..{_MAX_SEQ}, got {n!r}")
    return f"{pref}{n:05d}"


def parse_inum(s) -> tuple[str, int] | None:
    """Return ``(prefix, n)`` for a valid INUM string, else ``None``."""
    if s is None:
        return None
    text = str(s).strip().upper()
    match = _INUM_RE.fullmatch(text)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def validate_inum(s) -> str:
    """Normalize and validate; raise ``ValueError`` if malformed."""
    parsed = parse_inum(s)
    if parsed is None:
        raise ValueError(
            f"invalid item INUM {s!r} -- expected two A–Z letters + "
            f"5 digits (e.g. AD00001)"
        )
    prefix, n = parsed
    return format_inum(prefix, n)


def persist_inum_text(item) -> str | None:
    """Validated INUM string for the container blob, or None."""
    raw = getattr(item, "inum", None) if item is not None else None
    if not isinstance(raw, str) or not str(raw).strip():
        return None
    try:
        return validate_inum(raw)
    except ValueError:
        return None


def note_inum(inum) -> str | None:
    """Add a validated INUM to the process taken set. Returns the tag."""
    if inum is None or str(inum).strip() == "":
        return None
    try:
        code = validate_inum(inum)
    except ValueError:
        return None
    _PROCESS_TAKEN.add(code)
    return code


def _clean_prefix(prefix: str) -> str:
    """Validate a two-letter prefix for allocation."""
    pref = str(prefix or "").strip().upper()
    if len(pref) != 2 or not all("A" <= ch <= "Z" for ch in pref):
        raise ValueError(f"inum prefix must be two A–Z letters, got {prefix!r}")
    return pref


def _scan_free_inum(pref: str, used, start: int) -> tuple[str, int]:
    """First free ``PREFIX#####`` at or after ``start``; also return its n.

    ``used`` must support ``in`` against normalized (uppercase) tags.
    """
    for n in range(max(1, start), _MAX_SEQ + 1):
        candidate = format_inum(pref, n)
        if candidate not in used:
            return candidate, n
    raise ValueError(
        f"no free INUM left under prefix {pref!r} (1..{_MAX_SEQ} exhausted)"
    )


def _normalized_pool(taken):
    """Membership pool for allocation, without rebuilding big sets.

    Sets come from ``collect_taken_inums`` / ``_PROCESS_TAKEN`` / the boot
    heal, which only ever add tags that already passed ``validate_inum``,
    so they are normalized by construction and can be used as-is. Ad-hoc
    lists/tuples from callers are small, so normalizing those is cheap.
    Rebuilding on every call is what made bulk stamping quadratic.
    """
    if taken is None:
        return frozenset()
    if isinstance(taken, (set, frozenset)):
        return taken
    return {str(v).strip().upper() for v in taken if v}


def next_inum(prefix: str, taken: set[str]) -> str:
    """Allocate the lowest free ``PREFIX#####`` under ``prefix``.

    ``taken`` holds already-used INUM strings. Raises ``ValueError`` if the
    5-digit space is exhausted. Bulk callers (boot heal, item spawn) go
    through the cursor-aware paths below instead of rescanning from 1.
    """
    pref = _clean_prefix(prefix)
    code, _n = _scan_free_inum(pref, _normalized_pool(taken), 1)
    return code


def _next_inum_cursored(prefix: str, taken, cursor: dict) -> str:
    """Cursor-aware allocation for bulk stamping (amortized O(1) per item)."""
    pref = _clean_prefix(prefix)
    code, n = _scan_free_inum(pref, _normalized_pool(taken), cursor.get(pref, 1))
    cursor[pref] = n + 1
    return code


def collect_taken_inums(items) -> set[str]:
    """Gather validated INUM strings from Item objects."""
    taken: set[str] = set()
    for item in items or ():
        code = persist_inum_text(item)
        if code:
            taken.add(code)
    return taken


def allocate_inum(name: str, *, taken: set[str]) -> str:
    """Derive prefix from the short desc and return the next free INUM."""
    prefix = inum_prefix(name)
    return next_inum(prefix, taken)


def ensure_item_inum(item, *, taken: set[str] | None = None) -> str:
    """Stamp a unique INUM on ``item`` if missing; return the tag.

    ``taken`` defaults to the process-lifetime set. Mutates ``item.inum``.
    """
    pool = _PROCESS_TAKEN if taken is None else taken
    existing = persist_inum_text(item)
    if existing:
        item.inum = existing
        pool.add(existing)
        _PROCESS_TAKEN.add(existing)
        return existing
    if taken is None:
        # Default (spawn / persist dual-write) path: ride the process
        # cursor so a world with tens of thousands of copies under one
        # prefix does not rescan the whole range per new item.
        code = _next_inum_cursored(
            inum_prefix(item_display_name(item)), pool, _PROCESS_CURSOR,
        )
    else:
        code = allocate_inum(item_display_name(item), taken=pool)
    item.inum = code
    pool.add(code)
    _PROCESS_TAKEN.add(code)
    return code


def apply_saved_inum(item, raw) -> str | None:
    """Restore a blob INUM onto ``item`` (invalid tags become missing)."""
    if item is None:
        return None
    if raw is None or str(raw).strip() == "":
        return None
    try:
        code = validate_inum(raw)
    except ValueError:
        item.inum = None
        return None
    item.inum = code
    note_inum(code)
    return code


def staff_item_label(item) -> str:
    """GM prose: ``short desc[INUM]`` when a valid instance tag exists.

    Players must never see this form -- ordinary look uses ``item.key``.
    """
    name = item_display_name(item) if item is not None else ""
    code = persist_inum_text(item)
    if not code:
        return name
    return f"{name}[{code}]"


def _is_item(obj) -> bool:
    """Duck-type an engine Item (avoid importing world at module load)."""
    if obj is None:
        return False
    from engine.world import Item

    return isinstance(obj, Item)


def walk_item_tree(item, *, _seen=None):
    """Yield ``item`` plus nested bag / wallet / pocket / loot Items."""
    if not _is_item(item):
        return
    seen = _seen if _seen is not None else set()
    iid = id(item)
    if iid in seen:
        return
    seen.add(iid)
    yield item
    for attr in ("bag_contents", "wallet_contents", "clothing_contents"):
        for sub in getattr(item, attr, None) or ():
            if _is_item(sub):
                yield from walk_item_tree(sub, _seen=seen)
    for entry in getattr(item, "loot", None) or ():
        if _is_item(entry):
            yield from walk_item_tree(entry, _seen=seen)


def iter_live_item_placements(game):
    """Yield ``(item, character_or_none, room)`` for every live Item.

    Floor items have ``character is None``. Carried / nested bag rows
    stamp the holding Character so persist can dirty the owner.
    """
    seen: set[int] = set()

    def emit(root, char, room):
        for node in walk_item_tree(root):
            iid = id(node)
            if iid in seen:
                continue
            seen.add(iid)
            yield node, char, room

    rooms = getattr(game, "rooms", None) or {}
    for room in rooms.values():
        for obj in getattr(room, "contents", ()) or ():
            if _is_item(obj):
                yield from emit(obj, None, room)

    for ch in list(getattr(game, "characters", None) or []):
        room = getattr(ch, "location", None)
        for attr in ("inventory", "gear_bag", "home_stash"):
            for obj in getattr(ch, attr, None) or ():
                if _is_item(obj):
                    yield from emit(obj, ch, room)


def find_item_by_inum(game, inum):
    """Return ``(item, room, character_or_none)`` for an exact INUM, or None."""
    try:
        want = validate_inum(inum)
    except ValueError:
        return None
    for item, char, room in iter_live_item_placements(game):
        code = persist_inum_text(item)
        if code == want:
            return item, room, char
    return None


def _inum_collision_winner(rows):
    """Keep the oldest created_seq when two items share a tag."""
    return sorted(
        rows,
        key=lambda t: (
            int(getattr(t[0], "created_seq", 0) or 0),
            id(t[0]),
        ),
    )[0]


def heal_unique_item_inums(game) -> dict:
    """Make every live Item INUM valid and unique. Collisions keep the prefix.

    The oldest ``created_seq`` keeps the colliding tag; later copies get
    the next free number under that prefix. Invalid tags are treated as
    missing. Idempotent. Returns ``stamped`` / ``reallocated`` /
    ``changed`` (``(item, character, room)`` tuples) so persist can dirty
    holders.
    """
    stats = {"stamped": 0, "reallocated": 0, "changed": []}
    if game is None:
        return stats
    # First boot on a large world can stamp tens of thousands of copies.
    # Touch the watcher stamp so a slow heal is not mistaken for a freeze
    # (hang timeout is 120s *after* boot grace).
    try:
        from engine import game_heartbeat

        game_heartbeat.touch_heartbeat("item_inum_heal")
    except Exception:
        pass
    placements = list(iter_live_item_placements(game))
    groups = {}
    for item, char, room in placements:
        raw = getattr(item, "inum", None)
        parsed = None
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = validate_inum(raw)
            except ValueError:
                parsed = None
        if parsed is None:
            if raw not in (None, ""):
                item.inum = None
            continue
        if parsed != str(raw).strip().upper():
            item.inum = parsed
            stats["changed"].append((item, char, room))
        groups.setdefault(parsed, []).append((item, char, room))

    taken: set[str] = set()
    prefer_prefix = {}
    for inum, rows in groups.items():
        winner = _inum_collision_winner(rows)
        taken.add(inum)
        parsed = parse_inum(inum)
        prefix = parsed[0] if parsed else None
        for extra in rows:
            item, char, room = extra
            if extra[0] is winner[0]:
                item.inum = inum
                continue
            item.inum = None
            if prefix:
                prefer_prefix[id(item)] = prefix
            stats["changed"].append((item, char, room))

    # One cursor for the whole sweep: every clear above already happened,
    # so numbers below a prefix's cursor stay taken and never need a
    # rescan. Seeded from the process cursor so a second sweep in the same
    # process does not walk ground the first one already covered.
    cursor = dict(_PROCESS_CURSOR)
    for idx, (item, char, room) in enumerate(placements):
        if persist_inum_text(item):
            continue
        prefix = prefer_prefix.get(id(item))
        if prefix:
            item.inum = _next_inum_cursored(prefix, taken, cursor)
            stats["reallocated"] += 1
        else:
            item.inum = _next_inum_cursored(
                inum_prefix(item_display_name(item)), taken, cursor,
            )
            stats["stamped"] += 1
        taken.add(item.inum)
        _PROCESS_TAKEN.add(item.inum)
        stats["changed"].append((item, char, room))
        if idx and idx % 500 == 0:
            try:
                from engine import game_heartbeat

                game_heartbeat.touch_heartbeat("item_inum_heal")
            except Exception:
                pass
    for code in taken:
        _PROCESS_TAKEN.add(code)
    # Hand the sweep's progress to later spawns (same reason as above).
    for pref, nxt in cursor.items():
        if nxt > _PROCESS_CURSOR.get(pref, 1):
            _PROCESS_CURSOR[pref] = nxt
    try:
        from engine import game_heartbeat

        game_heartbeat.touch_heartbeat("item_inum_heal_done")
    except Exception:
        pass
    return stats


def remint_inum_occupants_for_items(game, character) -> list[str]:
    """Keep restored gear INUMs; remint any other live copy on the same tag.

    Boot ``heal_unique_item_inums`` keeps the oldest ``created_seq``, which
    would steal a restored item's archived INUM after a wipe+respawn. Restore
    wants the opposite: the body coming back from archive keeps the tag.
    """
    notes = []
    if game is None or character is None:
        return notes
    keep_ids = set()
    keep_items = []
    for attr in ("inventory", "gear_bag", "home_stash"):
        for obj in getattr(character, attr, None) or ():
            for node in walk_item_tree(obj):
                keep_ids.add(id(node))
                keep_items.append(node)
    if not keep_items:
        return notes
    taken = collect_taken_inums(
        node for node, _char, _room in iter_live_item_placements(game)
    )
    cursor = dict(_PROCESS_CURSOR)
    for keep in keep_items:
        code = persist_inum_text(keep)
        if not code:
            continue
        for item, char, room in iter_live_item_placements(game):
            if id(item) in keep_ids:
                continue
            if persist_inum_text(item) != code:
                continue
            parsed = parse_inum(code)
            prefix = parsed[0] if parsed else inum_prefix(item_display_name(item))
            item.inum = None
            new = _next_inum_cursored(prefix, taken, cursor)
            item.inum = new
            taken.add(new)
            _PROCESS_TAKEN.add(new)
            notes.append(f"{item_display_name(item)} {code} -> {new}")
            print(
                f"[inum] remint occupant {item_display_name(item)} "
                f"{code} -> {new} so restored gear keeps the tag",
                flush=True,
            )
            if char is not None:
                try:
                    from engine.persistence import mark_character_dirty

                    mark_character_dirty(game, char, force=True)
                except Exception:
                    pass
            elif room is not None:
                try:
                    from engine.persistence import mark_floor_room_dirty

                    mark_floor_room_dirty(game, room)
                except Exception:
                    pass
    for pref, nxt in cursor.items():
        if nxt > _PROCESS_CURSOR.get(pref, 1):
            _PROCESS_CURSOR[pref] = nxt
    return notes
