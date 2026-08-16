"""
channels.py -- unified communication channel system (engine).

One registry drives global channels (ooc, wiznet, GM-created), room speech
history (say), and private tell history. Built-in channels register at import;
staff-created channels persist in meta and reload on boot.

Scopes:
  global  -- every eligible online session (ooc, wiznet, custom)
  room    -- everyone in one room (say ring on game keyed by room)
  private -- one-to-one tells (per-session ring)

Audience (who may hear / speak):
  all, staff, gm, origin:<id> (via engine.hooks.channel_audience_ok)

Stdlib only.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from engine import hooks

DEFAULT_RING_MAX = 20
CUSTOM_META_KEY = "channel_custom_registry"

SCOPE_GLOBAL = "global"
SCOPE_ROOM = "room"
SCOPE_PRIVATE = "private"

AUDIENCE_ALL = "all"
AUDIENCE_STAFF = "staff"
AUDIENCE_GM = "gm"

# format kinds for render + gateway export
FORMAT_OOC = "ooc"
FORMAT_WIZNET = "wiznet"
FORMAT_SAY = "say"
FORMAT_TELL = "tell"
FORMAT_PLAIN = "plain"

_VERB_RE = re.compile(r"^[a-z][a-z0-9_]{1,15}$")


@dataclass(frozen=True)
class ChannelSpec:
    """One channel definition (built-in or staff-authored)."""

    name: str
    verb: str
    game_attr: str
    meta_key: str
    scope: str
    audience: str
    replay_header: str
    empty_message: str
    usage_message: str
    ring_max: int = DEFAULT_RING_MAX
    builtin: bool = True
    gateway_stitch: bool = False
    format: str = FORMAT_PLAIN
    prefix: str = ""
    color_role: str = "muted"
    title: str = ""


_BUILTIN: dict[str, ChannelSpec] = {}
_VERB_INDEX: dict[str, str] = {}


def register_channel(spec: ChannelSpec) -> ChannelSpec:
    """Register a channel (built-in or custom). Idempotent by ``spec.name``."""
    _BUILTIN[spec.name] = spec
    _VERB_INDEX[spec.verb.lower()] = spec.name
    return spec


def get_channel(name: str) -> Optional[ChannelSpec]:
    return _BUILTIN.get(name)


def lookup_verb(verb: str) -> Optional[ChannelSpec]:
    """Return spec when *verb* names a global custom/builtin channel."""
    key = (verb or "").strip().lower()
    if not key:
        return None
    name = _VERB_INDEX.get(key)
    if name is None:
        return None
    spec = get_channel(name)
    if spec is None or spec.scope != SCOPE_GLOBAL:
        return None
    return spec


def all_channels() -> tuple[ChannelSpec, ...]:
    return tuple(_BUILTIN.values())


def gateway_stitch_channels() -> tuple[ChannelSpec, ...]:
    return tuple(
        s for s in _BUILTIN.values()
        if s.gateway_stitch and s.scope == SCOPE_GLOBAL
    )


def stitch_channel_for_command(text: str) -> Optional[str]:
    """Return channel name when *text* is a bare or speak stitch verb."""
    lower = (text or "").strip().lower()
    if not lower:
        return None
    # First token only — tolerate tabs / runs of spaces after the verb.
    head = lower.split(None, 1)[0]
    for spec in gateway_stitch_channels():
        if head == spec.verb:
            return spec.name
    return None


# ---------------------------------------------------------------------------
# Game attachment
# ---------------------------------------------------------------------------


def init_game(game) -> None:
    """Attach empty ring deques for every global channel on ``game``."""
    if not hasattr(game, "_room_say_rings") or game._room_say_rings is None:
        game._room_say_rings = {}
    for spec in _BUILTIN.values():
        if spec.scope != SCOPE_GLOBAL:
            continue
        if getattr(game, spec.game_attr, None) is None:
            setattr(game, spec.game_attr, deque(maxlen=spec.ring_max))


def ring(game, channel_name: str):
    """Return the game's ring deque for a global channel."""
    spec = get_channel(channel_name)
    if spec is None or spec.scope != SCOPE_GLOBAL:
        return None
    return getattr(game, spec.game_attr, None)


def room_say_ring(game, room) -> deque:
    """Last-N say lines for one room (keyed by room.key)."""
    if room is None:
        return deque(maxlen=DEFAULT_RING_MAX)
    rings = getattr(game, "_room_say_rings", None)
    if rings is None:
        game._room_say_rings = {}
        rings = game._room_say_rings
    key = getattr(room, "key", None) or id(room)
    buf = rings.get(key)
    if buf is None:
        buf = deque(maxlen=DEFAULT_RING_MAX)
        rings[key] = buf
    return buf


def session_tell_ring(session) -> deque:
    """Per-session tell history (in + out)."""
    if session is None:
        return deque(maxlen=DEFAULT_RING_MAX)
    buf = getattr(session, "tell_history", None)
    if buf is None:
        buf = deque(maxlen=DEFAULT_RING_MAX)
        session.tell_history = buf
    return buf


# ---------------------------------------------------------------------------
# Audience / mute gates
# ---------------------------------------------------------------------------


def _is_muted(character) -> bool:
    return bool(getattr(character, "muted", False))


def audience_ok(viewer, audience: str, game) -> bool:
    """True when *viewer* may hear/speak on an audience-restricted channel."""
    aud = (audience or AUDIENCE_ALL).strip().lower()
    if aud in ("", AUDIENCE_ALL):
        return True
    if aud == AUDIENCE_STAFF:
        from engine.command_support import _is_staff_gm
        return _is_staff_gm(viewer)
    if aud == AUDIENCE_GM:
        from engine.command_support import _is_gm
        return _is_gm(viewer)
    if aud.startswith("origin:"):
        return hooks.channel_audience_ok(viewer, aud, game)
    return hooks.channel_audience_ok(viewer, aud, game)


def can_speak(character, spec: ChannelSpec, game) -> bool:
    if character is None or getattr(character, "session", None) is None:
        return False
    if _is_muted(character):
        return False
    return audience_ok(character, spec.audience, game)


def can_hear(character, spec: ChannelSpec, game) -> bool:
    if character is None or getattr(character, "session", None) is None:
        return False
    return audience_ok(character, spec.audience, game)


# ---------------------------------------------------------------------------
# Append + gateway mirror
# ---------------------------------------------------------------------------


def append(
    game,
    channel_name: str,
    entry: Any,
    *,
    gateway_plain: Optional[str] = None,
) -> None:
    """Append one entry to a global channel ring + optional gateway mirror."""
    spec = get_channel(channel_name)
    if spec is None or spec.scope != SCOPE_GLOBAL or entry is None:
        return
    history = ring(game, channel_name)
    if history is not None:
        history.append(entry)
    plain = (gateway_plain or "").strip()
    if not plain and spec.gateway_stitch:
        plain = (export_gateway_plain_line(channel_name, entry, game) or "").strip()
    if plain and spec.gateway_stitch:
        _schedule_gateway_mirror(game, channel_name, plain)


def append_room_say(game, room, plain_line: str) -> None:
    text = (plain_line or "").strip()
    if not text or room is None:
        return
    room_say_ring(game, room).append(text)


def append_tell(session, plain_line: str) -> None:
    text = (plain_line or "").strip()
    if not text or session is None:
        return
    session_tell_ring(session).append(text)


def is_empty(game, channel_name: str) -> bool:
    history = ring(game, channel_name)
    return not history


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load_json_list(conn, meta_key: str, *, ring_max: int) -> list:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (meta_key,)
    ).fetchone()
    if not row or not row[0]:
        return []
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    if len(data) > ring_max:
        data = data[-ring_max:]
    return data


def _save_json_list(conn, meta_key: str, items: list, *, ring_max: int) -> None:
    payload_items = list(items)
    if len(payload_items) > ring_max:
        payload_items = payload_items[-ring_max:]
    payload = json.dumps(payload_items, separators=(",", ":"))
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (meta_key, payload),
        )


def _normalize_ooc_loaded(data: list) -> list:
    entries = []
    for item in data:
        if isinstance(item, str):
            entries.append(item)
        elif isinstance(item, dict) and item.get("speaker"):
            entries.append(item)
    return entries


def _serialize_ooc_ring(game) -> list:
    history = ring(game, "ooc") or []
    entries = []
    for item in history:
        if isinstance(item, str):
            entries.append(item)
        elif isinstance(item, dict) and item.get("speaker"):
            entries.append(item)
    return entries


def _serialize_wiznet_ring(game) -> list:
    history = ring(game, "wiznet") or []
    return [line for line in history if isinstance(line, str) and line.strip()]


def _serialize_plain_ring(game, channel_name: str) -> list:
    history = ring(game, channel_name) or []
    out = []
    for item in history:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            out.append(item)
    return out


def load_channel(conn, channel_name: str) -> list:
    spec = get_channel(channel_name)
    if spec is None:
        return []
    raw = _load_json_list(conn, spec.meta_key, ring_max=spec.ring_max)
    if spec.format == FORMAT_OOC:
        return _normalize_ooc_loaded(raw)
    if spec.format == FORMAT_WIZNET:
        return [line for line in raw if isinstance(line, str)]
    return raw


def save_channel(conn, game, channel_name: str) -> None:
    spec = get_channel(channel_name)
    if spec is None:
        return
    if spec.format == FORMAT_OOC:
        items = _serialize_ooc_ring(game)
    elif spec.format == FORMAT_WIZNET:
        items = _serialize_wiznet_ring(game)
    else:
        items = _serialize_plain_ring(game, channel_name)
    _save_json_list(conn, spec.meta_key, items, ring_max=spec.ring_max)


def load_all(game, conn) -> None:
    """Refill global rings + register custom channel defs from meta."""
    _load_custom_registry(conn)
    init_game(game)
    for spec in _BUILTIN.values():
        if spec.scope != SCOPE_GLOBAL:
            continue
        target = ring(game, spec.name)
        if target is None:
            setattr(game, spec.game_attr, deque(maxlen=spec.ring_max))
            target = ring(game, spec.name)
        for line in load_channel(conn, spec.name):
            target.append(line)


def save_all(conn, game) -> None:
    for spec in _BUILTIN.values():
        if spec.scope == SCOPE_GLOBAL and not spec.builtin:
            save_channel(conn, game, spec.name)
        elif spec.builtin and spec.scope == SCOPE_GLOBAL:
            save_channel(conn, game, spec.name)
    _save_custom_registry(conn, game)


def entries(game, channel_name: str) -> tuple:
    history = ring(game, channel_name)
    if history is None:
        return ()
    return tuple(history)


# ---------------------------------------------------------------------------
# Custom channel CRUD (staff)
# ---------------------------------------------------------------------------


def _custom_payload(game) -> dict:
    payload = getattr(game, "_channel_custom_payload", None)
    if not isinstance(payload, dict):
        payload = {"defs": [], "removed": []}
        game._channel_custom_payload = payload
    if "defs" not in payload:
        payload["defs"] = []
    if "removed" not in payload:
        payload["removed"] = []
    return payload


def _spec_from_def(defn: dict) -> Optional[ChannelSpec]:
    name = str(defn.get("name") or "").strip().lower()
    verb = str(defn.get("verb") or name).strip().lower()
    if not name or not _VERB_RE.match(verb):
        return None
    title = str(defn.get("title") or verb).strip()
    audience = str(defn.get("audience") or AUDIENCE_ALL).strip().lower()
    prefix = str(defn.get("prefix") or f"[{verb.upper()}]").strip()
    color = str(defn.get("color_role") or "muted").strip()
    ring_max = int(defn.get("ring_max") or DEFAULT_RING_MAX)
    ring_max = max(5, min(ring_max, 50))
    return ChannelSpec(
        name=name,
        verb=verb,
        game_attr=f"channel_{name}_history",
        meta_key=f"channel_history_{name}",
        scope=SCOPE_GLOBAL,
        audience=audience,
        replay_header=f"Recent {title} (last {ring_max}):",
        empty_message=(
            f"No recent {title}. Type '{verb} <message>' to speak."
        ),
        usage_message=f"Usage: {verb} <message>  (bare {verb} replays history)",
        ring_max=ring_max,
        builtin=False,
        gateway_stitch=True,
        format=FORMAT_PLAIN,
        prefix=prefix,
        color_role=color,
        title=title,
    )


def _load_custom_registry(conn) -> None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (CUSTOM_META_KEY,)
    ).fetchone()
    if not row or not row[0]:
        return
    try:
        payload = json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    for defn in payload.get("defs") or []:
        if not isinstance(defn, dict):
            continue
        spec = _spec_from_def(defn)
        if spec is not None:
            register_channel(spec)


def _save_custom_registry(conn, game) -> None:
    payload = _custom_payload(game)
    defs = []
    for spec in _BUILTIN.values():
        if spec.builtin or spec.scope != SCOPE_GLOBAL:
            continue
        defs.append({
            "name": spec.name,
            "verb": spec.verb,
            "title": spec.title or spec.verb,
            "audience": spec.audience,
            "prefix": spec.prefix,
            "color_role": spec.color_role,
            "ring_max": spec.ring_max,
        })
    payload["defs"] = defs
    blob = json.dumps(payload, separators=(",", ":"))
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (CUSTOM_META_KEY, blob),
        )


def list_custom_channels() -> list[ChannelSpec]:
    return [s for s in _BUILTIN.values() if not s.builtin and s.scope == SCOPE_GLOBAL]


def save_custom_registry(conn, game) -> None:
    """Persist staff-authored channel definitions to meta."""
    _save_custom_registry(conn, game)


def create_custom_channel(
    game,
    *,
    name: str,
    verb: str,
    title: str = "",
    audience: str = AUDIENCE_ALL,
    prefix: str = "",
    color_role: str = "muted",
    ring_max: int = DEFAULT_RING_MAX,
) -> tuple[bool, str]:
    """Staff: register a new global channel. Returns (ok, message)."""
    defn = {
        "name": (name or "").strip().lower(),
        "verb": (verb or name or "").strip().lower(),
        "title": (title or verb or name or "").strip(),
        "audience": (audience or AUDIENCE_ALL).strip().lower(),
        "prefix": prefix,
        "color_role": color_role,
        "ring_max": ring_max,
    }
    spec = _spec_from_def(defn)
    if spec is None:
        return False, "Invalid channel name/verb (lowercase letters, digits, underscore; 2-16 chars)."
    if spec.verb in ("say", "tell", "ooc", "wiznet", "emote", "who", "help"):
        return False, f"Verb '{spec.verb}' is reserved."
    existing = get_channel(spec.name)
    if existing is not None:
        if existing.builtin:
            return False, f"Built-in channel '{spec.name}' cannot be replaced."
        return False, f"Channel '{spec.name}' already exists."
    verb_owner = _VERB_INDEX.get(spec.verb)
    if verb_owner and verb_owner != spec.name:
        return False, f"Verb '{spec.verb}' is already a channel."
    register_channel(spec)
    setattr(game, spec.game_attr, deque(maxlen=spec.ring_max))
    return True, f"Channel '{spec.title or spec.name}' created (verb: {spec.verb}, audience: {spec.audience})."


def remove_custom_channel(game, name: str) -> tuple[bool, str]:
    """Staff: remove a non-built-in global channel."""
    key = (name or "").strip().lower()
    spec = get_channel(key)
    if spec is None:
        return False, f"No channel named '{key}'."
    if spec.builtin:
        return False, f"Built-in channel '{key}' cannot be removed."
    _BUILTIN.pop(key, None)
    _VERB_INDEX.pop(spec.verb, None)
    payload = _custom_payload(game)
    payload.setdefault("removed", []).append(key)
    return True, f"Channel '{key}' removed."


def set_channel_audience(game, name: str, audience: str) -> tuple[bool, str]:
    key = (name or "").strip().lower()
    spec = get_channel(key)
    if spec is None:
        return False, f"No channel named '{key}'."
    if spec.builtin and key in ("ooc", "wiznet"):
        return False, f"Built-in '{key}' audience is fixed."
    aud = (audience or AUDIENCE_ALL).strip().lower()
    new = ChannelSpec(
        **{**asdict(spec), "audience": aud},
    )
    register_channel(new)
    return True, f"Channel '{key}' audience set to {aud}."


# ---------------------------------------------------------------------------
# Render + replay
# ---------------------------------------------------------------------------


def send_empty_hint(character, channel_name: str) -> None:
    spec = get_channel(channel_name)
    session = getattr(character, "session", None)
    if spec is None or session is None:
        return
    session.send(spec.empty_message)


def send_replay_header(character, channel_name: str) -> None:
    spec = get_channel(channel_name)
    session = getattr(character, "session", None)
    if spec is None or session is None:
        return
    session.send(spec.replay_header)


def render_ooc_entry(entry, viewer, game) -> str:
    from engine import ooc_channel
    return ooc_channel.format_ooc_history_entry(entry, viewer, game)


def replay_wiznet_entry(entry) -> str:
    from engine import style
    plain = entry if isinstance(entry, str) else str(entry)
    return style.paint("absinthe_green", plain)


def render_plain_entry(spec: ChannelSpec, entry, viewer) -> str:
    from engine import display_prefs
    from engine import style

    if isinstance(entry, dict):
        plain = str(entry.get("plain") or "")
    else:
        plain = str(entry or "")
    plain = plain.strip()
    if not plain:
        return ""
    display_prefs.ensure_display_defaults(viewer)
    if getattr(viewer, "screenreader", False) or not getattr(viewer, "use_color", True):
        return plain
    return style.paint_for(viewer, spec.color_role, plain)


def replay_global(character, game, channel_name: str) -> None:
    """Bare-verb history replay for one global channel."""
    spec = get_channel(channel_name)
    session = getattr(character, "session", None)
    if spec is None or session is None:
        return
    if not can_hear(character, spec, game):
        session.send("You cannot access that channel.")
        return
    if is_empty(game, channel_name):
        send_empty_hint(character, channel_name)
        return
    send_replay_header(character, channel_name)
    for entry in entries(game, channel_name):
        if spec.format == FORMAT_OOC:
            line = render_ooc_entry(entry, character, game)
        elif spec.format == FORMAT_WIZNET:
            line = replay_wiznet_entry(entry)
        else:
            line = render_plain_entry(spec, entry, character)
        if line:
            session.send(line)
    session.send("")


def replay_room_say(character, game) -> None:
    session = getattr(character, "session", None)
    room = getattr(character, "location", None)
    if session is None or room is None:
        return
    buf = room_say_ring(game, room)
    if not buf:
        session.send("No recent speech here. Type 'say <message>' to speak.")
        return
    session.send(f"Recent speech here (last {DEFAULT_RING_MAX}):")
    for line in buf:
        session.send(line)
    session.send("")


def replay_tells(character) -> None:
    session = getattr(character, "session", None)
    if session is None:
        return
    buf = session_tell_ring(session)
    if not buf:
        session.send("No recent tells. Type 'tell <name> <message>' to whisper.")
        return
    session.send(f"Recent tells (last {DEFAULT_RING_MAX}):")
    for line in buf:
        session.send(line)
    session.send("")


def format_custom_plain(spec: ChannelSpec, speaker_face: str, message: str) -> str:
    text = (message or "").strip()
    if not text:
        return ""
    prefix = (spec.prefix or "").strip()
    if prefix:
        return f"{prefix} [{speaker_face}]: {text}"
    return f"[{speaker_face}]: {text}"


def speak_custom_global(
    game,
    spec: ChannelSpec,
    speaker,
    message: str,
    *,
    speaker_face: str,
) -> bool:
    """Broadcast one staff/custom global plain channel line."""
    from engine import display_prefs
    from engine import gmcp
    from engine import style

    plain = format_custom_plain(spec, speaker_face, message)
    if not plain:
        return False
    append(game, spec.name, plain, gateway_plain=plain)
    delivered = False
    for session in list(getattr(game, "sessions", None) or []):
        other = getattr(session, "character", None)
        if other is None:
            continue
        if not can_hear(other, spec, game):
            continue
        display_prefs.ensure_display_defaults(other)
        if getattr(other, "screenreader", False) or not getattr(other, "use_color", True):
            line = plain
        else:
            line = style.paint_for(other, spec.color_role, plain)
        session.send(line)
        session.send("")
        gmcp.push_comm(session, spec.verb, message, speaker_face)
        delivered = True
    if not delivered and getattr(speaker, "session", None) is not None:
        sess = speaker.session
        display_prefs.ensure_display_defaults(speaker)
        if getattr(speaker, "screenreader", False) or not getattr(speaker, "use_color", True):
            line = plain
        else:
            line = style.paint_for(speaker, spec.color_role, plain)
        sess.send(line)
        sess.send("")
        delivered = True
    return delivered


def cmd_dynamic_channel(character, args: str, game, *, verb: str) -> None:
    """Handler for custom global channel verbs (registered at dispatch)."""
    spec = lookup_verb(verb)
    session = getattr(character, "session", None)
    if spec is None or session is None:
        return
    if not can_speak(character, spec, game):
        session.send("You cannot speak on that channel.")
        return
    text = (args or "").strip()
    if not text:
        replay_global(character, game, spec.name)
        return
    from command_support import _presence_face
    face = _presence_face(character)
    speak_custom_global(game, spec, character, text, speaker_face=face)


# ---------------------------------------------------------------------------
# Gateway mirror
# ---------------------------------------------------------------------------


def export_gateway_plain_line(
    channel_name: str, entry: Any, game,
) -> Optional[str]:
    if channel_name == "ooc":
        from engine import ooc_channel
        if isinstance(entry, str):
            return entry.strip() or None
        if isinstance(entry, dict):
            message = str(entry.get("message") or "").strip()
            if not message:
                return None
            kind = entry.get("kind") or ooc_channel.OOC_KIND_NORMAL
            speaker_key = entry.get("speaker")
            finder = getattr(game, "find_character", None)
            speaker = (
                finder(speaker_key) if callable(finder) and speaker_key else None
            )
            if speaker is None:
                plain = entry.get("plain")
                if isinstance(plain, str) and plain.strip():
                    return plain.strip()
                face = "?"
            else:
                face = ooc_channel.speaker_face_for_character(speaker, game)
            return ooc_channel.format_ooc_line(face, message, kind=kind)
        return None
    if channel_name == "wiznet":
        if isinstance(entry, str) and entry.strip():
            return entry.strip()
        return None
    if isinstance(entry, str) and entry.strip():
        return entry.strip()
    if isinstance(entry, dict):
        plain = entry.get("plain")
        if isinstance(plain, str) and plain.strip():
            return plain.strip()
    return None


def export_gateway_snapshot(game) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for spec in gateway_stitch_channels():
        lines: list[str] = []
        for entry in entries(game, spec.name):
            plain = export_gateway_plain_line(spec.name, entry, game)
            if plain:
                lines.append(plain)
        out[spec.name] = lines
    return out


def _schedule_gateway_mirror(game, channel_name: str, plain_line: str) -> None:
    text = (plain_line or "").strip()
    if not text:
        return
    bridge = getattr(game, "gateway_bridge", None)
    if bridge is None:
        return
    schedule = getattr(bridge, "schedule_channel_mirror", None)
    if callable(schedule):
        schedule(channel_name, text)


# ---------------------------------------------------------------------------
# Built-in channel registry (import side effect)
# ---------------------------------------------------------------------------

register_channel(
    ChannelSpec(
        name="ooc",
        verb="ooc",
        game_attr="ooc_history",
        meta_key="ooc_history",
        scope=SCOPE_GLOBAL,
        audience=AUDIENCE_ALL,
        replay_header="Recent OOC (last 20):",
        empty_message="No recent OOC. Type 'ooc <message>' to speak.",
        usage_message="Usage: ooc <message>  (bare ooc replays history)",
        ring_max=DEFAULT_RING_MAX,
        builtin=True,
        gateway_stitch=True,
        format=FORMAT_OOC,
        prefix="((OOC))",
        color_role="ooc",
        title="OOC",
    )
)

register_channel(
    ChannelSpec(
        name="wiznet",
        verb="wiznet",
        game_attr="wiznet_history",
        meta_key="wiznet_history",
        scope=SCOPE_GLOBAL,
        audience=AUDIENCE_STAFF,
        replay_header="Recent wiznet (last 20):",
        empty_message="No recent wiznet. Type 'wiznet <text>' to speak.",
        usage_message="Usage: wiznet <message>  (bare wiznet replays history)",
        ring_max=DEFAULT_RING_MAX,
        builtin=True,
        gateway_stitch=True,
        format=FORMAT_WIZNET,
        prefix="[WIZ]",
        color_role="absinthe_green",
        title="wiznet",
    )
)

register_channel(
    ChannelSpec(
        name="say",
        verb="say",
        game_attr="_say_unused",
        meta_key="",
        scope=SCOPE_ROOM,
        audience=AUDIENCE_ALL,
        replay_header="Recent speech here (last 20):",
        empty_message="No recent speech here. Type 'say <message>' to speak.",
        usage_message="Usage: say <message>  (bare say replays room speech)",
        ring_max=DEFAULT_RING_MAX,
        builtin=True,
        gateway_stitch=False,
        format=FORMAT_SAY,
        color_role="say",
        title="say",
    )
)

register_channel(
    ChannelSpec(
        name="tell",
        verb="tell",
        game_attr="_tell_unused",
        meta_key="",
        scope=SCOPE_PRIVATE,
        audience=AUDIENCE_ALL,
        replay_header="Recent tells (last 20):",
        empty_message="No recent tells. Type 'tell <name> <message>' to whisper.",
        usage_message="Usage: tell <name> <message>  (bare tell replays history)",
        ring_max=DEFAULT_RING_MAX,
        builtin=True,
        gateway_stitch=False,
        format=FORMAT_TELL,
        title="tell",
    )
)
