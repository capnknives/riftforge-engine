"""job_catalog.py -- generic occupation table (id -> definition dict).

Games load and validate their JSON (Cadence behavior enums stay in the
game package), then ``install_table()`` so engine desk loops can look up
titles without importing the game.

stdlib only. No game imports.
"""

from __future__ import annotations

_TABLE = {}


def install_table(table):
    """Point the kernel at the game's live jobs dict (same object).

    Mutators in the game package keep writing the same dict; lookups here
    see those writes without a second copy.
    """
    global _TABLE
    if not isinstance(table, dict):
        raise TypeError("jobs table must be a dict")
    _TABLE = table


def jobs_table():
    """Return the installed occupation dict (may be empty)."""
    return _TABLE


def get(job_id):
    """Return one occupation row, or None."""
    raw = str(job_id or "").strip().lower()
    if not raw:
        return None
    return _TABLE.get(raw)


def known_jobs():
    """Sorted occupation ids currently installed."""
    return sorted(_TABLE)


def all_jobs():
    """Shallow copy of id -> defn (safe to iterate while editing)."""
    return dict(_TABLE)


def title(job_id):
    """Player-facing job title, or the id when unknown."""
    raw = str(job_id or "").strip()
    row = get(raw)
    if not row:
        return raw
    return (row.get("title") or raw).strip() or raw
