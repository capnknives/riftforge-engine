# Upgrading Riftforge from SUPERS

**Status:** Phase 5 — SUPERS pins public **`capnknives/riftforge-engine`**.

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

1. Clone `riftforge-engine` and private SUPERS side by side.
2. In the SUPERS venv/container: `pip install -e ../riftforge-engine`
3. Docker bind-mounts **both** trees; `watch_and_run` watches game +
   optionally the engine mount.
4. Edit either → game restart (gateway holds clients). Pin stays until
   you cut a tag.

See [`ENGINE_CONSUMER.md`](ENGINE_CONSUMER.md) and
[`plans/two_repo_purity.md`](plans/two_repo_purity.md).
