"""
cadence_planner.py -- one dedicated read-only Cadence lifestyle-decision thread (lag P31.4).

Narrow amendment to hard rule 3 (AGENTS.md), proposed in
docs/plans/lag_p31_4_cadence_planner_thread_2026-08-23.md -- awaiting maintainer
unpark before AGENTS.md is actually amended. This module's thread computes lifestyle
decisions from read-only CadenceActorProjection snapshots and reports CadenceIntent
lists back onto the asyncio loop via call_soon_threadsafe. It must NEVER import
supers, touch a live Character/Room object, call engine.npc_act.npc_do, or hold a
database connection -- only pure decision functions over plain projection data.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass(frozen=True)
class CadenceActorProjection:
    """Immutable read-only actor snapshot for off-loop lifestyle decisions."""

    actor_key: str
    needs: dict  # plain float values keyed by meter name
    zone_key: str
    room_key: str
    vessel_spirit_flags: dict  # bool / short-string gates
    job_schedule_due: bool
    persona_origin_flags: dict
    lod_eligible: bool
    projected_tick: int
    asleep: bool = False
    can_sleep: bool = False  # room offers sleep / at home — computed on the loop later
    exits: tuple = ()  # cardinal verb strings, e.g. ("n", "e") — never room objects
    wander_roll: float = 1.0  # 0..1, precomputed on the loop; wander if < wander_chance
    wander_chance: float = 0.06  # matches supers/cadence.py IDLE_WANDER_CHANCE * persona
    # Main-loop hop/work string (n / enter X / work). Empty = no forced verb.
    forced_verb: str = ""


@dataclass(frozen=True)
class CadenceIntent:
    """One planned verb for main-loop drain -- no live object refs."""

    actor_key: str
    verb_line: str
    phase_tag: str = "lifestyle"
    planned_tick: int = 0


@dataclass
class CadencePlannerBatch:
    """Inbound projection batch handed to the planner thread."""

    projections: list = field(default_factory=list)  # list[CadenceActorProjection]
    projected_tick: int = 0
    enqueued_at: float = field(default_factory=time.perf_counter)
    on_done: Optional[Callable[[list], None]] = None
    on_error: Optional[Callable[[BaseException], None]] = None
    # Loop that should receive on_done/on_error. Defaults to the loop passed
    # at start_cadence_planner; dispatch should set the *running* tick loop
    # so drain is not queued on a dead loop from Game.__init__.
    ack_loop: Optional[object] = None


# Copied from engine/systems/needs.py and supers/cadence.py — do not import supers here.
SEEK_THRESHOLD = 0.60  # engine/systems/needs.py
WAKE_ENERGY = SEEK_THRESHOLD * 0.25  # 0.15; matches supers/cadence.py _cadence_maybe_wake
IDLE_WANDER_CHANCE = 0.06  # supers/cadence.py


def default_plan_actor_intents(projection: CadenceActorProjection) -> list:
    """Phase 2/3 pure lifestyle decisions for unwatched/LOD actors.

    Priority: decay-only, wake, desk-sit / home hop, sleep, wander.
    Returns at most one CadenceIntent. Must never import supers, touch
    Character/Room, or call npc_do.

    Blood / hunt / grocery are **not** planned here -- those verbs need
    live world state. ``should_lod_skip`` keeps SEEK-fuel Vampires and
    CRITICAL survival on the main-loop ``_act`` path instead.
    """
    if not projection.lod_eligible:
        return []

    flags = projection.vessel_spirit_flags
    if flags.get("possessed") or flags.get("riding"):
        return []

    energy = float(projection.needs.get("energy", 0.0) or 0.0)
    tick = projection.projected_tick
    key = projection.actor_key
    forced = str(getattr(projection, "forced_verb", "") or "").strip()

    if projection.asleep:
        # On-shift clerks should not sleep through the counter.
        if projection.job_schedule_due or energy <= WAKE_ENERGY:
            return [CadenceIntent(actor_key=key, verb_line="wake", planned_tick=tick)]
        return []

    if projection.job_schedule_due:
        if forced:
            return [CadenceIntent(actor_key=key, verb_line=forced, planned_tick=tick)]
        return []

    if energy >= SEEK_THRESHOLD and projection.can_sleep:
        return [CadenceIntent(actor_key=key, verb_line="sleep", planned_tick=tick)]

    if forced:
        return [CadenceIntent(actor_key=key, verb_line=forced, planned_tick=tick)]

    chance = float(getattr(projection, "wander_chance", None) or IDLE_WANDER_CHANCE)
    if projection.wander_roll < chance and projection.exits:
        direction = projection.exits[hash((key, tick)) % len(projection.exits)]
        return [CadenceIntent(actor_key=key, verb_line=direction, planned_tick=tick)]

    return []


# Integration smokes monkeypatch this name; keep it bound to the Phase 2 fn.
default_plan_actor_intents = default_plan_actor_intents


class CadencePlannerThread:
    """Owns the cadence-planner daemon thread and its latest-wins queue."""

    def __init__(self, *, loop, plan_fn=None):
        self._loop = loop
        self._plan_fn = plan_fn or default_plan_actor_intents
        self._queue: "queue.Queue[CadencePlannerBatch | None]" = queue.Queue(
            maxsize=1,
        )
        self._thread: Optional[threading.Thread] = None
        self._busy = threading.Event()
        self._pending = 0
        self._pending_lock = threading.Lock()

    @property
    def busy(self):
        return self._busy.is_set()

    def pending(self):
        with self._pending_lock:
            return self._pending > 0 or self._busy.is_set()

    def start(self):
        self._thread = threading.Thread(
            target=self._run, name="cadence-planner", daemon=True,
        )
        self._thread.start()

    def enqueue(self, batch: CadencePlannerBatch):
        """Latest-wins while the planner is still draining an older batch."""
        with self._pending_lock:
            self._pending += 1
        try:
            self._queue.put_nowait(batch)
        except queue.Full:
            try:
                dropped = self._queue.get_nowait()
                if dropped is not None:
                    self._finish_batch(dropped, error=RuntimeError(
                        "superseded by newer cadence planner batch",
                    ))
            except queue.Empty:
                pass
            self._queue.put_nowait(batch)

    def shutdown(self, timeout=30):
        """Drain queued work then stop the planner thread.

        Default timeout is 30s (not P30's 120s) -- no SQL apply to finish here.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self.pending() and time.monotonic() < deadline:
            time.sleep(0.01)
        self._queue.put(None)
        if self._thread is not None:
            remaining = max(0.0, deadline - time.monotonic())
            self._thread.join(timeout=remaining)

    def _finish_batch(self, batch, *, intents=None, error=None):
        with self._pending_lock:
            self._pending = max(0, self._pending - 1)
        loop = getattr(batch, "ack_loop", None) or self._loop
        if error is not None:
            if batch.on_error is not None:
                loop.call_soon_threadsafe(batch.on_error, error)
            return
        if batch.on_done is not None:
            loop.call_soon_threadsafe(batch.on_done, intents or [])

    def _run(self):
        while True:
            batch = self._queue.get()
            if batch is None:
                break
            self._busy.set()
            t0 = time.perf_counter()
            intents: list = []
            try:
                for projection in batch.projections:
                    intents.extend(self._plan_fn(projection))
                _ = (time.perf_counter() - t0)  # wall time reserved for profiling
                self._finish_batch(batch, intents=intents)
            except Exception as exc:
                try:
                    from engine import log_util

                    log_util.ops(
                        "cadence",
                        "planner thread batch failed "
                        f"{type(exc).__name__}: {str(exc)[:200]}",
                        exc=exc,
                    )
                except Exception:
                    pass
                self._finish_batch(batch, error=exc)
            finally:
                self._busy.clear()


_planner: Optional[CadencePlannerThread] = None


def start_cadence_planner(*, loop, plan_fn=None) -> CadencePlannerThread:
    """Start the planner thread if it is not already running.

    Idempotent so ``gm lag planner on`` can re-enable without a second
    daemon thread.
    """
    global _planner
    if _planner is not None:
        return _planner
    _planner = CadencePlannerThread(loop=loop, plan_fn=plan_fn)
    _planner.start()
    return _planner


def shutdown_cadence_planner(timeout=30):
    global _planner
    if _planner is not None:
        _planner.shutdown(timeout=timeout)
        _planner = None


def enqueue_cadence_batch(batch: CadencePlannerBatch):
    if _planner is None:
        raise RuntimeError("cadence planner not started")
    _planner.enqueue(batch)


def planner_started() -> bool:
    """True once ``start_cadence_planner`` has succeeded and is active."""
    return _planner is not None


def planner_busy() -> bool:
    return _planner is not None and _planner.pending()


def _env_bool(name, default):
    """Parse a truthy env flag (matches engine.persistence._persist_env_bool)."""
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() not in ("0", "off", "false", "no", "")


def cadence_planner_enabled() -> bool:
    """True when the cadence planner thread feature flag is on.

    Unset env stays False so ``Game()`` smokes do not start a planner
    thread. Production compose / live ``.env`` default the flag on
    (``RIFTFORGE_CADENCE_PLANNER_THREAD=1``).
    """
    return _env_bool("RIFTFORGE_CADENCE_PLANNER_THREAD", False)


def cadence_planner_max_actors() -> int:
    """Per-heartbeat projection cap; 0 means no cap (all LOD-eligible)."""
    raw = os.environ.get("RIFTFORGE_CADENCE_PLANNER_MAX_ACTORS", "0")
    try:
        return max(0, int(str(raw).strip() or 0))
    except (TypeError, ValueError):
        return 0
