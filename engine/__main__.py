"""
engine/__main__.py -- lean engine entry: ``python -m engine``.

Forces ``RIFTFORGE_GAME=none`` when unset so a casual launch does not
auto-pick SUPERS from the monorepo. Then runs the shared ``server.main``
tick/telnet loop (same shape as ``python -m supers``).
"""

from __future__ import annotations

import asyncio
import os


def main():
    """Launch a lean (no game package) engine process."""
    if not (os.environ.get("RIFTFORGE_GAME") or "").strip():
        os.environ["RIFTFORGE_GAME"] = "none"
    from server import main as server_main

    try:
        asyncio.run(server_main())
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
