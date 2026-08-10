"""
quest_flags.py -- generic bookkeeping for ``Character.quest_progress``.

Each quest id maps to a small persisted blob::

    {
      "status": "active" | "done" | "abandoned",
      "step": 0,
      "flags": {},
    }

This module owns **only** dict get/create and field writes on that shape.
Authored-quest policy (loader, notify gates, grants, spawns, prose) stays
in ``engine.systems.quests`` and game hook registrations.
"""

from __future__ import annotations


def progress(character):
    """Return ``character.quest_progress``, creating ``{}`` when absent."""
    box = getattr(character, "quest_progress", None)
    if box is None or not isinstance(box, dict):
        character.quest_progress = {}
        return character.quest_progress
    return box


def entry(character, quest_id):
    """Return one quest progress entry, or ``None`` when the id is unknown."""
    return progress(character).get(quest_id)


def _ensure_entry(character, quest_id, *, status="active", step=0):
    """Return an existing entry or create a default-shaped new one."""
    prog = progress(character)
    ent = prog.get(quest_id)
    if ent is None:
        ent = {"status": status, "step": step, "flags": {}}
        prog[quest_id] = ent
    flags = ent.get("flags")
    if flags is None or not isinstance(flags, dict):
        ent["flags"] = {}
    return ent


def set_status(character, quest_id, status, *, step=None):
    """Set ``status`` (and optional ``step``) on a quest progress entry.

    Creates a default entry when the quest id was not yet tracked.
    """
    ent = _ensure_entry(character, quest_id, status=status, step=step or 0)
    ent["status"] = status
    if step is not None:
        ent["step"] = step
    return ent


def get_flag(character, quest_id, flag_name, default=None):
    """Read one key from the quest's ``flags`` sub-dict."""
    ent = entry(character, quest_id)
    if not ent:
        return default
    flags = ent.get("flags")
    if not isinstance(flags, dict):
        return default
    return flags.get(flag_name, default)


def set_flag(character, quest_id, flag_name, value):
    """Write one key into the quest's ``flags`` sub-dict.

    Creates the quest entry and ``flags`` dict when either is missing.
    """
    ent = _ensure_entry(character, quest_id)
    flags = ent.setdefault("flags", {})
    flags[flag_name] = value
    return value
