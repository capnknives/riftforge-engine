"""Sync JSON client for the Ash viewport (loopback, not telnet).

Talks to ``engine.viewport`` on ``127.0.0.1:4002`` (override with
``RIFTFORGE_VIEWPORT_BIND``). Used by MCP, the local sidecar, and
``docker compose exec`` on Azure crashgate.

    py -3.13 tools/viewport_client.py open
    py -3.13 tools/viewport_client.py send look
    py -3.13 tools/viewport_client.py poll
    py -3.13 tools/viewport_client.py inspect
    py -3.13 tools/viewport_client.py reset
    py -3.13 tools/viewport_client.py --json "{\\"op\\":\\"send\\",\\"lines\\":[\\"look\\"]}"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def bind_addr() -> tuple[str, int]:
    text = (os.environ.get("RIFTFORGE_VIEWPORT_BIND") or "127.0.0.1:4002").strip()
    if ":" in text:
        host, _, port_s = text.rpartition(":")
        return (host or "127.0.0.1"), int(port_s)
    return "127.0.0.1", int(text)


def _read_json_line(sock, buf: bytearray) -> tuple[dict, bytearray]:
    """Read one JSON line from *sock*, using *buf* as a carry-over."""
    while True:
        idx = buf.find(b"\n")
        if idx >= 0:
            line = bytes(buf[:idx])
            del buf[: idx + 1]
            break
        chunk = sock.recv(65536)
        if not chunk:
            line = bytes(buf)
            buf.clear()
            break
        buf.extend(chunk)
    if not line.strip():
        return {"ok": False, "error": "empty viewport reply"}, buf
    try:
        reply = json.loads(line.decode("utf-8"))
    except json.JSONDecodeError as err:
        return {"ok": False, "error": f"bad viewport json: {err}"}, buf
    if not isinstance(reply, dict):
        return {"ok": False, "error": "viewport reply was not an object"}, buf
    return reply, buf


def request(payload: dict, *, timeout: float = 90.0) -> dict:
    """Send one JSON object, read one JSON line. Raises OSError on connect fail."""
    replies = request_many([payload], timeout=timeout)
    if not replies:
        return {"ok": False, "error": "empty viewport reply"}
    return replies[0]


def request_many(payloads: list[dict], *, timeout: float = 90.0) -> list[dict]:
    """One TCP connection, many JSON-line requests (crashgate batch)."""
    host, port = bind_addr()
    replies = []
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        buf = bytearray()
        for payload in payloads:
            raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            sock.sendall(raw)
            reply, buf = _read_json_line(sock, buf)
            replies.append(reply)
    return replies


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def format_reply(reply: dict) -> str:
    """Human/agent-readable feed from a viewport JSON reply."""
    if not reply.get("ok"):
        extra = []
        if reply.get("booting"):
            extra.append("booting=true")
        suffix = (" " + " ".join(extra)) if extra else ""
        return f"viewport error: {reply.get('error') or reply}{suffix}"
    lines = []
    char = reply.get("character")
    room = reply.get("room")
    op = reply.get("op")
    if op:
        header = f"viewport {op}"
        if char:
            header += f"  character={char}"
        if room:
            header += f"  room={room}"
        if reply.get("booting"):
            header += "  booting=true"
        if reply.get("sha"):
            header += f"  sha={reply.get('sha')}"
        if reply.get("mode"):
            header += f"  mode={reply.get('mode')}"
        lines.append(header)
    if reply.get("staff_account"):
        lines.append(f"staff_account: {reply.get('staff_account')}")
    if "is_npc" in reply:
        lines.append(f"is_npc: {reply.get('is_npc')}")
    if reply.get("gm_mode") is not None and op == "inspect":
        lines.append(f"gm_mode: {reply.get('gm_mode')}")
    if op == "inspect" and reply.get("quests") is not None:
        lines.append(f"quests: {reply.get('quests')}")
    if op == "inspect" and reply.get("hp") is not None:
        lines.append(f"hp: {reply.get('hp')}/{reply.get('max_hp')}")
    turn = reply.get("turn") or {}
    if turn.get("handler") or turn.get("source"):
        lines.append(
            "handler: "
            f"{turn.get('handler') or '?'}  "
            f"{turn.get('source') or ''}".rstrip()
        )
        if turn.get("verb"):
            lines.append(f"verb: {turn.get('verb')}")
    if turn.get("ok") is False or reply.get("timed_out"):
        if turn.get("blocked"):
            lines.append("turn: blocked by mode=player")
        else:
            lines.append("turn: timed out waiting for dispatch (prose below if any)")
    last_in = None
    for event in reply.get("events") or []:
        kind = event.get("kind")
        if kind == "in":
            text = strip_ansi(str(event.get("text") or ""))
            # Consecutive identical in-lines are a since=0 replay, not typing.
            if text == last_in:
                continue
            last_in = text
            lines.append(f"> {text}")
        elif kind == "prose":
            text = strip_ansi(str(event.get("text") or ""))
            lines.append(text)
        elif kind == "dispatch":
            lines.append(
                f"[dispatch] {event.get('handler') or ''}  "
                f"{event.get('source') or ''}".rstrip()
            )
        elif kind == "gmcp":
            pkg = event.get("package") or ""
            lines.append(f"[gmcp] {pkg}")
    if not lines:
        lines.append(f"viewport seq={reply.get('seq', 0)} (no new events)")
    else:
        lines.append(f"-- seq {reply.get('seq', 0)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ash viewport JSON client")
    parser.add_argument(
        "op",
        nargs="?",
        choices=(
            "hello", "open", "send", "poll", "inspect", "close",
            "reset", "mode",
        ),
        help="omit when passing --json",
    )
    parser.add_argument("line", nargs="?", help="command line (send)")
    parser.add_argument("--character", default="Ash")
    parser.add_argument(
        "--since",
        type=int,
        default=None,
        help="only events with seq > N (omit = server watermark; 0 = full ring)",
    )
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--mode", choices=("staff", "player"))
    parser.add_argument(
        "--json",
        dest="json_blob",
        help="raw JSON request object (one TCP call; used by crashgate batch)",
    )
    parser.add_argument(
        "--lines",
        action="append",
        default=[],
        help="extra send lines (repeatable); one TCP round-trip on the server",
    )
    args = parser.parse_args(argv)
    if args.json_blob:
        try:
            payload = json.loads(args.json_blob)
        except json.JSONDecodeError as err:
            print(f"viewport error: bad --json ({err})", file=sys.stderr)
            return 1
        if not isinstance(payload, dict):
            print("viewport error: --json must be an object", file=sys.stderr)
            return 1
    else:
        if not args.op:
            parser.error("op is required unless --json is set")
        payload = {"op": args.op}
        if args.op == "open":
            payload["character"] = args.character
            if args.mode:
                payload["mode"] = args.mode
        elif args.op == "send":
            extra = list(args.lines or [])
            if (args.line or "").strip():
                extra.append(args.line)
            if not extra:
                parser.error("send requires a command line or --lines")
            if len(extra) == 1:
                payload["line"] = extra[0]
            else:
                payload["lines"] = extra
            if args.since is not None:
                payload["since"] = args.since
            payload["timeout"] = min(args.timeout, 90.0)
            if args.mode:
                payload["mode"] = args.mode
        elif args.op == "poll":
            if args.since is not None:
                payload["since"] = args.since
        elif args.op == "mode":
            payload["mode"] = args.mode or args.line or "staff"
    try:
        reply = request(payload, timeout=max(args.timeout, 5.0))
    except OSError as err:
        print(
            f"viewport error: cannot connect to {bind_addr()[0]}:{bind_addr()[1]} "
            f"({err}). Enable RIFTFORGE_VIEWPORT=1 on that Game and game-only "
            "restart, or start tools/viewport_sidecar.py locally. If the tool "
            "is missing from Cursor MCP, run this CLI from a checkout that "
            "has tools/viewport_client.py — do not write a telnet script.",
            file=sys.stderr,
        )
        return 1
    text = format_reply(reply)
    print(text)
    # Booting hello is ok=true; send timeout is ok=true with timed_out.
    if reply.get("ok") and reply.get("timed_out"):
        return 0
    return 0 if reply.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
