# Viewport harness — let an AI agent play and debug your game

The viewport is a loopback JSON door into a running `Game()`. An agent
(Cursor, Claude Code, any MCP client, or a plain script) types commands as
a real player `Session` and gets back:

- every line the game sent, in order, ANSI stripped;
- the **Python handler and `file:line`** that ran each command;
- GMCP pushes and snoop relays as structured events;
- an `inspect` snapshot (room, HP, quests, staff state).

So "the room description is wrong" arrives already attached to
`engine/verbs/basic.py:374`. No telnet client, no terminal scraping.

```
agent --MCP stdio--> tools/viewport_mcp.py --JSON lines--> engine/viewport.py
                           |                                 (inside Game())
                           +-- starts tools/viewport_sidecar.py on first use
```

## Quick start

No setup beyond a checkout. Standard library only, Python 3.11+.

```bash
python tools/viewport_sidecar.py &          # isolated Game() on a temp database
python tools/viewport_client.py open        # occupy the playtest body
python tools/viewport_client.py send look
python tools/viewport_client.py inspect
python tools/viewport_client.py close
```

`send look` prints something like:

```
viewport send  mode=staff
handler: engine.verbs.basic.cmd_look  engine/verbs/basic.py:374
verb: look
> look
[dispatch] engine.verbs.basic.cmd_look  engine/verbs/basic.py:374

Notbigville - Main Street - Plaza                          [ City ]
  A modest Kansas square where two highways meet under a wide
  prairie sky. ...
-- seq 9
```

The sidecar boots `basegame` by default. Pick another game with
`RIFTFORGE_GAME=classic` (or `none` for the one-room lean engine, or your
own game package).

## Use it from an agent (MCP)

Add the server to your MCP client. For Cursor (`.cursor/mcp.json`) or
Claude Code (`.mcp.json`) in the repo root:

```json
{
  "mcpServers": {
    "riftforge-viewport": {
      "command": "python3",
      "args": ["tools/viewport_mcp.py"]
    }
  }
}
```

The server exposes one tool, `viewport`. It starts the sidecar the first
time it is called and stops it when the client disconnects.

| `op` | What it does |
|------|--------------|
| `open` | Occupy the playtest body (or `character=`); returns the opening look |
| `send` | Type `line=` (or several `lines=`); returns output + handler source |
| `inspect` | Room, HP, quests, staff/NPC flags — no command typed |
| `poll` | Events since the last read (things that happened between sends) |
| `reset` | Back to a known start: staff mode, abandon quests, fresh look |
| `mode` | `staff` (default) or `player`; player mode refuses `gm` verbs |
| `play` | `open` + `send` + `inspect` for one `line=` in a single call |
| `close` | Park the body and end the session |
| `hello` | Is the listener up, and is `Game()` still booting? |

Leave `since` out. The listener remembers what it already showed you;
`since=0` replays the whole event ring.

## Use it on a server you are already running

The sidecar is the safe default, but the listener is part of the engine.
Set `RIFTFORGE_VIEWPORT=1` in the environment (or the repo `.env`) of any
`python server.py` and it binds `127.0.0.1:4002` beside the telnet port.
The client and the MCP server talk to whatever is listening there, and
only start a sidecar when nothing is.

A normal server has no minted playtest body, so pass a real one:
`viewport_client.py open --character YourCharacter`.

| Variable | Meaning |
|----------|---------|
| `RIFTFORGE_VIEWPORT` | `1` turns the listener on inside `Game()` |
| `RIFTFORGE_VIEWPORT_BIND` | `host:port`, loopback only (default `127.0.0.1:4002`) |
| `RIFTFORGE_VIEWPORT_SAFETIES` | `live` = snapshot and restore every body touched, refuse destructive verbs; `off` = sidecar / test box |
| `RIFTFORGE_VIEWPORT_STAFF_ACCOUNT` | Staff account to stamp on the session when the body has none |
| `RIFTFORGE_VIEWPORT_DB` | Pin the sidecar's sqlite path (default: fresh temp file per run) |

## Safety model

- **Loopback only.** The listener refuses a non-loopback bind (short of an
  explicit lab-only override). It has no login step, so it must never be
  published. Reach a remote box by running
  the client *on* that box (SSH, `docker exec`).
- **The sidecar never touches a real save.** Fresh temp sqlite, no telnet
  port, no gateway.
- **`RIFTFORGE_VIEWPORT_SAFETIES=live`** is for a server with real players:
  the harness may only occupy a staff fixture body, every body a command
  touches is snapshotted and put back on `close` (or when the agent
  disconnects or goes idle), and roster-destroying verbs are refused. See
  `engine/viewport_live.py`.

## Wire protocol

One JSON object per line each way, on the loopback socket.

```json
{"op": "send", "line": "look"}
```

```json
{"ok": true, "op": "send", "seq": 9, "mode": "staff",
 "turn": {"verb": "look", "handler": "engine.verbs.basic.cmd_look",
          "source": "engine/verbs/basic.py:374"},
 "events": [{"seq": 6, "kind": "in", "text": "look"},
            {"seq": 7, "kind": "dispatch", "handler": "...", "source": "..."},
            {"seq": 8, "kind": "prose", "text": "..."}]}
```

Event kinds: `in` (what was typed), `dispatch` (handler + source), `prose`
(a `Session.send`), `gmcp` (package + payload). A `send` that outlives its
timeout still answers `ok: true` with `timed_out: true` and whatever prose
arrived, so a slow verb is visible instead of lost.

## Files

| Path | Role |
|------|------|
| `engine/viewport.py` | The harness: `ViewportSession`, event ring, JSON listener |
| `engine/viewport_live.py` | Live-server safeties (freeze, restore, refused verbs) |
| `tools/viewport_sidecar.py` | Isolated `Game()` + listener on a temp database |
| `tools/viewport_client.py` | JSON-line client and CLI |
| `tools/viewport_mcp.py` | MCP stdio server (the `viewport` tool) |
| `tools/viewport_harness_smoke.py` | End-to-end test: MCP → sidecar → `look` → handler source |

## Why not telnet?

A telnet script sees a byte stream. It has to guess where one command's
output ends, strip prompts and colour, and it learns nothing about which
code produced a line. The viewport session is the same `Session` class a
player gets, so commands run through the real dispatch path, but its
reader and writer are in-process queues: turns have a clear end, output is
recorded as structured events, and the dispatcher stamps each turn with
the handler it resolved.
