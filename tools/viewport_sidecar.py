"""Local isolated Game() + Ash viewport (no telnet, no live DB).

Binds loopback :4002 **before** Game() returns so hello answers with
booting=true during boot_migrations. Does **not** bind :4000. Uses a
fresh sqlite file per process (not riftforge.db, not a sticky sidecar DB).
After Game() is ready, mints playtest account Ash (head_gm) so occupy
does not need a manual ``seed_crashgate_agent_account`` pass.

Game pick: ``RIFTFORGE_GAME`` wins when set. Unset, the sidecar boots
SUPERS when that package is on disk (private monorepo), else ``basegame``
(public engine tree), else the lean one-room engine. Games that do not
author an Ash body (basegame / classic / lean) get a throwaway playtest
body minted in the start room -- sidecar only, never a live Game.

    py -3.13 tools/viewport_sidecar.py
    RIFTFORGE_GAME=classic python tools/viewport_sidecar.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)



def _default_game() -> str:
    """Which game the sidecar boots when RIFTFORGE_GAME is unset.

    The private monorepo keeps its old default (SUPERS). The public
    engine tree has no ``supers/``, so fall to the basegame demo, then to
    the lean engine. Checking the directory (not importing) keeps the
    one-game-per-process rule: nothing is imported until game_select runs.
    """
    for name in ("supers", "basegame"):
        if os.path.isfile(os.path.join(_ROOT, name, "__init__.py")):
            return name
    return "none"


os.environ.setdefault("RIFTFORGE_GAME", _default_game())
os.environ["RIFTFORGE_VIEWPORT"] = "1"
os.environ["RIFTFORGE_VIEWPORT_SAFETIES"] = "off"
os.environ["RIFTFORGE_GATEWAY"] = "0"


def _sidecar_db_path() -> str:
    pinned = os.environ.get("RIFTFORGE_VIEWPORT_DB", "").strip()
    if pinned:
        return pinned
    handle, path = tempfile.mkstemp(
        prefix="riftforge_viewport_", suffix=".db",
    )
    os.close(handle)
    return path


def _ensure_playtest_body(game, hub, account) -> None:
    """Mint a throwaway playtest body when this game authors none.

    SUPERS spawns an immersion Ash, so this is a no-op there. basegame,
    classic, and the lean engine have no such fixture: without a body,
    ``op=open`` answered "No character named 'Ash'". The body lives only
    in this process's temp sqlite. Linking it to the playtest staff
    account is what lets ``gm on`` work through the viewport.
    """
    from engine import viewport as viewport_mod

    name = viewport_mod.DEFAULT_CHARACTER
    if hub.resolve_body(name) is not None:
        return
    from engine.world import Character

    body = Character(name, "A playtest body the viewport harness rides.")
    start = getattr(game, "start_room", None)
    if start is None:
        rooms = getattr(game, "rooms", None) or {}
        start = next(iter(rooms.values()), None)
    if start is None:
        print("[viewport-sidecar] no room to place the playtest body", flush=True)
        return
    body.move_to(start)
    if account is not None:
        from engine import accounts as accounts_mod

        err = accounts_mod.link_character(game, account, body)
        if err:
            print(f"[viewport-sidecar] playtest body not linked: {err}", flush=True)
    print(f"[viewport-sidecar] minted playtest body {name!r}", flush=True)


async def _run() -> None:
    from engine import viewport as viewport_mod
    from engine.viewport import ViewportHub
    from server import Game

    db_path = _sidecar_db_path()
    print(f"[viewport-sidecar] booting Game() db={db_path}", flush=True)
    hub = ViewportHub(None)
    hub.booting = True
    server = await viewport_mod.bind_viewport_server(hub)
    print("[viewport-sidecar] listen up (Game still booting)", flush=True)

    game = Game(db_path=db_path)
    hub.game = game
    game.viewport_hub = hub
    from engine.accounts import ensure_playtest_staff_account

    account = ensure_playtest_staff_account(game)
    _ensure_playtest_body(game, hub, account)
    hub.booting = False
    asyncio.create_task(game.tick_loop())
    print("[viewport-sidecar] Game ready", flush=True)
    async with server:
        await server.serve_forever()


def main() -> int:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
