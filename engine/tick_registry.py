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
import traceback

# Per-handler name: monotonic time of the last stderr traceback we printed.
# Without this, one handler raising every 3s heartbeat would flood Docker logs.
_tick_fail_log_at = {}


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
_TICK_SOFT_BUDGET_MS_DEFAULT = 300.0
_TICK_FAIL_LOG_SECONDS_DEFAULT = 60.0
_TICK_DISABLE_THRESHOLD_DEFAULT = 5
_TICK_DISABLE_WINDOW_SECONDS_DEFAULT = 300.0
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


def tick_fail_log_seconds():
    """Min seconds between stderr tracebacks for the same handler name."""
    raw = (os.environ.get("RIFTFORGE_TICK_FAIL_LOG_SECONDS") or "").strip()
    if not raw:
        return _TICK_FAIL_LOG_SECONDS_DEFAULT
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _TICK_FAIL_LOG_SECONDS_DEFAULT


def _tick_fail_ops_enabled():
    raw = (os.environ.get("RIFTFORGE_TICK_FAIL_OPS") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def tick_disable_threshold():
    """Failures in the sliding window before a handler is auto-disabled."""
    raw = (os.environ.get("RIFTFORGE_TICK_DISABLE_THRESHOLD") or "").strip()
    if not raw:
        return _TICK_DISABLE_THRESHOLD_DEFAULT
    try:
        return max(1, int(raw))
    except ValueError:
        return _TICK_DISABLE_THRESHOLD_DEFAULT


def tick_disable_window_seconds():
    """Sliding window for tick_fail auto-disable (seconds)."""
    raw = (os.environ.get("RIFTFORGE_TICK_DISABLE_WINDOW") or "").strip()
    if not raw:
        return _TICK_DISABLE_WINDOW_SECONDS_DEFAULT
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _TICK_DISABLE_WINDOW_SECONDS_DEFAULT


def _tick_handlers_disabled(game):
    """Set of handler names skipped until game child respawn / copyover."""
    disabled = getattr(game, "_tick_handlers_disabled", None)
    if not isinstance(disabled, set):
        disabled = set()
        game._tick_handlers_disabled = disabled
    return disabled


def is_tick_handler_disabled(game, name):
    return name in _tick_handlers_disabled(game)


def clear_tick_handler_health(game):
    """Reset failure windows and disabled set (tests; fresh Game starts empty)."""
    game._tick_handlers_disabled = set()
    game._tick_handler_fail_times = {}


def _maybe_disable_tick_handler(game, name):
    """After a failure, disable the handler when the window threshold is hit."""
    from engine import metrics as metrics_mod

    now = time.monotonic()
    window = tick_disable_window_seconds()
    store = getattr(game, "_tick_handler_fail_times", None)
    if not isinstance(store, dict):
        store = {}
        game._tick_handler_fail_times = store
    times = store.get(name)
    if not isinstance(times, collections.deque):
        times = collections.deque()
        store[name] = times
    times.append(now)
    cutoff = now - window
    while times and times[0] < cutoff:
        times.popleft()
    threshold = tick_disable_threshold()
    if len(times) < threshold:
        return
    disabled = _tick_handlers_disabled(game)
    if name in disabled:
        return
    disabled.add(name)
    metrics_mod.bump(game, f"tick_disabled:{name}")
    if _tick_fail_ops_enabled():
        from engine import log_util

        log_util.ops(
            "tick_registry",
            f"disabled handler={name} after {len(times)} failures in "
            f"{window:.0f}s window",
        )


def _tick_fail_log_due(name):
    """True when we may print another traceback for this handler name."""
    now = time.monotonic()
    last = _tick_fail_log_at.get(name)
    if last is None or (now - last) >= tick_fail_log_seconds():
        _tick_fail_log_at[name] = now
        return True
    return False


def _note_tick_handler_failure(game, name, exc):
    """Count + remember one handler failure without aborting the tick pass."""
    from engine import metrics as metrics_mod

    metrics_mod.bump(game, f"tick_fail:{name}")
    last_fail = getattr(game, "_tick_handler_last_fail", None)
    if not isinstance(last_fail, dict):
        last_fail = {}
        game._tick_handler_last_fail = last_fail
    msg = str(exc).strip()
    last_fail[name] = {
        "exc_type": type(exc).__name__,
        "message": msg[:120],
        "at_mono": time.monotonic(),
    }
    if _tick_fail_log_due(name):
        traceback.print_exc()
        if _tick_fail_ops_enabled():
            from engine import log_util

            log_util.ops(
                "tick_registry",
                f"handler={name} {type(exc).__name__}: {msg[:200]}",
                exc=exc,
            )
    _maybe_disable_tick_handler(game, name)


def _invoke_tick_handler_safe(game, name, fn):
    """Run one sync tick handler; sibling handlers still run on failure.

    Hangs are not caught here -- only exceptions. A stuck handler still trips
    the watcher heartbeat (GAME_HANG_TIMEOUT).
    """
    try:
        fn(game)
    except Exception as exc:
        _note_tick_handler_failure(game, name, exc)


async def _invoke_tick_handler_safe_async(game, name, fn, async_fn):
    """Run one production tick handler (async_fn when set, else sync fn)."""
    try:
        if async_fn is not None:
            await async_fn(game)
        else:
            fn(game)
    except Exception as exc:
        _note_tick_handler_failure(game, name, exc)


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

    Exceptions in fn/async_fn are caught per handler so one bad system does
    not skip later handlers in the same heartbeat. After
    ``RIFTFORGE_TICK_DISABLE_THRESHOLD`` failures in the disable window the
    handler is skipped until game child respawn. Game.tick_loop still wraps
    the whole heartbeat for bugs outside this registry.
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


def _cadence_pressure_skip_ambient(game, name, h_ms, spent, budget):
    """After a heavy Cadence slice, drop remaining skippable ambient work."""
    if name != "cadence" or budget <= 0:
        return spent
    spent = max(spent, h_ms)
    meta = getattr(game, "_cadence_budget_meta", None) or {}
    saturated = (
        meta.get("wall_capped")
        or meta.get("action_capped")
        or h_ms >= HANDLER_WARN_MS * 2
    )
    if not saturated:
        # Also saturate when Cadence used most of its action budget.
        try:
            used = int(meta.get("used") or 0)
            limit = int(meta.get("limit") or 0)
            if limit > 0 and used >= max(1, limit - 4):
                saturated = True
        except (TypeError, ValueError):
            pass
    if saturated:
        spent = max(spent, budget)
    return spent


def _async_handler_budget_cost(name, h_ms):
    """How much of the soft budget an async handler consumes."""
    if name == "cadence":
        return h_ms
    return min(h_ms, 100.0)


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
    from engine import boot_stability

    if (
        total_ms >= TICK_WARN_MS
        and not boot_stability.tick_stderr_warmup_active(game)
    ):
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
        payload["autosave_running"] = bool(
            getattr(game, "_autosave_running", False)
        )
        last_as = getattr(game, "_last_autosave_stats", None) or {}
        if last_as.get("save_ms") is not None:
            payload["last_autosave_save_ms"] = last_as.get("save_ms")
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
        if is_tick_handler_disabled(game, name):
            skipped.append(f"{name}:disabled")
            continue
        if (
            skippable
            and budget > 0
            and spent >= budget
        ):
            skipped.append(name)
            continue
        h0 = time.perf_counter()
        _invoke_tick_handler_safe(game, name, fn)
        h_ms = (time.perf_counter() - h0) * 1000.0
        spent += h_ms
        spent = _cadence_pressure_skip_ambient(
            game, name, h_ms, spent, budget,
        )
        try:
            from engine import game_heartbeat

            game_heartbeat.touch_heartbeat(f"tick:{name}")
        except Exception:
            pass
        if all_handlers is not None:
            all_handlers.append((name, h_ms))
        if h_ms >= HANDLER_WARN_MS:
            slow_handlers.append((name, h_ms))
        try:
            from engine import lag_watch
            lag_watch.note_handler_stall(game, name, h_ms)
        except Exception:
            pass
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
        if is_tick_handler_disabled(game, name):
            skipped.append(f"{name}:disabled")
            continue
        if (
            skippable
            and budget > 0
            and spent >= budget
        ):
            skipped.append(name)
            continue
        h0 = time.perf_counter()
        await _invoke_tick_handler_safe_async(game, name, fn, async_fn)
        h_ms = (time.perf_counter() - h0) * 1000.0
        if async_fn is not None:
            spent += _async_handler_budget_cost(name, h_ms)
            spent = _cadence_pressure_skip_ambient(
                game, name, h_ms, spent, budget,
            )
        else:
            spent += h_ms
            spent = _cadence_pressure_skip_ambient(
                game, name, h_ms, spent, budget,
            )
        try:
            from engine import game_heartbeat

            game_heartbeat.touch_heartbeat(f"tick:{name}")
        except Exception:
            pass
        if all_handlers is not None:
            all_handlers.append((name, h_ms))
        if h_ms >= HANDLER_WARN_MS:
            slow_handlers.append((name, h_ms))
        try:
            from engine import lag_watch
            lag_watch.note_handler_stall(game, name, h_ms)
        except Exception:
            pass
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
