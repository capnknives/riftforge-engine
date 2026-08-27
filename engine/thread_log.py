"""
thread_log.py -- JSONL rows with stable line ids and in-place rewrite.

Generic persistence for threaded records. Callers own the filename, row
schema, and permission policy -- this module never mixes logs:

  * player social board -- supers/player_social.py (player_social.log)
  * helper query tickets -- engine/player_queries.py (player_queries.log)
  * staff tickets -- engine/reports.py (bug_reports.log / suggestions.log / …)

Ids are 1-based **physical line numbers**. Rewrite never reorders or
deletes a line, so ids stay stable (same contract as reports.mark).

File I/O only: no networking, no world model.
"""

import json
import os


class ThreadLogIOError(OSError):
    """JSONL thread log read/write failed (permissions, bind-mount, …)."""


def ensure_file(path, *, log_label="thread_log"):
    """Create an empty writable JSONL file (Docker bind-mount safe).

    Same chmod-plus-append probe as reports.ensure_report_logs so Windows
    bind-mounts that land read-only still get a chance to become writable.
    """
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        pass
    try:
        if not os.path.isfile(path):
            with open(path, "a", encoding="utf-8"):
                pass
        try:
            os.chmod(path, 0o666)
        except OSError:
            pass
        with open(path, "a", encoding="utf-8"):
            pass
    except OSError as exc:
        print(
            f"[{log_label}] ensure_file {os.path.basename(path)}: "
            f"{exc!r}",
            flush=True,
        )


def read_raw_lines(path):
    """Return every physical line (including blanks). Missing file -> []."""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.readlines()
    except OSError as exc:
        raise ThreadLogIOError(f"cannot read {path!r}: {exc}") from exc


def parse_entry(raw, line_no, *, defaults=None):
    """Parse one JSONL line. Blank / malformed -> None.

    ``line_no`` is the 1-based physical line number and becomes ``id``.
    ``defaults`` is an optional dict of keys to setdefault on a good parse
    (status, messages, visibility, …) so callers do not fork the skip logic.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        entry = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(entry, dict):
        return None
    entry["id"] = line_no
    for key, value in (defaults or {}).items():
        entry.setdefault(key, value)
    return entry


def read_all(path, *, defaults=None):
    """Parsed entries oldest-first; skip blank / malformed; id = line number.

    Missing file -> []. IO errors raise ThreadLogIOError so callers can
    print-and-empty (listings) or surface a verb error (writes).
    """
    lines = read_raw_lines(path)
    entries = []
    for line_no, raw in enumerate(lines, start=1):
        entry = parse_entry(raw, line_no, defaults=defaults)
        if entry is not None:
            entries.append(entry)
    return entries


def get_by_id(path, entry_id, *, defaults=None):
    """Return one parsed row by physical line id, or None."""
    try:
        entry_id = int(entry_id)
    except (TypeError, ValueError):
        return None
    if entry_id < 1:
        return None
    lines = read_raw_lines(path)
    if entry_id > len(lines):
        return None
    return parse_entry(lines[entry_id - 1], entry_id, defaults=defaults)


def append_line(path, payload, *, compact=False):
    """Append one JSON object and stamp ``id`` as the new physical line count."""
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        pass
    body = _dump(payload, compact=compact)
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(body + "\n")
        with open(path, encoding="utf-8") as handle:
            payload["id"] = sum(1 for _ in handle)
    except OSError as exc:
        raise ThreadLogIOError(f"cannot append {path!r}: {exc}") from exc
    return payload


def rewrite_line(path, entry_id, payload, *, compact=False, missing="no thread"):
    """Replace one physical line in place. Raises IndexError if the id is out of range."""
    if not os.path.isfile(path):
        raise IndexError(f"{missing} yet")
    try:
        lines = read_raw_lines(path)
    except ThreadLogIOError:
        raise
    if entry_id < 1 or entry_id > len(lines):
        raise IndexError(f"{missing} #{entry_id}")
    payload["id"] = entry_id
    lines[entry_id - 1] = _dump(payload, compact=compact) + "\n"
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
    except OSError as exc:
        raise ThreadLogIOError(f"cannot update {path!r}: {exc}") from exc
    return payload


def _dump(payload, *, compact=False):
    """Serialize a row. Compact (no spaces) matches player_queries.log."""
    if compact:
        return json.dumps(payload, separators=(",", ":"))
    return json.dumps(payload)
