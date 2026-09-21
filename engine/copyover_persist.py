"""
copyover_persist.py -- one complete persist after the Veil pause line.

Deploy countdown (T-minus) is playable and does **not** write SQLite.
``copyover._perform`` announces MSG_BEFORE, then runs exactly one
cooperative ``force_full`` snapshot (yields + ``touch_heartbeat("persist")``)
before exit. Completeness stamps sit beside ``.copyover_skip_deferred``.
The child must not skip town/catalog re-seed unless this process proved
the snapshot.
"""

from __future__ import annotations

import os
import time


COMPLETE_STAMP_NAME = ".copyover_persist_complete"


def _stamp_root(root=None):
    """Same root as copyover skip-deferred stamps."""
    from engine import copyover as copyover_mod

    return copyover_mod._stamp_root(root)


def persist_complete_path(root=None):
    """Checkout file written after a successful seal persist."""
    return os.path.join(_stamp_root(root), COMPLETE_STAMP_NAME)


def mark_persist_complete(*, root=None, stats=None):
    """Child may skip deferred heals only when this stamp exists."""
    path = persist_complete_path(root)
    n_chars = 0
    n_sidecar = 0
    if isinstance(stats, dict):
        n_chars = int(stats.get("n_char_rows") or 0)
        n_sidecar = int(stats.get("n_sidecar_rows") or 0)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                f"{time.time():.3f} chars={n_chars} sidecar={n_sidecar}\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        print(
            f"[copyover_persist] completeness stamp failed: {exc!r}",
            flush=True,
        )
        raise


def consume_persist_complete(*, root=None):
    """True once when the parent sealed a complete snapshot; removes stamp."""
    path = persist_complete_path(root)
    if not os.path.isfile(path):
        return False
    try:
        os.remove(path)
    except OSError:
        return False
    return True


def persist_complete_pending(*, root=None):
    """True when a sealed copyover persist is waiting for the child."""
    return os.path.isfile(persist_complete_path(root))


def clear_persist_complete(*, root=None):
    """Disarm the stamp when the rewrite aborts after the seal.

    The seal stamps completeness, then the process still has to exit
    (gateway bind flush / ``os.execv``). If that last step fails the
    abort keeps playing and the world moves on -- a stamp left on disk
    would let some later crash-respawn skip deferred heals against a
    stale snapshot. Same reason ``_cancel_rewrite`` clears
    ``.planned_restart``.
    """
    try:
        os.remove(persist_complete_path(root))
    except OSError:
        pass


def _world_save_stats_copy(game):
    """Shallow copy of the last world-save stamp.

    Aux flush and login checkpoints can overwrite
    ``game._last_world_save_stats`` with a skipped empty force_full
    (routine refuse / parking skip). Copyover must log and stamp the
    world snapshot that already applied, not that later clobber.
    """
    stats = getattr(game, "_last_world_save_stats", None)
    if isinstance(stats, dict):
        return dict(stats)
    return {}


def _log_persist_counts(label, stats):
    """Print roster + sidecar sizes -- lag_watch chars= is dirty-count only."""
    stats = stats or {}
    n_chars = stats.get("n_char_rows")
    n_items = stats.get("n_item_rows")
    n_sidecar = stats.get("n_sidecar_rows")
    skipped = bool(stats.get("skipped"))
    print(
        f"[copyover_persist] {label} "
        f"n_char_rows={n_chars} n_item_rows={n_items} "
        f"n_sidecar_rows={n_sidecar} skipped={skipped}",
        flush=True,
    )


def _require_world_applied(game, *, label):
    """Raise when the last world snapshot did not land on SQLite."""
    stats = getattr(game, "_last_world_save_stats", None) or {}
    if stats.get("skipped"):
        detail = (
            stats.get("empty_force_full") and "empty_force_full"
            or stats.get("short_force_full") and "short_force_full"
            or stats.get("short_sidecar") and "short_sidecar"
            or stats.get("short_room_items") and "short_room_items"
            or stats.get("wall_budget") and "wall_budget"
            or "skipped"
        )
        raise RuntimeError(
            f"{label} world snapshot did not apply ({detail})"
        )
    if stats.get("writer_pending"):
        raise RuntimeError(
            f"{label} left persist writer pending -- snapshot not acked"
        )
    skipped_login = list(stats.get("skipped_login_names") or ())
    if skipped_login:
        raise RuntimeError(
            f"{label} skipped login bodies {skipped_login}"
        )
    if getattr(game, "_persist_heavy_sidecar_failed", False):
        raise RuntimeError(f"{label} heavy sidecar collect failed")


def _supers_is_active():
    """True when this process is the SUPERS game (site/parking tables exist).

    Asked up front, from ``game_select``, instead of inferring "lean
    engine" from an ``ImportError``. A real import failure inside a saver
    (bad overlay, circular import mid-rewrite -- bug report 387) must abort
    the warmup, not be read as "no SUPERS here" and silently skip every
    remaining table.
    """
    try:
        import game_select

        return game_select.game_name() == "supers"
    except Exception:
        return False


def _flush_live_map_backups(*, root=None):
    """Snapshot live map/zone JSON into protected ``content/map_backups/``.

    Remodel / populate already refresh one slot after each save. Warmup
    does the full set so a missed refresh cannot revert a house when
    auto-deploy resets live maps to git and boot merges the backups.
    """
    from engine import map_backups as map_backups_mod

    summary = map_backups_mod.write_backup_all(root=root)
    first = (summary or "").splitlines()[0] if summary else "ok"
    print(f"[copyover_persist] {first}", flush=True)
    # Backup rewrites dirtied backup mtimes; stamp so boot_kind=reload does
    # not redo the full heal walk (see engine.map_heal.stamp_heal_fingerprint).
    import importlib

    from engine import map_heal as map_heal_mod

    map_heal_mod = importlib.reload(map_heal_mod)
    stamp = getattr(map_heal_mod, "stamp_heal_fingerprint", None)
    if stamp is not None:
        stamp(root=root)
    return summary


def _touch_persist_heartbeat():
    """Watcher liveness during long copyover SQLite (scoped hang hold)."""
    try:
        from engine import game_heartbeat as game_heartbeat_mod

        game_heartbeat_mod.touch_heartbeat("persist")
    except Exception:
        pass


async def flush_copyover_aux_async(game, *, force=True):
    """Force-write clock, accounts, every meta slice, sites, and parking.

    Copyover uses the async meta path (complete ``force_full``, no
    stagger-skip). Homestead and other site tables yield between plots.
    """
    import asyncio

    if game is None:
        raise RuntimeError("flush_copyover_aux_async: no game")
    conn = getattr(game, "db", None)
    if conn is None:
        raise RuntimeError("flush_copyover_aux_async: game.db is not open")
    from engine import persistence as persist_mod
    from engine import hooks as hooks_mod

    await asyncio.sleep(0)
    _touch_persist_heartbeat()
    persist_mod.save_game_time(conn, getattr(game, "game_time_ticks", 0))
    persist_mod.save_calendar_epoch_day(
        conn, getattr(game, "calendar_epoch_day", 0),
    )
    persist_mod.save_game_clock_tuning(conn, game)
    persist_mod.save_accounts(conn, game, force_full=True)
    await asyncio.sleep(0)
    _touch_persist_heartbeat()

    if _supers_is_active():
        # Site tables live in the game package (supers/copyover_aux.py).
        # A missing hook must abort: skipping them used to look like a
        # lean boot and drop homestead / parking rows (bug report 387).
        ran = await hooks_mod.flush_copyover_aux_game(game, conn)
        if not ran:
            raise RuntimeError(
                "copyover aux: game is active but no copyover aux flusher "
                "is registered"
            )
    else:
        hooks_mod.save_game_meta(game, conn, force_full=True)

    _flush_live_map_backups()
    meta_stats = getattr(game, "_last_game_meta_save_stats", None) or {}
    if meta_stats and not meta_stats.get("force_full"):
        raise RuntimeError(
            "copyover aux meta save was not force_full"
        )
    game._copyover_aux_flushed = True


def flush_copyover_aux(game, *, force=True):
    """Sync aux flush for tests and legacy callers."""
    import asyncio

    asyncio.run(flush_copyover_aux_async(game, force=force))


def _begin_required_sidecar(game):
    """Sidecar collect exceptions abort seal instead of skip."""
    game._persist_copyover_warmup = True
    game._persist_heavy_sidecar_required = True
    game._persist_heavy_sidecar_failed = False


def _end_required_sidecar(game):
    """Clear copyover persist flags after seal completes or aborts."""
    game._persist_copyover_warmup = False
    game._persist_heavy_sidecar_required = False


def _prepare_copyover_world_save(game):
    """Same pre-exit world arms as ``save_world_before_process_exit``."""
    from engine import persistence as persist_mod

    if persist_mod.persist_background_writer_enabled():
        from engine.persistence_writer import shutdown_persistence_writer

        shutdown_persistence_writer(
            timeout=persist_mod.persist_writer_drain_timeout_s(),
        )
    game._persist_world_full_scheduled = False
    persist_mod._persist_reset_force_full_verify_progress(game)
    persist_mod._mark_online_session_characters_dirty(game)
    game._persist_force_full = True
    game._persist_force_full_chars_only = False


async def run_warmup_async(game, *, retries=3):
    """One cooperative full persist after MSG_BEFORE (Veil pause).

    Wall budget is off so nothing is deferred. Writer is drained first.
    Scoped hang hold keeps the watcher from SIGKILL during long applies.
    Raises if the snapshot does not apply.
    """
    import asyncio

    from engine import game_heartbeat as game_heartbeat_mod
    from engine import persistence as persist_mod

    if game is None:
        raise RuntimeError("copyover persist: no game")
    conn = getattr(game, "db", None)
    if conn is None:
        raise RuntimeError("copyover persist: game.db is not open")

    game_heartbeat_mod.set_hang_check_hold(True)
    last_exc = None
    seal_stats = {}
    try:
        for attempt in range(max(1, int(retries or 1))):
            _begin_required_sidecar(game)
            game._persist_copyover_save = True
            _prepare_copyover_world_save(game)
            try:
                _touch_persist_heartbeat()
                await persist_mod.save_world_async(
                    conn, game, wall_budget_ms=0, writer_handoff=None,
                )
                await persist_mod.wait_writer_idle_async()
                _require_world_applied(game, label="copyover seal")
                # World apply already landed. Snapshot counts now -- aux
                # / checkpoint writes stamp skipped empty stats onto the
                # same attribute and must not become the seal.
                seal_stats = _world_save_stats_copy(game)
                await asyncio.sleep(0)
                _touch_persist_heartbeat()
                await flush_copyover_aux_async(game, force=True)
                try:
                    persist_mod.archive_online_login_checkpoints(conn, game)
                except Exception as exc:
                    print(
                        f"[copyover_persist] login checkpoints failed: {exc!r}",
                        flush=True,
                    )
                _log_persist_counts("seal", seal_stats)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                print(
                    f"[copyover_persist] seal failed "
                    f"(attempt {attempt + 1}/{retries}): {exc!r}",
                    flush=True,
                )
                if attempt + 1 >= retries:
                    break
                await asyncio.sleep(0.4 * (attempt + 1))
            finally:
                game._persist_copyover_save = False
                _end_required_sidecar(game)
    finally:
        game_heartbeat_mod.set_hang_check_hold(False)
    if last_exc is not None:
        raise last_exc
    return seal_stats
