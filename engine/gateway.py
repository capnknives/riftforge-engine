"""
gateway.py -- long-lived telnet acceptor that holds client sockets across
game restarts.

Owns public port (default 4000). Forwards each client's bytes to the game
over a framed IPC socket on 127.0.0.1:4001. When the game process dies and
respawns, clients stay connected; the new game reattaches by session id.

Run:
  python -m engine.gateway
  # or: python engine/gateway.py

Env:
  RIFTFORGE_PORT          -- public telnet port (default 4000)
  RIFTFORGE_GATEWAY_IPC   -- IPC listen host:port (default 127.0.0.1:4001)

Stdlib only. No game logic here — only sockets and framing.
"""

from __future__ import annotations

import asyncio
import os
import signal
import uuid
from typing import Optional

from engine.gateway_protocol import (
    TYPE_CTRL,
    TYPE_DATA,
    encode_ctrl,
    encode_data,
    read_frame,
)


def _env_int(name: str, default: int) -> int:
    """Parse an int from the environment, or return default."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_ipc_addr() -> tuple[str, int]:
    """Return (host, port) for the game IPC listener."""
    raw = os.environ.get("RIFTFORGE_GATEWAY_IPC", "127.0.0.1:4001").strip()
    if ":" in raw:
        host, _, port_s = raw.rpartition(":")
        return host or "127.0.0.1", int(port_s)
    return "127.0.0.1", int(raw)


class ClientSlot:
    """One held telnet client and its optional bound character name."""

    def __init__(self, session_id: str, reader, writer):
        self.session_id = session_id
        self.reader = reader
        self.writer = writer
        self.name: Optional[str] = None  # set when game reports bound
        self.alive = True


class Gateway:
    """Accept telnet clients; bridge them to one game IPC connection."""

    def __init__(self, public_port: int, ipc_host: str, ipc_port: int):
        self.public_port = public_port
        self.ipc_host = ipc_host
        self.ipc_port = ipc_port
        self.clients: dict[str, ClientSlot] = {}
        self._game_writer = None  # asyncio StreamWriter to game, or None
        self._game_lock = asyncio.Lock()
        self._running = True

    async def send_to_game(self, frame: bytes) -> None:
        """Write one framed message to the connected game, if any."""
        async with self._game_lock:
            w = self._game_writer
            if w is None:
                return
            try:
                w.write(frame)
                await w.drain()
            except (ConnectionError, OSError, asyncio.IncompleteReadError):
                self._game_writer = None

    async def send_to_client(self, session_id: str, data: bytes) -> None:
        """Forward game bytes to one held telnet client."""
        slot = self.clients.get(session_id)
        if slot is None or not slot.alive:
            return
        try:
            slot.writer.write(data)
            await slot.writer.drain()
        except (ConnectionError, OSError):
            await self._drop_client(session_id, notify_game=True)

    async def _drop_client(self, session_id: str, notify_game: bool) -> None:
        """Remove a client slot and optionally tell the game it closed."""
        slot = self.clients.pop(session_id, None)
        if slot is None:
            return
        slot.alive = False
        try:
            slot.writer.close()
            await slot.writer.wait_closed()
        except Exception:
            pass
        if notify_game:
            await self.send_to_game(encode_ctrl({"op": "close", "sid": session_id}))

    async def handle_telnet(self, reader, writer) -> None:
        """Accept one public telnet connection and hold it until EOF."""
        session_id = uuid.uuid4().hex  # 32 ascii chars
        slot = ClientSlot(session_id, reader, writer)
        self.clients[session_id] = slot
        print(f"[gateway] client open sid={session_id[:8]}… "
              f"({len(self.clients)} held)", flush=True)
        # Tell the game (if up) about the new session.
        await self.send_to_game(encode_ctrl({"op": "open", "sid": session_id}))
        try:
            while self._running and slot.alive:
                data = await reader.read(4096)
                if not data:
                    break
                await self.send_to_game(encode_data(session_id, data))
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            await self._drop_client(session_id, notify_game=True)
            print(f"[gateway] client closed sid={session_id[:8]}… "
                  f"({len(self.clients)} held)", flush=True)

    async def handle_game_ipc(self, reader, writer) -> None:
        """One game process connected to the IPC port.

        Only one game at a time: a new hello replaces the previous writer.
        On connect, send welcome with all held sessions so the game can reattach.
        """
        peer = writer.get_extra_info("peername")
        print(f"[gateway] game IPC connected from {peer}", flush=True)
        async with self._game_lock:
            # Drop a previous game writer without closing clients.
            old = self._game_writer
            self._game_writer = writer
            if old is not None and old is not writer:
                try:
                    old.close()
                except Exception:
                    pass

        # Wait for hello, then send welcome snapshot.
        try:
            while True:
                ftype, sid, payload = await read_frame(reader)
                if ftype is None:
                    break
                if ftype == TYPE_CTRL:
                    op = (payload or {}).get("op")
                    if op == "hello":
                        sessions = [
                            {"sid": c.session_id, "name": c.name}
                            for c in self.clients.values()
                            if c.alive
                        ]
                        await self.send_to_game(
                            encode_ctrl({"op": "welcome", "sessions": sessions})
                        )
                    elif op == "bound":
                        # Game finished login for this sid.
                        s = self.clients.get((payload or {}).get("sid", ""))
                        if s is not None:
                            s.name = (payload or {}).get("name") or None
                    elif op == "unbound":
                        s = self.clients.get((payload or {}).get("sid", ""))
                        if s is not None:
                            s.name = None
                    elif op == "kick":
                        # Intentional quit / takeover — drop the real TCP.
                        sid = (payload or {}).get("sid", "")
                        if sid:
                            await self._drop_client(sid, notify_game=False)
                    elif op == "ping":
                        await self.send_to_game(encode_ctrl({"op": "pong"}))
                elif ftype == TYPE_DATA and sid:
                    # Game → client telnet bytes.
                    await self.send_to_client(sid, payload or b"")
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError) as exc:
            print(f"[gateway] game IPC ended: {exc}", flush=True)
        finally:
            async with self._game_lock:
                if self._game_writer is writer:
                    self._game_writer = None
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            print("[gateway] game IPC disconnected (clients held)", flush=True)

    async def run(self) -> None:
        """Start public + IPC servers and run until cancelled."""
        telnet_server = await asyncio.start_server(
            self.handle_telnet, "0.0.0.0", self.public_port
        )
        ipc_server = await asyncio.start_server(
            self.handle_game_ipc, self.ipc_host, self.ipc_port
        )
        print(
            f"[gateway] listening telnet=:{self.public_port} "
            f"ipc={self.ipc_host}:{self.ipc_port}",
            flush=True,
        )
        async with telnet_server, ipc_server:
            await asyncio.Future()  # run forever


def main() -> None:
    """Entry point for `python -m engine.gateway`."""
    public_port = _env_int("RIFTFORGE_PORT", 4000)
    ipc_host, ipc_port = _parse_ipc_addr()
    gw = Gateway(public_port, ipc_host, ipc_port)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _stop(*_args):
        print("[gateway] shutting down…", flush=True)
        gw._running = False
        for task in asyncio.all_tasks(loop):
            task.cancel()

    if hasattr(signal, "SIGTERM"):
        try:
            loop.add_signal_handler(signal.SIGTERM, _stop)
        except NotImplementedError:
            pass
    if hasattr(signal, "SIGINT"):
        try:
            loop.add_signal_handler(signal.SIGINT, _stop)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(gw.run())
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
