"""
town_roads.py -- shared road + sewer dig helpers for township and townforge.

Centralizes grate-every-N-tiles policy and opposite-direction lookup so
player ``township dig`` and staff spine generation stay aligned.
"""

from __future__ import annotations

OPPOSITE = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
    "northeast": "southwest",
    "southwest": "northeast",
    "northwest": "southeast",
    "southeast": "northwest",
    "up": "down",
    "down": "up",
    "in": "out",
    "out": "in",
}

# Default grate interval for surface road tiles (township + townforge).
DEFAULT_GRATE_EVERY_N = 3


def opposite(direction):
    """Return the paired compass exit name, or None."""
    return OPPOSITE.get((direction or "").strip().lower())


def should_stamp_grate(tile_index, every_n=DEFAULT_GRATE_EVERY_N):
    """True when this road tile index should expose a down grate to sewer."""
    if every_n <= 0:
        return False
    return int(tile_index) % int(every_n) == 0


def grate_description_suffix():
    """Prose fragment appended when a grate is present."""
    return " A rusted grate in the pavement opens down."
