"""discord_staff_ops_inbox.py -- Discord #ops restore/revive into the live game.

The sidecar bot (``tools/discord_staff_ops_bot.py``) drops JSON requests under
``.discord_staff_ops_inbox/`` (gitignored). ``tick_discord_staff_ops_inbox``
runs on the game heartbeat, applies GM restore / squashbug logic, and writes a
short result to ``.discord_staff_ops_outbox/`` for the bot to reply.

Restart / revert SHA are handled in the bot via ``watcher_request`` (no game
tick needed). Only world mutations that need ``Game`` state use this inbox.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

INBOX_DIR_NAME = ".discord_staff_ops_inbox"
OUTBOX_DIR_NAME = ".discord_staff_ops_outbox"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def inbox_dir(root=None) -> Path:
    base = Path(root) if root is not None else _repo_root()
    return base / INBOX_DIR_NAME


def outbox_dir(root=None) -> Path:
    base = Path(root) if root is not None else _repo_root()
    return base / OUTBOX_DIR_NAME


def ensure_dirs(root=None) -> tuple[Path, Path]:
    inbox = inbox_dir(root)
    outbox = outbox_dir(root)
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    return inbox, outbox


def new_request_id() -> str:
    """Unique id shared between inbox request and outbox reply."""
    return uuid.uuid4().hex[:12]


def enqueue_op(
    op: str,
    args: str,
    *,
    discord_user_id: str,
    discord_name: str = "",
    request_id: str | None = None,
    root=None,
) -> tuple[Path | None, str]:
    """Write one pending ops request (bot-side). Returns (path, request_id)."""
    op_s = str(op or "").strip().lower()
    if not op_s:
        return None, ""
    rid = (request_id or new_request_id()).strip() or new_request_id()
    directory, _ = ensure_dirs(root)
    payload = {
        "request_id": rid,
        "op": op_s,
        "args": str(args or "").strip(),
        "discord_user_id": str(discord_user_id or ""),
        "discord_name": str(discord_name or "").strip(),
        "ts": time.time(),
    }
    name = f"ops-{rid}.json"
    path = directory / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path, rid


def write_result(
    request_id: str,
    *,
    ok: bool,
    message: str,
    root=None,
) -> Path | None:
    """Write one outbox reply for the bot to pick up."""
    rid = str(request_id or "").strip()
    if not rid:
        return None
    _, directory = ensure_dirs(root)
    payload = {
        "request_id": rid,
        "ok": bool(ok),
        "message": str(message or "").strip() or ("OK" if ok else "Failed"),
        "ts": time.time(),
    }
    path = directory / f"ops-{rid}.done.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_result(request_id: str, *, root=None, delete: bool = True) -> dict | None:
    """Load one outbox reply; optionally delete after read."""
    rid = str(request_id or "").strip()
    if not rid:
        return None
    path = outbox_dir(root) / f"ops-{rid}.done.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if delete:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return raw if isinstance(raw, dict) else None


def _load_request(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def execute_op(game, op: str, args: str) -> tuple[bool, str]:
    """Run one inbox op against the live ``Game`` (head-GM equivalent)."""
    from engine import hooks as hooks_mod

    return hooks_mod.discord_staff_op_executor(game, op, args)


def tick_discord_staff_ops_inbox(game) -> None:
    """Process every pending Discord ops file (sync tick handler)."""
    directory = inbox_dir()
    if not directory.is_dir():
        return
    paths = sorted(directory.glob("ops-*.json"))
    if not paths:
        return
    for path in paths:
        payload = _load_request(path)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        if not payload:
            continue
        rid = str(payload.get("request_id") or "").strip()
        op = str(payload.get("op") or "").strip()
        args = str(payload.get("args") or "").strip()
        by = str(payload.get("discord_name") or payload.get("discord_user_id") or "discord")
        try:
            ok, msg = execute_op(game, op, args)
        except Exception as exc:
            ok, msg = False, f"Discord ops failed: {exc}"
        print(
            f"[discord_staff_ops] {by!r} op={op!r} args={args!r} ok={ok}: {msg}",
            flush=True,
        )
        if rid:
            write_result(rid, ok=ok, message=msg)


def register_tick(game) -> None:
    """Register the inbox poller on the game heartbeat."""
    from engine.tick_registry import register_tick

    register_tick(
        game,
        tick_discord_staff_ops_inbox,
        order=6,
        name="discord_staff_ops_inbox",
    )
