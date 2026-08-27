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

from engine import thread_log


# Kind strings used by callers and as the JSON "kind" field when useful.
BUG = "bug"
SUGGEST = "suggest"
# Player-submitted helpfile drafts (cmd_helpsubmit). Reuses this whole
# module -- JSONL log, open/resolved/rejected status, gm_notify ping -- so a
# GM reviews these the exact same way as a bug/suggestion (docs/plans/
# helpfile_editing_system.md), instead of a bespoke proposal table + queue UI.
HELP = "help"
# Copy typos / grammar in rooms, help, and combat lines -- separate from
# bugs so staff can prioritize them without mixing crash reports (idea 194).
TYPO = "typo"

# Optional post-append hooks: list of callback(kind, payload). Kept for
# future side effects; bug_webhook POSTs are GM-on-demand (squashbugs), not
# registered here anymore.
_after_record_hooks = []
# Optional hooks after mark() flips status (e.g. thank bug reporters).
_after_mark_hooks = []

# Separate files (user choice) -- never committed; see .gitignore.
_FILENAMES = {
    BUG: "bug_reports.log",
    SUGGEST: "suggestions.log",
    HELP: "help_proposals.log",
    TYPO: "typos.log",
}

# A report starts "open"; a GM later marks it "resolved" (fixed/built) or
# "rejected" (won't do) instead of the log growing forever with no way to
# tell triaged entries apart from new ones.
STATUSES = ("open", "resolved", "rejected")

# Threaded comments (suggestion 258 remainder). Same messages[] shape as
# the player board / helper queries -- separate files, shared kit.
MAX_COMMENTS_PER_TICKET = 40
MAX_COMMENT_CHARS = 400

_READ_DEFAULTS = {
    "status": "open",
    "messages": [],
}

# Labels shown in GM ``reports`` / ``reports show`` output.
DISPLAY_LABELS = {
    BUG: "BUG",
    SUGGEST: "IDEA",
    HELP: "HELP",
    TYPO: "TYPO",
}

# Parse words staff type after ``show`` / ``resolve`` (bug, idea, …).
KIND_ALIASES = {
    "bug": BUG,
    "bugs": BUG,
    "suggest": SUGGEST,
    "suggestion": SUGGEST,
    "suggestions": SUGGEST,
    "idea": SUGGEST,
    "ideas": SUGGEST,
    "help": HELP,
    "typo": TYPO,
    "typos": TYPO,
}

# Sections for the GM ``reports`` list (all-kinds view, oldest-first within
# each block). Order matches the combined ``gm reports`` sheet.
REPORT_LIST_SECTIONS = (
    (BUG, "Bugs:", "BUG"),
    (SUGGEST, "Ideas:", "IDEA"),
    (TYPO, "Typos:", "TYPO"),
    (HELP, "Help ideas:", "HELP"),
)


class ReportsIOError(thread_log.ThreadLogIOError):
    """Report log append/update failed (permissions, read-only bind-mount, …)."""


def ensure_report_logs(directory="."):
    """Create JSONL report files with best-effort writable permissions.

    Docker bind-mounts on Windows can leave ``bug_reports.log`` /
    ``suggestions.log`` read-only on the host; without a writable file the
    tick credit reconcile and player ``bug`` / ``suggest`` verbs crash the
    game child. Call once from ``Game.__init__`` (and after manual restores).
    """
    directory = directory or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        pass
    for kind in _FILENAMES:
        thread_log.ensure_file(_path(kind, directory), log_label="reports")


def parse_kind_word(kind_word):
    """Map a staff-facing kind token to BUG / SUGGEST / HELP / TYPO, or None."""
    return KIND_ALIASES.get((kind_word or "").lower())


def _path(kind, directory):
    """Return the absolute path for a report kind under directory."""
    filename = _FILENAMES.get(kind)
    if not filename:
        raise ValueError(f"unknown report kind: {kind!r}")
    return os.path.join(directory, filename)


def register_after_record(callback):
    """Register callback(kind, payload) to run after each successful record()."""
    _after_record_hooks.append(callback)


def register_after_mark(callback):
    """Register callback(kind, payload, *, old_status, directory, game) after mark()."""
    _after_mark_hooks.append(callback)


def record(kind, reporter, description, history, directory=".", context=None,
           subject=None):
    """Append one timestamped report as a single JSON line.

    history is a list of [line, traceback_or_None] pairs from the session
    ring buffer (connection.Session.history). We split that into:
      - history: plain command lines (most recent last)
      - errors:  only the entries that carried a traceback, as
                 {"line": ..., "traceback": ...}

    context is an optional dict of diagnostic facts (room, vitals, mission,
    …) from engine.report_context.build(). Suggestions / typos / help ideas
    attach a slim identity/location snapshot instead of the full dump.

    Returns the payload dict that was written, including a 1-based ``id``
    (the physical line number in the JSONL file -- same numbering
    recent()/mark() use). Callers that POST a webhook (bugs via
    engine/bug_webhook.py; suggestions via engine/suggestion_webhook.py --
    both GM-on-demand) can hand this dict straight to the notifier without
    re-reading the log.
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
        "messages": [],
    }
    if context:
        payload["context"] = context
    if subject:
        payload["subject"] = subject
    path = _path(kind, directory)
    try:
        payload = thread_log.append_line(path, payload)
    except thread_log.ThreadLogIOError as exc:
        print(
            f"[reports] record failed ({_FILENAMES.get(kind, kind)}): {exc!r}",
            flush=True,
        )
        raise ReportsIOError(
            f"cannot write report log {path!r}: {exc}"
        ) from exc
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
    try:
        entries = thread_log.read_all(path, defaults=_READ_DEFAULTS)
    except thread_log.ThreadLogIOError as exc:
        print(
            f"[reports] recent read failed ({_FILENAMES.get(kind, kind)}): "
            f"{exc!r}",
            flush=True,
        )
        return []
    if n is None:
        return entries
    # Slice the tail: entries[-n:] is the last n; if fewer exist, all of them.
    return entries[-n:]


def get_by_id(kind, entry_id, directory="."):
    """Return one parsed report by its stable id, or None when missing."""
    for entry in recent(kind, None, directory=directory):
        if entry.get("id") == entry_id:
            return entry
    return None


def by_reporter(kind, reporter_key, directory=".", *, open_only=True):
    """Return every report filed by ``reporter_key`` (``Character.key``).

    Default ``open_only=True`` keeps resolved/rejected tickets out of the
    player ``bugs`` / ``ideas`` listings -- the usual "have I already filed
    this?" check. Pass ``open_only=False`` when the player asks for full
    history (``bugs all``). Oldest-first, same order as ``recent()``.
    """
    needle = (reporter_key or "").strip()
    if not needle:
        return []
    out = []
    for entry in recent(kind, None, directory=directory):
        if entry.get("reporter") != needle:
            continue
        if open_only and entry.get("status", "open") != "open":
            continue
        out.append(entry)
    return out


def format_entry_lines(kind, entry, *, game=None):
    """Plain-text detail body for one report (GM show / host sync_reports)."""
    label = DISPLAY_LABELS.get(kind, (kind or "?").upper())
    entry_id = entry.get("id", "?")
    status = entry.get("status", "open")
    reporter_raw = entry.get("reporter", "?")
    reporter = reporter_raw
    if game is not None:
        from engine.char_identity import reporter_display_name

        reporter = reporter_display_name(game, reporter_raw)

    lines = [
        f"{label} #{entry_id} ({status})",
        f"time: {entry.get('time', '?')}",
        f"reporter: {reporter}",
    ]
    subject_raw = entry.get("subject")
    if subject_raw:
        if game is not None:
            from engine.char_identity import reporter_display_name

            subject_label = reporter_display_name(game, subject_raw)
        else:
            subject_label = subject_raw
        lines.append(f"subject: {subject_label}")
    lines.extend([
        "",
        (entry.get("description") or "").strip(),
    ])
    history = entry.get("history") or []
    if history:
        lines.append("")
        lines.append("history:")
        for line in history:
            lines.append(f"  {line}")
    errors = entry.get("errors") or []
    if errors:
        lines.append("")
        lines.append("errors:")
        for err in errors:
            lines.append(f"  > {err.get('line', '?')}")
            tb = (err.get("traceback") or "").strip()
            if tb:
                for tb_line in tb.splitlines():
                    lines.append(f"    {tb_line}")
    context = entry.get("context")
    if context:
        lines.append("")
        lines.append("context:")
        lines.append(json.dumps(context, indent=2, sort_keys=True))
    lines.extend(_comment_lines(entry, game=game))
    return lines


def _comment_lines(entry, *, game=None):
    """Shared comment block for GM show and player bugs read."""
    messages = entry.get("messages") or []
    if not messages:
        return []
    from engine.char_identity import reporter_display_name

    lines = ["", "comments:"]
    for msg in messages:
        who_raw = msg.get("from") or msg.get("author") or "?"
        who = reporter_display_name(game, who_raw) if game is not None else who_raw
        staff_bit = " [staff]" if msg.get("staff") else ""
        stamp = msg.get("time") or msg.get("tick") or "?"
        text = (msg.get("text") or "").strip()
        lines.append(f"  [{stamp}] {who}{staff_bit}: {text}")
    return lines


def comment_count(entry):
    """How many threaded comments sit on this ticket (0 if none)."""
    return len(entry.get("messages") or [])


def format_player_thread_lines(kind, entry, *, game=None):
    """Lean player dump: description + comments, no history/context."""
    label = DISPLAY_LABELS.get(kind, (kind or "?").upper())
    entry_id = entry.get("id", "?")
    status = entry.get("status", "open")
    lines = [
        f"{label} #{entry_id} ({status})",
        f"filed: {entry.get('time', '?')}",
        "",
        (entry.get("description") or "").strip() or "(no description)",
    ]
    comments = _comment_lines(entry, game=game)
    if comments:
        lines.extend(comments)
    else:
        lines.extend(["", "(no comments yet)"])
    cmd = {
        BUG: "bugs",
        SUGGEST: "ideas",
        TYPO: "typos",
        HELP: "helpsubmit",
    }.get(kind, "bugs")
    if entry.get("status") == "open":
        lines.extend([
            "",
            f"Add a note: {cmd} comment {entry_id} <text>",
        ])
    return lines


def can_player_comment(entry, character_key):
    """Reporter may comment only while the ticket is still open."""
    if entry is None:
        return False
    if (entry.get("status") or "open") != "open":
        return False
    needle = (character_key or "").strip()
    return bool(needle) and entry.get("reporter") == needle


def can_player_read(entry, character_key):
    """Reporter may read their own ticket (any status)."""
    if entry is None:
        return False
    needle = (character_key or "").strip()
    return bool(needle) and entry.get("reporter") == needle


def append_comment(
    kind, entry_id, author_key, text, directory=".", *,
    staff=False, tick=0,
):
    """Append one messages[] row. Does not flip status (staff still resolve)."""
    body = (text or "").strip()
    if not body:
        raise ValueError("comment text required")
    if len(body) > MAX_COMMENT_CHARS:
        raise ValueError(
            f"Keep comments under {MAX_COMMENT_CHARS} characters "
            f"(yours is {len(body)})."
        )
    entry = get_by_id(kind, entry_id, directory=directory)
    if entry is None:
        raise IndexError(f"no {kind} report #{entry_id}")
    messages = list(entry.get("messages") or [])
    if len(messages) >= MAX_COMMENTS_PER_TICKET:
        raise ValueError(
            f"Ticket #{entry_id} hit the {MAX_COMMENTS_PER_TICKET}-comment cap."
        )
    messages.append({
        "from": author_key or "?",
        "text": body,
        "tick": int(tick or 0),
        "time": datetime.now().isoformat(timespec="seconds"),
        "staff": bool(staff),
    })
    entry["messages"] = messages
    path = _path(kind, directory)
    try:
        return thread_log.rewrite_line(
            path, entry_id, entry, missing=f"no {kind} report",
        )
    except thread_log.ThreadLogIOError as exc:
        raise ReportsIOError(str(exc)) from exc


def mark(kind, entry_id, status, directory=".", game=None):
    """Set the status of one report (by its recent()-assigned id) in place.

    Rewrites only that one JSON line, preserving every other line and their
    order -- the append-only file stays append-only except for this one
    targeted status flip. Raises ValueError for an unknown status and
    IndexError for an id outside the file's current line range, so callers
    (the GM 'resolve' command) can turn either into a friendly message.

    Optional ``game`` is passed through to after-mark hooks (e.g. thanking an
    online bug reporter when status becomes ``resolved``).
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    path = _path(kind, directory)
    payload = get_by_id(kind, entry_id, directory=directory)
    if payload is None:
        if not os.path.isfile(path):
            raise IndexError(f"no {kind} reports logged yet")
        raise IndexError(f"no {kind} report #{entry_id}")

    old_status = payload.get("status", "open")
    payload["status"] = status
    try:
        payload = thread_log.rewrite_line(
            path, entry_id, payload, missing=f"no {kind} report",
        )
    except thread_log.ThreadLogIOError as exc:
        raise ReportsIOError(
            f"cannot update report log {path!r}: {exc}"
        ) from exc
    for hook in _after_mark_hooks:
        hook(kind, payload, old_status=old_status, directory=directory, game=game)
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
