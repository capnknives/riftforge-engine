"""
active_combat_demo.py -- basegame consumer of engine active combat.

Wires the tick drain, broadcasts compressed prose to the room, and leaves
SUPERS completely untouched. Rooms or NPCs opt in with ``active_combat=True``.
"""

from __future__ import annotations

from engine.systems import active_combat as ac


def tick(game, *, now_fn=None, rng=None):
    """Drain twitch buffers and broadcast tag-first combat lines."""
    lines = ac.tick_active_combat(game, now_fn=now_fn, rng=rng)
    for entry in lines:
        text = entry.get("text")
        if not text:
            continue
        attacker = entry.get("attacker")
        room = getattr(attacker, "location", None) if attacker else None
        if room is None:
            defender = entry.get("defender")
            room = getattr(defender, "location", None) if defender else None
        if room is None:
            continue
        # Prefer room.broadcast when present; fall back to per-session send.
        broadcast = getattr(room, "broadcast", None)
        if callable(broadcast):
            broadcast(text)
            continue
        for other in getattr(room, "contents", []) or []:
            session = getattr(other, "session", None)
            if session is not None:
                session.send(text)
