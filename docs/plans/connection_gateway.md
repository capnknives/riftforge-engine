# Connection gateway — design record

Level 3 connection holder: a long-lived process owns telnet `:4000` so
the game process can restart (code edit, crash, copyover-off path)
without dropping client TCP sockets.

**Status:** ✅ Shipped on `main` (`07b6987` gateway + soft-optional;
`4fc5bc6` Session.run login fix). Docker hold-across-restart verified.
**Worktree:** `D:/Claude/riftforge-gateway` (synced; track closed).

Related: [`two_repo_purity.md`](two_repo_purity.md) Phase 4b done; **Phase 5
remotes still parked** until explicitly opened. Supersedes GAP_AUDIT
`arch-listening-socket` as the primary fix for accept-window / crash drops.

## Architecture

```
Telnet clients  --TCP:4000-->  engine/gateway.py
                                    |
                                    | IPC TCP 127.0.0.1:4001
                                    v
                              server.py (game)
```

| Process | Owns | Restarts when |
|---------|------|----------------|
| `python -m engine.gateway` | Accept `:4000`, client sockets, session ids | Rarely (supervisor parent) |
| `python server.py` | World, ticks, Session logic | Code/content change, crash |

Env: `RIFTFORGE_GATEWAY=1` enables gateway mode (Docker default via
`watch_and_run`). `RIFTFORGE_GATEWAY=0` keeps direct telnet + in-process
copyover (Windows / learning).

**IPC must stay loopback** (`127.0.0.1:4001` default). Gateway and
`gateway_client` refuse a non-loopback `RIFTFORGE_GATEWAY_IPC` host unless
`RIFTFORGE_GATEWAY_IPC_ALLOW_NONLOCAL=1` (unsafe on live). Never publish
`:4001` in Docker/UFW — reattach skips the password by design.

## IPC framing

Length-prefixed frames (`uint32` big-endian length of body):

- **DATA** (`type=0x01`): 32-byte ASCII session id (hex uuid, no dashes) + raw telnet bytes
- **CTRL** (`type=0x02`): UTF-8 JSON

CTRL from game: `hello`, `bound` `{sid,name}`, `unbound` `{sid}`, `ping`
CTRL from gateway: `welcome` `{sessions:[{sid,name|null,peer?}]}`,
`open` `{sid,peer?}`, `close` `{sid}`

`peer` is the public telnet client host (string). The game Session writer
exposes it via `get_extra_info("peername")` for banlist + head-GM-only
`from …` staff pings; junior GMs do not see IPs on the ops channel.

## Reattach

1. Game connects to `:4001`, sends `hello`.
2. Gateway replies `welcome` with live sessions.
3. Game loads world from SQLite; for each session with a `name`, finds the
   Character and resumes `Session.play()` (no `disconnect()` — no Echo).
4. Sessions without a name reset to the login prompt.
5. New accepts while game is up: gateway `open` + DATA forward.

**Player warnings:** watch_and_run SIGUSR1s the game on code change (not a
silent kill). `copyover._perform` sends the Veil “hold on” line, saves,
then exits; reattach sends the Veil “still here” line. Bug-fix
`deploy_notify.on_resume` runs after gateway `welcome` (same as after
classic copyover resume).

**Hold music (elevator):** while game IPC is down and clients are held,
`engine/gateway.py` periodically writes plain `[WAIT]` Veil lines to
those sockets (grace default 5s, interval default 20s). Disable with
`RIFTFORGE_GATEWAY_HOLD_MUSIC=0`. This is gateway-side on purpose — the
dead game cannot speak. Shipping / editing gateway sources still
restarts gateway + game once (clients drop); after that, crash downtime
keeps clients and plays hold lines.

## Watcher

[`engine/watch_and_run.py`](../../engine/watch_and_run.py) with gateway on:

- Start gateway once, then game with `RIFTFORGE_GATEWAY=1`.
- On `.py` / content change: terminate **game only** (SIGTERM), respawn game.
- Never SIGUSR1 the gateway; never kill gateway on code edits.
- With gateway off: legacy SIGUSR1 copyover path unchanged.

## Non-goals

- Redis / multi-node / game logic in the gateway
- Removing bare `server.py` telnet mode
- Phase 5 remote split (separate)

## Verify

- Game kill/restart → logged-in clients stay; listener never dies.
- `RIFTFORGE_GATEWAY=0` → direct telnet + copyover still work.
- Full `smoke_test.py` green with gateway off (default).
