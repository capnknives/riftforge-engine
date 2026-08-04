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
  RIFTFORGE_GATEWAY_STITCH_STALE -- seconds without game→client DATA
    before the gateway handles ``ooc`` / ``wiznet`` / ``gm recover restart``
    / ``gm recover clearhold`` / ``gm recover revert`` / ``gm recover restoredb``
    locally.
    locally (default 12; covers hung game with IPC still up)
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
from collections import deque
from typing import Optional

from engine import ooc_channel
from engine import watcher_request
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


def _stitch_stale_seconds() -> float:
    """No game output for this long → gateway stitch mode (OOC/wiznet + GM restart)."""
    return max(3.0, float(_env_int("RIFTFORGE_GATEWAY_STITCH_STALE", 12)))


def _pop_complete_lines(buf: bytes) -> tuple[list[bytes], bytes]:
    """Split telnet input on newlines; return (lines, remainder)."""
    lines: list[bytes] = []
    while True:
        idx = buf.find(b"\n")
        if idx < 0:
            break
        line = buf[:idx]
        buf = buf[idx + 1 :]
        if line.endswith(b"\r"):
            line = line[:-1]
        lines.append(line)
    return lines, buf


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
        self.ooc_face: Optional[str] = None  # account or character label for OOC
        self.head_gm: bool = False  # head GM may queue restart from gateway
        self.staff_gm: bool = False  # staff may use wiznet while game is down
        # Real public-socket peer (not the IPC loopback). Forwarded on
        # open / welcome so the game Session can ban + head-GM-notify.
        self.peer: Optional[str] = peer
        self.alive = True
        self.line_buffer = b""  # stitch-mode line assembly


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
        self._hold_down_since_wall: Optional[float] = None
        self._hold_next_at: Optional[float] = None
        self._hold_line_index = 0
        # One Discord down/up pair per outage that lasts past Discord grace.
        self._wknz_outage_announced = False
        # Set by game CTRL ``planned_restart`` (SIGUSR1 / copyover exit) so
        # intentional game-only reloads never hit WKNZ as a "crash".
        self._suppress_discord_outage = False
        # Gateway stitch mirrors for global channels (plain text, no color).
        # Authoritative rings live on the game — see engine/channel_history.py.
        from engine import channel_history

        self._stitch_histories: dict[str, deque[str]] = {}
        for spec in channel_history.gateway_stitch_channels():
            self._stitch_histories[spec.name] = deque(maxlen=spec.ring_max)
        # Last time any game→client DATA frame was forwarded (stitch detect).
        self._last_game_data_at = time.monotonic()

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

    def _maybe_crash_recovery_outage(self) -> None:
        """Tell the watcher when game IPC has been down past the crash window."""
        if self._hold_down_since_wall is None:
            return
        if self._suppress_discord_outage:
            return
        try:
            from engine import crash_recovery

            if (time.time() - self._hold_down_since_wall) >= (
                crash_recovery.crash_window_seconds()
            ):
                crash_recovery.write_gateway_outage(
                    down_since_wall=self._hold_down_since_wall,
                    planned_restart=False,
                )
        except Exception as exc:
            print(
                f"[gateway] crash_recovery outage file skipped: {exc!r}",
                flush=True,
            )

    def _mark_game_ipc_down(self) -> None:
        """Start tracking downtime when IPC drops."""
        if self._hold_down_since is None:
            self._hold_down_since = time.monotonic()
        if self._hold_down_since_wall is None:
            self._hold_down_since_wall = time.time()

    def _clear_game_ipc_down(self) -> None:
        """Clear downtime tracking when IPC returns."""
        self._hold_down_since = None
        self._hold_down_since_wall = None
        self._hold_next_at = None
        try:
            from engine import crash_recovery

            crash_recovery.clear_gateway_outage()
        except Exception:
            pass

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
                    self._clear_game_ipc_down()
                    continue
                self._mark_game_ipc_down()
                now = time.monotonic()
                self._maybe_crash_recovery_outage()
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
                self._clear_game_ipc_down()
                continue
            self._mark_game_ipc_down()
            now = time.monotonic()
            self._maybe_crash_recovery_outage()
            if not has_clients:
                # No telnet holders: still track downtime for Discord so
                # #wknz-radio hears real outages even when nobody is parked.
                self._maybe_discord_outage_down(now, discord_grace)
                continue
            if self._hold_next_at is None:
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

    def _should_intercept_commands(self) -> bool:
        """True when the gateway should handle OOC / GM restart locally."""
        if self._game_writer is None:
            return True
        stale = time.monotonic() - self._last_game_data_at
        return stale >= _stitch_stale_seconds()

    def _note_game_data(self) -> None:
        """Refresh stitch timer when the game forwards bytes to a client."""
        self._last_game_data_at = time.monotonic()

    def _slot_ooc_face(self, slot: ClientSlot) -> str:
        """Best label for gateway OOC while the game is unreachable."""
        if slot.ooc_face:
            return slot.ooc_face
        if slot.name:
            return slot.name
        return "???"

    async def _send_plain(self, session_id: str, text: str) -> None:
        await self.send_to_client(session_id, (text + "\r\n").encode("utf-8"))

    async def _broadcast_plain(self, text: str) -> None:
        payload = (text + "\r\n").encode("utf-8")
        for sid in list(self.clients.keys()):
            await self.send_to_client(sid, payload)

    def _slot_wiznet_face(self, slot: ClientSlot) -> str:
        """Staff label for gateway wiznet while the game is unreachable."""
        face = self._slot_ooc_face(slot)
        if face.endswith("(GM)"):
            return face
        return f"{face}(GM)"

    def _merge_chat_history(self, snapshot: dict[str, list]) -> None:
        """Merge game-owned channel rings into gateway stitch buffers."""
        for name, lines in (snapshot or {}).items():
            buf = self._stitch_histories.get(name)
            if buf is None:
                continue
            for line in lines or []:
                if isinstance(line, str) and line.strip():
                    buf.append(line.strip())

    def _merge_chat_history_payload(self, payload: dict) -> None:
        """Accept ``channels`` dict or legacy per-channel keys on *payload*."""
        from engine import channel_history

        channels = (payload or {}).get("channels")
        if isinstance(channels, dict):
            self._merge_chat_history(channels)
            return
        snapshot: dict[str, list] = {}
        for spec in channel_history.gateway_stitch_channels():
            raw = (payload or {}).get(spec.name)
            if raw:
                snapshot[spec.name] = raw
        self._merge_chat_history(snapshot)

    def _append_gateway_chat_line(self, channel: str, line: str) -> None:
        """Record one plain chat line from the game (live mirror)."""
        text = (line or "").strip()
        if not text:
            return
        buf = self._stitch_histories.get(channel)
        if buf is not None:
            buf.append(text)

    def _channel_message_text(self, text: str, verb: str) -> Optional[str]:
        """Return speak body, or ``None`` when *text* is the bare verb."""
        stripped = (text or "").strip()
        lower = stripped.lower()
        if lower == verb:
            return None
        prefix = f"{verb} "
        if lower.startswith(prefix):
            return stripped[len(prefix) :].strip()
        return None

    async def _replay_gateway_channel(
        self, slot: ClientSlot, channel_name: str,
    ) -> None:
        from engine import channel_history

        spec = channel_history.get_channel(channel_name)
        buf = self._stitch_histories.get(channel_name)
        if spec is None:
            return
        if not buf:
            await self._send_plain(slot.session_id, spec.empty_message)
            return
        await self._send_plain(slot.session_id, spec.replay_header)
        for entry in buf:
            await self._send_plain(slot.session_id, entry)
        await self._send_plain(slot.session_id, "")

    async def _relay_gateway_wiznet(self, slot: ClientSlot, message: str) -> None:
        """Broadcast one wiznet line from the gateway while stitch mode is on."""
        plain = f"[WIZ] {self._slot_wiznet_face(slot)}: {message}"
        self._append_gateway_chat_line("wiznet", plain)
        for sid, peer in list(self.clients.items()):
            if not peer.staff_gm:
                continue
            await self._send_plain(sid, plain)
            await self._send_plain(sid, "")

    async def _relay_gateway_ooc(self, slot: ClientSlot, message: str) -> None:
        """Broadcast one OOC line from the gateway while stitch mode is on."""
        face = self._slot_ooc_face(slot)
        plain = ooc_channel.format_ooc_line(face, message)
        self._append_gateway_chat_line("ooc", plain)
        await self._broadcast_plain(plain)
        await self._broadcast_plain("")
        try:
            bridge = self._discord_bridge()
            bridge.schedule_ooc(plain)
        except Exception as exc:
            print(f"[gateway] ooc discord mirror skipped: {exc!r}", flush=True)

    async def _relay_gateway_channel(
        self, slot: ClientSlot, channel_name: str, message: str,
    ) -> None:
        if channel_name == "ooc":
            await self._relay_gateway_ooc(slot, message)
        elif channel_name == "wiznet":
            await self._relay_gateway_wiznet(slot, message)

    async def _handle_intercepted_line(self, slot: ClientSlot, line: bytes) -> bool:
        """Handle stitch-mode commands. Return True when consumed."""
        try:
            text = line.decode("utf-8", errors="replace").strip()
        except Exception:
            return False
        if not text:
            return True
        lower = text.lower()
        from engine import channel_history

        channel_name = channel_history.stitch_channel_for_command(text)
        if channel_name:
            # Game IPC up: forward to authoritative game rings.
            if self._game_writer is not None:
                return False
            spec = channel_history.get_channel(channel_name)
            if spec is None:
                return False
            if spec.staff_only and not slot.staff_gm:
                await self._send_plain(slot.session_id, "You aren't a GM.")
                return True
            body = self._channel_message_text(text, spec.verb)
            if body is None:
                await self._replay_gateway_channel(slot, channel_name)
                return True
            if not body:
                await self._send_plain(slot.session_id, spec.usage_message)
                return True
            await self._relay_gateway_channel(slot, channel_name, body)
            return True
        if lower.startswith("gm recover restart") or lower.startswith(
            "recover restart"
        ):
            if not slot.head_gm:
                await self._send_plain(
                    slot.session_id,
                    "Only the head GM can force a game restart from here.",
                )
                return True
            backup = "backup" in lower.split() or "save" in lower.split()
            if watcher_request.queue_restart_game(
                by=self._slot_ooc_face(slot),
                backup=backup,
            ):
                if backup:
                    tip = (
                        "Restart queued — backup + game respawn. "
                    )
                else:
                    tip = "Restart queued — game respawn (no backup). "
                await self._send_plain(
                    slot.session_id,
                    f"*** [ALERT] {tip}"
                    "Hold on; OOC still works until the Veil settles. ***",
                )
            else:
                await self._send_plain(
                    slot.session_id,
                    "*** [ALERT] Could not queue restart (watcher request file). ***",
                )
            return True
        if lower in ("gm recover revert", "recover revert"):
            if not slot.head_gm:
                await self._send_plain(
                    slot.session_id,
                    "Only the head GM can revert code to the last stable SHA.",
                )
                return True
            if watcher_request.queue_revert_stable(
                by=self._slot_ooc_face(slot),
            ):
                await self._send_plain(
                    slot.session_id,
                    "*** [ALERT] Revert queued — code rolls back to last stable "
                    "SHA (no backup), then game respawn. Auto-deploy held until "
                    "gm recover clearhold. ***",
                )
            else:
                await self._send_plain(
                    slot.session_id,
                    "*** [ALERT] Could not queue revert (watcher request file). ***",
                )
            return True
        if lower.startswith("gm recover restoredb") or lower.startswith(
            "recover restoredb"
        ):
            if not slot.head_gm:
                await self._send_plain(
                    slot.session_id,
                    "Only the head GM can restore riftforge.db from backups.",
                )
                return True
            parts = text.split()
            date = ""
            if len(parts) >= 3:
                date = parts[2].strip()
            if watcher_request.queue_restore_db(
                date=date,
                by=self._slot_ooc_face(slot),
            ):
                when = date or "latest restorable nightly backup"
                await self._send_plain(
                    slot.session_id,
                    "*** [ALERT] DB restore queued — copies "
                    f"backups/{when}/riftforge.db over the live file "
                    "(corrupt copy quarantined), then game respawn. "
                    "Gateway stays up. ***",
                )
            else:
                await self._send_plain(
                    slot.session_id,
                    "*** [ALERT] Could not queue restoredb "
                    "(watcher request file). ***",
                )
            return True
        if lower in (
            "gm recover clearhold",
            "recover clearhold",
            "gm recover clear",
            "recover clear",
        ):
            if not slot.head_gm:
                await self._send_plain(
                    slot.session_id,
                    "Only the head GM can clear the crash revert hold.",
                )
                return True
            if watcher_request.queue_clear_revert_hold(
                by=self._slot_ooc_face(slot),
            ):
                await self._send_plain(
                    slot.session_id,
                    "*** [ALERT] Hold cleared — auto-deploy catch-up queued. "
                    "Merged fixes on origin/main should sync on the next poll. ***",
                )
            else:
                await self._send_plain(
                    slot.session_id,
                    "*** [ALERT] Could not queue clearhold (watcher request file). ***",
                )
            return True
        if self._game_writer is None:
            wait = (
                "*** [WAIT] OOC and wiznet work while the game restarts; "
                "other commands need the Veil to settle. ***"
                if slot.staff_gm
                else "*** [WAIT] OOC works while the game restarts; other "
                "commands need the Veil to settle. ***"
            )
            await self._send_plain(slot.session_id, wait)
            return True
        return False

    async def _forward_client_line(self, session_id: str, line: bytes) -> None:
        """Send one assembled line to the game child."""
        payload = line + b"\r\n"
        await self.send_to_game(encode_data(session_id, payload))

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
                if self._should_intercept_commands():
                    slot.line_buffer += data
                    lines, slot.line_buffer = _pop_complete_lines(slot.line_buffer)
                    for line in lines:
                        if await self._handle_intercepted_line(slot, line):
                            continue
                        if self._game_writer is not None:
                            await self._forward_client_line(session_id, line)
                else:
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
            self._note_game_data()
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
                            s.ooc_face = (payload or {}).get("ooc_face") or s.name
                            s.head_gm = bool((payload or {}).get("head_gm"))
                            s.staff_gm = bool((payload or {}).get("staff_gm"))
                    elif op == "unbound":
                        s = self.clients.get((payload or {}).get("sid", ""))
                        if s is not None:
                            s.name = None
                            s.ooc_face = None
                            s.head_gm = False
                            s.staff_gm = False
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
                    elif op == "chat_history":
                        self._merge_chat_history_payload(payload or {})
                    elif op == "chat_append":
                        self._append_gateway_chat_line(
                            str((payload or {}).get("channel") or ""),
                            str((payload or {}).get("line") or ""),
                        )
                    elif op == "ping":
                        await self.send_to_game(encode_ctrl({"op": "pong"}))
                elif ftype == TYPE_DATA and sid:
                    # Game → client telnet bytes.
                    self._note_game_data()
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
