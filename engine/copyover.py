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
a small state file right before calling execv. Mid-create seats (chargen)
are snapshotted into the account draft vault and resume at the same prompt
instead of being dropped back to the name screen.

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
import time

STATE_PATH = ".copyover_state.json"
# Planned copyover wrote a full SQLite snapshot. The new Game() must not
# re-ensure town NPCs / lodging / civic housing (live stitch 3–7 min).
# Game-only reloads also skip via RIFTFORGE_BOOT_KIND=reload.
SKIP_DEFERRED_NAME = ".copyover_skip_deferred"
# Watcher must not SIGUSR1 until the new child can handle it (handler
# installed + veil open). Cleared on spawn so a previous process cannot
# look "ready".
READY_NAME = ".copyover_ready"
# Last failed rewrite after MSG_BEFORE (gm copyover last / MCP).
ABORT_LOG_NAME = ".copyover_last_abort.json"
# Last successful Veil-ready stamp (bug-report runtime honesty).
OK_LOG_NAME = ".copyover_last_ok.json"
# Append-only rewrite history (watch mtime, GM, abort). gm copyover last.
JOURNAL_NAME = ".copyover_journal.jsonl"
JOURNAL_KEEP = 80
JOURNAL_SHOW = 12


def _stamp_root(root=None):
    """Repo cwd, or ``RIFTFORGE_COPYOVER_STAMP_DIR`` for smokes."""
    if root:
        return root
    override = (os.environ.get("RIFTFORGE_COPYOVER_STAMP_DIR") or "").strip()
    if override:
        return override
    return os.getcwd()


def _skip_deferred_path(root=None):
    return os.path.join(_stamp_root(root), SKIP_DEFERRED_NAME)


def _ready_path(root=None):
    return os.path.join(_stamp_root(root), READY_NAME)


def abort_log_path(root=None):
    """Checkout file for the last cancelled Veil rewrite."""
    return os.path.join(_stamp_root(root), ABORT_LOG_NAME)


def ok_log_path(root=None):
    """Checkout file for the last successful Veil-ready stamp."""
    return os.path.join(_stamp_root(root), OK_LOG_NAME)


def journal_path(root=None):
    """Checkout JSONL of recent copyover triggers (mtime / GM / abort)."""
    return os.path.join(_stamp_root(root), JOURNAL_NAME)


def append_journal_event(
    kind,
    *,
    reason="",
    files=None,
    detail="",
    by="",
    root=None,
):
    """Append one rewrite-history row; keep the last JOURNAL_KEEP lines.

    ``kind`` is ``watch``, ``gm``, or ``abort``. Write failures are logged
    and ignored so a journal miss never blocks SIGUSR1 or a save abort.
    """
    payload = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": str(kind or "watch"),
    }
    reason = (reason or "").strip()
    if reason:
        payload["reason"] = reason[:240]
    detail = (detail or "").strip()
    if detail:
        payload["detail"] = detail[:400]
    by = (by or "").strip()
    if by:
        payload["by"] = by[:80]
    clean_files = []
    for path in list(files or ()):
        text = str(path or "").replace("\\", "/").strip()
        if text and text not in clean_files:
            clean_files.append(text)
        if len(clean_files) >= 40:
            break
    if clean_files:
        payload["files"] = clean_files
        payload["n_files"] = len(clean_files)
    path = journal_path(root)
    try:
        rows = []
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        rows.append(payload)
        rows = rows[-JOURNAL_KEEP:]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":")))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        print(
            f"[copyover] journal append failed path={path!r}: {exc!r}",
            flush=True,
        )
    return payload


def mark_skip_deferred_boot(*, root=None):
    """New process should skip deferred town re-seed (world already saved)."""
    path = _skip_deferred_path(root)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"{time.time():.3f}\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass


def consume_skip_deferred_boot(*, root=None):
    """True once per planned copyover; removes the stamp."""
    path = _skip_deferred_path(root)
    if not os.path.isfile(path):
        return False
    try:
        os.remove(path)
    except OSError:
        return False
    return True


def skip_deferred_boot_pending(*, root=None):
    """True when a planned copyover asked the next boot to skip deferred."""
    return os.path.isfile(_skip_deferred_path(root))


def clear_copyover_ready(*, root=None):
    """Drop the playable stamp (watcher calls this on every game spawn)."""
    try:
        os.remove(_ready_path(root))
    except OSError:
        pass


def mark_copyover_ready(*, root=None):
    """Veil is open / SIGUSR1 is safe. Watcher may queue the next reload."""
    path = _ready_path(root)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"{time.time():.3f}\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass
    # Best-effort ok stamp for bug-report runtime (does not block ready).
    record_last_ok(root=root)


def record_last_ok(*, root=None):
    """Persist when copyover became safe to queue (mirrors record_last_abort).

    Payload shape is ``{at, sha?}`` written to ``OK_LOG_NAME``. The optional
    ``sha`` is a cheap read of ``.git/HEAD`` (no subprocess) when available.
    Write failures are logged and ignored so copyover never aborts on this.
    """
    payload = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    sha = None
    try:
        # Lazy import avoids any copyover ↔ report_context cycle at import.
        from engine.report_context import _head_sha_cheap

        sha = _head_sha_cheap(root)
    except Exception:
        sha = None
    if sha:
        payload["sha"] = str(sha)
    path = ok_log_path(root)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        print(
            f"[copyover] ok log write failed path={path!r}: {exc!r}",
            flush=True,
        )
    return payload


def copyover_ready_since(spawn_wall, *, root=None):
    """True when ``.copyover_ready`` mtime is at or after this spawn."""
    path = _ready_path(root)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    if spawn_wall is None:
        return True
    return mtime >= (float(spawn_wall) - 1.0)


def record_last_abort(reason, *, detail="", traceback_text=""):
    """Persist why a rewrite stopped after MSG_BEFORE (best-effort)."""
    payload = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reason": str(reason or "unknown"),
        "detail": str(detail or "")[:4000],
        "traceback": str(traceback_text or "")[:8000],
    }
    path = abort_log_path()
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        print(
            f"[copyover] abort log write failed path={path!r}: {exc!r}",
            flush=True,
        )
    try:
        from engine import diag_export

        diag_export.append_copyover_abort_event(payload)
    except Exception as exc:
        print(f"[copyover] abort diag append failed: {exc!r}", flush=True)
    try:
        append_journal_event(
            "abort",
            reason=str(reason or "unknown"),
            detail=str(detail or "")[:400],
        )
    except Exception as exc:
        print(f"[copyover] abort journal append failed: {exc!r}", flush=True)
    return payload


def format_last_abort(*, root=None):
    """Staff-facing dump of the last cancelled Veil rewrite only."""
    path = abort_log_path(root)
    if not os.path.isfile(path):
        return (
            "No rewrite abort on file. Either the last Veil rewrite "
            "finished, or this checkout has not cancelled one yet.\n"
            "On live after a cancel, type gm copyover last in that game."
        )
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return f"Could not read rewrite abort log: {exc!r}"
    if not isinstance(data, dict):
        return "Rewrite abort log is not an object."
    at = data.get("at") or "?"
    reason = data.get("reason") or "unknown"
    detail = (data.get("detail") or "").strip()
    tb = (data.get("traceback") or "").strip()
    lines = [
        f"Last rewrite cancel: {at}",
        f"Reason: {reason}",
    ]
    if detail:
        lines.append("Detail:")
        lines.append(detail)
    if tb:
        lines.append("Trace:")
        lines.append(tb)
    return "\n".join(lines)


def _format_journal_row(row):
    """One staff line for a journal event."""
    at = row.get("at") or "?"
    kind = row.get("kind") or "watch"
    reason = (row.get("reason") or "").strip()
    by = (row.get("by") or "").strip()
    n_files = row.get("n_files")
    files = row.get("files") or []
    line = f"{at}  {kind}"
    if reason:
        line = f"{line}  {reason}"
    if by:
        line = f"{line}  by {by}"
    if files:
        shown = files[:8]
        extra = (n_files or len(files)) - len(shown)
        names = ", ".join(shown)
        if extra > 0:
            names = f"{names} +{extra} more"
        line = f"{line}\n    files: {names}"
    return line


def format_copyover_last(*, root=None):
    """Staff dump for ``gm copyover last``: journal + last ok + last abort.

    Mystery overnight Veils (bug report 1471) never hit the abort file --
    staff need the watcher's file list, not only cancel reasons.
    """
    lines = []
    journal = journal_path(root)
    rows = []
    if os.path.isfile(journal):
        try:
            with open(journal, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except OSError as exc:
            lines.append(f"Could not read rewrite journal: {exc!r}")
            rows = []
    if rows:
        lines.append("Recent rewrites (newest last):")
        for row in rows[-JOURNAL_SHOW:]:
            lines.append(_format_journal_row(row))
    else:
        lines.append(
            "No rewrite journal yet. Watcher copyovers, GM copyover, "
            "and cancelled Veils append here."
        )
    ok_path = ok_log_path(root)
    if os.path.isfile(ok_path):
        try:
            with open(ok_path, encoding="utf-8") as handle:
                ok = json.load(handle)
        except (OSError, json.JSONDecodeError):
            ok = None
        if isinstance(ok, dict):
            at = ok.get("at") or "?"
            sha = (ok.get("sha") or "")[:12]
            bit = f"Last ready stamp: {at}"
            if sha:
                bit = f"{bit}  sha={sha}"
            lines.append("")
            lines.append(bit)
    lines.append("")
    lines.append(format_last_abort(root=root))
    return "\n".join(lines)


def format_copyover_hub(*, root=None):
    """Staff dump for bare ``gm copyover``: command list + rewrite status.

    Bare copyover used to start a Veil (staff typed it expecting a log).
    The hub never rewrites; ``gm copyover start`` is the fire verb.
    """
    lines = [
        "copyover -- Veil rewrite hub (bare does not rewrite)",
        "",
        "  gm copyover              this sheet",
        "  gm copyover status      same as bare",
        "  gm copyover last        recent rewrites + last abort (also: why / log)",
        "  gm copyover start       hot-reload now (players stay connected)",
        "",
        format_copyover_last(root=root),
    ]
    return "\n".join(lines)


def _staff_abort_message(reason, detail):
    """One-line reason staff can type into ``gm copyover last``."""
    short = (detail or "").split("\n", 1)[0].strip()[:160]
    msg = f"rewrite cancelled ({reason}) -- gm copyover last"
    if short:
        msg = f"rewrite cancelled ({reason}: {short}) -- gm copyover last"
    return msg


def _notify_staff_abort(game, reason, detail):
    """[GM] ping + ops webhook + wiznet history so staff actually see it.

    Do **not** walk ``character.gm_rank == head_gm`` on the session body.
    After account migration that field is empty, and occupy (Gabriel / Ash)
    is an immersion cast -- those pings never arrived (live 2026-09-10).
    ``ping_gms`` uses ``_receives_staff_ops`` (account rank + playcast).
    """
    msg = _staff_abort_message(reason, detail)
    print(f"[copyover] {msg}", flush=True)
    if game is None:
        return
    try:
        from engine import gm_notify
        from engine import ops_webhook
    except Exception as exc:
        print(f"[copyover] abort notify skipped: {exc!r}", flush=True)
        return
    try:
        gm_notify.ping_gms(game, msg)
    except Exception as exc:
        print(f"[copyover] abort ping_gms failed: {exc!r}", flush=True)
    try:
        gm_notify.append_wiznet_history(game, f"[WIZ] {msg}")
    except Exception as exc:
        print(f"[copyover] abort wiznet history failed: {exc!r}", flush=True)
    try:
        ops_webhook.schedule_ops_alert(
            "copyover_abort",
            msg,
            reason=str(reason or ""),
            detail=str(detail or "")[:500],
        )
    except Exception as exc:
        print(f"[copyover] abort ops webhook failed: {exc!r}", flush=True)


def _cancel_rewrite(game, reason, detail=""):
    """Record + staff-ping + player MSG_CANCEL (HB-19).

    Also restarts the P30 persist writer if the terminating save already
    shut it down -- abort stays up, so later autosave must not fall back
    to on-loop apply until the next game-only restart.
    """
    import traceback as traceback_mod

    tb = traceback_mod.format_exc()
    if tb and tb.strip() == "NoneType: None":
        tb = ""
    record_last_abort(reason, detail=detail, traceback_text=tb)
    _notify_staff_abort(game, reason, detail)
    _announce_cancel(game)
    if game is not None:
        game._copyover_in_flight = False
    # Abort stays in this process -- do not leave .planned_restart armed or
    # the hang killer will skip forever (L-10 is only for an in-flight Veil).
    from engine import crash_recovery

    crash_recovery.clear_planned_restart()
    _restore_persistence_writer_after_abort(game)


def _restore_persistence_writer_after_abort(game):
    """Start the persist writer again after a Veil abort that stayed up.

    ``save_world_before_process_exit`` always ``shutdown_persistence_writer``
    before the sync snapshot. Skip-saved login PCs abort *after* that kill
    and keep the process running -- without this restart, ``save_async``
    applies SQLite on the asyncio loop until someone restarts the game.
    """
    if game is None:
        return
    try:
        from engine.lag_threads import sync_threads_to_env

        notes = sync_threads_to_env(game)
    except Exception as exc:
        print(
            f"[copyover] persist-writer restore after abort failed: {exc!r}",
            flush=True,
        )
        return
    for note in notes or ():
        print(f"[copyover] {note}", flush=True)

# Player-facing lines (plain tags -- never color alone). Shared by classic
# execv copyover, gateway graceful restart, and gateway reattach.
# Player-facing arc (keep in sync with deploy_notify / gateway hold music):
#   warn+countdown → countdown-end (still playable) → MSG_BEFORE →
#   [WAIT] while IPC down →
#   MSG_AFTER ([WAIT] reattached, still frozen) → soft stitch (optional) →
#   "Rewrite complete" after deferred boot. Reserve "thread holds" for
#   gateway hold music only — not reattach (players type verbs here).
MSG_BEFORE = (
    "*** The Veil shudders — hold on while the world rewrites. ***"
)
MSG_AFTER = (
    "*** [WAIT] You are still here — hold still; commands wait while "
    "the world stitches. ***"
)
MSG_CANCEL = (
    "*** Rewrite cancelled — you can keep playing. ***"
)


def _announce_cancel(game):
    """Tell every session the Veil rewrite did not finish (HB-19)."""
    for session in _iter_copyover_sessions(game):
        try:
            session.send(MSG_CANCEL)
        except Exception:
            pass


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

    Coalesce stacked SIGUSR1 / gm copyover while a Veil is already
    running -- a second ``_perform`` would send MSG_BEFORE twice, force-
    full save twice, and race abort vs exit.
    """
    if getattr(game, "_copyover_in_flight", False):
        print(
            "[copyover] already in flight -- ignoring stacked trigger",
            flush=True,
        )
        return
    game._copyover_in_flight = True
    asyncio.create_task(_perform(game))


def _iter_copyover_sessions(game):
    """Playing sessions plus mid-chargen connecting seats (unique)."""
    seen = []
    for bucket in (
        getattr(game, "sessions", None) or [],
        getattr(game, "connecting_sessions", None) or [],
    ):
        for session in list(bucket):
            if session not in seen:
                seen.append(session)
    return seen


async def _announce_before(game):
    """Send MSG_BEFORE to every logged-in or mid-create session and drain."""
    for session in _iter_copyover_sessions(game):
        if not session.character:
            continue
        session.send(MSG_BEFORE)
        try:
            await session.writer.drain()
        except (ConnectionResetError, BrokenPipeError, TimeoutError,
                ConnectionError, OSError, AttributeError) as exc:
            # Dead / half-open sockets must not abort the whole reload.
            key = getattr(session.character, "key", "?")
            print(
                f"[copyover] skipping dead session "
                f"({key}): {exc!r}",
                flush=True,
            )


def _heal_occupy_sessions_before_copyover_persist(game):
    """Point sessions at occupy cast bodies before save / bind rows."""
    from engine import hooks

    for session in _iter_copyover_sessions(game):
        char = getattr(session, "character", None)
        if char is None:
            continue
        try:
            hooks.heal_staff_occupy_session_desync(char, game)
        except Exception:
            pass


async def _flush_gateway_session_binds(game):
    """Push corrected reattach keys before gateway-mode copyover exit."""
    from engine.gateway_client import gateway_enabled
    from engine.session_bind import session_copyover_bind_name

    if not gateway_enabled():
        return
    pending = []
    for session in _iter_copyover_sessions(game):
        char = getattr(session, "character", None)
        if char is None:
            continue
        try:
            from engine import hooks

            char = hooks.heal_staff_occupy_session_desync(char, game)
        except Exception:
            pass
        bind = session_copyover_bind_name(session)
        if not bind or bind == "?":
            continue
        bridge = getattr(session, "gateway_bridge", None)
        sid = getattr(session, "gateway_session_id", None)
        if bridge is None or not sid:
            continue
        notify = getattr(bridge, "notify_bound", None)
        if not callable(notify):
            continue
        from engine import ooc_channel

        pending.append(
            notify(
                sid,
                bind,
                ooc_face=ooc_channel.speaker_face_for_session(session, game),
                head_gm=ooc_channel.session_is_head_gm(session, game),
                staff_gm=ooc_channel.session_is_staff_gm(session, game),
                gmcp_supports=dict(getattr(session, "gmcp_supports", None) or {}),
            )
        )
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


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


def clear_content_caches_for_copyover():
    """Drop in-memory catalog caches before copyover hook re-register.

    Auto-deploy overlays update ``earth_america.json`` and
    ``supers/content/dungeons/catalog.json`` on the bind-mount while this
    process still holds pre-overlay ``load_catalog()`` results.
    ``validate_mouth_alignment_at_boot`` reads the atlas fresh from disk but
    used the stale catalog cache — false drift aborts copyover after
    MSG_BEFORE (players see Veil lines then nothing happens).
    """
    from engine import hooks

    hooks.clear_content_caches_for_copyover()


def reload_world_save_modules():
    """Reload persistence + blob codec from disk before a copyover snapshot.

    Auto-deploy overlays land on the bind-mount while this process still
    holds older bytecode -- without a reload, ``game.save()`` would skip
    God twins and omit new blob fields (bug report 152).

    Reload ``engine.hooks``, then ``engine.char_identity``, then
    ``engine.persistence``. Persistence imports hook callables at module
    top; if we reload persistence first while the in-memory hooks module
    still lacks a newly added name (e.g. ``heal_force_unspirit``),
    ``importlib.reload`` raises ``ImportError`` and copyover aborts —
    the game process never exits, so overlays never become live bytecode
    (bug report 387).

    Persistence save-path helpers also import names from
    ``engine.char_identity`` (``is_account_linked_body``). Auto-deploy can
    land those names on disk while this process still holds the old
    identity module -- reload(persistence) then ImportError's after the
    Veil rewrite announce (live 2026-09-10, reason ``save``).

    Homestead collect/apply live in the game package. ``game_select``
    reloads that module after persistence so a new ``owner_cnum``
    INSERT cannot pair with an old wipe-rewrite that NULLs the column.

    ``supers.persist_meta`` imports the root ``persistence`` facade, not
    ``engine.persistence``. Auto-deploy can refresh ``engine.persistence``
    on disk while this process still holds a stale facade missing new
    ``save_*`` re-exports — copyover save then AttributeError's (e.g.
    ``save_ground_item_decay_tuning``).
    """
    import importlib

    from engine import hooks
    from engine import persistence as ep

    # Catalog rebuild must not abort the rewrite. A vocab mismatch here
    # means overlays landed new JSON before this process imported the new
    # validators -- the child is about to reload hooks and exit; the next
    # Game() loads catalogs from disk. Swallow, log, keep going.
    try:
        clear_content_caches_for_copyover()
    except Exception as exc:
        import traceback

        print(
            f"[copyover] catalog cache rebuild failed (continuing; "
            f"next process loads fresh): {exc!r}",
            flush=True,
        )
        traceback.print_exc()

    # Hooks first: persistence's top-level ``from engine.hooks import …``
    # must see the on-disk hooks module, not the pre-overlay cache.
    importlib.reload(hooks)
    # Identity next: persistence function-level imports bind to the
    # in-memory module object. Reload it from disk before persistence.
    from engine import char_identity as ci

    importlib.reload(ci)
    ep = importlib.reload(ep)
    import persistence as persistence_facade

    importlib.reload(persistence_facade)
    # reload(hooks) clears bootstrap callbacks; re-register the active game's
    # persist_blob codec before copyover save (empty blobs wiped live, #2096).
    try:
        import game_select

        game_select.reregister_hooks_after_reload()
    except ImportError:
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

    from engine import persistence

    await persistence.wait_collect_idle_async(game)
    await persistence.wait_writer_idle_async()

    from engine.gateway_client import gateway_enabled

    await _announce_before(game)

    # Persist the world NOW -- the new process's Game.__init__ reloads from
    # disk, so whatever isn't saved here is lost, same as any other restart.
    try:
        reload_world_save_modules()
    except Exception as exc:
        import traceback

        print(
            f"[copyover] module reload failed -- restoring gameplay hooks "
            f"and staying up: {exc!r}",
            flush=True,
        )
        traceback.print_exc()
        try:
            import game_select

            game_select.restore_hooks_after_copyover_abort()
            print(
                "[copyover] restored full hooks after module reload failure",
                flush=True,
            )
        except Exception as restore_exc:
            print(
                f"[copyover] failed to restore hooks after module reload "
                f"failure: {restore_exc!r}",
                flush=True,
            )
        _cancel_rewrite(
            game, "module_reload", detail=repr(exc),
        )
        return
    from engine import hooks as _hooks

    if not _hooks.blob_codec_registered():
        print(
            "[copyover] ABORT: blob codec not registered after module reload; "
            "refusing to save (would wipe character blobs)",
            flush=True,
        )
        # reload(hooks) already cleared attachers / combat / chargen. Do not
        # return into a half-wired event loop — restore full game hooks so
        # the process stays playable until the next real restart.
        try:
            import game_select

            game_select.restore_hooks_after_copyover_abort()
            print(
                "[copyover] restored full hooks after abort "
                "(no save; process continues)",
                flush=True,
            )
        except Exception as exc:
            print(
                f"[copyover] failed to restore hooks after abort: {exc!r}",
                flush=True,
            )
        _cancel_rewrite(
            game,
            "blob_codec",
            detail="blob codec not registered after module reload",
        )
        return
    try:
        from engine import account_chargen_draft as draft_mod

        n_drafts = draft_mod.persist_creating_sessions(game)
        if n_drafts:
            print(
                f"[copyover] snapshotted {n_drafts} mid-chargen session(s)",
                flush=True,
            )
        await draft_mod.flush_creating_session_binds(game)
    except Exception as exc:
        print(
            f"[copyover] mid-chargen snapshot failed (continuing): {exc!r}",
            flush=True,
        )
    try:
        # SQLite busy/locked during a laggy rewrite used to cancel the
        # Veil after one try. Retry a couple of times before aborting.
        _heal_occupy_sessions_before_copyover_persist(game)
        last_save_exc = None
        for attempt in range(3):
            try:
                game.save(copyover=True)
                last_save_exc = None
                break
            except Exception as save_exc:
                last_save_exc = save_exc
                msg = str(save_exc).lower()
                busy = "busy" in msg or "locked" in msg
                if not busy or attempt >= 2:
                    raise
                print(
                    f"[copyover] save busy (attempt {attempt + 1}/3) -- retrying: "
                    f"{save_exc!r}",
                    flush=True,
                )
                await asyncio.sleep(0.4 * (attempt + 1))
        if last_save_exc is not None:
            raise last_save_exc
        stats = getattr(game, "_last_world_save_stats", None) or {}
        if stats.get("skipped") or stats.get("writer_pending"):
            raise RuntimeError(
                "copyover save did not apply world snapshot "
                f"(stats={stats!r})"
            )
    except Exception as exc:
        import traceback

        print(
            f"[copyover] save failed -- restoring gameplay hooks and staying up: "
            f"{exc!r}",
            flush=True,
        )
        traceback.print_exc()
        try:
            import game_select

            game_select.restore_hooks_after_copyover_abort()
            print(
                "[copyover] restored full hooks after save failure "
                "(process continues; schedule game-only restart)",
                flush=True,
            )
        except Exception as restore_exc:
            print(
                f"[copyover] failed to restore hooks after save failure: "
                f"{restore_exc!r}",
                flush=True,
            )
        _cancel_rewrite(
            game, "save", detail=repr(exc),
        )
        return
    mark_skip_deferred_boot()

    if gateway_enabled():
        await _flush_gateway_session_binds(game)
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
    inheritable_fds = []
    for session in _iter_copyover_sessions(game):
        if not session.character:
            # Still on the name/password prompt -- nothing to reattach to.
            continue
        sock = session.writer.get_extra_info("socket")
        if sock is None:
            continue
        fd = sock.fileno()
        os.set_inheritable(fd, True)   # survive the execv() below
        inheritable_fds.append(fd)
        # Freeze the login body name -- never gmspirit:Key -- so resume
        # finds the corporeal Character; after_session_attach restores
        # gm on when gm_staff_form is set. Cast occupy uses Gabriel, not
        # the ranked PC on the parked spirit (bug report 1380).
        from engine import hooks
        from engine.session_bind import session_copyover_bind_name

        try:
            hooks.heal_staff_occupy_session_desync(session.character, game)
        except Exception:
            pass
        bind_name = session_copyover_bind_name(session)
        if not bind_name:
            continue
        creating = getattr(session, "login_stage", None) == "creating"
        # Playing bodies with a leftover chargen_draft flag stay in play
        # (bug report 837). Mid-create seats use login_stage == "creating".
        entries.append({"fd": fd, "name": bind_name, "chargen": bool(creating)})

    with open(STATE_PATH, "w") as f:
        json.dump(entries, f)
        f.flush()
        os.fsync(f.fileno())

    try:
        os.execv(sys.executable, [sys.executable, os.path.abspath(sys.argv[0]),
                                   "--copyover", STATE_PATH])
    except OSError as e:
        for fd in inheritable_fds:
            try:
                os.set_inheritable(fd, False)
            except OSError:
                pass
        # execv failed to even start (should be very rare) -- the current
        # process is still fully intact at this point, so keep running on
        # the old code rather than losing the whole server.
        print(f"[copyover] execv failed, staying on current code: {e}", flush=True)
        # reload_world_save_modules() cleared gameplay hooks; restore so
        # staff GM form / Monster night-sight work until the next restart.
        try:
            import game_select

            game_select.restore_hooks_after_copyover_abort()
            print(
                "[copyover] restored full hooks after execv failure",
                flush=True,
            )
        except Exception as exc:
            print(
                f"[copyover] failed to restore hooks after execv failure: "
                f"{exc!r}",
                flush=True,
            )
        try:
            os.remove(STATE_PATH)
        except OSError:
            pass
        _cancel_rewrite(game, "execv", detail=repr(e))


def sanitize_copyover_entries(entries):
    """Keep only classic-copyover rows with a usable name and integer fd.

    One malformed state row used to abort the whole resume loop (missing
    ``name`` returned early; missing or string ``fd`` KeyError/TypeError
    after the state file was already deleted). Skip the bad row instead.
    """
    if not isinstance(entries, list):
        return []
    usable = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            fileno = int(entry.get("fd"))
        except (TypeError, ValueError):
            continue
        if fileno < 0:
            continue
        row = dict(entry)
        row["name"] = name.strip()
        row["fd"] = fileno
        usable.append(row)
    return usable


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
    except (OSError, ValueError):
        # Missing or corrupt state file -- nothing we can do but boot
        # normally, same fail-soft spirit as persistence.py's .get(...,
        # default) fallbacks elsewhere in this codebase.
        return

    if not isinstance(entries, list):
        return
    entries = sanitize_copyover_entries(entries)
    if not entries:
        try:
            os.remove(path)
        except OSError:
            pass
        return

    try:
        os.remove(path)
    except OSError:
        return

    # Imported here (not at module level) to avoid a circular import:
    # connection.py doesn't import copyover.py, so this is one-directional.
    from engine.connection import Session
    from engine import account_chargen_draft as draft_mod
    from engine import hooks

    for entry in entries:
        name = entry["name"]
        want_chargen = bool(entry.get("chargen"))
        chargen_char = None
        if want_chargen or hooks.is_chargen_draft_vault(game, name):
            chargen_char = draft_mod.resolve_copyover_chargen_body(game, name)
        if chargen_char is not None:
            try:
                sock = socket.socket(fileno=entry["fd"])
                reader, writer = await asyncio.open_connection(sock=sock)
            except (OSError, TypeError, ValueError):
                continue
            session = Session(reader, writer, game)
            session.character = chargen_char
            chargen_char.session = session
            session._set_creating()
            session.reset_gmcp()
            from engine import gmcp

            # No MSSP re-offer here (bug report 1219 class): this socket
            # already negotiated MSSP once at original connect; the classic
            # execv copyover keeps the same fd, so re-sending WILL MSSP is
            # pure re-negotiation noise a client should never see twice.
            gmcp.offer_gmcp(session)
            session.send(MSG_AFTER)
            asyncio.create_task(_resume_chargen(session))
            continue
        # Prefer exact login body (never husk: / gmspirit: leftovers).
        finder = getattr(game, "find_login_character", None)
        if callable(finder):
            char = finder(name)
        else:
            char = game.find_character(name)
        if not char:
            # Missing body (vaulted, deleted, or heal): never orphan the
            # inherited FD (HB-04). Gateway reattach already falls through
            # to login; classic copyover must match.
            try:
                sock = socket.socket(fileno=entry["fd"])
                reader, writer = await asyncio.open_connection(sock=sock)
            except (OSError, TypeError, ValueError):
                continue
            session = Session(reader, writer, game)
            game.sessions.append(session)
            session.send(MSG_AFTER)
            asyncio.create_task(_resume_login(session))
            continue
        try:
            sock = socket.socket(fileno=entry["fd"])
            reader, writer = await asyncio.open_connection(sock=sock)
        except (OSError, TypeError, ValueError):
            continue   # the client hung up during the reload window

        session = Session(reader, writer, game)
        session.character = char
        char.session = session
        from engine.session_attach import detach_stale_sessions

        detach_stale_sessions(char, game, session)
        if session not in game.sessions:
            game.sessions.append(session)
        # Socket survives copyover; GMCP state does not -- re-offer once.
        # MSSP is deliberately NOT re-offered: it was already negotiated on
        # this same fd at original connect, and resending WILL MSSP on an
        # already-negotiated socket every copyover crashed a screenreader
        # TinTin++ client (bug report 1219).
        session.reset_gmcp()
        from engine import gmcp
        gmcp.offer_gmcp(session)
        # Same post-attach hook as a normal login (pending Tier break, mail
        # notify, GMCP Char vitals/status). Without this, copyover resumes
        # skip mail/GMCP that login would have pushed. Also restores
        # `gm on` when the body has gm_staff_form.
        hooks.after_session_attach(char, game)
        session.send(MSG_AFTER)
        # Skip reprinting the login MOTD board on copyover resume.
        session._copyover_resume = True
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


async def _resume_chargen(session):
    """Re-enter create-flow after classic copyover kept the socket."""
    try:
        from engine.account_login import resume_chargen_after_hold

        result = await resume_chargen_after_hold(session)
        if result is None:
            return
        await session.play()
    except (ConnectionResetError, BrokenPipeError):
        session.disconnect()


async def _resume_client(session):
    """Run a resumed session's command loop, same crash-tolerance as
    server.py's handle_client for a normal connection."""
    try:
        await session.play()
    except (ConnectionResetError, BrokenPipeError):
        session.disconnect()
