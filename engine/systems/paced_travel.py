"""
paced_travel.py -- generic paced walking between tagged destinations.

Games inject passability (``set_paced_travel_edge_ok``), optional zone
destination lists (``set_paced_travel_destinations``), and pocket enter
aliases (``set_paced_travel_enter_alias``). Stdlib only; zero ``supers``
imports.
"""

from __future__ import annotations

import re
from collections import deque

from engine import hooks as hooks_mod
from engine.systems.overland import is_virtual_room

MAX_WALK_STEPS = 200

TRAVEL_PACES = ("walk", "jog", "run")
PACE_STEP_EVERY = {
    "walk": 2,
    "jog": 1,
    "run": 1,
}
PACE_HOPS_PER_ADVANCE = {
    "walk": 1,
    "jog": 1,
    "run": 3,
}
WALK_STEP_EVERY = PACE_STEP_EVERY["walk"]

# Stamp junk around the numbers: parens, quotes, commas, spaces.
# Leftover letters mean this is a place name, not a coordinate.
_COORD_PUNCT_RE = re.compile(r"[\d,()'\"`.\s/-]+")

_RESOURCE_HINTS = {
    "clinic": ("clinic",),
    "hospital": ("clinic",),
    "food": ("food",),
    "cook": ("food",),
    "kitchen": ("food",),
    "diner": ("food",),
    "restaurant": ("food",),
    "grocery": ("vendor", "food"),
    "shop": ("vendor",),
    "store": ("vendor",),
    "bank": ("bank",),
    "mail": ("mail",),
    "post": ("mail",),
    "gym": ("training",),
    "train": ("training",),
    "training": ("training",),
    "spar": ("training",),
    "sparring": ("training",),
    "sparring gym": ("training",),
    "spar gym": ("training",),
    "work": ("work",),
    "job": ("work",),
    "sleep": ("sleep",),
    "bed": ("sleep",),
    "bedroom": ("sleep",),
    "hotel": ("sleep",),
    "motel": ("sleep",),
    "wash": ("hygiene",),
    "shower": ("hygiene",),
    "hygiene": ("hygiene",),
    "water": ("water",),
    "bar": ("social",),
    "social": ("social",),
    "entertainment": ("entertainment",),
    "garage": (),
}

_NOISY_LAST_WORD = re.compile(r"^(\d+|[a-z]\d*|\d+[a-z]?|[a-z])$", re.I)

_SECONDARY_ROOM_TOKENS = frozenset({
    "ward", "upper", "hall", "stockroom", "cellar", "annex", "back",
    "overflow", "corridor", "entryway", "foyer",
})

# Motel unit codes / plane interiors -- not player-facing run targets.
_LODGING_UNIT_RE = re.compile(r"^b\d+[a-z]$", re.I)
_VEHICLE_INTERIOR_RE = re.compile(r"^\d+\s+(cabin|cargo|cockpit)$", re.I)
_LIST_NOISE_LABELS = frozenset({
    "approach", "apron", "access spur", "bend north", "backroom",
    "apparatus bay", "bar floor", "car", "ride", "truck", "vehicle",
})


def normalize_pace(raw):
    """Return a valid travel pace id (default walk)."""
    key = str(raw or "walk").strip().lower()
    if key in PACE_STEP_EVERY:
        return key
    return "walk"


def pace_gerund(pace):
    """Capitalized progressive for status lines (Walking / Jogging / …)."""
    return {
        "walk": "Walking",
        "jog": "Jogging",
        "run": "Running",
    }.get(normalize_pace(pace), "Walking")


def _not_traveling_msg(pace):
    """Refuse line when stop is typed with no active journey."""
    return {
        "walk": "You are not walking anywhere.",
        "jog": "You are not jogging anywhere.",
        "run": "You are not running anywhere.",
    }.get(normalize_pace(pace), "You are not walking anywhere.")


def pace_of_focus(focus):
    """Pace stamped on a walk_focus dict (default walk)."""
    if not isinstance(focus, dict):
        return "walk"
    return normalize_pace(focus.get("pace"))


def normalize_query(raw):
    """Lowercase and turn underscores / hyphens into spaces for matching."""
    text = (raw or "").strip().lower()
    text = text.replace("_", " ").replace("-", " ")
    return " ".join(text.split())


def parse_coordinates(raw):
    """Return a coordinate tuple parsed from ``raw``, or ``None`` on no match.

    Discrimination uses ``len`` on the returned tuple:

    * ``len == 2`` -- macro atlas tile ``(mx, my)`` from ``20 5``,
      ``35,11``, or ``(35,11)``.
    * ``len == 4`` -- macro tile plus foot micro ``(mx, my, ux, uy)``
      from four integers in any common stamp: ``44 32 3 7``,
      ``35,11 5,0``, ``(35,11) 5,0``, or ``'35 11' '5,0'``.
      Micro ``ux`` / ``uy`` must lie in ``0`` through ``9`` (the 10-by-10
      foot grid inside each atlas tile); out-of-range micro values are a
      parse failure (``None``), same as unmatched text.

    Leftover letters fail (so ``lebanon`` and ``build (35,11) 5,7``
    stay names, not coords). Three numbers like ``1 2 3`` do not match.
    """
    text = (raw or "").strip()
    if not text:
        return None
    leftover = _COORD_PUNCT_RE.sub("", text)
    if leftover:
        return None
    nums = [int(tok) for tok in re.findall(r"-?\d+", text)]
    if len(nums) == 2:
        return (nums[0], nums[1])
    if len(nums) == 4:
        mx, my, ux, uy = nums
        # MICRO_SIZE is 10 on the America dual-layer atlas (0..9 cells).
        from engine.systems.overland import MICRO_SIZE

        if not (0 <= ux < MICRO_SIZE and 0 <= uy < MICRO_SIZE):
            return None
        return (mx, my, ux, uy)
    return None


def format_atlas_coords(x, y):
    """Player-facing atlas cell with no parentheses (screenreader-safe)."""
    return f"atlas {int(x)} {int(y)}"


def _is_vnum_token(text):
    """True when ``text`` looks like a hand-room VNUM (``GE00008``)."""
    from engine.room_vnum import parse_vnum

    return parse_vnum(str(text or "").strip().upper()) is not None


def _token_is_noisy(token):
    """True when a walk alias token should not appear in player lists."""
    if not token:
        return True
    if _is_vnum_token(token):
        return True
    if token in _LIST_NOISE_LABELS:
        return True
    if _LODGING_UNIT_RE.match(token):
        return True
    if _VEHICLE_INTERIOR_RE.match(token):
        return True
    return bool(_NOISY_LAST_WORD.match(token))


def _player_walk_room_label(room):
    """PLAYER-facing label for walk ambiguity -- never ``NAME[VNUM]`` chrome."""
    from engine.room_naming import bare_key, is_opaque_storage_key
    from engine.room_vnum import describe_room

    label = describe_room(room, staff=False)
    if not label or label == "?":
        return "somewhere"
    leg = getattr(room, "legacy_key", None)
    if leg:
        hint = bare_key(str(leg))
        # Phase 3 VNUM rekeys keep ``unowned shopN`` / ``amenityN`` on
        # legacy_key for resolver aliases -- never show those dig ids in
        # walk / seek prose (bug report 604: Ash Garage (unowned shop5)).
        if (
            hint
            and not is_opaque_storage_key(hint)
            and not is_opaque_storage_key(str(leg))
            and normalize_query(hint) != normalize_query(label)
        ):
            return f"{label} ({hint})"
    return label


def _send(character, text):
    """Send one line when the actor has a live Session."""
    session = getattr(character, "session", None)
    if session is not None:
        session.send(text)


def _room_tokens(room):
    """Word tokens from a room's player title and storage key for matching."""
    parts = []
    title = ""
    if hasattr(room, "look_title"):
        title = room.look_title() or ""
    key = getattr(room, "key", "") or ""
    key_for_tokens = "" if _is_vnum_token(key) else key
    for raw in (title, key_for_tokens):
        norm = normalize_query(raw or "")
        if norm:
            parts.extend(norm.split())
    seen = set()
    out = []
    for tok in parts:
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _alias_candidates_for_room(room, zone_rooms):
    """Short walk labels for one room."""
    from engine.room_vnum import describe_room
    key = getattr(room, "key", "") or ""
    title = describe_room(room) if room is not None else key
    norm_key = normalize_query(key)
    norm_title = normalize_query(title)
    aliases = []
    if norm_key and not _is_vnum_token(key):
        aliases.append(norm_key)
    if norm_title and norm_title != norm_key:
        aliases.insert(0, norm_title)
    tokens = _room_tokens(room)
    if tokens:
        last = tokens[-1]
        if not _token_is_noisy(last) and len(last) >= 3:
            same_last = [
                r for r in zone_rooms
                if _room_tokens(r) and _room_tokens(r)[-1] == last
            ]
            if len(same_last) == 1:
                aliases.append(last)
        if len(tokens) >= 2:
            pair = f"{tokens[-2]} {tokens[-1]}"
            if not _token_is_noisy(tokens[-1]):
                aliases.append(pair)
    for tag in getattr(room, "resources", None) or []:
        aliases.append(normalize_query(str(tag)))
    if getattr(room, "hospital", False):
        aliases.append("clinic")
        aliases.append("hospital")
    seen = set()
    out = []
    for alias in aliases:
        if alias and alias not in seen:
            seen.add(alias)
            out.append(alias)
    return out


def _score_room_match(needle, room):
    """Higher is a better match for ``needle`` against ``room``."""
    from engine.room_vnum import describe_room
    key_n = normalize_query(getattr(room, "key", "") or "")
    title_n = normalize_query(describe_room(room) if room is not None else key_n)
    tokens = _room_tokens(room)
    score = 0
    if needle == key_n:
        score += 100
    if needle == title_n:
        score += 90
    if tokens and tokens[-1] == needle:
        score += 40
    tags = set(getattr(room, "resources", None) or [])
    if getattr(room, "hospital", False):
        tags.add("clinic")
    if needle in tags:
        score += 30
    hinted = _RESOURCE_HINTS.get(needle)
    if hinted and tags.intersection(hinted):
        score += 25
    if needle == "clinic" and getattr(room, "hospital", False):
        score += 35
    for hay in (key_n, title_n):
        if hay.startswith(needle + " ") or hay.endswith(" " + needle):
            score += 15
        if needle in hay:
            score += 5
        needle_tokens = needle.split()
        if needle_tokens and all(t in hay for t in needle_tokens):
            score += 8
    for bad in _SECONDARY_ROOM_TOKENS:
        if bad in tokens:
            score -= 12
    score -= min(len(title_n or key_n), 40) // 10
    return score


def _pick_best_room(needle, candidates):
    """Return (room, None) or (None, ambiguity_message) from scored hits."""
    if not candidates:
        return None, f"No place matching '{needle}' here. Type walk for a list."
    if len(candidates) == 1:
        return candidates[0], None
    scored = sorted(
        ((_score_room_match(needle, room), room) for room in candidates),
        key=lambda pair: (-pair[0], pair[1].key.lower()),
    )
    best_score, best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -999
    if best_score >= 20 and best_score > second_score:
        return best, None
    names = ", ".join(
        _player_walk_room_label(room)
        for _s, room in scored[:8]
    )
    return None, f"Which place? {names}"


def rooms_in_zone(game, zone):
    """All rooms sharing ``zone`` (settlement pocket), or empty list."""
    if not zone or game is None:
        return []
    rooms = getattr(game, "rooms", None) or {}
    from engine.char_index import room_graph_cache_token

    token = room_graph_cache_token(game)
    cache = getattr(game, "_rooms_by_zone_cache", None)
    if cache is None or cache[0] != token:
        index = {}
        for room in rooms.values():
            z = getattr(room, "zone", None)
            if z:
                index.setdefault(z, []).append(room)
        game._rooms_by_zone_cache = (token, index)
        cache = game._rooms_by_zone_cache
    return list(cache[1].get(zone, ()))


def list_zone_destinations(character, game):
    """Build sorted unique short labels for bare ``walk`` inside a zone."""
    room = character.location
    zone = getattr(room, "zone", None)
    zone_rooms = rooms_in_zone(game, zone)
    labels = set()
    last_word_rooms = {}
    pair_rooms = {}
    for candidate in zone_rooms:
        tokens = _room_tokens(candidate)
        if tokens:
            last = tokens[-1]
            if last:
                last_word_rooms.setdefault(last, []).append(candidate)
            if len(tokens) >= 2:
                pair = f"{tokens[-2]} {tokens[-1]}"
                pair_rooms.setdefault(pair, []).append(candidate)
        for tag in getattr(candidate, "resources", None) or []:
            tag_n = normalize_query(str(tag))
            if tag_n in _RESOURCE_HINTS or tag_n in (
                "clinic", "food", "training", "bank", "mail", "vendor",
                "hygiene", "sleep", "social", "work",
            ):
                labels.add(tag_n)
        if getattr(candidate, "hospital", False):
            labels.add("clinic")
    for last, same_last in last_word_rooms.items():
        if (
            not _token_is_noisy(last)
            and len(last) >= 3
            and last not in _SECONDARY_ROOM_TOKENS
            and len(same_last) == 1
        ):
            labels.add(last)
    for pair, same_pair in pair_rooms.items():
        parts = pair.split()
        if (
            parts
            and parts[-1] not in _SECONDARY_ROOM_TOKENS
            and not _token_is_noisy(parts[-1])
            and len(pair) <= 24
            and len(same_pair) == 1
        ):
            labels.add(pair)
    extra = hooks_mod.paced_travel_destinations(character, game, zone)
    if extra:
        labels.update(extra)
    return sorted(labels, key=str.lower)[:40]


def match_zone_room(needle, zone_rooms):
    """Pick a room in ``zone_rooms`` for ``needle``, or raise ambiguity."""
    needle = normalize_query(needle)
    if not needle or not zone_rooms:
        return None, "No places to walk to here."

    candidates = []
    for room in zone_rooms:
        aliases = _alias_candidates_for_room(room, zone_rooms)
        from engine.room_vnum import describe_room
        key_n = normalize_query(room.key)
        title_n = normalize_query(describe_room(room))
        desc_n = normalize_query(getattr(room, "description", "") or "")
        tags = set(getattr(room, "resources", None) or [])
        if getattr(room, "hospital", False):
            tags.add("clinic")
        hinted = _RESOURCE_HINTS.get(needle) or ()
        token_hay = f"{key_n} {title_n} {desc_n}"
        if (
            needle in aliases
            or needle == key_n
            or needle in key_n
            or needle in tags
            or (hinted and tags.intersection(hinted))
            or (needle.split() and all(t in token_hay for t in needle.split()))
        ):
            candidates.append(room)

    by_key = {room.key: room for room in candidates}
    return _pick_best_room(needle, list(by_key.values()))


def _best_enter_alias(entries, hub):
    """Pick a stable ``enter <alias>`` label for ``hub`` from zone_entries."""
    aliases = [a for a, h in entries.items() if h is hub]
    if not aliases:
        return None
    custom = hooks_mod.paced_travel_enter_alias(entries, hub)
    if custom:
        return custom
    return min(aliases, key=len)


def _edge_ok(from_room, neighbor, actor=None, game=None):
    """May a paced walk BFS step this exit?"""
    if neighbor is from_room:
        return False
    return hooks_mod.paced_travel_edge_ok(
        from_room, neighbor, actor=actor, game=game,
    )


def _hub_ok(hub, from_room, actor=None, game=None):
    """May a paced walk BFS enter this pocket hub?"""
    return hooks_mod.paced_travel_hub_ok(
        hub, from_room, actor=actor, game=game,
    )


def next_hop_toward_destination(start, dest_key, actor=None, game=None,
                                max_nodes=600):
    """BFS first hop toward ``dest_key``, including pocket enter/exit edges.

    Caps expansions at ``max_nodes`` (default 600 -- same numeric cap as
    sibling ``path_hop_distances_from`` / ``path_hop_count``, matching
    ``supers.pathfind.HOMEWARD_BFS_MAX_NODES``). Engine cannot import
    supers; without this cap a player ``walk`` / look-adjacent hop would
    flood the ~12k-cell Earth atlas on one command.
    """
    if start is None or not dest_key:
        return None
    from engine.room_vnum import room_keys_match

    if room_keys_match(game, start.key, dest_key):
        return None
    if is_virtual_room(start):
        return None

    seen = {start}
    queue = deque()

    def _push(neighbor, hop):
        if neighbor is None or neighbor in seen:
            return
        if neighbor is start and hop is not None:
            return
        if max_nodes is not None and len(seen) >= max_nodes:
            return
        seen.add(neighbor)
        queue.append((neighbor, hop))

    def _expand(room, first_hop):
        for direction, neighbor in room.exits.items():
            if not _edge_ok(room, neighbor, actor=actor, game=game):
                continue
            hop = direction if first_hop is None else first_hop
            _push(neighbor, hop)
        entries = getattr(room, "zone_entries", None) or {}
        hubs_seen = set()
        for hub in entries.values():
            if hub is None or hub in seen or hub.key in hubs_seen:
                continue
            if not _hub_ok(hub, room, actor=actor, game=game):
                continue
            alias = _best_enter_alias(entries, hub)
            if not alias:
                continue
            hubs_seen.add(hub.key)
            hop = ("enter", alias) if first_hop is None else first_hop
            _push(hub, hop)
        exit_to = getattr(room, "zone_exit_to", None)
        if exit_to is not None and exit_to not in seen:
            hop = ("exit", None) if first_hop is None else first_hop
            _push(exit_to, hop)

    expanded = 0
    _expand(start, None)
    while queue:
        room, first_hop = queue.popleft()
        if room_keys_match(game, room.key, dest_key):
            return first_hop
        expanded += 1
        if max_nodes is not None and expanded > max_nodes:
            return None
        _expand(room, first_hop)
    return None


def path_hop_distances_from(start, actor=None, game=None, max_nodes=600):
    """BFS hop counts from ``start`` to reachable rooms."""
    if start is None:
        return {}
    if is_virtual_room(start):
        return {start.key: 0}

    tick = int(getattr(game, "game_time_ticks", 0) or 0) if game is not None else 0
    actor_token = id(actor) if actor is not None else 0
    cache = getattr(game, "_walk_hop_dist_cache", None)
    if (
        game is not None
        and isinstance(cache, dict)
        and cache.get("tick") == tick
        and cache.get("start_key") == start.key
        and cache.get("actor") == actor_token
        and cache.get("max_nodes") == max_nodes
    ):
        return cache["distances"]

    seen = {start}
    queue = deque([(start, 0)])
    distances = {start.key: 0}
    expanded = 0

    while queue:
        room, dist = queue.popleft()
        expanded += 1
        if expanded > max_nodes:
            break
        next_dist = dist + 1
        for direction, neighbor in (room.exits or {}).items():
            if neighbor is None or neighbor in seen:
                continue
            if not _edge_ok(room, neighbor, actor=actor, game=game):
                continue
            seen.add(neighbor)
            distances[neighbor.key] = next_dist
            queue.append((neighbor, next_dist))
        entries = getattr(room, "zone_entries", None) or {}
        hubs_seen = set()
        for hub in entries.values():
            if hub is None or hub in seen or hub.key in hubs_seen:
                continue
            if not _hub_ok(hub, room, actor=actor, game=game):
                continue
            alias = _best_enter_alias(entries, hub)
            if not alias:
                continue
            hubs_seen.add(hub.key)
            if hub.key not in seen:
                seen.add(hub)
                distances[hub.key] = next_dist
                queue.append((hub, next_dist))
        exit_to = getattr(room, "zone_exit_to", None)
        if exit_to is not None and exit_to not in seen:
            seen.add(exit_to)
            distances[exit_to.key] = next_dist
            queue.append((exit_to, next_dist))

    if game is not None:
        game._walk_hop_dist_cache = {
            "tick": tick,
            "start_key": start.key,
            "actor": actor_token,
            "max_nodes": max_nodes,
            "distances": distances,
        }
    return distances


def path_hop_count(start, dest, actor=None, game=None, max_nodes=600):
    """Return BFS hop count from ``start`` to ``dest``, or None if blocked."""
    if start is None or dest is None:
        return None
    if start is dest or getattr(start, "key", None) == getattr(dest, "key", None):
        return 0
    dest_key = getattr(dest, "key", None)
    if not dest_key:
        return None
    return path_hop_distances_from(
        start, actor=actor, game=game, max_nodes=max_nodes,
    ).get(dest_key)


def _topo_neighbors(room, game):
    """Graph neighbors for distance planning (ignores actor passability)."""
    if room is None:
        return []
    out = []
    seen = set()
    for neighbor in (room.exits or {}).values():
        if neighbor is None:
            continue
        key = getattr(neighbor, "key", None)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(neighbor)
    entries = getattr(room, "zone_entries", None) or {}
    for hub in entries.values():
        if hub is None:
            continue
        key = getattr(hub, "key", None)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(hub)
    exit_to = getattr(room, "zone_exit_to", None)
    if exit_to is not None:
        key = getattr(exit_to, "key", None)
        if key and key not in seen:
            out.append(exit_to)
    return out


def _topo_distances_from(start, game, *, max_nodes=800):
    """Hop counts on the settlement graph (no actor gates)."""
    if start is None:
        return {}
    seen = {start}
    queue = deque([(start, 0)])
    distances = {start.key: 0}
    expanded = 0
    while queue:
        room, dist = queue.popleft()
        expanded += 1
        if expanded > max_nodes:
            break
        next_dist = dist + 1
        for neighbor in _topo_neighbors(room, game):
            if neighbor in seen:
                continue
            seen.add(neighbor)
            distances[neighbor.key] = next_dist
            queue.append((neighbor, next_dist))
    return distances


def nearest_reachable_toward(start, dest, actor=None, game=None, max_nodes=600):
    """When ``dest`` is blocked, return the closest reachable room toward it.

    Returns ``(frontier_room, None)`` or ``(None, None)`` when no partial
    path exists (caller should fall back to the usual refuse line).
    """
    if start is None or dest is None:
        return None, None
    dest_key = getattr(dest, "key", None)
    if not dest_key:
        return None, None
    if start is dest or start.key == dest_key:
        return dest, None
    if path_hop_count(start, dest, actor=actor, game=game, max_nodes=max_nodes) is not None:
        return dest, None

    reachable = path_hop_distances_from(
        start, actor=actor, game=game, max_nodes=max_nodes,
    )
    if dest_key in reachable:
        return dest, None

    topo = _topo_distances_from(dest, game, max_nodes=max_nodes)
    dest_dist = topo.get(dest_key)
    if dest_dist is None:
        return None, None

    best_key = None
    best_dist = None
    for key, _hop in reachable.items():
        d = topo.get(key)
        if d is None:
            continue
        if best_dist is None or d < best_dist:
            best_dist = d
            best_key = key
    if not best_key or best_key == start.key:
        return None, None
    if best_key == dest_key:
        return dest, None

    rooms = getattr(game, "rooms", None) or {}
    from engine.room_vnum import lookup_room

    frontier = lookup_room(game, best_key)
    if frontier is None:
        return None, None
    return frontier, None


def get_walk_focus(actor):
    """Return the live paced-walk focus dict, or None."""
    focus = getattr(actor, "walk_focus", None)
    if not isinstance(focus, dict):
        return None
    if not focus.get("dest_room_key") and not focus.get("overland_macro"):
        return None
    return focus


def has_walk_focus(actor):
    """True when the actor is mid paced walk."""
    return get_walk_focus(actor) is not None


def clear_walk_focus(actor, *, notice=None):
    """Drop paced walk focus; optional notice to a live Session."""
    if actor is None:
        return
    actor.walk_focus = None
    if notice:
        sess = getattr(actor, "session", None)
        if sess is not None:
            from engine.npc_act import SilentSession

            if not isinstance(sess, SilentSession):
                sess.send(notice)


def format_walk_focus_status(actor, game=None):
    """One-line status for bare ``walk`` / ``jog`` / ``run`` mid-journey."""
    focus = get_walk_focus(actor)
    if focus is None:
        return "Walk focus: (none)."
    label = focus.get("dest_label") or "your destination"
    mode = focus.get("mode") or "room"
    steps = int(focus.get("steps", 0) or 0)
    pace = pace_of_focus(focus)
    verb = pace_gerund(pace)
    stop_hint = focus.get("stop_verb") or (
        f"{pace} stop" if pace != "walk" else "walk stop"
    )
    if mode == "eta":
        until = int(focus.get("arrive_until_tick") or 0)
        now = 0
        if game is not None:
            now = int(getattr(game, "game_time_ticks", 0) or 0)
        remain = max(0, until - now)
        from engine import game_clock_tuning as clock_mod

        span = clock_mod.format_player_tick_span(remain, game)
        # SUPERS stamps eta_lead (smoke vs seep). Never interpolate dest keys.
        lead = focus.get("eta_lead") or "On the way toward"
        return (
            f"{lead} {label} ({span}). "
            f"Type {stop_hint} to cancel."
        )
    return (
        f"{verb} toward {label} ({mode}, {steps} steps so far). "
        f"Type {stop_hint} to cancel."
    )


def _stamp_walk_focus(actor, game, *, mode, dest_label, dest_room_key=None,
                      overland_macro=None, overland_micro=None,
                      enter_alias=None, pace="walk",
                      arrival_notice=None, intended_label=None):
    """Replace walk_focus with a fresh paced journey stamp."""
    tick = 0
    if game is not None:
        tick = int(getattr(game, "game_time_ticks", 0) or 0)
    focus = {
        "mode": mode,
        "dest_room_key": dest_room_key,
        "dest_label": dest_label,
        "intended_label": intended_label or dest_label,
        "overland_macro": (
            list(overland_macro) if overland_macro is not None else None
        ),
        "overland_micro": (
            list(overland_micro) if overland_micro is not None else None
        ),
        "enter_alias": enter_alias,
        "pace": normalize_pace(pace),
        "started_tick": tick,
        "last_step_tick": tick,
        "steps": 0,
        "arrival_notice": arrival_notice,
    }
    actor.walk_focus = focus
    return focus


def _take_player_hop(character, hop, game, quiet=True):
    """Apply one hop: cardinal via move_one, or enter/exit pocket."""
    if hop is None or character is None:
        return False
    if isinstance(hop, tuple):
        kind, alias = hop
        if kind == "enter" and alias:
            from engine.verbs.basic import cmd_enter

            cmd_enter(character, alias, game)
            return True
        if kind == "exit":
            from engine.verbs.basic import cmd_exit_zone

            cmd_exit_zone(character, "", game)
            return True
        return False

    from command_support import _move_one, _pull_followers
    from engine import hooks
    from engine import vision as vision_mod

    if getattr(character, "asleep", False):
        _send(character, "You're asleep -- type 'wake' before you can move.")
        return False
    hooks.cancel_rest(character)
    room = character.location
    dest = room.exits.get(hop)
    if not dest:
        _send(character, "You can't go that way.")
        return False
    if not vision_mod.character_knows_exit(character, room, hop):
        _send(character, "You can't go that way.")
        return False
    block_message = hooks.move_gate_block(character, room, dest, game)
    if block_message:
        _send(character, block_message)
        return False
    _move_one(character, hop, dest, game, auto_look=not quiet)
    _pull_followers(character, room, hop, game)
    if quiet and character.session is not None:
        gait = hooks_mod.paced_travel_gait_of(character)
        character.session.send(f"You {gait} {hop}.")
    return True


def step_toward_room(actor, dest, game, *, mode="player", quiet=True):
    """Take one hop toward ``dest`` (player hop)."""
    if actor is None or dest is None:
        return False
    if getattr(actor, "location", None) is dest:
        return False
    if mode != "player":
        return hooks_mod.paced_travel_cadence_step(
            actor, dest, game, quiet=quiet,
        )
    dest_key = getattr(dest, "key", None)
    if not dest_key:
        return False
    hop = next_hop_toward_destination(
        actor.location, dest_key, actor=actor, game=game,
    )
    if hop is None:
        return False
    before = actor.location
    ok = _take_player_hop(actor, hop, game, quiet=quiet)
    if not ok:
        return False
    return actor.location is not before


def start_paced_walk(character, dest_room, game, *, label=None, pace="walk",
                     engaged_check=None, in_vehicle_check=None,
                     arrival_notice=None, intended_label=None):
    """Begin (or replace) a paced walk to ``dest_room``; take one hop now."""
    from engine import group as group_mod

    pace = normalize_pace(pace)
    if group_mod.block_live_group_move(character):
        return ""
    if dest_room is None:
        return f"{pace_gerund(pace)} where?"
    if character.location is dest_room:
        clear_walk_focus(character)
        return f"You are already at {_player_walk_room_label(dest_room)}."
    if in_vehicle_check is not None:
        msg = in_vehicle_check(character, dest_room, game)
        if msg is not None:
            return msg
    if engaged_check is not None:
        msg = engaged_check(character)
        if msg:
            return msg
    if getattr(character, "asleep", False):
        return "You're asleep -- type 'wake' before you can move."

    dest_label = label or _player_walk_room_label(dest_room)
    intended = intended_label or dest_label
    _stamp_walk_focus(
        character, game,
        mode="room",
        dest_label=dest_label,
        dest_room_key=dest_room.key,
        pace=pace,
        arrival_notice=arrival_notice,
        intended_label=intended,
    )
    _send(character, f"{pace_gerund(pace)} toward {intended}...")
    before = character.location
    moved = step_toward_room(
        character, dest_room, game, mode="player", quiet=True,
    )
    focus = get_walk_focus(character)
    if focus is not None:
        focus["steps"] = int(focus.get("steps", 0) or 0) + (1 if moved else 0)
        focus["last_step_tick"] = int(
            getattr(game, "game_time_ticks", 0) or 0
        )
    if character.location is dest_room:
        clear_walk_focus(character)
        from engine.verbs.basic import cmd_look

        _send(character, f"You arrive at {dest_label}.")
        cmd_look(character, "", game, after_move=True)
        return ""
    if not moved or character.location is before:
        clear_walk_focus(character)
        return f"You can't find a path to {dest_label} from here."
    if engaged_check is not None and engaged_check(character):
        clear_walk_focus(
            character,
            notice=f"Something engages you -- {pace} interrupted.",
        )
        from engine.verbs.basic import cmd_look

        cmd_look(character, "", game, after_move=True)
        return ""
    return ""


def walk_to(character, dest_room, game, *, pace="walk", **kwargs):
    """Start a paced walk to ``dest_room`` (compat name for callers)."""
    return start_paced_walk(character, dest_room, game, pace=pace, **kwargs)


def _advance_room_walk(character, game, focus, engaged_check=None):
    """One paced hop for a room-mode walk_focus."""
    dest_key = focus.get("dest_room_key")
    from engine.room_vnum import lookup_room

    dest = lookup_room(game, dest_key) if dest_key else None
    label = focus.get("dest_label") or dest_key or "your destination"
    pace = pace_of_focus(focus)
    if dest is None:
        clear_walk_focus(
            character,
            notice=f"{pace_gerund(pace)} cancelled -- {label} is gone.",
        )
        return False
    if character.location is dest:
        clear_walk_focus(character)
        from engine.verbs.basic import cmd_look

        notice = focus.get("arrival_notice")
        intended = focus.get("intended_label") or label
        if notice:
            _send(character, f"You arrive near {intended}.")
            _send(character, notice)
        else:
            _send(character, f"You arrive at {label}.")
        cmd_look(character, "", game, after_move=True)
        return False
    if engaged_check is not None and engaged_check(character):
        clear_walk_focus(
            character,
            notice=f"Something engages you -- {pace} interrupted.",
        )
        from engine.verbs.basic import cmd_look

        cmd_look(character, "", game, after_move=True)
        return False
    if getattr(character, "asleep", False):
        clear_walk_focus(
            character,
            notice=f"You're asleep -- {pace} cancelled.",
        )
        return False
    steps = int(focus.get("steps", 0) or 0)
    if steps >= MAX_WALK_STEPS:
        clear_walk_focus(
            character,
            notice=(
                f"Stopped after {MAX_WALK_STEPS} steps "
                f"(still short of {label})."
            ),
        )
        return False
    before = character.location
    moved = step_toward_room(
        character, dest, game, mode="player", quiet=True,
    )
    focus["steps"] = steps + (1 if moved else 0)
    focus["last_step_tick"] = int(getattr(game, "game_time_ticks", 0) or 0)
    if character.location is dest:
        clear_walk_focus(character)
        from engine.verbs.basic import cmd_look

        notice = focus.get("arrival_notice")
        intended = focus.get("intended_label") or label
        if notice:
            _send(character, f"You arrive near {intended}.")
            _send(character, notice)
        else:
            _send(character, f"You arrive at {label}.")
        cmd_look(character, "", game, after_move=True)
        return False
    if not moved or character.location is before:
        clear_walk_focus(
            character,
            notice=f"{pace_gerund(pace)} interrupted -- no path to {label}.",
        )
        return False
    # Run/jog take multiple hops per tick -- refresh the room each hop
    # (bug #368) so players are not blind between steps.
    if pace in ("run", "jog") and character.location is not dest:
        from engine.verbs.basic import cmd_look

        cmd_look(character, "", game, after_move=True)
    if engaged_check is not None and engaged_check(character):
        clear_walk_focus(
            character,
            notice=f"Something engages you -- {pace} interrupted.",
        )
        from engine.verbs.basic import cmd_look

        cmd_look(character, "", game, after_move=True)
        return False
    return True


def tick_walks(game, *, advance_overland=None, engaged_check=None):
    """Advance paced player journeys at each pace's hop rate."""
    from engine.char_index import iter_characters
    from engine.npc_act import SilentSession

    if game is None:
        return
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    for character in iter_characters(game):
        focus = get_walk_focus(character)
        if focus is None:
            continue
        # ETA skip-road (Demon smoke): Cadence / Echoes also finish.
        if (focus.get("mode") or "") == "eta":
            hooks_mod.paced_travel_eta_tick(character, game, focus, now)
            continue
        sess = getattr(character, "session", None)
        if sess is None or isinstance(sess, SilentSession):
            continue
        if getattr(character, "is_npc", False):
            continue
        if getattr(character, "idle_mode", False):
            clear_walk_focus(character)
            continue
        pace = pace_of_focus(focus)
        step_every = int(PACE_STEP_EVERY.get(pace, WALK_STEP_EVERY) or 1)
        last = int(focus.get("last_step_tick", 0) or 0)
        if now - last < step_every:
            continue
        hops = int(PACE_HOPS_PER_ADVANCE.get(pace, 1) or 1)
        # Game hook may raise hops for Origin biology (vampire run, …).
        override = hooks_mod.paced_travel_hops_of(character, pace)
        if override is not None:
            try:
                hops = max(1, int(override))
            except (TypeError, ValueError):
                pass
        mode = focus.get("mode") or "room"
        for _ in range(max(1, hops)):
            focus = get_walk_focus(character)
            if focus is None:
                break
            mode = focus.get("mode") or "room"
            if mode == "overland" and advance_overland is not None:
                still = advance_overland(
                    character, game, focus, engaged_check=engaged_check,
                )
            else:
                still = _advance_room_walk(
                    character, game, focus, engaged_check=engaged_check,
                )
            if not still:
                break


def cmd_paced_travel(character, args, game, *, pace="walk"):
    """Lean walk/jog/run handler for in-zone room-graph pacing (basegame demo).

    SUPERS registers richer lifestyle/overland/vehicle detours via hooks;
    this shell covers stop, destination listing, and named room match only.
    """
    pace = normalize_pace(pace)
    raw = (args or "").strip()
    room = getattr(character, "location", None)
    if room is None:
        _send(character, "You are nowhere.")
        return

    head = raw.split(maxsplit=1)[0].lower() if raw else ""
    if head in ("stop", "off", "cancel", "clear", "done"):
        if has_walk_focus(character):
            clear_walk_focus(character, notice=f"{pace_gerund(pace)} cancelled.")
        else:
            _send(character, _not_traveling_msg(pace))
        return

    if not raw:
        if has_walk_focus(character):
            _send(character, format_walk_focus_status(character, game))
            return
        zone = getattr(room, "zone", None)
        if zone:
            labels = list_zone_destinations(character, game)
            _send(character, f"{pace_gerund(pace)} destinations in this area ({zone}):")
            if labels:
                for label in labels:
                    _send(character, f"  {label}")
            else:
                _send(character, "  (none found -- try room names from look)")
            return
        _send(character, f"No {pace} list here.")
        return

    zone = getattr(room, "zone", None)
    if not zone:
        _send(character, "You can't pace-travel from here.")
        return
    zone_rooms = rooms_in_zone(game, zone)
    dest, err = match_zone_room(raw, zone_rooms)
    if err:
        _send(character, err)
        return
    if dest is None:
        _send(character, f"No destination matches {raw!r} in this zone.")
        return
    msg = start_paced_walk(character, dest, game, pace=pace)
    if msg:
        _send(character, msg)
