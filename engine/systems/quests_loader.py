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
    "quest_flag",
    "character_flag",
    "dialogue_done",
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


def _reset_quests_dirs_for_tests(dirs):
    """Replace quest dirs (smoke tests only — not for production boot)."""
    global _QUESTS_DIRS, _CACHE
    _QUESTS_DIRS = [str(d) for d in (dirs or [])]
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
    chain_id = data.get("chain_id")
    if chain_id is not None and not isinstance(chain_id, str):
        raise AssertionError(f"{where}: chain_id must be a string when set")
    chapter = data.get("chapter")
    if chapter is not None and not isinstance(chapter, int):
        raise AssertionError(f"{where}: chapter must be an int when set")
    chain_title = data.get("chain_title")
    if chain_title is not None and not isinstance(chain_title, str):
        raise AssertionError(f"{where}: chain_title must be a string when set")
    requires_quest = data.get("requires_quest")
    if requires_quest is not None:
        if not isinstance(requires_quest, list):
            raise AssertionError(f"{where}: requires_quest must be a list when set")
        for i, req_id in enumerate(requires_quest):
            if not req_id or not isinstance(req_id, str):
                raise AssertionError(
                    f"{where}: requires_quest[{i}] must be a non-empty string"
                )
    requires = data.get("requires")
    if requires is not None:
        if not isinstance(requires, dict):
            raise AssertionError(f"{where}: requires must be a dict when set")
        _validate_when(requires, where=f"{where} requires")
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
        nxt = step.get("next")
        if nxt is not None and not isinstance(nxt, str):
            raise AssertionError(f"{sw}: next must be a string when set")
        dialogue_tree = step.get("dialogue_tree")
        if dialogue_tree is not None and not isinstance(dialogue_tree, str):
            raise AssertionError(f"{sw}: dialogue_tree must be a string when set")
        grant = step.get("grant")
        if grant is not None:
            if isinstance(grant, list):
                for gi, g in enumerate(grant):
                    if not isinstance(g, dict):
                        raise AssertionError(
                            f"{sw}: grant[{gi}] must be a dict"
                        )
            elif not isinstance(grant, dict):
                raise AssertionError(f"{sw}: grant must be a dict or list")
        branches = step.get("branches")
        if branches is not None:
            if not isinstance(branches, list):
                raise AssertionError(f"{sw}: branches must be a list when set")
            for bi, branch in enumerate(branches):
                bw = f"{sw} branches[{bi}]"
                if not isinstance(branch, dict):
                    raise AssertionError(f"{bw}: must be a dict")
                branch_next = branch.get("next")
                if not branch_next or not isinstance(branch_next, str):
                    raise AssertionError(f"{bw}: next must be a non-empty string")
                branch_when = branch.get("when") or {}
                if not isinstance(branch_when, dict):
                    raise AssertionError(f"{bw}: when must be a dict")
                _validate_when(branch_when, where=bw)
    # Dangling next / branch targets fail after all step ids are known.
    step_ids = seen
    for i, step in enumerate(steps):
        sw = f"{where} steps[{i}]"
        nxt = step.get("next")
        if nxt and nxt not in step_ids:
            raise AssertionError(f"{sw}: next references unknown step id {nxt!r}")
        for bi, branch in enumerate(step.get("branches") or []):
            branch_next = branch.get("next")
            if branch_next and branch_next not in step_ids:
                raise AssertionError(
                    f"{sw} branches[{bi}]: next references unknown step id "
                    f"{branch_next!r}"
                )
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
