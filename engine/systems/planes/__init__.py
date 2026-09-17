"""
planes -- generic dimensional-plane vocabulary registry.

See ``registry.py``. ``engine/world.py``'s ``Room.plane`` (default
``"earth"``) is the per-room primitive this package's registry validates
against; ``Room.realm`` is the coarser family a plane belongs to.
"""

from __future__ import annotations

from engine.systems.planes.policy import (
    DEFAULT_CORPOREAL_PRISON_PLANE,
    is_corporeal_prison_plane,
    room_is_corporeal_prison,
)
from engine.systems.planes.registry import (
    is_registered,
    known_planes,
    plane_metadata,
    realm_for,
    register_plane,
    validate,
)

__all__ = [
    "DEFAULT_CORPOREAL_PRISON_PLANE",
    "is_corporeal_prison_plane",
    "is_registered",
    "known_planes",
    "plane_metadata",
    "realm_for",
    "register_plane",
    "room_is_corporeal_prison",
    "validate",
]
