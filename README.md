# Riftforge

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/capnknives/riftforge-engine)

Public MUD **engine** — pure Python, `asyncio`, standard library only.
No frameworks, no third-party runtime deps.

Build your own text game on reusable frameworks (planes, gates, combat
briefs, body parts, content kinds, travel, economy, …). Game-specific lore,
catalogs, and prose live in a **separate** consumer repo and pin **tagged
releases** of this package.

**Current release: [`v0.4.0`](https://github.com/capnknives/riftforge-engine/releases/tag/v0.4.0)** — core expansion frameworks (planes/gates, dual-root content kinds, needs registry, spawn/bestiary + nest AI, instance-room teardown, anatomy/body parts, and more). A larger follow-on slice is planned; pin `@v0.4.0` until the next tag ships.

## Install

```bash
pip install -e .
# or pin from another project:
#   riftforge @ git+https://github.com/capnknives/riftforge-engine.git@v0.4.0
```

Requires **Python 3.11+**.

## Quick start

**Lean engine demo** (sessions, rooms, tick loop — no game package):

```bash
python server.py
telnet localhost 4000
```

**Reference game** (`basegame` — Notbigville, Kansas demo town):

```bash
RIFTFORGE_GAME=basegame python server.py
telnet localhost 4000
```

## What's in v0.4.0

### Core runtime (`engine/`)

| Area | What you get |
|------|----------------|
| **Sessions & I/O** | Async telnet server, login/reconnect, optional gateway child (clients survive game-only restart), copyover hot-reload |
| **World model** | Rooms, characters, groups, movement helpers, map loader, runtime room flags, map heal/backups |
| **Hooks** | Registration surface so games wire chargen, persist, help, dispatch, and domain behavior without engine imports of game code |
| **Persistence** | Character/world save helpers; lean `Character` surface games extend via attach hooks |
| **Tick loop** | Ordered tick registry — games register heartbeat callbacks at boot |
| **Content** | JSON content store + validation; dual-root **kind profiles** (`engine/content_kinds/`) with `extends` merge, templates, and lint |
| **OLC** | In-game menu wizard (`engine/olc.py`) for authoring entities through kind profiles |
| **Stats** | Shared six-primary spine (`POW` / `VIT` / `FOC` / `FIN` / `RES` / `PRE`) + Tier helpers |
| **Verbs** | Lean engine command stubs; games override via merged `COMMANDS` tables |
| **Ops (optional)** | Auto-deploy overlay, bug/suggestion reports, Discord/OOC bridges — all hookable, none required |

Purity gate: **`engine/` never imports a game package.** Enforced by `tools/engine_smoke.py`.

### Frameworks (`engine/systems/`)

Reusable opt-in systems another MUD can adopt through hooks and registration:

| Domain | Modules | Notes |
|--------|---------|-------|
| **Planes & gates** | `planes/`, `gates/` | Elemental plane registry, rotating gate networks, visibility rules |
| **Travel & weather** | `weather`, `regional_weather`, `overland`, `storm_chase`, `globe`, `aerial`, `globe_flight`, `charter_flight` | CONUS atlas, regional forecasts, storm chase board, globe + flight tiers |
| **Needs** | `needs` | Meter math primitives + registry — games register meter ids and tick handlers |
| **Economy** | `economy`, `civic_shop` | Coin ledger, vendor buy/sell primitives, civic shop fixtures |
| **Combat** | `combat_core`, `combat_engine`, `combat_mundane`, `combat_martial_arts` | Structured battle brief (data) separate from prose; round/swing resolve; mundane + martial kits |
| **Anatomy** | `anatomy`, `body_parts` | Region HP tiers, `plan_region_damage` / `apply_region_damage`; games register max-HP resolver |
| **Environment** | `room_structure`, `breach` | Wall/floor state (`get_wall_state`, wreck/repair); slam/breach hooks |
| **Spawn** | `spawn/` | Bestiary tables + nest-AI dispatch |
| **Instances** | `instance_rooms` | Tear down pocket/instance rooms when empty |
| **Social & gear** | `mail`, `social_catalog`, `wearables`, `containers`, `floor_loot` | Letters, emote catalog, clothing slots, containers |
| **Civic** | `clinic`, `justice`, `player_site` | Injury intake, crime case shell, player-owned site hooks |
| **Quests** | `quests`, `quests_loader` | Quest state machine + JSON loader |
| **Studio bridge** | `studio_bridge` | Hot-reload hook for Area Studio content edits (monorepo tool; bridge API ships in engine) |
| **Other** | `pathfind` (BFS), `languages`, `umbral`, `utility_delay`, `press_beat`, `origin_registry` | Pathfinding, language tags, umbral shroud shell, timed actions, press-beat pacing |

Combat design invariant: **math resolves to a Structured Battle Brief (data); prose is a separate render step.** Games supply narrators; the engine supplies brief builders and apply paths.

### Reference game (`basegame/`)

A complete **proof consumer** with zero SUPERS dependency — copy patterns from here:

- **Notbigville, Kansas** — demo town + wilds map (`content/zones/notbigville.json`)
- **Chargen** — four mundane jobs, six-stat point-buy, `score` / character sheet
- **Gates** — four rotating elemental gates (smoke-tested rotation)
- **Needs** — registered hunger/fatigue meters with seek/critical behavior
- **Combat** — swing/round narrative combat via `combat_engine`
- **Weather & travel** — regional weather, America overland atlas, Storm Watch chase desk
- **Mail, shops, justice, slam** — thin verb wrappers over engine frameworks
- **Spawn nests** — bestiary + nest-AI demo

Wiring example: `basegame/bootstrap.py` registers hooks; `basegame/tick_bootstrap.py` orders heartbeat callbacks.

## Layout

| Path | Role |
|------|----------------|
| `engine/` | Generic MUD core + `engine/systems/` frameworks |
| `world.py` / `persistence.py` / `command_support.py` | Thin root facades over engine cores |
| `server.py` / `commands.py` / `maps.py` / `game_select.py` | Boot, dispatch, map loader, `RIFTFORGE_GAME` chooser |
| `basegame/` | Reference game (Notbigville demo) |
| `content/maps/demo.json` | Minimal map for lean engine boot |
| `docs/ENGINE_CONSUMER.md` | How a game registers on the engine |
| `docs/RELEASING_RIFTFORGE.md` / `docs/UPGRADING_RIFTFORGE.md` | Cut / consume a release |

## Smoke tests

```bash
python tools/engine_smoke.py      # lean engine — no game package on disk
python tools/basegame_smoke.py    # basegame reference game
```

CI runs both on every push.

## Building your own game

1. `pip install -e .` (or pin `@v0.4.0`).
2. Read [`docs/ENGINE_CONSUMER.md`](docs/ENGINE_CONSUMER.md) — hooks for chargen, persist, help, `register_all_hooks()`.
3. Copy `basegame/` as a skeleton, or register your package via `RIFTFORGE_GAME=yourgame`.
4. Put catalogs in your repo (`content/kinds/`, maps, NPCs); register kind dirs with `set_content_kinds_dirs`.
5. Never add game imports inside `engine/` — wire behavior through hooks.

## Docs

- **Consumer guide:** [`docs/ENGINE_CONSUMER.md`](docs/ENGINE_CONSUMER.md)
- **Two-repo split / purity roadmap:** [`docs/plans/two_repo_purity.md`](docs/plans/two_repo_purity.md)
- **Release / upgrade:** [`docs/RELEASING_RIFTFORGE.md`](docs/RELEASING_RIFTFORGE.md), [`docs/UPGRADING_RIFTFORGE.md`](docs/UPGRADING_RIFTFORGE.md)

## Contributing

Keep the package **game-agnostic**: the engine never imports a game package at
module level. Prefer hooks (`engine.hooks`) over hard-wired game calls. New
framework APIs should ship with a `basegame/` proof or smoke coverage when
possible.

## License

MIT — see [`LICENSE`](LICENSE).
