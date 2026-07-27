"""
gateway.py -- long-lived telnet acceptor that holds client sockets across
game restarts.

Owns public port (default 4000). Forwards each client's bytes to the game
over a framed IPC socket on 127.0.0.1:4001. When the game process dies and
respawns, clients stay connected; the new game reattaches by session id.

While the game IPC is down, held clients get occasional plain-text
"elevator music" (Veil hold lines) so a crash/restart does not feel like
a frozen dead socket. That UX lives here on purpose — the game process
cannot speak when it is dead. After a longer Discord outage grace (default
45s; planned SIGUSR1 reloads suppress entirely), Discord #wknz-radio may
get a one-shot ``[WKNZ] Wits`` apology (and an "we're back" when IPC
returns) via ``engine.discord_bridge`` — env-gated, GM ``discord crash``
mute, fail-soft.

Run:
  python -m engine.gateway
  # or: python engine/gateway.py

Env:
  RIFTFORGE_PORT          -- public telnet port (default 4000)
  RIFTFORGE_GATEWAY_IPC   -- IPC listen host:port (default 127.0.0.1:4001)
  RIFTFORGE_GATEWAY_IPC_ALLOW_NONLOCAL -- set 1 to bind IPC off loopback
    (unsafe on live; passwordless reattach becomes reachable)
  RIFTFORGE_GATEWAY_HOLD_MUSIC -- 0/off/false/no disables hold lines
    (default on)
  RIFTFORGE_GATEWAY_HOLD_GRACE -- seconds after game IPC drop before the
    first hold line (default 5; short restarts stay quiet)
  RIFTFORGE_GATEWAY_HOLD_INTERVAL -- seconds between hold lines while
    the game stays down (default 20)
  RIFTFORGE_GATEWAY_DISCORD_OUTAGE_GRACE -- seconds of game-down before
    WKNZ Discord crash/uncrash posts (default 45; never shorter than
    HOLD_GRACE). Planned SIGUSR1 reloads also send ``planned_restart``
    so Discord stays quiet even on slow boots.
  DISCORD_BRIDGE_WEBHOOK_WKNZ -- optional; outage down/up Discord posts

Stdlib only. No world / combat logic — sockets, framing, hold UX, and
optional Discord outage tells.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import signal
import time
import uuid
from typing import Optional

from engine.gateway_protocol import (
    TYPE_CTRL,
    TYPE_DATA,
    encode_ctrl,
    encode_data,
    parse_ipc_addr,
    read_frame,
    require_loopback_ipc,
)


# Rotating hold lines while the game child is down. Plain text + [WAIT]
# tag (never color alone — gateway has no style palette). Telnet \r\n.
# Keep them short; clients may sit here for a long boot.
HOLD_MUSIC_LINES = (
    b"\r\n*** [WAIT] The Veil hums softly. The world is still "
    b"stitching itself back together. ***\r\n",
    b"\r\n*** [WAIT] Soft static under the floorboards. Hang on -- "
    b"you are still connected. ***\r\n",
    b"\r\n*** [WAIT] Somewhere a distant elevator chime. The bones "
    b"of the world are rewriting. ***\r\n",
    b"\r\n*** [WAIT] The Veil holds your place. Please stand by. ***\r\n",
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


def _env_truthy(name: str, default: bool = True) -> bool:
    """Parse a yes/no env flag; empty → default.

    Accepts common off spellings (0, false, off, no) so compose toggles
    stay forgiving. Anything else counts as on when default is True.
    """
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "off", "no", "n"):
        return False
    if raw in ("1", "true", "on", "yes", "y"):
        return True
    return default


def _hold_grace_seconds() -> int:
    """Seconds after IPC drop before the first Veil hold line (floor 1)."""
    return max(1, _env_int("RIFTFORGE_GATEWAY_HOLD_GRACE", 5))


def _discord_outage_grace_seconds(hold_grace: int | None = None) -> int:
    """Seconds of game-down before WKNZ Discord outage down/up.

    Defaults to 45 so routine game-only restarts (auto-deploy / watcher
    SIGUSR1, often ~7-15s) do not spam #wknz-radio. Never shorter than
    hold grace so Discord cannot fire before clients hear hold music.
    """
    hold = _hold_grace_seconds() if hold_grace is None else max(1, int(hold_grace))
    raw = _env_int("RIFTFORGE_GATEWAY_DISCORD_OUTAGE_GRACE", 45)
    return max(hold, max(1, raw))


def _peer_host_from_writer(writer) -> Optional[str]:
    """Best-effort client IP/host from a public telnet StreamWriter.

    Returns a string like ``1.2.3.4`` (or an IPv6 literal), or None when
    the OS did not report a peer. The game uses this for banlist + head-GM
    staff pings; junior GMs never see it on the ops channel.
    """
    get_info = getattr(writer, "get_extra_info", None)
    if get_info is None:
        return None
    peer = get_info("peername")
    if isinstance(peer, tuple) and peer:
        return str(peer[0])
    if isinstance(peer, str) and peer:
        return peer
    return None


class ClientSlot:
    """One held telnet client and its optional bound character name."""

    def __init__(self, session_id: str, reader, writer, peer: Optional[str] = None):
        self.session_id = session_id
        self.reader = reader
        self.writer = writer
        self.name: Optional[str] = None  # set when game reports bound
        # Real public-socket peer (not the IPC loopback). Forwarded on
        # open / welcome so the game Session can ban + head-GM-notify.
        self.peer: Optional[str] = peer
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
        # Hold-music bookkeeping (monotonic times; None = game is up /
        # no clients / music disabled path not yet started).
        self._hold_down_since: Optional[float] = None
        self._hold_next_at: Optional[float] = None
        self._hold_line_index = 0
        # One Discord down/up pair per outage that lasts past Discord grace.
        self._wknz_outage_announced = False
        # Set by game CTRL ``planned_restart`` (SIGUSR1 / copyover exit) so
        # intentional game-only reloads never hit WKNZ as a "crash".
        self._suppress_discord_outage = False

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

    def _discord_bridge(self):
        """Import + reload ``discord_bridge`` so GM mutes apply in-process.

        The gateway parent is long-lived. A one-shot import at first outage
        can leave mute helpers stuck on old code while ``discord crash off``
        already wrote ``.discord_bridge_toggles``. Reload on each call so
        staff toggles (and bridge fixes) take effect without bouncing
        :4000.
        """
        from engine import discord_bridge

        return importlib.reload(discord_bridge)

    def _discord_outage_down(self) -> None:
        """One-shot Discord apology when Discord outage grace expires.

        Fail-soft. Skips when a planned restart is in flight, when already
        announced, when GM ``discord crash off`` mutes, or when schedule
        returns False (no webhook / cooldown). Only marks announced when
        a post was actually queued -- so a mute cannot strand an "up".
        """
        if self._wknz_outage_announced or self._suppress_discord_outage:
            return
        try:
            bridge = self._discord_bridge()
            if not bridge.is_toggle_on("crash"):
                print(
                    "[gateway] Discord outage muted (discord crash off)",
                    flush=True,
                )
                return
            if bridge.schedule_wknz_outage_down():
                self._wknz_outage_announced = True
        except Exception as exc:
            print(
                f"[discord_bridge] wknz outage_down skipped: {exc}",
                flush=True,
            )

    def _discord_outage_up(self) -> None:
        """Discord 'we're back' after an announced outage. Fail-soft."""
        if not self._wknz_outage_announced:
            return
        self._wknz_outage_announced = False
        try:
            bridge = self._discord_bridge()
            if not bridge.is_toggle_on("crash"):
                return
            bridge.schedule_wknz_outage_up()
        except Exception as exc:
            print(
                f"[discord_bridge] wknz outage_up skipped: {exc}",
                flush=True,
            )

    def _maybe_discord_outage_down(self, now: float, discord_grace: int) -> None:
        """Fire Discord down once downtime exceeds Discord grace."""
        if self._hold_down_since is None:
            return
        if (now - self._hold_down_since) >= discord_grace:
            self._discord_outage_down()

    async def _broadcast_hold_line(self, data: bytes) -> None:
        """Send one hold-music line to every alive held client."""
        # Snapshot keys — send_to_client may drop a dead socket mid-loop.
        for sid in list(self.clients.keys()):
            await self.send_to_client(sid, data)

    async def _hold_music_loop(self) -> None:
        """While the game IPC is down, drip Veil hold lines to clients.

        Short game restarts stay quiet for hold music (HOLD_GRACE). Discord
        WKNZ outage posts use a longer DISCORD_OUTAGE_GRACE (default 45s)
        and are suppressed entirely for ``planned_restart`` IPC. Disable
        hold lines with ``RIFTFORGE_GATEWAY_HOLD_MUSIC=0`` (Discord grace
        still applies).
        """
        hold_grace = _hold_grace_seconds()
        discord_grace = _discord_outage_grace_seconds(hold_grace)
        if not _env_truthy("RIFTFORGE_GATEWAY_HOLD_MUSIC", default=True):
            # Hold lines off -- still watch for Discord outage tells.
            while self._running:
                await asyncio.sleep(0.5)
                game_up = self._game_writer is not None
                if game_up:
                    self._discord_outage_up()
                    self._suppress_discord_outage = False
                    self._hold_down_since = None
                    continue
                now = time.monotonic()
                if self._hold_down_since is None:
                    self._hold_down_since = now
                    continue
                self._maybe_discord_outage_down(now, discord_grace)
            return
        interval = max(1, _env_int("RIFTFORGE_GATEWAY_HOLD_INTERVAL", 20))
        while self._running:
            await asyncio.sleep(0.5)
            game_up = self._game_writer is not None
            has_clients = any(c.alive for c in self.clients.values())
            if game_up:
                # Game back -- Discord "we're back" if we announced down.
                self._discord_outage_up()
                self._suppress_discord_outage = False
                self._hold_down_since = None
                self._hold_next_at = None
                continue
            now = time.monotonic()
            if not has_clients:
                # No telnet holders: still track downtime for Discord so
                # #wknz-radio hears real outages even when nobody is parked.
                if self._hold_down_since is None:
                    self._hold_down_since = now
                    continue
                self._maybe_discord_outage_down(now, discord_grace)
                continue
            if self._hold_down_since is None:
                self._hold_down_since = now
                self._hold_next_at = now + hold_grace
                continue
            # Discord gate is independent of hold-line drip timing.
            self._maybe_discord_outage_down(now, discord_grace)
            if self._hold_next_at is None or now < self._hold_next_at:
                continue
            line = HOLD_MUSIC_LINES[
                self._hold_line_index % len(HOLD_MUSIC_LINES)
            ]
            self._hold_line_index += 1
            await self._broadcast_hold_line(line)
            self._hold_next_at = now + interval

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
        peer = _peer_host_from_writer(writer)
        slot = ClientSlot(session_id, reader, writer, peer=peer)
        self.clients[session_id] = slot
        print(
            f"[gateway] client open sid={session_id[:8]}… "
            f"peer={peer or '-'} ({len(self.clients)} held)",
            flush=True,
        )
        # Tell the game (if up) about the new session + real peer.
        open_msg = {"op": "open", "sid": session_id}
        if peer:
            open_msg["peer"] = peer
        await self.send_to_game(encode_ctrl(open_msg))
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
            # Game is back — stop elevator music for this outage and
            # (if we announced Discord down) post the WKNZ "we're back".
            self._hold_down_since = None
            self._hold_next_at = None
            self._discord_outage_up()
            # Clear planned-restart suppress whether or not Discord fired.
            self._suppress_discord_outage = False

        # Wait for hello, then send welcome snapshot.
        try:
            while True:
                ftype, sid, payload = await read_frame(reader)
                if ftype is None:
                    break
                if ftype == TYPE_CTRL:
                    op = (payload or {}).get("op")
                    if op == "hello":
                        # Include peer so reattach after game restart still
                        # knows the real client IP (banlist / head-GM pings).
                        sessions = []
                        for c in self.clients.values():
                            if not c.alive:
                                continue
                            entry = {"sid": c.session_id, "name": c.name}
                            if c.peer:
                                entry["peer"] = c.peer
                            sessions.append(entry)
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
                    elif op == "planned_restart":
                        # SIGUSR1 / auto-deploy game-only reload -- Veil
                        # hold music still runs for clients, but WKNZ must
                        # not treat this as a crash/uncrash pair.
                        self._suppress_discord_outage = True
                        print(
                            "[gateway] planned restart -- "
                            "Discord outage suppressed",
                            flush=True,
                        )
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
        # Background drip while game is down; cancelled with the gateway.
        hold_task = asyncio.create_task(
            self._hold_music_loop(),
            name="gateway-hold-music",
        )
        try:
            async with telnet_server, ipc_server:
                await asyncio.Future()  # run forever
        finally:
            hold_task.cancel()
            try:
                await hold_task
            except asyncio.CancelledError:
                pass


def main() -> None:
    """Entry point for `python -m engine.gateway`."""
    public_port = _env_int("RIFTFORGE_PORT", 4000)
    ipc_host, ipc_port = parse_ipc_addr()
    # Pen-test M3: never expose passwordless reattach on a public bind.
    require_loopback_ipc(ipc_host, role="gateway")
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
