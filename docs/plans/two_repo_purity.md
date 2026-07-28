# Two-repo purity: public Riftforge, private SUPERS

Living plan for splitting the monorepo into a **public Riftforge engine**
anyone can download, and a **private SUPERS game** that depends on it.
Authoritative short status still lives in [`HANDOFF.md`](../../HANDOFF.md);
hook API details grow in [`../ENGINE_CONSUMER.md`](../ENGINE_CONSUMER.md).

**Status:** Phases 0–6 **done** for the remote split. Public
[`riftforge-engine`](https://github.com/capnknives/riftforge-engine)
(`v0.2.0` pin); private `capnknives/RiftForge` (SUPERS). **Phase 7**
(engine framework extraction + `basegame/` + Stage G root glue) is
**complete** — Stages **1–9**, A1/A2, Plan B, Stage G, and the public
`v0.2.0` tag. Ongoing: export discipline when engine changes; dual-mount
hack when editing both trees. Gateway:
[`connection_gateway.md`](connection_gateway.md) (shipped).
T3 hygiene peels (`persistence-api` / `lean-demo` / dirty-saves **bench**)
are done in [`refactor_plan.md`](refactor_plan.md); full incremental
dirty-saves stay parked until measured GO.

**Naming:** two-repo **Phase 7** (this file) is **not** combat
“Phase 7” template-blend weaving — that stays parked under combat prose
plans.

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
| **5** | Remote split | ✅ public `riftforge-engine` + private SUPERS; pin `v0.2.0` |
| **6** | Living docs | ✅ RELEASING / UPGRADING / LIVE_DEPLOY + CI + gateway/auto-deploy verify |

Phases 0–6 complete (2026-07-17). Follow-on hygiene (lean `python -m engine`
demo, monorepo engine dedup) is explicitly later — not required for the
remote split.

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
AGENTS.md's "Where things live" — but Phase 3's lean, installable engine
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
  undecomposed root modules (AGENTS.md's "Where things live") — Phase 4/5
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

## Phase 5 notes (done)

**Opened / remotes cut 2026-07-17.** Merged to SUPERS `main`. Pin
`@v0.2.0` on [`riftforge-engine`](https://github.com/capnknives/riftforge-engine).

| Lock | Value |
|------|-------|
| Public remote | `capnknives/riftforge-engine` |
| Private SUPERS | `capnknives/RiftForge` (private) |
| Pin shape | `riftforge @ git+https://github.com/capnknives/riftforge-engine.git@v0.2.0` |

### Staging (all done)

1. **Packaging proof** ✅
2. **Public tree layout** ✅ — `tools/export_public_engine.py`
3. **Remotes** ✅ — public engine + private SUPERS + pin

## Phase 6 notes (done)

Living docs + ops verify (2026-07-17):

- RELEASING / UPGRADING / LIVE_DEPLOY name dual-mount + pin-bump + gateway
  hold + auto-deploy fetch timeout.
- Public engine CI: `engine_smoke` on `riftforge-engine`.
- SUPERS CI: full smoke + engine-only + pin-resolve job.
- Local Docker: `RIFTFORGE_GATEWAY=1` holds clients across game restart;
  auto-deploy poll must not freeze the watcher (`AUTO_DEPLOY_FETCH_TIMEOUT`).

## Live Docker (must not regress)

1. Host edits → bind-mount → `watch_and_run` → with gateway on: restart
   **game only** (clients held on `:4000`); with gateway off: SIGUSR1 copyover.
2. SUPERS `origin/main` advances → `auto_deploy` overlays → same restart /
   copyover path as (1).
3. After split: auto-deploy polls **private SUPERS** only. Engine reaches
   live via editable bind-mount (hack) or SUPERS pin-bump commit (tagged).
4. Gateway process is long-lived; auto-deploy still overlays the game tree
   only — never put game logic in `engine/gateway.py`.

## Phase 7 — engine extraction + basegame

Follow-on after the remote split (Phases 0–6). Goal: grow reusable
frameworks under `engine/` / `engine/systems/`, prove them with
`basegame/`, keep SUPERS lore and catalogs private. **Hygiene track** —
does not invent product #1 (`remaining_project_schedule.md` + newest
HANDOFF “Next up” still win). Unpark a stage explicitly (“start Stage N”).

Archive of the A1/A2/Plan B design note:
[`../archive/phase7_a1_a2_plan_b_complete.md`](../archive/phase7_a1_a2_plan_b_complete.md).

### Phase 7 status

| Stage / piece | Status | PR / note |
|---------------|--------|-----------|
| **1** — `content_store` / `content_validate` / `tick_registry` → `engine/` | ✅ Done | [#782](https://github.com/capnknives/RiftForge/pull/782) |
| **2** — `basegame/` + `game_select.py` | ✅ Done | [#793](https://github.com/capnknives/RiftForge/pull/793) |
| **3** — generic `engine/systems/weather.py` (SUPERS weather stays) | ✅ Done | [#798](https://github.com/capnknives/RiftForge/pull/798) |
| **A1 + A2** — `engine/stats.py` + basegame shared spine + `score` | ✅ Done | [#837](https://github.com/capnknives/RiftForge/pull/837) |
| **Plan B** — `attach_supers` → 14 `_attach_*` helpers | ✅ Done | [#842](https://github.com/capnknives/RiftForge/pull/842) |
| Public export includes `basegame` in `PUBLIC_PATHS` | ✅ Done | with #837 |
| New public tag / friend push to `riftforge-engine` | ✅ Done | **`v0.2.0`** — Phase 7 frameworks + basegame; SUPERS pin bumped |
| **Notbigville weather/travel demo** | ✅ Done | CONUS `regional_weather` + overland + storm chase + globe/aerial → `engine/systems/`; basegame Notbigville; public **`v0.3.0`**; SUPERS pin `@v0.3.0`. Facades kept. See § next purity pass. |
| **4** — needs/meter kit + effort → `engine/systems/` | ✅ Done | [#844](https://github.com/capnknives/RiftForge/pull/844) |
| **5** — economy coin/vendor primitives | ✅ Done | [#856](https://github.com/capnknives/RiftForge/pull/856) |
| **6** — pathfind BFS → `engine/` | ✅ Done | [#855](https://github.com/capnknives/RiftForge/pull/855) |
| **7** — `engine/systems/combat_core` (brief only) | ✅ Done | [#848](https://github.com/capnknives/RiftForge/pull/848) |
| **8** — lean Character debt (`t3-lean-room-flags`) | ✅ Done | [#849](https://github.com/capnknives/RiftForge/pull/849) |
| **9** — mail / socials / clothing + basegame proof | ✅ Done | [#864](https://github.com/capnknives/RiftForge/pull/864) |
| **G** — root `server.py` / `maps.py` / chargen/help | ✅ Done | Maps stamper [#865](https://github.com/capnknives/RiftForge/pull/865); boot seed / chargen / help [#867](https://github.com/capnknives/RiftForge/pull/867) |

### Boundary rule (locked)

**Promote into `engine/` (or `engine/systems/`) when the module is a
reusable framework** another game could opt into — even if SUPERS-tuned
defaults exist today.

| Layer | Owns |
|-------|------|
| **`engine/` / `engine/systems/`** | Primitives + frameworks: meters, coin/vendor APIs, pathfind BFS, battle-brief build/apply, content store, tick registry, shared spine, generic ambient weather **and** CONUS `regional_weather` / overland / storm chase / globe+aerial (Notbigville / `v0.3.0`), lean Character surface |
| **`basegame/`** | Proof consumer: adopts engine frameworks; ships minimal verbs/help/maps; no SUPERS lore |
| **`supers/`** | Catalogs, Origin/Path/Cadence fiction, combat prose/lexicon, daylight + clinic/radio/elemental **hooks** into regional weather, Tier flavor names, fuel economies, town AI; thin facades for peeled frameworks |

**Confirm before promoting (edge cases):** public remote visibility (new
tags / friend access), anything that would force SUPERS lore into the
public tree, or peels that break live Docker / gateway. Default when
unsure: **ask**, then peel with supers re-export facades (Stage 1 pattern).

**Stay in supers (explicit):** Cadence town AI, hospital/clinic fiction,
crime, alignment/incap kill methods, combat prose/narrate/lexicon, full
`training.py` Track-B, `daylight`, Origin fuel chassis. CONUS weather /
storm chase / America overland / globe flight now live under
`engine/systems/` with supers re-export facades (`v0.3.0`).

### Done notes (Stages 1–9, A1/A2, Plan B)

- **Stage 1:** moved JSON catalog helpers + ordered tick registration into
  `engine/`; supers keeps re-export facades. Lean boot gets an empty tick
  pipeline.
- **Stage 2:** `RIFTFORGE_GAME=basegame` via `game_select.py`; mutual
  exclusion vs supers; `tools/basegame_smoke.py` proves a second game.
- **Stage 3 (revised):** do **not** move SUPERS CONUS weather — ship new
  generic ambient weather under `engine/systems/weather.py` and wire
  basegame; SUPERS weather/daylight/storm stay private.
- **A1:** six primaries + Tier math in `engine/stats.py`;
  `Character.stats` / `tier` set in `engine/world.py`; `supers/stats.py`
  re-exports; engine-smoke asserts the generic defaults.
- **A2 (bundled with A1 in #837):** basegame drops its 4-stat spine,
  adopts the shared six, ships `score` / `sc` + help.
- **Plan B:** pure code-motion split of `attach_supers()` into fourteen
  named `_attach_*` helpers (508 field defaults unchanged).
- **Stage 4:** name-agnostic capped 0–1 meter kit (`seek_rate`, attach/ensure/
  dump/load/clamp, `advance`, `satisfy`, `sate_ambient`, `is_critical`,
  `most_urgent`/`most_critical`, `level_phrase`) in `engine/systems/needs.py`;
  `supers/needs.py` keeps the eleven-meter set + all fiction (Vampire fuel
  mirroring, Celestial skips, pack duty, homesickness tiers, ...) via a thin
  facade — every public function keeps its exact name/signature, so none of
  the ~70 existing call sites changed. `supers/effort.py`'s meter clamp
  routes through the same kit. No `basegame/` hunger/thirst wiring yet
  (deferred as optional/later, per the original stage scope). `needs_timing`
  output confirmed byte-identical pre/post refactor.
- **Stage 5:** auditing `supers/economy.py` (~1,140 lines) the same way
  Stage 4 / Stage 7 audited needs / combat found that only the wallet
  ledger is genuinely reusable framework — `format_money`,
  `money_noun` / `money_score_label`, `wallet_balance` /
  `bank_balance`, `can_afford`, `deposit`, and `withdraw` now live in
  `engine/systems/economy.py` (optional dynamic `coins` /
  `bank_coins` via getattr; Character attach + persist stay in supers).
  The stage blurb's "flat currency, vendor stock, buy/sell" undersells
  the coupling: vendor catalogs, `buy` / `sell` / `fence_to_vendor`,
  gig work (`start_work` / `stop_work` / `tick_work`), stipends,
  thrift / `can_afford_resource`, and `needs.NEED_RESOURCE` mapping are
  all SUPERS content and stay in `supers/economy.py` as an unchanged
  re-export facade for the ledger helpers. Room `vendor_stock` already
  lives on `supers/room_attach.py` (Stage 8) — no change there. Engine
  readers already used defensive `getattr(character, "coins", 0)`
  (mission strongbox reward in `engine/verbs/basic.py`); nothing else
  under `engine/` needed rewiring. No `basegame/` cash wiring yet
  (deferred like Stage 4 / Stage 7).
- **Stage 7:** researching `supers/combat.py`'s `build_brief` (~977 lines)
  and `apply_brief` (~35 lines) found almost all of both are genuinely
  SUPERS content, same boundary-rule call as A1 (`accuracy()`/`evasion()`/
  etc. stayed in `supers/stats.py`) — `gap_offense_mult`/
  `outcome_mult_for_gap` (SUPERS' tuned tier-gap curve) and `apply_brief`
  (Integrity absorption, hex-breaking, GMCP push, no clean generic slice)
  were deliberately left untouched, not overlooked. The one genuinely
  generic, non-trivial mechanism found: `_roll_reaction`'s rescale-and-roll
  step (weighted outcomes with a guaranteed floor share for the default),
  now `roll_weighted_outcome()` in `engine/systems/combat_core.py`.
  `_roll_reaction` (its one caller, inside `build_brief`) delegates just
  that tail; every accuracy/evasion/crit/block chance formula feeding the
  weights is untouched. Verified bit-for-bit RNG-identical against the old
  inline roll across 200k+ random trials (a first pass using Python's
  `sum()` builtin surfaced a genuine floating-point divergence at rare
  threshold boundaries — fixed by summing with a plain accumulation loop
  instead). No `basegame/` combat exists yet, so no consumer wiring in this
  PR either.
- **Stage 8:** audited `Character.__init__` field-by-field first —
  every SUPERS-flavored-looking field (`regimen`, `spirit`/`spirit_state`/
  `spirit_tether`, `gm_rank`, `body`/`body_room`, `password_hash`,
  `snooping`/`snoopers`, `idle_mode`, ...) turned out to be read/written
  directly by real `engine/` code (`command_support.py`, `connection.py`,
  `persistence.py`, `snoop.py`) — Character was already lean, nothing
  moved. The debt was entirely on `Room`: ~46 fields (Vampire/hunter
  hunt-AI, Demon travel + Hellcraft wards, Croatoan, Divine consecration,
  Cadence lodging/homestead, town-system flags, Jinn mirage ids, city
  paint metadata) had zero `engine/` reader and moved to new
  `supers/room_attach.py`, wired through new `set_room_attacher`/
  `attach_room` hooks (`engine/hooks.py`, mirroring the Character
  attacher exactly). `plane`/`realm` and `outdoor` were deliberately kept
  generic (`engine/systems/weather.py` reads `outdoor` directly; `plane`
  would have needed a second `maps.py` fix for no clear boundary-rule
  win). One real compatibility bug found and fixed: `maps.py`'s
  `_add_room` had a bare `room.vampire_nest` self-read (deciding
  `spawn_nest` when JSON omits it) that would `AttributeError` for any
  game without a room attacher — now `getattr(room, "vampire_nest", False)`.
  `basegame/` needed no changes (never touched any of the 46 fields).
- **Stage 6:** auditing `supers/pathfind.py` (~673 lines) the same way
  Stage 7 audited `build_brief` found that only the deque BFS mechanism
  is genuinely reusable framework — `path_directions_to` /
  `next_step_toward` / `path_to_room` now live in `engine/pathfind.py`
  with an injected `edge_ok(from_room, neighbor)` callback so the
  engine never imports SUPERS passability. `supers/pathfind.py` keeps
  public signatures unchanged (thin wrappers that pass `passable` as
  `edge_ok`) plus everything that is Cadence/lore: `passable` itself
  (evil_zone / vampire_safe / hunter_safe / evil_ward / lodging ACL /
  no_loiter), `avoid_evil_for`, the immersion `step` walk (gait/vessel
  prose, escort, followers, cast barks), and pocket/homeward lore
  (`next_hop_homeward`, `_homeward_allow`, wilderness gateways,
  preferred enter aliases). One purity fix in the same peel: the
  module-level `from supers import economy` moved inside `step()` so
  the BFS core path stays free of that dependency. No `basegame/`
  pathfind wiring yet (deferred like Stage 4 / Stage 7).
- **Stage 9:** auditing mail / socials / clothing the same way Stages
  5/7 audited economy / combat found three small frameworks, not three
  large subsystems. **Mail:** text inbox + send/read/discard /
  `is_mail_room` / login notify → `engine/systems/mail.py`;
  `supers/mail.py` keeps `ship_item` (Curio / rare_dealer) as a
  re-export facade. **Socials:** catalog validate / resolve / perform /
  format_list → `engine/systems/social_catalog.py` (injected
  `find_in_room`); SUPERS keeps `socials.json` + `make_social_commands`
  (free-form `emote` was already engine). **Clothing:** stacked wear
  map + wear/remove/rebind → `engine/systems/wearables.py` (injected
  `is_clothing` / `slot_for` / `display_key`); SUPERS keeps catalog
  stamp, restring, outfits, look lines. **basegame proof:** Post Office
  room + `mail` verb + help (score pattern); canned socials and wear
  deferred like Stage 4/7.

### Remaining stages (detail)

**Phase 7 complete.** Stage G (maps stamper #865 + boot seed / chargen /
help #867) and public **`riftforge-engine` `@v0.2.0`** shipped; Notbigville
weather/travel demo + **`@v0.3.0`** shipped after. Root `server.py` /
`maps.py` remain glue shells by design.

**Later (not scheduled):** full dirty-tracked saves only after
`tools/persist_save_bench.py` (or live lag) shows GO + explicit unpark.
Further public tags when engine APIs change.

### Next purity pass (post-v0.3.0) — parked until explicit unpark

Hygiene follow-on after the Notbigville peel. **Do not** treat as free
backlog or invent #1 (`AGENTS.md` rules 15 / 17). Unpark with an
explicit “start overland purity” (or similar) ask.

| Item | Why it exists | Suggested fix when unparked |
|------|---------------|-----------------------------|
| Lazy optional ``importlib`` loads of ``supers.*`` inside `engine/systems/overland.py` | Foot travel works without them; vehicles / dungeons / Lebanon starter / solar / planar influence still resolve via `_try_game_module` when SUPERS is installed | Replace with `engine.hooks` registrations (Stage-1 / vehicle-enter pattern); then assert no ``supers`` strings under `engine/` even via importlib |
| Dual weather modules | `engine/systems/weather.py` = tiny ambient kit (Stage 3); `regional_weather.py` = CONUS + tornadoes | Keep both; optionally strengthen module docs / README — **do not** merge or delete ambient without asking |
| SUPERS re-export facades | `supers/weather.py`, `overland.py`, `storm_watch.py`, `globe.py`, `stellar_globe_flight.py` | **Keep** until a dedicated call-site rewrite is worth the churn |
| Climate-contrast pockets | Plan wanted Seattle/Miami; hubs must exist at atlas link time | Add stub zones + pocket rows when someone wants climate demo contrast |
| `engine_smoke` vs monorepo | Smoke fails if `supers/` is present by design | Run purity/engine smoke in the public export tree or with `supers` renamed aside |

**Done for v0.3.0 (boot gate, not full purity):**
- `from supers` / `import supers` lines removed from `engine/` (scanner-clean).
- `_do_transition` uses `hooks.encounter_check` (not root `world.encounter_check`).
- Hellhound sight via `hooks.set_can_see_hellhound`.
- Overland still optionally loads SUPERS modules by qualname for vehicle/Cadence paths.

### Delegation (who leads what)

| Agent | Best for |
|-------|----------|
| **Grok** | Stage **9** + **G** lead (**done**) — boundary audit, API shape, `basegame/` wiring, SoT fidelity |
| **Composer** | Easy leftovers **at the end** only: Stage-1-style facades, call-site re-exports, smoke asserts, status-table / CHANGELOG polish — once Grok has locked the API |
| **Sonnet** | API/shape forks historically (`needs` Stage 4, `combat_core` Stage 7, lean Character Stage 8); edge-case go/no-go — all Sonnet-led stages done |

Stages **4** / **7** / **8** → Sonnet lead (**done**). Stages **5** /
**6** → Composer peels (**done**). Stage **9** + **G** → Grok lead
(**done**). Public **`v0.2.0`** tag → Grok (**done**).

### Peel recipe (every remaining stage)

1. Linked worktree + feature branch from `origin/main` (not staging).
2. Move framework into `engine/` or `engine/systems/`; supers re-export
   facade (Stage 1 pattern).
3. Zero `from supers` / `import supers` under `engine/`.
4. Targeted smoke (+ `basegame_smoke` / `engine_smoke` when the surface
   is shared). Crash-surface → local Docker gate before live-bound PR.
5. CHANGELOG.d fragment; PR; maintainer merges.

## Related docs

- [`../ENGINE_CONSUMER.md`](../ENGINE_CONSUMER.md) — how a game registers hooks
- [`../RELEASING_RIFTFORGE.md`](../RELEASING_RIFTFORGE.md) — cutting public tags
- [`../UPGRADING_RIFTFORGE.md`](../UPGRADING_RIFTFORGE.md) — bumping the pin in SUPERS
- [`../LIVE_DEPLOY.md`](../LIVE_DEPLOY.md) — watch/copyover/auto-deploy after the split
- [`refactor_plan.md`](refactor_plan.md) — hygiene T2/T3 pointer (Phase 7 SoT is this file)
- [`tools/export_public_engine.py`](../../tools/export_public_engine.py) — `PUBLIC_PATHS` (includes `basegame`)
- [`tools/basegame_smoke.py`](../../tools/basegame_smoke.py) / [`tools/engine_smoke.py`](../../tools/engine_smoke.py)
- Archive: `docs/archive/HANDOFF_HISTORY.md` (“Engine/SUPERS folder split”)
- Archive: [`../archive/phase7_a1_a2_plan_b_complete.md`](../archive/phase7_a1_a2_plan_b_complete.md)
