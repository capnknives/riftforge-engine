"""
lean_boot.py -- point the shared map loader at the one-room lean demo.

When ``RIFTFORGE_GAME=none`` (or auto with SUPERS absent), the monorepo
still has a full ``content/maps/*.json`` tree. Without this redirect,
``build_world()`` loads ~12k rooms and the lean story is incomplete.
Public export already ships a lone ``demo.json``; this module makes the
private tree match that behavior for ``none``.

Canonical map: ``engine/demo/content/maps/demo.json`` (not under the
SUPERS ``content/maps/`` glob — that would pollute live boots).

Called from ``game_select._resolve()`` when the active game is ``none``.
Basegame / SUPERS set their own maps dirs in bootstrap instead.
"""

from __future__ import annotations

import os


def lean_maps_dir():
    """Absolute path to the canonical lean demo maps directory."""
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "demo",
        "content",
        "maps",
    )


def configure_lean_maps():
    """Redirect maps.py to the one-file lean demo set (demo.json only)."""
    import maps

    lean_dir = lean_maps_dir()
    if not os.path.isdir(lean_dir):
        raise FileNotFoundError(
            f"lean demo maps missing: {lean_dir} "
            "(expected engine/demo/content/maps/demo.json)"
        )
    maps.set_maps_dir(lean_dir)
    # Empty zones — lean demo has no pocket JSON.
    empty_zones = os.path.join(
        os.path.dirname(lean_dir), "zones"
    )
    os.makedirs(empty_zones, exist_ok=True)
    maps.set_zones_dir(empty_zones)
