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
| Chargen | `set_chargen(async_fn)` | skip (return True) | `chargen.run` |
| Help topics | `set_help(topics, categories)` | empty | `help_topics` maps |
| Command dispatch | `set_dispatch(fn)` | `None` (npc_do no-ops) | `commands.dispatch` |
| Eclipse ambient line | `set_eclipse_ambient_line(fn)` | `""` | `supers.balance.eclipse_ambient_line` |
| Vampire fear message | `set_vampire_fear_message(fn)` | `None` | `supers.slayer.fear_message_for_vampire` |
| Look/examine quirk | `set_look_quirk(fn)` | `None` | `supers.relationships.maybe_look_quirk` |
| Pre-move gate | `set_move_gate(fn)` | `None` (never blocks) | `supers.bootstrap._move_gate_block` (jail + hunter-safe) |
| Cancel awake rest | `set_cancel_rest(fn)` | no-op | `supers.lodging.cancel_rest_if_any` |
| Loot-from-body line | `set_loot_room_line(fn)` | generic "`<actor> takes <item> from <body>.`" | `supers.scavenge.loot_room_line` |
| Strongbox relic reward | `set_make_relic_item(fn)` | `None` | `supers.faith.make_relic_item` |
| Spirit-sight gate | `set_can_see_spirit(fn)` | only a spirit sees itself | `supers.bootstrap._can_see_spirit` (Spirit Magic OR Attunement ≥15) |
| Pre-move cancel | `set_before_relocate(fn)` | `None` (nothing to cancel) | `supers.bootstrap._before_relocate` (cancels training) |
| Post-move arrival | `set_after_arrive(fn)` | no-op | `supers.bootstrap._after_arrive` (stop work, carry body, lodging owner-enters) |
| Room-entry encounter roll | `set_encounter_check(fn)` | no-op | `supers.world_ext.encounter_check` (wilderness/dungeon spawns + aggro) |
| Evil Strikes Back world-meter defaults | `set_ensure_game_defaults(fn)` | no-op | `supers.balance.ensure_game_defaults` |
| Recompute max HP | `set_recompute_hp(fn)` | no-op | `supers.bootstrap._recompute_hp` |
| Legacy strongbox upgrade | `set_upgrade_legacy_container(fn)` | no-op, reports "not upgraded" | `supers.world_ext.upgrade_legacy_strongbox` |
| Map seed-item builder | `set_make_world_item(fn)` | plain flavor `Item` from `item_data` alone | `supers.items.make_world_item` |

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
undecomposed root modules — Phases 4–5 will finish the split. Hooks are
what let all of these stop **hard-coding** SUPERS imports in the meantime.

## See also

- [`RELEASING_RIFTFORGE.md`](RELEASING_RIFTFORGE.md)
- [`UPGRADING_RIFTFORGE.md`](UPGRADING_RIFTFORGE.md)
- [`LIVE_DEPLOY.md`](LIVE_DEPLOY.md)
