"""
pager.py -- chunk long staff/player dumps so clients are not flooded.

Long multi-line output (e.g. ``gm where body``) goes through ``page()``:
the first slice is sent immediately; the rest waits on the character until
they type ``more`` (next page) or ``stop`` (discard).

Session-only queue -- not persisted. Page size is ``character.pager_lines``
(default 20; ``config pager <N>``).
"""

from __future__ import annotations

# Default lines per page (not counting the "-- More --" footer).
DEFAULT_PAGE_LINES = 20
PAGE_LINES_MIN = 5
PAGE_LINES_MAX = 100

# Attribute holding remaining lines (list of str, no trailing CR).
_QUEUE_ATTR = "_pager_queue"


def page_size(character):
    """How many content lines to send per ``more`` page."""
    try:
        n = int(getattr(character, "pager_lines", DEFAULT_PAGE_LINES))
    except (TypeError, ValueError):
        n = DEFAULT_PAGE_LINES
    return max(PAGE_LINES_MIN, min(PAGE_LINES_MAX, n))


def clear(character):
    """Discard any waiting pager text. Returns how many lines were dropped."""
    queue = getattr(character, _QUEUE_ATTR, None) or []
    n = len(queue)
    setattr(character, _QUEUE_ATTR, [])
    return n


def pending_count(character):
    """Lines still waiting behind the pager."""
    return len(getattr(character, _QUEUE_ATTR, None) or [])


def page(character, lines, *, note=None):
    """Send *lines* through the pager (list of strings, or one multi-line str).

    If everything fits in one page, send it all with no footer. Otherwise
    send the first page and stash the rest for ``more`` / ``stop``.
    """
    if character is None or getattr(character, "session", None) is None:
        return
    if isinstance(lines, str):
        # Split on real newlines; callers usually pass a list already.
        text = lines.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
    else:
        lines = [str(ln) for ln in (lines or [])]

    # Starting a new dump replaces any unfinished pager.
    clear(character)

    if note:
        character.session.send(note)

    if not lines:
        return

    size = page_size(character)
    if len(lines) <= size:
        for ln in lines:
            character.session.send(ln)
        return

    first = lines[:size]
    rest = lines[size:]
    for ln in first:
        character.session.send(ln)
    setattr(character, _QUEUE_ATTR, rest)
    character.session.send(
        f"-- More: {len(rest)} line(s) left. Type 'more' or 'stop'. --"
    )


def cmd_more(character, args, game):
    """Continue paged output (next slice of a long dump)."""
    rest = list(getattr(character, _QUEUE_ATTR, None) or [])
    if not rest:
        character.session.send(
            "No more text waiting. "
            "(Long lists page automatically -- see 'help more'.)"
        )
        return
    # Re-enter page() with the remainder (it clears then re-queues).
    page(character, rest)


def cmd_stop(character, args, game):
    """Discard remaining paged output so a runaway dump stops."""
    n = clear(character)
    if n:
        character.session.send(f"Stopped -- discarded {n} waiting line(s).")
    else:
        character.session.send("Nothing to stop (pager is empty).")
