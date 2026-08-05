"""
quests_loader.py -- load and validate authored quest JSON catalogs.

Games register one or more quest content directories via
``set_quests_dirs``. Validation accepts built-in ``complete_when`` types
plus any extra types registered through ``register_complete_when_types``
(SUPERS registers supernatural predicate names at boot).

Fail loud on missing ids / bad step shapes so bad content never boots
quietly. Cache clears via ``_clear_caches_for_tests`` in smoke.
"""

from __future__ import annotations

import glob
import json
import os

# Built-in predicate types any game can use without registering extensions.
BASE_COMPLETE_WHEN_TYPES = frozenset({
    "start",
    "talk_npc",
    "verb",
    "any_verbs",
    "any_of",
    "enter_room",
    "kill_tag",
    "give_item",
    "has_item",
    "flag",
    "item",
    "buy",
    "help_topic",
})

_EXTRA_COMPLETE_WHEN_TYPES: set[str] = set()
_QUESTS_DIRS: list[str] = []
_CACHE: dict = {}


def set_quests_dirs(dirs):
    """Register quest JSON directories (additive — later dirs extend the catalog)."""
    global _QUESTS_DIRS, _CACHE
    for d in dirs or []:
        path = str(d)
        if path not in _QUESTS_DIRS:
            _QUESTS_DIRS.append(path)
    _CACHE = {}


def register_complete_when_types(types):
    """Extend validation with extra ``complete_when.type`` strings."""
    global _EXTRA_COMPLETE_WHEN_TYPES
    for name in types or []:
        if name:
            _EXTRA_COMPLETE_WHEN_TYPES.add(str(name))


def allowed_complete_when_types():
    """Union of built-in and game-registered predicate type names."""
    return BASE_COMPLETE_WHEN_TYPES | frozenset(_EXTRA_COMPLETE_WHEN_TYPES)


def _clear_caches_for_tests():
    """Reset quest catalog cache (smoke / unit helpers)."""
    global _CACHE
    _CACHE = {}


def validate_quest(data, *, where="quest"):
    """Fail loud if a quest dict is missing required fields."""
    if not isinstance(data, dict):
        raise AssertionError(f"{where}: quest must be a dict")
    qid = data.get("id")
    if not qid or not isinstance(qid, str):
        raise AssertionError(f"{where}: id must be a non-empty string")
    if not data.get("title"):
        raise AssertionError(f"{where}: title required")
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise AssertionError(f"{where}: steps must be a non-empty list")
    seen = set()
    for i, step in enumerate(steps):
        sw = f"{where} steps[{i}]"
        if not isinstance(step, dict):
            raise AssertionError(f"{sw}: must be a dict")
        sid = step.get("id")
        if not sid or not isinstance(sid, str):
            raise AssertionError(f"{sw}: id required")
        if sid in seen:
            raise AssertionError(f"{sw}: duplicate step id {sid!r}")
        seen.add(sid)
        when = step.get("complete_when") or {}
        if not isinstance(when, dict):
            raise AssertionError(f"{sw}: complete_when must be a dict")
        _validate_when(when, where=sw)
    return data


def _validate_when(when, *, where):
    """Recursively validate complete_when (supports any_of nesting)."""
    kind = when.get("type")
    if kind == "any_of":
        opts = when.get("options")
        if not isinstance(opts, list) or not opts:
            raise AssertionError(f"{where}: any_of needs a non-empty options list")
        for i, opt in enumerate(opts):
            if not isinstance(opt, dict):
                raise AssertionError(f"{where} options[{i}]: must be a dict")
            _validate_when(opt, where=f"{where} options[{i}]")
        return
    allowed = allowed_complete_when_types()
    if kind not in allowed:
        raise AssertionError(
            f"{where}: complete_when.type must be one of "
            f"{sorted(allowed)}, got {kind!r}"
        )


def load_quests():
    """Load every ``*.json`` quest file from registered dirs into a dict by id."""
    global _CACHE
    if _CACHE:
        return _CACHE
    by_id = {}
    for quests_dir in _QUESTS_DIRS:
        pattern = os.path.join(quests_dir, "*.json")
        for path in sorted(glob.glob(pattern)):
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and data.get("_guide") and not data.get("id"):
                continue
            validate_quest(data, where=path)
            qid = data["id"]
            if qid in by_id:
                raise AssertionError(f"duplicate quest id {qid!r} in {path}")
            by_id[qid] = data
    _CACHE = by_id
    return _CACHE


def get_quest(quest_id):
    """Return one quest dict or None."""
    if not quest_id:
        return None
    return load_quests().get(quest_id)


def list_quest_ids():
    """Sorted catalog ids."""
    return sorted(load_quests().keys())


# Back-compat alias used by older imports.
ALLOWED_COMPLETE_WHEN_TYPES = allowed_complete_when_types()
