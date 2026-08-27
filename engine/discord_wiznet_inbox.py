"""discord_wiznet_inbox.py -- ingest Discord #staff chat into live wiznet.

The sidecar bot drops one JSON file per allowlisted staff message under
``.discord_wiznet_inbox/`` (gitignored). ``tick_discord_wiznet_inbox``
runs on the game heartbeat, broadcasts each line, and deletes the file.

Loop prevention: Discord-originated lines skip the outbound Discord mirror
(same pattern as ``discord_ooc_inbox``).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

INBOX_DIR_NAME = ".discord_wiznet_inbox"


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
    """Write one inbound wiznet payload (bot-side). Returns path or None."""
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


def _face_for_payload(game, payload: dict) -> str:
    """Prefer linked MUD GM face; else Discord display name."""
    from engine import discord_ooc_links

    face = str(payload.get("face") or "").strip()
    discord_id = str(payload.get("discord_user_id") or "")
    account = discord_ooc_links.lookup_account_for_discord(discord_id)
    if account:
        linked = discord_ooc_links.resolve_ooc_face_for_discord(game, account)
        if linked:
            return linked
    if face:
        if not face.endswith("(Discord)") and not face.endswith("(GM)"):
            return f"{face}(Discord)"
        return face
    return "Discord"


def tick_discord_wiznet_inbox(game) -> None:
    """Process every pending inbound wiznet file (sync tick handler)."""
    from engine import discord_bridge
    from engine import gm_notify

    if not discord_bridge.is_toggle_on("wiznet"):
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
        face = _face_for_payload(game, payload)
        gm_notify.wiznet_broadcast_from_discord(game, face, message)


def register_tick(game) -> None:
    """Register the inbox poller on the game heartbeat."""
    from engine.tick_registry import register_tick

    register_tick(
        game,
        tick_discord_wiznet_inbox,
        order=5,
        name="discord_wiznet_inbox",
    )
