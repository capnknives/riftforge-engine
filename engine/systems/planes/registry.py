"""
registry -- generic plane/realm vocabulary a game builds at boot.

``engine/world.py``'s ``Room.plane`` (default ``"earth"``) and ``Room.realm``
(default ``"prime"``) are already plain string fields on every Room -- this
module only adds the missing piece: a place to declare which plane ids a
game actually uses, what realm family each belongs to, and (optionally) any
display metadata a generic tool (minimap legend, ``list planes``, Area
Studio) wants without needing to know a single game's lore.

Nothing here is game-specific. ``"earth"`` is pre-registered under realm
``"prime"`` because it is ``Room``'s own default -- a bare engine boot with
zero game hooks must still validate a plain, unauthored Room. Every other
plane id (SUPERS' ``hell``/``heaven``/``purgatory``/... or basegame's
elemental demo set) is the registering game's choice, made at boot before
any map JSON referencing it loads (mirrors how every other ``engine.hooks``
registration must run before ``build_world()``).

This registry does not replace root ``maps.py``'s ``PLANES``/
``REALM_FOR_PLANE`` yet -- those still gate SUPERS' own map loader unchanged
(zero behavior change, zero live risk). Wiring ``maps.py`` to validate
against this registry instead of its own hardcoded set is a follow-up, once
a second real consumer (this phase's basegame demo) has proven the shape.
"""

from __future__ import annotations

_planes: dict[str, dict] = {}


def register_plane(plane_id, realm, **metadata):
    """Register ``plane_id`` under realm family ``realm``.

    Idempotent -- re-registering the same id overwrites its entry (a game
    re-running boot, or a hot-reload, does not need to guard this itself).
    ``**metadata`` is opaque passthrough (e.g. ``label=``, ``description=``,
    ``color=``) for generic listing/rendering tools; nothing here reads it.
    """
    plane_id = str(plane_id)
    realm = str(realm)
    _planes[plane_id] = {"realm": realm, **metadata}


def is_registered(plane_id):
    """True when ``plane_id`` has been registered."""
    return plane_id in _planes


def known_planes():
    """Frozen snapshot of every registered plane id."""
    return frozenset(_planes)


def realm_for(plane_id, default="prime"):
    """Registered realm for ``plane_id``, or ``default`` if unregistered."""
    entry = _planes.get(plane_id)
    return entry["realm"] if entry is not None else default


def plane_metadata(plane_id):
    """Copy of ``plane_id``'s registered metadata (including ``realm``).

    Empty dict for an unregistered id -- callers checking existence should
    use ``is_registered`` first, not an empty-dict falsy check (a
    registered plane with no extra metadata is also just ``{"realm": ...}``,
    never empty).
    """
    return dict(_planes.get(plane_id, {}))


def validate(plane_id, *, realm=None):
    """Raise ``ValueError`` unless ``plane_id`` is registered.

    If ``realm`` is given, it must match the registered realm. Returns the
    registered realm on success (mirrors ``maps.py``'s existing
    ``_resolve_plane_and_realm`` contract, so that loader can delegate here
    without changing its error shape).
    """
    entry = _planes.get(plane_id)
    if entry is None:
        raise ValueError(
            f"unknown plane {plane_id!r} -- must be one of "
            f"{sorted(_planes)}"
        )
    expected = entry["realm"]
    if realm is not None and realm != expected:
        raise ValueError(
            f"realm {realm!r} does not match plane {plane_id!r} "
            f"(expected {expected!r})"
        )
    return expected


def reset():
    """Clear every registration except the built-in ``earth``/``prime``.

    Test-only -- production boots never need to call this (registration is
    idempotent; see ``register_plane``).
    """
    _planes.clear()
    _seed_defaults()


def _seed_defaults():
    register_plane("earth", "prime")


_seed_defaults()
