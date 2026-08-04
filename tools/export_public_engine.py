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
    "game_select.py",
    "basegame",
    "tools/engine_smoke.py",
    "tools/basegame_smoke.py",
    "tools/demo_weather_smoke.py",
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
#   riftforge @ git+https://github.com/capnknives/riftforge-engine.git@v0.4.0
```

## Smoke

```bash
python tools/engine_smoke.py      # lean engine, no game package present
python tools/basegame_smoke.py    # basegame, the reference game below
```

## Layout

| Path | Role |
|------|------|
| `engine/` | Generic MUD core (sessions, verbs, hooks, persistence helpers, stat spine, …) |
| `world.py` / `persistence.py` / `command_support.py` | Thin root facades over the engine cores |
| `server.py` / `commands.py` / `maps.py` / `game_select.py` | Shared boot + dispatch + map loader + game-package chooser |
| `basegame/` | A small, complete reference game built on the engine — ordinary humans, four jobs, its own town + wilds map, a `score` command |
| `content/maps/demo.json` | Minimal demo map for a bare install (no game package) |
| `docs/ENGINE_CONSUMER.md` | How a game registers hooks on the engine |
| `docs/RELEASING_RIFTFORGE.md` / `docs/UPGRADING_RIFTFORGE.md` | Cut / consume a release |

## Run a bare demo

```bash
python server.py          # needs Python 3.11+
telnet localhost 4000
```

Without a game package registered, you get a lean engine demo — enough to
prove sessions, rooms, and the tick loop.

## Run the reference game

```bash
RIFTFORGE_GAME=basegame python server.py
telnet localhost 4000
```

`basegame` is a small, complete example of "a game built on top of the
engine" — chargen (pick a job, a short point-buy across the shared six
primary stats), a demo town + wilds map, and a `score` command showing
your Path/stats/Tier/HP. It's a good starting point for building your own
game, or for editing `basegame/verbs/character.py`'s `score` command
directly to see how a character sheet is put together. A full game
supplies chargen, help topics, combat, and content via `engine.hooks` at
boot -- `basegame/bootstrap.py` is a small, readable example of exactly
that wiring.

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

# Public-repo CI (lives only on riftforge-engine; monorepo does not ship this).
ENGINE_CI_YAML = """\
name: CI

on:
  push:
  pull_request:

jobs:
  engine-smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install editable engine
        run: |
          python -m pip install -U pip
          python -m pip install -e .
      - name: Engine-only smoke
        run: python tools/engine_smoke.py
      - name: Basegame smoke
        run: python tools/basegame_smoke.py
"""


# Minimal demo map so maps.load_all_maps / engine_smoke can run without SUPERS.
# Canonical source: engine/demo/content/maps/demo.json (copied at export).


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
    demo_src = os.path.join(
        root, "engine", "demo", "content", "maps", "demo.json"
    )
    if os.path.isfile(demo_src):
        shutil.copy2(demo_src, os.path.join(maps_dir, "demo.json"))
    else:
        print(f"warn: missing lean demo map {demo_src}", file=sys.stderr)

    with open(os.path.join(dest, "help_topics.py"), "w", encoding="utf-8") as f:
        f.write(HELP_TOPICS_STUB)

    # Public-repo CI workflow (so a wipe+sync export does not drop Actions).
    gh_dir = os.path.join(dest, ".github", "workflows")
    os.makedirs(gh_dir, exist_ok=True)
    with open(os.path.join(gh_dir, "ci.yml"), "w", encoding="utf-8") as f:
        f.write(ENGINE_CI_YAML)

    # Empty content/npcs so nothing accidental is assumed present.
    os.makedirs(os.path.join(dest, "content", "npcs"), exist_ok=True)

    # Always overwrite — never ship the private monorepo README body.
    with open(os.path.join(dest, "README.md"), "w", encoding="utf-8") as f:
        f.write(PUBLIC_README)

    with open(os.path.join(dest, ".gitignore"), "w", encoding="utf-8") as f:
        f.write(
            "__pycache__/\n*.pyc\n.pytest_cache/\n*.db\n"
            ".game_heartbeat\n_public_engine_export/\n"
        )

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
