# Game mode schema checklist

Every new engine consumer (`classic/`, future modes) must ship **kind
profiles + validated JSON + boot gate** before gameplay code. OLC and
Area Studio rely on the same shapes — ad-hoc Python dicts break lint,
`content_new`, and live authoring.

## Required for each game package

| Step | Artifact |
|------|----------|
| 1 | `{game}/content/kinds/*.json` kind profiles (`extends` engine parents) |
| 2 | `{game}/content_validate.py` — `validate_kind` on maps, zones, catalogs, bestiary |
| 3 | `{game}/bootstrap.py` — `set_content_kinds_dirs([engine, game kinds])` + call `validate_all_content()` in `register_all_hooks` |
| 4 | `{game}/content/catalog/*.json` for classes, spells, items, … (not hardcoded Python tables) |
| 5 | `docs/templates/kind_*.{game}*.example.json` — run `schema_sync_templates.py` after kind edits |
| 6 | `tools/{game}_smoke.py` — boot + `validate_all_content()` |
| 7 | `game_select.py` — `RIFTFORGE_GAME={game}` wiring |
| 8 | `tools/scaffold_game_mode.py --slug {game}` when starting a **new** mode from scratch |

## Scaffold a new mode

```powershell
py -3.13 tools/scaffold_game_mode.py --slug forgequest --label "Forge Quest"
```

Then copy wiring from `classic/` (`bootstrap.py`, `content_validate.py`,
`game_select.py` branch).

## Classic reference kinds

| Kind | Writes to |
|------|-----------|
| `zone.classic.pocket` | `classic/content/zones/*.json` |
| `map.classic.wilderness` | `classic/content/maps/*.json` |
| `room.classic.indoor` / `outdoor` | zone/map `rooms[]` |
| `catalog.classic.class` | `catalog/classes.json` rows |
| `catalog.classic.spell` | `catalog/spells.json` rows |
| `creature.classic.hostile` | `bestiary/*.json` rows |

Lint locally:

```powershell
$env:RIFTFORGE_GAME = "classic"
py -3.13 -c "from classic.content_validate import validate_all_content; validate_all_content(); print('ok')"
py -3.13 tools/classic_smoke.py
```

See also: `docs/plans/classic_game_mvp.md`, `docs/CONTENT_AUTHORING.md`.
