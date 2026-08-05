"""content_validate.py -- validate classic JSON through kind profiles."""

from __future__ import annotations

import glob
import json
import os

from engine.content_kinds import validate_kind

_CONTENT_DIR = os.path.join(os.path.dirname(__file__), "content")
_MAPS_DIR = os.path.join(_CONTENT_DIR, "maps")
_ZONES_DIR = os.path.join(_CONTENT_DIR, "zones")
_CATALOG_DIR = os.path.join(_CONTENT_DIR, "catalog")
_BESTIARY_DIR = os.path.join(_CONTENT_DIR, "bestiary")


def _read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _room_kind(room):
    """Pick indoor/outdoor room kind for one room row."""
    if room.get("outdoor"):
        return "room.classic.outdoor"
    return "room.classic.indoor"


def validate_classes_file(path=None):
    """Validate ``catalog/classes.json`` file + each class row."""
    path = path or os.path.join(_CATALOG_DIR, "classes.json")
    doc = _read_json(path)
    validate_kind(
        "catalog.classic.classes_file",
        doc,
        reject_unknown=False,
        where=path,
    )
    for index, row in enumerate(doc.get("classes") or []):
        validate_kind(
            "catalog.classic.class",
            row,
            reject_unknown=True,
            where=f"{path} classes[{index}]",
        )
    return doc


def validate_spells_file(path=None):
    """Validate ``catalog/spells.json`` file + each spell row."""
    path = path or os.path.join(_CATALOG_DIR, "spells.json")
    doc = _read_json(path)
    validate_kind(
        "catalog.classic.spells_file",
        doc,
        reject_unknown=False,
        where=path,
    )
    for index, row in enumerate(doc.get("spells") or []):
        validate_kind(
            "catalog.classic.spell",
            row,
            reject_unknown=True,
            where=f"{path} spells[{index}]",
        )
    return doc


def validate_bestiary_file(path):
    """Validate one bestiary catalog file + creature rows."""
    doc = _read_json(path)
    validate_kind(
        "creature.classic.catalog",
        doc,
        reject_unknown=False,
        where=path,
    )
    for index, row in enumerate(doc.get("creatures") or []):
        validate_kind(
            "creature.classic.hostile",
            row,
            reject_unknown=True,
            where=f"{path} creatures[{index}]",
        )
    return doc


def validate_bestiary_dir(dir_path=None):
    """Validate every ``bestiary/*.json`` file."""
    dir_path = dir_path or _BESTIARY_DIR
    paths = sorted(glob.glob(os.path.join(dir_path, "*.json")))
    return [validate_bestiary_file(path) for path in paths]


def validate_zone_file(path):
    """Validate a zone pocket file and each room row."""
    doc = _read_json(path)
    validate_kind(
        "zone.classic.pocket",
        doc,
        reject_unknown=False,
        where=path,
    )
    for index, room in enumerate(doc.get("rooms") or []):
        validate_kind(
            _room_kind(room),
            room,
            reject_unknown=True,
            where=f"{path} rooms[{index}]",
        )
    return doc


def validate_map_file(path):
    """Validate a wilderness map file and each room row."""
    doc = _read_json(path)
    validate_kind(
        "map.classic.wilderness",
        doc,
        reject_unknown=False,
        where=path,
    )
    for index, room in enumerate(doc.get("rooms") or []):
        validate_kind(
            "room.classic.outdoor",
            room,
            reject_unknown=True,
            where=f"{path} rooms[{index}]",
        )
    return doc


def validate_all_content():
    """Boot gate: validate every classic content JSON file."""
    validate_classes_file()
    validate_spells_file()
    validate_bestiary_dir()
    for path in sorted(glob.glob(os.path.join(_ZONES_DIR, "*.json"))):
        validate_zone_file(path)
    for path in sorted(glob.glob(os.path.join(_MAPS_DIR, "*.json"))):
        validate_map_file(path)
