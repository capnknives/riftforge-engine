# Releasing Riftforge (public engine)

**Status:** Phase 5 — public remote **`capnknives/riftforge-engine`**.

## Cut a release

1. Land engine-only changes on `riftforge-engine` `main` (or export from
   the monorepo via `python tools/export_public_engine.py` and push).
2. In that tree: `pip install -e .` then
   `python tools/engine_smoke.py` (no `supers/` present).
3. Tag `vX.Y.Z` (semver; breaking hook API = major)::

       git tag -a v0.1.0 -m "riftforge-engine v0.1.0"
       git push origin v0.1.0

4. Announce in the engine CHANGELOG; never ship SUPERS content.

## Purity checklist before a tag

- No `supers` imports under the `engine` package
- No `content/npcs`, Origins catalogs, or SUPERS help pages
- Demo map only under `content/maps/` (export tool writes `demo.json`)
- `tools/engine_smoke.py` / `tools/packaging_smoke.py` pass

See [`plans/two_repo_purity.md`](plans/two_repo_purity.md).
