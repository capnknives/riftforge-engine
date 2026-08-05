"""
civic_fixture.py -- generic enterable street-fixture framework.

A "civic fixture" is a lookable Item sitting in an outdoor street ``Room``
that opens a pocket-hub ``Room`` through the engine's existing pocket-mouth
mechanism -- ``Room.zone_entries`` (alias -> hub Room), ``Room.zone_exit`` /
``zone_exit_to`` (hub -> street), and ``Character.zone_entry_hub_key`` (which
mouth the character is currently inside, read by ``engine.verbs.basic``'s
``cmd_enter`` / ``cmd_exit``). That is the same "pocket dimension" pattern
already used for dungeon mouths and homestead lots -- this module does not
invent a second one, it just gives any game a reusable way to *site* a
fixture (compass facing math, layout placement) and *wire* one (mouth,
Item, look line, structural HP/wrecked arithmetic, breach ejection)
without hand-rolling the plumbing per feature.

Deliberately has **no opinion on economy** -- no rent, no insurance, no
deed, no tenant, no wholesale catalog, no combat damage tuning. Those are
game content/policy (SUPERS' ``player_shops.py`` keeps them) layered on
top of the mechanism here, the same "mechanism vs policy" split as
``engine.systems.room_structure`` / ``supers.room_structure`` (hard rule 5
family). HP/wrecked state is exposed as **pure math** (``apply_damage`` /
``full_repair`` / ``clamp_hp``), not a keyed store -- unlike
``room_structure.py`` (which had no prior storage for wall props), a civic
fixture's HP already lives on the game's own economic record (SUPERS'
``shop`` dict, SQL-persisted) -- this module computes the next value,
the caller keeps owning where it lives.

Peeled from ``supers/player_shops.py`` under docs/plans/
riftforge_core_expansion.md's Phase 6b (originally deferred as "not a
mechanical extraction" because fixture HP/wrecked looked fused with the
rent/insurance/tenant record) -- re-examined: the siting/mouth/Item/HP-math
mechanics below have zero SUPERS coupling and already ride on generic
engine ``Room``/``Character`` fields; only the economic fields (rent,
insurance, deed, tenant, wholesale) are genuinely fused game content, and
those already live in their own ``meta`` sub-dict on the SUPERS side, not
mixed into these functions. Stdlib only; zero ``supers`` imports.
"""

from __future__ import annotations

from engine.world import Item, Room

# Descriptive-only compass facing -- never a walkable exit, never a second
# entry path. `enter <alias>` (engine.verbs.basic.cmd_enter) stays the only
# mouth into the hub; these codes are purely for look-text and layout math.
_FACING_ALIASES = {
    "n": "N", "north": "N",
    "ne": "NE", "northeast": "NE",
    "e": "E", "east": "E",
    "se": "SE", "southeast": "SE",
    "s": "S", "south": "S",
    "sw": "SW", "southwest": "SW",
    "w": "W", "west": "W",
    "nw": "NW", "northwest": "NW",
}
_FACING_WORDS = {
    "N": "north", "NE": "northeast", "E": "east", "SE": "southeast",
    "S": "south", "SW": "southwest", "W": "west", "NW": "northwest",
}

# Layout canvas deltas (matches maps._LAYOUT_XY_DELTA) <-> facing codes.
_LAYOUT_DELTA_TO_FACING = {
    (0, 1): "N", (0, -1): "S", (1, 0): "E", (-1, 0): "W",
    (1, 1): "NE", (-1, 1): "NW", (1, -1): "SE", (-1, -1): "SW",
}
_FACING_TO_LAYOUT_DELTA = {v: k for k, v in _LAYOUT_DELTA_TO_FACING.items()}

# Install-time pick order: diagonals first so cardinals stay open for a
# street spine's own N/E/S/W expansion on the layout grid.
DEFAULT_FACING_INSTALL_PREFERENCE = ("NE", "NW", "SE", "SW", "E", "W", "N", "S")
DIAGONAL_FACINGS = frozenset({"NE", "NW", "SE", "SW"})

# Generic "this area type reads like a through-street" vocabulary -- civic
# planning, not lore. A game can pass its own via ``is_spine_cell``.
DEFAULT_SPINE_AREA_TYPES = frozenset({"city", "city_street", "highway"})
DEFAULT_SPINE_TITLE_TOKENS = ("main street", "highway", "boulevard")


def normalize_facing(token):
    """User-typed direction -> canonical two-letter code, or None."""
    return _FACING_ALIASES.get(str(token or "").strip().lower())


def facing_word(code):
    """Canonical facing code -> prose word ('NE' -> 'northeast'), or None."""
    return _FACING_WORDS.get(str(code or "").strip().upper())


def facing_from_layout_delta(dx, dy):
    """Map a host->hub layout step to a facing code, or None."""
    try:
        return _LAYOUT_DELTA_TO_FACING.get((int(dx), int(dy)))
    except (TypeError, ValueError):
        return None


def layout_delta_for_facing(code):
    """Facing code -> (dx, dy) layout offset from host street to hub."""
    return _FACING_TO_LAYOUT_DELTA.get(str(code or "").strip().upper())


def fixture_frontage_phrase(facing_code):
    """Short look phrase: 'northeast frontage'."""
    word = facing_word(facing_code)
    if not word:
        return ""
    return f"{word} frontage"


def room_layout_xyz(room):
    """Return (x, y, z) when the room has Studio layout coords, else None."""
    if room is None:
        return None
    lx = getattr(room, "layout_x", None)
    ly = getattr(room, "layout_y", None)
    if lx is None or ly is None:
        return None
    lz = getattr(room, "layout_z", None)
    if lz is None:
        lz = 0
    return int(lx), int(ly), int(lz)


def layout_neighbor_room(game, room, dx, dy):
    """Room at layout (x+dx, y+dy, z) on the same map layer, or None."""
    if game is None or room is None:
        return None
    origin = room_layout_xyz(room)
    if origin is None:
        return None
    ox, oy, oz = origin
    map_id = getattr(room, "map_id", None)
    if not map_id:
        return None
    rooms = getattr(game, "rooms", None) or {}
    for candidate in rooms.values():
        if getattr(candidate, "map_id", None) != map_id:
            continue
        pos = room_layout_xyz(candidate)
        if pos is None:
            continue
        cx, cy, cz = pos
        if cx == ox + int(dx) and cy == oy + int(dy) and cz == oz:
            return candidate
    return None


def _default_is_spine_cell(room):
    """Generic "looks like a through-street continuation" heuristic."""
    if room is None:
        return False
    if not getattr(room, "outdoor", False):
        return False
    area = str(getattr(room, "area_type", None) or "").lower()
    if area in DEFAULT_SPINE_AREA_TYPES:
        return True
    title = str(getattr(room, "title", None) or "").lower()
    return any(tok in title for tok in DEFAULT_SPINE_TITLE_TOKENS)


def _facing_candidate_score(host, facing_code, game, *, is_spine_cell):
    """Higher = better install pad. Diagonals beat cardinals; an empty pad
    beats an existing street-spine cell in that direction."""
    delta = layout_delta_for_facing(facing_code)
    if delta is None:
        return -999
    dx, dy = delta
    score = 0
    if facing_code in DIAGONAL_FACINGS:
        score += 100
    neighbor = layout_neighbor_room(game, host, dx, dy)
    if neighbor is None:
        score += 50
    elif is_spine_cell(neighbor):
        score -= 80
    elif not getattr(neighbor, "outdoor", False):
        score += 20
    return score


def infer_facing_at_install(
    host, game, *, hub=None, facing_preference=None, is_spine_cell=None,
):
    """Pick a mandatory storefront facing when a fixture is installed.

    Priority:
    1. Exact layout delta host->hub (migration / an already-placed pocket).
    2. Indoor compass exit off the host (legacy wing conversion).
    3. Heuristic pad pick -- **diagonals before cardinals** so cardinal
       cells stay open for street expansion on the layout grid.

    ``facing_preference`` overrides ``DEFAULT_FACING_INSTALL_PREFERENCE``;
    ``is_spine_cell(room) -> bool`` overrides the generic "through-street"
    heuristic for games with their own zoning vocabulary.
    """
    preference = facing_preference or DEFAULT_FACING_INSTALL_PREFERENCE
    spine_check = is_spine_cell or _default_is_spine_cell
    if host is None:
        return "NE", {"dx": 1, "dy": 1, "dz": 0}
    host_pos = room_layout_xyz(host)
    hub_pos = room_layout_xyz(hub) if hub is not None else None
    if host_pos is not None and hub_pos is not None:
        dx = hub_pos[0] - host_pos[0]
        dy = hub_pos[1] - host_pos[1]
        dz = hub_pos[2] - host_pos[2]
        facing = facing_from_layout_delta(dx, dy)
        if facing:
            return facing, {"dx": dx, "dy": dy, "dz": dz}
    exits = getattr(host, "exits", None) or {}
    for direction, dest in exits.items():
        code = normalize_facing(direction)
        if code is None:
            continue
        if dest is not None and not getattr(dest, "outdoor", True):
            delta = layout_delta_for_facing(code) or (0, 0)
            return code, {"dx": delta[0], "dy": delta[1], "dz": 0}
    if host_pos is not None:
        best = None
        best_score = -9999
        for code in preference:
            score = _facing_candidate_score(
                host, code, game, is_spine_cell=spine_check,
            )
            if score > best_score:
                best_score = score
                best = code
        if best:
            dx, dy = layout_delta_for_facing(best)
            return best, {"dx": dx, "dy": dy, "dz": 0}
    return "NE", {"dx": 1, "dy": 1, "dz": 0}


def stamp_hub_layout(host, hub, offset):
    """Place ``hub`` at ``host``'s layout position + ``offset`` (Studio map)."""
    if host is None or hub is None:
        return
    host_pos = room_layout_xyz(host)
    if host_pos is None:
        return
    hub.layout_x = host_pos[0] + int(offset.get("dx", 0))
    hub.layout_y = host_pos[1] + int(offset.get("dy", 0))
    hub.layout_z = host_pos[2] + int(offset.get("dz", 0))
    if getattr(host, "map_id", None):
        hub.map_id = host.map_id


# ---------------------------------------------------------------------------
# Fixture Item identification + collection
# ---------------------------------------------------------------------------

def is_fixture_item(obj):
    """True when ``obj`` is a civic-fixture Item stamped by this module."""
    return (
        obj is not None
        and isinstance(obj, Item)
        and bool(getattr(obj, "civic_fixture", False))
    )


def fixtures_on_room(room):
    """All civic fixtures sitting in ``room.contents``."""
    out = []
    for obj in list(getattr(room, "contents", None) or []):
        if is_fixture_item(obj):
            out.append(obj)
    return out


def fixture_for_id(room, fixture_id):
    """Find the fixture Item for ``fixture_id`` in a host room, if any."""
    if room is None or not fixture_id:
        return None
    for obj in list(getattr(room, "contents", None) or []):
        if is_fixture_item(obj) and getattr(obj, "fixture_id", None) == fixture_id:
            return obj
    return None


def make_fixture_item(fixture_id, description, *, furniture=True):
    """Build a curb fixture Item. Caller supplies the full description
    text (name, alias hint, frontage phrase, ...) -- this module has no
    opinion on wording."""
    item = Item(f"fixture:{fixture_id}", description, furniture=furniture)
    item.civic_fixture = True
    item.fixture_id = fixture_id
    return item


def fixture_look_line(
    *, display_name, enter_alias, wrecked=False, facing=None,
    for_rent=False, wrecked_label=None,
):
    """One curb look-line, generic wording with a caller-suppliable
    wrecked label override."""
    if wrecked:
        label = wrecked_label or f"{display_name} — burned out and boarded up."
        return label
    frontage = fixture_frontage_phrase(facing)
    if frontage:
        line = f"{display_name} — {frontage} (enter {enter_alias})"
    else:
        line = f"{display_name} (enter {enter_alias})"
    if for_rent:
        line += " [FOR RENT]"
    return line


def place_fixture(host, item):
    """Ensure ``item`` is the only fixture with its ``fixture_id`` in
    ``host.contents`` (replaces a stale copy, e.g. after a stat change)."""
    if host is None:
        return
    fixture_id = getattr(item, "fixture_id", None)
    existing = fixture_for_id(host, fixture_id)
    if existing is not None:
        host.remove(existing)
    host.add(item)


def remove_fixture(host, fixture_id):
    """Remove the fixture matching ``fixture_id`` from ``host``, if present."""
    if host is None:
        return
    item = fixture_for_id(host, fixture_id)
    if item is not None:
        host.remove(item)


# ---------------------------------------------------------------------------
# Pocket-mouth wiring (Room.zone_entries / zone_exit / zone_exit_to --
# the same generic fields engine.verbs.basic.cmd_enter/cmd_exit already
# read for dungeon mouths and homestead lots)
# ---------------------------------------------------------------------------

def wire_mouth(host, hub, alias, fixture_id):
    """Stamp the street's ``enter <alias>`` mouth; hub exits back only."""
    if host is None or hub is None:
        return
    alias = str(alias or "").strip().lower()
    if not hasattr(host, "zone_entries") or host.zone_entries is None:
        host.zone_entries = {}
    host.zone_entries[alias] = hub
    hub.zone_exit_to = host
    hub.zone_exit = True
    hub.zone = fixture_id


def unwire_mouth(host, alias):
    """Remove one ``enter`` alias from the host street."""
    if host is None:
        return
    alias = str(alias or "").strip().lower()
    entries = getattr(host, "zone_entries", None) or {}
    if alias in entries:
        del entries[alias]


def create_hub_room(hub_key, description, *, fixture_id=None):
    """Runtime interior Room for one fixture. Caller stamps game-specific
    attrs (title, resources, jobs, ...) after this returns."""
    hub = Room(hub_key, description)
    hub.outdoor = False
    hub.wilderness = False
    hub.no_random_spawn = True
    if fixture_id is not None:
        hub.zone = fixture_id
    return hub


# ---------------------------------------------------------------------------
# Structural HP / wrecked math -- pure functions, no storage opinion. The
# caller's own record (SUPERS' ``shop`` dict, SQL-persisted) keeps owning
# where "hp" and "wrecked" actually live; this just computes the next
# value the same way every time.
# ---------------------------------------------------------------------------

def clamp_hp(hp, hp_max):
    """Clamp ``hp`` into ``[0, hp_max]``."""
    try:
        hp_i = int(hp)
    except (TypeError, ValueError):
        hp_i = 0
    try:
        max_i = int(hp_max)
    except (TypeError, ValueError):
        max_i = 0
    return max(0, min(hp_i, max_i))


def apply_damage(hp, hp_max, damage):
    """Return ``(new_hp, wrecked)`` after ``damage`` chips ``hp``."""
    try:
        dmg = max(0, int(damage))
    except (TypeError, ValueError):
        dmg = 0
    new_hp = clamp_hp(int(hp or 0) - dmg, hp_max)
    return new_hp, new_hp <= 0


def full_repair(hp_max):
    """Return the ``(hp, wrecked)`` pair for a freshly repaired fixture."""
    return clamp_hp(hp_max, hp_max), False


# ---------------------------------------------------------------------------
# Breach ejection -- force a character through a wrecked fixture's front
# into its own interior hub. Degrades to a no-op (caller keeps the plain
# wreck, no forced move) when the hub is not there to land in -- never a
# hard error, never a teleport to an unrelated room.
# ---------------------------------------------------------------------------

def breach_defender_into_fixture(
    character, hub, *, source_line=None, landing_line=None, self_line=None,
):
    """Move ``character`` into ``hub`` and stamp the pocket-mouth tracking
    field (``zone_entry_hub_key``) the same way ``cmd_enter`` would, since
    this bypasses that verb. Caller supplies fully-rendered broadcast text
    (or ``None`` to skip a line) -- this module has no opinion on prose."""
    if character is None or hub is None:
        return False
    old_room = getattr(character, "location", None)
    if old_room is not None and source_line:
        old_room.broadcast(source_line, exclude=character)
    character.move_to(hub)
    character.zone_entry_hub_key = hub.key
    if landing_line:
        hub.broadcast(landing_line, exclude=character)
    session = getattr(character, "session", None)
    if session is not None and self_line:
        session.send(self_line)
    return True
