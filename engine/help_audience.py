"""Help index audience -- player ``help`` vs staff ``gmhelp``.

Static topic lookup gating (GM / Builder hubs, spoiler paths, ``gm*`` pages)
lives in ``supers.help_audience.build_staff_only_help_keywords`` and
``engine.hooks.is_gm_only_help``. This module strips staff categories from
the player handbook index and rewrites staff ``help X`` refs to ``gmhelp X``.
"""

from __future__ import annotations

import re

# Category titles that list staff cheat-sheets, not player hubs.
STAFF_HELP_CATEGORIES = frozenset({"gm", "builder"})

# ``help <keyword>`` inside a staff page should point at gmhelp when the
# keyword is staff-only. Multi-word keys (gm-eve-runbook) try longest prefix.
_HELP_REF_RE = re.compile(
    r"\bhelp ([a-z][a-z0-9_-]*(?:[ ][a-z0-9][a-z0-9_-]*){0,4})",
    re.I,
)


def flatten_help_categories(categories):
    """Return [(name, blurb), ...] from HELP_CATEGORIES rows, one row per
    unique (case-insensitive) topic name.

    A topic can legitimately live in several categories for the
    *categorized* bare ``help`` browse view (grouped by heading, so seeing
    the same name under two headings makes sense there). But the flat A-Z
    catalog (``help topics`` / ``help gmtopics``) has no headings to
    explain a repeat -- it just prints the same name back-to-back once per
    category it is cross-listed in (bug report 934: ``help form`` showed
    up 6 times). Dedupe here so the flat list matches what "one page per
    name" implies; first occurrence's blurb wins.
    """
    flat = []
    seen: set[str] = set()
    for _category, entries in categories or []:
        for entry in entries or ():
            if isinstance(entry, (list, tuple)):
                name = entry[0]
                blurb = entry[1] if len(entry) > 1 else ""
            else:
                name = entry
                blurb = ""
            key = str(name).strip().lower()
            if key in seen:
                continue
            seen.add(key)
            flat.append((name, blurb))
    return flat


def sort_help_entries(entries):
    """Case-insensitive A-Z sort for help index rows."""
    return sorted(entries or [], key=lambda row: str(row[0]).lower())


def category_help_entries(categories, category_name):
    """Return one category's index rows, or [] when missing."""
    want = str(category_name or "").strip().lower()
    for category, entries in categories or []:
        if str(category).strip().lower() == want:
            return list(entries or ())
    return []


def filter_help_categories(categories, *, is_gm):
    """Return HELP_CATEGORIES rows visible to this viewer.

    Shared hubs (``form``, ``fuel``) are listed in several Origin bands.
    Dedup case-insensitively so ``help topics`` lists each key once
    (bug report 934). First category wins.
    """
    from engine import hooks

    seen = set()
    out = []
    for category, entries in categories or []:
        if not is_gm and str(category).strip().lower() in STAFF_HELP_CATEGORIES:
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
            if not is_gm and hooks.is_gm_only_help(key):
                continue
            if key in seen:
                continue
            seen.add(key)
            visible.append(row)
        if visible:
            out.append((category, visible))
    return out


def is_staff_command_help(help_text):
    """True when a COMMANDS one-liner is a staff verb, not a player page."""
    text = (help_text or "").strip()
    if not text:
        return False
    low = text.lower()
    return low.startswith("gm:") or low.startswith("head gm:")


def rewrite_staff_help_refs(text):
    """Point ``help <staff-topic>`` at ``gmhelp <staff-topic>`` on staff pages.

    Player hubs in the same See also footer stay on ``help``. Longest
    matching prefix wins so ``help gm criminal`` becomes
    ``gmhelp gm criminal`` without eating the filter word.
    """
    from engine import hooks

    if not text:
        return text

    extra = {"gmtopics", "gmhelp"}

    def _repl(match):
        words = match.group(1).split()
        for n in range(len(words), 0, -1):
            key = " ".join(words[:n]).lower()
            if hooks.is_gm_only_help(key) or key in extra:
                rest = " ".join(words[n:])
                out = "gmhelp " + " ".join(words[:n])
                if rest:
                    out += " " + rest
                return out
        return match.group(0)

    return _HELP_REF_RE.sub(_repl, text)
