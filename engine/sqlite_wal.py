"""WAL checkpoint hygiene for the single-writer SQLite save path (lag P13).

Live runs ``RIFTFORGE_SQLITE_JOURNAL=WAL`` (lag P12). When passive
``wal_autocheckpoint`` never completes, the ``-wal`` sidecar grows until
an eventual checkpoint fsyncs the whole backlog on the asyncio thread —
``wall_ms`` in the tens of seconds while ``cpu_ms`` stays low.

This module:
  * tightens ``wal_autocheckpoint`` on connect when WAL is active;
  * runs an explicit ``wal_checkpoint`` after every successful world save;
  * optionally drains an oversized ``-wal`` file once at game boot.

Env (all optional — see ``.env.example``):
  ``RIFTFORGE_SQLITE_WAL_AUTOCHECKPOINT`` — pages (default 200; 0 = leave SQLite default)
  ``RIFTFORGE_SQLITE_WAL_CHECKPOINT`` — TRUNCATE|RESTART|PASSIVE|off (default TRUNCATE when WAL)
  ``RIFTFORGE_SQLITE_WAL_CHECKPOINT_MIN_BYTES`` — skip checkpoint below this size (default 0 = always)
  ``RIFTFORGE_SQLITE_WAL_BOOT_CHECKPOINT_MIN_BYTES`` — boot drain threshold (default 16 MiB; 0 = off)
"""

from __future__ import annotations

import os
import sqlite3
import time

WAL_AUTOCHECKPOINT_ENV = "RIFTFORGE_SQLITE_WAL_AUTOCHECKPOINT"
WAL_CHECKPOINT_ENV = "RIFTFORGE_SQLITE_WAL_CHECKPOINT"
WAL_CHECKPOINT_MIN_BYTES_ENV = "RIFTFORGE_SQLITE_WAL_CHECKPOINT_MIN_BYTES"
WAL_BOOT_CHECKPOINT_MIN_BYTES_ENV = "RIFTFORGE_SQLITE_WAL_BOOT_CHECKPOINT_MIN_BYTES"
WAL_AUTOSAVE_TRUNCATE_MIN_BYTES_ENV = (
    "RIFTFORGE_SQLITE_WAL_AUTOSAVE_TRUNCATE_MIN_BYTES"
)

_DEFAULT_AUTOCHECKPOINT_PAGES = 200
_DEFAULT_BOOT_CHECKPOINT_MIN_BYTES = 16 * 1024 * 1024
# Routine autosave: PASSIVE checkpoint unless -wal is at least this large
# (lag P14). TRUNCATE after every pulse was fsync-ing huge sidecars on the
# asyncio thread (~50s live stalls with apply_ms still ~75ms).
_DEFAULT_AUTOSAVE_TRUNCATE_MIN_BYTES = 8 * 1024 * 1024
_AUTOSAVE_SOFT_CHECKPOINT_REASONS = frozenset({
    "autosave",
    "scheduled",
    "vault_restore",
})


def _env_int(name, default, *, minimum=0):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return int(default)
    try:
        return max(minimum, int(float(raw)))
    except (TypeError, ValueError):
        return int(default)


def wal_autocheckpoint_pages():
    """Pages between passive autocheckpoints (0 = do not override SQLite default)."""
    return _env_int(WAL_AUTOCHECKPOINT_ENV, _DEFAULT_AUTOCHECKPOINT_PAGES)


def checkpoint_min_bytes():
    """Only run post-save checkpoint when ``-wal`` is at least this large (0 = always)."""
    return _env_int(WAL_CHECKPOINT_MIN_BYTES_ENV, 0)


def boot_checkpoint_min_bytes():
    """Drain an oversized ``-wal`` once at connect (0 = disabled)."""
    return _env_int(
        WAL_BOOT_CHECKPOINT_MIN_BYTES_ENV,
        _DEFAULT_BOOT_CHECKPOINT_MIN_BYTES,
    )


def autosave_truncate_min_bytes():
    """Use TRUNCATE (not PASSIVE) on routine saves when ``-wal`` >= this size."""
    return _env_int(
        WAL_AUTOSAVE_TRUNCATE_MIN_BYTES_ENV,
        _DEFAULT_AUTOSAVE_TRUNCATE_MIN_BYTES,
    )


def journal_mode(conn):
    """Return lowercase journal mode for ``conn`` ('wal', 'delete', …)."""
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        return (row[0] if row else "").strip().lower()
    except sqlite3.Error:
        return ""


def wal_file_bytes(db_path):
    """Size of ``{db_path}-wal`` on disk (0 when missing or ``:memory:``)."""
    if not db_path or db_path == ":memory:":
        return 0
    wal_path = f"{db_path}-wal"
    try:
        return os.path.getsize(wal_path)
    except OSError:
        return 0


def checkpoint_mode_for_conn(conn):
    """Resolve checkpoint mode from env + live journal mode (legacy default)."""
    return checkpoint_mode_for_save(conn, reason="save")


def checkpoint_mode_for_save(conn, *, reason="save", wal_bytes=None):
    """Resolve checkpoint mode from env, journal mode, save reason, WAL size."""
    raw = (os.environ.get(WAL_CHECKPOINT_ENV) or "").strip().upper()
    if raw in ("OFF", "0", "NONE", "DISABLE", "DISABLED"):
        return None
    if journal_mode(conn) != "wal":
        return None
    if raw in ("TRUNCATE", "RESTART", "PASSIVE"):
        explicit = raw
    else:
        explicit = None
    reason = (reason or "save").strip().lower()
    if reason in _AUTOSAVE_SOFT_CHECKPOINT_REASONS:
        if wal_bytes is None:
            wal_bytes = 0
        truncate_at = autosave_truncate_min_bytes()
        if truncate_at > 0 and wal_bytes >= truncate_at:
            return explicit or "TRUNCATE"
        return "PASSIVE"
    return explicit or "TRUNCATE"


def configure_on_connect(conn, db_path):
    """Apply WAL tuning pragmas after ``journal_mode`` is set on ``connect()``."""
    if not db_path or db_path == ":memory:":
        return
    if journal_mode(conn) != "wal":
        return
    pages = wal_autocheckpoint_pages()
    if pages > 0:
        conn.execute(f"PRAGMA wal_autocheckpoint={pages}")


def maybe_checkpoint_on_boot(conn, db_path):
    """Drain a huge existing ``-wal`` before the first heartbeat (fail-soft)."""
    threshold = boot_checkpoint_min_bytes()
    if threshold <= 0:
        return {}
    before = wal_file_bytes(db_path)
    if before < threshold:
        return {}
    stats = maybe_checkpoint_after_save(
        conn, db_path, reason="boot", force=True,
    )
    if stats and not stats.get("wal_checkpoint_skipped"):
        print(
            "[persistence] wal boot checkpoint "
            f"before={stats.get('wal_file_bytes_before')} "
            f"after={stats.get('wal_file_bytes_after')} "
            f"ms={stats.get('wal_checkpoint_ms')} "
            f"mode={stats.get('wal_checkpoint_mode')}",
            flush=True,
        )
    return stats


def maybe_checkpoint_after_save(conn, db_path, *, reason="save", force=False):
    """Run configured ``wal_checkpoint`` after a successful save (fail-soft).

    Returns a stats dict suitable for ``autosave_ms`` NDJSON (may be empty).
    """
    if not db_path or db_path == ":memory:":
        return {}

    wal_bytes_before = wal_file_bytes(db_path)
    mode = checkpoint_mode_for_save(
        conn, reason=reason, wal_bytes=wal_bytes_before,
    )
    if not mode:
        return {}
    min_bytes = checkpoint_min_bytes()
    if not force and min_bytes > 0 and wal_bytes_before < min_bytes:
        return {
            "wal_checkpoint_skipped": True,
            "wal_file_bytes_before": wal_bytes_before,
            "wal_checkpoint_min_bytes": min_bytes,
        }

    t0 = time.perf_counter()
    busy = log_pages = checkpointed = None
    err = None
    try:
        row = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        if row:
            busy, log_pages, checkpointed = row[0], row[1], row[2]
    except sqlite3.Error as exc:
        err = repr(exc)

    ms = (time.perf_counter() - t0) * 1000.0
    wal_bytes_after = wal_file_bytes(db_path)
    stats = {
        "wal_checkpoint_mode": mode,
        "wal_checkpoint_ms": round(ms, 2),
        "wal_checkpoint_reason": reason,
        "wal_file_bytes_before": wal_bytes_before,
        "wal_file_bytes_after": wal_bytes_after,
        "wal_checkpoint_busy": busy,
        "wal_checkpoint_log_pages": log_pages,
        "wal_checkpointed_pages": checkpointed,
    }
    if err:
        stats["wal_checkpoint_error"] = err

    slow_ms = 2000.0
    try:
        from engine import lag_watch

        slow_ms = lag_watch.autosave_slow_ms()
    except Exception:
        pass
    if err or ms >= slow_ms:
        try:
            from engine import log_util

            log_util.ops(
                "lag_watch",
                "wal_checkpoint "
                f"reason={reason} mode={mode} ms={ms:.1f} "
                f"wal_before={wal_bytes_before} wal_after={wal_bytes_after} "
                f"busy={busy} log={log_pages} checkpointed={checkpointed} "
                f"err={err or ''}",
            )
        except Exception:
            pass
    return stats
