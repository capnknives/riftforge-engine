# Riftforge

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/capnknives/riftforge-engine)

Public MUD **engine** — pure Python, `asyncio`, standard library only.
No frameworks, no third-party runtime deps.

Build your own text game on reusable frameworks (planes, gates, combat
briefs, body parts, content kinds, travel, economy, …). Game-specific lore,
catalogs, and prose live in a **separate** consumer repo and pin **tagged
releases** of this package.

**Current release: [`v0.5.0`](https://github.com/capnknives/riftforge-engine/releases/tag/v0.5.0)** — map-authoring OLC helpers, plus generic phone, appearance-builder, persona-trait, and relationship-tag frameworks peeled out of a private game's policy layer; dual-root content kind profiles gained abstract item/NPC/creature/map grandparents. Builds on `v0.4.0`'s core expansion frameworks. **This export** adds the **`classic/`** OSR demo (Millbrook + wilds), generic **`combat_osr`** d20 swing engine, and a combat-systems map in `docs/ENGINE_CONSUMER.md`. Pin `@v0.5.0` until the next semver tag ships from `main`.

## Install

```bash
pip install -e .
# or pin from another project:
#   riftforge @ git+https://github.com/capnknives/riftforge-engine.git@v0.5.0
```

Requires **Python 3.11+**.

## Quick start

**Engine MVP demo** (Notbigville, jobs, weather, atlas — ships with ``basegame/``):

```bash
python -m engine
telnet localhost 4000
```

Side-by-side with another instance on :4000:

```bash
RIFTFORGE_PORT=5000 RIFTFORGE_DB=riftforge_engine.db RIFTFORGE_GATEWAY=0 python -m engine
telnet localhost 5000
```

**Lean one-room boot** (no game package — CI / hook-default proof):

```bash
RIFTFORGE_GAME=none python server.py
telnet localhost 4000
```

**Explicit reference game** (same as default when ``basegame/`` is present):

```bash
RIFTFORGE_GAME=basegame python server.py
telnet localhost 4000
```

**Classic OSR fantasy** (Millbrook village + wilds — second shipped demo):

```bash
RIFTFORGE_GAME=classic python server.py
telnet localhost 4000
```

Scaffold a new game mode (kind profiles + content dirs):

```bash
python tools/scaffold_game_mode.py --slug mygame --label "My Game"
```

See [`docs/GAME_MODE_SCHEMA_CHECKLIST.md`](docs/GAME_MODE_SCHEMA_CHECKLIST.md).

## What's in v0.5.0

### Core runtime (`engine/`)

| Area | What you get |
|------|----------------|
| **Sessions & I/O** | Async telnet server, login/reconnect, optional gateway child (clients survive game-only restart), copyover hot-reload |
| **World model** | Rooms, characters, groups, movement helpers, map loader, runtime room flags, map heal/backups |
| **Hooks** | Registration surface so games wire chargen, persist, help, dispatch, and domain behavior without engine imports of game code |
| **Persistence** | Character/world save helpers; lean `Character` surface games extend via attach hooks |
| **Tick loop** | Ordered tick registry — games register heartbeat callbacks at boot |
| **Content** | JSON content store + validation; dual-root **kind profiles** (`engine/content_kinds/`) with `extends` merge, templates, and lint; abstract `item.generic` / `npc.generic` / `creature.generic` / `map.earth` grandparents in `engine/content/kinds/` for games to `extends` |
| **OLC** | In-game menu wizard (`engine/olc.py`) for authoring entities through kind profiles |
| **Stats** | Shared six-primary spine (`POW` / `VIT` / `FOC` / `FIN` / `RES` / `PRE`) + Tier helpers |
| **Character sheet** | Schema (`engine/content/sheet_profile.json`) + `engine/systems/sheet.py` assembly/framing; games register field + section hooks |
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
| **Combat (swing)** | `combat_core`, `combat_engine`, `combat_mundane`, `combat_martial_arts`, `combat_osr` | Brief → apply → narrate; ids `mundane` (demo brawl), `martial_arts` (stance RPS), `osr` (d20 vs AC + hooks) |
| **Combat (twitch)** | `active_combat`, `active_combat_defense`, `combat_runtime`, `readiness`, `fight` | Optional timestamp-buffered backend — basegame demo; separate from swing registry |
| **Anatomy** | `anatomy`, `body_parts` | Region HP tiers, `plan_region_damage` / `apply_region_damage`; games register max-HP resolver |
| **Environment** | `room_structure`, `breach` | Wall/floor state (`get_wall_state`, wreck/repair); slam/breach hooks |
| **Spawn** | `spawn/` | Bestiary tables + nest-AI dispatch |
| **Instances** | `instance_rooms` | Tear down pocket/instance rooms when empty |
| **Social & gear** | `mail`, `social_catalog`, `wearables`, `containers`, `floor_loot` | Letters, emote catalog, clothing slots, containers |
| **Civic** | `clinic`, `justice`, `player_site` | Injury intake, crime case shell, player-owned site hooks |
| **Quests** | `quests`, `quests_loader` | Quest state machine + JSON loader |
| **Map authoring** | `map_store` | OLC dig/link/room-field helpers; games register field catalogs + seed-item placement via hooks |
| **Phone** | `phone` | Numbers, contacts, ring/answer/hangup, plane-local dial, payphone fee hook |
| **Appearance** | `appearance` | Generic look-slot catalog, short/long description builder; games register kits + catalogs |
| **Persona traits** | `persona_registry` | Trait catalog load/validate/save, need multipliers, conflict + traveler APIs |
| **Relationships** | `relationships` | Directed-tag relationship core: kind ladder, CRUD, asymmetry codes, favorite-person resolution |
| **Studio bridge** | `studio_bridge` | Hot-reload hook for Area Studio content edits (monorepo tool; bridge API ships in engine) |
| **Other** | `pathfind` (BFS), `languages`, `umbral`, `utility_delay`, `press_beat`, `origin_registry`, `sheet` | Pathfinding, language tags, umbral shroud shell, timed actions, press-beat pacing, score-sheet schema |

Combat design invariant: **math resolves to a Structured Battle Brief (data); prose is a separate render step.** The public engine ships **generic demo** swing math (`mundane`, `martial_arts`, `osr`) and an optional **twitch** backend (`active_combat`) for basegame — not the private SUPERS cinematic combat stack. See [`docs/ENGINE_CONSUMER.md`](docs/ENGINE_CONSUMER.md) § Combat systems.

### Reference game (`basegame/`)

A complete **proof consumer** with zero SUPERS dependency — copy patterns from here:

- **Notbigville, Kansas** — demo town + wilds map (`content/zones/notbigville.json`)
- **Chargen** — four mundane jobs, six-stat point-buy, `score` via engine sheet hooks (`basegame/sheet_score.py`)
- **Gates** — four rotating elemental gates (smoke-tested rotation)
- **Needs** — registered hunger/fatigue meters with seek/critical behavior
- **Combat (swing)** — heartbeat `resolve_round` via `combat_engine`; default `mundane`; optional `martial_arts`
- **Combat (twitch)** — optional `active_combat` backend on flagged rooms (`loadcombat`, telegraphs)
- **Weather & travel** — regional weather, America overland atlas, Storm Watch chase desk
- **Mail, shops, justice, slam** — thin verb wrappers over engine frameworks
- **Spawn nests** — bestiary + nest-AI demo

Wiring example: `basegame/bootstrap.py` registers hooks; `basegame/tick_bootstrap.py` orders heartbeat callbacks.

### Classic OSR demo (`classic/`)

Schema-first second consumer — OSR STR–CHA, War/Cleric/Mage/Rogue, d20 combat:

- **Millbrook** — ten-room village + linked wilderness (`classic/content/`)
- **Combat** — generic `osr` engine (`combat_osr.py`) + classic resolver hooks; heartbeat + instant `attack`/`cast`
- **Kind profiles** — `classic/content/kinds/` validated at boot
- **Catalog JSON** — classes and spells in `classic/content/catalog/`
- **Verbs** — `score`, `sheet`, `attack`, `cast`, `skill`

Boot: `RIFTFORGE_GAME=classic python server.py` (never auto-selected; default remains `basegame` for `python -m engine`).

## Layout

| Path | Role |
|------|----------------|
| `engine/` | Generic MUD core + `engine/systems/` frameworks |
| `world.py` / `persistence.py` / `command_support.py` | Thin root facades over engine cores |
| `server.py` / `commands.py` / `maps.py` / `game_select.py` | Boot, dispatch, map loader, `RIFTFORGE_GAME` chooser |
| `basegame/` | Reference game (Notbigville demo) |
| `classic/` | OSR fantasy demo (Millbrook + wilds) |
| `engine/content/sheet_profile.json` | Score-sheet field catalog (games extend via hooks) |
| `content/maps/demo.json` | Minimal map for lean engine boot (`RIFTFORGE_GAME=none`) |
| `docs/ENGINE_CONSUMER.md` | How a game registers on the engine |
| `docs/RELEASING_RIFTFORGE.md` / `docs/UPGRADING_RIFTFORGE.md` | Cut / consume a release |

## Smoke tests

```bash
python tools/engine_smoke.py      # lean engine — no game package on disk
python tools/basegame_smoke.py    # basegame reference game
python tools/classic_smoke.py     # classic OSR demo
```

CI runs all three on every push.

## Building your own game

1. `pip install -e .` (or pin `@v0.5.0`).
2. Read [`docs/ENGINE_CONSUMER.md`](docs/ENGINE_CONSUMER.md) — hooks for chargen, persist, help, `register_all_hooks()`.
3. Copy `basegame/` or `classic/` as a skeleton, or register your package via `RIFTFORGE_GAME=yourgame`.
4. Put catalogs in your repo (`content/kinds/`, maps, NPCs); register kind dirs with `set_content_kinds_dirs`. Run `tools/scaffold_game_mode.py` for a fresh tree.
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
