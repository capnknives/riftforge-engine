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
from typing import Callable, Optional

from engine.gateway_protocol import (
    TYPE_CTRL,
    TYPE_DATA,
    encode_ctrl,
    encode_data,
    read_frame,
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
    """Host/port of the gateway IPC listener."""
    raw = os.environ.get("RIFTFORGE_GATEWAY_IPC", "127.0.0.1:4001").strip()
    if ":" in raw:
        host, _, port_s = raw.rpartition(":")
        return host or "127.0.0.1", int(port_s)
    return "127.0.0.1", int(raw)


class GatewaySessionWriter:
    """asyncio-like StreamWriter that sends DATA frames for one session.

    Session.play() expects writer.write / drain / close / is_closing /
    get_extra_info / wait_closed — we implement the subset used by
    engine.connection.Session.
    """

    def __init__(self, session_id: str, send_fn: Callable):
        self.session_id = session_id
        self._send = send_fn  # async callable(bytes) -> None
        self._closing = False
        self._closed = asyncio.Event()
        self._closed.set()  # start "open" wait_closed semantics: not closed yet
        self._closed.clear()

    def write(self, data: bytes) -> None:
        """Queue telnet bytes to the gateway (scheduled on the running loop)."""
        if self._closing or not data:
            return
        # Session.write is sync; schedule the async send.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._send(encode_data(self.session_id, data)))

    async def drain(self) -> None:
        """No local buffer — gateway send already awaited in send_fn path.

        write() fire-and-forgets tasks; drain is a no-op best-effort yield
        so callers keep their await drain() pattern.
        """
        await asyncio.sleep(0)

    def close(self) -> None:
        """Mark closed; gateway still holds the real socket until client hangs up."""
        self._closing = True
        self._closed.set()

    def is_closing(self) -> bool:
        return self._closing

    async def wait_closed(self) -> None:
        await self._closed.wait()

    def get_extra_info(self, name: str, default=None):
        """Peer address is the gateway, not the real client — report stub."""
        if name == "peername":
            return ("gateway", 0)
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

    async def send_frame(self, frame: bytes) -> None:
        """Write one framed message to the gateway."""
        async with self._send_lock:
            if self.writer is None:
                return
            self.writer.write(frame)
            await self.writer.drain()

    async def notify_bound(self, session_id: str, name: str) -> None:
        """Tell the gateway this sid is logged in as name (for reattach)."""
        await self.send_frame(
            encode_ctrl({"op": "bound", "sid": session_id, "name": name})
        )

    async def notify_unbound(self, session_id: str) -> None:
        """Clear the bound name (logout / mid-login reset)."""
        await self.send_frame(encode_ctrl({"op": "unbound", "sid": session_id}))

    async def kick_client(self, session_id: str) -> None:
        """Ask the gateway to close the public TCP for this session (quit)."""
        await self.send_frame(encode_ctrl({"op": "kick", "sid": session_id}))

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

        try:
            while True:
                ftype, sid, payload = await read_frame(self.reader)
                if ftype is None:
                    break
                if ftype == TYPE_CTRL:
                    await self._on_ctrl(payload or {})
                elif ftype == TYPE_DATA and sid:
                    r = self._readers.get(sid)
                    if r is not None:
                        r.feed(payload or b"")
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            print(f"[gateway_client] IPC ended: {exc}", flush=True)
        finally:
            # Do not disconnect characters — gateway still holds sockets.
            # Cancel play tasks only; leave Echo conversion to real client close.
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
            for entry in msg.get("sessions") or []:
                sid = entry.get("sid")
                if not sid:
                    continue
                name = entry.get("name")
                await self._open_session(sid, reattach_name=name)
        elif op == "open":
            sid = msg.get("sid")
            if sid:
                await self._open_session(sid, reattach_name=None)
        elif op == "close":
            sid = msg.get("sid")
            if sid:
                await self._close_session(sid, client_gone=True)
        elif op == "pong":
            pass

    async def _open_session(
        self, session_id: str, reattach_name: Optional[str]
    ) -> None:
        """Create a Session for a held client (new or reattach)."""
        if session_id in self._sessions:
            return
        reader = GatewaySessionReader(session_id)
        writer = GatewaySessionWriter(session_id, self.send_frame)
        self._readers[session_id] = reader
        session = self.session_factory(
            reader, writer, self.game, gateway_session_id=session_id
        )
        session.gateway_bridge = self
        self._sessions[session_id] = session
        # Reattach: skip login if we have a name and the character exists.
        if reattach_name:
            session._gateway_reattach_name = reattach_name
        task = asyncio.create_task(
            self._run_session(session_id, session),
            name=f"gateway-session-{session_id[:8]}",
        )
        session._gateway_task = task

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
            print(f"[gateway_client] session {session_id[:8]}… error: {exc}", flush=True)
        finally:
            # Normal run() exit means logout or client EOF — Session already
            # called disconnect if it logged in. Just drop our maps.
            self._sessions.pop(session_id, None)
            self._readers.pop(session_id, None)

    async def _close_session(self, session_id: str, client_gone: bool) -> None:
        """Gateway reports client TCP closed — feed EOF so play() exits."""
        reader = self._readers.get(session_id)
        if reader is not None:
            reader.feed_eof()
        session = self._sessions.get(session_id)
        if session is not None and client_gone:
            # Real disconnect path: play() will see EOF and disconnect().
            pass
