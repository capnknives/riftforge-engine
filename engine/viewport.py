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
import subprocess
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
# Staff setup lines ``op=reset`` may dispatch. Player mode blocks other gm*.
RESET_STAFF_LINES = ("gm on", "gm wipebodies", "gm expelspirits")


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


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git_sha() -> str:
    """Short HEAD sha for inspect/hello (empty when git is unavailable)."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_repo_root(),
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


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


def finish_turn(session, turn_id=None) -> None:
    """Release waiters after one dispatch (including unknown-command paths).

    HB-35: a timed-out turn must not set the Event belonging to a newer
    inject. Callers pass the turn id captured at dispatch start; a
    mismatch means a later inject already replaced the waiter.
    """
    if session is None:
        return
    if turn_id is not None and getattr(session, "_viewport_turn_id", None) != turn_id:
        return
    done = getattr(session, "_viewport_turn_done", None)
    if done is not None and not done.is_set():
        done.set()


def _first_verb(line: str) -> str:
    return (line or "").strip().split(None, 1)[0].lower() if (line or "").strip() else ""


def player_mode_blocks(line: str) -> bool:
    """True when mode=player should refuse this line (staff hub)."""
    verb = _first_verb(line)
    return verb == "gm"


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
        self._viewport_turn_id = 0
        self._viewport_turn_done = asyncio.Event()
        self._viewport_turn_done.set()
        # Set when play() reaches the command loop (after auto-look + NEWS).
        self._viewport_play_ready = asyncio.Event()

    def _write(self, message):
        """Record every prose line (including snoop mirrors via emit_raw)."""
        if self.alive and isinstance(message, str):
            self._viewport_hub.record(self, "prose", text=message)
        super()._write(message)

    def send(self, message):
        # Session.send color-strips, snoop-fans, then calls _write (which
        # records). Do not record here — emit_raw also routes through _write.
        super().send(message)

    def send_gmcp(self, package, payload, force=False):
        if self.alive:
            self._viewport_hub.record(
                self, "gmcp", package=package, payload=payload,
            )
        super().send_gmcp(package, payload, force=force)

    async def read_line(self):
        """Mark the play loop ready, then wait for one injected command."""
        ready = getattr(self, "_viewport_play_ready", None)
        if ready is not None and not ready.is_set():
            ready.set()
        return await super().read_line()

    def inject_line(self, line: str) -> None:
        """Queue one player command for the play() loop."""
        text = (line or "").replace("\r", "").replace("\n", "")
        self._viewport_turn_id = int(getattr(self, "_viewport_turn_id", 0) or 0) + 1
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

    async def wait_ready(self, timeout: float = 20.0) -> bool:
        """Wait until play() is in the command loop (past deferred NEWS)."""
        ready = getattr(self, "_viewport_play_ready", None)
        if ready is None:
            return True
        try:
            await asyncio.wait_for(ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False


class ViewportHub:
    """One hub per Game: event ring + at most one Ash ViewportSession."""

    def __init__(self, game):
        self.game = game
        self.events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self.seq = 0
        self.watermark = 0
        self.session: ViewportSession | None = None
        self._play_task: asyncio.Task | None = None
        self.t0 = _now_ms()
        # staff = Ash can gm; player = refuse gm* (quest play).
        self.mode = "staff"
        # Sidecar binds :4002 before Game() returns.
        self.booting = False

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

    def take_events(self, since=None) -> list[dict[str, Any]]:
        """Events after *since*; omitted since uses the client watermark.

        After return, watermark advances to seq so the next omitted-since
        send does not reprint the whole ring (V4/V6).
        """
        if since is None:
            since = self.watermark
        events = self.events_since(int(since))
        self.watermark = self.seq
        return events

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

    def occupy_like_cast(self, char) -> None:
        """Match telnet Cast occupy: staff stamp + drop idle fixture NPC flags.

        ``after_session_attach`` also calls immersion_fixture.apply_fixture_npc_mode
        when SUPERS hooks are registered. This flag drop is the engine-side
        belt so authored quests see a player even if the hook is missing.
        """
        self._stamp_staff_account(char)
        session = self.session
        if session is None or char is None:
            return
        if getattr(session, "character", None) is not char:
            return
        staff = bool(getattr(session, "staff_account", None))
        login_cast = bool(getattr(char, "staff_login", False))
        if staff or login_cast:
            char.is_npc = False
            if getattr(char, "idle_mode", False):
                char.idle_mode = False

    def resolve_body(self, name: str):
        """Find the playable / immersion body (Ash by default)."""
        raw = (name or DEFAULT_CHARACTER).strip() or DEFAULT_CHARACTER
        game = self.game
        if game is None:
            return None
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

        if self.booting or self.game is None:
            raise RuntimeError(
                "viewport sidecar is still booting Game(); wait for hello "
                "booting=false, then open."
            )
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
        self.occupy_like_cast(char)
        if not getattr(char, "idle_mode", False):
            hooks.stamp_input_activity(char, self.game)
        from engine import gmcp
        gmcp.on_session_attach(char, self.game)
        hooks.after_session_attach(char, self.game)
        # Fixture hook may re-apply; occupy again so is_npc stays false
        # while this Session rides the body.
        self.occupy_like_cast(char)
        session._viewport_turn_done = asyncio.Event()
        self._play_task = asyncio.create_task(session.play())
        # Wait until the command loop, not the auto-look finish_turn —
        # deferred NEWS after look used to make the next send time out.
        await session.wait_ready(timeout=20.0)
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
        if not session._viewport_play_ready.is_set():
            await session.wait_ready(timeout=timeout)
        if self.mode == "player" and player_mode_blocks(line):
            session.send(
                "viewport mode=player refuses staff commands. "
                "op=reset / mode=staff for gm, or send player verbs."
            )
            turn = self.last_turn()
            turn["ok"] = False
            turn["blocked"] = True
            turn["seq"] = self.seq
            return turn
        session.inject_line(line)
        ok = await session.wait_turn(timeout=timeout)
        turn = self.last_turn()
        turn["ok"] = ok
        turn["timed_out"] = not ok
        turn["seq"] = self.seq
        return turn

    async def reset_playtest(self) -> dict[str, Any]:
        """Place Ash in a known lobby, clear authored quests, staff setup."""
        self.mode = "staff"
        session, key = await self.attach(DEFAULT_CHARACTER)
        char = session.character
        self.occupy_like_cast(char)
        lobby = _find_room_name_parts(self.game, ("sheriff", "lobby"))
        if lobby is not None and getattr(char, "location", None) is not lobby:
            try:
                char.move_to(lobby)
            except Exception as exc:
                from engine import log_util

                log_util.ops("viewport", "reset move failed", exc=exc)
        try:
            from engine.systems import quests as quests_mod
            for qid in list(quests_mod.active_quest_ids(char) or []):
                try:
                    quests_mod.abandon(char, qid, game=self.game, confirm=True)
                except Exception as exc:
                    from engine import log_util

                    log_util.ops(
                        "viewport",
                        f"reset abandon failed qid={qid}",
                        exc=exc,
                    )
        except Exception as exc:
            from engine import log_util

            log_util.ops("viewport", "reset abandon loop failed", exc=exc)
        last = {}
        for line in RESET_STAFF_LINES:
            last = await self.send_line(line, timeout=20.0)
        return {
            "character": key,
            "room": _room_label(char),
            "turn": last,
        }


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


def _find_room_name_parts(game, parts):
    """Return the first room whose display name contains every *parts* token."""
    if game is None:
        return None
    needles = [p.lower() for p in parts if p]
    rooms = getattr(game, "rooms", None) or {}
    values = rooms.values() if isinstance(rooms, dict) else list(rooms)
    for room in values:
        try:
            from engine.room_vnum import room_name
            label = (room_name(room) or "").lower()
        except Exception:
            label = str(getattr(room, "name", None) or "").lower()
        if all(n in label for n in needles):
            return room
    return None


def _inspect_payload(hub: ViewportHub) -> dict[str, Any]:
    session = hub.session
    char = getattr(session, "character", None) if session else None
    quests = []
    try:
        from engine.systems import quests as quests_mod
        quests = list(quests_mod.active_quest_ids(char) or [])
    except Exception as exc:
        from engine import log_util

        log_util.ops("viewport", "inspect failed field=quests", exc=exc)
        quests = []
    turn = hub.last_turn()
    hp = getattr(char, "hp", None)
    max_hp = getattr(char, "max_hp", None)
    return {
        "ok": True,
        "op": "inspect",
        "seq": hub.seq,
        "turn": turn,
        "character": getattr(char, "key", None),
        "room": _room_label(char),
        "staff_account": getattr(session, "staff_account", None) if session else None,
        "gm_mode": bool(getattr(char, "gm_mode", False)),
        "is_npc": bool(getattr(char, "is_npc", False)),
        "mode": hub.mode,
        "hp": hp,
        "max_hp": max_hp,
        "quests": quests,
        "timed_out": bool(turn.get("timed_out")),
        "sha": _git_sha(),
        "booting": bool(hub.booting),
        "viewport_enabled": viewport_enabled(),
    }


def _resolve_since(req: dict[str, Any], hub: ViewportHub):
    """None means 'use hub watermark'; 0 still means dump the full ring."""
    if "since" not in req or req.get("since") is None:
        return None
    return int(req.get("since") or 0)


def _set_mode(hub: ViewportHub, raw) -> str:
    text = str(raw or "").strip().lower()
    if text in ("player", "staff"):
        hub.mode = text
    return hub.mode


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
                "booting": bool(hub.booting),
                "sha": _git_sha(),
                "viewport_enabled": viewport_enabled(),
                "mode": hub.mode,
            }
        if hub.booting and op not in ("hello", "ping"):
            return {
                "ok": False,
                "error": "viewport sidecar is still booting Game()",
                "booting": True,
            }
        if op == "open":
            if req.get("mode"):
                _set_mode(hub, req.get("mode"))
            name = str(req.get("character") or DEFAULT_CHARACTER)
            session, key = await hub.attach(name)
            char = session.character
            events = hub.events_since(0)
            hub.watermark = hub.seq
            return {
                "ok": True,
                "op": "open",
                "character": key,
                "room": _room_label(char),
                "seq": hub.seq,
                "events": events,
                "turn": hub.last_turn(),
                "staff_account": getattr(session, "staff_account", None),
                "is_npc": bool(getattr(char, "is_npc", False)),
                "mode": hub.mode,
                "sha": _git_sha(),
            }
        if op == "mode":
            mode = _set_mode(hub, req.get("mode") or req.get("line"))
            return {"ok": True, "op": "mode", "mode": mode, "seq": hub.seq}
        if op == "reset":
            info = await hub.reset_playtest()
            events = hub.take_events(None)
            return {
                "ok": True,
                "op": "reset",
                "character": info.get("character"),
                "room": info.get("room"),
                "seq": hub.seq,
                "events": events,
                "turn": info.get("turn") or {},
                "mode": hub.mode,
                "is_npc": bool(
                    getattr(getattr(hub.session, "character", None), "is_npc", False)
                ),
                "staff_account": getattr(hub.session, "staff_account", None)
                if hub.session
                else None,
            }
        if op == "send":
            if req.get("mode"):
                _set_mode(hub, req.get("mode"))
            timeout = float(req.get("timeout") or 20.0)
            since = _resolve_since(req, hub)
            raw_lines = req.get("lines")
            lines = []
            if isinstance(raw_lines, list):
                lines = [str(x) for x in raw_lines if str(x).strip()]
            one = str(req.get("line") or "").strip()
            if one:
                lines.append(one)
            if not lines:
                return {"ok": False, "error": "line or lines is required"}
            turn = {}
            for line in lines:
                turn = await hub.send_line(line, timeout=timeout)
            events = hub.take_events(since)
            # Protocol ok even on wait_turn timeout — prose is in events (V3).
            return {
                "ok": True,
                "op": "send",
                "turn": turn,
                "seq": hub.seq,
                "events": events,
                "timed_out": bool(turn.get("timed_out")),
                "blocked": bool(turn.get("blocked")),
                "mode": hub.mode,
            }
        if op == "poll":
            since = _resolve_since(req, hub)
            return {
                "ok": True,
                "op": "poll",
                "seq": hub.seq,
                "events": hub.take_events(since),
                "turn": hub.last_turn(),
            }
        if op == "inspect":
            return _inspect_payload(hub)
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


async def bind_viewport_server(hub: ViewportHub):
    """Bind loopback JSON and return the asyncio Server (does not serve forever)."""
    host, port = viewport_bind()
    server = await asyncio.start_server(
        lambda r, w: _client_loop(r, w, hub),
        host=host,
        port=port,
    )
    state = "booting" if hub.booting else "ready"
    print(
        f"[viewport] listening on {host}:{port} (Ash play harness, {state})",
        flush=True,
    )
    return server


async def serve_viewport(game) -> None:
    """Bind the loopback JSON listener and serve until cancelled."""
    hub = hub_for(game)
    server = await bind_viewport_server(hub)
    async with server:
        await server.serve_forever()


def start_viewport_task(game):
    """Schedule serve_viewport when enabled. Returns the Task or None.

    Re-applies whitelisted ``.env`` first so a stale watcher module cache
    cannot leave ``RIFTFORGE_VIEWPORT`` unset in this process.
    """
    from engine import env_file

    env_file.apply_repo_env(_repo_root())
    if not viewport_enabled():
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return loop.create_task(serve_viewport(game))
