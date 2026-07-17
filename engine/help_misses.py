"""
help_misses.py — append-only log of failed `help <query>` lookups.

When a player types a help query that matches neither HELP_TOPICS nor
COMMANDS, we record it so maintainers can later spot missing topics vs
typos. File I/O only (same pattern as reports.py): no networking, no
world model.

Log lives beside riftforge.db (Game.report_dir) as help_misses.log —
never committed (.gitignore).
"""

import json
import os
from datetime import datetime


# Filename under Game.report_dir (same folder as bug_reports.log).
HELP_MISSES_FILE = "help_misses.log"


def _path(directory):
    """Return the absolute path for the help-misses log under directory."""
    return os.path.join(directory, HELP_MISSES_FILE)


def record(query, reporter, directory="."):
    """Append one failed help lookup as a single JSON line.

    query is the raw topic/verb string the player typed (already stripped /
    lowercased by cmd_help). reporter is the character key (or a placeholder
    if somehow session-less). Returns the payload that was written.

    Malformed earlier lines are irrelevant — we only append. Callers should
    not raise to the player if disk I/O fails; wrap at the call site if
    needed.
    """
    payload = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "reporter": reporter,
        "query": query,
    }
    path = _path(directory)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")
    return payload


def recent(n=50, directory="."):
    """Return the last n parsed miss records (oldest-first among them).

    Useful for a future GM triage verb or offline tooling. Missing/empty
    file -> []. Malformed lines are skipped so a truncated write cannot
    break readers.
    """
    if n is not None and n <= 0:
        return []
    path = _path(directory)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if n is None:
        return rows
    return rows[-n:]
