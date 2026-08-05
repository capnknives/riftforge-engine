# Engine consumer guide — how a game uses Riftforge

Four layers in this monorepo (and after the public/private remote split):

| Layer | Path | Role |
|-------|------|------|
| **Engine** | `engine/` | Public Riftforge — generic MUD core. Zero game imports. |
| **Basegame** | `basegame/` | Shipped **proof consumer** — Notbigville demos every new engine API without SUPERS lore. Ships with public `riftforge-engine`. |
| **Classic** | `classic/` | Second public **OSR demo** — Millbrook village + wilds; schema-first catalogs; `RIFTFORGE_GAME=classic`. |
| **SUPERS** | `supers/` | Private production game — Origins, Cadence, catalogs, live play. Pins the engine via GitHub tags. |

Games **register** behavior at boot through `engine.hooks`; the engine never
imports a game. Exactly **one** game package runs per process —
`game_select.py` + `RIFTFORGE_GAME` (`supers` | `basegame` | `classic` | `none`).
Live defaults to SUPERS; `python -m engine` prefers basegame when present, else
lean demo. Never co-import game packages in one process (hooks would clobber).

Full purity roadmap: [`plans/two_repo_purity.md`](plans/two_repo_purity.md).

## Dependency direction

```
SUPERS (private game)  ──┐
                         ├──►  Riftforge engine
basegame (public proof)  ──┤
classic (public OSR demo) ─┘
```

Never the reverse. Lazy `from supers import …` inside `engine/` is a
violation of the purity gate. Game packages may import `engine` only — never
each other (`supers`, `basegame`, `classic` are mutually exclusive at runtime).

## Hook registry (`engine.hooks`)

Call these **before** constructing `Character`s or loading a save:

| Hook | Setter | Default (no game) | SUPERS registers |
|------|--------|-------------------|------------------|
| Character attach | `set_character_attacher(fn)` | no-op | `supers.character_attach.attach_supers` |
| Persist blob | `set_blob_codec(to_blob, from_blob)` | `{}` / no-op apply | `supers.persist_blob` |
| Game meta load/save | `set_game_meta_codec(load_fn, save_fn)` | no-op | `supers.persist_meta` |
| Chargen | `set_chargen(async_fn)` | skip (return True) | `chargen.run` |
| Help topics | `set_help(topics, categories)` | empty | `help_topics` maps |
| Command dispatch | `set_dispatch(fn)` | `None` (npc_do no-ops) | `commands.dispatch` |
| Eclipse ambient line | `set_eclipse_ambient_line(fn)` | `""` | `supers.balance.eclipse_ambient_line` |
| Vampire fear message | `set_vampire_fear_message(fn)` | `None` | `supers.slayer.fear_message_for_vampire` |
| Look/examine quirk | `set_look_quirk(fn)` | `None` | `supers.relationships.maybe_look_quirk` |
| Extra target match needles | `set_extra_target_match_needles(fn)` | `[]` | `supers.target_kinds.kind_match_needles` (Origin/Path/kind room targeting) |
| Pre-move gate | `set_move_gate(fn)` | `None` (never blocks) | `supers.bootstrap._move_gate_block` (jail + hunter-safe) |
| Cancel awake rest | `set_cancel_rest(fn)` | no-op | `supers.lodging.cancel_rest_if_any` |
| Loot-from-body line | `set_loot_room_line(fn)` | generic "`<actor> takes <item> from <body>.`" | `supers.scavenge.loot_room_line` |
| Strongbox relic reward | `set_make_relic_item(fn)` | `None` | `supers.faith.make_relic_item` |
| Spirit-sight gate | `set_can_see_spirit(fn)` | only a spirit sees itself | `supers.bootstrap._can_see_spirit` (Spirit Magic OR Attunement ≥15) |
| Dark-room night-sight | `set_can_see_in_dark(fn)` | False (torch only) | `supers.bootstrap._can_see_in_dark` (GM form, God twin, all non-Human Origins, heatvision, hostiles) |
| Pre-move cancel | `set_before_relocate(fn)` | `None` (nothing to cancel) | `supers.bootstrap._before_relocate` (cancels training) |
| Post-move arrival | `set_after_arrive(fn)` | no-op | `supers.bootstrap._after_arrive` (stop work, carry body, lodging owner-enters) |
| Room-entry encounter roll | `set_encounter_check(fn)` | no-op | `supers.world_ext.encounter_check` (wilderness/dungeon spawns + aggro) |
| Evil Strikes Back world-meter defaults | `set_ensure_game_defaults(fn)` | no-op | `supers.balance.ensure_game_defaults` |
| Recompute max HP | `set_recompute_hp(fn)` | no-op | `supers.bootstrap._recompute_hp` |
| Legacy strongbox upgrade | `set_upgrade_legacy_container(fn)` | no-op, reports "not upgraded" | `supers.world_ext.upgrade_legacy_strongbox` |
| Homeless floor-item sink | `set_orphan_item_room(fn)` | `game.start_room` | `supers.magic.orphan_item_room_for_game` (Beneath Lucifer's Cage) |
| Map seed-item builder | `set_make_world_item(fn)` | plain flavor `Item` from `item_data` alone | `supers.items.make_world_item` |
| Atlas map center | `set_map_center_room(fn)` | `None` | `supers.bootstrap._map_center_room` (America cell from `macro_pos`) |
| Special directional move | `set_try_directional_move(fn)` | False (classic exits) | `supers.overland.try_overland_move` |
| Special zone enter | `set_try_enter_zone(fn)` | False | `supers.overland.try_enter_landmark` |
| After classic zone enter | `set_after_zone_enter(fn)` | no-op | clear overland coords + dungeon hub soft-stamp |
| Special zone exit | `set_try_exit_zone(fn)` | False | `supers.overland.try_exit_to_overland` |
| After HELP_TOPICS page | `set_after_help_topic(fn)` | no-op | `supers.quests.notify(…, "help_topic")` |
| Quest grant / rewards | `engine.systems.quests.set_quest_grant_handler` / `set_quest_completion_reward_handler` | cash + flags only | `supers.quests.policy` (favor, catalog items) |
| Quest spawns / inventory | `set_quest_spawn_handler`, `set_quest_inventory_has`, `set_quest_inventory_consume` | no-op / plain inventory | `supers.quests.policy` |
| Quest predicates | `engine.systems.quests.register_quest_predicate` | built-in `complete_when` types | game-specific extensions |
| Quest predicate *type names* | `engine.systems.quests_loader.register_complete_when_types` | 13 generic types (`enter_room`, `has_item`, …) | `supers.quests.policy.SUPERS_COMPLETE_WHEN_TYPES` (`true_form`, `takehunt`, `rent`, …) |
| Quest catalog dirs | `engine.systems.quests_loader.set_quests_dirs` (additive) | `[]` | `supers/content/quests/` via loader facade |
| Quest empty-log / no-offers flavor | `engine.systems.quests.set_quest_empty_log_hint`, `set_quest_no_offers_hint` | generic SUPERS-free line | `supers.quests.policy` (chargen opener pointers) |
| Kind profile dirs | `set_content_kinds_dirs(dirs)` | `[]` (no kinds) | `supers/content/kinds/` via `register_core_hooks` |
| Kind domain validate | `set_content_kind_domain_validator(fn)` | skip | `supers.content_kinds.validators.validate_domain` |
| Kind catalog save | `set_content_kind_save_entity(fn)` | raises if OLC save | `supers.content_kinds.persist.save_entity` |
| Menu OLC auth | `set_olc_authorizer(fn)` | deny | GM check via `register_all_hooks` |
| Map JSON validator | `set_map_json_validator(validator)` | `engine.content_validate` fallback | `supers.content_validate` |
| Map area_type vocabulary | `set_map_area_types(dict_or_frozenset)` | 9-entry lean default (`ruins`, `city`, …) | `supers.maps_room_json.MAP_AREA_TYPES` |
| Map room city-meta stamper | `set_map_room_city_stamper(fn)` | no-op (lean engine ignores city header fields) | `supers.maps_room_json.stamp_map_room_city_meta` |
| Map-store OLC entry fields | `set_map_store_apply_entry_fields(fn)` | no-op | `supers.map_store` field catalogs (rset flags/text) |
| Map-store seed-item placement | `set_map_store_place_seed_items(fn)` | no-op | `supers.map_store` (lodging home-link + nest stamping) |
| Persona catalog path | `set_persona_content_path(fn)` | raises if unset | `supers.personas` (`supers/content/personas.json`) |
| Phone dial alias | `set_phone_dial_alias_resolver(fn)` | `None` (engine default lookup) | `supers.phone` (WKNZ / phonebook aliases) |
| Phone room-emote style | `set_phone_room_emote_style(paint_fn, tag_fn, call_tag_fn=None)` | passthrough / empty tags | `supers.phone` (styled `[PHONE]`/`[CALL]` tags) |
| Phone voicemail line | `set_phone_voicemail_line(fn)` | generic voicemail refusal | `supers.phone` (Echo auto-answer/asks text) |
| Phone payphone fee | `set_phone_payphone_fee(fn)` | `0` | `supers.phone` (`$1` per outbound call) |
| Appearance catalog path | `set_appearance_content_path(fn)` | raises if unset | `supers.appearance` (`supers/content/appearance.json`) |
| Appearance kit registry | `set_appearance_kits(kits)` | raises if unset | `supers.appearance.APPEARANCE_KITS` |
| Appearance kit person-words | `set_appearance_kit_person_words(mapping)` | `{}` | `supers.appearance._KIT_PERSON_WORD` |
| Appearance kit short nouns | `set_appearance_kit_short_nouns(mapping)` | `{}` | `supers.appearance._KIT_SHORT_NOUN` |
| Appearance no-crown hair styles | `set_appearance_no_crown_styles(mapping)` | `{}` | `supers.appearance._NO_CROWN_STYLES` |
| Appearance kit inference | `set_kit_for_character_resolver(fn)` | `None` | `supers.appearance` (Cosmic Elemental Aspect inference) |
| Appearance age phrase | `set_appearance_age_phrase_fn(fn)` | `None` | `supers.appearance` (decade phrase for `build_description`) |

SUPERS auto-registers attach + blob when the `supers` package is imported
(`supers.bootstrap.register_core_hooks`). Everything else (chargen, help,
dispatch, and the Phase 2/2b/3 game-flavor hooks above) is registered from
the game entry (`server.py`, via `supers.bootstrap.register_all_hooks()`)
so a bare engine import stays clean. See each hook's docstring in
`engine/hooks.py` for its exact call signature — most are one-line
callables (`fn(character, ...)` -> a value or `None`), not multi-step
protocols.

Phase 2b (`command_support.py`'s old shared move/spirit-sight helpers) and
Phase 3 (`world.py`/`persistence.py`'s lean cores) both moved under
`engine/` this way — see `docs/plans/two_repo_purity.md`'s "Phase 2b" and
"Phase 3" notes for the file-by-file breakdown.

`who`, `time`, and `idlemode` are NOT hooks -- they moved wholesale to
`supers/verbs/engine_flavor.py` because almost nothing generic was left in
them once the SUPERS flavor was stripped out. `engine/verbs/basic.py` keeps
lean stubs under the same verb names for a bare engine install; SUPERS'
richer versions win at the `{**ENGINE_COMMANDS, **SUPERS_COMMANDS}` merge in
`commands.py`. See `docs/plans/two_repo_purity.md`'s "Phase 2 notes".

## Help files (engine vs game)

The engine owns **help machinery**; each game owns **help text**.

| Layer | What ships | Where |
|-------|------------|--------|
| Engine | `cmd_help`, `help_db` SQLite overlay, `hedit` / `helpsubmit`, `ENGINE_COMMANDS` one-liners | `engine/verbs/basic.py`, `engine/help_db.py`, `engine/connection.py` |
| Game | `HELP_TOPICS` pages + `HELP_CATEGORIES` bare-`help` index | `supers/help_topics.py` + `help/topics/*.py`, `basegame/help_topics.py`, or `classic/help_topics.py` |

**Registration:** `hooks.set_help(topics, categories)` before players connect
(see table above). A bare `python -m engine` install has an empty topic map;
`help <verb>` still falls back to the `ENGINE_COMMANDS` one-liner.

**Lookup order** (`cmd_help`): DB overlay exact/alias → static
`HELP_TOPICS` → DB full-text search → `COMMANDS` one-liner → fuzzy
"Did you mean?" (`docs/plans/helpfile_editing_system.md`).

**Rule of thumb for engine promotions:** hook-only frameworks
(`combat_core`, `needs` decay, planes registry, …) document in this file
and plan docs — no player `HELP_TOPICS` unless the **game** exposes a
player loop. Player-facing engine verbs (`bug`, `follow`, `group`, …)
always get a `COMMANDS` one-liner from `ENGINE_COMMANDS`; the **game**
ships full topic pages when the verb needs more than one sentence
(AGENTS.md rule 11). GM/staff engine verbs (`hedit`, `reports`,
`resolve`) ship GM topic pages in the game layer (SUPERS: `help/topics/gm.py`;
basegame: `basegame/help_engine_topics.py`).

**HEDIT** is engine-based end-to-end; hot-edited pages live in
`riftforge.db` (`helpfiles` / `help_fts`) and override static pages at
lookup time without a deploy. Git-tracked `HELP_TOPICS` remains the
PR-reviewed source of truth for canon pages.

### Example (game boot)

```python
# Prefer game_select so only one package registers hooks:
#   RIFTFORGE_GAME=supers|basegame|classic|none
# Or call the active package's bootstrap explicitly:

from supers.bootstrap import register_all_hooks  # or basegame.bootstrap / classic.bootstrap

register_all_hooks()   # attach, blob, chargen, help
# then build Game / accept connections
```

## Combat systems — what runs where

Three **independent** combat paths share one design rule (hard rule 5): resolve
**math → brief (data) → apply → narrate prose** — never merge math and text.
They are **not** interchangeable backends inside SUPERS.

| Path | Code | Who uses it | Feel |
|------|------|-------------|------|
| **SUPERS narrative combat** | `supers/combat.py` → `supers/combat_prose.py` (+ lexicon) | Live game (`162.243.50.82`) | Full Structured Battle Brief — Momentum, Disciplines, signatures, incap, spar, … |
| **Swing combat** | `engine/systems/combat_engine.py` registry | `basegame/`, `classic/` | Heartbeat `resolve_round` + per-character `combat_engine` id |
| **Active (twitch) combat** | `engine/systems/active_combat.py` + `combat_runtime.py` | `basegame/` demo only today | Timestamp queues, telegraphs, Balance/Equilibrium, `punch`/`dodge`/… |

### Swing engines (`character.combat_engine`)

Registered on import of each module under `engine/systems/`:

| Id | Module | Purpose |
|----|--------|---------|
| `mundane` | `combat_mundane.py` | Generic demo brawl — weighted hit/crit/miss (basegame default) |
| `martial_arts` | `combat_martial_arts.py` | Second demo style — stance RPS + combo counter |
| `osr` | `combat_osr.py` | Generic d20 + attack bonus vs ascending AC; games register `register_osr_*` hooks |

**Classic** sets `combat_engine = "osr"` and registers class/BAB/AC math via
`classic/rules/osr_resolvers.py`. **Basegame** defaults to `mundane`; set
`character.combat_engine = "martial_arts"` to try the second style.

These are **not** lite copies of SUPERS prose combat — tiny briefs and one-line
`narrate()` strings for the public engine demo. Same *pattern*, different product.

### Active combat backend (optional second tick path)

`combat_runtime.py` loads backends:

- `swing` — basegame `resolve_round` (mundane/martial_arts/osr per character)
- `active_combat` — `active_combat.tick_active_combat` (kinetic engine id)

Rooms or NPCs with `active_combat=True` force new fights to
`fight.combat_mode = "active"`. SUPERS does **not** use this stack. Detail:
[`plans/fast_paced_combat_engine.md`](plans/fast_paced_combat_engine.md).

### Firearms / combat backends (do not cross-wire)

SUPERS and engine **active combat** both have gun-shaped verbs, but they are
**different systems**. Do not merge handlers, state attrs, or ammo models.

| | SUPERS (narrative swing) | Engine active combat (basegame demo) |
|--|--------------------------|--------------------------------------|
| Ammo | `supers/firearm_ammo.py` on wielded **Items** | `engine/systems/firearms.py` (`engine_firearm` / `firearm_sight`) |
| Verbs | `load` / `reload` / `unload`; shoot via **attack swing** | `reload` → `load` → `aim <name>` → `fire` (queue + telegraph) |
| `aim` | Melee **called shot** (`combat_aim` + anatomy) | Firearm **sight line** only |
| Backend | `supers/combat.py` round briefs | Detachable `active_combat` backend (`combat_runtime`) |

Detail: [`plans/fast_paced_combat_engine.md`](plans/fast_paced_combat_engine.md)
§ “Firearm boundary vs SUPERS”.

## Engine demo (`python -m engine`)

```text
python -m engine
# explicit lean one-room boot (CI / purity gate):
#   RIFTFORGE_GAME=none python server.py
# OSR fantasy MVP (Millbrook + wilds):
#   RIFTFORGE_GAME=classic python server.py
# side-by-side with Docker on :4000:
#   RIFTFORGE_PORT=5000 RIFTFORGE_DB=riftforge_engine.db python -m engine
```

When ``RIFTFORGE_GAME`` is unset, ``python -m engine`` boots the shipped
**basegame** MVP (Notbigville, jobs, weather, America atlas) if ``basegame/``
is present — never auto-picks SUPERS from the monorepo. With no game package
on disk it falls back to ``engine/demo/content/maps/demo.json`` (one room).

### ``RIFTFORGE_GAME`` (hosting)

| Value | Game |
|-------|------|
| ``supers`` | Production SUPERS (monorepo only) |
| ``basegame`` | Notbigville reference demo (public engine default for ``python -m engine``) |
| ``classic`` | OSR Millbrook demo (public engine; explicit only) |
| ``none`` | Lean one-room engine boot |
| unset / ``auto`` | ``supers`` if importable, else lean (monorepo); public tree uses ``basegame`` via ``python -m engine`` |

``RIFTFORGE_DB`` selects the SQLite file (default ``riftforge.db``).
``RIFTFORGE_PORT`` selects the telnet listen port (default ``4000``; set
``RIFTFORGE_GATEWAY=0`` for a direct bind when the Docker gateway owns
``4000``).

Opaque SQLite extras use `persistence.load_meta_json` /
`save_meta_json`; game-shaped Tide/Cadence meta stays in the game codec.

## Character sheet (`score`)

The engine owns sheet **schema**, **assembly**, and **framing**:

| Piece | Location |
|-------|----------|
| Field catalog | `engine/content/sheet_profile.json` |
| Assembly | `engine/systems/sheet.py` (`SheetContext`, `render_score`, `format_assembled`) |
| Game rows | `hooks.register_sheet_field(id, fn)` — `fn(ctx) -> str \| None` |
| Game sections | `hooks.register_sheet_contributor(id, fn, priority=…)` — `fn(ctx) -> SheetSection \| list \| None` |

Basegame registers Path + HP field hooks in `basegame/sheet_score.py`.
SUPERS body rows still build in `supers/verbs/character.py`; framing
routes through `engine.systems.sheet.format_assembled`. Peel SUPERS
blocks into `supers/sheet_score.py` contributors over time.

```python
from engine.systems.sheet import SheetContext, render_score
from engine import hooks

hooks.register_sheet_field("hp", lambda ctx: f"  HP: {ctx.target.hp}/…")
text = render_score(SheetContext(target=character, game=game, viewer=character))
```

## What still lives in the monorepo root

`world.py`, `persistence.py`, and `command_support.py` are now thin
re-export **facades** (Phase 3 MVP) over `engine/world.py`,
`engine/persistence.py`, and `engine/command_support.py` — the lean,
supers-agnostic cores actually live under `engine/`; the root files exist
purely so every existing `from world import X` / `persistence.X` /
`command_support.X` callsite across the codebase keeps working unchanged.
`world.py`'s facade re-exports SUPERS-only spawn content
(`supers/world_ext.py`) **lazily**, via a module-level `__getattr__` — so
`import world` / `from world import Character` still works with SUPERS
completely uninstalled, and only touching a SUPERS-only name
(`make_wilderness_hostile`, `DUNGEON_ENCOUNTER_CHANCE`, ...) needs SUPERS
on the path.

`server.py`, `commands.py`, and `maps.py` (map JSON loading; SUPERS
catalog lookups go through the `make_world_item` hook now) remain shared,
undecomposed root modules — optional hygiene tracked as
`arch-undecomposed-core` / [`plans/codebase_health_audit_2026-07-20.md`](plans/codebase_health_audit_2026-07-20.md)
(two-repo remotes Phases 0–6 are already done). Hooks are what let all of
these stop **hard-coding** SUPERS imports in the meantime.

## Planned hook bundles (Riftforge core expansion)

Not yet implemented — tracked in
[`plans/riftforge_core_expansion.md`](plans/riftforge_core_expansion.md).
Each row below becomes real `set_*`/`register_*` entries in the table above
as its phase lands; listed here now so a bundle name isn't picked twice.

| Bundle | Phase | Key registrations (planned) |
|--------|-------|------------------------------|
| Planes | 1 | `register_plane(plane_id, metadata)`, pocket loader |
| Gates | 1 | `register_gate_network(GateNetwork, room_predicate)` |
| Needs | 3 | `register_meter`, `register_fuel_meter`, `tick_needs`, decay policy hook — extends existing `engine/systems/needs.py`, not a new module |
| Cadence | 4 | `cadence_tick`, `register_need_pursuer`, `register_job_behavior` |
| Combat swing registry | 1 **DONE** | `register_combat_engine` in `engine/systems/combat_engine.py`; shipped `mundane`, `martial_arts`, `osr` |
| Active twitch combat | separate track | `engine/systems/active_combat.py` + `combat_runtime` — basegame demo; SUPERS unchanged |
| Body parts | 5b | `register_anatomy_regions`, `body_parts_heal_mult`, brief `target_region`/limb fields |
| Room env | 5c | `register_slam_target_pipeline`, `stamp_breach_pick`/`apply_breach`, layout-direction neighbor resolver |
| Spawn | 6 | `register_nest_ai`, bestiary table loader hooks |
| Missions | 6 | board accept/abandon/instance portal shell |
| Civic shops | 6b | `register_civic_fixture`, enter-pocket loader, wholesale order terminal hook, fixture HP/wreck/repair |
| Clinic | 7 | `register_clinic_rooms`, `can_hospitalize`, ward tick |
| Justice | 7 | wanted/fines/jail + `register_crime_catalog` |
| Studio | 8 | `studio_catalog_roots`, kind dirs (extends existing content-kind hooks) |

## See also

- [`RELEASING_RIFTFORGE.md`](RELEASING_RIFTFORGE.md)
- [`UPGRADING_RIFTFORGE.md`](UPGRADING_RIFTFORGE.md)
- [`LIVE_DEPLOY.md`](LIVE_DEPLOY.md)
