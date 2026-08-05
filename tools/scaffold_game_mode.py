#!/usr/bin/env python3
"""scaffold_game_mode.py -- generate kind profiles + content dirs for a new game.

Every new engine consumer should start from schema-first layout so OLC,
content_new, and validate_kind stay aligned. This tool stamps the minimum
tree; copy classic/ as the reference implementation.

Usage:
    py -3.13 tools/scaffold_game_mode.py --slug mygame --label "My Game"
    py -3.13 tools/scaffold_game_mode.py --slug mygame --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _kind_profile(kind_id, label, writes_to, extends=None, fields=None):
    doc = {
        "id": kind_id,
        "label": label,
        "writes_to": writes_to,
        "fields": fields or {},
    }
    if extends:
        doc["extends"] = extends
    return doc


def _scaffold(slug, label, *, dry_run=False):
    pkg = os.path.join(_ROOT, slug)
    content = os.path.join(pkg, "content")
    kinds = os.path.join(content, "kinds")
    dirs = [
        pkg,
        content,
        kinds,
        os.path.join(content, "maps"),
        os.path.join(content, "zones"),
        os.path.join(content, "catalog"),
        os.path.join(content, "bestiary"),
        os.path.join(pkg, "rules"),
        os.path.join(pkg, "verbs"),
    ]
    for path in dirs:
        if dry_run:
            print(f"mkdir {path}")
        else:
            os.makedirs(path, exist_ok=True)

    kind_files = {
        f"room.{slug}.indoor.json": _kind_profile(
            f"room.{slug}.indoor",
            f"{label} indoor room",
            f"{slug}/content/zones rooms[]",
            extends="room.engine",
            fields={
                "outdoor": {
                    "type": "bool",
                    "required": True,
                    "default": False,
                    "doc": "Indoor room.",
                },
                "zone": {
                    "type": "string",
                    "required": True,
                    "doc": "Zone id.",
                },
            },
        ),
        f"room.{slug}.outdoor.json": _kind_profile(
            f"room.{slug}.outdoor",
            f"{label} outdoor room",
            f"{slug}/content/maps rooms[]",
            extends="room.engine",
            fields={
                "outdoor": {
                    "type": "bool",
                    "required": True,
                    "default": True,
                    "doc": "Outdoor room.",
                },
            },
        ),
        f"zone.{slug}.pocket.json": _kind_profile(
            f"zone.{slug}.pocket",
            f"{label} pocket zone file",
            f"{slug}/content/zones/*.json",
            extends="map.earth",
            fields={
                "city_name": {"type": "string", "required": True, "doc": "City name."},
                "rooms": {"type": "list", "required": True, "doc": "Room rows."},
            },
        ),
        f"map.{slug}.wilderness.json": _kind_profile(
            f"map.{slug}.wilderness",
            f"{label} wilderness map file",
            f"{slug}/content/maps/*.json",
            extends="map.earth",
            fields={
                "wilderness": {
                    "type": "bool",
                    "required": True,
                    "default": True,
                    "doc": "Wilderness stamp.",
                },
                "rooms": {"type": "list", "required": True, "doc": "Room rows."},
            },
        ),
        f"catalog.{slug}.classes_file.json": _kind_profile(
            f"catalog.{slug}.classes_file",
            f"{label} classes catalog file",
            f"{slug}/content/catalog/classes.json",
            fields={"classes": {"type": "list", "required": True, "doc": "Class rows."}},
        ),
        f"catalog.{slug}.spells_file.json": _kind_profile(
            f"catalog.{slug}.spells_file",
            f"{label} spells catalog file",
            f"{slug}/content/catalog/spells.json",
            fields={"spells": {"type": "list", "required": True, "doc": "Spell rows."}},
        ),
    }

    for name, payload in kind_files.items():
        path = os.path.join(kinds, name)
        text = json.dumps(payload, indent=2) + "\n"
        if dry_run:
            print(f"write {path}")
        else:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)

    readme = os.path.join(pkg, "SCHEMA_README.md")
    readme_text = f"""# {label} (`{slug}/`) schema checklist

1. Register `{slug}/content/kinds/` in `{slug}/bootstrap.py` via `set_content_kinds_dirs`.
2. Add `{slug}/content_validate.py` calling `validate_kind` on every JSON file at boot.
3. Wire `RIFTFORGE_GAME={slug}` in `game_select.py`.
4. Add `tools/{slug}_smoke.py` that calls `validate_all_content()`.
5. Sync templates: `py -3.13 tools/schema_sync_templates.py --write` (after adding game kinds to CI boot list).
6. Author through `content_new explain <kind>` / `content_new lint --kind <kind> file.json`.

Reference consumer: `classic/` + `docs/GAME_MODE_SCHEMA_CHECKLIST.md`.
"""
    if dry_run:
        print(f"write {readme}")
    else:
        with open(readme, "w", encoding="utf-8") as handle:
            handle.write(readme_text)

    print(f"Scaffolded schema tree for {slug!r} under {pkg}")


def main():
    parser = argparse.ArgumentParser(description="Scaffold a schema-first game mode package.")
    parser.add_argument("--slug", required=True, help="Python package name (e.g. classic)")
    parser.add_argument("--label", default=None, help="Human label (default: Title Case slug)")
    parser.add_argument("--dry-run", action="store_true", help="Print actions only")
    args = parser.parse_args()
    label = args.label or args.slug.replace("_", " ").title()
    if args.slug in ("supers", "engine"):
        print(f"Refusing to scaffold over reserved name {args.slug!r}", file=sys.stderr)
        return 2
    _scaffold(args.slug, label, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
