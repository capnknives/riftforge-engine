"""Autosave envelope timing helpers (lag P17).

Computes unaccounted wall time on background saves so the next 50s+
freeze names the gap instead of guessing.
"""

from __future__ import annotations

import os
import shutil

# Phase keys summed when computing ``unaccounted_ms``.
_PHASE_MS_KEYS = (
    "collect_ms",
    "apply_ms",
    "game_time_ms",
    "calendar_ms",
    "clock_ms",
    "accounts_ms",
    "game_meta_ms",
    "homesteads_ms",
    "gather_ms",
    "player_shops_ms",
    "township_ms",
    "personal_realms_ms",
    "demesnes_ms",
    "dream_pockets_ms",
    "wal_checkpoint_ms",
    "world_ms",
    "save_wall_ms",
    "lock_wait_ms",
)

_AUTOSAVE_STALL_MS_DEFAULT = 5000.0


def autosave_stall_ms():
    """NDJSON ``autosave_stall`` threshold (default 5000ms)."""
    raw = os.environ.get("RIFTFORGE_AUTOSAVE_STALL_MS", "").strip()
    if not raw:
        return _AUTOSAVE_STALL_MS_DEFAULT
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return _AUTOSAVE_STALL_MS_DEFAULT


def disk_free_mb(path):
    """Free space on the filesystem hosting ``path`` (None on failure)."""
    if not path:
        return None
    try:
        usage = shutil.disk_usage(os.path.dirname(os.path.abspath(path)))
        return round(usage.free / (1024.0 * 1024.0), 2)
    except OSError:
        return None


def sum_phases_ms(stats):
    """Sum instrumented phase timers on one autosave stats dict."""
    total = 0.0
    for key in _PHASE_MS_KEYS:
        val = stats.get(key)
        if val is None:
            continue
        try:
            total += float(val)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


def enrich_autosave_stats(stats, *, db_path=None):
    """Add ``unaccounted_ms`` and ``disk_free_mb`` in place; return stats."""
    if not isinstance(stats, dict):
        return stats
    save_ms = float(stats.get("save_ms") or 0.0)
    phases = sum_phases_ms(stats)
    # ``save_wall_ms`` is a subset of envelope work; unaccounted is envelope
    # minus every named slice we log (double-counting save_wall is ok --
    # it highlights cooperative-yield gaps vs blocking I/O).
    stats["phases_sum_ms"] = phases
    stats["unaccounted_ms"] = round(max(0.0, save_ms - phases), 2)
    if db_path:
        stats["disk_free_mb"] = disk_free_mb(db_path)
    return stats


def should_log_autosave_stall(stats):
    """True when this save warrants an ``autosave_stall`` NDJSON row."""
    threshold = autosave_stall_ms()
    if threshold <= 0:
        return False
    save_ms = float(stats.get("save_ms") or 0.0)
    unaccounted = float(stats.get("unaccounted_ms") or 0.0)
    return save_ms >= threshold or unaccounted >= threshold
