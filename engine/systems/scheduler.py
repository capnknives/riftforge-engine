"""
scheduler.py -- generic tick-keyed delayed-callback queue.

Consolidates the execute_at_tick pattern already reimplemented three times
independently (supers/ai_finish.py, supers/dead_mans_blood.py,
supers/world_ext.py) into one shared, engine-level primitive. Runs inside
the existing tick_loop() -- no new thread, no new asyncio task (hard rule 3).

Callbacks are looked up by *registered name*, not stored as raw function
references, so a scheduled row can survive a JSON round-trip / process
restart the same way persist_blob.py's other queues do.
"""

from __future__ import annotations

_CALLBACK_REGISTRY: dict[str, callable] = {}


def register_callback(name: str, fn) -> None:
    """Register ``fn(game, **payload)`` under ``name`` for scheduled rows."""
    _CALLBACK_REGISTRY[name] = fn


def schedule_ticks(game, delay_ticks: int, callback_name: str, **payload) -> None:
    """Queue ``callback_name(game, **payload)`` to run in ``delay_ticks`` ticks."""
    if game is None or delay_ticks < 0:
        return
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    rows = _pending(game)
    rows.append({
        "execute_at_tick": now + int(delay_ticks),
        "callback": str(callback_name),
        "payload": dict(payload or {}),
    })


def _pending(game) -> list:
    if not hasattr(game, "_scheduled_callbacks"):
        game._scheduled_callbacks = []
    return game._scheduled_callbacks


def tick_scheduler(game) -> None:
    """Fire every due row. Registered on the tick pipeline."""
    rows = _pending(game)
    if not rows:
        return
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    due = [r for r in rows if int(r.get("execute_at_tick", 0) or 0) <= now]
    if not due:
        return
    game._scheduled_callbacks = [r for r in rows if r not in due]
    for row in due:
        fn = _CALLBACK_REGISTRY.get(row.get("callback"))
        if fn is None:
            continue
        try:
            fn(game, **(row.get("payload") or {}))
        except Exception:
            from engine import diag_export

            diag_export.append_event(
                "A_D",
                "scheduler.py:tick_scheduler",
                "callback_error",
                {"callback": row.get("callback")},
            )


def register_tick(game) -> None:
    """Call once from supers/tick_bootstrap.py alongside other register_tick calls."""
    from engine.tick_registry import register_tick as _register_tick

    _register_tick(game, tick_scheduler, order=15, name="scheduler")
