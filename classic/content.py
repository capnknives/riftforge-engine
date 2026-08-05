"""content.py -- load and cache schema-validated classic catalog JSON."""

from __future__ import annotations

import json
import os

_CONTENT_DIR = os.path.join(os.path.dirname(__file__), "content")
_CATALOG_DIR = os.path.join(_CONTENT_DIR, "catalog")
_BESTIARY_DIR = os.path.join(_CONTENT_DIR, "bestiary")

_CLASSES_CACHE = None
_SPELLS_CACHE = None
_BESTIARY_CACHE = None


def _read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_classes_catalog(*, reload=False):
    """Return validated class rows keyed by id."""
    global _CLASSES_CACHE
    if _CLASSES_CACHE is not None and not reload:
        return _CLASSES_CACHE
    from classic.content_validate import validate_classes_file

    path = os.path.join(_CATALOG_DIR, "classes.json")
    doc = validate_classes_file(path)
    rows = {}
    order = []
    for row in doc["classes"]:
        rows[row["id"]] = row
        order.append(row["id"])
    _CLASSES_CACHE = {"by_id": rows, "order": tuple(order)}
    return _CLASSES_CACHE


def load_spells_catalog(*, reload=False):
    """Return validated spell rows keyed by id."""
    global _SPELLS_CACHE
    if _SPELLS_CACHE is not None and not reload:
        return _SPELLS_CACHE
    from classic.content_validate import validate_spells_file

    path = os.path.join(_CATALOG_DIR, "spells.json")
    doc = validate_spells_file(path)
    rows = {}
    for row in doc["spells"]:
        normalized = dict(row)
        normalized["classes"] = frozenset(row["classes"])
        rows[row["id"]] = normalized
    _SPELLS_CACHE = rows
    return _SPELLS_CACHE


def load_bestiary_catalog(*, reload=False):
    """Return spawn registry from validated bestiary files."""
    global _BESTIARY_CACHE
    if _BESTIARY_CACHE is not None and not reload:
        return _BESTIARY_CACHE
    from classic.content_validate import validate_bestiary_dir
    from engine.systems import spawn as spawn_engine
    from engine.stats import STAT_NAMES

    files = validate_bestiary_dir(_BESTIARY_DIR)
    parsed = spawn_engine.load_catalog_files(_BESTIARY_DIR)
    registry = spawn_engine.build_registry(
        parsed,
        max_tier=0,
        stat_names=STAT_NAMES,
        field_vocab={},
    )
    _BESTIARY_CACHE = {"files": files, "registry": registry}
    return _BESTIARY_CACHE
