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

Gateway mode (RIFTFORGE_GATEWAY=1): SIGUSR1 still runs _perform, but after
announcing MSG_BEFORE and saving, the game exits so watch_and_run can
respawn it. Client TCP stays on engine.gateway; reattach sends MSG_AFTER.

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

# Player-facing lines (plain tags -- never color alone). Shared by classic
# execv copyover, gateway graceful restart, and gateway reattach.
MSG_BEFORE = (
    "*** The Veil shudders. Something ancient is rewriting the bones of "
    "this world. Hold on. ***"
)
MSG_AFTER = (
    "*** The Veil settles. You are still here. ***"
)


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


async def _announce_before(game):
    """Send MSG_BEFORE to every logged-in session and drain (best-effort)."""
    for session in list(game.sessions):
        if not session.character:
            continue
        session.send(MSG_BEFORE)
        try:
            await session.writer.drain()
        except (ConnectionResetError, BrokenPipeError, TimeoutError,
                ConnectionError, OSError) as exc:
            # Dead / half-open sockets must not abort the whole reload.
            print(
                f"[copyover] skipping dead session "
                f"({session.character.key}): {exc!r}",
                flush=True,
            )


async def _notify_gateway_planned_restart(game):
    """Best-effort CTRL to the gateway: this exit is a planned reload.

    Looks on ``game.gateway_bridge`` first (set by server.py), then any
    live session's bridge. Fail-soft -- missing bridge still exits; Discord
    grace alone covers short restarts.
    """
    bridge = getattr(game, "gateway_bridge", None)
    if bridge is None:
        for session in list(getattr(game, "sessions", None) or []):
            bridge = getattr(session, "gateway_bridge", None)
            if bridge is not None:
                break
    if bridge is None:
        return
    notify = getattr(bridge, "notify_planned_restart", None)
    if not callable(notify):
        return
    try:
        await notify()
    except Exception as exc:
        print(
            f"[copyover] planned_restart notify failed: {exc!r}",
            flush=True,
        )


def reload_world_save_modules():
    """Reload persistence + blob codec from disk before a copyover snapshot.

    Auto-deploy overlays land on the bind-mount while this process still
    holds older bytecode -- without a reload, ``game.save()`` would skip
    God twins and omit new blob fields (bug report 152).
    """
    import importlib

    from engine import hooks
    from engine import persistence as ep

    ep = importlib.reload(ep)
    from engine import hooks
    hooks.reload_blob_codec()
    return ep


async def _perform(game):
    """Announce, save, then hot-reload.

    Two modes:
      - Direct telnet (no gateway): freeze fds, write state, os.execv
        (classic copyover -- never returns on success).
      - Gateway mode: clients stay on the gateway. Announce MSG_BEFORE,
        save, then exit so watch_and_run can respawn the game; reattach
        sends MSG_AFTER. Do not execv (no inheritable client fds here).
    """
    print("[copyover] reload requested -- freezing connections briefly", flush=True)

    from engine import crash_recovery
    crash_recovery.mark_planned_restart()

    from engine.gateway_client import gateway_enabled

    await _announce_before(game)

    # Persist the world NOW -- the new process's Game.__init__ reloads from
    # disk, so whatever isn't saved here is lost, same as any other restart.
    reload_world_save_modules()
    game.save(copyover=True)

    if gateway_enabled():
        # Watcher holds :4000; exiting is the reload. Players already got
        # MSG_BEFORE; MSG_AFTER lands on gateway reattach in connection.py.
        # Tell the gateway first so WKNZ Discord does not treat this as a
        # crash (auto-deploy / code watch restarts are routine).
        await _notify_gateway_planned_restart(game)
        print(
            "[copyover] gateway mode -- exiting for watcher respawn "
            "(clients held)",
            flush=True,
        )
        # Hard exit from an asyncio task (sys.exit alone would not stop PID 1
        # child cleanly enough for the watcher to reap immediately).
        os._exit(0)
        return  # unreachable after _exit; kept for tests that stub _exit

    entries = []
    for session in list(game.sessions):
        if not session.character:
            # Still on the name/password prompt -- nothing to reattach to.
            continue
        sock = session.writer.get_extra_info("socket")
        if sock is None:
            continue
        fd = sock.fileno()
        os.set_inheritable(fd, True)   # survive the execv() below
        # Freeze the login body name -- never gmspirit:Key -- so resume
        # finds the corporeal Character; after_session_attach restores
        # gm on when gm_staff_form is set.
        from engine.command_support import strip_ephemeral_storage_prefix
        actor = session.character
        bind_name = getattr(actor, "gm_body_key", None) or actor.key
        bind_name = strip_ephemeral_storage_prefix(bind_name)
        entries.append({"fd": fd, "name": bind_name})

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
        # Prefer exact login body (never husk: / gmspirit: leftovers).
        finder = getattr(game, "find_login_character", None)
        if callable(finder):
            char = finder(entry["name"])
        else:
            char = game.find_character(entry["name"])
        if not char:
            # Unfinished homezone lesson: boot heal vaulted the body; do not
            # drop the socket -- send them through login instead.
            from engine import hooks

            if hooks.is_tutorial_incomplete_vault(game, entry["name"]):
                try:
                    sock = socket.socket(fileno=entry["fd"])
                    reader, writer = await asyncio.open_connection(sock=sock)
                except OSError:
                    continue
                session = Session(reader, writer, game)
                game.sessions.append(session)
                session.send(MSG_AFTER)
                asyncio.create_task(_resume_login(session))
            continue
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
        from engine import hooks
        gmcp.offer_gmcp(session)
        mssp.offer_mssp(session)
        # Same post-attach hook as a normal login (pending Tier break, mail
        # notify, GMCP Char vitals/status). Without this, copyover resumes
        # skip mail/GMCP that login would have pushed. Also restores
        # `gm on` when the body has gm_staff_form.
        hooks.after_session_attach(char, game)
        session.send(MSG_AFTER)
        asyncio.create_task(_resume_client(session))

    # If a bug-fix deploy was in flight, announce it is live and mark resolved.
    from engine import deploy_notify
    await deploy_notify.on_resume(game)


async def _resume_login(session):
    """Run login prompts after copyover when the body was tutorial-folded."""
    try:
        await session.run()
    except (ConnectionResetError, BrokenPipeError):
        session.disconnect()


async def _resume_client(session):
    """Run a resumed session's command loop, same crash-tolerance as
    server.py's handle_client for a normal connection."""
    try:
        await session.play()
    except (ConnectionResetError, BrokenPipeError):
        session.disconnect()
