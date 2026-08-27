"""Help index audience -- filter categorized bare ``help`` for staff sections.

Static topic lookup gating (GM / Builder hubs, spoiler paths, ``gm*`` pages)
lives in ``supers.help_audience.build_staff_only_help_keywords`` and
``engine.hooks.is_gm_only_help``. This module only strips staff categories
from the index players see on bare ``help`` / ``help topics``.
"""

from __future__ import annotations

# Category titles that list staff cheat-sheets, not player hubs.
STAFF_HELP_CATEGORIES = frozenset({"gm", "builder"})


def filter_help_categories(categories, *, is_gm):
    """Return HELP_CATEGORIES rows visible to this viewer."""
    if is_gm:
        return list(categories or [])
    from engine import hooks

    out = []
    for category, entries in categories or []:
        if str(category).strip().lower() in STAFF_HELP_CATEGORIES:
            continue
        visible = []
        for entry in entries or ():
            if isinstance(entry, (list, tuple)):
                key = str(entry[0]).strip().lower()
                blurb = entry[1] if len(entry) > 1 else ""
                row = (entry[0], blurb) if len(entry) > 1 else entry[0]
            else:
                key = str(entry).strip().lower()
                row = entry
            if hooks.is_gm_only_help(key):
                continue
            visible.append(row)
        if visible:
            out.append((category, visible))
    return out
