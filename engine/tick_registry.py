"""tick_registry.py -- ordered Game.on_tick handler registration.

server.Game keeps a sorted list of (order, name, fn, async_fn) callbacks
instead of a hand-edited import laundry list in on_tick. Bootstrap
(supers.tick_bootstrap.register_default_ticks) fills the list once at
Game construction; new systems register there rather than editing
server.py's on_tick body.

Also times each heartbeat so live lag is diagnosable: when a tick takes
longer than TICK_WARN_MS, stderr gets ``[tick] total=...ms`` plus any
handler over HANDLER_WARN_MS (see kill-live-tick-lag plan). Every tick
also appends a sample to ``game._tick_stats`` for the GM ``tick`` verb.

Production ``tick_loop`` uses ``run_ticks_async`` so Cadence can yield
between actors; smoke/tools keep calling sync ``run_ticks`` / ``on_tick``.

Moved from supers/tick_registry.py in the two-repo purity Stage 1 migration
(docs/plans/two_repo_purity.md) -- this module never imported anything
SUPERS-specific (only stdlib + engine.diag_export), so a lean engine boot
with SUPERS absent now gets a working tick pipeline (zero handlers
registered until a game registers its own). supers/tick_registry.py is now
a re-export facade so existing `from supers.tick_registry import X` /
`from supers import tick_registry` call sites keep working unchanged.
"""

import collections
import time


# Log the whole heartbeat when it exceeds this (milliseconds). Idle ticks
# with the character registry should sit well under this; spikes mean a
# new O(rooms) scan or Cadence stampede landed.
TICK_WARN_MS = 100.0
# Within a slow tick, also name any single handler above this threshold.
HANDLER_WARN_MS = 20.0
# How many recent tick samples GM `tick` keeps (ring buffer).
TICK_STATS_LEN = 20


def register_tick(game, fn, *, order=100, name=None, async_fn=None):
    """Append a per-heartbeat callback. Lower `order` runs earlier.

    fn(game) -> None is always required (smoke / sync ``on_tick``).
    async_fn(game) is optional; production ``run_ticks_async`` awaits it
    when set, otherwise falls back to sync ``fn``.

    Exceptions are NOT caught here -- Game.tick_loop already wraps the
    whole heartbeat in try/except so one bad system does not kill the
    heartbeat.
    """
    if not hasattr(game, "_tick_handlers"):
        game._tick_handlers = []
    entry = (
        int(order),
        name or getattr(fn, "__name__", repr(fn)),
        fn,
        async_fn,
    )
    game._tick_handlers.append(entry)
    # Keep stable order: primary by order, secondary by registration name.
    game._tick_handlers.sort(key=lambda t: (t[0], t[1]))


def _finalize_tick_run(game, t0, slow_handlers, all_handlers, capture):
    """Shared timing, GM ring buffer, stderr, and diag export."""
    total_ms = (time.perf_counter() - t0) * 1000.0
    slow_handlers.sort(key=lambda pair: pair[1], reverse=True)
    _record_tick_sample(game, total_ms, slow_handlers)
    if total_ms >= TICK_WARN_MS:
        parts = [f"[tick] total={total_ms:.1f}ms"]
        if slow_handlers:
            detail = ", ".join(
                f"{name}={ms:.1f}ms" for name, ms in slow_handlers
            )
            parts.append(f"slow=[{detail}]")
        print(" ".join(parts))
    if capture and all_handlers is not None and (
        total_ms >= TICK_WARN_MS or total_ms >= 500.0
    ):
        from engine import diag_export
        ranked = sorted(all_handlers, key=lambda p: p[1], reverse=True)
        n_chars = len(getattr(game, "characters", ()) or ())
        n_rooms = len(getattr(game, "rooms", {}) or {})
        diag_export.append_event(
            "A_D",
            "tick_registry.py:run_ticks",
            "slow_or_spike_tick",
            {
                "total_ms": round(total_ms, 2),
                "n_chars": n_chars,
                "n_rooms": n_rooms,
                "game_time_ticks": getattr(game, "game_time_ticks", None),
                "top_handlers": [
                    {"name": n, "ms": round(ms, 2)} for n, ms in ranked[:12]
                ],
                "slow_warn": [
                    {"name": n, "ms": round(ms, 2)}
                    for n, ms in slow_handlers[:12]
                ],
            },
        )


def run_ticks(game):
    """Invoke every registered tick handler in order (sync smoke/tools)."""
    handlers = getattr(game, "_tick_handlers", ())
    t0 = time.perf_counter()
    slow_handlers = []
    from engine import diag_export
    capture = diag_export.diag_enabled()
    all_handlers = [] if capture else None
    for _order, name, fn, _async_fn in handlers:
        h0 = time.perf_counter()
        fn(game)
        h_ms = (time.perf_counter() - h0) * 1000.0
        if all_handlers is not None:
            all_handlers.append((name, h_ms))
        if h_ms >= HANDLER_WARN_MS:
            slow_handlers.append((name, h_ms))
    _finalize_tick_run(game, t0, slow_handlers, all_handlers, capture)


async def run_ticks_async(game):
    """Invoke every registered tick handler (production heartbeat).

    Handlers with ``async_fn`` run through that coroutine so Cadence can
    ``await asyncio.sleep(0)`` between actors; others stay sync.
    """
    handlers = getattr(game, "_tick_handlers", ())
    t0 = time.perf_counter()
    slow_handlers = []
    from engine import diag_export
    capture = diag_export.diag_enabled()
    all_handlers = [] if capture else None
    for _order, name, fn, async_fn in handlers:
        h0 = time.perf_counter()
        if async_fn is not None:
            await async_fn(game)
        else:
            fn(game)
        h_ms = (time.perf_counter() - h0) * 1000.0
        if all_handlers is not None:
            all_handlers.append((name, h_ms))
        if h_ms >= HANDLER_WARN_MS:
            slow_handlers.append((name, h_ms))
    _finalize_tick_run(game, t0, slow_handlers, all_handlers, capture)


def _record_tick_sample(game, total_ms, slow_handlers):
    """Append one tick sample to the GM-facing ring on ``game``."""
    ring = getattr(game, "_tick_stats", None)
    if ring is None:
        ring = collections.deque(maxlen=TICK_STATS_LEN)
        game._tick_stats = ring
    ring.append({
        "total_ms": float(total_ms),
        "slow": list(slow_handlers),
    })


def clear_ticks(game):
    """Remove all handlers (tests that rebuild Game wiring)."""
    game._tick_handlers = []
