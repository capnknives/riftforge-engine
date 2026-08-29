"""
dialogue_loader.py -- load and validate Conversation Tree JSON catalogs.

Games register one or more dialogue content directories via
``set_dialogue_dirs``. Validation fails loud on missing ids, unknown
``check.kind``, dangling ``goto`` node ids, and ``success``/``failure``
blocks without a ``check`` (those belong on a trivial ``flat`` check
per the design's option-shape notes).

Cache clears via ``_clear_caches_for_tests`` in smoke.
"""

from __future__ import annotations

import glob
import json
import os

ALLOWED_CHECK_KINDS = frozenset({"opposed", "skill_dc", "flat"})
ALLOWED_OPPOSED_MODES = frozenset({"intimidate", "persuade", "bluff"})

_DIALOGUE_DIRS: list[str] = []
_CACHE: dict = {}


def set_dialogue_dirs(dirs):
    """Register dialogue JSON directories (additive -- later dirs extend)."""
    global _DIALOGUE_DIRS, _CACHE
    for d in dirs or []:
        path = str(d)
        if path not in _DIALOGUE_DIRS:
            _DIALOGUE_DIRS.append(path)
    _CACHE = {}


def _reset_dialogue_dirs_for_tests(dirs):
    """Replace dialogue dirs (smoke tests only -- not for production boot)."""
    global _DIALOGUE_DIRS, _CACHE
    _DIALOGUE_DIRS = [str(d) for d in (dirs or [])]
    _CACHE = {}


def _clear_caches_for_tests():
    """Reset dialogue catalog cache (smoke / unit helpers)."""
    global _CACHE
    _CACHE = {}


def _validate_outcome(outcome, *, where, node_ids):
    """Validate a success/failure/bare-option outcome block."""
    if outcome is None:
        return
    if not isinstance(outcome, dict):
        raise AssertionError(f"{where}: outcome must be a dict")
    say = outcome.get("say")
    if say is not None:
        if not isinstance(say, list) or any(
            not isinstance(line, str) for line in say
        ):
            raise AssertionError(f"{where}: say must be a list of strings")
    grant = outcome.get("grant")
    if grant is not None and not isinstance(grant, dict):
        raise AssertionError(f"{where}: grant must be a dict")
    nxt = outcome.get("goto")
    if nxt:
        if not isinstance(nxt, str) or not nxt.strip():
            raise AssertionError(f"{where}: goto must be a non-empty string")
        if nxt not in node_ids:
            raise AssertionError(
                f"{where}: dangling goto {nxt!r} (not in nodes)"
            )


def _validate_check(check, *, where):
    """Fail loud on an unknown or incomplete check block."""
    if not isinstance(check, dict):
        raise AssertionError(f"{where}: check must be a dict")
    kind = check.get("kind")
    if kind not in ALLOWED_CHECK_KINDS:
        raise AssertionError(
            f"{where}: check.kind must be one of "
            f"{sorted(ALLOWED_CHECK_KINDS)}, got {kind!r}"
        )
    if kind == "opposed":
        mode = (check.get("mode") or "").strip().lower()
        if mode not in ALLOWED_OPPOSED_MODES:
            raise AssertionError(
                f"{where}: opposed check.mode must be one of "
                f"{sorted(ALLOWED_OPPOSED_MODES)}, got {mode!r}"
            )
    elif kind == "skill_dc":
        skill = check.get("skill")
        if not skill or not isinstance(skill, str):
            raise AssertionError(f"{where}: skill_dc check needs skill")
        if "dc" not in check:
            raise AssertionError(f"{where}: skill_dc check needs dc")
    elif kind == "flat":
        if "chance" not in check:
            raise AssertionError(f"{where}: flat check needs chance")


def _validate_requires(req, *, where):
    """Requires is an optional predicate object (shorthand or typed)."""
    if req is None:
        return
    if not isinstance(req, dict):
        raise AssertionError(f"{where}: requires must be a dict")


def _validate_option(option, *, where, node_ids):
    """Validate one option dict (both check and check-free shapes)."""
    if not isinstance(option, dict):
        raise AssertionError(f"{where}: must be a dict")
    oid = option.get("id")
    if not oid or not isinstance(oid, str):
        raise AssertionError(f"{where}: id required")
    label = option.get("label")
    if not label or not isinstance(label, str):
        raise AssertionError(f"{where}: label required")
    _validate_requires(option.get("requires"), where=f"{where}:requires")
    check = option.get("check")
    if check is not None:
        _validate_check(check, where=f"{where}:check")
        # success/failure are only meaningful with a check.
        for key in ("success", "failure"):
            if key in option:
                _validate_outcome(
                    option.get(key),
                    where=f"{where}:{key}",
                    node_ids=node_ids,
                )
    else:
        if option.get("success") is not None or option.get("failure") is not None:
            raise AssertionError(
                f"{where}: success/failure require a check; use "
                "check.kind 'flat' with chance 1.0 for a guaranteed "
                "success block (see quest_system_design.md §11.3)"
            )
        _validate_outcome(option, where=where, node_ids=node_ids)


def _validate_node(node, *, where, node_ids):
    """Validate one node dict."""
    if not isinstance(node, dict):
        raise AssertionError(f"{where}: must be a dict")
    say = node.get("say")
    if not isinstance(say, list) or not say:
        raise AssertionError(f"{where}: say must be a non-empty list of strings")
    if any(not isinstance(line, str) or not str(line).strip() for line in say):
        raise AssertionError(f"{where}: say entries must be non-empty strings")
    if len(say) > 2:
        raise AssertionError(
            f"{where}: say is 1-2 lines (pacing); got {len(say)}"
        )
    options = node.get("options") or []
    if not isinstance(options, list):
        raise AssertionError(f"{where}: options must be a list")
    seen = set()
    for i, option in enumerate(options):
        ow = f"{where} options[{i}]"
        _validate_option(option, where=ow, node_ids=node_ids)
        oid = option.get("id")
        if oid in seen:
            raise AssertionError(f"{ow}: duplicate option id {oid!r}")
        seen.add(oid)


def validate_tree(data, *, where="dialogue"):
    """Fail loud if a Conversation Tree dict is missing required fields."""
    if not isinstance(data, dict):
        raise AssertionError(f"{where}: tree must be a dict")
    tid = data.get("id")
    if not tid or not isinstance(tid, str):
        raise AssertionError(f"{where}: id must be a non-empty string")
    start = data.get("start_node")
    if not start or not isinstance(start, str):
        raise AssertionError(f"{where}: start_node required")
    nodes = data.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        raise AssertionError(f"{where}: nodes must be a non-empty object")
    node_ids = set(nodes.keys())
    if start not in node_ids:
        raise AssertionError(
            f"{where}: start_node {start!r} is not in nodes"
        )
    for nid, node in nodes.items():
        if not nid or not isinstance(nid, str):
            raise AssertionError(f"{where}: node id must be a non-empty string")
        _validate_node(node, where=f"{where} nodes[{nid}]", node_ids=node_ids)
    return data


def load_trees():
    """Load every ``*.json`` tree file from registered dirs into a dict by id."""
    global _CACHE
    if _CACHE:
        return _CACHE
    by_id = {}
    for dialogue_dir in _DIALOGUE_DIRS:
        if not os.path.isdir(dialogue_dir):
            continue
        pattern = os.path.join(dialogue_dir, "*.json")
        for path in sorted(glob.glob(pattern)):
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and data.get("_guide") and not data.get("id"):
                continue
            validate_tree(data, where=path)
            tid = data["id"]
            if tid in by_id:
                raise AssertionError(f"duplicate dialogue tree id {tid!r} in {path}")
            by_id[tid] = data
    _CACHE = by_id
    return _CACHE


def get_tree(tree_id):
    """Return one tree dict or None."""
    if not tree_id:
        return None
    return load_trees().get(tree_id)


def list_tree_ids():
    """Sorted catalog ids."""
    return sorted(load_trees().keys())
