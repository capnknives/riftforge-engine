"""viewport_harness_smoke.py -- end-to-end check of the public play harness.

Drives ``tools/viewport_mcp.py`` the way an MCP client does (JSON-RPC
messages into ``handle_rpc``) against a real sidecar ``Game()``:

    initialize -> tools/list -> open -> send look -> inspect -> reset
    -> mode=player refuses gm -> play -> close

It proves the three things the harness promises: the game's own output
comes back, the handler ``file:line`` comes back with it, and no game
package beyond what ships in the public tree is needed. Runs on a spare
loopback port so a developer's own viewport on :4002 is left alone.

    python tools/viewport_harness_smoke.py

Stdlib only. Exit 0 = pass.
"""

from __future__ import annotations

import os
import socket
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# The private monorepo runs Game()-booting smokes from a throwaway copy so
# boot heals never dirty the checkout. The public tree does not ship that
# helper (and basegame writes nothing into the repo), so it is optional.
try:
    import smoke_sandbox

    smoke_sandbox.isolate(__file__)
except ImportError:
    pass


def _free_port() -> int:
    """A loopback port nothing is using right now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _pick_game() -> str:
    """basegame when it ships, else the lean one-room engine.

    Never SUPERS: this smoke is the public-tree proof, and the private
    game has its own (much larger) ``viewport_smoke.py``.
    """
    if os.path.isfile(os.path.join(_ROOT, "basegame", "__init__.py")):
        return "basegame"
    return "none"


# Set before importing the MCP module's helpers read them.
os.environ["RIFTFORGE_VIEWPORT_BIND"] = f"127.0.0.1:{_free_port()}"
os.environ["RIFTFORGE_GAME"] = _pick_game()
os.environ.pop("RIFTFORGE_VIEWPORT_DB", None)

from tools import viewport_mcp  # noqa: E402


def _assert(cond, message) -> None:
    if not cond:
        raise AssertionError(message)


_NEXT_ID = [0]


def _rpc(method: str, params: dict | None = None) -> dict:
    """One JSON-RPC round trip through the server's handler."""
    _NEXT_ID[0] += 1
    reply = viewport_mcp.handle_rpc(
        {"jsonrpc": "2.0", "id": _NEXT_ID[0], "method": method, "params": params or {}}
    )
    _assert(reply is not None, f"{method}: no reply")
    _assert("error" not in reply, f"{method}: {reply.get('error')}")
    return reply["result"]


def _call(**arguments) -> tuple[str, bool]:
    """Call the viewport tool; return (text, isError)."""
    result = _rpc("tools/call", {"name": "viewport", "arguments": arguments})
    return result["content"][0]["text"], bool(result["isError"])


def test_protocol() -> None:
    init = _rpc("initialize", {"protocolVersion": "2025-06-18"})
    _assert(init["serverInfo"]["name"] == "riftforge-viewport", init)
    _assert(init["protocolVersion"] == "2025-06-18", init)
    tools = _rpc("tools/list")["tools"]
    _assert([t["name"] for t in tools] == ["viewport"], tools)
    # Notifications never get a reply (a reply would desync the client).
    _assert(
        viewport_mcp.handle_rpc(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ) is None,
        "notification must not be answered",
    )
    missing = viewport_mcp.handle_rpc(
        {"jsonrpc": "2.0", "id": 99, "method": "no/such"}
    )
    _assert(missing["error"]["code"] == -32601, missing)


def test_payloads() -> None:
    """Argument mapping, no Game needed."""
    payload, err = viewport_mcp.build_payload({"op": "send", "line": "look"})
    _assert(not err and "since" not in payload, (payload, err))
    payload, err = viewport_mcp.build_payload(
        {"op": "send", "lines": ["look", "north"], "since": 7}
    )
    _assert(payload == {"op": "send", "lines": ["look", "north"], "since": 7}, payload)
    _payload, err = viewport_mcp.build_payload({"op": "send"})
    _assert("requires line" in err, err)
    _payload, err = viewport_mcp.build_payload({"op": "teleport"})
    _assert("unknown op" in err, err)
    text, is_error = _call(op="play")
    _assert(is_error and "requires line" in text, text)


def test_play_session() -> None:
    """Boot the sidecar and play through it."""
    text, is_error = _call(op="open")
    _assert(not is_error, f"open failed:\n{text}")
    _assert("viewport open" in text and "character=" in text, text)
    _assert("room=" in text, f"open should name the room:\n{text}")

    text, is_error = _call(op="send", line="look")
    _assert(not is_error, f"send look failed:\n{text}")
    _assert("> look" in text, f"feed should echo the typed line:\n{text}")
    # The reason the harness exists: output arrives with the handler source.
    _assert("handler: " in text and "cmd_look" in text, text)
    _assert("engine/verbs/basic.py:" in text, f"no handler file:line:\n{text}")
    _assert("Exits" in text or "exits" in text, f"no room prose:\n{text}")

    text, is_error = _call(op="inspect")
    _assert(not is_error and "is_npc: False" in text, text)

    # reset must not type staff verbs this game never registered.
    text, is_error = _call(op="reset")
    _assert(not is_error, f"reset failed:\n{text}")
    _assert("Unknown command" not in text, f"reset typed an unknown verb:\n{text}")

    text, is_error = _call(op="send", line="gm on", mode="player")
    _assert(is_error and "blocked by mode=player" in text, text)
    _text, is_error = _call(op="mode", mode="staff")
    _assert(not is_error, "mode=staff should switch back")

    text, is_error = _call(op="play", line="look")
    _assert(not is_error, f"play failed:\n{text}")
    for label in ("--- open ---", "--- send ---", "--- inspect ---"):
        _assert(label in text, f"play missing {label}:\n{text}")

    text, is_error = _call(op="close")
    _assert(not is_error, f"close failed:\n{text}")


def main() -> int:
    tests = (test_protocol, test_payloads, test_play_session)
    try:
        for test in tests:
            test()
            print(f"ok  {test.__name__}")
    finally:
        viewport_mcp.stop_sidecar()
    print(
        f"viewport_harness_smoke: all {len(tests)} passed "
        f"(game={os.environ['RIFTFORGE_GAME']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
