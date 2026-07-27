# Releasing Riftforge (public engine)

**Status:** Phase 6 remotes **done**; Phase 7 framework peels **done**.
Public remote **`capnknives/riftforge-engine`**. Current SUPERS pin:
**`v0.2.0`** (Phase 7 engines: stats, needs, economy, pathfind,
combat_core, mail/socials/wearables, Stage G hooks).

## Cut a release

1. Land engine-only changes on `riftforge-engine` `main` (or export from
   the monorepo via `python tools/export_public_engine.py` and push).
2. In that tree: `pip install -e .` then
   `python tools/engine_smoke.py` (no `supers/` present) and
   `python tools/basegame_smoke.py`.
3. Tag `vX.Y.Z` (semver; breaking hook API = major)::

       git tag -a v0.2.0 -m "riftforge-engine v0.2.0 — Phase 7 frameworks"
       git push origin v0.2.0

   Prefer **`v0.2.0`** over older `v0.1.x` tags for new consumers.
   Re-exports via `tools/export_public_engine.py` ignore `__pycache__` /
   `*.pyc` and rewrite a lean `help_topics.py` stub + public README +
   `.github/workflows/ci.yml`.

4. Announce in the engine CHANGELOG / commit message; never ship SUPERS
   content. Bump `supers/pyproject.toml` on the private monorepo to the
   new tag ([`UPGRADING_RIFTFORGE.md`](UPGRADING_RIFTFORGE.md)).

## Purity checklist before a tag

- No `supers` imports under the `engine` package
- No `content/npcs`, Origins catalogs, or SUPERS help pages
- Demo map only under `content/maps/` (export tool writes `demo.json`)
- `tools/engine_smoke.py` / `tools/basegame_smoke.py` pass

See [`plans/two_repo_purity.md`](plans/two_repo_purity.md).
