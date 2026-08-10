"""tick_registry.py -- ordered Game.on_tick handler registration.

server.Game keeps a sorted list of (order, name, fn, async_fn, every_n,
skippable) callbacks instead of a hand-edited import laundry list in
on_tick. Bootstrap (supers.tick_bootstrap.register_default_ticks) fills
the list once at Game construction; new systems register there rather
than editing server.py's on_tick body.

Also times each heartbeat so live lag is diagnosable: when a tick takes
longer than TICK_WARN_MS, stderr gets ``[tick] total=...ms`` plus any
handler over HANDLER_WARN_MS (see kill-live-tick-lag plan). Every tick
also appends a sample to ``game._tick_stats`` for the GM ``tick`` verb.

Production ``tick_loop`` uses ``run_ticks_async`` so Cadence can yield
between actors; smoke/tools keep calling sync ``run_ticks`` / ``on_tick``.

Ambient handlers may set ``every_n>1`` (run 1 of N heartbeats, staggered
by name) and ``skippable=True`` so a soft wall-budget can drop remaining
ambient work when the heartbeat is already over budget
(``RIFTFORGE_TICK_SOFT_BUDGET_MS``, default 350).

Moved from supers/tick_registry.py in the two-repo purity Stage 1 migration
(docs/plans/two_repo_purity.md) -- this module never imported anything
SUPERS-specific (only stdlib + engine.diag_export), so a lean engine boot
with SUPERS absent now gets a working tick pipeline (zero handlers
registered until a game registers its own). supers/tick_registry.py is now
a re-export facade so existing `from supers.tick_registry import X` /
`from supers import tick_registry` call sites keep working unchanged.
"""

import collections
import os
import time


# Log the whole heartbeat when it exceeds this (milliseconds). Idle ticks
# with the character registry should sit well under this; spikes mean a
# new O(rooms) scan or Cadence stampede landed.
TICK_WARN_MS = 100.0
# Within a slow tick, also name any single handler above this threshold.
HANDLER_WARN_MS = 20.0
# How many recent tick samples GM `tick` keeps (ring buffer).
TICK_STATS_LEN = 20
# Soft wall: once handlers have spent this many ms, skip remaining
# skippable ambient handlers for this heartbeat.
_TICK_SOFT_BUDGET_MS_DEFAULT = 350.0
_tick_finalize_cadence_clear = None


def set_tick_finalize_cadence_clear(fn):
    """Register fn(game) to clear per-tick Cadence debug stamps (optional)."""
    global _tick_finalize_cadence_clear
    _tick_finalize_cadence_clear = fn


def tick_soft_budget_ms():
    """Per-heartbeat soft budget for skipping ambient handlers (0=off)."""
    raw = (os.environ.get("RIFTFORGE_TICK_SOFT_BUDGET_MS") or "").strip()
    if not raw:
        return _TICK_SOFT_BUDGET_MS_DEFAULT
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _TICK_SOFT_BUDGET_MS_DEFAULT


def register_tick(
    game, fn, *, order=100, name=None, async_fn=None,
    every_n=1, skippable=False,
):
    """Append a per-heartbeat callback. Lower `order` runs earlier.

    fn(game) -> None is always required (smoke / sync ``on_tick``).
    async_fn(game) is optional; production ``run_ticks_async`` awaits it
    when set, otherwise falls back to sync ``fn``.

    every_n: run on 1 of N heartbeats (staggered by name hash so ambient
    systems do not all fire on the same tick). skippable: may be dropped
    when the soft tick budget is already spent.

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
        max(1, int(every_n or 1)),
        bool(skippable),
    )
    game._tick_handlers.append(entry)
    # Keep stable order: primary by order, secondary by registration name.
    game._tick_handlers.sort(key=lambda t: (t[0], t[1]))


def _handler_due(name, every_n, game_time_ticks):
    """True when this every_n handler should run on this game tick."""
    if every_n <= 1:
        return True
    ticks = int(game_time_ticks or 0)
    # Stable stagger: different ambient names land on different residues.
    offset = sum(ord(ch) for ch in (name or "")) % every_n
    return (ticks % every_n) == offset


def _unpack_handler(entry):
    """Normalize old 4-tuples and new 6-tuples."""
    if len(entry) >= 6:
        order, name, fn, async_fn, every_n, skippable = entry[:6]
    elif len(entry) == 4:
        order, name, fn, async_fn = entry
        every_n, skippable = 1, False
    else:
        order, name, fn = entry[0], entry[1], entry[2]
        async_fn = entry[3] if len(entry) > 3 else None
        every_n, skippable = 1, False
    return order, name, fn, async_fn, every_n, skippable


def _finalize_tick_run(
    game, t0, slow_handlers, all_handlers, capture, *, skipped=None,
):
    """Shared timing, GM ring buffer, stderr, and diag export."""
    from engine import diag_export

    total_ms = (time.perf_counter() - t0) * 1000.0
    slow_handlers.sort(key=lambda pair: pair[1], reverse=True)
    if _tick_finalize_cadence_clear is not None:
        try:
            _tick_finalize_cadence_clear(game)
        except Exception:
            pass
    _record_tick_sample(game, total_ms, slow_handlers, skipped=skipped)
    if total_ms >= TICK_WARN_MS:
        parts = [f"[tick] total={total_ms:.1f}ms"]
        if slow_handlers:
            detail = ", ".join(
                f"{name}={ms:.1f}ms" for name, ms in slow_handlers
            )
            parts.append(f"slow=[{detail}]")
        if skipped:
            parts.append(f"skipped_ambient={len(skipped)}")
        print(" ".join(parts))
    ranked = sorted(all_handlers or [], key=lambda p: p[1], reverse=True)
    n_chars = len(getattr(game, "characters", ()) or ())
    n_sessions = len(getattr(game, "sessions", None) or ())
    cadence_meta = getattr(game, "_cadence_budget_meta", None) or {}
    cadence_saturated = (
        cadence_meta.get("used") is not None
        and cadence_meta.get("limit") is not None
        and int(cadence_meta.get("used") or 0)
        >= int(cadence_meta.get("limit") or 0)
    )
    cadence_slow = any(
        name == "cadence" and ms >= HANDLER_WARN_MS
        for name, ms in slow_handlers
    )
    auto_reason = None
    if total_ms >= TICK_WARN_MS or total_ms >= 500.0:
        auto_reason = "tick_overrun"
    elif cadence_saturated and cadence_slow:
        auto_reason = "cadence_budget_cap"
    if auto_reason:
        payload = {
            "total_ms": round(total_ms, 2),
            "n_chars": n_chars,
            "n_sessions": n_sessions,
            "n_rooms": len(getattr(game, "rooms", {}) or {}),
            "game_time_ticks": getattr(game, "game_time_ticks", None),
            "top_handlers": [
                {"name": n, "ms": round(ms, 2)} for n, ms in ranked[:12]
            ],
            "slow_warn": [
                {"name": n, "ms": round(ms, 2)}
                for n, ms in slow_handlers[:12]
            ],
            "cadence_budget": cadence_meta,
        }
        if skipped:
            payload["skipped_ambient"] = list(skipped)[:20]
        if diag_export.append_auto_capture_event(
            game, payload, reason=auto_reason,
        ):
            diag_export.maybe_notify_auto_capture(game, total_ms)
    if capture and all_handlers is not None and (
        total_ms >= TICK_WARN_MS or total_ms >= 500.0
    ):
        payload = {
            "total_ms": round(total_ms, 2),
            "n_chars": n_chars,
            "n_rooms": len(getattr(game, "rooms", {}) or {}),
            "game_time_ticks": getattr(game, "game_time_ticks", None),
            "top_handlers": [
                {"name": n, "ms": round(ms, 2)} for n, ms in ranked[:12]
            ],
            "slow_warn": [
                {"name": n, "ms": round(ms, 2)}
                for n, ms in slow_handlers[:12]
            ],
        }
        if skipped:
            payload["skipped_ambient"] = list(skipped)[:20]
        diag_export.append_event(
            "A_D",
            "tick_registry.py:run_ticks",
            "slow_or_spike_tick",
            payload,
        )


def run_ticks(game):
    """Invoke every registered tick handler in order (sync smoke/tools)."""
    handlers = getattr(game, "_tick_handlers", ())
    t0 = time.perf_counter()
    slow_handlers = []
    from engine import diag_export
    capture = diag_export.diag_enabled()
    all_handlers = [] if capture else None
    skipped = []
    budget = tick_soft_budget_ms()
    ticks = getattr(game, "game_time_ticks", 0)
    spent = 0.0
    for entry in handlers:
        _order, name, fn, _async_fn, every_n, skippable = _unpack_handler(entry)
        if not _handler_due(name, every_n, ticks):
            continue
        if (
            skippable
            and budget > 0
            and spent >= budget
        ):
            skipped.append(name)
            continue
        h0 = time.perf_counter()
        fn(game)
        h_ms = (time.perf_counter() - h0) * 1000.0
        # Async yield inflation must not zero the soft budget; sync path
        # has no async_fn so full cost counts.
        spent += h_ms
        if all_handlers is not None:
            all_handlers.append((name, h_ms))
        if h_ms >= HANDLER_WARN_MS:
            slow_handlers.append((name, h_ms))
    _finalize_tick_run(
        game, t0, slow_handlers, all_handlers, capture, skipped=skipped,
    )


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
    skipped = []
    budget = tick_soft_budget_ms()
    ticks = getattr(game, "game_time_ticks", 0)
    spent = 0.0
    for entry in handlers:
        _order, name, fn, async_fn, every_n, skippable = _unpack_handler(entry)
        if not _handler_due(name, every_n, ticks):
            continue
        if (
            skippable
            and budget > 0
            and spent >= budget
        ):
            skipped.append(name)
            continue
        h0 = time.perf_counter()
        if async_fn is not None:
            await async_fn(game)
        else:
            fn(game)
        h_ms = (time.perf_counter() - h0) * 1000.0
        # Cadence (and other async_fn) wall time includes asyncio yields
        # that overlap autosave -- do not let that erase ambient work.
        if async_fn is not None:
            spent += min(h_ms, 100.0)
        else:
            spent += h_ms
        if all_handlers is not None:
            all_handlers.append((name, h_ms))
        if h_ms >= HANDLER_WARN_MS:
            slow_handlers.append((name, h_ms))
    _finalize_tick_run(
        game, t0, slow_handlers, all_handlers, capture, skipped=skipped,
    )


def _record_tick_sample(game, total_ms, slow_handlers, *, skipped=None):
    """Append one tick sample to the GM-facing ring on ``game``."""
    ring = getattr(game, "_tick_stats", None)
    if ring is None:
        ring = collections.deque(maxlen=TICK_STATS_LEN)
        game._tick_stats = ring
    sample = {
        "total_ms": float(total_ms),
        "slow": list(slow_handlers),
    }
    if skipped:
        sample["skipped_ambient"] = list(skipped)
    ring.append(sample)


def clear_ticks(game):
    """Remove all handlers (tests that rebuild Game wiring)."""
    game._tick_handlers = []
