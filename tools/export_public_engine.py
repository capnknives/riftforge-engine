"""
export_public_engine.py — build a clean tree for capnknives/riftforge-engine.

Copies only public-safe paths into an output directory (default:
``_public_engine_export/``). Does NOT include supers/, content/, game smoke,
or live ops. Run from the monorepo root::

    py -3.13 tools/export_public_engine.py
    # then: cd _public_engine_export && git init && ...

Stdlib only. Never imported by server.py.
"""

from __future__ import annotations

import os
import shutil
import sys

# Paths relative to monorepo root that ship in the public engine remote.
PUBLIC_PATHS = (
    "engine",
    "pyproject.toml",
    "world.py",
    "persistence.py",
    "command_support.py",
    "commands.py",
    "server.py",
    "maps.py",
    "tools/engine_smoke.py",
    "tools/packaging_smoke.py",
    "tools/export_public_engine.py",
    "docs/ENGINE_CONSUMER.md",
    "docs/RELEASING_RIFTFORGE.md",
    "docs/UPGRADING_RIFTFORGE.md",
    "docs/plans/two_repo_purity.md",
    "docs/plans/connection_gateway.md",
    "AGENTS.md",
    "LICENSE",
    "README.md",
)

# Minimal help maps so commands.py imports without shipping SUPERS lore.
HELP_TOPICS_STUB = '''\
"""help_topics.py — lean stub for the public engine tree.

SUPERS topic pages live only in the private game repo. A bare engine
install gets an empty topic index; verbs still have COMMANDS one-liners.
"""

HELP_TOPICS = {}
HELP_CATEGORIES = []
'''


# Minimal demo map so maps.load_all_maps / engine_smoke can run without SUPERS.
DEMO_MAP = """{
  "id": "demo",
  "name": "Demo Realm",
  "rooms": [
    {
      "key": "Demo Start",
      "description": "A bare engine demo room.",
      "exits": {},
      "is_start": true
    }
  ]
}
"""


def _repo_root() -> str:
    """Monorepo root (parent of tools/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def export(dest: str) -> None:
    """Copy public paths into dest, add demo map + public README overlay."""
    root = _repo_root()
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)

    for rel in PUBLIC_PATHS:
        src = os.path.join(root, rel)
        if not os.path.exists(src):
            print(f"skip missing: {rel}", file=sys.stderr)
            continue
        out = os.path.join(dest, rel)
        if os.path.isdir(src):
            shutil.copytree(src, out)
        else:
            os.makedirs(os.path.dirname(out) or dest, exist_ok=True)
            shutil.copy2(src, out)

    maps_dir = os.path.join(dest, "content", "maps")
    os.makedirs(maps_dir, exist_ok=True)
    with open(os.path.join(maps_dir, "demo.json"), "w", encoding="utf-8") as f:
        f.write(DEMO_MAP)

    with open(os.path.join(dest, "help_topics.py"), "w", encoding="utf-8") as f:
        f.write(HELP_TOPICS_STUB)

    # Empty content/npcs so nothing accidental is assumed present.
    os.makedirs(os.path.join(dest, "content", "npcs"), exist_ok=True)

    readme = os.path.join(dest, "README.md")
    banner = (
        "# Riftforge engine\n\n"
        "Public MUD **engine** (pure Python, asyncio, stdlib only).\n"
        "Game content (SUPERS) lives in a separate private repo and depends "
        "on tagged releases of this package.\n\n"
        "Install: `pip install -e .`\n"
        "Smoke: rename any local `supers/` aside, then "
        "`python tools/engine_smoke.py`.\n\n"
        "Consumer guide: `docs/ENGINE_CONSUMER.md`.\n"
        "Roadmap: `docs/plans/two_repo_purity.md`.\n\n"
        "---\n\n"
    )
    if os.path.isfile(readme):
        with open(readme, "r", encoding="utf-8") as f:
            body = f.read()
        with open(readme, "w", encoding="utf-8") as f:
            f.write(banner + body)
    else:
        with open(readme, "w", encoding="utf-8") as f:
            f.write(banner)

    # Public tree must not ship a supers package.
    assert not os.path.exists(os.path.join(dest, "supers"))
    print(f"exported_public_engine -> {dest}")


def main() -> int:
    """CLI: optional dest path as argv[1]."""
    root = _repo_root()
    dest = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        root, "_public_engine_export"
    )
    export(dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
