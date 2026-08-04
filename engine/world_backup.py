"""world_backup.py -- nightly snapshots of DB + live-authored content.

The watcher schedules a daily run (wall-clock, default America/Chicago 00:05).
Before copying SQLite, the game child is asked to ``save()`` via a request
flag so the backup API sees a quiescent snapshot when possible.

Layout::

    backups/YYYY-MM-DD/
      riftforge.db
      manifest.json
      content/maps/
      content/zones/
      map_backups/
      map_archives/
      catalogs/
      npcs/

Env (optional):

- ``RIFTFORGE_BACKUP_TZ`` -- IANA timezone (default ``America/Chicago``)
- ``RIFTFORGE_BACKUP_HOUR`` / ``RIFTFORGE_BACKUP_MINUTE`` -- local run time
- ``RIFTFORGE_BACKUP_RETENTION_DAYS`` -- prune older trees (default ``14``)
- ``RIFTFORGE_BACKUP_ACK_SECONDS`` -- wait for game save ack (default ``20``)
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from zoneinfo import ZoneInfo

REQUEST_FILENAME = ".backup_request"
ACK_FILENAME = ".backup_ack"
STATE_FILENAME = ".backup_state.json"
BACKUPS_DIRNAME = "backups"

DEFAULT_TZ = "America/Chicago"
DEFAULT_HOUR = 0
DEFAULT_MINUTE = 5
DEFAULT_RETENTION_DAYS = 14
DEFAULT_ACK_SECONDS = 20.0


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def backups_root(root=None):
    return os.path.join(root or _repo_root(), BACKUPS_DIRNAME)


def state_path(root=None):
    return os.path.join(root or _repo_root(), STATE_FILENAME)


def request_path(root=None):
    return os.path.join(root or _repo_root(), REQUEST_FILENAME)


def ack_path(root=None):
    return os.path.join(root or _repo_root(), ACK_FILENAME)


def backup_tz():
    """Return tzinfo for backup scheduling (falls back to UTC without tzdata)."""
    from datetime import timezone

    name = (os.environ.get("RIFTFORGE_BACKUP_TZ") or DEFAULT_TZ).strip()
    try:
        return ZoneInfo(name)
    except Exception:
        try:
            return ZoneInfo("UTC")
        except Exception:
            return timezone.utc


def backup_hour_minute():
    hour_raw = (os.environ.get("RIFTFORGE_BACKUP_HOUR") or "").strip()
    minute_raw = (os.environ.get("RIFTFORGE_BACKUP_MINUTE") or "").strip()
    try:
        hour = int(hour_raw) if hour_raw else DEFAULT_HOUR
    except ValueError:
        hour = DEFAULT_HOUR
    try:
        minute = int(minute_raw) if minute_raw else DEFAULT_MINUTE
    except ValueError:
        minute = DEFAULT_MINUTE
    return max(0, min(23, hour)), max(0, min(59, minute))


def retention_days():
    raw = (os.environ.get("RIFTFORGE_BACKUP_RETENTION_DAYS") or "").strip()
    if not raw:
        return DEFAULT_RETENTION_DAYS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def ack_timeout_seconds():
    raw = (os.environ.get("RIFTFORGE_BACKUP_ACK_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_ACK_SECONDS
    try:
        return max(3.0, float(raw))
    except ValueError:
        return DEFAULT_ACK_SECONDS


def load_state(root=None):
    path = state_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


def save_state(state, *, root=None):
    path = state_path(root)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        pass


def local_today(root=None):
    tz = backup_tz()
    return datetime.now(tz).date().isoformat()


def backup_due(*, root=None, now=None):
    """True when today's dated backup has not completed yet and wall time passed."""
    root = root or _repo_root()
    tz = backup_tz()
    now_dt = datetime.now(tz) if now is None else now.astimezone(tz)
    hour, minute = backup_hour_minute()
    if (now_dt.hour, now_dt.minute) < (hour, minute):
        return False
    state = load_state(root)
    return state.get("last_run_date") != now_dt.date().isoformat()


def request_game_save(*, root=None):
    """Ask the game child to save before we copy SQLite."""
    path = request_path(root)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"{time.time():.3f}\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass


def clear_request_ack(*, root=None):
    for path in (request_path(root), ack_path(root)):
        try:
            os.remove(path)
        except OSError:
            pass


def wait_for_ack(*, root=None, timeout=None):
    """Poll for ``.backup_ack``; return True when present."""
    timeout = ack_timeout_seconds() if timeout is None else timeout
    ack = ack_path(root)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.isfile(ack):
            return True
        time.sleep(0.25)
    return False


def write_ack(*, root=None):
    path = ack_path(root)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"{time.time():.3f}\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass


def backup_request_pending(*, root=None):
    return os.path.isfile(request_path(root))


def current_head_sha(root=None):
    root = root or _repo_root()
    try:
        proc = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _copy_tree(src, dest):
    if not os.path.isdir(src):
        return 0
    if os.path.exists(dest):
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest)
    return sum(
        1 for _dir, _dirs, files in os.walk(dest) for _f in files
    )


def _copy_file(src, dest_dir):
    if not os.path.isfile(src):
        return False
    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, os.path.join(dest_dir, os.path.basename(src)))
    return True


def _sqlite_backup(src_db, dest_db):
    os.makedirs(os.path.dirname(dest_db), exist_ok=True)
    if os.path.exists(dest_db):
        os.remove(dest_db)
    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True, timeout=30)
    try:
        dest = sqlite3.connect(dest_db)
        try:
            src.backup(dest)
            dest.commit()
        finally:
            dest.close()
    finally:
        src.close()


def _protected_catalog_paths(root):
    from tools.apply_pr_fix import protected_prefixes

    paths = []
    for prefix in protected_prefixes():
        norm = prefix.replace("\\", "/").rstrip("/")
        if norm.endswith(".json"):
            full = os.path.join(root, norm)
            if os.path.isfile(full):
                paths.append(full)
    return paths


def run_backup(*, root=None, triggered_by="scheduler", force=False):
    """Run one dated backup tree. Returns manifest dict."""
    root = root or _repo_root()
    today = local_today(root)
    state = load_state(root)
    if not force and state.get("last_run_date") == today:
        return state.get("last_manifest") or {}

    dest_root = os.path.join(backups_root(root), today)
    os.makedirs(dest_root, exist_ok=True)

    request_game_save(root=root)
    ack_ok = wait_for_ack(root=root)
    clear_request_ack(root=root)

    src_db = os.path.join(root, "riftforge.db")
    dest_db = os.path.join(dest_root, "riftforge.db")
    db_ok = False
    if os.path.isfile(src_db):
        try:
            _sqlite_backup(src_db, dest_db)
            db_ok = True
        except sqlite3.Error as exc:
            print(f"[backup] sqlite backup failed: {exc!r}", flush=True)

    # Refresh hot map backups + daily archive before copying trees.
    map_archive_lines = []
    try:
        from engine import hooks
        hooks.write_map_backup_all(root=root)
        map_archive_lines.append(
            hooks.write_map_daily_archive(
                root=root,
                confirmed_by=triggered_by or "scheduler",
            )
        )
    except Exception as exc:
        print(f"[backup] map snapshot hooks failed: {exc!r}", flush=True)

    copied = {}
    for label, rel in (
        ("maps", "content/maps"),
        ("zones", "content/zones"),
        ("map_backups", "content/map_backups"),
        ("map_archives", "content/map_archives"),
        ("npcs", "content/npcs"),
    ):
        src = os.path.join(root, rel)
        dest = os.path.join(dest_root, rel.replace("/", os.sep))
        if os.path.isdir(src):
            copied[label] = _copy_tree(src, dest)

    catalog_dest = os.path.join(dest_root, "catalogs")
    catalog_count = 0
    for path in _protected_catalog_paths(root):
        if _copy_file(path, catalog_dest):
            catalog_count += 1
    copied["catalogs"] = catalog_count

    manifest = {
        "date": today,
        "sha": current_head_sha(root),
        "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "triggered_by": triggered_by,
        "consistent": bool(ack_ok and db_ok),
        "ack": ack_ok,
        "db_ok": db_ok,
        "copied": copied,
        "path": dest_root,
    }
    manifest_path = os.path.join(dest_root, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")

    prune_old_backups(root=root)
    state = {
        "last_run_date": today,
        "last_ok": manifest["consistent"],
        "last_error": "" if manifest["consistent"] else "inconsistent snapshot",
        "last_manifest": manifest,
    }
    save_state(state, root=root)
    print(
        f"[backup] wrote {dest_root} "
        f"(consistent={manifest['consistent']})",
        flush=True,
    )
    return manifest


def prune_old_backups(*, root=None):
    root = root or _repo_root()
    keep = retention_days()
    base = backups_root(root)
    if not os.path.isdir(base):
        return []
    dated = [
        name for name in os.listdir(base)
        if len(name) == 10 and name[4] == "-" and os.path.isdir(os.path.join(base, name))
    ]
    dated.sort(reverse=True)
    removed = []
    for name in dated[keep:]:
        path = os.path.join(base, name)
        shutil.rmtree(path, ignore_errors=True)
        removed.append(name)
    if removed:
        print(f"[backup] pruned {len(removed)} old tree(s)", flush=True)
    return removed


def list_recent_backups(*, root=None, limit=14):
    base = backups_root(root)
    if not os.path.isdir(base):
        return []
    dated = [
        name for name in os.listdir(base)
        if len(name) == 10 and os.path.isdir(os.path.join(base, name))
    ]
    dated.sort(reverse=True)
    return dated[:limit]


def status_text(*, root=None):
    root = root or _repo_root()
    state = load_state(root)
    hour, minute = backup_hour_minute()
    lines = [
        "World backup status:",
        f"  store: {backups_root(root)}",
        f"  schedule: {backup_tz().key} {hour:02d}:{minute:02d} daily",
        f"  retention: {retention_days()} day(s)",
        f"  last run date: {state.get('last_run_date') or '(never)'}",
        f"  last ok: {state.get('last_ok')}",
    ]
    err = state.get("last_error") or ""
    if err:
        lines.append(f"  last error: {err}")
    recent = list_recent_backups(root=root, limit=5)
    if recent:
        lines.append("  recent: " + ", ".join(recent))
    return "\r\n".join(lines)


def backup_db_path(date, *, root=None):
    """Absolute path to ``backups/<date>/riftforge.db``."""
    root = root or _repo_root()
    return os.path.join(backups_root(root), date, "riftforge.db")


def _read_manifest(date, *, root=None):
    path = os.path.join(backups_root(root), date, "manifest.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def verify_db_file(path):
    """Run ``PRAGMA integrity_check``; return ``(ok, detail)``."""
    if not os.path.isfile(path):
        return False, "missing file"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    except sqlite3.Error as exc:
        return False, str(exc)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        return False, str(exc)
    finally:
        conn.close()
    detail = (row[0] if row else "") or ""
    ok = detail.strip().lower() == "ok"
    return ok, detail or "integrity_check failed"


def find_latest_restorable_backup(*, root=None):
    """Newest dated backup whose DB passes integrity_check."""
    root = root or _repo_root()
    for date in list_recent_backups(root=root, limit=retention_days()):
        db_path = backup_db_path(date, root=root)
        if not os.path.isfile(db_path):
            continue
        ok, detail = verify_db_file(db_path)
        if ok:
            return date, detail
    return None, "no restorable backup found"


def restore_live_db(date=None, *, root=None, triggered_by="staff"):
    """Replace live ``riftforge.db`` from ``backups/<date>/``.

    Quarantines the current DB beside the live file. The game child must
    be stopped (watcher handles that for ``gm recover restoredb``).

    Returns ``(ok: bool, detail: str)``.
    """
    root = root or _repo_root()
    if date:
        date = date.strip()
    else:
        date, find_detail = find_latest_restorable_backup(root=root)
        if not date:
            return False, find_detail

    src = backup_db_path(date, root=root)
    if not os.path.isfile(src):
        return False, f"backup missing: backups/{date}/riftforge.db"

    ok, check_detail = verify_db_file(src)
    if not ok:
        return False, f"backup failed integrity_check: {check_detail}"

    live_db = os.path.join(root, "riftforge.db")
    quarantine = ""
    if os.path.isfile(live_db):
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        quarantine = f"{live_db}.corrupt.{stamp}"
        try:
            os.replace(live_db, quarantine)
        except OSError as exc:
            return False, f"could not quarantine live db: {exc!r}"

    try:
        shutil.copy2(src, live_db)
    except OSError as exc:
        if quarantine and os.path.isfile(quarantine) and not os.path.isfile(live_db):
            try:
                os.replace(quarantine, live_db)
            except OSError:
                pass
        return False, f"copy failed: {exc!r}"

    ok, live_detail = verify_db_file(live_db)
    if not ok:
        return False, f"restored db failed integrity_check: {live_detail}"

    manifest = _read_manifest(date, root=root) or {}
    print(
        f"[backup] restored live db from backups/{date}/ "
        f"(by={triggered_by}; quarantine={quarantine or '(none)'})",
        flush=True,
    )
    return True, f"restored from backups/{date}/ (manifest consistent={manifest.get('consistent')})"


def try_auto_restore_live_db(*, root=None, triggered_by="auto_db_recover"):
    """Staging helper: restore newest good nightly backup once."""
    date, _detail = find_latest_restorable_backup(root=root)
    if not date:
        return False, _detail
    return restore_live_db(date, root=root, triggered_by=triggered_by)
