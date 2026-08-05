"""paced_travel.py -- in-zone paced walking (one hop per tick).

Generic room-graph pathing, walk-focus stamps, and the shared
``walk`` / ``jog`` / ``run`` dispatch core. Overland foot travel,
lifestyle amenity resolution, and Cadence drive hops stay in the game
layer (SUPERS ``walk.py``) via hooks.

Pure logic: no sockets, no ``supers`` imports.
"""

from __future__ import annotations

import re
from collections import deque

from command_support import _move_one, _pull_followers
from engine import hooks
from engine.pathfind import next_step_toward, path_directions_to

# Cap so a bad path or loop cannot hang the single-threaded command loop.
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

# Player shorthand that maps onto room.resources tags (auto landmarks).
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
    return bool(_NOISY_LAST_WORD.match(token))


def _player_walk_room_label(room):
    """PLAYER-facing label for walk ambiguity -- never ``NAME[VNUM]`` chrome."""
    from engine.room_naming import bare_key
    from engine.room_vnum import describe_room

    label = describe_room(room, staff=False)
    if not label or label == "?":
        return "somewhere"
    leg = getattr(room, "legacy_key", None)
    if leg:
        hint = bare_key(str(leg))
        if hint and normalize_query(hint) != normalize_query(label):
            return f"{label} ({hint})"
    return label


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
    """Short walk labels for one room (unique last-word, title, key, resources)."""
    key = getattr(room, "key", "") or ""
    title = room.look_title() if hasattr(room, "look_title") else key
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
    key_n = normalize_query(getattr(room, "key", "") or "")
    title_n = normalize_query(
        room.look_title() if hasattr(room, "look_title") else key_n
    )
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


def match_zone_room(needle, zone_rooms):
    """Pick a room in ``zone_rooms`` for ``needle``, or raise ambiguity."""
    needle = normalize_query(needle)
    if not needle or not zone_rooms:
        return None, "No places to walk to here."

    candidates = []
    for room in zone_rooms:
        aliases = _alias_candidates_for_room(room, zone_rooms)
        key_n = normalize_query(room.key)
        tags = set(getattr(room, "resources", None) or [])
        if getattr(room, "hospital", False):
            tags.add("clinic")
        hinted = _RESOURCE_HINTS.get(needle) or ()
        if (
            needle in aliases
            or needle == key_n
            or needle in key_n
            or needle in tags
            or (hinted and tags.intersection(hinted))
            or (needle.split() and all(t in key_n for t in needle.split()))
        ):
            candidates.append(room)

    by_key = {room.key: room for room in candidates}
    return _pick_best_room(needle, list(by_key.values()))


def _expand_walk_neighbors(room, *, edge_ok, actor=None, game=None):
    """Yield ``(neighbor, hop)`` for BFS -- cardinals, enter, and zone exit."""
    for direction, neighbor in (room.exits or {}).items():
        if neighbor is None or neighbor is room:
            continue
        if not edge_ok(room, neighbor):
            continue
        yield neighbor, direction

    entries = getattr(room, "zone_entries", None) or {}
    hubs_seen = set()
    for hub in entries.values():
        if hub is None or hub.key in hubs_seen:
            continue
        hub_zone = getattr(hub, "zone", None)
        if hub_zone and not edge_ok(room, hub):
            continue
        alias = hooks.paced_travel_enter_alias(entries, hub)
        if not alias:
            continue
        hubs_seen.add(hub.key)
        yield hub, ("enter", alias)

    exit_to = getattr(room, "zone_exit_to", None)
    if exit_to is not None:
        yield exit_to, ("exit", None)


def next_hop_toward_destination(
    start, dest_key, actor=None, game=None, *, edge_ok=None,
):
    """BFS first hop toward ``dest_key``, including pocket enter/exit edges.

    ``edge_ok(from_room, neighbor)`` supplies passability; when omitted the
    hook ``paced_travel_edge_ok`` is used (always True when unset).
    """
    if start is None or not dest_key:
        return None
    if start.key == dest_key:
        return None
    from engine.systems.overland import is_virtual_room

    if is_virtual_room(start):
        return None

    if edge_ok is None:
        edge_ok = lambda fr, nb, a=actor, g=game: hooks.paced_travel_edge_ok(
            fr, nb, actor=a, game=g,
        )

    seen = {start}
    queue = deque()

    def _push(neighbor, hop):
        if neighbor is None or neighbor in seen:
            return
        if neighbor is start and hop is not None:
            return
        seen.add(neighbor)
        queue.append((neighbor, hop))

    def _expand(room, first_hop):
        for neighbor, hop in _expand_walk_neighbors(
            room, edge_ok=edge_ok, actor=actor, game=game,
        ):
            if neighbor in seen:
                continue
            use_hop = hop if first_hop is None else first_hop
            _push(neighbor, use_hop)

    _expand(start, None)
    while queue:
        room, first_hop = queue.popleft()
        if room.key == dest_key:
            return first_hop
        _expand(room, first_hop)
    return None


def path_hop_distances_from(start, actor=None, game=None, max_nodes=600, *, edge_ok=None):
    """BFS hop counts from ``start`` to reachable rooms (cap ``max_nodes``)."""
    if start is None:
        return {}
    from engine.systems.overland import is_virtual_room

    if is_virtual_room(start):
        return {start.key: 0}

    if edge_ok is None:
        edge_ok = lambda fr, nb, a=actor, g=game: hooks.paced_travel_edge_ok(
            fr, nb, actor=a, game=g,
        )

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
        for neighbor, _hop in _expand_walk_neighbors(
            room, edge_ok=edge_ok, actor=actor, game=game,
        ):
            if neighbor in seen:
                continue
            seen.add(neighbor)
            distances[neighbor.key] = next_dist
            queue.append((neighbor, next_dist))

    if game is not None:
        game._walk_hop_dist_cache = {
            "tick": tick,
            "start_key": start.key,
            "actor": actor_token,
            "max_nodes": max_nodes,
            "distances": distances,
        }
    return distances


def path_hop_count(start, dest, actor=None, game=None, max_nodes=600, *, edge_ok=None):
    """Return BFS hop count from ``start`` to ``dest``, or None if blocked."""
    if start is None or dest is None:
        return None
    if start is dest or getattr(start, "key", None) == getattr(dest, "key", None):
        return 0
    dest_key = getattr(dest, "key", None)
    if not dest_key:
        return None
    hops = path_hop_distances_from(
        start, actor=actor, game=game, max_nodes=max_nodes, edge_ok=edge_ok,
    ).get(dest_key)
    return hops


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
    label = focus.get("dest_label") or focus.get("dest_room_key") or "?"
    mode = focus.get("mode") or "room"
    steps = int(focus.get("steps", 0) or 0)
    pace = pace_of_focus(focus)
    verb = pace_gerund(pace)
    stop_hint = f"{pace} stop" if pace != "walk" else "walk stop"
    return (
        f"{verb} toward {label} ({mode}, {steps} steps so far). "
        f"Type {stop_hint} to cancel."
    )


def _stamp_walk_focus(actor, game, *, mode, dest_label, dest_room_key=None,
                      overland_macro=None, enter_alias=None, pace="walk"):
    """Replace walk_focus with a fresh paced journey stamp."""
    tick = 0
    if game is not None:
        tick = int(getattr(game, "game_time_ticks", 0) or 0)
    focus = {
        "mode": mode,
        "dest_room_key": dest_room_key,
        "dest_label": dest_label,
        "overland_macro": (
            list(overland_macro) if overland_macro is not None else None
        ),
        "enter_alias": enter_alias,
        "pace": normalize_pace(pace),
        "started_tick": tick,
        "last_step_tick": tick,
        "steps": 0,
    }
    actor.walk_focus = focus
    return focus


def _send(character, text):
    """Send one line when the actor has a live Session."""
    session = getattr(character, "session", None)
    if session is not None:
        session.send(text)


def _engaged(character):
    """True when this character has a live fight target."""
    return getattr(character, "target", None) is not None


def _engaged_refuse_move(character):
    """Player-facing block when walking away mid-fight."""
    if not _engaged(character):
        return None
    msg = hooks.paced_travel_engaged_refuse(character)
    if msg:
        return msg
    return (
        "You're squared up -- disengage first or let the first beat land."
    )


def _default_player_hop(character, hop, game, quiet=True):
    """Apply one hop: cardinal via ``_move_one``, or enter/exit."""
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
        gait = hooks.paced_travel_gait_of(character)
        character.session.send(f"You {gait} {hop}.")
    return True


def step_toward_room(actor, dest, game, *, mode="player", quiet=True, edge_ok=None):
    """Take one hop toward ``dest`` (player hop or Cadence hook)."""
    if actor is None or dest is None:
        return False
    if getattr(actor, "location", None) is dest:
        return False
    if mode == "cadence":
        return hooks.paced_travel_cadence_step(actor, dest, game)

    dest_key = getattr(dest, "key", None)
    if not dest_key:
        return False
    hop = next_hop_toward_destination(
        actor.location, dest_key, actor=actor, game=game, edge_ok=edge_ok,
    )
    if hop is None:
        return False
    before = actor.location
    ok = hooks.paced_travel_player_hop(actor, hop, game, quiet=quiet)
    if not ok:
        return False
    return actor.location is not before


def start_paced_walk(character, dest_room, game, *, label=None, pace="walk"):
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
    drive_msg = hooks.paced_travel_drive_to(character, dest_room, game)
    if drive_msg is not None:
        return drive_msg
    if _engaged(character):
        return _engaged_refuse_move(character)
    if getattr(character, "asleep", False):
        return "You're asleep -- type 'wake' before you can move."

    dest_label = label or _player_walk_room_label(dest_room)
    _stamp_walk_focus(
        character, game,
        mode="room",
        dest_label=dest_label,
        dest_room_key=dest_room.key,
        pace=pace,
    )
    _send(character, f"{pace_gerund(pace)} toward {dest_label}...")
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
    if _engaged(character):
        clear_walk_focus(
            character,
            notice=f"Something engages you -- {pace} interrupted.",
        )
        from engine.verbs.basic import cmd_look

        cmd_look(character, "", game, after_move=True)
        return ""
    return ""


def walk_to(character, dest_room, game, *, pace="walk"):
    """Start a paced walk to ``dest_room`` (compat name for callers)."""
    return start_paced_walk(character, dest_room, game, pace=pace)


def _advance_room_walk(character, game, focus):
    """One paced hop for a room-mode walk_focus. Returns True if still walking."""
    dest_key = focus.get("dest_room_key")
    dest = (getattr(game, "rooms", None) or {}).get(dest_key) if dest_key else None
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

        _send(character, f"You arrive at {label}.")
        cmd_look(character, "", game, after_move=True)
        return False
    if _engaged(character):
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

        _send(character, f"You arrive at {label}.")
        cmd_look(character, "", game, after_move=True)
        return False
    if not moved or character.location is before:
        clear_walk_focus(
            character,
            notice=f"{pace_gerund(pace)} interrupted -- no path to {label}.",
        )
        return False
    if _engaged(character):
        clear_walk_focus(
            character,
            notice=f"Something engages you -- {pace} interrupted.",
        )
        from engine.verbs.basic import cmd_look

        cmd_look(character, "", game, after_move=True)
        return False
    return True


def tick_walks(game):
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
        for _ in range(max(1, hops)):
            focus = get_walk_focus(character)
            if focus is None:
                break
            mode = focus.get("mode") or "room"
            if mode == "overland":
                still = hooks.paced_travel_overland_advance(character, game, focus)
            else:
                still = _advance_room_walk(character, game, focus)
            if not still:
                break


def cmd_paced_travel(character, args, game, *, pace="walk"):
    """Generic room-graph dispatch for ``walk`` / ``jog`` / ``run``.

    Game layers handle homestead, vehicles, lifestyle tokens, and overland
    lists before calling this for stop / zone-target resolution.
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
            clear_walk_focus(
                character,
                notice=f"{pace_gerund(pace)} cancelled.",
            )
        else:
            _send(character, _not_traveling_msg(pace))
        return

    if raw and hooks.paced_travel_overland_handler(character, args, game, pace):
        return

    if not raw:
        if has_walk_focus(character):
            _send(character, format_walk_focus_status(character, game))
            return
        listed = hooks.paced_travel_list_destinations(character, game, pace)
        if listed:
            return
        _send(
            character,
            f"No {pace} list here. Use exits from look, or name a destination.",
        )
        return

    zone = getattr(room, "zone", None)
    if zone:
        zone_rooms = hooks.paced_travel_zone_rooms(game, zone)
        dest, err = match_zone_room(raw, zone_rooms)
        if err:
            _send(character, err)
            return
        msg = start_paced_walk(character, dest, game, pace=pace)
        if msg:
            _send(character, msg)
        return

    _send(
        character,
        f"{pace_gerund(pace)} works inside settlement zones. "
        "Type look for exits here.",
    )
