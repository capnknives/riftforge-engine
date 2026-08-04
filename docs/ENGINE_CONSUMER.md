# Engine consumer guide — how a game uses Riftforge

Games (today: **SUPERS**) sit on top of the Riftforge engine. They must
**register** their behavior at boot; the engine never imports the game.

Full purity roadmap: [`plans/two_repo_purity.md`](plans/two_repo_purity.md).

## Dependency direction

```
SUPERS (game)  -->  Riftforge (engine)
```

Never the reverse. Lazy `from supers import …` inside `engine/` is a
violation of the purity gate.

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
| Kind profile dirs | `set_content_kinds_dirs(dirs)` | `[]` (no kinds) | `supers/content/kinds/` via `register_core_hooks` |
| Kind domain validate | `set_content_kind_domain_validator(fn)` | skip | `supers.content_kinds.validators.validate_domain` |
| Kind catalog save | `set_content_kind_save_entity(fn)` | raises if OLC save | `supers.content_kinds.persist.save_entity` |
| Menu OLC auth | `set_olc_authorizer(fn)` | deny | GM check via `register_all_hooks` |

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

### Example (game boot)

```python
from engine import hooks
from supers.bootstrap import register_all_hooks

register_all_hooks()   # attach, blob, chargen, help
# then build Game / accept connections
```

## Lean engine demo

```text
python -m engine
# or: RIFTFORGE_GAME=none python server.py
```

Forces lean mode when unset, points `maps` at
`engine/demo/content/maps/` (one-room `demo.json`), and skips game-package
hooks. Opaque SQLite extras use `persistence.load_meta_json` /
`save_meta_json`; game-shaped Tide/Cadence meta stays in the game codec.

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
| Combat | 5 | `register_combat_engine(id, build_brief, apply_brief)`, `register_combat_narrator` — wraps existing `engine/systems/combat_core.py`, not a new roll implementation |
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
