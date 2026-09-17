"""
policy.py -- corporeal-prison plane id (folklore peel Wave 5c).

The engine knows *which* plane id treats intruders and natives as punchable
flesh while souls routed elsewhere stay visitors. Live SUPERS still owns
Leviathan identity, map content, and desk routing -- this module only
answers the generic plane predicate via ``engine.hooks``.
"""

from __future__ import annotations

# Live plane id; games may override via ``set_is_corporeal_prison_plane``.
DEFAULT_CORPOREAL_PRISON_PLANE = "purgatory"


def is_corporeal_prison_plane(plane) -> bool:
    """True when ``plane`` is the corporeal-prison afterlife id."""
    from engine import hooks

    return hooks.is_corporeal_prison_plane(plane)


def room_is_corporeal_prison(room) -> bool:
    """True when the room's ``plane`` field is the corporeal-prison id."""
    if room is None:
        return False
    return is_corporeal_prison_plane(getattr(room, "plane", None) or "")
