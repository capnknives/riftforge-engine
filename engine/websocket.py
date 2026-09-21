"""
websocket.py -- minimal RFC 6455 server helpers (stdlib only).

Hand-rolled HTTP upgrade + text-frame read/write for browser clients.
No third-party ``websockets`` package (hard rule 1). Gateway-terminated:
client sockets survive game-child restart like telnet today.

MVP scope: text frames only, no permessage-deflate, no TLS, no fragmentation
reassembly beyond a single frame per message (browsers do not fragment normal
``send()`` calls; our server never splits outbound lines across frames).

Close codes: ``decode_client_messages_ex`` reports *why* a stream ended
(RFC 6455 s7.4.1) instead of a bare "it closed" bool. The gateway logs that
so browser drops are attributable -- a tab close (1001) and a proxy reaping
an idle socket (1006) are otherwise indistinguishable.
See ``docs/plans/web_client_drop_diagnostics.md``.
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

# RFC 6455 s7.4.1 close codes we actually name in gateway drop logs. The
# code is the cheapest signal we have for *why* a browser player fell off:
# a tab close and a proxy reaping an idle socket look identical in the
# "client went away" sense, but 1001 vs 1006 tells them apart. See
# docs/plans/web_client_drop_diagnostics.md.
CLOSE_NORMAL = 1000       # polite close (client sent a close frame)
CLOSE_GOING_AWAY = 1001   # tab closed / navigated away
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_UNSUPPORTED = 1003
CLOSE_NO_STATUS = 1005    # close frame carried no code (synthetic)
CLOSE_ABNORMAL = 1006     # socket died with no close frame (synthetic)
CLOSE_INVALID_PAYLOAD = 1007
CLOSE_POLICY = 1008
CLOSE_TOO_BIG = 1009

_CLOSE_LABELS = {
    CLOSE_NORMAL: "normal",
    CLOSE_GOING_AWAY: "going-away",
    CLOSE_PROTOCOL_ERROR: "protocol-error",
    CLOSE_UNSUPPORTED: "unsupported-data",
    CLOSE_NO_STATUS: "no-status",
    CLOSE_ABNORMAL: "abnormal-no-close-frame",
    CLOSE_INVALID_PAYLOAD: "invalid-payload",
    CLOSE_POLICY: "policy-violation",
    CLOSE_TOO_BIG: "frame-too-big",
    1010: "extension-expected",
    1011: "server-error",
    1015: "tls-failure",
}


def close_code_label(code) -> str:
    """Human tag for a close code -- gateway logs read by a person."""
    if code is None:
        return "none"
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "bad-code"
    known = _CLOSE_LABELS.get(code)
    if known:
        return known
    if 3000 <= code <= 3999:
        return "registered-app"
    if 4000 <= code <= 4999:
        return "private-app"
    return "unknown"


class WsClose:
    """Why one WebSocket read loop ended.

    ``source`` is where the verdict came from:

    - ``"client"``   -- the peer sent a close frame (``code`` may be None
      when the frame carried no body, which RFC 6455 allows)
    - ``"protocol"`` -- *we* refused the stream (oversize / unmasked frame);
      the code is what we would send back, not what the client said
    """

    __slots__ = ("code", "reason", "source")

    def __init__(self, code=None, reason: str = "", source: str = "client"):
        self.code = code
        self.reason = reason
        self.source = source

    @property
    def label(self) -> str:
        return close_code_label(self.code)

    def __repr__(self) -> str:  # debugging / log lines
        return (
            f"WsClose(code={self.code!r}, label={self.label!r}, "
            f"source={self.source!r}, reason={self.reason!r})"
        )


def _parse_close_payload(payload: bytes) -> WsClose:
    """Unpack a CLOSE frame body into code + reason.

    RFC 6455 s5.5.1: the body is optional. When present it is a 2-byte
    big-endian status code followed by an optional UTF-8 reason. A 1-byte
    body is malformed -- we record that rather than raising, because this
    runs on internet input.
    """
    if not payload:
        # Legal: "no status code was present".
        return WsClose(CLOSE_NO_STATUS, "", "client")
    if len(payload) < 2:
        return WsClose(CLOSE_PROTOCOL_ERROR, "short-close-body", "protocol")
    code = struct.unpack("!H", payload[:2])[0]
    try:
        reason = payload[2:].decode("utf-8", errors="replace")[:120]
    except Exception:
        reason = ""
    return WsClose(code, reason, "client")


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


def encode_text_frame_from_bytes(payload: bytes) -> bytes:
    """Build a text frame from already-encoded UTF-8 bytes.

    Callers that must measure the payload first (the gateway stamps frame
    counters) use this so the string is not UTF-8 encoded twice on the
    outbound hot path.
    """
    if len(payload) > MAX_FRAME_PAYLOAD:
        payload = payload[:MAX_FRAME_PAYLOAD]
    return _encode_frame(payload, opcode=_OPCODE_TEXT, fin=True)


def encode_text_frame(text: str) -> bytes:
    """Build one unmasked server→client text frame (FIN + opcode 0x1)."""
    return encode_text_frame_from_bytes(text.encode("utf-8"))


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


def decode_client_messages_ex(buf: bytes):
    """Parse client frames, reporting *why* the stream ended.

    Same parse as :func:`decode_client_messages` but the third element is a
    :class:`WsClose` (or None when the stream is simply incomplete and we
    should read more). The gateway logs that verdict so a browser drop is
    attributable -- see docs/plans/web_client_drop_diagnostics.md.

    Returns (messages, remainder, close_info_or_None).
    """
    messages = []
    i = 0
    n = len(buf)
    close_info = None
    while i < n:
        start = i
        if i + 2 > n:
            break
        b0 = buf[i]
        b1 = buf[i + 1]
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        i += 2
        extra = 0
        if length == 126:
            if i + 2 > n:
                i = start
                break
            length = struct.unpack("!H", buf[i : i + 2])[0]
            i += 2
            extra = 2
        elif length == 127:
            if i + 8 > n:
                i = start
                break
            length = struct.unpack("!Q", buf[i : i + 8])[0]
            i += 8
            extra = 8
        if length > MAX_FRAME_PAYLOAD:
            # Client sent more than we will buffer. Name it: this is the
            # inbound twin of the outbound truncation bug.
            return messages, buf[i:], WsClose(
                CLOSE_TOO_BIG, f"inbound frame {length}B", "protocol"
            )
        if not masked:
            # RFC 6455 s5.1: client frames MUST be masked. An unmasked frame
            # means a broken client or a meddling proxy -- worth telling apart.
            return messages, buf[i:], WsClose(
                CLOSE_PROTOCOL_ERROR, "unmasked client frame", "protocol"
            )
        if i + 4 + length > n:
            i = start
            break
        mask_key = buf[i : i + 4]
        i += 4
        payload = bytearray(buf[i : i + length])
        i += length
        for j in range(len(payload)):
            payload[j] ^= mask_key[j % 4]
        if opcode == _OPCODE_CLOSE:
            # The body carries the status code we previously discarded.
            close_info = _parse_close_payload(bytes(payload))
            break
        if opcode == _OPCODE_PING:
            continue
        if opcode in (_OPCODE_TEXT, _OPCODE_BINARY, _OPCODE_CONTINUATION):
            messages.append(bytes(payload))
    return messages, buf[i:], close_info


def decode_client_messages(buf: bytes):
    """Back-compat wrapper: third element is the old bool.

    Callers that want the close code use :func:`decode_client_messages_ex`.
    """
    messages, remainder, close_info = decode_client_messages_ex(buf)
    return messages, remainder, close_info is not None


def decode_client_frames(buf: bytes):
    """Parse client→server frames from ``buf``.

    Returns (application_bytes, remainder, close_requested):
      concatenated payloads (legacy callers). Prefer decode_client_messages
      when frames are JSON envelopes.
    """
    messages, remainder, close_requested = decode_client_messages(buf)
    return b"".join(messages), remainder, close_requested


def encode_pong(payload: bytes = b"") -> bytes:
    """Server pong reply to a client ping."""
    return _encode_frame(payload[:125], opcode=_OPCODE_PONG, fin=True)


def encode_close(code: int | None = None, reason: str = "") -> bytes:
    """Polite server close frame, optionally carrying a status code.

    Default (no args) stays byte-identical to the original bodiless frame so
    existing callers are unchanged. Passing a code lets the *browser* log why
    we hung up, which is the mirror of the code we now read from the client.
    """
    if code is None:
        return _encode_frame(b"", opcode=_OPCODE_CLOSE, fin=True)
    body = struct.pack("!H", int(code) & 0xFFFF)
    if reason:
        # RFC 6455 s5.5: control frame payload is capped at 125 bytes total.
        body += reason.encode("utf-8", errors="replace")[:123]
    return _encode_frame(body, opcode=_OPCODE_CLOSE, fin=True)
