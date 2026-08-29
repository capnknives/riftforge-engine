"""Ash viewport — agent play harness (loopback JSON, not telnet).

A ViewportSession is a real ``engine.connection.Session`` whose reader/writer
are asyncio queues. Commands go through ``commands.dispatch``; every
``send()`` and GMCP push is captured as structured events, plus the handler
file:line that ran the turn.

The JSON listener binds loopback only (default ``127.0.0.1:4002``). It is
the agent/MCP path for playtest — never a public port, never live unless
``RIFTFORGE_VIEWPORT=1`` is set on that host (crashgate yes, live default no).

Stdlib only. No supers imports.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from collections import deque
from typing import Any

from engine.connection import Session
from engine.gateway_protocol import is_loopback_host

# Protocol version the MCP / viewport_client.py speak.
VIEWPORT_PROTOCOL = 1
DEFAULT_BIND = "127.0.0.1:4002"
DEFAULT_CHARACTER = "Ash"
MAX_EVENTS = 2000
MAX_LINE = 65536


def viewport_enabled() -> bool:
    """True when the game should listen for agent viewport clients."""
    return os.environ.get("RIFTFORGE_VIEWPORT", "0").strip() in (
        "1",
        "true",
        "True",
        "yes",
        "YES",
    )


def viewport_bind() -> tuple[str, int]:
    """Loopback host/port for the JSON viewport listener."""
    text = (os.environ.get("RIFTFORGE_VIEWPORT_BIND") or DEFAULT_BIND).strip()
    if ":" in text:
        host, _, port_s = text.rpartition(":")
        host = host or "127.0.0.1"
        port = int(port_s)
    else:
        host, port = "127.0.0.1", int(text)
    if not is_loopback_host(host):
        allow = os.environ.get("RIFTFORGE_VIEWPORT_ALLOW_NONLOCAL", "").strip()
        if allow not in ("1", "true", "True", "yes", "YES"):
            raise RuntimeError(
                f"viewport bind {host!r} is not loopback; set "
                "RIFTFORGE_VIEWPORT_ALLOW_NONLOCAL=1 only for lab use "
                "(never on live)"
            )
    return host, port


def _now_ms() -> float:
    return time.monotonic() * 1000.0


def _handler_source(handler) -> tuple[str, str]:
    """Return (qualname, file:line) for a command handler."""
    if handler is None:
        return "", ""
    qual = getattr(handler, "__qualname__", None) or getattr(
        handler, "__name__", ""
    )
    mod = getattr(handler, "__module__", "") or ""
    if mod and qual and not str(qual).startswith(mod):
        label = f"{mod}.{qual}"
    else:
        label = str(qual or "")
    try:
        path = inspect.getfile(handler)
        line = inspect.getsourcelines(handler)[1]
    except (OSError, TypeError):
        return label, ""
    # Repo-relative when the file lives under a known package root.
    norm = path.replace("\\", "/")
    rel = os.path.basename(path)
    for needle in ("/engine/", "/supers/", "/help/", "/tools/"):
        idx = norm.lower().rfind(needle)
        if idx >= 0:
            rel = norm[idx + 1 :]
            break
    else:
        if norm.lower().endswith("/commands.py"):
            rel = "commands.py"
    return label, f"{rel}:{line}"


def note_dispatch(session, verb, handler) -> None:
    """Stamp the current viewport turn with the resolved handler (no-op else)."""
    if session is None or not getattr(session, "viewport_enabled", False):
        return
    label, source = _handler_source(handler)
    session._viewport_last_turn = {
        "verb": verb or "",
        "handler": label,
        "source": source,
    }
    hub = getattr(getattr(session, "game", None), "viewport_hub", None)
    if hub is not None:
        hub.record(
            session,
            "dispatch",
            verb=verb or "",
            handler=label,
            source=source,
        )


def finish_turn(session) -> None:
    """Release waiters after one dispatch (including unknown-command paths)."""
    if session is None:
        return
    done = getattr(session, "_viewport_turn_done", None)
    if done is not None and not done.is_set():
        done.set()


class ViewportWriter:
    """asyncio StreamWriter subset used by Session._write / drain / close."""

    def __init__(self):
        self._closing = False
        self._closed = asyncio.Event()

    def write(self, data: bytes) -> None:
        # Prose is recorded in ViewportSession.send, not from the wire, so
        # telnet IAC / GMCP frames can be discarded here.
        _ = data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self._closing = True
        self._closed.set()

    def is_closing(self) -> bool:
        return self._closing

    def get_extra_info(self, name, default=None):
        if name == "peername":
            return ("viewport", 0)
        return default

    async def wait_closed(self) -> None:
        await self._closed.wait()


class ViewportSession(Session):
    """Real Session with queue I/O — live send contract, captured feed."""

    def __init__(self, game, hub: "ViewportHub"):
        reader = asyncio.StreamReader()
        writer = ViewportWriter()
        super().__init__(reader, writer, game)
        self.viewport_enabled = True
        self.gmcp_enabled = True
        self._viewport_hub = hub
        self._viewport_last_turn = {}
        self._viewport_turn_done = asyncio.Event()
        self._viewport_turn_done.set()

    def send(self, message):
        # Record the live str (do not stringify lists — Session._write must
        # still TypeError on a non-str, same as FakeSession's str-only gate).
        if self.alive and isinstance(message, str):
            self._viewport_hub.record(self, "prose", text=message)
        super().send(message)

    def send_gmcp(self, package, payload, force=False):
        if self.alive:
            self._viewport_hub.record(
                self, "gmcp", package=package, payload=payload,
            )
        super().send_gmcp(package, payload, force=force)

    def inject_line(self, line: str) -> None:
        """Queue one player command for the play() loop."""
        text = (line or "").replace("\r", "").replace("\n", "")
        self._viewport_turn_done = asyncio.Event()
        self._viewport_hub.record(self, "in", text=text)
        self.reader.feed_data((text + "\r\n").encode("utf-8"))

    async def wait_turn(self, timeout: float = 20.0) -> bool:
        """Wait until dispatch finishes (or timeout)."""
        done = self._viewport_turn_done
        if done is None:
            return True
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False


class ViewportHub:
    """One hub per Game: event ring + at most one Ash ViewportSession."""

    def __init__(self, game):
        self.game = game
        self.events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self.seq = 0
        self.session: ViewportSession | None = None
        self._play_task: asyncio.Task | None = None
        self.t0 = _now_ms()

    def record(self, session, kind: str, **fields) -> dict[str, Any]:
        self.seq += 1
        event = {
            "seq": self.seq,
            "t_ms": round(_now_ms() - self.t0, 2),
            "kind": kind,
        }
        event.update(fields)
        # GMCP payloads must stay JSON-serializable for the client.
        payload = event.get("payload")
        if payload is not None and not isinstance(payload, (dict, list, str, int, float, bool)):
            event["payload"] = str(payload)
        self.events.append(event)
        return event

    def events_since(self, since: int) -> list[dict[str, Any]]:
        return [e for e in self.events if int(e.get("seq") or 0) > int(since or 0)]

    def last_turn(self) -> dict[str, Any]:
        session = self.session
        if session is None:
            return {}
        return dict(getattr(session, "_viewport_last_turn", None) or {})

    def _stamp_staff_account(self, char) -> None:
        """Copy a staff account onto this viewport Session when occupying Ash.

        Telnet Cast occupy stamps ``session.staff_account`` from the GM
        login. Viewport ``open Ash`` skips that menu, so ``_is_gm`` used
        to fail even when crashgate account Ash is head_gm.
        """
        session = self.session
        if session is None or getattr(session, "staff_account", None):
            return
        from engine import accounts as accounts_mod

        game = self.game
        acct = accounts_mod.account_for_character(game, char)
        if accounts_mod.account_is_staff(acct):
            session.staff_account = acct.name
            return
        env_name = os.environ.get("RIFTFORGE_VIEWPORT_STAFF_ACCOUNT", "").strip()
        names = []
        if env_name:
            names.append(env_name)
        char_key = str(getattr(char, "key", "") or "").strip().lower()
        # Default Ash fixture: try the crashgate staff-login accounts.
        # Do not grant GM on a random occupied body just because Ash exists.
        if char_key == "ash":
            for want in ("Ash", "CrashgateAgent1"):
                if want not in names:
                    names.append(want)
        for want in names:
            found = accounts_mod.find_account(game, want)
            if accounts_mod.account_is_staff(found):
                session.staff_account = found.name
                return

    def resolve_body(self, name: str):
        """Find the playable / immersion body (Ash by default)."""
        raw = (name or DEFAULT_CHARACTER).strip() or DEFAULT_CHARACTER
        game = self.game
        finder = getattr(game, "find_login_character", None)
        char = finder(raw) if callable(finder) else None
        if char is None:
            finder2 = getattr(game, "find_character", None)
            char = finder2(raw) if callable(finder2) else None
        if char is None:
            low = raw.lower()
            for candidate in list(getattr(game, "characters", None) or []):
                key = str(getattr(candidate, "key", "") or "")
                if key.lower() == low:
                    return candidate
        return char

    async def attach(self, name: str | None = None) -> tuple[ViewportSession, str]:
        """Bind a ViewportSession to Ash (or *name*) and start play()."""
        from engine.session_attach import detach_stale_sessions
        from engine import hooks

        want = (name or DEFAULT_CHARACTER).strip() or DEFAULT_CHARACTER
        char = self.resolve_body(want)
        if char is None:
            raise LookupError(
                f"No character named {want!r} in this Game. Crashgate needs "
                "the seeded Ash body; local sidecar boots a world that "
                "ensure_all should spawn immersion Ash."
            )
        if self.session is not None and self.session.alive:
            if getattr(self.session, "character", None) is char:
                return self.session, want
            await self.detach(park=True)

        session = ViewportSession(self.game, self)
        self.session = session
        detach_stale_sessions(char, self.game, session)
        char.session = session
        session.character = char
        session._promote_to_sessions()
        # Direct occupy (not Cast-menu login) has no staff_account stamp.
        # Crashgate Ash is a staff-login fixture; without this, gm on
        # replies "You aren't a GM." even though account Ash is head_gm.
        self._stamp_staff_account(char)
        if not getattr(char, "idle_mode", False):
            hooks.stamp_input_activity(char, self.game)
        from engine import gmcp
        gmcp.on_session_attach(char, self.game)
        hooks.after_session_attach(char, self.game)
        session._viewport_turn_done = asyncio.Event()
        self._play_task = asyncio.create_task(session.play())
        await session.wait_turn(timeout=20.0)
        return session, getattr(char, "key", want)

    async def detach(self, *, park: bool = True) -> None:
        """End the viewport session; park the body as an Echo when *park*."""
        session = self.session
        if session is None:
            return
        session.alive = False
        try:
            session.reader.feed_eof()
        except Exception:
            pass
        if park:
            try:
                session.disconnect(reason="viewport_close")
            except Exception:
                pass
        task = self._play_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._play_task = None
        self.session = None

    async def send_line(self, line: str, timeout: float = 20.0) -> dict[str, Any]:
        """Inject one command and wait for dispatch to finish."""
        session = self.session
        if session is None or not session.alive:
            raise RuntimeError("viewport is not open; call open first")
        session.inject_line(line)
        ok = await session.wait_turn(timeout=timeout)
        turn = self.last_turn()
        turn["ok"] = ok
        turn["seq"] = self.seq
        return turn


def hub_for(game) -> ViewportHub:
    """Return the Game's viewport hub, creating it if needed."""
    hub = getattr(game, "viewport_hub", None)
    if hub is None:
        hub = ViewportHub(game)
        game.viewport_hub = hub
    return hub


def _room_label(character) -> str:
    room = getattr(character, "location", None)
    if room is None:
        return ""
    try:
        from engine.room_vnum import room_name
        return room_name(room)
    except Exception:
        return str(getattr(room, "name", None) or getattr(room, "key", "") or "")


async def _handle_request(hub: ViewportHub, req: dict[str, Any]) -> dict[str, Any]:
    """One JSON request → JSON response (never raises to the caller)."""
    op = str(req.get("op") or "").strip().lower()
    try:
        if op in ("hello", "ping"):
            session = hub.session
            char = getattr(session, "character", None) if session else None
            return {
                "ok": True,
                "op": "hello",
                "protocol": VIEWPORT_PROTOCOL,
                "character": getattr(char, "key", None),
                "seq": hub.seq,
            }
        if op == "open":
            name = str(req.get("character") or DEFAULT_CHARACTER)
            session, key = await hub.attach(name)
            char = session.character
            return {
                "ok": True,
                "op": "open",
                "character": key,
                "room": _room_label(char),
                "seq": hub.seq,
                "events": hub.events_since(0),
                "turn": hub.last_turn(),
            }
        if op == "send":
            line = str(req.get("line") or "")
            if not line.strip():
                return {"ok": False, "error": "line is required"}
            timeout = float(req.get("timeout") or 20.0)
            since = int(req.get("since") or 0)
            turn = await hub.send_line(line, timeout=timeout)
            return {
                "ok": bool(turn.get("ok", True)),
                "op": "send",
                "turn": turn,
                "seq": hub.seq,
                "events": hub.events_since(since),
            }
        if op == "poll":
            since = int(req.get("since") or 0)
            return {
                "ok": True,
                "op": "poll",
                "seq": hub.seq,
                "events": hub.events_since(since),
                "turn": hub.last_turn(),
            }
        if op == "inspect":
            turn = hub.last_turn()
            return {
                "ok": True,
                "op": "inspect",
                "seq": hub.seq,
                "turn": turn,
                "character": getattr(
                    getattr(hub.session, "character", None), "key", None
                ),
                "room": _room_label(getattr(hub.session, "character", None)),
            }
        if op == "close":
            await hub.detach(park=True)
            return {"ok": True, "op": "close", "seq": hub.seq}
        return {"ok": False, "error": f"unknown op {op!r}"}
    except LookupError as err:
        return {"ok": False, "error": str(err)}
    except Exception as err:
        return {"ok": False, "error": f"{type(err).__name__}: {err}"}


async def _client_loop(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, hub: ViewportHub):
    """One JSON-lines client. Does not close the Ash session on TCP hangup."""
    try:
        while True:
            raw = await reader.readline()
            if not raw:
                break
            if len(raw) > MAX_LINE:
                reply = {"ok": False, "error": "line too long"}
            else:
                try:
                    req = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError as err:
                    reply = {"ok": False, "error": f"bad json: {err}"}
                else:
                    if not isinstance(req, dict):
                        reply = {"ok": False, "error": "request must be an object"}
                    else:
                        reply = await _handle_request(hub, req)
            writer.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def serve_viewport(game) -> None:
    """Bind the loopback JSON listener and serve until cancelled."""
    host, port = viewport_bind()
    hub = hub_for(game)
    server = await asyncio.start_server(
        lambda r, w: _client_loop(r, w, hub),
        host=host,
        port=port,
    )
    print(f"[viewport] listening on {host}:{port} (Ash play harness)", flush=True)
    async with server:
        await server.serve_forever()


def start_viewport_task(game):
    """Schedule serve_viewport when enabled. Returns the Task or None.

    Re-applies whitelisted ``.env`` first so a stale watcher module cache
    cannot leave ``RIFTFORGE_VIEWPORT`` unset in this process.
    """
    from engine import env_file

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_file.apply_repo_env(root)
    if not viewport_enabled():
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return loop.create_task(serve_viewport(game))
