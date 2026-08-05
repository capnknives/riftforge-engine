"""
combat_runtime.py -- game-level combat backend loader and router.

Two *backends* sit above the per-style swing engines in
``combat_engine.py`` (``mundane``, ``martial_arts``, …) and the per-style
active engines in ``active_combat.py`` (``kinetic``, …):

  * ``swing`` -- heartbeat ``resolve_round`` / per-swing briefs
    (``fight.combat_mode == "narrative"``).
  * ``active_combat`` -- timestamp-buffered twitch queues + telegraphs
    (``fight.combat_mode == "active"``).

Games register backends with ``register_combat_backend``, then call
``load_combat_backend("<id>")`` at bootstrap. ``set_game_combat_backend``
picks the default for new fights; room / NPC ``active_combat`` flags still
override for arena pockets.

Basegame wires both backends in ``basegame/combat_backends.py``.

Stdlib only. Zero ``supers`` imports.
"""

from __future__ import annotations

from engine.systems import fight as fight_mod

# Public backend ids.
BACKEND_SWING = "swing"
BACKEND_ACTIVE = "active_combat"

GAME_BACKEND_ATTR = "combat_backend"

_default_backend_id = BACKEND_SWING

# backend_id -> {"load": fn, "tick": fn, "fight_mode": str, "label": str}
_BACKENDS: dict[str, dict] = {}
_loaded: set[str] = set()


def register_combat_backend(backend_id, *, load_fn, tick_fn, fight_mode, label):
    """Register a combat backend (idempotent overwrite)."""
    _BACKENDS[str(backend_id)] = {
        "load": load_fn,
        "tick": tick_fn,
        "fight_mode": str(fight_mode),
        "label": str(label),
    }


def known_combat_backends():
    """Every registered backend id (loaded or not)."""
    return frozenset(_BACKENDS)


def loaded_combat_backends():
    """Backend ids that have had ``load_combat_backend`` called."""
    return frozenset(_loaded)


def load_combat_backend(backend_id):
    """Load ``backend_id``: run its ``load_fn`` once and mark it loaded.

    Returns ``(True, None)`` on success, ``(False, reason)`` on failure.
    Idempotent.
    """
    entry = _BACKENDS.get(str(backend_id))
    if entry is None:
        known = ", ".join(sorted(_BACKENDS)) or "(none registered)"
        return False, f"Unknown combat backend {backend_id!r}. Known: {known}."
    if backend_id in _loaded:
        return True, None
    entry["load"]()
    _loaded.add(str(backend_id))
    return True, None


def set_default_combat_backend(backend_id):
    """Set the module-level default used before / without a Game instance."""
    global _default_backend_id
    if str(backend_id) not in _BACKENDS:
        raise ValueError(f"unknown combat backend {backend_id!r}")
    _default_backend_id = str(backend_id)


def get_default_combat_backend():
    """Module-level default backend id."""
    return _default_backend_id


def set_game_combat_backend(game, backend_id):
    """Pin ``game``'s default combat backend for new engagements."""
    bid = str(backend_id)
    if bid not in _BACKENDS:
        raise ValueError(f"unknown combat backend {backend_id!r}")
    setattr(game, GAME_BACKEND_ATTR, bid)


def get_game_combat_backend(game):
    """Return ``game``'s backend id, else the module default."""
    if game is None:
        return _default_backend_id
    return str(getattr(game, GAME_BACKEND_ATTR, None) or _default_backend_id)


def ensure_game_combat_backend(game, *, default=None):
    """Stamp ``game.combat_backend`` if missing (idempotent)."""
    if game is None:
        return
    if not hasattr(game, GAME_BACKEND_ATTR):
        setattr(game, GAME_BACKEND_ATTR, str(default or _default_backend_id))


def backend_for_fight_mode(fight_mode):
    """Map a Fight.combat_mode string back to a backend id, or None."""
    for bid, entry in _BACKENDS.items():
        if entry.get("fight_mode") == str(fight_mode):
            return bid
    return None


def fight_mode_for_backend(backend_id):
    """Return the Fight.combat_mode string for ``backend_id``."""
    entry = _BACKENDS.get(str(backend_id))
    if entry is None:
        return fight_mod.MODE_NARRATIVE
    return entry.get("fight_mode", fight_mod.MODE_NARRATIVE)


def resolve_engagement_fight_mode(game=None, *, room=None, target=None):
    """Pick ``fight.combat_mode`` for a new engagement.

    Priority (highest wins):
      1. ``room.active_combat`` or ``target.active_combat`` -> active
      2. ``game.combat_backend`` (or module default) -> that backend's mode
    """
    if room is not None and bool(getattr(room, "active_combat", False)):
        return fight_mod.MODE_ACTIVE
    if target is not None and bool(getattr(target, "active_combat", False)):
        return fight_mod.MODE_ACTIVE
    backend_id = get_game_combat_backend(game)
    return fight_mode_for_backend(backend_id)


def engagement_uses_active_combat(game=None, *, room=None, target=None):
    """True when a new fight in this context should use active combat."""
    return (
        resolve_engagement_fight_mode(game, room=room, target=target)
        == fight_mod.MODE_ACTIVE
    )


def tick(game):
    """Run every *loaded* backend's heartbeat handler in registration order."""
    for backend_id in _BACKENDS:
        if backend_id not in _loaded:
            continue
        _BACKENDS[backend_id]["tick"](game)


def describe_backends():
    """Human-readable lines for help / ``loadcombat list``."""
    lines = []
    for bid in sorted(_BACKENDS):
        entry = _BACKENDS[bid]
        loaded = "loaded" if bid in _loaded else "not loaded"
        lines.append(f"  {bid} ({loaded}) -- {entry.get('label', bid)}")
    return lines


def reset_for_tests():
    """Clear loaded set only -- registry entries stay (smoke/tests)."""
    _loaded.clear()
