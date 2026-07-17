"""
gateway_protocol.py -- length-prefixed IPC frames between gateway and game.

Frame layout (all multi-byte integers big-endian):

  uint32 length_of_body
  body = uint8 type + payload

Types:
  TYPE_DATA (0x01) -- 32-byte ASCII session id + raw telnet bytes
  TYPE_CTRL (0x02) -- UTF-8 JSON object

Stdlib only. Shared by engine.gateway and engine.gateway_client.
"""

from __future__ import annotations

import json
import struct

TYPE_DATA = 0x01
TYPE_CTRL = 0x02

# Fixed-width session id: uuid4.hex is exactly 32 ASCII chars.
SID_LEN = 32

_HEADER = struct.Struct(">I")  # body length
_TYPE = struct.Struct("B")


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
