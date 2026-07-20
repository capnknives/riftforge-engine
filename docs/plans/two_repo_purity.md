# Two-repo purity: public Riftforge, private SUPERS

Living plan for splitting the monorepo into a **public Riftforge engine**
anyone can download, and a **private SUPERS game** that depends on it.
Authoritative short status still lives in [`HANDOFF.md`](../../HANDOFF.md);
hook API details grow in [`../ENGINE_CONSUMER.md`](../ENGINE_CONSUMER.md).

**Status:** In progress — **Phase 5 remote split OPEN**. Phase 0–4b done.
Worktree: `D:/Claude/riftforge-phase5` on `feature/two-repo-phase5`.
Public engine remote (locked): **`capnknives/riftforge-engine`**. Current
monorepo becomes private SUPERS. Gateway design:
[`connection_gateway.md`](connection_gateway.md) (shipped).

## Locked decisions

- **Dependency:** SUPERS → Riftforge only. Never `engine` → `supers`
  (lazy imports count as violations).
- **End state:** Two GitHub remotes — public
  **`capnknives/riftforge-engine`**, private **`capnknives/RiftForge`**
  (SUPERS; this monorepo).
- **Wiring:** SUPERS `pyproject.toml` pins Riftforge via **GitHub version
  tags** on `riftforge-engine` (pip). Local hacking: editable path install.
  Live Docker: dual bind-mount while editing; tagged pin for clean ship.
- **Purity gate:** `import` / minimal server boot works with SUPERS
  **uninstalled**. No Origins, Cadence, or game content in the public tree.
- **Live Docker loop preserved:** bind-mount + `watch_and_run` +
  auto-deploy on **SUPERS** `origin/main` (see
  [`../UPGRADING_RIFTFORGE.md`](../UPGRADING_RIFTFORGE.md) /
  [`../LIVE_DEPLOY.md`](../LIVE_DEPLOY.md)). With
  `RIFTFORGE_GATEWAY=1`, the gateway holds `:4000` and the watcher restarts
  **game only**; with `=0`, in-process copyover remains.

## Phases

| Phase | Goal | Exit criteria |
|-------|------|----------------|
| **0** | Document destination | This file + consumer/upgrade stubs linked from AGENTS/HANDOFF |
| **1** | Registry hooks | Character/persist/chargen/help registered; no hardwired `attach_supers` / blob import |
| **2** | Engine purity | ✅ `rg "from supers\|import supers" engine/` is empty (incl. old function-local lazy imports in `engine/verbs/basic.py`) |
| **2b** | `command_support.py` purity | ✅ shared move/spirit-sight helpers hookified; zero supers imports in `engine/command_support.py` |
| **3** | Lean world + game bootstrap | ✅ MVP: lean `engine/world.py`/`engine/persistence.py`; dual installable packages declared; game entry alias added |
| **4** | Engine-only smoke | ✅ CI job `engine-only-smoke` green with SUPERS absent (`tools/engine_smoke.py`) |
| **5** | Remote split | 🔄 **OPEN** — public `riftforge-engine` + private SUPERS; pip tag pin; Docker dual-mount |
| **6** | Living docs | RELEASING / UPGRADING / LIVE_DEPLOY kept current |

Phase 5 is **open** (2026-07-17). Pre-gates green: Phase 4 engine-only
smoke + Phase 4b gateway / soft-optional boot.

## Phase 2 notes (done)

`engine/` (including `engine/verbs/basic.py`'s old function-local lazy
imports) has zero `from supers` / `import supers`. What changed:

- **New optional-callable hooks on `engine/hooks.py`:** `eclipse_ambient_line`,
  `vampire_fear_message`, `look_quirk`, `move_gate_block`, `cancel_rest`,
  `loot_room_line`, `make_relic_item`, plus `set_dispatch`/`get_dispatch` for
  `engine/npc_act.py`. Each defaults to a safe no-op/None; `supers/bootstrap.py`'s
  `register_all_hooks()` wires the real SUPERS implementations (and
  `commands.dispatch`) in.
- **`who` / `time` / `idlemode` moved to `supers/verbs/engine_flavor.py`.**
  Once you strip the SUPERS flavor (badges, World Tide, eclipse ambience,
  Cadence AI) out of these three, almost nothing engine-generic is left, so
  rather than grow a hook per line they moved wholesale. `engine/verbs/basic.py`
  keeps lean stubs under the same verb names for a bare engine install; the
  `SUPERS_COMMANDS` dict-merge in `commands.py` overrides them when SUPERS is
  present.
- **`smoke_test.py`'s `engine_hooks_purity_tests`** now scans every `.py`
  file under `engine/` for a SUPERS import (the Phase 2 exit criterion
  itself, not just a note in this doc) and, in the same SUPERS-import-blocked
  subprocess used for the lean-`Character` check, exercises the lean
  who/idlemode stubs and every new hook's no-game default.

## Phase 2b notes (done)

`command_support.py`'s shared move/spirit-sight helpers (`_move_one`'s
training-cancel/work-stop/carried-body/lodging-owner calls,
`_can_see_spirit`'s Spirit-Magic/Attunement check, `_pull_followers`'
hunter-safe check) reached into `supers` directly. They were exempt from
the Phase 2 gate because they lived at the repo root, not `engine/` — see
the monorepo layout notes — but Phase 3's lean, installable engine
package needed them hook-ified the same way `engine/verbs/basic.py`'s old
lazy imports were in Phase 2:

- **Four new hooks on `engine/hooks.py`:** `can_see_spirit`,
  `before_relocate`, `after_arrive`, `encounter_check`. Each defaults to a
  safe fallback (a spirit always perceives itself; nothing to cancel;
  no-op arrival; no-op encounter roll) so a bare engine move still works
  with no game installed.
- **`engine/command_support.py`** now holds the actual helper code (zero
  `supers` imports), reading those four hooks plus the existing
  `move_gate_block` hook (`_pull_followers`' hunter-safe check now reuses
  the same gate `cmd_move` itself calls, instead of importing
  `supers.slayer` directly).
- **Root `command_support.py`** is a thin re-export facade over
  `engine/command_support.py`, so every existing
  `from command_support import X` callsite keeps working unchanged.
  `SPIRIT_SIGHT_ATTUNEMENT` moved to `supers/bootstrap.py` (a pure SUPERS
  tuning constant the engine-side default never needed).
- **`supers/bootstrap.py`'s `register_all_hooks()`** wires the real
  implementations: `_can_see_spirit` (Spirit Magic OR Attunement ≥15),
  `_before_relocate` (training cancel), `_after_arrive` (work stop +
  `cadence.move_body` + `lodging.check_owner_enters`), and
  `world_ext.encounter_check` (wilderness/dungeon spawn + aggro rolls).

## Phase 3 notes (MVP done)

Lean world + persistence cores now live under `engine/`, with SUPERS-only
game content split out to `supers/`:

- **`engine/world.py`**: `GameObject`, `Room`, `Item`, `Character`,
  `make_body`, `break_follows` — zero SUPERS imports.
- **`supers/world_ext.py`**: everything else the old root `world.py` had —
  the training dummy, wilderness/dungeon hostile spawning, procedural
  dungeons, lockboxes, and `encounter_check` (now wired onto
  `engine.hooks.encounter_check` — see Phase 2b above).
- **Root `world.py`** is a re-export facade: engine names are real,
  eager imports; SUPERS names (`make_wilderness_hostile`,
  `DUNGEON_ENCOUNTER_CHANCE`, ...) are re-exported **lazily** via a
  module-level `__getattr__` (PEP 562) so `import world` /
  `from world import Character` keeps working with SUPERS completely
  uninstalled — only touching a SUPERS-only name needs SUPERS on the path
  at that moment. GM `setdungeonchance` and smoke_test.py mutate
  `supers.world_ext.DUNGEON_ENCOUNTER_CHANCE`/`WILDERNESS_ENCOUNTER_CHANCE`
  directly now, not through the facade (a facade attribute ASSIGNMENT would
  only ever shadow the copy on `world`, never the name the check functions
  actually read).
- **`engine/persistence.py`**: the full SQLite save/load layer, now with
  zero SUPERS imports — the two spots that used to reach into
  `supers.balance`/`supers.stats` directly go through two new hooks
  (`ensure_game_defaults`, `recompute_hp`), plus a third
  (`upgrade_legacy_container`) that also let `engine/verbs/basic.py`'s
  `cmd_open` drop its own latent `from world import upgrade_legacy_strongbox`
  lazy import. Root `persistence.py` is a thin re-export facade.
- **`maps.py`** (still at the repo root, per the task's "root maps can
  stay" option): gained `set_maps_dir`/`get_maps_dir` for a future
  standalone consumer, and its one remaining `from supers import items`
  seed-item lookup now goes through a new `make_world_item` hook
  (`supers.items.make_world_item`, registered in `register_all_hooks()`).
- **Packaging (MVP):** root `pyproject.toml` now declares `engine` as the
  installable `riftforge` package (`[tool.setuptools.packages.find]`).
  `supers/pyproject.toml` declares the `supers` package with a monorepo
  path dependency on `riftforge` (`file://..`), plus a
  `supers/__main__.py` alias so `python -m supers` mirrors
  `python server.py`. This has **not** been exercised with a real
  `pip install -e .` + `pip install -e ./supers` round-trip yet — the live
  server and `smoke_test.py` still run the whole monorepo unpackaged
  (repo root on `sys.path`), which is what the Docker bind-mount +
  editable hacking loop needs regardless. A real install-and-run pass
  is Phase 5 work (the actual remote split).
- **Not done in this MVP pass:** `server.py`/`commands.py` remain shared,
  undecomposed root modules (see the monorepo layout notes) — Phase 4/5
  will need to finish deciding what, if anything, of those moves.

## Phase 4 notes (done)

Engine-only CI smoke with SUPERS **physically absent**:

- **`tools/engine_smoke.py`** — asserts lean `Character`, lean
  `who`/`idlemode`, hook no-op defaults, `engine.command_support` /
  `engine.persistence`, root `world` facade, `maps.load_all_maps()`, and
  Phase 4b lean `import commands` / `import server` + `Game(:memory:)`.
  Refuses to run if `importlib.util.find_spec("supers")` is non-None.
- **CI job `engine-only-smoke`** in `.github/workflows/ci.yml` —
  `mv supers supers.off` then `python tools/engine_smoke.py`. Full
  monorepo `smoke-test` job is unchanged.
- **Phase 4b (done):** soft-optional `server.py` / `commands.py` + Level 3
  connection gateway — shipped on `main` (`07b6987`, login fix `4fc5bc6`).
  See [`connection_gateway.md`](connection_gateway.md).

## Phase 5 notes (in progress)

**Opened 2026-07-17.** Worktree `D:/Claude/riftforge-phase5`
(`feature/two-repo-phase5`).

| Lock | Value |
|------|-------|
| Public remote | `capnknives/riftforge-engine` |
| Private SUPERS | Current `capnknives/RiftForge` (privatize when remotes cut) |
| Pin shape | `riftforge @ git+https://github.com/capnknives/riftforge-engine.git@vX.Y.Z` |

### Staging

1. **Packaging proof** ✅ (same monorepo): `pip install -e .` then
   `pip install -e ./supers` (Windows: use `riftforge>=0.1.0`, not
   `file://..`). `tools/packaging_smoke.py` + `tools/engine_smoke.py`
   with `supers` aside. Worktree venv: `.venv-phase5/`.
2. **Public tree layout:** see table below.
3. **Remotes:** create `capnknives/riftforge-engine`, privatize
   `RiftForge`, tag `v0.1.0`, pin SUPERS, update LIVE_DEPLOY /
   RELEASING / UPGRADING.

### Public vs SUPERS tree (layout target)

**Public (`riftforge-engine`):**

- `engine/` (entire package)
- Root packaging: `pyproject.toml` (riftforge / engine only)
- Lean facades needed for install: `world.py`, `persistence.py`,
  `command_support.py` (or fold into `engine` and drop facades later)
- `maps.py` + optional **demo** maps only (no SUPERS realm JSON)
- `tools/engine_smoke.py`, `docs/ENGINE_CONSUMER.md`,
  `docs/RELEASING_RIFTFORGE.md`
- Lean boot: prefer `python -m engine` demo listener; no SUPERS hooks

**SUPERS-only (`RiftForge` private):**

- `supers/`, `content/` (npcs, immersion, full maps, catalogs)
- `help_topics.py`, full `smoke_test.py`, `server.py` game entry,
  `commands.py` merge, `chargen.py`, game tools under `tools/`
- Docker / auto-deploy / live ops; pins `riftforge-engine`

**Deferred to remotes cut:** actually creating `riftforge-engine` and
privatizing — after packaging proof is green.

## Live Docker (must not regress)

1. Host edits → bind-mount → `watch_and_run` → with gateway on: restart
   **game only** (clients held on `:4000`); with gateway off: SIGUSR1 copyover.
2. SUPERS `origin/main` advances → `auto_deploy` overlays → same restart /
   copyover path as (1).
3. After split: auto-deploy polls **private SUPERS** only. Engine reaches
   live via editable bind-mount (hack) or SUPERS pin-bump commit (tagged).
4. Gateway process is long-lived; auto-deploy still overlays the game tree
   only — never put game logic in `engine/gateway.py`.

## Related docs

- [`../ENGINE_CONSUMER.md`](../ENGINE_CONSUMER.md) — how a game registers hooks
- [`../RELEASING_RIFTFORGE.md`](../RELEASING_RIFTFORGE.md) — cutting public tags
- [`../UPGRADING_RIFTFORGE.md`](../UPGRADING_RIFTFORGE.md) — bumping the pin in SUPERS
- [`../LIVE_DEPLOY.md`](../LIVE_DEPLOY.md) — watch/copyover/auto-deploy after the split
- Archive: `docs/archive/HANDOFF_HISTORY.md` (“Engine/SUPERS folder split”)
