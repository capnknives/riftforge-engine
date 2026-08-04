"""
bestiary -- generic creature-catalog loader.

Reads a directory of ``{"category": str, "creatures": [template, ...]}``
JSON files into one ``(category, tier) -> [template, ...]`` registry, with
the same fail-loud-at-load validation every catalog loader in this engine
follows: a broken file surfaces at boot, not the first time a tick tries
to spawn something.

A game supplies its own tier ceiling (``max_tier``) and any optional
enum-shaped fields it wants validated (``field_vocab``) -- this module has
no opinion on what a creature's body type, blood economy tag, or anything
else means. ``stat_names`` defaults to ``engine.stats.STAT_NAMES`` (the
six-primary spine every Riftforge game shares).
"""

from __future__ import annotations

import glob
import json
import os
import random

from engine.stats import STAT_NAMES


def load_catalog_files(dir_path):
    """Read and parse every ``*.json`` under ``dir_path``, sorted for
    deterministic load order. Returns ``[(filename, data), ...]``.
    """
    paths = sorted(glob.glob(os.path.join(dir_path, "*.json")))
    files = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as handle:
            files.append((os.path.basename(path), json.load(handle)))
    return files


def build_registry(
    files,
    *,
    max_tier,
    stat_names=STAT_NAMES,
    field_vocab=None,
    required_fields=("name", "description"),
    optional_string_fields=(),
):
    """Merge parsed catalog files into one
    ``{(category, tier): [template, ...]}``.

    Each file is ``{"category": str, "creatures": [template, ...]}``.
    Validates, per template:

    - a same-file duplicate ``id`` fails loud (a copy-paste bug, not a
      cross-file id reuse -- that's fine)
    - every name in ``required_fields`` is present as a non-empty string
    - ``tier`` is in ``0..max_tier``
    - ``stat_ranges`` covers every name in ``stat_names`` with a
      ``(low, high)`` pair where ``low <= high``
    - ``field_vocab`` is ``{field_name: (allowed_values, default)}`` for
      optional enum-shaped fields (SUPERS: body_type, blood_type, ...) --
      validated against ``allowed_values`` when present on the template,
      otherwise defaulted to ``default`` (``None`` means "leave unset,
      no default applied")
    - ``optional_string_fields`` names fields that, when present, must be
      non-empty strings (no fixed vocabulary -- SUPERS: boss_voice)

    Stamps ``template["category"] = category``. Raises ``ValueError`` on
    any violation, message-prefixed with the source filename.
    """
    field_vocab = field_vocab or {}
    registry = {}
    for filename, data in files:
        category = data["category"]
        seen_ids = set()
        for template in data["creatures"]:
            template_id = template["id"]
            if template_id in seen_ids:
                raise ValueError(
                    f"{filename}: duplicate creature id {template_id!r}"
                )
            seen_ids.add(template_id)

            for required in required_fields:
                value = template.get(required)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"{filename}: creature {template_id!r} needs a "
                        f"non-empty {required!r}"
                    )

            tier = template["tier"]
            if not (0 <= tier <= max_tier):
                raise ValueError(
                    f"{filename}: creature {template_id!r} has tier "
                    f"{tier!r}, must be 0-{max_tier}"
                )
            for stat_name, bounds in template["stat_ranges"].items():
                if stat_name not in stat_names:
                    raise ValueError(
                        f"{filename}: creature {template_id!r} has an "
                        f"unknown stat {stat_name!r} in stat_ranges"
                    )
                low, high = bounds
                if low > high:
                    raise ValueError(
                        f"{filename}: creature {template_id!r}'s "
                        f"{stat_name} range {bounds!r} has min > max"
                    )
            missing = set(stat_names) - set(template["stat_ranges"])
            if missing:
                raise ValueError(
                    f"{filename}: creature {template_id!r} is missing "
                    f"stat_ranges for {sorted(missing)}"
                )

            for field_name, (allowed, default) in field_vocab.items():
                value = template.get(field_name, default)
                if value is not None and value not in allowed:
                    omit = " (or omitted)" if default is None else ""
                    raise ValueError(
                        f"{filename}: creature {template_id!r} has an "
                        f"unknown {field_name} {value!r}, must be one of "
                        f"{allowed}{omit}"
                    )

            for field_name in optional_string_fields:
                value = template.get(field_name)
                if value is not None and (
                    not isinstance(value, str) or not value.strip()
                ):
                    raise ValueError(
                        f"{filename}: creature {template_id!r} has an "
                        f"invalid {field_name} {value!r} (non-empty str)"
                    )

            template["category"] = category
            registry.setdefault((category, tier), []).append(template)
    return registry


def get_pool(registry, categories, tier, *, resolve_category=None):
    """Every template matching any of ``categories`` at exactly ``tier``.

    ``resolve_category(category, tier)`` is an optional callback: return a
    list (even empty) to use it INSTEAD of the plain registry lookup for
    that category, or ``None`` to fall through to the normal lookup. Lets
    a game special-case category prefixes (SUPERS: a ``"demesne:<id>"``
    category delegates to a God's own SQLite-authored bestiary) without
    this module knowing anything about what makes a category special.
    """
    pool = []
    for category in categories:
        cat = str(category)
        if resolve_category is not None:
            resolved = resolve_category(cat, tier)
            if resolved is not None:
                pool.extend(resolved)
                continue
        pool.extend(registry.get((cat, tier), []))
    return pool


def find_creature(registry, identifier):
    """Case-insensitive id or name match across the WHOLE registry,
    ignoring category/tier scoping. Returns ``(template, tier)`` or ``None``.
    """
    needle = identifier.strip().lower()
    for (category, tier), templates in registry.items():
        for template in templates:
            if (
                template["id"].lower() == needle
                or template["name"].lower() == needle
            ):
                return template, tier
    return None


def roll_stats(template):
    """One set of primaries for ``template`` -- each stat independently
    picked within its ``stat_ranges`` band via ``random.uniform``, rounded
    to one decimal place.
    """
    return {
        stat_name: round(random.uniform(low, high), 1)
        for stat_name, (low, high) in template["stat_ranges"].items()
    }
