# Releasing Riftforge (public engine)

**Status:** Phase 6 remotes **done**; Phase 7 framework peels **done**;
`riftforge_core_expansion.md` Phases 1-8 **done**; two-repo purity H1-H10
extraction track (`docs/plans/two_repo_purity_extractions_plan.md`)
**done**. Public remote **`capnknives/riftforge-engine`**. Current public tag
**`v0.8.0`** (folklore kernels + Phase 2 purity restore; Occupants GMCP).
Prior **`v0.7.0`** (2026-09-05: Phase 2 purity restore, journal / rumor / job-catalog
kernels, flavor-neutral news/storm desks, chargen-menu sentinel). Prior
**`v0.6.2`** (2026-08-29: liquid-flavor kernels + kind stamps + party-merge
companion hook). Prior **`v0.6.1`** (2026-08-27: Phase 2 purity restore PR 3266
+ ~185 monorepo `engine/` commits since **`v0.6.0`**). Prior **`v0.6.0`**
(2026-08-21; 103 monorepo engine commits since **`v0.5.3`**: dispatch/door/
copyover hooks, universal closable doors, channel speech block + MSSP +
Discord ops hook peels, lag/cooperative-save hardening). Prior **`v0.5.3`**
strangler collapse FSM + robbery peel + fixture_id civic mirror.

## Cut a release

1. Land engine-only changes on `riftforge-engine` `main` (or export from
   the monorepo via `python tools/export_public_engine.py` and push).
2. In that tree: `pip install -e .` then
   `python tools/engine_smoke.py` (no `supers/` present),
   `python tools/basegame_smoke.py`, and
   `python tools/classic_smoke.py`.
3. Tag `vX.Y.Z` (semver; breaking hook API = major)::

       git tag -a v0.8.0 -m "riftforge-engine v0.8.0 — folklore kernels + Phase 2 purity"
       git push origin v0.8.0

   Prefer the **latest** tag for new consumers.
   Re-exports via `tools/export_public_engine.py` ignore `__pycache__` /
   `*.pyc` and rewrite a lean root `help_topics.py` facade + public README +
   `.github/workflows/ci.yml` (player help content stays in private SUPERS:
   `supers/help_topics.py` + `help/topics/*.py`).

4. Announce in the engine CHANGELOG / commit message; never ship SUPERS
   content. Bump `supers/pyproject.toml` on the private monorepo to the
   new tag ([`UPGRADING_RIFTFORGE.md`](UPGRADING_RIFTFORGE.md)).

## Purity checklist before a tag

- No `supers` imports under the `engine` package
- No lazy `import maps` under `engine/` — use `engine.world_maps` /
  `engine.map_ui` (root `maps.py` facade remains for monorepo tools)
- No `content/npcs`, Origins catalogs, or SUPERS help pages
- Demo map: export writes `content/maps/demo.json` from canonical
  `engine/demo/content/maps/demo.json`; monorepo lean boot uses
  `RIFTFORGE_GAME=none` (`engine.lean_boot`). ``python -m engine`` boots
  **basegame** when that package ships (MVP demo); CI still forces ``none``.
- Score sheet: ``engine/content/sheet_profile.json`` +
  ``engine/systems/sheet.py``; games extend via
  ``register_sheet_field`` / ``register_auto_field`` /
  ``register_sheet_contributor`` (new meters auto-appear in a band or
  overflow — do not hand-append in ``format_score``).
- Optional env: ``RIFTFORGE_DB`` (SQLite path), ``RIFTFORGE_PORT`` (telnet)
- Optional WebSocket TLS: ``RIFTFORGE_WSS_CERT`` + ``RIFTFORGE_WSS_KEY`` (pair)
- Monorepo engine version / current public tag: **v0.8.0**. Export via
  ``tools/export_public_engine.py``, then tag ``riftforge-engine`` remote.
- `tools/engine_smoke.py` / `tools/basegame_smoke.py` /
  `tools/classic_smoke.py` pass

See [`plans/two_repo_purity.md`](plans/two_repo_purity.md).
