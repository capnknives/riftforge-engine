"""
websocket.py -- minimal RFC 6455 server helpers (stdlib only).

Hand-rolled HTTP upgrade + text-frame read/write for browser clients.
No third-party ``websockets`` package (hard rule 1). Gateway-terminated:
client sockets survive game-child restart like telnet today.

MVP scope: text frames only, no permessage-deflate, no TLS, no fragmentation
reassembly beyond a single frame per message (browsers do not fragment normal
``send()`` calls; our server never splits outbound lines across frames).
"""

from __future__ import annotations

import base64
import hashlib
import struct

# RFC 6455 fixed GUID concatenated with Sec-WebSocket-Key for Accept.
_WS_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Opcodes we handle.
_OPCODE_CONTINUATION = 0x0
_OPCODE_TEXT = 0x1
_OPCODE_BINARY = 0x2
_OPCODE_CLOSE = 0x8
_OPCODE_PING = 0x9
_OPCODE_PONG = 0xA

# Defensive caps for internet-facing parsers (browser_websocket_client.md).
MAX_HANDSHAKE_BYTES = 8192
MAX_FRAME_PAYLOAD = 65536


def compute_accept_key(sec_websocket_key: str) -> str:
    """Return Sec-WebSocket-Accept for a client key (RFC 6455 §4.2.2)."""
    digest = hashlib.sha1(sec_websocket_key.strip().encode("ascii") + _WS_MAGIC)
    return base64.b64encode(digest.digest()).decode("ascii")


def parse_handshake_request(raw: bytes):
    """Parse an HTTP GET upgrade request.

    Returns (sec_websocket_key, response_bytes) on success, or (None, None)
    when the buffer is incomplete, and (None, error_response_bytes) on a
    hard reject (bad method, missing key, oversize headers).
    """
    if len(raw) > MAX_HANDSHAKE_BYTES:
        return None, _http_response(400, "Handshake too large")
    if b"\r\n\r\n" not in raw:
        return None, None  # need more bytes
    head, _body = raw.split(b"\r\n\r\n", 1)
    try:
        text = head.decode("latin-1")
    except UnicodeDecodeError:
        return None, _http_response(400, "Bad request")
    lines = text.split("\r\n")
    if not lines or not lines[0].upper().startswith("GET "):
        return None, _http_response(400, "Expected GET")
    headers = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    key = headers.get("sec-websocket-key")
    if not key:
        return None, _http_response(400, "Missing Sec-WebSocket-Key")
    upgrade = headers.get("upgrade", "").lower()
    connection = headers.get("connection", "").lower()
    if "websocket" not in upgrade or "upgrade" not in connection:
        return None, _http_response(400, "Not a WebSocket upgrade")
    accept = compute_accept_key(key)
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    return key, response.encode("ascii")


def _http_response(code: int, reason: str) -> bytes:
    """Tiny plain HTTP error for rejected handshakes."""
    body = reason.encode("utf-8", errors="replace")
    return (
        f"HTTP/1.1 {code} {reason}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii") + body


def encode_text_frame(text: str) -> bytes:
    """Build one unmasked server→client text frame (FIN + opcode 0x1)."""
    payload = text.encode("utf-8")
    if len(payload) > MAX_FRAME_PAYLOAD:
        payload = payload[:MAX_FRAME_PAYLOAD]
    return _encode_frame(payload, opcode=_OPCODE_TEXT, fin=True)


def encode_bytes_frame(data: bytes) -> bytes:
    """Wrap raw bytes as one server→client binary frame (rare; tests)."""
    if len(data) > MAX_FRAME_PAYLOAD:
        data = data[:MAX_FRAME_PAYLOAD]
    return _encode_frame(data, opcode=_OPCODE_BINARY, fin=True)


def _encode_frame(payload: bytes, *, opcode: int, fin: bool) -> bytes:
    """RFC 6455 frame encoder (server frames are never masked)."""
    first = (0x80 if fin else 0x00) | (opcode & 0x0F)
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", first, length)
    elif length < (1 << 16):
        header = struct.pack("!BBH", first, 126, length)
    else:
        header = struct.pack("!BBQ", first, 127, length)
    return header + payload


def decode_client_frames(buf: bytes):
    """Parse client→server frames from ``buf``.

    Returns (application_bytes, remainder, close_requested):
      - application_bytes: concatenated text/binary payloads (UTF-8 text only
        for MVP -- binary is passed through as raw bytes).
      - remainder: bytes after the last fully parsed frame.
      - close_requested: True when a CLOSE opcode was seen.
    """
    out = bytearray()
    i = 0
    n = len(buf)
    close_requested = False
    while i < n:
        if i + 2 > n:
            break
        b0 = buf[i]
        b1 = buf[i + 1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        i += 2
        if length == 126:
            if i + 2 > n:
                i -= 2
                break
            length = struct.unpack("!H", buf[i : i + 2])[0]
            i += 2
        elif length == 127:
            if i + 8 > n:
                i -= 2
                break
            length = struct.unpack("!Q", buf[i : i + 8])[0]
            i += 8
        if length > MAX_FRAME_PAYLOAD:
            # Drop the connection-friendly signal by returning close.
            return bytes(out), buf[i:], True
        if not masked:
            # Client frames must be masked (RFC 6455 §5.1).
            return bytes(out), buf[i:], True
        if i + 4 + length > n:
            i -= 2
            if length >= 126:
                i -= 6 if length < (1 << 16) else 8
            break
        mask_key = buf[i : i + 4]
        i += 4
        payload = bytearray(buf[i : i + length])
        i += length
        for j in range(len(payload)):
            payload[j] ^= mask_key[j % 4]
        if opcode == _OPCODE_CLOSE:
            close_requested = True
            break
        if opcode == _OPCODE_PING:
            # Caller may pong at a higher layer; ignore payload here.
            continue
        if opcode in (_OPCODE_TEXT, _OPCODE_BINARY, _OPCODE_CONTINUATION):
            out.extend(payload)
        if not fin:
            # MVP: we do not reassemble fragments -- wait for more or stop.
            continue
    return bytes(out), buf[i:], close_requested


def encode_pong(payload: bytes = b"") -> bytes:
    """Server pong reply to a client ping."""
    return _encode_frame(payload[:125], opcode=_OPCODE_PONG, fin=True)


def encode_close() -> bytes:
    """Polite server close frame."""
    return _encode_frame(b"", opcode=_OPCODE_CLOSE, fin=True)
