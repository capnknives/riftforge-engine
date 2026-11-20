"""viewport_mcp.py -- public MCP server for the agent play harness.

One tool, ``viewport``: an AI coding agent (Cursor, Claude Code, any MCP
client) plays the MUD as a real ``Session`` and reads back every line the
game sent *plus the Python handler file:line that ran the turn*. That last
part is the point -- the agent goes from "the room looks wrong" straight
to the function to open, with no telnet scraping in between.

How the pieces fit
------------------
::

    agent  --MCP stdio-->  viewport_mcp.py  --loopback JSON-->  engine/viewport.py
                                 |                                 (inside Game())
                                 +-- starts tools/viewport_sidecar.py when
                                     nothing is listening yet

* ``engine/viewport.py`` is the harness itself (listener on 127.0.0.1:4002).
* ``tools/viewport_sidecar.py`` boots an isolated ``Game()`` on a temp
  sqlite file with that listener on. No telnet port, no real save file.
* ``tools/viewport_client.py`` is the JSON-line client (also a CLI).
* This file is the MCP face. It finds a listening viewport or starts the
  sidecar, sends one request, and returns the formatted feed.

If a Game you started yourself is already listening (``RIFTFORGE_VIEWPORT=1
python server.py``), this server talks to that one and starts nothing.

Wire it into an MCP client (``.cursor/mcp.json`` / ``.mcp.json``)::

    {"mcpServers": {"riftforge-viewport": {
        "command": "python3", "args": ["tools/viewport_mcp.py"]}}}

Environment
-----------
``RIFTFORGE_GAME``           game the sidecar boots (see viewport_sidecar.py).
``RIFTFORGE_VIEWPORT_BIND``  host:port of the listener (default 127.0.0.1:4002).

Stdlib only. Never imported by server.py. The private game repo layers
remote hosts on top of the same engine listener; this file is local only.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools import viewport_client as client  # noqa: E402

_SERVER_NAME = "riftforge-viewport"
_SERVER_VERSION = "1.0.0"
# Protocol revision we answer with when the client does not name one.
_PROTOCOL = "2024-11-05"

# How long a cold sidecar may take to finish Game() before we give up.
# basegame boots in seconds; a big content tree can take minutes.
_BOOT_TIMEOUT = 300.0

# The sidecar this process started (None when we attached to a listener
# someone else owns). Only our own child is ever stopped.
_SIDECAR: subprocess.Popen | None = None

_OPS = ("hello", "open", "send", "poll", "inspect", "reset", "mode", "close")


def _log(message: str) -> None:
    """Diagnostics go to stderr -- stdout carries the MCP protocol."""
    print(f"[viewport_mcp] {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Sidecar lifecycle
# ---------------------------------------------------------------------------

def _sidecar_log_path() -> str:
    """Boot log for our sidecar; one file per listener port.

    Keyed by port so two checkouts (or the smoke on a spare port) do not
    write over each other's log.
    """
    _host, port = client.bind_addr()
    return os.path.join(
        tempfile.gettempdir(), f"riftforge_viewport_sidecar_{port}.log",
    )


def _hello() -> dict | None:
    """The listener's hello reply, or None when nothing answers."""
    try:
        reply = client.request({"op": "hello"}, timeout=2.0)
    except OSError:
        return None
    return reply if reply.get("ok") else None


def _log_tail() -> str:
    """Last few KB of the sidecar boot log (for a failed start)."""
    try:
        with open(_sidecar_log_path(), "rb") as fh:
            return fh.read()[-4000:].decode("utf-8", errors="replace")
    except OSError:
        return "(no sidecar log)"


def stop_sidecar() -> None:
    """Stop the sidecar this process started. Safe to call twice."""
    global _SIDECAR
    proc, _SIDECAR = _SIDECAR, None
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def ensure_viewport(*, boot_timeout: float = _BOOT_TIMEOUT) -> tuple[str, bool]:
    """Make sure a ready viewport is listening. Return (note, is_error).

    A listener that answers hello with ``booting=false`` is used as-is.
    Otherwise start ``tools/viewport_sidecar.py`` as a child and wait for
    it. The child is stopped when this process exits, so closing the MCP
    client does not leave a Game running in the background.
    """
    global _SIDECAR
    hello = _hello()
    if hello is not None and not hello.get("booting"):
        return "", False
    if hello is None:
        # Nothing listening at all: this one is ours to start.
        if _SIDECAR is not None and _SIDECAR.poll() is None:
            pass  # our child is alive but has not bound yet; keep waiting
        else:
            try:
                log_fh = open(_sidecar_log_path(), "wb")
                _SIDECAR = subprocess.Popen(
                    [
                        sys.executable,
                        os.path.join(_ROOT, "tools", "viewport_sidecar.py"),
                    ],
                    cwd=_ROOT,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                )
            except OSError as err:
                return f"viewport: failed to start the sidecar: {err}", True
            atexit.register(stop_sidecar)
    deadline = time.monotonic() + boot_timeout
    while time.monotonic() < deadline:
        hello = _hello()
        if hello is not None and not hello.get("booting"):
            pid = _SIDECAR.pid if _SIDECAR is not None else "?"
            return f"sidecar ready pid={pid}", False
        if _SIDECAR is not None and _SIDECAR.poll() is not None:
            return (
                f"viewport: sidecar exited {_SIDECAR.returncode} before it "
                f"was ready.\n{_log_tail()}",
                True,
            )
        time.sleep(0.5)
    return (
        f"viewport: Game() did not finish booting within "
        f"{int(boot_timeout)}s.\n{_log_tail()}",
        True,
    )


# ---------------------------------------------------------------------------
# The one tool
# ---------------------------------------------------------------------------

def build_payload(args: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Turn MCP tool arguments into one viewport JSON request.

    Returns ``(payload, "")`` or ``(None, error text)``. ``since`` is only
    sent when the caller passed it: the listener keeps a watermark, and a
    default of 0 would replay the whole event ring on every send.
    """
    op = str(args.get("op") or "hello").strip().lower()
    if op not in _OPS:
        return None, (
            f"viewport: unknown op {op!r}; use one of "
            f"{', '.join(_OPS)}, or play"
        )
    payload: dict[str, Any] = {"op": op}
    if args.get("mode"):
        payload["mode"] = str(args.get("mode")).strip().lower()
    if op in ("open", "reset"):
        if args.get("character"):
            payload["character"] = str(args.get("character"))
    elif op == "send":
        lines = []
        raw_lines = args.get("lines")
        if isinstance(raw_lines, list):
            lines = [str(x).strip() for x in raw_lines if str(x).strip()]
        one = str(args.get("line") or "").strip()
        if one:
            lines.append(one)
        if not lines:
            return None, "viewport send requires line= or lines= (what to type)"
        if len(lines) == 1:
            payload["line"] = lines[0]
        else:
            payload["lines"] = lines
    elif op == "mode":
        payload["mode"] = str(args.get("mode") or args.get("line") or "staff")
    if op in ("send", "poll") and args.get("since") not in (None, ""):
        payload["since"] = int(args.get("since"))
    return payload, ""


def _request(payload: dict[str, Any]) -> tuple[str, bool]:
    """Ensure a viewport, send one request, format the feed."""
    note, err = ensure_viewport()
    if err:
        return note, True
    op = payload.get("op")
    # open / reset run the opening look and may wait on boot-time work.
    timeout = 300.0 if op in ("open", "reset", "hello") else 90.0
    try:
        reply = client.request(payload, timeout=timeout)
    except OSError as exc:
        return f"viewport: connect failed: {exc}", True
    text = client.format_reply(reply)
    if note:
        text = note + "\n" + text
    failed = (not reply.get("ok")) or bool(reply.get("blocked"))
    return text, failed


def _play(args: dict[str, Any]) -> tuple[str, bool]:
    """``op=play``: open the body, send one line, inspect. One call.

    Uses ``open`` (not ``reset``) so repeated plays keep the body where
    the last one left it. Send ``op=reset`` first for a known start.
    """
    line = str(args.get("line") or "").strip()
    if not line:
        return "viewport op=play requires line= (one command to type)", True
    open_args: dict[str, Any] = {"op": "open"}
    for key in ("character", "mode"):
        if args.get(key):
            open_args[key] = args.get(key)
    blocks = []
    for label, step in (
        ("open", open_args),
        ("send", {"op": "send", "line": line}),
        ("inspect", {"op": "inspect"}),
    ):
        payload, err = build_payload(step)
        if err:
            return err, True
        text, failed = _request(payload)
        blocks.append(f"--- {label} ---\n{text}")
        if failed:
            return "\n".join(blocks), True
    return "\n".join(blocks), False


def tool_viewport(args: dict[str, Any]) -> tuple[str, bool]:
    """Dispatch the ``viewport`` tool. Returns (text, is_error)."""
    op = str(args.get("op") or "hello").strip().lower()
    if op == "play":
        return _play(args)
    payload, err = build_payload(args)
    if err:
        return err, True
    return _request(payload)


TOOLS_SPEC: list[dict[str, Any]] = [
    {
        "name": "viewport",
        "description": (
            "Play the Riftforge MUD through the viewport harness. Returns "
            "the full Session output for each command plus the Python "
            "handler file:line that ran it, so a wrong line of output maps "
            "straight to the code. Runs against an isolated local Game() "
            "on a temp database (started on first use). Typical: op=open, "
            "then op=send line='look', then op=inspect. op=play does "
            "open + send + inspect for one line. Omit since (the server "
            "keeps a watermark). mode=player refuses staff verbs. "
            "op=close when done."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "description": (
                        "hello, open, send, poll, inspect, reset, mode, "
                        "close, or play"
                    ),
                },
                "line": {
                    "type": "string",
                    "description": "Command to type (op=send / op=play)",
                },
                "lines": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Several commands in one send",
                },
                "character": {
                    "type": "string",
                    "description": "Body to occupy; default is the playtest body",
                },
                "since": {
                    "type": "integer",
                    "description": (
                        "Event seq; omit to use the server watermark "
                        "(0 = whole ring)"
                    ),
                },
                "mode": {
                    "type": "string",
                    "description": "staff (default) or player (refuses gm*)",
                },
            },
            "required": ["op"],
            "additionalProperties": False,
        },
    },
]

_INSTRUCTIONS = (
    "Riftforge viewport harness. Use the viewport tool to play the game "
    "and debug it: every send returns the game's output and the handler "
    "file:line. Start with op=open, then op=send. Do not write telnet "
    "scripts for playtest. The Game is an isolated local sidecar on a "
    "temp database; nothing here touches a real save."
)


# ---------------------------------------------------------------------------
# MCP stdio (JSON-RPC 2.0)
# ---------------------------------------------------------------------------

def handle_rpc(message: dict[str, Any]) -> dict[str, Any] | None:
    """Answer one JSON-RPC message. None for notifications (no reply)."""
    method = message.get("method")
    msg_id = message.get("id")
    if "id" not in message:
        return None  # notifications (initialized, cancelled, ...) get no reply

    def ok(result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method == "initialize":
        params = message.get("params") or {}
        return ok({
            "protocolVersion": str(params.get("protocolVersion") or _PROTOCOL),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
            "instructions": _INSTRUCTIONS,
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS_SPEC})
    if method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        if name != "viewport":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": f"unknown tool {name!r}"},
            }
        try:
            text, is_error = tool_viewport(params.get("arguments") or {})
        except Exception:
            # A tool crash is a tool result, not a protocol error: the
            # agent should see the traceback and carry on.
            text, is_error = traceback.format_exc(), True
        return ok({
            "content": [{"type": "text", "text": text}],
            "isError": bool(is_error),
        })
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def _pop_message(buf: bytearray) -> tuple[dict[str, Any] | None, bool]:
    """Pop one JSON-RPC object off *buf*. Second value: Content-Length framed.

    MCP stdio is newline-delimited JSON. Some older clients still send
    LSP-style ``Content-Length`` headers, so both are accepted and the
    reply uses whichever framing the client used.
    """
    if not buf:
        return None, False
    if buf.lstrip().lower().startswith(b"content-length:"):
        header_end = buf.find(b"\r\n\r\n")
        if header_end == -1:
            return None, True
        header = bytes(buf[:header_end]).decode("ascii", errors="replace")
        match = re.search(r"Content-Length:\s*(\d+)", header, re.I)
        if not match:
            del buf[: header_end + 4]
            return None, True
        start = header_end + 4
        length = int(match.group(1))
        if len(buf) < start + length:
            return None, True
        body = bytes(buf[start : start + length])
        del buf[: start + length]
        return json.loads(body.decode("utf-8")), True
    nl = buf.find(b"\n")
    if nl == -1:
        return None, False
    line = bytes(buf[:nl]).strip()
    del buf[: nl + 1]
    if not line:
        return _pop_message(buf)
    return json.loads(line.decode("utf-8")), False


def _write_message(message: dict[str, Any], *, framed: bool) -> None:
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    out = sys.stdout.buffer
    if framed:
        out.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    else:
        out.write(body + b"\n")
    out.flush()


def serve_stdio() -> int:
    """Read JSON-RPC from stdin until EOF, answer on stdout."""
    buf = bytearray()
    stdin = sys.stdin.buffer
    _log(f"stdio server root={_ROOT}")
    while True:
        chunk = stdin.read1(65536) if hasattr(stdin, "read1") else stdin.read(65536)
        if not chunk:
            break
        buf.extend(chunk)
        while True:
            try:
                message, framed = _pop_message(buf)
            except json.JSONDecodeError as err:
                _log(f"bad json: {err}")
                break
            if message is None:
                break
            if not isinstance(message, dict):
                continue
            try:
                reply = handle_rpc(message)
            except Exception:
                _log(traceback.format_exc())
                reply = None
                if "id" in message:
                    reply = {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {"code": -32603, "message": "internal error"},
                    }
            if reply is not None:
                _write_message(reply, framed=framed)
    stop_sidecar()
    return 0


def main() -> int:
    return serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
