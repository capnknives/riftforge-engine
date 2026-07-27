"""
room_naming.py -- structured player-facing ROOM NAME helpers (engine).

Canonical ROOM NAME shape for towns, cities, and Earth sites::

    [City] - [Main] - [Sub]

Examples::

    Lebanon - Hotel - Lobby
    Lebanon - 12215 Campbell Pass - Living
    Blue Earth - Besieged Parish - Nave

Two-part titles (``City - Main``) are fine when there is no sub-room.
Internal JSON ``key`` values are never rewritten here -- only ``title``.

Game-specific city labels for map ids live in ``supers.room_naming``.
"""

from __future__ import annotations

import re

# Interior / unit labels that mark a third segment (not a street hub).
ROOM_SUB_LABELS = frozenset({
    "living", "bedroom", "kitchen", "bathroom", "den", "porch",
    "backyard", "office", "guest bedroom", "lobby", "front desk",
    "entrance", "reception",
})

# Strip leading ``map:`` / ``lebanon:`` scopes from keys when deriving.
_SCOPE_RE = re.compile(r"^[^:]+:")

# Cadence dig keys -- storage graph ids, never player ROOM NAMEs.
# Matches ``unowned shop4``, ``unowned amenity1``, ``lawrence:unowned shop2``.
_OPAQUE_CADENCE_KEY_RE = re.compile(
    r"^(?:[^:]+:)?unowned (?:shop|amenity)\d+\b",
    re.IGNORECASE,
)

# Job id → generic Main segment when no authored title is set.
# Keep ids as plain strings so the engine never imports supers.jobs.
_JOB_GENERIC_MAIN = {
    "laundry_hand": "Laundry",
    "cook": "Diner",
    "hotel_clerk": "Hotel Desk",
    "bartender": "Bar",
    "brimstone_barkeep": "Bar",
    "mechanic": "Garage",
    "gas_clerk": "Gas Station",
    "grocer": "Grocery",
    "pawnbroker": "Pawn Shop",
    "post_clerk": "Post Office",
    "town_librarian": "Library",
    "ledger_clerk": "Bank",
    "clinic_aide": "Clinic",
    "gravedigger": "Graveyard",
    "deputy": "Sheriff Office",
    "ash_drill_keeper": "Gym",
    "radio_host": "Radio Station",
    "newsie": "News Stand",
}

# Resource tags → generic Main (first match in this order wins).
# More specific civic tags before broad food/vendor/work.
_RESOURCE_GENERIC_MAIN = (
    ("bank", "Bank"),
    ("library", "Library"),
    ("bar", "Bar"),
    ("nightlife", "Bar"),
    ("training", "Gym"),
    ("blood", "Blood Den"),
    ("hygiene", "Laundry"),
    ("food", "Diner"),
    ("vendor", "Shop"),
    ("sleep", "Lodging"),
    ("work", "Workplace"),
)


def bare_key(key: str) -> str:
    """Return the unscoped storage key (drop ``lebanon:`` prefix)."""
    text = str(key or "").strip()
    return _SCOPE_RE.sub("", text)


def is_opaque_storage_key(key: str) -> bool:
    """True for Cadence dig keys players must never see as ROOM NAMEs.

    Examples: ``unowned shop4``, ``unowned amenity1``,
    ``lawrence:unowned amenity3``. Authored titles and ordinary dig names
    return False.
    """
    text = str(key or "").strip()
    if not text:
        return False
    return bool(_OPAQUE_CADENCE_KEY_RE.match(text))


def generic_main_from_flags(room) -> str | None:
    """Guess a short place Main (Laundry, Diner, …) from room flags.

    Uses boolean stamps (hospital / mechanic), ``jobs`` ids, then
    ``resources`` tags. Returns None when nothing useful is stamped --
    callers fall back to Shop / Amenity from the opaque key shape.
    """
    if room is None:
        return None
    if getattr(room, "hospital", False):
        return "Hospital"
    if getattr(room, "mechanic", False):
        return "Mechanic Shop"
    jobs = getattr(room, "jobs", None) or ()
    for job_id in jobs:
        main = _JOB_GENERIC_MAIN.get(str(job_id or "").strip())
        if main:
            return main
    tags = {
        str(t or "").strip().lower()
        for t in (getattr(room, "resources", None) or ())
        if t
    }
    # Food + vendor without a cook job reads as grocery more often than diner.
    if "food" in tags and "vendor" in tags and "cook" not in {
        str(j or "").strip() for j in jobs
    }:
        # Prefer grocery when water is also stocked (Lebanon grocery pattern).
        if "water" in tags and "social" not in tags:
            return "Grocery"
    for tag, main in _RESOURCE_GENERIC_MAIN:
        if tag in tags:
            return main
    return None


def generic_title_from_flags(room) -> str | None:
    """Invent a player ROOM NAME from flags when dig left no authored title.

    Prefers ``City - Main`` when ``room.city_name`` is stamped; otherwise
    a bare Main (``Laundry``). Opaque ``unowned shopN`` / ``amenityN``
    keys without useful flags become ``Shop`` / ``Civic Building``.
    Returns None only when ``room`` is missing.
    """
    if room is None:
        return None
    main = generic_main_from_flags(room)
    if not main:
        bare = bare_key(getattr(room, "key", "") or "").lower()
        if bare.startswith("unowned shop"):
            main = "Shop"
        elif bare.startswith("unowned amenity"):
            main = "Civic Building"
        else:
            main = "Shop"
    city = str(getattr(room, "city_name", None) or "").strip()
    if city:
        try:
            return structured_title(city, main)
        except ValueError:
            return main
    return main


def authored_title_is_usable(title: str, key: str = "") -> bool:
    """True when ``title`` is a real ROOM NAME (not blank / opaque dig key).

    Treats a title that merely repeats an opaque storage key as unusable
    so look / work fall through to the flag-based generic.
    """
    text = str(title or "").strip()
    if not text:
        return False
    if is_opaque_storage_key(text):
        return False
    key_s = str(key or "").strip()
    if key_s and text == key_s and is_opaque_storage_key(key_s):
        return False
    return True


def structured_title(city: str, main: str, sub: str | None = None) -> str:
    """Build a player-facing ROOM NAME: City - Main [- Sub].

    Empty ``sub`` yields a two-part title. Segments are stripped; empty
    city/main raise ``ValueError`` so callers fail loud at author time.
    """
    city_s = str(city or "").strip()
    main_s = str(main or "").strip()
    if not city_s or not main_s:
        raise ValueError("structured_title needs non-empty city and main")
    parts = [city_s, main_s]
    sub_s = str(sub or "").strip()
    if sub_s:
        parts.append(sub_s)
    return " - ".join(parts)


def split_structured_title(title: str) -> tuple[str, str, str | None]:
    """Split a ROOM NAME into (city, main, sub_or_None).

    If there are fewer than two `` - `` segments, city is ``""`` and the
    whole string is main (legacy / unstructured titles).
    """
    text = str(title or "").strip()
    if not text:
        return ("", "", None)
    parts = [p.strip() for p in text.split(" - ") if p.strip()]
    if len(parts) >= 3:
        return (parts[0], parts[1], " - ".join(parts[2:]))
    if len(parts) == 2:
        return (parts[0], parts[1], None)
    return ("", parts[0], None)


def title_leaf(title: str) -> str:
    """Last `` - ``-separated segment (street leaf, floor label, …)."""
    _city, main, sub = split_structured_title(title)
    if sub:
        return sub
    if main:
        return main
    return str(title or "").strip()


def strip_city_prefix(title: str, city: str | None = None) -> str:
    """Drop a leading ``City - `` for populate / suffix matching.

    When ``city`` is given, only that city is stripped (case-insensitive).
    Otherwise any first segment before `` - `` is dropped when present.
    """
    text = str(title or "").strip()
    if " - " not in text:
        return text
    city_part, rest = text.split(" - ", 1)
    if city is not None and city_part.strip().lower() != str(city).strip().lower():
        return text
    return rest.strip()


def street_hub_leaf(title: str) -> str:
    """Street name used by ``populate homes`` (no house number / room sub).

    ``Lebanon - Campbell Pass`` → ``Campbell Pass``
    ``Lebanon - 12215 Campbell Pass - Living`` → ``''`` (not a hub)
    """
    text = str(title or "").strip()
    if not text:
        return ""
    _city, main, sub = split_structured_title(text)
    # Three-part with a room sub or numbered address main → not a hub.
    if sub is not None:
        leaf = sub.strip().lower()
        if leaf in ROOM_SUB_LABELS or re.match(r"^\d+\b", main or ""):
            return ""
        if re.match(r"^\d+\b", main or ""):
            return ""
    candidate = main if _city else text
    if not _city:
        candidate = text
    candidate = str(candidate or "").strip()
    if re.match(r"^\d+\s+", candidate):
        return ""
    return candidate


def strip_address_from_exit_label(direction: str, title: str) -> str:
    """When the exit verb is a house number, drop that number from the label.

    Legacy: ``12223 Campbell Pass`` → ``Campbell Pass``
    Structured: ``Lebanon - 12223 Campbell Pass - Porch`` →
    ``Lebanon - Campbell Pass - Porch``
    """
    title = str(title or "").strip()
    direction = str(direction or "").strip()
    if not direction.isdigit() or not title:
        return title
    prefix = f"{direction} "
    if title.startswith(prefix):
        return title[len(prefix):].strip() or title
    parts = [p.strip() for p in title.split(" - ")]
    if len(parts) >= 2 and parts[1].startswith(prefix):
        parts[1] = parts[1][len(prefix):].strip() or parts[1]
        return " - ".join(parts)
    return title


def ensure_city_prefix(title: str, city: str) -> str:
    """If ``title`` lacks this city prefix, prepend ``City - ``."""
    text = str(title or "").strip()
    city_s = str(city or "").strip()
    if not text or not city_s:
        return text
    if text.lower().startswith(city_s.lower() + " - "):
        return text
    if text.lower().startswith(city_s.lower() + " "):
        rest = text[len(city_s):].strip()
        return structured_title(city_s, rest) if rest else city_s
    return structured_title(city_s, text)


# ---------------------------------------------------------------------------
# Zone city color meta + sighted paint (docs/AREA_BUILDING.md)
# ---------------------------------------------------------------------------

# Default Main-segment roles by place kind (zone JSON may override).
DEFAULT_MAIN_COLORS = {
    "street": "silver",
    "highway": "gold",
    "lodging": "gold",
    "home": "teal",
    "civic": "absinthe_green",
    "hospital": "dark_red",
    "sewer": "slate_grey",
    "default": "silver",
}

DEFAULT_CITY_COLOR = "dark_grey"
DEFAULT_SUB_COLOR = "slate_grey"

_ORDINALS = (
    "", "First", "Second", "Third", "Fourth", "Fifth",
    "Sixth", "Seventh", "Eighth", "Ninth", "Tenth",
    "Eleventh", "Twelfth", "Thirteenth", "Fourteenth", "Fifteenth",
)

_BEARING_WORD = {
    "N": "North", "S": "South", "E": "East", "W": "West",
}


def parse_city_meta(data: dict | None) -> dict:
    """Normalize zone/map top-level city naming + color fields.

    Returns a dict with ``city_name``, ``city_color``, ``sub_color``,
    ``main_colors`` (merged over defaults). Missing city_name → ``""``.
    """
    data = data if isinstance(data, dict) else {}
    main_colors = dict(DEFAULT_MAIN_COLORS)
    raw_main = data.get("main_colors")
    if isinstance(raw_main, dict):
        for key, role in raw_main.items():
            kind = str(key or "").strip().lower()
            role_s = str(role or "").strip()
            if kind and role_s:
                main_colors[kind] = role_s
    city_name = str(data.get("city_name") or "").strip()
    city_color = str(data.get("city_color") or DEFAULT_CITY_COLOR).strip()
    sub_color = str(data.get("sub_color") or DEFAULT_SUB_COLOR).strip()
    return {
        "city_name": city_name,
        "city_color": city_color or DEFAULT_CITY_COLOR,
        "sub_color": sub_color or DEFAULT_SUB_COLOR,
        "main_colors": main_colors,
    }


def infer_main_kind(main: str, *, room=None, key: str = "") -> str:
    """Guess place kind for Main-segment color (street / lodging / …)."""
    blob = " ".join(
        str(x or "") for x in (
            main,
            key,
            getattr(room, "key", None) if room is not None else "",
            getattr(room, "title", None) if room is not None else "",
        )
    ).lower()
    if room is not None:
        if getattr(room, "hospital", False):
            return "hospital"
        if getattr(room, "is_hotel_room", False):
            return "lodging"
        if getattr(room, "is_home", False) or getattr(room, "is_house", False):
            return "home"
        if getattr(room, "private_home", False) and re.search(
            r"\d{5}", blob
        ):
            return "home"
    if "sewer" in blob or "nest" in blob and "rat" not in blob:
        return "sewer"
    if "hospital" in blob or "clinic" in blob and "walk-in" in blob:
        return "hospital"
    if "hospital" in blob:
        return "hospital"
    if any(w in blob for w in ("hotel", "motel", "lodging")):
        return "lodging"
    if "highway" in blob:
        return "highway"
    if re.search(r"\b\d{5}\b", blob) or "apartment" in blob:
        return "home"
    if any(
        w in blob for w in (
            "street", "pass", "parkway", "pike", "square", "lane",
            "avenue", "boulevard", "drive", "road", "way", "court",
        )
    ):
        # Town Square / civic "Square" without street → civic unless Main
        # Street / Valley Square hub.
        if main and main.strip().lower() in ("town square",):
            return "civic"
        return "street"
    if any(
        w in blob for w in (
            "grocery", "diner", "library", "gym", "bank", "post",
            "laundry", "pawn", "garage", "sheriff", "park", "graveyard",
            "bar", "shop", "pet supply", "radio", "storm watch",
        )
    ):
        return "civic"
    return "default"


def main_color_for(kind: str, main_colors: dict | None = None) -> str:
    """Resolve a Main color role from kind + optional zone overrides."""
    colors = main_colors if isinstance(main_colors, dict) else DEFAULT_MAIN_COLORS
    kind_s = str(kind or "default").strip().lower() or "default"
    return (
        colors.get(kind_s)
        or colors.get("default")
        or DEFAULT_MAIN_COLORS["default"]
    )


def meta_from_room(room, game=None) -> dict:
    """City paint meta for a live Room (room attrs, else map_registry)."""
    base = parse_city_meta({})
    if room is None:
        return base
    # Prefer per-room stamps from the loader.
    city_name = str(getattr(room, "city_name", None) or "").strip()
    city_color = str(getattr(room, "city_color", None) or "").strip()
    sub_color = str(getattr(room, "sub_color", None) or "").strip()
    main_colors = getattr(room, "main_colors", None)
    if city_name or city_color or isinstance(main_colors, dict):
        merged = dict(DEFAULT_MAIN_COLORS)
        if isinstance(main_colors, dict):
            merged.update({
                str(k).lower(): str(v)
                for k, v in main_colors.items()
                if k and v
            })
        return {
            "city_name": city_name,
            "city_color": city_color or DEFAULT_CITY_COLOR,
            "sub_color": sub_color or DEFAULT_SUB_COLOR,
            "main_colors": merged,
        }
    # Fallback: Game.map_registry / maps.LAST_MAP_REGISTRY.
    map_id = getattr(room, "map_id", None)
    registry = None
    if game is not None:
        registry = getattr(game, "map_registry", None)
    if not registry:
        try:
            import maps as maps_mod
            registry = getattr(maps_mod, "LAST_MAP_REGISTRY", None)
        except Exception:
            registry = None
    if isinstance(registry, dict) and map_id in registry:
        entry = registry.get(map_id) or {}
        return parse_city_meta(entry)
    return base


def ordinal_block_sub(number: int, bearing: str) -> str:
    """``Fourth Block South`` from (4, 'S')."""
    bearing = str(bearing or "").strip().upper()[:1]
    word = _BEARING_WORD.get(bearing, bearing.title() or "Away")
    try:
        n = int(number)
    except (TypeError, ValueError):
        n = 0
    if 1 <= n < len(_ORDINALS):
        return f"{_ORDINALS[n]} Block {word}"
    return f"Block {n} {word}"


def mile_marker_sub(number: int) -> str:
    """``Mile Marker 7`` — immersive highway Sub."""
    try:
        n = int(number)
    except (TypeError, ValueError):
        n = 0
    return f"Mile Marker {n}"


def landmark_sub_from_amenity_title(amenity_title: str) -> str:
    """Build ``Near the Hotel`` from a structured amenity ROOM NAME."""
    title = str(amenity_title or "").strip()
    if not title:
        return ""
    _city, main, sub = split_structured_title(title)
    # Prefer building Main (Hotel) over Sub (Lobby) for wayfinding.
    label = main or title_leaf(title) or title
    # Drop leading articles for "Near the …".
    label = re.sub(r"^(the|a|an)\s+", "", label, flags=re.IGNORECASE).strip()
    if not label:
        return ""
    # Already "Near …" → keep.
    if label.lower().startswith("near "):
        return label
    return f"Near the {label}"


def paint_structured_room_title(
    character,
    title: str,
    *,
    room=None,
    game=None,
    staff_vnum: str | None = None,
) -> str:
    """Sighted ANSI for ``City - Main - Sub``; plain when SR / no color.

    Screenreader characters get the plain title (plus optional ``[VNUM]``
    for staff). Color is decoration only — segments stay readable if
    stripped.
    """
    from engine import style

    plain = str(title or "").strip()
    if not plain:
        return plain
    staff_suffix = ""
    if staff_vnum:
        staff_suffix = f"[{staff_vnum}]"

    # Screenreader / color-off: plain immersive text only.
    if getattr(character, "screenreader", False):
        return plain + staff_suffix
    if character is not None and getattr(character, "use_color", True) is False:
        return plain + staff_suffix

    city, main, sub = split_structured_title(plain)
    if not city or not main:
        # Legacy unstructured title — soft crimson whole (old look default).
        painted = style.paint_for(character, "dark_red", plain)
        if staff_suffix:
            painted += style.paint_for(character, "muted", staff_suffix)
        return painted

    meta = meta_from_room(room, game)
    city_role = meta.get("city_color") or DEFAULT_CITY_COLOR
    sub_role = meta.get("sub_color") or DEFAULT_SUB_COLOR
    kind = infer_main_kind(main, room=room, key=getattr(room, "key", "") or "")
    main_role = main_color_for(kind, meta.get("main_colors"))

    sep = style.paint_for(character, "dark_grey", " - ")
    parts = [
        style.paint_for(character, city_role, city),
        style.paint_for(character, main_role, main),
    ]
    if sub:
        parts.append(style.paint_for(character, sub_role, sub))
    painted = sep.join(parts)
    if staff_suffix:
        painted += style.paint_for(character, "muted", staff_suffix)
    return painted

