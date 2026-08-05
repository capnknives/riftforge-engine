"""
engine/__main__.py -- engine entry: ``python -m engine``.

When ``RIFTFORGE_GAME`` is unset, boots the shipped MVP demo (``basegame``
when that package is present — Notbigville, jobs, weather, atlas travel).
Falls back to the one-room lean map only when ``basegame/`` is absent (CI
``engine-only-smoke`` still forces ``RIFTFORGE_GAME=none`` explicitly).

Never auto-picks ``supers`` from the monorepo; use ``python server.py`` or
``RIFTFORGE_GAME=supers`` for the full game.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os


def default_entry_game():
    """Built-in demo game for ``python -m engine`` when env is unset."""
    if importlib.util.find_spec("basegame") is not None:
        return "basegame"
    return "none"


def main():
    """Launch the engine with the built-in MVP demo (or lean fallback)."""
    if not (os.environ.get("RIFTFORGE_GAME") or "").strip():
        os.environ["RIFTFORGE_GAME"] = default_entry_game()
    from server import main as server_main

    try:
        asyncio.run(server_main())
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
