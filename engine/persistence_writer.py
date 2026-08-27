"""
persistence_writer.py -- one dedicated SQLite-apply thread (lag P30).

Narrow amendment to hard rule 3 (AGENTS.md): the asyncio loop remains the
sole executor for commands, ticks, and Cadence. This module's thread applies
pre-collected save batches to a *second* sqlite3 connection and reports
results back onto the asyncio loop via ``call_soon_threadsafe``. No command
or tick handlers run here -- only SQL apply + WAL checkpoint.
"""

from __future__ import annotations

import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class WorldSnapshotPacket:
    """Immutable world rows for ``_apply_world_save_snapshot``."""

    char_rows: list
    item_rows: list
    meta: dict


@dataclass
class PersistWriterStep:
    """One labeled apply callable executed on the writer connection."""

    label: str
    apply: Callable[[sqlite3.Connection], None]


@dataclass
class PersistWriterBatch:
    """Ordered writer work: world snapshot then optional meta SQL slices."""

    world: Optional[WorldSnapshotPacket] = None
    steps: list = field(default_factory=list)
    enqueued_at: float = field(default_factory=time.perf_counter)
    on_done: Optional[Callable[[dict], None]] = None
    on_error: Optional[Callable[[BaseException], None]] = None
    # Passed through to sqlite_wal.maybe_checkpoint_after_save so
    # routine autosave uses PASSIVE (lag P14) instead of always
    # TRUNCATE-fsync'ing the -wal sidecar on the writer thread.
    checkpoint_reason: str = "autosave"
    # Loop that should receive on_done/on_error. Defaults to the loop
    # passed at start_persistence_writer; save_async sets the *running*
    # tick loop so ack is not queued on a dead loop from Game.__init__
    # (same pattern as CadencePlannerBatch.ack_loop).
    ack_loop: Optional[object] = None


class PersistenceWriter:
    """Owns the writer thread + its dedicated sqlite3 connection."""

    def __init__(self, db_path, *, loop, busy_timeout_ms=5000):
        self._db_path = db_path
        self._loop = loop
        self._busy_timeout_ms = busy_timeout_ms
        self._queue: "queue.Queue[PersistWriterBatch | None]" = queue.Queue(
            maxsize=1,
        )
        self._thread: Optional[threading.Thread] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._busy = threading.Event()
        self._pending = 0
        self._pending_lock = threading.Lock()
        # asyncio.Event waiters parked until this thread goes idle. The
        # lock also covers the "already idle?" check so a waiter cannot
        # miss the idle signal and hang until shutdown.
        self._idle_waiters_lock = threading.Lock()
        self._idle_waiters: list = []

    @property
    def busy(self):
        return self._busy.is_set()

    def pending(self):
        with self._pending_lock:
            return self._pending > 0 or self._busy.is_set()

    def start(self):
        from engine.persistence import _sqlite_connect_target

        connect_target, uri = _sqlite_connect_target(self._db_path)
        if uri:
            self._conn = sqlite3.connect(
                connect_target, uri=True, check_same_thread=False,
            )
        else:
            self._conn = sqlite3.connect(
                self._db_path, check_same_thread=False,
            )
        from engine import sqlite_wal

        if self._db_path != ":memory:":
            sqlite_wal.configure_on_connect(self._conn, self._db_path)
        self._conn.execute(
            f"PRAGMA busy_timeout={int(self._busy_timeout_ms)}",
        )
        self._thread = threading.Thread(
            target=self._run, name="persistence-writer", daemon=True,
        )
        self._thread.start()

    def enqueue(self, batch: PersistWriterBatch):
        """Latest-wins while the writer is still draining an older batch."""
        with self._pending_lock:
            self._pending += 1
        try:
            self._queue.put_nowait(batch)
        except queue.Full:
            try:
                dropped = self._queue.get_nowait()
                if dropped is not None:
                    # Newer snapshot owns dirty ack. Treating supersede as
                    # on_error restored deferred dirty and kicked another
                    # collect (save storm).
                    self._discard_superseded(dropped)
            except queue.Empty:
                pass
            self._queue.put_nowait(batch)

    def notify_when_idle(self, loop, event):
        """Set *event* on *loop* when this writer has no in-flight work.

        If the writer is already idle, the event is set immediately via
        ``call_soon`` (same thread as the caller -- this is invoked from
        the asyncio loop). Otherwise the writer thread fires
        ``call_soon_threadsafe`` from ``_signal_idle``.
        """
        with self._idle_waiters_lock:
            if not self.pending():
                try:
                    loop.call_soon(event.set)
                except RuntimeError:
                    event.set()
                return
            self._idle_waiters.append((loop, event))

    def _signal_idle(self):
        """Wake parked asyncio waiters. Called only when pending() is false."""
        with self._idle_waiters_lock:
            if self.pending():
                return
            waiters = list(self._idle_waiters)
            self._idle_waiters.clear()
        for loop, event in waiters:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass

    def shutdown(self, timeout=120):
        """Drain queued work then stop the writer thread."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self.pending() and time.monotonic() < deadline:
            time.sleep(0.01)
        self._queue.put(None)
        if self._thread is not None:
            remaining = max(0.0, deadline - time.monotonic())
            self._thread.join(timeout=remaining)
        self._signal_idle()

    def _discard_superseded(self, batch):
        """Drop a queued batch without ack/error callbacks.

        Pending count still drops so wait_until_idle_async cannot stall.
        """
        with self._pending_lock:
            self._pending = max(0, self._pending - 1)

    def _finish_batch(self, batch, *, stats=None, error=None):
        with self._pending_lock:
            self._pending = max(0, self._pending - 1)
        loop = getattr(batch, "ack_loop", None) or self._loop
        if error is not None:
            if batch.on_error is not None:
                loop.call_soon_threadsafe(batch.on_error, error)
            return
        if batch.on_done is not None:
            loop.call_soon_threadsafe(batch.on_done, stats or {})

    def _run(self):
        from engine.persistence import _apply_world_save_snapshot
        from engine import sqlite_wal

        while True:
            batch = self._queue.get()
            if batch is None:
                break
            if batch.world is None and not batch.steps and batch.on_done is None:
                # Latest-wins enqueue already incremented _pending --
                # ack the empty drop so waiters are not stranded.
                self._finish_batch(batch, stats={})
                if not self.pending():
                    self._signal_idle()
                continue
            self._busy.set()
            t0 = time.perf_counter()
            stats = {"writer_steps": []}
            error = None
            try:
                if batch.world is not None:
                    t_world = time.perf_counter()
                    _apply_world_save_snapshot(
                        self._conn,
                        batch.world.char_rows,
                        batch.world.item_rows,
                        batch.world.meta,
                    )
                    world_ms = (time.perf_counter() - t_world) * 1000.0
                    stats["apply_ms"] = round(world_ms, 2)
                    stats["writer_steps"].append(
                        ("world", round(world_ms, 2)),
                    )
                for step in batch.steps:
                    t_step = time.perf_counter()
                    step.apply(self._conn)
                    step_ms = (time.perf_counter() - t_step) * 1000.0
                    stats["writer_steps"].append(
                        (step.label, round(step_ms, 2)),
                    )
                sqlite_wal.maybe_checkpoint_after_save(
                    self._conn, self._db_path,
                    reason=str(getattr(batch, "checkpoint_reason", None) or "autosave"),
                )
                stats["writer_total_ms"] = round(
                    (time.perf_counter() - t0) * 1000.0, 2,
                )
            except Exception as exc:
                stats["writer_total_ms"] = round(
                    (time.perf_counter() - t0) * 1000.0, 2,
                )
                stats["error"] = repr(exc)
                error = exc
            # Clear busy *before* on_done so Game can kick a coalesced
            # follow-up without seeing persist_writer_in_flight() still
            # true (that race left pending saves stranded).
            self._busy.clear()
            self._finish_batch(batch, stats=stats, error=error)
            if not self.pending():
                self._signal_idle()


_writer: Optional[PersistenceWriter] = None


def start_persistence_writer(db_path, *, loop):
    """Start the writer thread if it is not already running.

    Idempotent so ``gm lag writer on`` can call this after a prior off
    without leaking a second SQLite connection.
    """
    global _writer
    if _writer is not None:
        return _writer
    _writer = PersistenceWriter(db_path, loop=loop)
    _writer.start()
    return _writer


def shutdown_persistence_writer(timeout=120):
    global _writer
    if _writer is not None:
        _writer.shutdown(timeout=timeout)
        _writer = None


def enqueue_persist_batch(batch: PersistWriterBatch):
    if _writer is None:
        raise RuntimeError("persistence writer not started")
    _writer.enqueue(batch)


def writer_started():
    """True once ``start_persistence_writer`` has succeeded and is active."""
    return _writer is not None


def writer_busy():
    return _writer is not None and _writer.pending()


def writer_thread_busy():
    return _writer is not None and _writer.busy


async def wait_until_idle_async(*, timeout=None):
    """Park the current asyncio task until the writer queue is empty.

    This is the anti-spin wait: ``asyncio.sleep(0)`` while the writer
    thread holds SQLite pegs a core (the event loop never actually
    sleeps -- it just polls). An ``asyncio.Event`` set from the writer
    thread via ``call_soon_threadsafe`` lets the loop go idle.
    """
    import asyncio

    if _writer is None or not _writer.pending():
        return
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    _writer.notify_when_idle(loop, event)
    if timeout is None:
        await event.wait()
        return
    try:
        await asyncio.wait_for(event.wait(), timeout=float(timeout))
    except asyncio.TimeoutError:
        return
