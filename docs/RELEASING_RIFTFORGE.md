# Releasing Riftforge (public engine)

**Status:** Phase 6 remotes **done**; Phase 7 framework peels **done**;
`riftforge_core_expansion.md` Phases 1-8 **done**; two-repo purity H1-H9
extraction track (`docs/plans/two_repo_purity_extractions_plan.md`)
**done**. Public remote **`capnknives/riftforge-engine`**. Current SUPERS
pin: **`v0.5.0`** (adds map-authoring OLC helpers plus generic phone,
appearance-builder, persona-trait, and relationship-tag frameworks, and
abstract item/NPC/creature/map generic kind grandparents, on top of
`v0.4.0`'s elemental planes + rotating gates, dual-root content-kind
profiles, needs-meter registry, spawn bestiary + nest-AI dispatch,
instance-room teardown, Area Studio reload bridge, and the anatomy/body-
parts region state machine).

## Cut a release

1. Land engine-only changes on `riftforge-engine` `main` (or export from
   the monorepo via `python tools/export_public_engine.py` and push).
2. In that tree: `pip install -e .` then
   `python tools/engine_smoke.py` (no `supers/` present),
   `python tools/basegame_smoke.py`, and
   `python tools/classic_smoke.py`.
3. Tag `vX.Y.Z` (semver; breaking hook API = major)::

       git tag -a v0.5.0 -m "riftforge-engine v0.5.0 — map-store OLC + phone/appearance/persona/relationship frameworks"
       git push origin v0.5.0

   Prefer **`v0.5.0`** over older tags for new consumers.
   Re-exports via `tools/export_public_engine.py` ignore `__pycache__` /
   `*.pyc` and rewrite a lean root `help_topics.py` facade + public README +
   `.github/workflows/ci.yml` (player help content stays in private SUPERS:
   `supers/help_topics.py` + `help/topics/*.py`).

4. Announce in the engine CHANGELOG / commit message; never ship SUPERS
   content. Bump `supers/pyproject.toml` on the private monorepo to the
   new tag ([`UPGRADING_RIFTFORGE.md`](UPGRADING_RIFTFORGE.md)).

## Purity checklist before a tag

- No `supers` imports under the `engine` package
- No `content/npcs`, Origins catalogs, or SUPERS help pages
- Demo map: export writes `content/maps/demo.json` from canonical
  `engine/demo/content/maps/demo.json`; monorepo lean boot uses
  `RIFTFORGE_GAME=none` (`engine.lean_boot`). ``python -m engine`` boots
  **basegame** when that package ships (MVP demo); CI still forces ``none``.
- Score sheet: ``engine/content/sheet_profile.json`` +
  ``engine/systems/sheet.py``; games extend via
  ``register_sheet_field`` / ``register_sheet_contributor``.
- Optional env: ``RIFTFORGE_DB`` (SQLite path), ``RIFTFORGE_PORT`` (telnet)
- Next public tag after merge: **v0.5.1+** (classic OSR demo, `combat_osr`, combat docs map).
- Export includes `classic/`, `tools/classic_smoke.py`, and updated `docs/ENGINE_CONSUMER.md`.
- `tools/engine_smoke.py` / `tools/basegame_smoke.py` /
  `tools/classic_smoke.py` pass

See [`plans/two_repo_purity.md`](plans/two_repo_purity.md).
