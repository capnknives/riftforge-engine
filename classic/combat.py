"""
combat.py -- classic active combat dispatcher (heartbeat + instant actions).

Uses the generic ``osr`` combat engine from ``engine.systems.combat_osr``
with classic resolvers registered at bootstrap. Heartbeat ``resolve_round``
mirrors basegame/SUPERS order-10 tick; ``resolve_instant_action`` is the
Active Override analogue for ``attack`` / ``cast``.
"""

from __future__ import annotations

from engine.systems import combat_engine
from engine.systems import combat_osr
from engine.display_prefs import paint_combat_line

# Re-export brief helpers for spell attacks and tests.
build_brief = combat_osr.build_brief
apply_brief = combat_osr.apply_brief
narrate = combat_osr.narrate


def _engine_id_for(attacker):
    """Resolve swing engine id; migrate legacy ``classic`` saves."""
    engine_id = getattr(attacker, "combat_engine", None) or "osr"
    if engine_id == "classic":
        return "osr"
    return engine_id


def _resolve_engine_swing(attacker, defender, game, *, rng=None):
    """One swing through the character's combat engine (default osr)."""
    engine_id = _engine_id_for(attacker)
    resolved = combat_engine.resolve_swing(
        engine_id, attacker, defender, game, rng=rng,
    )
    if resolved is None and engine_id != "osr":
        resolved = combat_engine.resolve_swing(
            "osr", attacker, defender, game, rng=rng,
        )
    return resolved


def _after_swing(resolved):
    """Classic post-apply: drop target lock when defender is down."""
    if not resolved:
        return
    brief = resolved.get("brief") or {}
    defender = brief.get("defender")
    if defender is None:
        return
    if float(getattr(defender, "hp", 0.0) or 0.0) <= 0:
        defender.target = None


def resolve_swing(attacker, defender, game=None, *, rng=None):
    """Public swing helper for tests."""
    resolved = _resolve_engine_swing(attacker, defender, game, rng=rng)
    if resolved is None:
        return {"outcome": "miss", "damage": 0.0, "text": None}
    _after_swing(resolved)
    return {
        "outcome": resolved["result"]["outcome"],
        "damage": resolved["result"]["damage"],
        "text": resolved.get("text"),
        "brief": resolved["brief"],
    }


def _broadcast_combat(game, attacker, text):
    """Room-visible combat line with per-viewer paint."""
    if not text or game is None:
        return
    room = getattr(attacker, "location", None)
    if room is None:
        if getattr(attacker, "session", None):
            attacker.session.send(text)
        return
    for watcher in room.characters():
        session = getattr(watcher, "session", None)
        if session is None:
            continue
        line = paint_combat_line(watcher, "room", text)
        session.send(line)


def resolve_instant_action(attacker, defender, game, *, rng=None):
    """Synchronous attack swing; stamps last_instant_action_tick."""
    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    resolved = _resolve_engine_swing(attacker, defender, game, rng=rng)
    if resolved is None:
        return None
    _after_swing(resolved)
    attacker.last_instant_action_tick = ticks
    attacker.target = defender
    text = resolved.get("text")
    _broadcast_combat(game, attacker, text)
    return resolved


def resolve_round(game, *, rng=None):
    """Advance every ongoing fight one heartbeat (skip instant-action tick)."""
    from engine.char_index import iter_characters

    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    for attacker in list(iter_characters(game)):
        if int(getattr(attacker, "last_instant_action_tick", -1) or -1) == ticks:
            continue
        target = getattr(attacker, "target", None)
        if target is None:
            continue
        if float(getattr(attacker, "hp", 0.0) or 0.0) <= 0:
            continue
        if float(getattr(target, "hp", 0.0) or 0.0) <= 0:
            attacker.target = None
            continue
        resolved = _resolve_engine_swing(attacker, target, game, rng=rng)
        if resolved:
            _after_swing(resolved)
        if resolved and resolved.get("text"):
            _broadcast_combat(game, attacker, resolved["text"])
