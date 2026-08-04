"""
nest_ai -- generic named-AI dispatch for nest/den hostile spawning.

A "nest" (den, hive, whatever a game calls it) tops up under a cap by
picking one of a small set of named AI makers. Nothing here knows what
"vampire" or "corpse_eater" means -- a game registers a maker function per
id it uses in its own nest catalog; this module only owns the id -> maker
lookup.
"""

from __future__ import annotations

_makers: dict[str, object] = {}


def register_nest_ai(ai_id, make_fn):
    """Register a hostile-maker for a nest AI id.

    ``make_fn(game, room, spec) -> Character | None`` -- ``room`` is the
    den room being topped up, ``spec`` is that nest kind's catalog entry
    (whatever fields the game's own nest JSON declares). Idempotent --
    re-registering the same id overwrites its entry.
    """
    _makers[str(ai_id)] = make_fn


def known_nest_ai():
    """Frozen set of every registered AI id."""
    return frozenset(_makers)


def make_nest_hostile(ai_id, game, room, spec):
    """Dispatch to the registered maker for ``ai_id``.

    Returns ``None`` (does not raise) when ``ai_id`` has no registered
    maker -- callers that need "unknown ai" to be a hard error should
    check ``known_nest_ai()`` themselves (e.g. at catalog-load time).
    """
    fn = _makers.get(str(ai_id))
    if fn is None:
        return None
    return fn(game, room, spec)
