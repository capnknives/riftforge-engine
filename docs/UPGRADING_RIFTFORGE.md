# Upgrading Riftforge from SUPERS

**Status:** Phase 6 done — SUPERS pins public **`capnknives/riftforge-engine`**
at **`@v0.1.1`**.

## Today (during the split)

Monorepo still runs unpackaged on Docker bind-mount. For packaging proof
on a workstation::

    pip install -e .              # riftforge / engine
    pip install -e ./supers       # requires riftforge already installed

After remotes exist, prefer the tagged pin below for clean ship; use
editable path for dual-checkout hacking.

## Tagged ship (clean)

1. Public `riftforge-engine`: land change, engine smoke, tag `vX.Y.Z`
   ([`RELEASING_RIFTFORGE.md`](RELEASING_RIFTFORGE.md)).
2. Private SUPERS (`capnknives/RiftForge`): set in `supers/pyproject.toml`::

       dependencies = [
           "riftforge @ git+https://github.com/capnknives/riftforge-engine.git@vX.Y.Z",
       ]

3. Run SUPERS `smoke_test.py`.
4. Merge to SUPERS `main` → live auto-deploy overlays → install new pin →
   game restart behind gateway ([`LIVE_DEPLOY.md`](LIVE_DEPLOY.md)).
5. Rollback: revert the pin commit on SUPERS.

## Local dual-checkout hacking (no tag yet)

1. Clone side by side, e.g. `D:\Claude\riftforge` (SUPERS) and
   `D:\Claude\riftforge-engine` (public engine).
2. In a venv: `pip install -e D:\Claude\riftforge-engine` then run SUPERS
   from the private tree (or keep monorepo unpackaged bind-mount as today).
3. **Docker dual-mount sketch** (optional while editing engine + game)::

       # docker-compose override example — adjust host paths
       services:
         riftforge:
           volumes:
             - D:/Claude/riftforge:/app
             - D:/Claude/riftforge-engine:/engine:ro
           environment:
             - RIFTFORGE_GATEWAY=1
             - PYTHONPATH=/engine:/app

   Today’s default remains a **single** SUPERS bind-mount (`.:/app`); the
   engine copy inside the monorepo is what live uses until you switch to
   the dual-mount or tagged-pin install path.
4. Edit either tree → watcher restarts **game only**; gateway holds clients.
5. Pin in `supers/pyproject.toml` stays at `@v0.1.1` until you cut a new tag.

See [`ENGINE_CONSUMER.md`](ENGINE_CONSUMER.md) and
[`plans/two_repo_purity.md`](plans/two_repo_purity.md).
