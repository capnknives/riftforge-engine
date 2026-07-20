"""
export_public_engine.py — build a clean tree for capnknives/riftforge-engine.

Copies only public-safe paths into an output directory (default:
``_public_engine_export/``). Does NOT include supers/, full game content,
game smoke, live ops, or AI/tooling instruction files (AGENTS.md, CLAUDE.md,
.cursor/, …). Run from the monorepo root::

    py -3.13 tools/export_public_engine.py
    # then sync the dest into the riftforge-engine checkout / remote

Stdlib only. Never imported by server.py.
"""

from __future__ import annotations

import os
import shutil
import sys

# Paths relative to monorepo root that ship in the public engine remote.
# Intentionally omits AGENTS.md / CLAUDE.md / .cursor / copilot instructions —
# those stay private to the game monorepo. Also omits the monorepo README.md
# (game-facing); export writes PUBLIC_README instead.
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
    "LICENSE",
)

# Standalone public README — do not prepend/append the private monorepo README
# (that file documents Cursor Automations, AGENTS.md, etc.).
PUBLIC_README = """\
# Riftforge

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/capnknives/riftforge-engine)

Public MUD **engine** — pure Python, `asyncio`, standard library only.
No frameworks, no third-party runtime deps.

Game content (origins, combat flavor, town simulation, and so on) lives in a
separate private repo and depends on **tagged releases** of this package.

## Install

```bash
pip install -e .
# or pin a release from another project:
#   riftforge @ git+https://github.com/capnknives/riftforge-engine.git@v0.1.1
```

## Smoke

```bash
python tools/engine_smoke.py
```

That check is meant to pass with no game package present.

## Layout

| Path | Role |
|------|------|
| `engine/` | Generic MUD core (sessions, verbs, hooks, persistence helpers, …) |
| `world.py` / `persistence.py` / `command_support.py` | Thin root facades over the engine cores |
| `server.py` / `commands.py` / `maps.py` | Shared boot + dispatch + map loader |
| `content/maps/demo.json` | Minimal demo map for a bare install |
| `docs/ENGINE_CONSUMER.md` | How a game registers hooks on the engine |
| `docs/RELEASING_RIFTFORGE.md` / `docs/UPGRADING_RIFTFORGE.md` | Cut / consume a release |

## Run a bare demo

```bash
python server.py          # needs Python 3.11+
telnet localhost 4000
```

Without a game package registered, you get a lean engine demo — enough to
prove sessions, rooms, and the tick loop. A full game supplies chargen, help
topics, combat, and content via `engine.hooks` at boot.

## Docs

- **Consumer guide:** [`docs/ENGINE_CONSUMER.md`](docs/ENGINE_CONSUMER.md)
- **Two-repo split / purity roadmap:** [`docs/plans/two_repo_purity.md`](docs/plans/two_repo_purity.md)
- **Release / upgrade:** [`docs/RELEASING_RIFTFORGE.md`](docs/RELEASING_RIFTFORGE.md), [`docs/UPGRADING_RIFTFORGE.md`](docs/UPGRADING_RIFTFORGE.md)

## Contributing

Engine changes should keep the package **game-agnostic**: the engine never
imports a game package at module level. Prefer hooks (`engine.hooks`) over
hard-wired game calls. See the consumer guide for the registration surface.

## License

MIT — see [`LICENSE`](LICENSE).
"""

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
    """Copy public paths into dest, add demo map + standalone public README."""
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
            shutil.copytree(
                src,
                out,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", ".pytest_cache"
                ),
            )
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

    # Always overwrite — never ship the private monorepo README body.
    with open(os.path.join(dest, "README.md"), "w", encoding="utf-8") as f:
        f.write(PUBLIC_README)

    # Public tree must not ship a supers package or AI instruction files.
    assert not os.path.exists(os.path.join(dest, "supers"))
    assert not os.path.exists(os.path.join(dest, "AGENTS.md"))
    assert not os.path.exists(os.path.join(dest, "CLAUDE.md"))
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
