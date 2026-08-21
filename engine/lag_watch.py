"""Always-on lag spike observability for live triage.

These logs fire without ``RIFTFORGE_DIAG_ENABLED`` so docker logs show
the next 50+ second freeze even when tick work itself looks fast (block
happened on the command path, autosave, or between heartbeats).

Env knobs (all optional):
  ``RIFTFORGE_INTER_TICK_GAP_MS`` — default 8000 (3s sleep + 5s slack)
  ``RIFTFORGE_HANDLER_STALL_MS`` — per tick handler ops log; default 500
  ``RIFTFORGE_HANDLER_CRITICAL_MS`` — louder stall line; default 2000
  ``RIFTFORGE_COMMAND_SLOW_MS`` — player verb ops log; default 1000
  ``RIFTFORGE_AUTOSAVE_SLOW_MS`` — background save ops log; default 2000
"""

from __future__ import annotations

_LAST_POST_TICK_ATTR = "_lag_watch_last_post_tick_mono"


def _env_float(name, default):
    import os

    raw = os.environ.get(name, "").strip()
    if not raw:
        return float(default)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(default)


def inter_tick_gap_warn_ms():
    """Wall ms between post_tick and next pre_tick before ops log."""
    return _env_float("RIFTFORGE_INTER_TICK_GAP_MS", 8000.0)


def handler_stall_warn_ms():
    return _env_float("RIFTFORGE_HANDLER_STALL_MS", 500.0)


def handler_critical_ms():
    return _env_float("RIFTFORGE_HANDLER_CRITICAL_MS", 2000.0)


def command_slow_ms():
    return _env_float("RIFTFORGE_COMMAND_SLOW_MS", 1000.0)


def autosave_slow_ms():
    return _env_float("RIFTFORGE_AUTOSAVE_SLOW_MS", 2000.0)


def note_post_tick_wall(game):
    """Stamp monotonic wall time at end of each heartbeat."""
    import time as _time

    setattr(game, _LAST_POST_TICK_ATTR, _time.monotonic())


def check_inter_tick_gap(game):
    """Log when the asyncio loop went quiet longer than expected."""
    import time as _time
    from engine import log_util

    last = getattr(game, _LAST_POST_TICK_ATTR, None)
    if last is None:
        return
    gap_ms = (_time.monotonic() - float(last)) * 1000.0
    threshold = inter_tick_gap_warn_ms()
    if threshold <= 0 or gap_ms < threshold:
        return
    autosave = bool(getattr(game, "_autosave_running", False))
    in_flight = bool(getattr(game, "_save_task_in_flight", False))
    log_util.ops(
        "lag_watch",
        "inter_tick_gap "
        f"ms={gap_ms:.1f} threshold={threshold:.0f} "
        f"ticks={getattr(game, 'game_time_ticks', '?')} "
        f"autosave_running={autosave} save_task={in_flight}",
    )


def note_handler_stall(game, name, ms):
    """Log slow individual tick handlers (cadence, law_enforcement, …)."""
    from engine import log_util

    ms = float(ms)
    crit = handler_critical_ms()
    warn = handler_stall_warn_ms()
    if crit > 0 and ms >= crit:
        log_util.ops(
            "lag_watch",
            f"handler_critical name={name} ms={ms:.1f} "
            f"ticks={getattr(game, 'game_time_ticks', '?')}",
        )
        return
    if warn > 0 and ms >= warn:
        log_util.ops(
            "lag_watch",
            f"handler_stall name={name} ms={ms:.1f} "
            f"ticks={getattr(game, 'game_time_ticks', '?')}",
        )


def note_command_stall(game, character, verb, ms, *, raw_preview=""):
    """Log slow player verbs (help, look, combat, …)."""
    from engine import log_util

    ms = float(ms)
    threshold = command_slow_ms()
    if threshold <= 0 or ms < threshold:
        return
    who = getattr(character, "key", "?") if character is not None else "?"
    preview = (raw_preview or "").strip().replace("\r", " ").replace("\n", " ")
    if len(preview) > 80:
        preview = preview[:77] + "..."
    log_util.ops(
        "lag_watch",
        f"command_slow verb={verb} ms={ms:.1f} who={who} "
        f"line={preview!r} ticks={getattr(game, 'game_time_ticks', '?')}",
    )


def note_autosave_stall(game, stats, *, reason):
    """Log slow SQLite apply / full save tasks."""
    from engine import log_util

    save_ms = float(stats.get("save_ms") or 0.0)
    apply_ms = stats.get("apply_ms")
    apply_ms_f = float(apply_ms) if apply_ms is not None else 0.0
    threshold = autosave_slow_ms()
    peak = max(save_ms, apply_ms_f)
    if threshold <= 0 or peak < threshold:
        return
    parts = [
        f"reason={reason}",
        f"save_ms={save_ms:.1f}",
        f"apply_ms={apply_ms_f:.1f}",
        f"force_full={stats.get('force_full')}",
    ]
    collect_ms = stats.get("collect_ms")
    if collect_ms is not None:
        parts.append(f"collect_ms={collect_ms}")
    for key in (
        "world_ms",
        "meta_core_ms",
        "meta_ext_ms",
        "game_meta_ms",
        "wal_checkpoint_ms",
    ):
        if stats.get(key) is not None:
            parts.append(f"{key}={stats.get(key)}")
    if stats.get("n_changed_chars") is not None:
        parts.append(f"chars={stats.get('n_changed_chars')}")
    if stats.get("wal_file_bytes_before") is not None:
        parts.append(f"wal_b={stats.get('wal_file_bytes_before')}")
    if stats.get("wal_checkpoint_mode"):
        parts.append(f"wal_mode={stats.get('wal_checkpoint_mode')}")
    if stats.get("game_meta_wrote"):
        parts.append(f"meta_wrote={stats.get('game_meta_wrote')}")
    log_util.ops(
        "lag_watch",
        "autosave_slow " + " ".join(parts) + " "
        f"ticks={getattr(game, 'game_time_ticks', '?')}",
    )
