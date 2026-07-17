"""
copyover.py -- staying connected through a hot code reload ("copyover", the
classic MUD term for this technique).

The problem: watch_and_run.py reloads server.py the instant a .py file
changes, but a normal reload (start a fresh process, let the old one exit)
closes every client's TCP socket when the old process exits -- there is no
way around that once a process is gone, the OS reclaims every file
descriptor it held. Players have to reconnect and log back in every time.

The fix: instead of exiting, the OLD process REPLACES ITS OWN PROGRAM IMAGE
in place via os.execv() -- same PID, same open file descriptors, brand new
code. A Python socket is normally marked "close on exec" by default (PEP
446, since Python 3.4), which is exactly why the LISTENING socket harmlessly
vanishes on its own during execv (freeing port 4000 for the new process to
rebind) -- but we deliberately flip that flag OFF (os.set_inheritable) for
each CONNECTED client's socket, so those specific ones survive the
replacement. The new process then re-wraps each surviving socket into a
fresh Session and resumes it directly, skipping login -- it already knows
which character was on which connection, because we wrote that mapping to
a small state file right before calling execv.

Two distinct signals, two distinct meanings (see server.py):
  SIGINT  -- a real shutdown (Ctrl-C, `docker stop`): save and exit for good.
  SIGUSR1 -- "hot-reload in place": freeze connections, dump state, execv.

Unix-only (execv/SIGUSR1/fd inheritance are POSIX concepts) -- on Windows,
install_signal_handler() is a no-op, so start-server.bat's plain
`python server.py` usage is completely unaffected either way.

Deliberately NOT covered here (see HANDOFF.md for the reasoning):
- The listening socket itself isn't preserved -- a brand-new connection
  attempt in the split second before the new process rebinds just gets
  refused and has to retry once.
- A genuine crash (not a copyover) still falls through to
  watch_and_run.py's plain respawn-a-new-process fallback, where
  reconnecting is unavoidable -- there's no live process left to save from.
"""

import asyncio
import json
import os
import signal
import socket
import sys

STATE_PATH = ".copyover_state.json"


def install_signal_handler(game):
    """Wire SIGUSR1 up to trigger a copyover. Call once from server.py's
    main(), after the event loop is running (loop.add_signal_handler needs
    a running loop -- it hooks the signal via asyncio's self-pipe trick
    instead of a raw signal.signal(), so it never interrupts a coroutine
    mid-step; it just schedules _perform to run at the next safe point).
    """
    if not hasattr(signal, "SIGUSR1"):
        return   # Windows -- no POSIX signals, copyover simply isn't available
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGUSR1, lambda: trigger(game))


def trigger(game):
    """Schedule a copyover to run at the next safe point in the event loop.
    The one public entry point both the SIGUSR1 handler above and the GM
    `copyover` command (commands.py) call -- neither runs inside a
    coroutine itself (a signal callback and a synchronous command handler,
    respectively), so both need create_task rather than an `await`.
    """
    asyncio.create_task(_perform(game))


async def _perform(game):
    """Freeze every connected session, dump enough state to reattach them,
    and replace this process with a fresh one running the current code on
    disk. Never returns on success -- execv() replaces the running program
    entirely. On failure (execv itself raised), logs it and returns,
    leaving the OLD process running rather than crashing.
    """
    print("[copyover] reload requested -- freezing connections briefly", flush=True)

    entries = []
    for session in list(game.sessions):
        if not session.character:
            # Still on the name/password prompt -- nothing to reattach to.
            # This connection just closes when execv() replaces the process;
            # a rare, accepted edge case (see copyover.py's module docstring).
            continue
        session.send("*** The world flickers -- reality is reforming. Hold on. ***")
        try:
            await session.writer.drain()
        except (ConnectionResetError, BrokenPipeError, TimeoutError,
                ConnectionError, OSError) as exc:
            # Dead / half-open sockets raise more than reset/pipe -- live
            # hit TimeoutError (errno 110) on drain and aborted the whole
            # copyover before execv. Skip this session; keep reloading.
            print(f"[copyover] skipping dead session "
                  f"({session.character.key}): {exc!r}", flush=True)
            continue

        sock = session.writer.get_extra_info("socket")
        if sock is None:
            continue
        fd = sock.fileno()
        os.set_inheritable(fd, True)   # survive the execv() below
        entries.append({"fd": fd, "name": session.character.key})

    # Persist the world NOW -- the new process's Game.__init__ reloads from
    # disk, so whatever isn't saved here is lost, same as any other restart.
    game.save()

    with open(STATE_PATH, "w") as f:
        json.dump(entries, f)

    try:
        os.execv(sys.executable, [sys.executable, os.path.abspath(sys.argv[0]),
                                   "--copyover", STATE_PATH])
    except OSError as e:
        # execv failed to even start (should be very rare) -- the current
        # process is still fully intact at this point, so keep running on
        # the old code rather than losing the whole server.
        print(f"[copyover] execv failed, staying on current code: {e}", flush=True)
        try:
            os.remove(STATE_PATH)
        except OSError:
            pass


async def resume(game):
    """Called once from server.py's main(), right after the fresh
    asyncio.start_server() call. A no-op unless this process was just
    exec'd by _perform() above (`--copyover <path>` in sys.argv) -- in
    which case it re-wraps every preserved socket into a Session attached
    directly to its character, skipping login/name/password entirely.
    """
    if "--copyover" not in sys.argv:
        return
    path = sys.argv[sys.argv.index("--copyover") + 1]

    try:
        with open(path) as f:
            entries = json.load(f)
        os.remove(path)
    except (OSError, ValueError):
        # Missing or corrupt state file -- nothing we can do but boot
        # normally, same fail-soft spirit as persistence.py's .get(...,
        # default) fallbacks elsewhere in this codebase.
        return

    # Imported here (not at module level) to avoid a circular import:
    # connection.py doesn't import copyover.py, so this is one-directional.
    from engine.connection import Session

    for entry in entries:
        char = game.find_character(entry["name"])
        if not char:
            continue   # shouldn't happen -- the world was just reloaded from
                       # the save _perform() made moments before execv
        try:
            sock = socket.socket(fileno=entry["fd"])
            reader, writer = await asyncio.open_connection(sock=sock)
        except OSError:
            continue   # the client hung up during the reload window

        session = Session(reader, writer, game)
        session.character = char
        char.session = session
        game.sessions.append(session)
        # Sockets survive copyover; GMCP/MSSP negotiation does not -- re-offer.
        session.reset_gmcp()
        from engine import gmcp
        from engine import mssp
        gmcp.offer_gmcp(session)
        mssp.offer_mssp(session)
        session.send("*** The world reforms around you. You're still here. ***")
        asyncio.create_task(_resume_client(session))

    # If a bug-fix deploy was in flight, announce it is live and mark resolved.
    from engine import deploy_notify
    await deploy_notify.on_resume(game)


async def _resume_client(session):
    """Run a resumed session's command loop, same crash-tolerance as
    server.py's handle_client for a normal connection."""
    try:
        await session.play()
    except (ConnectionResetError, BrokenPipeError):
        session.disconnect()
