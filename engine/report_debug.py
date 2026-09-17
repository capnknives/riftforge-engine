"""
report_debug.py -- shared stamp APIs for bug-report debug telemetry.

Engine-safe: stdlib only, no SUPERS imports. Later slices (occupy, vehicle,
era, signature, gates) fill bodies by calling the ``note_*`` helpers here;
``report_context`` and ``report_modules`` read the snapshots at report time.

Abort freshness exists because a stale copyover abort from days ago (e.g.
Sep 2) was riding on every bug report and drowning out current triage --
only include abort facts when they are younger than COPYOVER_ABORT_MAX_AGE_S.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from datetime import datetime, timezone

# Veil rewrite abort older than this is triage noise, not a live signal.
COPYOVER_ABORT_MAX_AGE_S = 900

# Last vehicle stamp expires after this many seconds (park/dismount class).
LAST_VEHICLE_TTL_S = 300

# Ring buffer size for recent follow-bond group events on a character.
GROUP_EVENT_MAX = 4

# Cap on actor keys mentioned in a single bug-report sidecar (Wave 0).
MENTIONED_ACTORS_CAP = 3

# Cadence ping-pong: same-room bounce count within a short tick window.
PINGPONG_COUNT_THRESHOLD = 6
PINGPONG_WINDOW_TICKS = 30

_OK_LOG_NAME = ".copyover_last_ok.json"


def _parse_iso_utc_seconds(at_text):
    """Parse an ISO-8601 UTC timestamp string into epoch seconds.

    Accepts trailing ``Z`` (common on copyover stamp files). Returns None
    when the string is missing or not parseable -- callers treat that as
    "no timestamp" rather than raising.
    """
    if at_text is None:
        return None
    text = str(at_text).strip()
    if not text:
        return None
    # ``fromisoformat`` on 3.11+ accepts ``Z``; normalize for older paths.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def copyover_abort_is_fresh(abort, *, now=None):
    """True when a copyover abort dict's ``at`` stamp is younger than the cap.

    Missing or unparseable ``at`` returns False so stale log files cannot
    pollute every new bug report indefinitely.
    """
    if not isinstance(abort, dict):
        return False
    at_ts = _parse_iso_utc_seconds(abort.get("at"))
    if at_ts is None:
        return False
    if now is None:
        now = time.time()
    try:
        age = float(now) - float(at_ts)
    except (TypeError, ValueError):
        return False
    return 0 <= age < float(COPYOVER_ABORT_MAX_AGE_S)


def _read_json_dict(path):
    """Load one JSON object from ``path``; return None on any failure."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def last_copyover_ok_snap(report_dir):
    """Read the last successful copyover stamp from disk.

    Tries ``report_dir`` first, then cwd, then copyover's stamp root
    (``RIFTFORGE_COPYOVER_STAMP_DIR`` / ``ok_log_path``) so abort and ok
    stamps resolve from the same directory. Shape is ``{at, sha?}``.
    """
    roots = []
    if report_dir:
        roots.append(report_dir)
    roots.append(os.getcwd())
    try:
        # Same directory ``record_last_abort`` / ``record_last_ok`` use.
        from engine.copyover import ok_log_path

        stamp_root = os.path.dirname(ok_log_path())
        if stamp_root:
            roots.append(stamp_root)
    except Exception:
        pass
    seen = set()
    for root in roots:
        if not root or root in seen:
            continue
        seen.add(root)
        data = _read_json_dict(os.path.join(root, _OK_LOG_NAME))
        if not data:
            continue
        at = data.get("at")
        if not at:
            continue
        out = {"at": str(at)}
        sha = data.get("sha")
        if sha:
            out["sha"] = str(sha)
        return out
    return None


def _vehicle_ring(character):
    """Return (or create) the per-character last-vehicle deque.

    ``deque`` is a fixed-max-length ring: appending when full drops the
    oldest entry automatically -- ideal for "last two vehicles" telemetry.
    """
    ring = getattr(character, "_last_vehicle_ring", None)
    if ring is None:
        ring = deque(maxlen=2)
        character._last_vehicle_ring = ring
    return ring


def note_last_vehicle(character, *, id, kind, reason):
    """Stamp one vehicle board/park/dismount event onto the character."""
    if character is None:
        return
    try:
        entry = {
            "id": str(id) if id is not None else None,
            "kind": str(kind) if kind is not None else None,
            "reason": str(reason) if reason is not None else None,
            "at": time.time(),
        }
        _vehicle_ring(character).append(
            {k: v for k, v in entry.items() if v is not None},
        )
    except Exception:
        pass


def last_vehicle_snap(character):
    """Newest vehicle stamp when still inside LAST_VEHICLE_TTL_S, else None."""
    if character is None:
        return None
    try:
        ring = getattr(character, "_last_vehicle_ring", None)
        if not ring:
            return None
        newest = ring[-1]
        if not isinstance(newest, dict):
            return None
        at = newest.get("at")
        if at is None:
            return None
        age = time.time() - float(at)
        if age > float(LAST_VEHICLE_TTL_S):
            return None
        return {
            k: newest[k]
            for k in ("id", "kind", "reason", "at")
            if k in newest
        } or None
    except Exception:
        return None


def note_last_hop(character, *, verb, dest, ok, refuse_code=None):
    """Stamp the last room-hop attempt (enter/travel gate class)."""
    if character is None:
        return
    try:
        entry = {
            "verb": str(verb) if verb is not None else None,
            "dest": str(dest) if dest is not None else None,
            "ok": bool(ok),
        }
        if refuse_code is not None:
            entry["refuse_code"] = str(refuse_code)
        character._last_hop = entry
    except Exception:
        pass


def last_hop_snap(character):
    """Return the last hop stamp dict, or None."""
    if character is None:
        return None
    try:
        snap = getattr(character, "_last_hop", None)
        return dict(snap) if isinstance(snap, dict) and snap else None
    except Exception:
        return None


def _group_event_ring(character):
    """Return (or create) the per-character group-event deque."""
    ring = getattr(character, "_group_event_ring", None)
    if ring is None:
        ring = deque(maxlen=GROUP_EVENT_MAX)
        character._group_event_ring = ring
    return ring


def note_group_event(character, *, tick, reason, leader, members, rooms):
    """Stamp one follow-bond group lifecycle event onto the character.

    ``rooms`` is a dict of member_key -> room_key (not a list of keys).
    ``list(rooms)`` on a dict would drop the locations and break triage.
    """
    if character is None:
        return
    try:
        if isinstance(rooms, dict):
            rooms_out = {
                str(k): str(v)
                for k, v in rooms.items()
                if k is not None and v is not None
            }
        else:
            rooms_out = {}
        entry = {
            "tick": int(tick) if tick is not None else None,
            "reason": str(reason) if reason is not None else None,
            "leader": str(leader) if leader is not None else None,
            "members": list(members) if members is not None else [],
            "rooms": rooms_out,
        }
        _group_event_ring(character).append(entry)
    except Exception:
        pass


def last_group_event_snap(character):
    """Newest group event from the ring, or None."""
    if character is None:
        return None
    try:
        ring = getattr(character, "_group_event_ring", None)
        if not ring:
            return None
        newest = ring[-1]
        if not isinstance(newest, dict):
            return None
        out = {}
        for k in ("tick", "reason", "leader", "members", "rooms"):
            if k not in newest:
                continue
            val = newest[k]
            # Copy containers so triage cannot mutate the live ring.
            if k == "rooms" and isinstance(val, dict):
                val = dict(val)
            elif k == "members" and isinstance(val, (list, tuple)):
                val = list(val)
            out[k] = val
        return out or None
    except Exception:
        return None


def note_last_signature(session, *, style, family, opened_fight, fight_id):
    """Stamp the last combat-signature style open onto the login Session."""
    if session is None:
        return
    try:
        session._last_signature = {
            "style": str(style) if style is not None else None,
            "family": str(family) if family is not None else None,
            "opened_fight": bool(opened_fight),
            "fight_id": (
                str(fight_id) if fight_id is not None else None
            ),
        }
    except Exception:
        pass


def last_signature_snap(session):
    """Return the last signature stamp dict, or None."""
    if session is None:
        return None
    try:
        snap = getattr(session, "_last_signature", None)
        return dict(snap) if isinstance(snap, dict) and snap else None
    except Exception:
        return None


def _pingpong_eligible(character):
    """True when Cadence ping-pong telemetry should run on this body.

    NPCs and offline Echoes always qualify. Live players only when they
    are in ``idle_mode`` (staff idlemode / away-from-keyboard class) so
    ordinary online play does not spam ops logs.
    """
    if character is None:
        return False
    session = getattr(character, "session", None)
    if session is None:
        return True
    if getattr(character, "is_npc", False):
        return True
    return bool(getattr(character, "idle_mode", False))


def note_cadence_pingpong_on_move(character, from_room, dest, game):
    """Stamp room-bounce loops onto ``character._cadence_pingpong``.

    Called from ``engine.hooks.after_move_crossing`` after a successful
    step. When the same body leaves and returns to one room repeatedly
    (A→B→A→B…) ``count`` climbs; at ``PINGPONG_COUNT_THRESHOLD`` within
    ``PINGPONG_WINDOW_TICKS`` we emit one ops line per tick via
    ``log_util.ops_once_per_tick`` so stuck Cadence pathfind is visible
    in Docker logs without importing SUPERS into the engine hook.
    """
    if not _pingpong_eligible(character) or game is None:
        return
    if from_room is None or dest is None:
        return
    from_key = getattr(from_room, "key", None)
    dest_key = getattr(dest, "key", None)
    if not from_key or not dest_key or from_key == dest_key:
        return

    try:
        tick = int(getattr(game, "game_time_ticks", 0) or 0)
    except (TypeError, ValueError):
        tick = 0

    state = getattr(character, "_cadence_pingpong", None)
    if not isinstance(state, dict):
        state = {}

    bounce_room = state.get("room_key")
    count = int(state.get("count", 0) or 0)
    window_start = int(state.get("window_start_tick", tick) or tick)

    # Drop stale windows so an old bounce pattern does not fire forever.
    if tick - window_start > int(PINGPONG_WINDOW_TICKS):
        bounce_room = None
        count = 0
        window_start = tick

    if bounce_room and dest_key == bounce_room:
        # Returned to the anchor room -- one ping-pong leg complete.
        count += 1
    elif bounce_room and from_key == bounce_room:
        # Outbound from the same anchor (A→B after B→A). Keep the pair
        # so A↔B oscillation can climb to PINGPONG_COUNT_THRESHOLD.
        pass
    else:
        # First leg or a new pair: remember the room we just left.
        bounce_room = from_key
        count = 0
        window_start = tick

    character._cadence_pingpong = {
        "room_key": bounce_room,
        "count": count,
        "window_start_tick": window_start,
    }

    if count < int(PINGPONG_COUNT_THRESHOLD):
        return

    actor_key = getattr(character, "key", None) or "?"
    try:
        from engine import log_util

        log_util.ops_once_per_tick(
            game,
            f"pingpong:{actor_key}",
            "cadence",
            (
                f"pingpong actor={actor_key} "
                f"room={bounce_room} count={count}"
            ),
        )
    except Exception:
        pass
