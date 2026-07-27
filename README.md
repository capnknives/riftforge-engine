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
#   riftforge @ git+https://github.com/capnknives/riftforge-engine.git@v0.2.0
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
