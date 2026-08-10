"""discord_ooc_inbox.py -- ingest Discord OOC posts into the live game.

The sidecar bot (``tools/discord_ooc_bot.py``) drops one JSON file per
message under ``.discord_ooc_inbox/`` (gitignored). ``tick_discord_ooc_inbox``
runs on the game heartbeat, broadcasts each line, and deletes the file.

Loop prevention: Discord-originated lines skip the outbound webhook mirror.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

INBOX_DIR_NAME = ".discord_ooc_inbox"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def inbox_dir(root=None) -> Path:
    base = Path(root) if root is not None else _repo_root()
    return base / INBOX_DIR_NAME


def ensure_inbox_dir(root=None) -> Path:
    path = inbox_dir(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def enqueue_message(
    discord_user_id: str,
    message: str,
    *,
    face: str | None = None,
    root=None,
) -> Path | None:
    """Write one inbound OOC payload (bot-side). Returns path or None."""
    text = str(message or "").strip()
    if not text:
        return None
    directory = ensure_inbox_dir(root)
    payload = {
        "discord_user_id": str(discord_user_id),
        "message": text[:500],
        "face": str(face or "").strip(),
        "ts": time.time(),
    }
    name = f"msg-{int(time.time() * 1000)}-{os.getpid()}.json"
    path = directory / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _load_payload(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def tick_discord_ooc_inbox(game) -> None:
    """Process every pending inbound OOC file (sync tick handler)."""
    from engine import discord_bridge
    from engine import discord_ooc_links
    from engine import ooc_channel

    if not discord_bridge.is_toggle_on("ooc"):
        return
    directory = inbox_dir()
    if not directory.is_dir():
        return
    paths = sorted(directory.glob("msg-*.json"))
    if not paths:
        return
    for path in paths:
        payload = _load_payload(path)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        if not payload:
            continue
        message = str(payload.get("message") or "").strip()
        if not message:
            continue
        discord_id = str(payload.get("discord_user_id") or "")
        account = discord_ooc_links.lookup_account_for_discord(discord_id)
        if not account:
            continue
        face = str(payload.get("face") or "").strip()
        if not face:
            face = discord_ooc_links.resolve_ooc_face_for_discord(game, account)
        ooc_channel.broadcast_ooc_from_discord(game, face, message)


def register_tick(game) -> None:
    """Register the inbox poller on the game heartbeat."""
    from engine.tick_registry import register_tick

    register_tick(
        game,
        tick_discord_ooc_inbox,
        order=5,
        name="discord_ooc_inbox",
    )
