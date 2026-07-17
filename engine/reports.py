"""
reports.py — append and read player bug/suggestion reports.

File I/O only: no networking, no world model. Commands call record(); the
GM 'reports' command calls recent(), and the GM 'resolve' command calls
mark() to flip a report's status once it's been triaged. Each report is one
JSON line (JSONL) so appending is safe and reading the last N entries stays
simple.

Logs live beside riftforge.db (Game.report_dir) so Docker's host volume
keeps them across container rebuilds -- same treatment as the save file.
"""

import json
import os
import re
from datetime import datetime


# Kind strings used by callers and as the JSON "kind" field when useful.
BUG = "bug"
SUGGEST = "suggest"

# Optional post-append hooks: list of callback(kind, payload). Kept for
# future side effects; bug_webhook POSTs are GM-on-demand (squashbugs), not
# registered here anymore.
_after_record_hooks = []

# Separate files (user choice) -- never committed; see .gitignore.
_FILENAMES = {
    BUG: "bug_reports.log",
    SUGGEST: "suggestions.log",
}

# A report starts "open"; a GM later marks it "resolved" (fixed/built) or
# "rejected" (won't do) instead of the log growing forever with no way to
# tell triaged entries apart from new ones.
STATUSES = ("open", "resolved", "rejected")


def _path(kind, directory):
    """Return the absolute path for a report kind under directory."""
    filename = _FILENAMES.get(kind)
    if not filename:
        raise ValueError(f"unknown report kind: {kind!r}")
    return os.path.join(directory, filename)


def register_after_record(callback):
    """Register callback(kind, payload) to run after each successful record()."""
    _after_record_hooks.append(callback)


def record(kind, reporter, description, history, directory="."):
    """Append one timestamped report as a single JSON line.

    history is a list of [line, traceback_or_None] pairs from the session
    ring buffer (connection.Session.history). We split that into:
      - history: plain command lines (most recent last)
      - errors:  only the entries that carried a traceback, as
                 {"line": ..., "traceback": ...}

    Returns the payload dict that was written, including a 1-based ``id``
    (the physical line number in the JSONL file -- same numbering
    recent()/mark() use). Callers that POST a webhook (bugs only -- see
    engine/bug_webhook.py) can hand this dict straight to the notifier
    without re-reading the log.
    """
    lines = []
    errors = []
    for entry in history:
        # Each entry is [raw_line, traceback_or_None] -- a mutable list so
        # Session.play() can fill in the traceback after a failed dispatch.
        line = entry[0]
        tb = entry[1] if len(entry) > 1 else None
        lines.append(line)
        if tb:
            errors.append({"line": line, "traceback": tb})

    payload = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "reporter": reporter,
        "description": description,
        "history": lines,
        "errors": errors,
        "status": "open",
    }
    path = _path(kind, directory)
    # "a" appends; if the file doesn't exist yet, open creates it.
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")
    # Id = physical line count after the append (matches recent()'s
    # enumerate(..., start=1) over the same file). Count every line,
    # including any blank ones, so ids stay stable with mark().
    with open(path, encoding="utf-8") as f:
        payload["id"] = sum(1 for _ in f)
    for hook in _after_record_hooks:
        hook(kind, payload)
    return payload


def recent(kind, n, directory="."):
    """Return the last n parsed reports for kind (oldest-first among them).

    n=None means "no limit" -- return every entry. The GM 'reports' command
    uses this to filter by status BEFORE truncating to a count, so open
    entries buried behind a run of already-resolved ones aren't hidden.

    Missing or empty file -> []. Malformed lines are skipped so a corrupted
    trailing write can't break the GM 'reports' command.

    Each dict gets an "id" -- its 1-based line number within its own log
    file -- so a GM can reference it later with mark(). Ids stay stable
    because mark() only ever rewrites a line in place, never reorders or
    deletes one. Entries logged before the status field existed default to
    "open" here rather than needing a one-time file migration.
    """
    if n is not None and n <= 0:
        return []
    path = _path(kind, directory)
    if not os.path.isfile(path):
        return []

    entries = []
    with open(path, encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                # Skip a bad line rather than failing the whole listing.
                continue
            entry["id"] = line_no
            entry.setdefault("status", "open")
            entries.append(entry)
    if n is None:
        return entries
    # Slice the tail: entries[-n:] is the last n; if fewer exist, all of them.
    return entries[-n:]


def mark(kind, entry_id, status, directory="."):
    """Set the status of one report (by its recent()-assigned id) in place.

    Rewrites only that one JSON line, preserving every other line and their
    order -- the append-only file stays append-only except for this one
    targeted status flip. Raises ValueError for an unknown status and
    IndexError for an id outside the file's current line range, so callers
    (the GM 'resolve' command) can turn either into a friendly message.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    path = _path(kind, directory)
    if not os.path.isfile(path):
        raise IndexError(f"no {kind} reports logged yet")

    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    if entry_id < 1 or entry_id > len(lines):
        raise IndexError(f"no {kind} report #{entry_id}")

    payload = json.loads(lines[entry_id - 1])
    payload["status"] = status
    lines[entry_id - 1] = json.dumps(payload) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return payload


# GM 'gmsuggest' funnel (suggestions.log #39: "a GM command to add all
# non-addressed suggestions to the systems_design MD open decisions section
# so they can be addressed at the next feature implementation session").
_FUNNEL_START = "<!-- FUNNEL:START -->"
_FUNNEL_END = "<!-- FUNNEL:END -->"


def funnel_open_suggestions(directory=".", repo_root="."):
    """Append every still-`open` suggestion into the design doc's managed
    funnel block, so a later feature session can triage them into formal
    Open Decisions. Idempotent: entries already inside the block (matched by
    their stable suggestions.log id) are skipped on a re-run, and only the
    text BETWEEN the markers is ever rewritten -- every hand-authored line
    elsewhere in the file is untouched.

    Targets docs/SYSTEMS_DESIGN.md under repo_root when it exists. Falls
    back to a flat SUGGESTION_INBOX.md next to the report logs (same
    managed-block shape) for a runtime with only the report volume mounted
    and no full repo checkout (e.g. some Docker deployments).

    Returns (added, skipped) counts.
    """
    open_suggestions = [
        s for s in recent(SUGGEST, None, directory=directory)
        if s.get("status") == "open"
    ]

    design_doc = os.path.join(repo_root, "docs", "SYSTEMS_DESIGN.md")
    target = design_doc if os.path.isfile(design_doc) else os.path.join(
        directory, "SUGGESTION_INBOX.md"
    )

    text = ""
    if os.path.isfile(target):
        with open(target, encoding="utf-8") as f:
            text = f.read()

    if _FUNNEL_START in text and _FUNNEL_END in text:
        pre, rest = text.split(_FUNNEL_START, 1)
        block, post = rest.split(_FUNNEL_END, 1)
    elif text:
        # No block yet in an existing file -- append a fresh one at the end.
        pre = text if text.endswith("\n") else text + "\n"
        block, post = "\n", "\n"
    else:
        # Brand-new inbox file (the SUGGESTION_INBOX.md fallback path).
        pre = (
            "# Suggestion funnel\n\n"
            "Unaddressed suggestions staged by the GM `gmsuggest` command "
            "(suggestions.log #39) for the next feature session to triage "
            "into formal design decisions.\n\n"
        )
        block, post = "\n", "\n"

    # Ids already funneled -- "suggestions.log #N" is the tag every funneled
    # line carries, so re-scanning the block IS the dedup check.
    already_ids = {int(n) for n in re.findall(r"suggestions\.log #(\d+)", block)}

    added = 0
    skipped = 0
    new_lines = []
    for s in open_suggestions:
        if s["id"] in already_ids:
            skipped += 1
            continue
        new_lines.append(
            f"- **suggestions.log #{s['id']}** ({s.get('reporter', '?')}, "
            f"{s.get('time', '?')}): {s['description']}"
        )
        added += 1

    if new_lines:
        block = block.rstrip("\n") + "\n" + "\n".join(new_lines) + "\n"
    elif not block.strip():
        block = "\n"

    new_text = f"{pre}{_FUNNEL_START}\n{block.strip(chr(10))}\n{_FUNNEL_END}\n{post.lstrip(chr(10))}"
    with open(target, "w", encoding="utf-8") as f:
        f.write(new_text)

    return added, skipped
