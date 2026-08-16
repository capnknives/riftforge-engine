"""
mine_ambience.py -- heartbeat ambient lines for occupied virtual mine rooms.

Rolls a low per-tick chance for each occupied ``virtual_mine`` room and
broadcasts one line from the ``pick_mine_ambient_line`` hook (SUPERS backs
the pool). Darkness does not block sound -- occupants can hear drips and
creaks in the dark.

Pure logic; no networking.
"""

from __future__ import annotations

import random

from engine import hooks

# Same order of magnitude as supers/reach_ambience.BLEED_ROLL_CHANCE_PER_TICK:
# routine atmosphere, not spam every heartbeat.
AMBIENCE_ROLL_CHANCE_PER_TICK = 0.002

# Minimum wait between beats in the same room (~90s at default clock scale).
ROOM_COOLDOWN_SECONDS = 90.0


def _room_cooldown_ready(room, game):
    tick = int(getattr(game, "game_time_ticks", 0) or 0)
    last = int(getattr(room, "_mine_ambience_last_tick", 0) or 0)
    from engine import game_clock_tuning as clock_mod
    cooldown = clock_mod.ticks_for_wall_seconds(ROOM_COOLDOWN_SECONDS, game)
    return (tick - last) >= cooldown


def _stamp_room_cooldown(room, game):
    room._mine_ambience_last_tick = int(getattr(game, "game_time_ticks", 0) or 0)


def _iter_occupied_mine_rooms(game):
    """Yield (room, occupants) for virtual mine rooms with at least one body."""
    from world import Character

    store = getattr(game, "mine_rooms", None) or {}
    for room in store.values():
        if room is None or not getattr(room, "virtual_mine", False):
            continue
        occupants = [
            obj for obj in getattr(room, "contents", []) or []
            if isinstance(obj, Character)
        ]
        if occupants:
            yield room, occupants


def tick(game):
    """Heartbeat: maybe broadcast one mine ambience line in one occupied room."""
    candidates = []
    for room, occupants in _iter_occupied_mine_rooms(game):
        if not occupants:
            continue
        if not _room_cooldown_ready(room, game):
            continue
        if random.random() >= AMBIENCE_ROLL_CHANCE_PER_TICK:
            continue
        candidates.append(room)
    if not candidates:
        return
    room = random.choice(candidates)
    line = hooks.pick_mine_ambient_line(room, game, rng=random)
    if not line:
        return
    room.broadcast(str(line), exclude=None)
    _stamp_room_cooldown(room, game)
