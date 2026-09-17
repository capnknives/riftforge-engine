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
  all, staff, gm, helper, origin:<id> (via engine.hooks.channel_audience_ok)

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
AUDIENCE_HELPER = "helper"

# format kinds for render + gateway export
FORMAT_OOC = "ooc"
FORMAT_QUESTIONS = "questions"
FORMAT_ANSWERS = "answers"
FORMAT_TRIVIA = "trivia"
FORMAT_WIZNET = "wiznet"
FORMAT_SAY = "say"
FORMAT_TELL = "tell"
FORMAT_PLAIN = "plain"

_VERB_RE = re.compile(r"^[a-z][a-z0-9_]{1,15}$")
MAX_PLAYER_CHANNELS_PER_ACCOUNT = 3
RESERVED_CHANNEL_VERBS = frozenset({
    "say", "tell", "ooc", "questions", "question", "answers", "trivia", "wiznet", "emote", "who",
    "help", "mute", "unmute", "mutes", "chans", "channel", "reply",
    "whisper", "block", "unblock", "blocks", "query", "queries",
    "querylist", "queryread", "querycomment", "queryclose",
    "replay",
})


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
    owner_account: str = ""


_BUILTIN: dict[str, ChannelSpec] = {}
_VERB_INDEX: dict[str, str] = {}


def register_channel(spec: ChannelSpec) -> ChannelSpec:
    """Register a channel (built-in or custom). Idempotent by ``spec.name``."""
    _BUILTIN[spec.name] = spec
    _VERB_INDEX[spec.verb.lower()] = spec.name
    return spec


def register_channel_verb_alias(name: str, alias: str) -> None:
    """Let a second verb speak and replay the same channel (question -> questions)."""
    key = (alias or "").strip().lower()
    if not key or name not in _BUILTIN:
        return
    _VERB_INDEX[key] = name


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
    # lookup_verb also matches aliases (question -> questions).
    head = lower.split(None, 1)[0]
    spec = lookup_verb(head)
    if spec is not None and spec.gateway_stitch and spec.scope == SCOPE_GLOBAL:
        return spec.name
    return None


# ---------------------------------------------------------------------------
# Game attachment
# ---------------------------------------------------------------------------


def init_game(game) -> None:
    """Attach empty ring deques for every global channel on ``game``."""
    if not hasattr(game, "_room_say_rings") or game._room_say_rings is None:
        game._room_say_rings = {}
    if not hasattr(game, "_room_emote_rings") or game._room_emote_rings is None:
        game._room_emote_rings = {}
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


def room_emote_ring(game, room) -> deque:
    """Last-N emote/smote poses for one room (keyed by room.key).

    Stores structured dicts ``{speaker, raw, mode}`` so replay can
    re-render ``@name`` tokens for the viewer, matching live emote.
    """
    if game is None or room is None:
        return deque(maxlen=DEFAULT_RING_MAX)
    rings = getattr(game, "_room_emote_rings", None)
    if rings is None:
        game._room_emote_rings = {}
        rings = game._room_emote_rings
    key = getattr(room, "key", None) or id(room)
    buf = rings.get(key)
    if buf is None:
        buf = deque(maxlen=DEFAULT_RING_MAX)
        rings[key] = buf
    return buf


def _tell_history_owner(character, game=None):
    """Return the Character whose private tell ring should hold *character*'s lines.

    GM staff spirits (``gmspirit:`` keys) are not saved to disk; their tell
    history rides on the linked mortal ``gm_body_key`` body so bare ``tell``
    survives gateway reattach / copyover like ``ooc`` history does globally.
    """
    if character is None:
        return None
    key = (getattr(character, "key", "") or "").lower()
    is_spirit = key.startswith("gmspirit:") or bool(
        getattr(character, "gm_spirit", False)
    )
    if is_spirit and game is not None:
        body_key = getattr(character, "gm_body_key", None)
        if isinstance(body_key, str) and body_key.strip():
            finder = getattr(game, "find_character", None)
            if callable(finder):
                body = finder(body_key.strip())
                if body is not None:
                    return body
    return character


def character_tell_ring(character, game=None) -> deque:
    """Per-character tell history (in + out); survives session recreation."""
    owner = _tell_history_owner(character, game)
    if owner is None:
        return deque(maxlen=DEFAULT_RING_MAX)
    buf = getattr(owner, "tell_history", None)
    if buf is None:
        buf = deque(maxlen=DEFAULT_RING_MAX)
        owner.tell_history = buf
    return buf


def character_ooctell_ring(character, game=None) -> deque:
    """Per-character OOC tell history (separate from IC tells)."""
    owner = _tell_history_owner(character, game)
    if owner is None:
        return deque(maxlen=DEFAULT_RING_MAX)
    buf = getattr(owner, "ooctell_history", None)
    if buf is None:
        buf = deque(maxlen=DEFAULT_RING_MAX)
        owner.ooctell_history = buf
    return buf


def session_tell_ring(session, game=None) -> deque:
    """Tell ring for the Session's bound character (legacy session field unused)."""
    if session is None:
        return deque(maxlen=DEFAULT_RING_MAX)
    char = getattr(session, "character", None)
    if char is not None:
        return character_tell_ring(char, game)
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
    if aud == AUDIENCE_HELPER:
        from engine.player_helpers import is_player_helper
        return is_player_helper(game, viewer)
    if aud.startswith("origin:") or aud.startswith("helper:"):
        return hooks.channel_audience_ok(viewer, aud, game)
    return hooks.channel_audience_ok(viewer, aud, game)


def can_speak(character, spec: ChannelSpec, game) -> bool:
    if character is None or getattr(character, "session", None) is None:
        return False
    if _is_muted(character):
        return False
    if hooks.channel_speech_blocked(character, game):
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


def append_room_emote(game, room, entry) -> None:
    """Append one room emote ring entry (structured dict or legacy plain str)."""
    if entry is None or room is None:
        return
    if isinstance(entry, str):
        text = entry.strip()
        if not text:
            return
        room_emote_ring(game, room).append(text)
        return
    if isinstance(entry, dict):
        raw = str(entry.get("raw") or "").strip()
        if not raw:
            return
        room_emote_ring(game, room).append(entry)
        return
    text = str(entry).strip()
    if text:
        room_emote_ring(game, room).append(text)


def append_room_say(game, room, entry) -> None:
    """Append one room say ring entry (structured dict or legacy plain str)."""
    if entry is None or room is None:
        return
    if isinstance(entry, str):
        text = entry.strip()
        if not text:
            return
        room_say_ring(game, room).append(text)
        return
    if isinstance(entry, dict):
        message = str(entry.get("message") or "").strip()
        if not message:
            return
        room_say_ring(game, room).append(entry)
        return
    text = str(entry).strip()
    if text:
        room_say_ring(game, room).append(text)


def append_tell(session, entry, *, game=None) -> None:
    """Append one tell history entry (structured dict or legacy plain str)."""
    if entry is None or session is None:
        return
    if isinstance(entry, str):
        text = entry.strip()
        if not text:
            return
        char = getattr(session, "character", None)
        if char is not None:
            character_tell_ring(char, game).append(text)
            return
        session_tell_ring(session, game).append(text)
        return
    if isinstance(entry, dict):
        message = str(entry.get("message") or "").strip()
        if not message:
            return
        char = getattr(session, "character", None)
        if char is not None:
            character_tell_ring(char, game).append(entry)
            return
        session_tell_ring(session, game).append(entry)
        return
    text = str(entry).strip()
    if not text:
        return
    char = getattr(session, "character", None)
    if char is not None:
        character_tell_ring(char, game).append(text)
        return
    session_tell_ring(session, game).append(text)


def append_ooctell(session, entry, *, game=None) -> None:
    """Append one OOC tell history entry (structured dict or legacy plain str)."""
    if entry is None or session is None:
        return
    if isinstance(entry, str):
        text = entry.strip()
        if not text:
            return
        char = getattr(session, "character", None)
        if char is not None:
            character_ooctell_ring(char, game).append(text)
            return
        buf = getattr(session, "ooctell_history", None)
        if buf is None:
            buf = deque(maxlen=DEFAULT_RING_MAX)
            session.ooctell_history = buf
        buf.append(text)
        return
    if isinstance(entry, dict):
        message = str(entry.get("message") or "").strip()
        if not message:
            return
        char = getattr(session, "character", None)
        if char is not None:
            character_ooctell_ring(char, game).append(entry)
            return
        buf = getattr(session, "ooctell_history", None)
        if buf is None:
            buf = deque(maxlen=DEFAULT_RING_MAX)
            session.ooctell_history = buf
        buf.append(entry)
        return


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


def _ooc_ring_entry_persistable(item) -> bool:
    """True when one OOC/questions/answers ring row should survive save/load."""
    if isinstance(item, str):
        return bool(item.strip())
    if isinstance(item, dict):
        if str(item.get("message") or "").strip():
            return True
        plain = item.get("plain")
        return isinstance(plain, str) and bool(plain.strip())
    return False


def _normalize_ooc_loaded(data: list) -> list:
    entries = []
    for item in data:
        if _ooc_ring_entry_persistable(item):
            entries.append(item)
    return entries


def _serialize_ooc_ring(game, *, channel_name: str = "ooc") -> list:
    history = ring(game, channel_name) or []
    return [item for item in history if _ooc_ring_entry_persistable(item)]


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
    if spec.format in (FORMAT_OOC, FORMAT_QUESTIONS, FORMAT_ANSWERS):
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
    elif spec.format in (FORMAT_QUESTIONS, FORMAT_ANSWERS):
        items = _serialize_ooc_ring(game, channel_name=channel_name)
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
    owner_account = str(defn.get("owner_account") or "").strip()
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
        owner_account=owner_account,
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
            "owner_account": spec.owner_account or "",
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


def list_player_channels(owner_account: str) -> list[ChannelSpec]:
    """Custom global channels owned by one account name."""
    owner_key = (owner_account or "").strip().lower()
    if not owner_key:
        return []
    return [
        s for s in list_custom_channels()
        if (s.owner_account or "").strip().lower() == owner_key
    ]


def player_channel_count(owner_account: str) -> int:
    return len(list_player_channels(owner_account))


def can_manage_channel(game, account, spec: ChannelSpec) -> bool:
    """True when *account* may remove a non-built-in global channel."""
    if spec is None or spec.builtin:
        return False
    owner_key = (spec.owner_account or "").strip().lower()
    if not owner_key:
        return False
    if account is None:
        return False
    from engine.accounts import account_lookup_key
    return account_lookup_key(account.name) == owner_key


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
    owner_account: str = "",
) -> tuple[bool, str]:
    """Register a new global channel. Returns (ok, message)."""
    defn = {
        "name": (name or "").strip().lower(),
        "verb": (verb or name or "").strip().lower(),
        "title": (title or verb or name or "").strip(),
        "audience": (audience or AUDIENCE_ALL).strip().lower(),
        "prefix": prefix,
        "color_role": color_role,
        "ring_max": ring_max,
        "owner_account": (owner_account or "").strip(),
    }
    spec = _spec_from_def(defn)
    if spec is None:
        return False, "Invalid channel name/verb (lowercase letters, digits, underscore; 2-16 chars)."
    if spec.verb in RESERVED_CHANNEL_VERBS:
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


def create_player_channel(
    game,
    account,
    *,
    name: str,
    verb: str,
    title: str = "",
) -> tuple[bool, str]:
    """Player-owned public channel (audience all). Returns ``(ok, message)``."""
    if account is None:
        return False, "Link an account first (help account)."
    if player_channel_count(account.name) >= MAX_PLAYER_CHANNELS_PER_ACCOUNT:
        return (
            False,
            f"You already own {MAX_PLAYER_CHANNELS_PER_ACCOUNT} public channels. "
            "Remove one with chans remove <name> before creating another.",
        )
    ok, msg = create_custom_channel(
        game,
        name=name,
        verb=verb,
        title=title or verb or name,
        audience=AUDIENCE_ALL,
        owner_account=account.name,
    )
    if not ok:
        return ok, msg
    return (
        True,
        (
            f"{msg} Anyone online can type {verb} <message>. "
            f"You own this channel — chans remove {name.strip().lower()} to delete it."
        ),
    )


def remove_custom_channel(game, name: str, *, account=None) -> tuple[bool, str]:
    """Remove a non-built-in global channel (owner or unrestricted staff path)."""
    key = (name or "").strip().lower()
    spec = get_channel(key)
    if spec is None:
        return False, f"No channel named '{key}'."
    if spec.builtin:
        return False, f"Built-in channel '{key}' cannot be removed."
    if account is not None:
        if not can_manage_channel(game, account, spec):
            return False, (
                f"You do not own channel '{key}'. "
                "Only the creator can remove player public channels."
            )
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
    if spec.builtin and key in ("ooc", "wiznet", "questions", "answers"):
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


def render_questions_entry(entry, viewer, game) -> str:
    from engine import questions_channel
    return questions_channel.format_questions_history_entry(entry, viewer, game)


def render_answers_entry(entry, viewer, game) -> str:
    from engine import answers_channel
    return answers_channel.format_answers_history_entry(entry, viewer, game)


def render_trivia_entry(entry, viewer, game) -> str:
    from engine import trivia_channel
    return trivia_channel.format_trivia_history_entry(entry, viewer, game)


def replay_wiznet_entry(entry) -> str:
    from engine import style
    plain = entry if isinstance(entry, str) else str(entry)
    return style.paint_preserving_urls("absinthe_green", plain)


def render_room_say_entry(entry, viewer, game) -> str:
    """Render one room say history entry for *viewer*."""
    from engine import display_prefs
    from command_support import _display_name

    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return str(entry or "")
    message = str(entry.get("message") or "").strip()
    if not message:
        return ""
    you_verb = str(entry.get("you_verb") or "say")
    they_verb = str(entry.get("they_verb") or "says")
    tone = entry.get("tone")
    prefix = str(entry.get("drunk_tag") or "")
    face = "?"
    speaker_key = entry.get("speaker")
    if speaker_key and game is not None:
        finder = getattr(game, "find_character", None)
        speaker = finder(speaker_key) if callable(finder) else None
        if speaker is not None:
            face = _display_name(speaker, viewer=viewer)
    line = display_prefs.format_say_chat(
        viewer,
        face,
        message,
        you=False,
        you_verb=you_verb,
        they_verb=they_verb,
        tone=tone,
        voice_phrase=entry.get("voice_phrase"),
    )
    return f"{prefix}{line}" if prefix else line


def render_tell_entry(entry, viewer) -> str:
    """Render one tell history entry for *viewer*."""
    from engine import display_prefs

    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return str(entry or "")
    message = str(entry.get("message") or "").strip()
    if not message:
        return ""
    peer = str(entry.get("peer") or "?")
    kind = str(entry.get("kind") or "")
    if kind == "out":
        return display_prefs.format_tell_chat(
            viewer, outgoing=True, peer_face=peer, message=message,
        )
    if kind == "in":
        return display_prefs.format_tell_chat(
            viewer, outgoing=False, peer_face=peer, message=message,
        )
    plain = str(entry.get("plain") or "").strip()
    return plain


def render_ooctell_entry(entry, viewer) -> str:
    """Render one OOC tell history entry for *viewer*."""
    from engine import display_prefs

    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return str(entry or "")
    message = str(entry.get("message") or "").strip()
    if not message:
        return ""
    peer = str(entry.get("peer") or "?")
    kind = str(entry.get("kind") or "")
    if kind == "out":
        return display_prefs.format_ooctell_chat(
            viewer, outgoing=True, peer_face=peer, message=message,
        )
    if kind == "in":
        return display_prefs.format_ooctell_chat(
            viewer, outgoing=False, peer_face=peer, message=message,
        )
    plain = str(entry.get("plain") or "").strip()
    return plain


def render_plain_entry(spec: ChannelSpec, entry, viewer, game=None) -> str:
    from engine import display_prefs
    from engine import style
    from command_support import _presence_face

    if isinstance(entry, dict):
        message = str(entry.get("message") or "").strip()
        if display_prefs.wants_plain_comms(viewer) and message:
            face = "?"
            speaker_key = entry.get("speaker")
            if speaker_key and game is not None:
                finder = getattr(game, "find_character", None)
                speaker = finder(speaker_key) if callable(finder) else None
                if speaker is not None:
                    face = _presence_face(speaker)
            line = display_prefs.format_custom_chat(
                viewer, spec.title, spec.prefix, face, message,
            )
        else:
            line = str(entry.get("plain") or "")
    else:
        line = str(entry or "")
    line = line.strip()
    if not line:
        return ""
    display_prefs.ensure_display_defaults(viewer)
    if getattr(viewer, "screenreader", False) or not getattr(viewer, "use_color", True):
        return line
    return style.paint_for(viewer, spec.color_role, line)


def _entry_speaker_key(entry: Any) -> Optional[str]:
    if isinstance(entry, dict):
        speaker = entry.get("speaker")
        if isinstance(speaker, str) and speaker.strip():
            return speaker.strip()
    return None


def _should_hide_public_entry(viewer, game, entry) -> bool:
    from engine.accounts import ooc_should_hide_from_viewer
    speaker_key = _entry_speaker_key(entry)
    if speaker_key:
        return ooc_should_hide_from_viewer(viewer, game, speaker_key)
    return False


def parse_replay_index(args) -> int | None:
    """Return a 1-based replay index when *args* is only digits."""
    text = str(args or "").strip()
    if not text.isdigit():
        return None
    n = int(text)
    if n < 1:
        return None
    return n


def resolve_global_channel(token: str):
    """Match a global channel by name or verb, or None."""
    key = str(token or "").strip().lower()
    if not key:
        return None
    spec = get_channel(key)
    if spec is None:
        spec = lookup_verb(key)
    if spec is None or spec.scope != SCOPE_GLOBAL:
        return None
    return spec


def replay_global(character, game, channel_name: str, *, index: int | None = None) -> None:
    """Bare-verb history replay for one global channel.

    *index* is 1-based among lines this viewer can see (same order as a
    full dump). None dumps the whole numbered ring.
    """
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
    visible = []
    for entry in entries(game, channel_name):
        if _should_hide_public_entry(character, game, entry):
            continue
        if spec.format == FORMAT_OOC:
            line = render_ooc_entry(entry, character, game)
        elif spec.format == FORMAT_QUESTIONS:
            line = render_questions_entry(entry, character, game)
        elif spec.format == FORMAT_ANSWERS:
            line = render_answers_entry(entry, character, game)
        elif spec.format == FORMAT_TRIVIA:
            line = render_trivia_entry(entry, character, game)
        elif spec.format == FORMAT_WIZNET:
            line = replay_wiznet_entry(entry)
        else:
            line = render_plain_entry(spec, entry, character, game)
        if line:
            visible.append(line)
    if not visible:
        send_empty_hint(character, channel_name)
        return
    verb = spec.verb or channel_name
    if index is not None:
        if index > len(visible):
            session.send(
                f"No {channel_name} line {index} "
                f"(1-{len(visible)} in the current ring)."
            )
            return
        session.send(f"{visible[index - 1]}")
        session.send("")
        return
    session.send(
        f"{spec.replay_header} Type replay {verb} <n> for one line."
    )
    for i, line in enumerate(visible, start=1):
        session.send(f"  {i}. {line}")
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
    for entry in buf:
        line = render_room_say_entry(entry, character, game)
        if line:
            session.send(line)
    session.send("")


def replay_room_emote(character, game) -> None:
    """Bare ``emote`` / ``smote``: replay last poses in this room."""
    session = getattr(character, "session", None)
    room = getattr(character, "location", None)
    if session is None or room is None:
        return
    buf = room_emote_ring(game, room)
    if not buf:
        session.send("No recent poses here. Type 'emote <action>' to pose.")
        return
    session.send(f"Recent poses here (last {DEFAULT_RING_MAX}):")
    from engine.rp_emote import format_emote_line

    for entry in buf:
        if isinstance(entry, str):
            text = entry.strip()
            if text:
                session.send(text)
            continue
        if not isinstance(entry, dict):
            continue
        raw = str(entry.get("raw") or "").strip()
        if not raw:
            continue
        mode = str(entry.get("mode") or "emote")
        speaker_key = str(entry.get("speaker") or "")
        speaker = None
        if speaker_key:
            for who in room.characters():
                if getattr(who, "key", None) == speaker_key:
                    speaker = who
                    break
            if speaker is None:
                finder = getattr(game, "find_character", None)
                if callable(finder):
                    speaker = finder(speaker_key)
        if speaker is None:
            # Speaker left; still show a third-person fallback.
            name = speaker_key or "Someone"
            session.send(f"{name} {raw}")
            continue
        line = format_emote_line(speaker, raw, character, game, mode=mode)
        if line:
            session.send(line)
    session.send("")


def replay_tells(character, game=None) -> None:
    session = getattr(character, "session", None)
    if session is None:
        return
    buf = character_tell_ring(character, game)
    if not buf:
        session.send("No recent tells. Type 'tell <name> <message>' to whisper.")
        return
    session.send(f"Recent tells (last {DEFAULT_RING_MAX}):")
    for entry in buf:
        line = render_tell_entry(entry, character)
        if line:
            session.send(line)
    session.send("")


def replay_ooctells(character, game=None) -> None:
    """Bare ``ooctell`` -- replay recent private OOC tells."""
    session = getattr(character, "session", None)
    if session is None:
        return
    buf = character_ooctell_ring(character, game)
    if not buf:
        session.send(
            "No recent OOC tells. Type 'ooctell <name> <message>' to whisper."
        )
        return
    session.send(f"Recent OOC tells (last {DEFAULT_RING_MAX}):")
    for entry in buf:
        line = render_ooctell_entry(entry, character)
        if line:
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
    speaker_key = getattr(speaker, "key", None)
    entry = {
        "speaker": speaker_key,
        "plain": plain,
        "message": message,
    }
    append(game, spec.name, entry, gateway_plain=plain)
    delivered = False
    from engine.accounts import ooc_should_hide_from_viewer

    def _render_for(viewer):
        line = display_prefs.format_custom_chat(
            viewer, spec.title, spec.prefix, speaker_face, message,
        )
        if getattr(viewer, "screenreader", False) or not getattr(viewer, "use_color", True):
            return line
        return style.paint_for(viewer, spec.color_role, line)

    for session in list(getattr(game, "sessions", None) or []):
        other = getattr(session, "character", None)
        if other is None:
            continue
        if not can_hear(other, spec, game):
            continue
        if ooc_should_hide_from_viewer(other, game, speaker_key):
            continue
        display_prefs.ensure_display_defaults(other)
        line = _render_for(other)
        gmcp.deliver_comm(session, spec.verb, message, speaker_face, line, "")
        delivered = True
    if not delivered and getattr(speaker, "session", None) is not None:
        sess = speaker.session
        display_prefs.ensure_display_defaults(speaker)
        line = _render_for(speaker)
        gmcp.deliver_comm(sess, spec.verb, message, speaker_face, line, "")
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
    from engine import ooc_channel

    face = ooc_channel.speaker_face_for_character(character, game)
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
                fallback = ooc_channel._ooc_fallback_face(entry)
                face = fallback if fallback else "?"
            else:
                face = ooc_channel.speaker_face_for_character(speaker, game)
            return ooc_channel.format_ooc_line(face, message, kind=kind)
        return None
    if channel_name == "questions":
        from engine import questions_channel
        if isinstance(entry, str):
            return entry.strip() or None
        if isinstance(entry, dict):
            message = str(entry.get("message") or "").strip()
            if not message:
                return None
            speaker_key = entry.get("speaker")
            finder = getattr(game, "find_character", None)
            speaker = (
                finder(speaker_key) if callable(finder) and speaker_key else None
            )
            if speaker is None:
                plain = entry.get("plain")
                if isinstance(plain, str) and plain.strip():
                    return plain.strip()
                fallback = entry.get("face")
                face = fallback if isinstance(fallback, str) and fallback else "?"
            else:
                face = questions_channel.speaker_face_for_character(speaker, game)
            return questions_channel.format_questions_line(face, message)
        return None
    if channel_name == "answers":
        from engine import answers_channel
        if isinstance(entry, str):
            return entry.strip() or None
        if isinstance(entry, dict):
            message = str(entry.get("message") or "").strip()
            if not message:
                return None
            speaker_key = entry.get("speaker")
            finder = getattr(game, "find_character", None)
            speaker = (
                finder(speaker_key) if callable(finder) and speaker_key else None
            )
            if speaker is None:
                plain = entry.get("plain")
                if isinstance(plain, str) and plain.strip():
                    return plain.strip()
                fallback = entry.get("face")
                face = fallback if isinstance(fallback, str) and fallback else "?"
            else:
                face = answers_channel.speaker_face_for_character(speaker, game)
            return answers_channel.format_answers_line(face, message)
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


# Plain lines the gateway stitch buffer uses while game IPC is down (copyover).
_OOC_PLAIN_RE = re.compile(
    r"^\(\(OOC\)\)(?: \[AUTHOR\])? \[(?P<face>[^\]]+)\]: (?P<message>.*)$",
    re.DOTALL,
)
_QUESTIONS_PLAIN_RE = re.compile(
    r"^\(\(QUESTIONS\)\) \[(?P<face>[^\]]+)\]: (?P<message>.*)$",
    re.DOTALL,
)
_ANSWERS_PLAIN_RE = re.compile(
    r"^\(\(ANSWERS\)\) \[(?P<face>[^\]]+)\]: (?P<message>.*)$",
    re.DOTALL,
)
_WIZNET_PLAIN_RE = re.compile(
    r"^\[WIZ\] (?P<face>[^:]+): (?P<message>.*)$",
    re.DOTALL,
)


def parse_gateway_plain_entry(channel_name: str, plain: str) -> Any:
    """Rebuild one structured ring entry from a gateway stitch plain line."""
    from engine import ooc_channel

    text = (plain or "").strip()
    if not text:
        return None
    if channel_name == "ooc":
        match = _OOC_PLAIN_RE.match(text)
        if not match:
            return text
        face = match.group("face")
        message = match.group("message")
        kind = (
            ooc_channel.OOC_KIND_AUTHOR_NUDGE
            if "[AUTHOR]" in text[:40]
            else ooc_channel.OOC_KIND_NORMAL
        )
        entry: dict[str, Any] = {
            "speaker": None,
            "message": message,
            "face": face,
            "plain": text,
        }
        if kind != ooc_channel.OOC_KIND_NORMAL:
            entry["kind"] = kind
        return entry
    if channel_name == "questions":
        match = _QUESTIONS_PLAIN_RE.match(text)
        if not match:
            return text
        return {
            "speaker": None,
            "message": match.group("message"),
            "face": match.group("face"),
            "plain": text,
        }
    if channel_name == "answers":
        match = _ANSWERS_PLAIN_RE.match(text)
        if not match:
            return text
        return {
            "speaker": None,
            "message": match.group("message"),
            "face": match.group("face"),
            "plain": text,
        }
    if channel_name == "wiznet":
        return text
    return text


def gateway_plain_comm_payload(channel_name: str, plain: str) -> Optional[dict[str, str]]:
    """Build ``Comm.Channel`` GMCP body from one gateway stitch plain line.

    Gateway stitch relays only ship ``op:text`` today; browser HUD comm panes
    listen for GMCP ``Comm.Channel`` (same shape as live ``deliver_comm``).
    """
    text = (plain or "").strip()
    if not text:
        return None
    spec = get_channel(channel_name)
    chan = (spec.verb if spec is not None else channel_name) or channel_name
    if channel_name == "wiznet":
        match = _WIZNET_PLAIN_RE.match(text)
        if not match:
            return None
        return {
            "chan": "wiznet",
            "player": match.group("face").strip(),
            "msg": match.group("message"),
        }
    entry = parse_gateway_plain_entry(channel_name, text)
    if isinstance(entry, dict):
        face = str(entry.get("face") or "?").strip() or "?"
        message = str(entry.get("message") or "").strip()
        if not message:
            return None
        return {"chan": chan, "player": face, "msg": message}
    return None


def record_gateway_stitch_plain(game, channel_name: str, plain: str) -> None:
    """Append one gateway stitch relay line to ooc.log / questions.log immediately.

    Used while the Veil reloads so copyover OOC is mined even before the
    ``chat_stitch_import`` replace pass runs (bug report 1473). Trivia
    #4224 accidentally dropped this helper while gateway_client still
    called it, which crashed the new game child after a multi-minute boot.
    """
    text = (plain or "").strip()
    if not text:
        return
    spec = get_channel(channel_name)
    if spec is None or spec.scope != SCOPE_GLOBAL:
        return
    entry = parse_gateway_plain_entry(channel_name, text)
    if entry is None and channel_name != "wiznet":
        return
    if entry is None:
        entry = text
    _record_gateway_transcript(channel_name, entry, text, game)


def _record_gateway_transcript(
    channel_name: str, entry: Any, plain: str, game,
) -> None:
    """Append one imported stitch line to staff mining logs when applicable."""
    try:
        from engine import channel_transcript
    except Exception:
        return
    if channel_name not in channel_transcript._FILES:
        return
    face = "?"
    message = ""
    extra = None
    if isinstance(entry, dict):
        face = str(entry.get("face") or "?").strip() or "?"
        message = str(entry.get("message") or "").strip()
        kind = entry.get("kind")
        if kind:
            extra = {"kind": kind}
    elif isinstance(entry, str):
        message = entry.strip()
        if channel_name == "wiznet" and message.startswith("[WIZ] "):
            body = message[6:]
            if ": " in body:
                face, message = body.split(": ", 1)
    if not message:
        return
    try:
        channel_transcript.record(
            channel_name,
            face=face,
            message=message,
            game=game,
            extra=extra,
        )
    except Exception:
        pass


def _stitch_dedupe_keys(channel_name: str, plain: str) -> set[str]:
    """Dedupe keys for gateway stitch plain lines (full plain + message body)."""
    text = (plain or "").strip()
    if not text:
        return set()
    keys = {text}
    # Wiznet rings store plain staff lines only. Never dedupe by message body
    # alone or reuse old ring rows during replace -- that resurrected an
    # ancient line as the latest replay entry after copyover (bug 1072).
    if channel_name == "wiznet":
        match = _WIZNET_PLAIN_RE.match(text)
        if match:
            face = match.group("face").strip()
            message = match.group("message").strip()
            if face and message:
                keys.add(f"face:{face}\0{message}")
        return keys
    entry = parse_gateway_plain_entry(channel_name, text)
    if isinstance(entry, dict):
        message = str(entry.get("message") or "").strip()
        if message:
            keys.add(f"msg:{message}")
            face = str(entry.get("face") or "").strip()
            if face:
                keys.add(f"face:{face}\0{message}")
    elif isinstance(entry, str) and entry.strip():
        keys.add(entry.strip())
    return keys


def _entry_for_gateway_plain(
    channel_name: str,
    plain: str,
    existing_by_key: dict[str, Any],
) -> Any | None:
    """Reuse a structured ring row when gateway plain matches an existing line."""
    text = (plain or "").strip()
    if not text:
        return None
    if channel_name == "wiznet":
        return text
    for key in _stitch_dedupe_keys(channel_name, text):
        entry = existing_by_key.get(key)
        if entry is not None:
            return entry
    return parse_gateway_plain_entry(channel_name, text)


def replace_ring_from_gateway_lines(
    game, channel_name: str, plain_lines: list,
) -> int:
    """Rebuild one global ring from the gateway merged stitch buffer."""
    spec = get_channel(channel_name)
    if spec is None or spec.scope != SCOPE_GLOBAL:
        return 0
    history = ring(game, channel_name)
    if history is None:
        return 0
    existing_by_key: dict[str, Any] = {}
    for entry in entries(game, channel_name):
        plain = export_gateway_plain_line(channel_name, entry, game)
        if not plain:
            continue
        for key in _stitch_dedupe_keys(channel_name, plain.strip()):
            existing_by_key.setdefault(key, entry)
    rebuilt: list[Any] = []
    seen_keys: set[str] = set()
    for raw in plain_lines or []:
        text = (raw or "").strip() if isinstance(raw, str) else ""
        if not text:
            continue
        dedupe_keys = _stitch_dedupe_keys(channel_name, text)
        if dedupe_keys & seen_keys:
            continue
        entry = _entry_for_gateway_plain(channel_name, text, existing_by_key)
        if entry is None:
            continue
        rebuilt.append(entry)
        seen_keys.update(dedupe_keys)
    ring_max = max(1, int(spec.ring_max or DEFAULT_RING_MAX))
    if len(rebuilt) > ring_max:
        rebuilt = rebuilt[-ring_max:]
    before_plain: set[str] = set()
    for entry in entries(game, channel_name):
        plain = export_gateway_plain_line(channel_name, entry, game)
        if plain:
            before_plain.add(plain.strip())
    history.clear()
    for entry in rebuilt:
        history.append(entry)
    imported = 0
    for entry in rebuilt:
        plain = export_gateway_plain_line(channel_name, entry, game)
        if not plain:
            continue
        text = plain.strip()
        if text in before_plain:
            continue
        imported += 1
        _record_gateway_transcript(channel_name, entry, text, game)
    return imported


def import_gateway_stitch_lines(game, channels: dict[str, list]) -> int:
    """Merge gateway-only stitch lines into game channel rings + ooc.log.

    During copyover the gateway relays OOC/wiznet while game IPC is down.
    Those lines live in the gateway stitch buffer until the game child
    reconnects and pulls anything missing from the saved snapshot.
    """
    imported = 0
    for channel_name, lines in (channels or {}).items():
        spec = get_channel(channel_name)
        if spec is None or spec.scope != SCOPE_GLOBAL:
            continue
        history = ring(game, channel_name)
        if history is None:
            continue
        existing_plain: set[str] = set()
        existing_keys: set[str] = set()
        for entry in entries(game, channel_name):
            plain = export_gateway_plain_line(channel_name, entry, game)
            if plain:
                stripped = plain.strip()
                existing_plain.add(stripped)
                existing_keys.update(_stitch_dedupe_keys(channel_name, stripped))
        for raw in lines or []:
            text = (raw or "").strip() if isinstance(raw, str) else ""
            if not text or text in existing_plain:
                continue
            dedupe_keys = _stitch_dedupe_keys(channel_name, text)
            if dedupe_keys & existing_keys:
                continue
            entry = parse_gateway_plain_entry(channel_name, text)
            if entry is None:
                continue
            history.append(entry)
            existing_plain.add(text)
            existing_keys.update(dedupe_keys)
            imported += 1
            _record_gateway_transcript(channel_name, entry, text, game)
    return imported


def sync_gateway_stitch_channels(game, channels: dict[str, list], *, mode: str) -> int:
    """Apply one gateway stitch payload to global channel rings."""
    if (mode or "").strip().lower() == "replace":
        count = 0
        for channel_name, lines in (channels or {}).items():
            count += replace_ring_from_gateway_lines(game, channel_name, lines or [])
        return count
    return import_gateway_stitch_lines(game, channels)


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
        name="questions",
        verb="questions",
        game_attr="questions_history",
        meta_key="questions_history",
        scope=SCOPE_GLOBAL,
        audience=AUDIENCE_ALL,
        replay_header="Recent questions (last 20):",
        empty_message="No recent questions. Type 'question <message>' (or questions) to ask.",
        usage_message="Usage: question <message>  (bare question or questions replays history)",
        ring_max=DEFAULT_RING_MAX,
        builtin=True,
        gateway_stitch=True,
        format=FORMAT_QUESTIONS,
        prefix="((QUESTIONS))",
        color_role="ooc",
        title="Questions",
    )
)
register_channel_verb_alias("questions", "question")

register_channel(
    ChannelSpec(
        name="answers",
        verb="answers",
        game_attr="answers_history",
        meta_key="answers_history",
        scope=SCOPE_GLOBAL,
        audience=AUDIENCE_HELPER,
        replay_header="Recent answers (last 20):",
        empty_message="No recent answers. Helpers: type 'answers <message>'.",
        usage_message="Usage: answers <message>  (bare answers replays history)",
        ring_max=DEFAULT_RING_MAX,
        builtin=True,
        gateway_stitch=True,
        format=FORMAT_ANSWERS,
        prefix="((ANSWERS))",
        color_role="absinthe_green",
        title="Answers",
    )
)

register_channel(
    ChannelSpec(
        name="trivia",
        verb="trivia",
        game_attr="trivia_history",
        meta_key="trivia_history",
        scope=SCOPE_GLOBAL,
        audience="optin:trivia",
        replay_header="Recent trivia (last 20):",
        empty_message=(
            "No recent trivia. Type 'trivia join' then 'trivia start' to play."
        ),
        usage_message=(
            "Usage: trivia join | start | start spn | <answer>  "
            "(bare trivia replays history)"
        ),
        ring_max=DEFAULT_RING_MAX,
        builtin=True,
        gateway_stitch=True,
        format=FORMAT_TRIVIA,
        prefix="((TRIVIA))",
        color_role="gold",
        title="Trivia",
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
