"""
studio_bridge -- generic hooks for content-authoring tools (Area Studio,
``tools/content_new.py``) that write catalog JSON straight to disk and
need to invalidate a game's in-memory cache afterward.

Nothing here knows what "bestiary" or "nests" means -- a game registers a
zero-arg reload callback per content key it wants Studio-editable; this
module only owns the key -> callback dispatch. Kind-profile validation
itself (``apply_template``, ``validate_kind``, ...) already lives in
``engine.content_kinds.engine`` and needs no bridge -- tools should import
it from there directly rather than through a game's re-export.
"""

from __future__ import annotations

_reload_hooks: dict[str, object] = {}


def register_content_reload(key, reload_fn):
    """Register a zero-arg callback that invalidates/rebuilds a game's
    in-memory cache for one content key (e.g. ``"bestiary"``, ``"nests"``).

    Idempotent -- re-registering the same key overwrites its entry.
    """
    _reload_hooks[str(key)] = reload_fn


def known_content_keys():
    """Frozen set of every registered content key."""
    return frozenset(_reload_hooks)


def reload_content(key):
    """Call the registered reload callback for ``key``, if any.

    Silently no-ops for an unregistered key -- a game that never
    registered a given key has nothing to invalidate, and a content-
    authoring tool writing a file no game cares to cache should not have
    to know that in advance.
    """
    fn = _reload_hooks.get(str(key))
    if fn is not None:
        fn()
