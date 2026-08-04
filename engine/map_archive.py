"""
map_archive.py -- daily authorized map/zone archive + staff nag state.

Hot backups (``content/map_backups/``) refresh on every save. Daily
archives (``content/map_archives/YYYY-MM-DD/``) are a second snapshot
set staff confirm with ``gm maps archive confirm`` — the tick nag
pesters online GMs until that runs after live map edits.

Both trees are auto-deploy protected (prefix-stashed across reset).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import date, datetime, timezone

import maps as maps_mod

from engine import map_backups

STATE_FILENAME = ".archive_state.json"
# ~30 real minutes -- staff-nag pacing, not a calendar quantity. Converted
# to actual game_time_ticks via ticks_for_wall_seconds at the live gm
# clock scale wherever consumed.
NAG_INTERVAL_SECONDS = 1800.0


class MapArchiveError(ValueError):
    """Raised for archive command failures (message is GM-facing)."""


def archives_root(root=None):
    """Absolute path to ``content/map_archives``."""
    if root is None:
        root = os.getcwd()
    return os.path.join(os.path.abspath(root), "content", "map_archives")


def state_path(root=None):
    """Path to the persisted archive nag / confirm state file."""
    return os.path.join(archives_root(root), STATE_FILENAME)


def _default_state():
    return {
        "last_confirm_date": "",
        "last_confirm_by": "",
        "last_confirm_ts": 0.0,
        "last_map_edit_ts": 0.0,
        "last_map_edit_map": "",
        "nag_next_tick": 0,
    }


def load_state(root=None):
    """Load archive state dict (empty defaults when missing)."""
    path = state_path(root)
    if not os.path.isfile(path):
        return _default_state()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return _default_state()
        out = _default_state()
        out.update(data)
        return out
    except (OSError, json.JSONDecodeError):
        return _default_state()


def save_state(state, root=None):
    """Persist archive state (atomic replace)."""
    os.makedirs(archives_root(root), exist_ok=True)
    path = state_path(root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def mark_map_edited(map_id, root=None):
    """Record that a live map/zone file was written (for daily nag)."""
    try:
        state = load_state(root)
        state["last_map_edit_ts"] = time.time()
        state["last_map_edit_map"] = str(map_id or "")
        save_state(state, root=root)
    except OSError:
        pass


def _today_iso():
    return date.today().isoformat()


def archive_due(state=None, root=None):
    """True when live edits exist and today's archive is not confirmed."""
    state = state if state is not None else load_state(root)
    edit_ts = float(state.get("last_map_edit_ts") or 0.0)
    if edit_ts <= 0:
        return False
    last_confirm = str(state.get("last_confirm_date") or "")
    return last_confirm != _today_iso()


def archive_status_report(root=None):
    """Multi-line GM status for ``gm maps archive``."""
    state = load_state(root)
    lines = ["Map archive status:"]
    edit_ts = float(state.get("last_map_edit_ts") or 0.0)
    if edit_ts > 0:
        when = datetime.fromtimestamp(edit_ts, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        who_map = state.get("last_map_edit_map") or "?"
        lines.append(f"  Last live map edit: {when} ({who_map})")
    else:
        lines.append("  Last live map edit: (none recorded this boot tree)")

    last_confirm = str(state.get("last_confirm_date") or "")
    if last_confirm:
        by = state.get("last_confirm_by") or "?"
        lines.append(f"  Last archive confirm: {last_confirm} by {by}")
    else:
        lines.append("  Last archive confirm: (never)")

    today = _today_iso()
    today_dir = os.path.join(archives_root(root), today)
    if os.path.isdir(today_dir):
        count = sum(
            1 for name in os.listdir(today_dir) if name.endswith(".json")
        )
        lines.append(f"  Today's archive ({today}): {count} file(s)")
    else:
        lines.append(f"  Today's archive ({today}): (not written)")

    if archive_due(state, root=root):
        lines.append(
            "  [ALERT] Daily archive OVERDUE — run: gm maps archive confirm"
        )
    else:
        lines.append("  Daily archive: up to date for today.")
    return "\r\n".join(lines)


def write_daily_archive(*, root=None, confirmed_by=""):
    """Snapshot every live map/zone into ``map_archives/<today>/``."""
    today = _today_iso()
    dest_dir = os.path.join(archives_root(root), today)
    os.makedirs(dest_dir, exist_ok=True)

    ok_ids = []
    errors = []
    for path in maps_mod.iter_map_json_paths():
        filename = os.path.basename(path)
        try:
            _path, _fn, map_id, kind, _data = map_backups.resolve_live_map(
                filename,
            )
            safe = map_backups._safe_map_id(map_id)
            dest = os.path.join(dest_dir, f"{safe}.json")
            shutil.copy2(path, dest)
            ok_ids.append(f"{map_id} ({kind})")
        except map_backups.MapBackupError as exc:
            errors.append(f"{filename}: {exc}")
        except OSError as exc:
            errors.append(f"{filename}: {exc}")

    state = load_state(root)
    state["last_confirm_date"] = today
    state["last_confirm_by"] = str(confirmed_by or "")
    state["last_confirm_ts"] = time.time()
    state["nag_next_tick"] = 0
    save_state(state, root=root)

    lines = [
        f"[MAP ARCHIVE] {len(ok_ids)} snapshot(s) under "
        f"content/map_archives/{today}/."
    ]
    if ok_ids:
        lines.append("  ok: " + ", ".join(ok_ids))
    if errors:
        lines.append(f"  failed ({len(errors)}):")
        for err in errors:
            lines.append(f"    - {err}")
    return "\r\n".join(lines)


def list_recent_archives(root=None, limit=7):
    """Return sorted dated archive folder names (newest first)."""
    root_dir = archives_root(root)
    if not os.path.isdir(root_dir):
        return []
    dirs = [
        name for name in os.listdir(root_dir)
        if os.path.isdir(os.path.join(root_dir, name))
        and name != os.path.basename(STATE_FILENAME)
        and len(name) == 10
    ]
    dirs.sort(reverse=True)
    return dirs[:limit]
