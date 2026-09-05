"""
gateway_client.py -- game-side IPC connection to engine.gateway.

When RIFTFORGE_GATEWAY=1, server.py does not bind :4000. Instead it connects
to the gateway's IPC port and creates Session objects whose reader/writer
are IPC adapters (bytes tagged with a session id).

Reattach: after hello/welcome, resume logged-in characters by name without
calling disconnect() (no Echo on game restart). Mid-login sessions reset
to the name prompt.

Stdlib only.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Callable, Optional

from engine.gateway_protocol import (
    TYPE_CTRL,
    TYPE_DATA,
    encode_ctrl,
    encode_data,
    parse_ipc_addr,
    read_frame,
    require_loopback_ipc,
)


def gateway_enabled() -> bool:
    """True when the game should speak IPC instead of binding telnet."""
    return os.environ.get("RIFTFORGE_GATEWAY", "0").strip() not in (
        "0",
        "false",
        "False",
        "no",
        "NO",
        "",
    )


def ipc_addr() -> tuple[str, int]:
    """Host/port of the gateway IPC listener (loopback required — M3)."""
    host, port = parse_ipc_addr()
    require_loopback_ipc(host, role="gateway_client")
    return host, port


class GatewaySessionWriter:
    """asyncio-like StreamWriter that sends DATA frames for one session.

    Session.play() expects writer.write / drain / close / is_closing /
    get_extra_info / wait_closed — we implement the subset used by
    engine.connection.Session.
    """

    def __init__(
        self,
        session_id: str,
        send_fn: Callable,
        peer_host: Optional[str] = None,
    ):
        self.session_id = session_id
        self._send = send_fn  # async callable(bytes) -> None
        self._closing = False
        self._closed = asyncio.Event()
        self._closed.set()  # start "open" wait_closed semantics: not closed yet
        self._closed.clear()
        # Real public telnet peer from the gateway (CTRL open/welcome).
        # Stored as (host, port) so gm_notify.peer_host matches direct telnet.
        if peer_host:
            self._peername = (str(peer_host), 0)
        else:
            # Older gateway / missing field -- keep the historical stub so
            # callers still get a non-None host string.
            self._peername = ("gateway", 0)

        # Coalesce many Session.write() calls into one IPC DATA frame.
        # create_task-per-line was a live CPU leak under look/help bursts
        # (follow-on item 6). drain() awaits the in-flight flush.
        self._pending = bytearray()
        self._flush_task = None

    def write(self, data: bytes) -> None:
        """Queue telnet bytes; schedule one flush task if none is in flight."""
        if self._closing or not data:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._pending.extend(data)
        task = self._flush_task
        if task is None or task.done():
            self._flush_task = loop.create_task(self._flush())

    async def _flush(self) -> None:
        """Send coalesced pending bytes; loop if write() raced the send."""
        while self._pending and not self._closing:
            payload = bytes(self._pending)
            del self._pending[:]
            await self._send(encode_data(self.session_id, payload))

    async def drain(self) -> None:
        """Wait until queued telnet bytes have been handed to IPC send."""
        task = self._flush_task
        if task is not None and not task.done():
            await task
        if self._pending and not self._closing:
            await self._flush()

    def close(self) -> None:
        """Mark closed; gateway still holds the real socket until client hangs up."""
        self._closing = True
        self._closed.set()

    def is_closing(self) -> bool:
        return self._closing

    async def wait_closed(self) -> None:
        await self._closed.wait()

    def get_extra_info(self, name: str, default=None):
        """Return the real client peer when the gateway forwarded it.

        Banlist + head-GM staff pings use this. Junior GM ops lines redact
        via gm_notify.format_from(viewer=…), not by stubbing the peer here.
        """
        if name == "peername":
            return self._peername
        return default


class GatewaySessionReader:
    """asyncio StreamReader fed by DATA frames for one session id."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._buffer = asyncio.Queue()
        self._eof = False
        # Compatibility: Session uses reader.read(n) and at_eof().
        self._leftover = b""

    def feed(self, data: bytes) -> None:
        """Push bytes from the gateway into this session's read queue."""
        if data:
            self._buffer.put_nowait(data)

    def feed_eof(self) -> None:
        """Signal that the client (or gateway) closed this session."""
        self._eof = True
        self._buffer.put_nowait(b"")

    def at_eof(self) -> bool:
        return self._eof and not self._leftover and self._buffer.empty()

    async def read(self, n: int = -1) -> bytes:
        """Read up to n bytes (or whatever is next) like StreamReader.read."""
        if self._leftover:
            if n < 0 or n >= len(self._leftover):
                out, self._leftover = self._leftover, b""
                return out
            out, self._leftover = self._leftover[:n], self._leftover[n:]
            return out
        if self._eof and self._buffer.empty():
            return b""
        chunk = await self._buffer.get()
        if not chunk:
            return b""
        if n < 0 or n >= len(chunk):
            return chunk
        self._leftover = chunk[n:]
        return chunk[:n]

    async def readline(self) -> bytes:
        """Read until \\n (used by some login paths)."""
        parts = []
        while True:
            chunk = await self.read(1)
            if not chunk:
                break
            parts.append(chunk)
            if chunk == b"\n":
                break
        return b"".join(parts)


class GatewayBridge:
    """Owns the IPC socket and maps session ids to Session tasks."""

    def __init__(self, game, session_factory: Callable):
        """
        session_factory(reader, writer, game, session_id=...) -> Session
        must return a Session that understands gateway_session_id.
        """
        self.game = game
        self.session_factory = session_factory
        self.reader = None
        self.writer = None
        self._sessions: dict[str, object] = {}  # sid -> Session
        self._readers: dict[str, GatewaySessionReader] = {}
        self._send_lock = asyncio.Lock()
        # World heartbeat starts after welcome/reattach (not at process boot).
        self._tick_loop_started = False
        self._last_gateway_frame_at = 0.0
        # Background stitch after welcome -- cancelled if IPC dies.
        self._welcome_boot_task = None
        # Gateway stitch-intercept off while gap-fill heals run (commands
        # still flow over IPC; this flag only blocks the "game looks stale"
        # OOC intercept). Watchdog also skips stale-kill while True.
        self._boot_warming = False

    def _ipc_ping_interval_seconds(self) -> float:
        raw = (os.environ.get("GAME_GATEWAY_IPC_PING_SECONDS") or "").strip()
        try:
            value = float(raw) if raw else 15.0
        except ValueError:
            value = 15.0
        return max(5.0, value)

    def _ipc_stale_seconds(self) -> float:
        raw = (os.environ.get("GAME_GATEWAY_IPC_STALE_SECONDS") or "").strip()
        try:
            value = float(raw) if raw else 45.0
        except ValueError:
            value = 45.0
        ping = self._ipc_ping_interval_seconds()
        return max(ping + 5.0, value)

    async def _ipc_watchdog_loop(self) -> None:
        """Exit the game process when gateway IPC stops responding.

        Split-brain recovery: ticks may continue while the gateway lost the
        IPC writer — periodic ping + stale detection forces watcher respawn.
        """
        interval = self._ipc_ping_interval_seconds()
        stale_limit = self._ipc_stale_seconds()
        while True:
            await asyncio.sleep(interval)
            writer = self.writer
            if writer is None or writer.is_closing():
                return
            now = time.monotonic()
            if (
                self._tick_loop_started
                and not self._boot_warming
                and self._last_gateway_frame_at > 0
                and (now - self._last_gateway_frame_at) > stale_limit
            ):
                print(
                    f"[gateway_client] no gateway IPC frame for "
                    f"{now - self._last_gateway_frame_at:.0f}s "
                    "-- exiting for watcher respawn",
                    flush=True,
                )
                try:
                    writer.close()
                except Exception:
                    pass
                return
            try:
                await self.send_frame(encode_ctrl({"op": "ping"}))
            except (
                asyncio.IncompleteReadError,
                ConnectionError,
                OSError,
            ):
                print(
                    "[gateway_client] IPC ping failed -- exiting",
                    flush=True,
                )
                try:
                    writer.close()
                except Exception:
                    pass
                return

    def _ensure_tick_loop(self) -> None:
        """Start game.tick_loop once, after held clients are rebound.

        server.py deliberately does not create_task(tick_loop) for the
        gateway path -- Cadence / no_loiter must not see connected PCs as
        sessionless Echoes during the IPC hello/welcome window.
        """
        if self._tick_loop_started:
            return
        self._tick_loop_started = True
        asyncio.create_task(self.game.tick_loop())

    async def send_frame(self, frame: bytes) -> None:
        """Write one framed message to the gateway."""
        async with self._send_lock:
            if self.writer is None:
                return
            self.writer.write(frame)
            await self.writer.drain()

    async def notify_bound(
        self,
        session_id: str,
        name: str,
        *,
        ooc_face: Optional[str] = None,
        head_gm: bool = False,
        staff_gm: bool = False,
        gmcp_supports: Optional[dict] = None,
    ) -> None:
        """Tell the gateway this sid is logged in as name (for reattach)."""
        msg = {"op": "bound", "sid": session_id, "name": name}
        if ooc_face:
            msg["ooc_face"] = ooc_face
        if head_gm:
            msg["head_gm"] = True
        if staff_gm:
            msg["staff_gm"] = True
        if isinstance(gmcp_supports, dict) and gmcp_supports:
            msg["gmcp_supports"] = dict(gmcp_supports)
        await self.send_frame(encode_ctrl(msg))

    async def notify_gmcp_supports(
        self, session_id: str, gmcp_supports: dict
    ) -> None:
        """Stash telnet Core.Supports on the gateway slot (copyover reattach)."""
        mapping = dict(gmcp_supports or {})
        await self.send_frame(
            encode_ctrl({
                "op": "gmcp_supports",
                "sid": session_id,
                "gmcp_supports": mapping,
            })
        )

    async def notify_unbound(self, session_id: str) -> None:
        """Clear the bound name (logout / mid-login reset)."""
        await self.send_frame(encode_ctrl({"op": "unbound", "sid": session_id}))

    async def kick_client(self, session_id: str) -> None:
        """Ask the gateway to close the public TCP for this session (quit)."""
        await self.send_frame(encode_ctrl({"op": "kick", "sid": session_id}))

    async def notify_planned_restart(self) -> None:
        """Tell the gateway this IPC drop is a planned game-only reload.

        Used by copyover / watcher SIGUSR1 before ``os._exit`` so WKNZ
        Discord does not post crash/uncrash for every auto-deploy. A short
        sleep gives the CTRL frame time to drain before the process dies.
        """
        await self.send_frame(encode_ctrl({"op": "planned_restart"}))
        await asyncio.sleep(0.05)

    async def notify_boot_warming(self, active: bool) -> None:
        """Tell the gateway deferred boot is running (suppress stitch intercept)."""
        self._boot_warming = bool(active)
        await self.send_frame(
            encode_ctrl({"op": "boot_warming", "active": self._boot_warming})
        )

    def _begin_gateway_copyover_reattach(self) -> None:
        """Reset veil-playable gate before held sessions reattach."""
        import asyncio

        # Copyover restore, not cold-boot re-seed -- boot_seed uses this
        # to skip civic housing maintain and open look/who immediately.
        self.game._gateway_copyover_reattach = True
        self.game._veil_world_ready = False
        self.game._veil_stitch_announced = False
        self.game._veil_hold_ready_until_announce = True
        self.game._veil_ready_event = asyncio.Event()

    async def _wait_gateway_copyover_sessions(self) -> None:
        """Let reattach tasks reach the playable gate before deferred heals."""
        await asyncio.sleep(0)

    async def _end_gateway_copyover_reattach(self) -> None:
        """Drop boot_warming after background heals (playable gate already open)."""
        self.game._veil_hold_ready_until_announce = False
        ev = getattr(self.game, "_veil_ready_event", None)
        if ev is not None and not ev.is_set():
            ev.set()
        await self.notify_boot_warming(False)

    async def _complete_welcome_boot(self, *, reload: bool) -> None:
        """Catch-up + deferred heals, off the IPC read loop.

        Classic copyover.resume() runs after execv; this is the gateway
        equivalent. Opening the veil *first* on a game-only reload
        lets look/who run -- and because this is a task, DATA frames still
        reach play(). Empty-who reloads still skip the heal catalog.
        """
        from engine import deploy_notify
        from engine import hooks
        from engine.session_attach import sweep_duplicate_session_attaches

        try:
            await deploy_notify.on_resume(self.game)
            if reload:
                # Diku copyover_recover: players type before zone resets.
                hooks.open_veil_playable(self.game)
            await hooks.gateway_resume_hook(self.game)
            await hooks.run_deferred_boot_seed_async(self.game)
            deploy_notify.announce_rewrite_ready(self.game)
            swept = sweep_duplicate_session_attaches(self.game)
            if swept:
                print(
                    f"[gateway_client] swept {swept} duplicate session row(s)",
                    flush=True,
                )
            try:
                await self.notify_chat_history()
            except Exception as exc:
                print(
                    f"[gateway_client] chat_history sync skipped: {exc!r}",
                    flush=True,
                )
            from engine import copyover as copyover_mod

            copyover_mod.mark_copyover_ready()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            import traceback

            print(
                f"[gateway_client] welcome boot failed: {exc!r}",
                flush=True,
            )
            traceback.print_exc()
        finally:
            if reload:
                await self._end_gateway_copyover_reattach()
            else:
                await self.notify_boot_warming(False)

    def _schedule_ctrl(self, msg: dict) -> None:
        """Fire-and-forget one CTRL frame from sync game code."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.send_frame(encode_ctrl(msg)))

    async def notify_chat_history(self) -> None:
        """Push the game's channel rings to the gateway stitch buffers."""
        from engine import channel_history

        snapshot = channel_history.export_gateway_snapshot(self.game)
        await self.send_frame(
            encode_ctrl({"op": "chat_history", "channels": snapshot})
        )

    def schedule_channel_mirror(self, channel_name: str, plain_line: str) -> None:
        """Mirror one plain channel line to the gateway stitch buffer."""
        text = (plain_line or "").strip()
        if not text:
            return
        self._schedule_ctrl(
            {"op": "chat_append", "channel": channel_name, "line": text}
        )

    async def connect_and_run(self) -> None:
        """Connect to gateway IPC, hello/welcome, then pump frames forever."""
        host, port = ipc_addr()
        # Retry until gateway is up (watcher may start game slightly early).
        for attempt in range(60):
            try:
                self.reader, self.writer = await asyncio.open_connection(host, port)
                break
            except (ConnectionRefusedError, OSError):
                await asyncio.sleep(0.25)
        else:
            raise RuntimeError(
                f"gateway IPC not reachable at {host}:{port} after retries"
            )

        await self.send_frame(encode_ctrl({"op": "hello"}))
        print(f"[gateway_client] connected to {host}:{port}", flush=True)

        self._last_gateway_frame_at = time.monotonic()
        watchdog = asyncio.create_task(
            self._ipc_watchdog_loop(),
            name="gateway-ipc-watchdog",
        )

        try:
            while True:
                ftype, sid, payload = await read_frame(self.reader)
                if ftype is None:
                    break
                self._last_gateway_frame_at = time.monotonic()
                if ftype == TYPE_CTRL:
                    await self._on_ctrl(payload or {})
                elif ftype == TYPE_DATA and sid:
                    r = self._readers.get(sid)
                    if r is not None:
                        r.feed(payload or b"")
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            print(f"[gateway_client] IPC ended: {exc}", flush=True)
        finally:
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass
            # Do not disconnect characters — gateway still holds sockets.
            # Cancel play tasks only; leave Echo conversion to real client close.
            boot_task = getattr(self, "_welcome_boot_task", None)
            if boot_task is not None and not boot_task.done():
                boot_task.cancel()
                try:
                    await boot_task
                except asyncio.CancelledError:
                    pass
            self._welcome_boot_task = None
            for sid, session in list(self._sessions.items()):
                task = getattr(session, "_gateway_task", None)
                if task is not None and not task.done():
                    task.cancel()
            self._sessions.clear()
            self._readers.clear()
            if self.writer is not None:
                try:
                    self.writer.close()
                    await self.writer.wait_closed()
                except Exception:
                    pass

    async def _on_ctrl(self, msg: dict) -> None:
        """Handle a CTRL message from the gateway."""
        op = msg.get("op")
        if op == "welcome":
            held = [
                e for e in (msg.get("sessions") or [])
                if e.get("sid") and e.get("name")
            ]
            from engine.boot_kind import parse_boot_kind, RELOAD

            welcome_kind = parse_boot_kind(msg.get("kind"), default="cold")
            if held or welcome_kind == RELOAD:
                self._begin_gateway_copyover_reattach()
            await self.notify_boot_warming(True)
            for entry in msg.get("sessions") or []:
                sid = entry.get("sid")
                if not sid:
                    continue
                name = entry.get("name")
                peer = entry.get("peer")
                gmcp_supports = entry.get("gmcp_supports")
                await self._open_session(
                    sid,
                    reattach_name=name,
                    peer_host=peer,
                    gmcp_supports=gmcp_supports,
                )
            if held:
                await self._wait_gateway_copyover_sessions()
            # Start the heartbeat as soon as held clients are rebound.
            # Do NOT await stitch on this CTRL handler -- that blocked the
            # IPC read loop, so typed look/who never reached Session.play
            # until every deferred heal returned (playable-first could not
            # help: DATA frames sat on the gateway). Circle copyover_recover
            # returns to nanny/game_loop; zone resets keep running.
            self._ensure_tick_loop()
            self._welcome_boot_task = asyncio.create_task(
                self._complete_welcome_boot(
                    reload=bool(held or welcome_kind == RELOAD),
                ),
                name="gateway-welcome-boot",
            )
        elif op == "open":
            sid = msg.get("sid")
            if sid:
                peer = msg.get("peer")
                await self._open_session(
                    sid, reattach_name=None, peer_host=peer
                )
        elif op == "close":
            sid = msg.get("sid")
            if sid:
                await self._close_session(sid, client_gone=True)
        elif op == "pong":
            pass
        elif op == "chat_stitch_import":
            from engine import channels

            payload = msg.get("channels") or {}
            mode = str(msg.get("mode") or "").strip().lower()
            if isinstance(payload, dict) and payload:
                count = channels.sync_gateway_stitch_channels(
                    self.game, payload, mode=mode,
                )
                if count:
                    print(
                        f"[gateway_client] synced {count} gateway stitch "
                        f"channel line(s) after copyover ({mode or 'append'})",
                        flush=True,
                    )
                    try:
                        self.game.save()
                    except Exception as exc:
                        print(
                            f"[gateway_client] stitch import save retrying: {exc!r}",
                            flush=True,
                        )
                        try:
                            self.game.save()
                        except Exception as exc2:
                            print(
                                f"[gateway_client] stitch import save failed: {exc2!r}",
                                flush=True,
                            )

    async def _open_session(
        self,
        session_id: str,
        reattach_name: Optional[str],
        peer_host: Optional[str] = None,
        gmcp_supports: Optional[dict] = None,
    ) -> None:
        """Create a Session for a held client (new or reattach)."""
        if session_id in self._sessions:
            return
        reader = GatewaySessionReader(session_id)
        writer = GatewaySessionWriter(
            session_id, self.send_frame, peer_host=peer_host
        )
        self._readers[session_id] = reader
        session = self.session_factory(
            reader, writer, self.game, gateway_session_id=session_id
        )
        session.gateway_bridge = self
        self._sessions[session_id] = session
        # Reattach: skip login if we have a name and the character exists.
        if reattach_name:
            session._gateway_reattach_name = reattach_name
        if isinstance(gmcp_supports, dict) and gmcp_supports:
            session._gateway_reattach_gmcp_supports = dict(gmcp_supports)
        task = asyncio.create_task(
            self._run_session(session_id, session),
            name=f"gateway-session-{session_id[:8]}",
        )
        session._gateway_task = task
        if reattach_name:
            # Session.run() attaches the character synchronously before its
            # first await (play). Yield so that attach lands before welcome
            # starts tick_loop -- otherwise Cadence can yank Cage/jail Echoes.
            await asyncio.sleep(0)

    async def _run_session(self, session_id: str, session) -> None:
        """Run Session.run() (login or reattach); Echo only if client gone."""
        try:
            # Must call run(), not play(): new sessions need the name prompt;
            # reattach is handled inside run() via _gateway_reattach_name.
            await session.run()
        except asyncio.CancelledError:
            # Game process restarting — do not disconnect (no Echo).
            raise
        except Exception as exc:
            # Uncaught failure after attach (e.g. SQLite save) used to leave
            # character.session wired for outbound combat while play() was
            # dead — Mudlet saw fight spam with no score/look/bug replies.
            import traceback

            print(
                f"[gateway_client] session {session_id[:8]}… error: {exc}",
                flush=True,
            )
            traceback.print_exc()
            self._abandon_crashed_session(session_id, session)
        finally:
            # Normal run() exit means logout or client EOF — Session already
            # called disconnect if it logged in. Just drop our maps.
            self._sessions.pop(session_id, None)
            self._readers.pop(session_id, None)

    def _abandon_crashed_session(self, session_id: str, session) -> None:
        """Detach a character left live after session.run() crashed.

        CancelledError (copyover / game restart) must NOT call this — held
        clients reattach without Echo conversion. Any other exception after
        attach needs a real disconnect so combat stops targeting the dead
        Session writer.
        """
        char = getattr(session, "character", None)
        still_bound = (
            char is not None
            and getattr(char, "session", None) is session
        )
        if not still_bound and not getattr(session, "alive", False):
            return
        try:
            # Full disconnect: Echo the body, clear session, kick TCP so the
            # client is not stuck watching outbound-only combat.
            session.disconnect()
        except Exception as cleanup_exc:
            import traceback

            print(
                f"[gateway_client] session {session_id[:8]}… "
                f"crash cleanup failed: {cleanup_exc}",
                flush=True,
            )
            traceback.print_exc()
            # Last resort: clear the pointer so combat send short-circuits.
            force_clear = getattr(session, "force_clear_zombie_attach", None)
            if callable(force_clear):
                force_clear()
            else:
                if char is not None and getattr(char, "session", None) is session:
                    char.session = None
                session.character = None
                session.alive = False

    async def _close_session(self, session_id: str, client_gone: bool) -> None:
        """Gateway reports client TCP closed — feed EOF so play() exits."""
        reader = self._readers.get(session_id)
        if reader is not None:
            reader.feed_eof()
        session = self._sessions.get(session_id)
        if session is not None and client_gone:
            # Real disconnect path: play() will see EOF and disconnect().
            pass
