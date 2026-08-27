"""Live disk sentinel for SQLite I/O stalls (lag P18).

Near-full disks caused 50s+ autosave freezes on live (issue 195). This
module logs warnings and skips growing pre-deploy snapshots when space is
tight — it does not delete player data.
"""

from __future__ import annotations

import os
import shutil

_WARN_PCT_DEFAULT = 85
_CHECK_EVERY_TICKS_DEFAULT = 1200


def _env_float(name, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def disk_warn_percent():
    return _env_float("RIFTFORGE_DISK_WARN_PERCENT", _WARN_PCT_DEFAULT)


def check_every_ticks():
    return max(1, _env_int("RIFTFORGE_DISK_CHECK_EVERY_TICKS", _CHECK_EVERY_TICKS_DEFAULT))


def usage_for_path(path):
    """Return (used_pct, free_mb, total_mb) or None."""
    if not path:
        return None
    try:
        root = os.path.dirname(os.path.abspath(path)) or path
        usage = shutil.disk_usage(root)
        total_mb = usage.total / (1024.0 * 1024.0)
        free_mb = usage.free / (1024.0 * 1024.0)
        used_pct = (usage.used / usage.total) * 100.0 if usage.total else 0.0
        return round(used_pct, 1), round(free_mb, 1), round(total_mb, 1)
    except OSError:
        return None


def maybe_tick_disk_health(game):
    """Periodic disk check; logs once per crossing above warn threshold."""
    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    every = check_every_ticks()
    if ticks % every != 0:
        return
    db_path = getattr(game, "db_path", None) or "riftforge.db"
    info = usage_for_path(db_path)
    if info is None:
        return
    used_pct, free_mb, total_mb = info
    warn = disk_warn_percent()
    prev = float(getattr(game, "_disk_health_last_used_pct", 0.0) or 0.0)
    game._disk_health_last_used_pct = used_pct
    game._disk_health_last_free_mb = free_mb
    if used_pct < warn and prev < warn:
        return
    from engine import log_util

    level = "disk_health_warn" if used_pct >= warn else "disk_health_ok"
    log_util.ops(
        "lag_watch",
        f"{level} used_pct={used_pct:.1f} free_mb={free_mb:.0f} "
        f"total_mb={total_mb:.0f} warn_pct={warn:.0f} "
        f"ticks={ticks}",
    )
