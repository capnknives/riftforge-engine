# Riftforge engine

Public MUD **engine** (pure Python, asyncio, stdlib only).
Game content (SUPERS) lives in a separate private repo and depends on tagged releases of this package.

Install: `pip install -e .`
Smoke: rename any local `supers/` aside, then `python tools/engine_smoke.py`.

Consumer guide: `docs/ENGINE_CONSUMER.md`.
Roadmap: `docs/plans/two_repo_purity.md`.

---

# Riftforge

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/capnknives/RiftForge)

A from-scratch, framework-free MUD engine in pure Python (standard library only).

**Riftforge** is the *engine* — networking, sessions, the tick loop, the parser, the
world model, persistence. **SUPERS** is the *game* built to run on it. Keeping them
mentally separate is deliberate: the engine doesn't know or care what game sits on top.

Past the vertical-slice stage now: characters persist through restarts, log off as
**Echoes** instead of vanishing (injury goes to Town Clinic — never deleted or
looted), have a real stat spine with tiers and long-horizon progression, can fight
and train, and GM tooling exists for running the thing day to day. See
[`HANDOFF.md`](HANDOFF.md) for the detailed, up-to-date status of every milestone,
and [`docs/SYSTEMS_DESIGN.md`](docs/SYSTEMS_DESIGN.md) for the full game design.

## Run it

**Locally:**

```bash
python server.py          # in one terminal (needs Python 3.11+)
telnet localhost 4000     # in another (open two to test multiplayer)
```

Windows: double-click `start-server.bat` — picks a Python 3.11+ interpreter
automatically and restarts the server if it crashes.

**In Docker** (auto-reloads on every code change — see "How it stays up to date" below):

```bash
docker compose up -d --build
telnet localhost 4000
```

Or double-click `start-docker.bat`. `docker-compose.yml` bind-mounts the project
directory, so `riftforge.db` (the save file) lives on your host, not inside the image.

Type a name at the prompt to create a character (you'll be asked to choose a password),
or type an existing name + password to reconnect to one.

**Map editor** (dev-only tool, separate from the game server):

```bash
python tools/map_editor/server.py     # http://127.0.0.1:8765
```

Windows: double-click `start-map-editor.bat`. A local browser-based tool for authoring
`content/maps/*.json` — see "Dev tools" below.

## What works

**The basics:**
- Password-protected login; reconnecting to an offline character wakes its **Echo**
  (logout never deletes a character — it becomes a figure standing in the world,
  shown as `Name (echo)` or `Name (echo, pushups)` when a regimen is set, until you
  log back in; Echoes can be injured into Town Clinic, never looted or deleted);
  `setpass` sets or changes your password after creation
- `look`/`l`, movement (`north`/`south`/`east`/`west`/`up`/`down`, or `n`/`s`/`e`/`w`/`u`/`d`,
  with auto-look on arrival), `say`/`'`, `who`, `quit`
- A **100×100 overworld** of wilderness rooms (`The Wastes (x, y)`); from gateway
  `(50, 50)` use `enter`/`in` for the nested Central Plaza / Shadowed Alley area, and
  `out`/`leave` to return to the wastes. `look` shows `Gravity: Nx` when a room is not
  normal gravity, and always shows `Area: <Type>` — every room carries a formal
  `area_type` tag (wilderness/ruins/city/mountains/ocean/lake/forest/plains). The grid
  is flagged **wilderness** — a small per-tick chance spawns a temporary,
  lethally-attackable hostile in an occupied room
- **Maps live as data**, one JSON file per map under `content/maps/` (loaded by
  `maps.py`, same pattern as the Origins/Disciplines catalogs below). From Central
  Plaza, `enter`/`in` also crosses into **the Cinder Reach**, a second, smaller
  20×20 wilderness grid (a Fire Plane) in its own file — proof that a room's exit
  can point at a room defined in an entirely different map, which is how future
  planes/locations get added without touching existing ones
- `get`/`take`, `drop`, `inventory`/`inv`/`i`
- `score`/`sc` — the stat spine: six primaries, Tier + a fan-power-scale flavor label
  (Street level → Galactic level, seven Tiers), Grade, Lifeforce, Energy, Power Level,
  Attunement, Stamina, a per-Tier Stat budget, and a qualitative Potential tag
- `time`/`date` — the current in-game day (a compressed clock, 3 game-days per real day)
- `commands`/`help` (or `help <verb>` for one command's detail) — generated directly
  from the dispatch table, so it can never drift from what's actually implemented
- `bug <description>` / `suggest <description>` — file a report (with your last few
  commands and any errors they raised) to a local log beside `riftforge.db`
- `sparaccept on|off` — let others spar your Echo while you're offline (non-lethal only;
  off by default so Echoes stay safe)
- `regimen <activity>|clear` — set the solo activity your Echo trains while logged off
  (reduced rate, stretch-capped; physical activities still respect room gravity)
- `path [id]` — show your Origin/Path (e.g. `Background: Street Rat` for a Human), or
  (once) pick a Human Background; `learn <discipline>` / `disciplines` — learn and list
  open Disciplines (Martial Arts, Energy Combat); `score` shows Origin/Path/Disciplines
- SQLite persistence (`riftforge.db`) — autosaves every tick, on connect/disconnect, and
  on shutdown

**Combat & progression:**
- `attack`/`kill <target>` — lethal combat, resolved every 3-second tick into a
  Structured Battle Brief (math) rendered as placeholder prose (a separate layer —
  Phase 3 swaps in a cinematic renderer without touching the math)
- `spar <target>` (try `spar dummy`) — the same engine in non-lethal mode: no death, no
  body drop, and both fighters gain stats from it. A permanent training dummy NPC is
  always available as a solo partner
- `train <activity>` (`pushups`/`situps`/`shadowbox`/`meditate`) — solo stat training,
  gated by stamina and per-activity fatigue (rotate activities; spamming one gets
  penalized, but never locked to a literal 0% chance). Physical activities cost and
  gain more under higher room gravity; a seeded **Gravity Chamber** (10x) sits east of
  Central Plaza
- Primaries climb slowly (fractional gains) up to a Tier-scaled cap, and the SUM of all
  six primaries is also capped per Tier (a build-diversity budget — maxing everything
  at once isn't possible); once either cap is hit, further training/sparring diverts
  into banked growth instead of dead-ending. Enough banked growth **breaks your Tier**
  — a public, room-wide moment, costing more banked growth at each successive Tier —
  raising the cap and giving `power_level` a genuinely accelerating (not just linear)
  climb the longer you play. A much-weaker character sharing a room with a much
  stronger one trains at a temporary boosted rate. A Tier 2+ character passively
  dampens lower-tier attackers (Aura Suppression), and a defender fighting a
  higher-tier attacker builds damage mitigation the longer they survive (Adaptation).
  `supers/balance_sim.py` (see below) is what verified this curve over simulated
  decades, not just guesswork.

**GM tooling** (`gmlist` to see who's staff):
- `restore [target]` — refill HP/stamina to the target's real (not default) max
- `set <target> <field> <value>` — edit a primary stat, `tier`, or `growth` directly
- `breaktier [target]` — force a Tier break on demand, for testing
- `setgravity <value>` — dial the current room's gravity for testing (session-only)
- `promote`/`demote <target>` — head-GM-only, grant/revoke ordinary GM rank
- `reports [n]` — list the most recent bug and suggestion reports
- `copyover` — hot-reload the whole server in place without dropping anyone (see below)

## How it stays up to date (Docker)

Two problems, both solved:

- **Stale code:** `engine/watch_and_run.py` is the container's entry point. It runs
  `server.py` as a child process and watches every `.py` file below the repo root
  (recursively — `engine/`, `supers/`, and the shared root files) for changes, so an
  edit takes effect within about a second — no more running yesterday's code without
  realizing it.
- **Dropped connections:** a code reload doesn't disconnect anyone. `engine/copyover.py`
  implements the classic MUD "copyover" technique — the process replaces its own program
  image in place (`os.execv`) while keeping every connected client's socket open across
  the swap, then reattaches each one to its character and resumes, skipping login
  entirely. `telnet` sessions just pause for a moment and keep working.

## In-game bug reports → Cursor Automations (fully automated)

1. Player files `bug` in telnet → webhook → Cursor cloud agent fixes → opens PR
2. You **squash-merge** the PR on GitHub
3. Docker's `engine/auto_deploy.py` (via `watch_and_run.py`) detects `origin/main`
   advanced, broadcasts an in-game countdown, overlays the fix, and copyovers

No manual deploy step. Configure webhook auth in `.env`; auto-deploy is on by
default (`AUTO_DEPLOY=1`). Disable with `AUTO_DEPLOY=0` in docker-compose / `.env`.

### Auto-deploy safety

- Auto-deploy runs **only when `origin/main` advances** (a new squash-merge SHA).
  It does **not** re-overlay because the local bind-mount drifted. Manual catch-up:
  `python tools/deploy_bug_fix.py --merged --bug-id N --summary "..."`.
- Overlay **never overwrites** pipeline modules listed in `AUTO_DEPLOY_PROTECT_PATHS`
  (defaults: `engine/bug_webhook.py`, `engine/bug_filing.py`, `engine/deploy_notify.py`,
  `engine/auto_deploy.py`, `engine/reports.py`). See `.env.example`.
- Side effects (webhooks, deploy hooks) live in `engine/` and register at import
  (`server.py` imports `bug_webhook`) — never as the only wiring inside
  `commands.py`. Webhook POSTs are GM-on-demand (`squashbugs`); player `bug`
  stays local.
- After changing pipeline code: push to `main` → `docker compose restart` → verify
  `squashbugs` exists and a GM `squashbugs` shows `[bug_webhook] POST ok` in
  docker logs. Toggle overlays live with GM `autodeploy on|off`.

See `.cursor/automations/SETUP.md` for the one-time Automations dashboard setup.

## The files

The repo root holds a shared core that isn't yet decomposed into engine vs. SUPERS —
see AGENTS.md's "Where things live" for exactly why each of these five is still mixed.

| File               | Job                                                                    |
|--------------------|-------------------------------------------------------------------------|
| `server.py`        | asyncio entry point, `Game` (world + db + sessions), the tick loop |
| `world.py`         | `GameObject` → `Room`/`Item`/`Character`; builds the starter world |
| `commands.py`      | thin merge of `ENGINE_COMMANDS` + `SUPERS_COMMANDS` into live `COMMANDS` |
| `help_topics.py`   | `HELP_TOPICS` / `HELP_CATEGORIES` (re-exported via `commands.py`) |
| `command_support.py` | helpers shared by both verb packages |
| `persistence.py`   | SQLite save/load |
| `maps.py`          | loads/validates `content/maps/*.json`; builds every `Room` (grids + hand-authored areas) |
| `smoke_test.py`    | the test suite — fakes the network, drives real game logic, must stay green |

**`engine/`** — generic, game-agnostic:

| File                        | Job                                                                    |
|------------------------------|-------------------------------------------------------------------------|
| `engine/connection.py`      | `Session` — per-client I/O, login/reconnect, the command loop (shared with copyover resume) |
| `engine/auth.py`            | password hashing (PBKDF2-HMAC-SHA256) |
| `engine/reports.py`         | append/read bug and suggestion reports (JSONL beside the save file) |
| `engine/verbs/`             | generic MUD command handlers (`ENGINE_COMMANDS`) |
| `engine/copyover.py` / `engine/watch_and_run.py` | zero-downtime hot-reload + Docker's auto-reload |

**`supers/`** — the game built on the engine (JSON data at `supers/content/`):

| File                        | Job                                                                    |
|------------------------------|-------------------------------------------------------------------------|
| `supers/stats.py`           | the stat spine: primaries, tiers, derived formulas, growth/Tier-breaking |
| `supers/character_attach.py` | SUPERS field defaults attached at end of `Character.__init__` |
| `supers/verbs/`             | SUPERS command handlers (`SUPERS_COMMANDS`) |
| `supers/hospital.py`        | Town Clinic injury / recovery (Echo-safe; never delete/loot) |
| `supers/training.py`        | solo training + sparring gains, stamina/fatigue, the Track-A→B cap-diversion |
| `supers/combat.py` / `supers/combat_prose.py` / `supers/combat_lexicon.py` | battle-brief math, the tagged-CFG cinematic renderer, and its word-pool loader -- kept as separate layers |
| `supers/content.py`         | loads/validates `supers/content/*.json`; Origin/Path/Discipline catalog lookups |
| `supers/bestiary.py`        | loads/validates `supers/content/bestiary/*.json`; wilderness spawn-table catalog lookups |
| `supers/balance_sim.py`     | a long-term progression *simulation* (not a test) — run on demand after tuning a constant |

## Dev tools

- **`tools/map_editor/`** (`start-map-editor.bat`) — a standalone, dev-only local web
  app (stdlib `http.server` + vanilla JS, `http://127.0.0.1:8765`) for authoring
  `content/maps/*.json` visually instead of by hand: paint `area_type` onto grid cells,
  drag prebuilt tile chunks onto the grid, resize a grid's width/height, and edit
  hand-authored rooms (exits, seed items) through forms. Every save re-runs the real
  `maps.load_all_maps()` loader as validation and rolls back on failure, so it can never
  write a map file the running game server would reject. Never imported by
  `server.py` — a separate tool, not an engine feature.

## Contributing / AI-assisted edits

Rules for changes — by hand or via AI tools — live in **[`AGENTS.md`](AGENTS.md)** (the
canonical guide) and **[`CONTRIBUTING.md`](CONTRIBUTING.md)**. Tool-specific pointers:
`.github/copilot-instructions.md`, `.cursor/rules/riftforge.mdc`, and `CLAUDE.md` all
defer to `AGENTS.md`, so the guardrails stay in one place.

## Project docs

- **[`docs/LORE.md`](docs/LORE.md)** — narrative / canon setting bible (planes,
  Primordials vs Celestials, immersion cast homes, open lore questions).
  Mechanics stay in SYSTEMS_DESIGN; this is the story reference.
- **[`docs/CONTENT_AUTHORING.md`](docs/CONTENT_AUTHORING.md)** — how to author
  maps, NPCs, catalogs, items, relics, personas, bestiary, and combat lexicon;
  points at `docs/templates/*.example.json` and in-game `help content`.
- **[`docs/SYSTEMS_DESIGN.md`](docs/SYSTEMS_DESIGN.md)** — the source of truth: stat
  spine, tiers, the Origin × Discipline model, progression, death/spirit/Reckoning,
  magic, combat rendering, accessibility, build order, and open decisions.
- **[`HANDOFF.md`](HANDOFF.md)** — the living "you are here": exactly what's built, what
  isn't, commit hashes, and what's queued next. Start here to pick the project up cold.
- **[`docs/COMBAT_LEXICON.md`](docs/COMBAT_LEXICON.md)** — the Phase-3 cinematic
  combat-prose word-pool corpus (live pools under `supers/content/combat_lexicon/`;
  this file is the reference for remaining gaps).
- **[`docs/BESTIARY.md`](docs/BESTIARY.md)** — the wilderness spawn-table reference:
  70 creatures (5 per Tier × 7 Tiers × 2 categories), live and wired up via
  `supers/bestiary.py` + `supers/content/bestiary/*.json`.
- **[`docs/ENGINE_ROADMAP.md`](docs/ENGINE_ROADMAP.md)** — the original from-scratch
  engine architecture plan (steps 1-7 are complete; kept for the reasoning, not as a
  live tracker — see `HANDOFF.md` for that).
- **[`docs/archive/`](docs/archive/)** — superseded design versions, kept for the
  reasoning trail through the pivots.

## Roadmap (short form)

See `HANDOFF.md` for the full, current status. In brief:

- **Phase 1 (Testable Basics)** — ✅ done: persistence + the Echo, the stat spine,
  minimal combat.
- **Phase 2 (Make it SUPERS)** — items 5–6 ✅; item 7 Body/Spirit/self-anchor ✅
  (Reckoning remainder **parked** — `docs/plans/reckoning.md`); item 8 ✅ closed
  (v0.36 — all Origin economies + Cosmic Favor + Magic sub-schools); item 9
  wilderness bestiary ✅.
- **Also done, outside the design doc's build order:** password-protected characters,
  GM/admin tooling, structural per-command helpfiles, Docker auto-reload, copyover,
  the long-term balance simulation, bug/suggestion reporting, the 100×100 overworld
  with nested starting area, a power-scale integration pass (Tier ladder extended
  to Galactic, Aura Suppression/Adaptation, a per-Tier total stat budget, a compressed
  game-time clock, and a catch-up mechanic), a real wilderness bestiary/spawn table
  (`docs/BESTIARY.md` wired up, Tier-matched spawns), and a formal `area_type` tag plus
  the standalone `tools/map_editor/` tool for authoring maps visually.
- **Next build** — default cluster is **immersion-parity Cadence verb gaps** (see
  `HANDOFF.md` prioritized Next-up). Then D15 signatures, combat-prose remainder,
  Ritual/Patron Quests / D35. Reckoning stays parked.

- **Phase 3 (Cinematic & Advanced)** — first workable combat prose + agency/fun
  pass ship; template blend / ultimates / Clash still open.

## License

MIT — see `LICENSE`.
