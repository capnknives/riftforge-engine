"""
gateway_protocol.py -- length-prefixed IPC frames between gateway and game.

Frame layout (all multi-byte integers big-endian):

  uint32 length_of_body
  body = uint8 type + payload

Types:
  TYPE_DATA (0x01) -- 32-byte ASCII session id + raw telnet bytes
  TYPE_CTRL (0x02) -- UTF-8 JSON object

Also owns IPC address helpers: the game↔gateway wire must stay on
loopback so reattach (passwordless resume after game restart) cannot be
spoofed from the public internet (pen-test M3).

Stdlib only. Shared by engine.gateway and engine.gateway_client.
"""

from __future__ import annotations

import json
import os
import struct

TYPE_DATA = 0x01
TYPE_CTRL = 0x02

# Fixed-width session id: uuid4.hex is exactly 32 ASCII chars.
SID_LEN = 32

# Default game↔gateway IPC (never publish this port in Docker/UFW).
DEFAULT_IPC_ADDR = "127.0.0.1:4001"

_HEADER = struct.Struct(">I")  # body length
_TYPE = struct.Struct("B")


def parse_ipc_addr(raw: str | None = None) -> tuple[str, int]:
    """Parse ``host:port`` for the gateway IPC (env or explicit string)."""
    text = (raw if raw is not None else os.environ.get(
        "RIFTFORGE_GATEWAY_IPC", DEFAULT_IPC_ADDR
    )).strip()
    if not text:
        text = DEFAULT_IPC_ADDR
    if ":" in text:
        host, _, port_s = text.rpartition(":")
        return (host or "127.0.0.1"), int(port_s)
    return "127.0.0.1", int(text)


def is_loopback_host(host: str) -> bool:
    """True when host is only reachable on this machine (IPv4/IPv6/localhost)."""
    h = (host or "").strip().lower()
    if not h:
        return False
    if h in ("127.0.0.1", "::1", "localhost"):
        return True
    # 127.0.0.0/8
    if h.startswith("127."):
        return True
    return False


def allow_nonlocal_ipc() -> bool:
    """Escape hatch for rare lab setups (default off — do not use on live)."""
    return os.environ.get(
        "RIFTFORGE_GATEWAY_IPC_ALLOW_NONLOCAL", ""
    ).strip() in ("1", "true", "True", "yes", "YES")


def require_loopback_ipc(host: str, *, role: str = "gateway") -> None:
    """Refuse a non-loopback IPC host unless the escape hatch is set.

    Reattach skips the password prompt by design; that is only safe while
    the IPC cannot be reached from the public network.
    """
    if is_loopback_host(host):
        return
    if allow_nonlocal_ipc():
        print(
            f"[{role}] WARNING: IPC host {host!r} is not loopback "
            f"(RIFTFORGE_GATEWAY_IPC_ALLOW_NONLOCAL=1). "
            f"Passwordless reattach is exposed if this port is reachable.",
            flush=True,
        )
        return
    raise SystemExit(
        f"[{role}] Refusing non-loopback gateway IPC host {host!r}. "
        f"Use 127.0.0.1 (default) so reattach cannot be spoofed from the "
        f"internet. Override only with "
        f"RIFTFORGE_GATEWAY_IPC_ALLOW_NONLOCAL=1 (unsafe on live)."
    )


def encode_data(session_id: str, payload: bytes) -> bytes:
    """Build a DATA frame for one session's telnet bytes."""
    sid = (session_id or "").encode("ascii")
    if len(sid) != SID_LEN:
        raise ValueError(f"session_id must be {SID_LEN} ascii chars, got {len(sid)}")
    body = _TYPE.pack(TYPE_DATA) + sid + (payload or b"")
    return _HEADER.pack(len(body)) + body


def encode_ctrl(obj: dict) -> bytes:
    """Build a CTRL frame from a JSON-serializable dict."""
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    body = _TYPE.pack(TYPE_CTRL) + raw
    return _HEADER.pack(len(body)) + body


async def read_frame(reader):
    """Read one frame from an asyncio StreamReader.

    Returns (TYPE_DATA, session_id, payload_bytes) or
    (TYPE_CTRL, None, dict) or (None, None, None) on EOF.
    """
    header = await reader.readexactly(4)
    if not header:
        return None, None, None
    (body_len,) = _HEADER.unpack(header)
    if body_len < 1 or body_len > 8_000_000:
        raise ValueError(f"invalid frame length {body_len}")
    body = await reader.readexactly(body_len)
    frame_type = body[0]
    rest = body[1:]
    if frame_type == TYPE_DATA:
        if len(rest) < SID_LEN:
            raise ValueError("DATA frame too short for session id")
        sid = rest[:SID_LEN].decode("ascii")
        return TYPE_DATA, sid, rest[SID_LEN:]
    if frame_type == TYPE_CTRL:
        return TYPE_CTRL, None, json.loads(rest.decode("utf-8"))
    raise ValueError(f"unknown frame type {frame_type}")
