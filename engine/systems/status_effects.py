"""status_effects.py -- timed stat modifiers on characters.

Each effect is a plain dict on ``character.status_effects``:
``{stat, delta, expires_tick}``. ``apply_status`` appends; ``tick`` walks
every character and drops expired rows, reverting modifiers automatically
via expiry (no bespoke per-effect timers).

Register ``tick`` on ``engine.tick_registry`` from the game's bootstrap.

stdlib only.
"""

from __future__ import annotations


def _effects(character):
    """Mutable list of active status rows (creates empty if missing)."""
    if character is None:
        return []
    effects = getattr(character, "status_effects", None)
    if effects is None or not isinstance(effects, list):
        character.status_effects = []
        return character.status_effects
    return effects


def _now_tick(game):
    return int(getattr(game, "game_time_ticks", 0) or 0)


def apply_status(character, stat_name, delta, duration_ticks, *, game=None):
    """Apply a timed stat modifier.

    ``stat_name`` is a string key (e.g. ``"str"``). ``delta`` is added to
    effective stat lookups until ``expires_tick``. Returns the new effect row.
  """
    if character is None:
        return None
    stat = (stat_name or "").strip().lower()
    if not stat:
        return None
    try:
        delta_f = float(delta)
    except (TypeError, ValueError):
        return None
    try:
        duration = max(1, int(duration_ticks))
    except (TypeError, ValueError):
        duration = 1
    now = _now_tick(game)
    row = {
        "stat": stat,
        "delta": delta_f,
        "expires_tick": now + duration,
    }
    _effects(character).append(row)
    return row


def stat_modifier(character, stat_name, *, game=None):
    """Sum of active deltas for ``stat_name`` (expired rows ignored)."""
    if character is None:
        return 0.0
    stat = (stat_name or "").strip().lower()
    if not stat:
        return 0.0
    now = _now_tick(game)
    total = 0.0
    for row in _effects(character):
        if (row.get("stat") or "").lower() != stat:
            continue
        expires = row.get("expires_tick")
        try:
            if expires is not None and int(expires) <= now:
                continue
        except (TypeError, ValueError):
            continue
        try:
            total += float(row.get("delta", 0) or 0)
        except (TypeError, ValueError):
            pass
    return total


def active_effects(character, *, game=None):
    """Non-expired effect rows (shallow copies)."""
    if character is None:
        return []
    now = _now_tick(game)
    out = []
    for row in _effects(character):
        expires = row.get("expires_tick")
        try:
            if expires is not None and int(expires) <= now:
                continue
        except (TypeError, ValueError):
            continue
        out.append(dict(row))
    return out


def clear_status(character, stat_name=None):
    """Drop all effects, or only those targeting ``stat_name``."""
    if character is None:
        return 0
    effects = _effects(character)
    if stat_name is None:
        n = len(effects)
        effects.clear()
        return n
    stat = stat_name.strip().lower()
    kept = [r for r in effects if (r.get("stat") or "").lower() != stat]
    removed = len(effects) - len(kept)
    character.status_effects = kept
    return removed


def tick(game):
    """Expire finished effects (register on ``tick_registry``)."""
    from engine.char_index import iter_characters

    now = _now_tick(game)
    for character in iter_characters(game):
        effects = _effects(character)
        if not effects:
            continue
        kept = []
        for row in effects:
            expires = row.get("expires_tick")
            try:
                if expires is not None and int(expires) <= now:
                    continue
            except (TypeError, ValueError):
                pass
            kept.append(row)
        if len(kept) != len(effects):
            character.status_effects = kept


def register_tick(game):
    """Wire ``tick`` onto ``game`` via ``engine.tick_registry``."""
    from engine.tick_registry import register_tick

    register_tick(game, tick, order=84, name="status_effects")
