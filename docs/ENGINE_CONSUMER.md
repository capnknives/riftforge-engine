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

## Stellar / Umbral — basegame only (not SUPERS)

Alien **Stellar** and **Umbral** bloodlines are **public engine demo**
content (`basegame/`, `engine/systems/umbral.py`, `basegame/content/maps/stellar_orbit.json`).
They are **not** part of the live supernatural MUD (`supers/`). SUPERS removed
`solar.py`, `umbral.py`, soak maps, paths, disciplines, and player verbs;
legacy saves heal to Human/Mortal via `content.heal_retired_alien_bloodline`.
Do not reintroduce Stellar/Umbral kits into `supers/` without an explicit
maintainer unpark.

## Hook registry (`engine.hooks`)

Call these **before** constructing `Character`s or loading a save:

| Hook | Setter | Default (no game) | SUPERS registers |
|------|--------|-------------------|------------------|
| Character attach | `set_character_attacher(fn)` | no-op | `supers.character_attach.attach_supers` |
| Load-time normalize | `set_normalize_character_after_load(fn)` | no-op | `supers.character_load_normalize.normalize_character_after_load` |
| Persist blob | `set_blob_codec(to_blob, from_blob)` | `{}` / no-op apply | `supers.persist_blob` |
| Game meta load/save | `set_game_meta_codec(load_fn, save_fn)` | no-op | `supers.persist_meta` |
| Chargen | `set_chargen(async_fn)` | skip (return True) | `chargen.run` |
| Help topics | `set_help(topics, categories)` | empty | `help_topics` maps |
| Command dispatch | `set_dispatch(fn)` | `None` (npc_do no-ops) | `commands.dispatch` |
| Party-merge auto-accept | `set_can_auto_companion(fn)` | `True` (lean auto-accept) | `supers.companion.can_auto_companion` |
| Eclipse ambient line | `set_eclipse_ambient_line(fn)` | `""` | `supers.balance.eclipse_ambient_line` |
| Room look extras | `set_room_look_extras(fn)` | `[]` | `supers.bootstrap._room_look_extras` (planar, haunt, vehicles, boards, …) |
| Room command hints | `set_room_command_hints(fn)` | `[]` | `supers.command_hints.commands_here_lines` (`commands here`) |
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
| Mine stratum tables | `register_mine_stratum_table(realm_id, table)` | empty `{}` table | `supers/mine/stratum_tables.py` |
| Mine marker stumble chance | `set_mine_marker_chance(fn)` | `0.0` | `supers/mine/companies.py` |
| Mine geology seed | `set_mine_geology_seed(fn)` | deterministic hash default | `supers/mine/stratum_tables.py` |
| Mine tool requirement | `set_mine_tool_check(fn)` | requires pick/shovel item | `supers/mine/stratum_tables.py` (Origin overrides) |
| Mine carry weight cap | `set_mine_carry_weight_cap(fn)` | unlimited (`None`) | `supers/mine/stratum_tables.py` |
| Mine gate job permission | `set_mine_company_job_gate_permission(fn)` | always `False` | `supers/mine/companies.py` (v1 seam) |
| Mine discoverable roll | `set_roll_mine_discoverable(fn)` | `None` | `supers/mine/discoverables.py` |
| Mine face cleared | `set_on_mine_face_cleared(fn)` | no-op | `supers/mine/discoverables.py` |
| Mine support catalog | `set_mine_support_catalog(fn)` | timber/steel defaults | `supers/mine/stratum_tables.py` |
| Mine alloy recipes | `set_mine_alloy_recipes(fn)` | `[]` | `supers/mine/forge_recipes.py` |
| Mine forge recipes | `set_mine_forge_recipes(fn)` | `[]` | `supers/mine/forge_recipes.py` |
| Mine blast (parked) | `set_mine_blast(fn)` | no-op | GM `gm mine blast` test only in v1 |
| Public watch (crowd traffic + watch room) | `set_public_watch_hooks(movement_hears_fn, is_watching_room_fn)` | `movement_hears_predicate` → `base_hears` unchanged; `is_watching_room` → `False` | `supers.public_watch` via `register_all_hooks` |
| Helper query notify | `set_query_notify_hooks(event_fn, reporter_live_fn)` | no-op | `supers.query_notify` via `register_all_hooks` |
| `look houses` detail rows | `set_lodging_look_home_detail_lines(fn)` | `[]` | `supers.lodging_browse.look_home_detail_lines` via `_register_lodging_walk_hooks` |
| `open` / `close <dir>` structure doors | `set_try_directional_open(fn)` | `False` (fall through to container open) | `supers.doors.try_directional_open` via `register_all_hooks` |
| Live roster mutate (fuel-loop index + Cadence caches) | `set_fuel_loop_roster_hook(fn)` | no-op | `supers.fuel.fuel_loop_roster_hook` via `register_core_hooks` |
| Horse ride (scenic fuel) | `engine.systems.vehicles.set_vehicle_is_horse_ride` | `False` (stranded = out of gas) | `supers.bootstrap._register_vehicle_hooks` → `horses.is_horse_ride` (not `vehicle_is_motorcycle`, which SUPERS uses for open-top mount UX) |
| Quest giver cast ensure | `engine.systems.quests.set_quest_ensure_giver` | no-op | `supers.quests.policy.register_quest_hooks` → `require_for_needed` + `maybe_restore_for_cast_key` |
| Vault folded_by tag | `set_vault_folded_by(fn)` | `None` | `supers.fold_vault.vault_folded_by` |
| Post-overlay game checks | `set_post_overlay_game_checks(fn)` | `[]` | `supers.bootstrap._post_overlay_cuff_checks` (cuff `blob_fragment` / `load_fragment` probe) |

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

### Copyover / overlay reload order

Auto-deploy overlays land on disk while the game child still holds older
bytecode. Before a copyover save, ``engine.copyover.reload_world_save_modules``
must refresh modules in this order:

1. ``importlib.reload(engine.hooks)`` — persistence imports hook callables at
   module top; reloading persistence first raises ``ImportError`` on any newly
   added hook name (bug report 387).
2. ``importlib.reload(engine.persistence)``
3. ``game_select.reregister_blob_codec()`` (or ``hooks.reload_blob_codec()``)
   — ``reload(hooks)`` clears bootstrap callbacks; re-register before
   ``game.save(copyover=True)``.

If the blob codec is missing after reload, copyover **aborts** the save and
calls ``game_select.restore_hooks_after_copyover_abort()`` (full
``register_all_hooks``) so the process stays playable. Regression:
``py -3.13 tools/copyover_blob_codec_smoke.py`` and
``py -3.13 tools/boot_lifecycle_phase_d_smoke.py``.

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
| Engine wallet rows | `engine:cash`, `engine:bank` in the catalog — resolved in `engine/systems/sheet.py`; games must not duplicate Cash/Bank strings |
| Resolve API | `resolve_field_by_id`, `resolve_profile_slot`, `append_profile_slots` |
| Assembly | `engine/systems/sheet.py` (`SheetContext`, `render_score`, `format_assembled`) |
| Game rows | `hooks.register_sheet_field(id, fn)` — `fn(ctx) -> str \| None` |
| Game sections | `hooks.register_sheet_contributor(id, fn, priority=…)` — `fn(ctx) -> SheetSection \| list \| None` |

Basegame registers Path + HP field hooks in `basegame/sheet_score.py`.
Wallet lines come from the engine catalog (`engine:cash`, `engine:bank`), not
hand-rolled in game contributors. SUPERS body rows assemble in
`supers/sheet_score.py` (`format_score`, pane filter registry, Origin
contributors in `supers/sheet_score_hooks.py`); wallet and resource rows merge
via `resolve_profile_slot` / `append_profile_slots`. World Tide / eclipse
never belong on `score` or `time` — see `.cursor/rules/score-sheet-schema.mdc`.

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

`server.py`, `commands.py`, and `maps.py` (H1a loader in `engine/world_maps.py`,
H1b/c display in `engine/map_ui.py`; root `maps.py` is a thin facade; SUPERS
catalog lookups go through the `make_world_item` hook now) remain shared,
undecomposed root modules — optional hygiene tracked as
`arch-undecomposed-core` / [`plans/codebase_health_audit_2026-07-20.md`](plans/codebase_health_audit_2026-07-20.md)
(two-repo remotes Phases 0–6 are already done). Hooks are what let all of
these stop **hard-coding** SUPERS imports in the meantime.

## Engine framework modules (core expansion — closed 2026-08-04)

Phases 0–9 of [`plans/riftforge_core_expansion.md`](plans/riftforge_core_expansion.md)
are **done** (public **`v0.4.0`** tag). Generic frameworks live under
`engine/systems/` (and `engine/studio_bridge.py`); SUPERS registers game
flavor in `supers/bootstrap.py` and thin facades — the per-character hook
table above is unchanged.

| Bundle | Engine module(s) | Status |
|--------|------------------|--------|
| **Planes** | `engine/systems/planes/` — `register_plane`, pocket loader | Shipped |
| **Gates** | `engine/systems/gates/` — `GateNetwork`, rotation | Shipped |
| **Needs** | `engine/systems/needs.py` — `register_meter`, fuel lane | Shipped |
| **Combat (swing registry)** | `engine/systems/combat_engine.py`, `combat_core.py`, `combat_mundane.py`, `combat_martial_arts.py`, `combat_osr.py` | Shipped — `mundane` / `martial_arts` / `osr` ids |
| **Active (twitch) combat** | `engine/systems/active_combat.py`, `combat_runtime.py` | Shipped in engine; basegame/classic demo; SUPERS unchanged |
| **Body parts** | `engine/systems/anatomy.py`, `body_parts.py` — regions + `register_hook` | Shipped |
| **Room env** | `engine/systems/room_structure.py`, `breach.py` | Wall-state layer shipped; combat breach pipeline deferred (Phase 5c) |
| **Spawn** | `engine/systems/spawn/` — bestiary + nest AI | Shipped |
| **Instance rooms** | `engine/systems/instance_rooms.py` | Shipped |
| **Studio bridge** | `engine/studio_bridge.py` | Shipped |
| **Content kinds** | `engine/content_kinds/` + `set_content_kinds_dirs` hooks | Shipped (Phase 2) |
| **Cadence** | — | **Deferred** — no generic kernel to peel (Phase 4 finding) |
| **SUPERS narrative combat** | `supers/combat.py` → `supers/combat_prose.py` | Stays in SUPERS (not swing/active backends) |
| **Civic shops** | `engine/systems/civic_shop.py` (ware shell) | Shell shipped; deep `player_shops` → `civic_shop` wiring **deferred** (Phase 6b / H-track) |
| **Lifestyle / civic kernels** | `skill_ranks`, `gather_nodes`, `vendor_stock`, `claim_board`, `wage_curve` | Shipped **v0.6.2** — games keep catalogs and job titles |
| **Combat status / prose loader** | `status_conditions`, `prose_pool_loader` | Shipped **v0.6.2** — catalog + JSON pools stay in the game |
| **Clinic** | `engine/systems/clinic.py` | Framework shipped + wired (H4); Town Clinic room keys stay SUPERS-branded |
| **Justice** | `engine/systems/justice.py` | Framework shipped + wired (H4); crime catalog stays SUPERS |
| **Missions** | partial | Board shell deferred; quest content in SUPERS |

Basegame `aim`/body-parts demo gap: follow-up in
[`plans/riftforge_core_expansion_followup.md`](plans/riftforge_core_expansion_followup.md).

## Post–core-expansion hygiene (H-track)

Phases **H1–H7** landed on `feature/purity-h-track-remaining` (see
[`plans/two_repo_purity_extractions_plan.md`](plans/two_repo_purity_extractions_plan.md)):
`engine/map_ui.py`, `engine/systems/{vehicles,lodging,paced_travel,phone,appearance,persona_registry,relationships}`,
`engine/map_store.py`, plus **H4** wiring (`hospital`→`clinic`, `crime`→`justice`).
**Deep `player_shops` → `civic_shop`** remains DEFERRED. **H8** (kind
grandparents) and **H9** (`v0.5.0` tag) **landed**; SUPERS pin is **`@v0.6.2`**
(2026-08-29; liquid-flavor kernels. Prior **`v0.6.1`** 2026-08-27; **`v0.6.0`** 2026-08-21).


## Hook bundles (engine mudlib unification)

Clinic / justice / civic-fixture seams **shipped** 2026-08-16 (strangler
unpark #2391/#2394). Remaining planned names live in
[`plans/python_mud_engine_features_plan.md`](plans/python_mud_engine_features_plan.md)
and [`plans/supers_engine_overlap_audit.md`](plans/supers_engine_overlap_audit.md).

| Bundle | Phase | Status | Key registrations |
|--------|-------|--------|-------------------|
| Clinic admit | 1 | **Shipped** | `clinic_admit_hooks` — ward pick, discharge, collapse FSM; [`engine/systems/clinic.py`](engine/systems/clinic.py) / [`engine/systems/collapse.py`](engine/systems/collapse.py) |
| Justice fines | 2 | **Shipped** | Adapter + robbery peel; [`engine/systems/justice.py`](engine/systems/justice.py) / `justice_adapter` |
| Civic fixtures | 3 | **Shipped** | Structural HP/wreck/repair + `fixture_id`; [`engine/systems/civic_fixture.py`](engine/systems/civic_fixture.py) |
| Quest flags | 4a | Planned | `quest_flags` — generic `quest_progress` dict accessors |

Quest flags remain unimplemented. See
[`plans/python_mud_engine_features_plan.md`](plans/python_mud_engine_features_plan.md).

## See also

- [`RELEASING_RIFTFORGE.md`](RELEASING_RIFTFORGE.md)
- [`UPGRADING_RIFTFORGE.md`](UPGRADING_RIFTFORGE.md)
- [`LIVE_DEPLOY.md`](LIVE_DEPLOY.md)
