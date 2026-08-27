"""
ws_json.py -- JSON envelopes for the browser WebSocket HUD.

Telnet still speaks IAC GMCP. Browser tabs cannot: the gateway splits the
game byte stream into ``text`` and ``gmcp`` ops, and the page sends commands
the same way. Stdlib json only (hard rule 1).

Wire (one WebSocket text frame per object)::

    {"op":"text","data":"...ansi..."}
    {"op":"gmcp","package":"Char.Vitals","payload":{...}}
    {"op":"cmd","data":"look"}
    {"op":"hello","client":"riftforge-web","version":"2"}

Legacy Phase 1 clients that send a raw line still work: the gateway treats a
frame that does not start with ``{`` as a telnet command line.
"""

from __future__ import annotations

import json

# Cap one envelope so a forged payload cannot stall the gateway JSON parser.
MAX_ENVELOPE_CHARS = 65536

OP_TEXT = "text"
OP_GMCP = "gmcp"
OP_CMD = "cmd"
OP_HELLO = "hello"


def encode_text(data: str) -> str:
    """Browser-bound prose (ANSI allowed; client HTML-escapes)."""
    return json.dumps(
        {"op": OP_TEXT, "data": data if data is not None else ""},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def encode_gmcp(package: str, payload) -> str:
    """Browser-bound GMCP package (same JSON the telnet client would get)."""
    return json.dumps(
        {"op": OP_GMCP, "package": str(package or ""), "payload": payload},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def parse_inbound(raw: str):
    """Parse one client WebSocket text frame.

    Returns a dict with ``kind``:

    - ``cmd`` -- send ``line`` (str, no CRLF) to the game as a command
    - ``gmcp`` -- ``package`` + ``payload`` to wrap as IAC SB GMCP
    - ``hello`` -- client identity; no game bytes
    - ``line`` -- legacy raw telnet line (no JSON)

    Returns None when the frame is empty or not a usable object.
    """
    text = raw if isinstance(raw, str) else ""
    if len(text) > MAX_ENVELOPE_CHARS:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    if not stripped.startswith("{"):
        # Phase 1: one command, optional trailing CRLF already stripped.
        line = text.replace("\r\n", "\n").replace("\r", "\n")
        if line.endswith("\n"):
            line = line[:-1]
        return {"kind": "line", "line": line}
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    op = str(obj.get("op") or "").strip().lower()
    if op in (OP_CMD, OP_TEXT):
        data = obj.get("data")
        if data is None:
            data = obj.get("line")
        line = "" if data is None else str(data)
        line = line.replace("\r\n", "\n").replace("\r", "\n")
        if line.endswith("\n"):
            line = line[:-1]
        return {"kind": "cmd", "line": line}
    if op == OP_GMCP:
        package = str(obj.get("package") or "").strip()
        if not package:
            return None
        return {
            "kind": "gmcp",
            "package": package,
            "payload": obj.get("payload"),
        }
    if op == OP_HELLO:
        return {
            "kind": "hello",
            "client": obj.get("client"),
            "version": obj.get("version"),
        }
    return None
