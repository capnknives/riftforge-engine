"""
channel_history.py -- authoritative game-side chat rings + gateway mirror.

Every global channel with bare-verb replay (``ooc``, ``wiznet``, future
channels) registers here once. The **game** process owns the ring buffer
(persisted in meta on ``game.save()``). The **gateway** keeps a separate
plain-text mirror so stitch mode can replay/send while game IPC is down.

Game IPC up: bare channel verbs always forward to the game (authoritative).
Game IPC down: gateway serves from its mirror copy (seeded on reconnect and
updated on each live append).

Stdlib only.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

# Default ring size for bare-verb replay (last N lines).
DEFAULT_RING_MAX = 20

OOC_HISTORY_MAX = DEFAULT_RING_MAX
WIZNET_HISTORY_MAX = DEFAULT_RING_MAX


@dataclass(frozen=True)
class ChannelSpec:
    """One registered global channel with replay + optional gateway stitch."""

    name: str
    game_attr: str
    meta_key: str
    verb: str
    replay_header: str
    empty_message: str
    usage_message: str
    ring_max: int = DEFAULT_RING_MAX
    staff_only: bool = False
    gateway_stitch: bool = True


# name -> spec
_CHANNELS: dict[str, ChannelSpec] = {}


def register_channel(spec: ChannelSpec) -> ChannelSpec:
    """Register a global channel (idempotent by ``spec.name``)."""
    _CHANNELS[spec.name] = spec
    return spec


def get_channel(name: str) -> Optional[ChannelSpec]:
    return _CHANNELS.get(name)


def all_channels() -> tuple[ChannelSpec, ...]:
    return tuple(_CHANNELS.values())


def gateway_stitch_channels() -> tuple[ChannelSpec, ...]:
    return tuple(s for s in _CHANNELS.values() if s.gateway_stitch)


def stitch_channel_for_command(text: str) -> Optional[str]:
    """Return channel name when *text* is a bare or speak stitch verb."""
    lower = (text or "").strip().lower()
    if not lower:
        return None
    for spec in gateway_stitch_channels():
        if lower == spec.verb:
            return spec.name
        if lower.startswith(f"{spec.verb} "):
            return spec.name
    return None


def init_game(game) -> None:
    """Attach empty ring deques for every registered channel on ``game``."""
    for spec in _CHANNELS.values():
        setattr(game, spec.game_attr, deque(maxlen=spec.ring_max))


def ring(game, channel_name: str):
    """Return the game's ring deque for *channel_name* (may be missing)."""
    spec = get_channel(channel_name)
    if spec is None:
        return None
    return getattr(game, spec.game_attr, None)


def append(
    game,
    channel_name: str,
    entry: Any,
    *,
    gateway_plain: Optional[str] = None,
) -> None:
    """Append one entry to the game ring and mirror plain text to gateway."""
    spec = get_channel(channel_name)
    if spec is None or entry is None:
        return
    history = ring(game, channel_name)
    if history is not None:
        history.append(entry)
    plain = (gateway_plain or "").strip()
    if not plain and spec.gateway_stitch:
        plain = (export_gateway_plain_line(channel_name, entry, game) or "").strip()
    if plain and spec.gateway_stitch:
        _schedule_gateway_mirror(game, channel_name, plain)


def is_empty(game, channel_name: str) -> bool:
    history = ring(game, channel_name)
    return not history


def entries(game, channel_name: str) -> tuple:
    history = ring(game, channel_name)
    if history is None:
        return ()
    return tuple(history)


# ---------------------------------------------------------------------------
# Persistence (meta table) — one JSON list per channel meta_key
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
    spec = get_channel("ooc")
    if spec is None:
        return []
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


def load_channel(conn, channel_name: str) -> list:
    """Load one channel ring from meta (empty list when missing)."""
    spec = get_channel(channel_name)
    if spec is None:
        return []
    raw = _load_json_list(conn, spec.meta_key, ring_max=spec.ring_max)
    if channel_name == "ooc":
        return _normalize_ooc_loaded(raw)
    if channel_name == "wiznet":
        return [line for line in raw if isinstance(line, str)]
    return raw


def save_channel(conn, game, channel_name: str) -> None:
    """Persist one channel ring to meta."""
    spec = get_channel(channel_name)
    if spec is None:
        return
    if channel_name == "ooc":
        items = _serialize_ooc_ring(game)
    elif channel_name == "wiznet":
        items = _serialize_wiznet_ring(game)
    else:
        history = ring(game, channel_name) or []
        items = list(history)
    _save_json_list(conn, spec.meta_key, items, ring_max=spec.ring_max)


def load_all(game, conn) -> None:
    """Refill every registered ring from meta after db connect."""
    for spec in _CHANNELS.values():
        target = ring(game, spec.name)
        if target is None:
            setattr(game, spec.game_attr, deque(maxlen=spec.ring_max))
            target = ring(game, spec.name)
        for line in load_channel(conn, spec.name):
            target.append(line)


def save_all(conn, game) -> None:
    """Persist every registered ring (called from ``game.save()`` meta pass)."""
    for spec in _CHANNELS.values():
        save_channel(conn, game, spec.name)


# ---------------------------------------------------------------------------
# Gateway mirror (plain text only — separate from game rings)
# ---------------------------------------------------------------------------


def export_gateway_plain_line(
    channel_name: str, entry: Any, game,
) -> Optional[str]:
    """Best-effort plain line for gateway stitch storage."""
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
    return None


def export_gateway_snapshot(game) -> dict[str, list[str]]:
    """Plain lines per channel for gateway ``chat_history`` CTRL sync."""
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
# Game-side bare replay helpers
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
    """Paint one stored wiznet plain line (entry is already ``[WIZ] …``)."""
    from engine import gm_notify
    from engine import style

    plain = entry if isinstance(entry, str) else str(entry)
    return style.paint("absinthe_green", plain)


# ---------------------------------------------------------------------------
# Built-in channels (register at import)
# ---------------------------------------------------------------------------

register_channel(
    ChannelSpec(
        name="ooc",
        game_attr="ooc_history",
        meta_key="ooc_history",
        verb="ooc",
        replay_header="Recent OOC (last 20):",
        empty_message="No recent OOC. Type 'ooc <message>' to speak.",
        usage_message="Usage: ooc <message>  (bare ooc replays history)",
        ring_max=OOC_HISTORY_MAX,
        staff_only=False,
        gateway_stitch=True,
    )
)

register_channel(
    ChannelSpec(
        name="wiznet",
        game_attr="wiznet_history",
        meta_key="wiznet_history",
        verb="wiznet",
        replay_header="Recent wiznet (last 20):",
        empty_message="No recent wiznet. Type 'wiznet <text>' to speak.",
        usage_message="Usage: wiznet <message>  (bare wiznet replays history)",
        ring_max=WIZNET_HISTORY_MAX,
        staff_only=True,
        gateway_stitch=True,
    )
)
