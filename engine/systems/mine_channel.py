"""
mine_channel.py -- shared per-face carve channel state and tick completion.

One progress pool per face; up to three joiners speed time-to-clear without
multiplying loot. Disconnect pauses with progress intact.
"""

from __future__ import annotations

import random

from engine import hooks
from engine.systems import mine_graph as graph_mod

# 3-5 heartbeats base channel (wall seconds converted at live scale).
CHANNEL_WALL_SECONDS_MIN = 12.0
CHANNEL_WALL_SECONDS_MAX = 20.0


def _channel_ticks(game, joiner_count, slow_mult=1.0):
    """Ticks until face clears for ``joiner_count`` workers."""
    from engine import game_clock_tuning as clock_mod
    base = random.uniform(CHANNEL_WALL_SECONDS_MIN, CHANNEL_WALL_SECONDS_MAX)
    # More joiners -> shorter wall time (not below 40% of solo).
    factor = max(0.4, 1.0 - 0.2 * max(0, joiner_count - 1))
    seconds = base * factor * float(slow_mult or 1.0)
    return max(1, clock_mod.ticks_for_wall_seconds(seconds, game))


def get_active_channel(face):
    """Return channel sub-dict on a face."""
    ch = face.setdefault("channel", {})
    ch.setdefault("joiners", [])
    ch.setdefault("progress", 0)
    ch.setdefault("progress_max", 100)
    return ch


def join_channel(game, mouth, from_room_id, direction, character):
    """Add character to face channel or start one. Returns (ok, message)."""
    from_room = graph_mod.room_by_id(mouth, from_room_id)
    if not from_room:
        return False, "You are not in a mine room."
    face = graph_mod.get_face(from_room, direction)
    if face.get("carved"):
        return False, f"That {direction} face is already cleared."
    if face.get("blocked_collapse"):
        return False, f"The {direction} passage is blocked by a collapse. Shore it first."

    ok, msg = graph_mod.can_carve_new_room(mouth, from_room)
    if not ok and not face.get("carve_hp", 0):
        return False, msg

    ch = get_active_channel(face)
    joiners = list(ch.get("joiners") or [])
    key = getattr(character, "key", None) or str(character)
    if key in joiners:
        return True, "You are already working that face."
    if len(joiners) >= graph_mod.MAX_CHANNEL_JOINERS:
        return False, "Too many people are already working that wall."

    tool_ok, slow_mult, tell = hooks.mine_tool_check(character, "carve")
    if not tool_ok:
        return False, tell or "You need a proper mining tool."
    if tell and character.location:
        character.location.broadcast(tell, exclude=None)

    joiners.append(key)
    ch["joiners"] = joiners
    ticks = _channel_ticks(game, len(joiners), slow_mult)
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    ch["ends_at_tick"] = now + ticks
    ch["progress_max"] = max(int(ch.get("progress_max") or 100), 1)
    graph_mod._touch_activity(game, mouth)
    game._mine_graphs_dirty = True
    return True, f"You set to work on the {direction} face."


def leave_channel(mouth, from_room_id, direction, character_key):
    """Remove one joiner; pause channel if empty."""
    from_room = graph_mod.room_by_id(mouth, from_room_id)
    if not from_room:
        return
    face = graph_mod.get_face(from_room, direction)
    ch = get_active_channel(face)
    joiners = [j for j in (ch.get("joiners") or []) if j != character_key]
    ch["joiners"] = joiners
    if not joiners:
        ch["ends_at_tick"] = None


def leave_all_channels_for_character(game, character_key):
    """Interrupt: drop character from every active channel."""
    state = graph_mod.ensure_game_state(game)
    for mouth in state.values():
        for room in (mouth.get("rooms") or {}).values():
            for direction, face in (room.get("exits") or {}).items():
                ch = face.get("channel") or {}
                if character_key in (ch.get("joiners") or []):
                    leave_channel(mouth, room["id"], direction, character_key)
    game._mine_graphs_dirty = True


def tick_mine_channels(game):
    """Advance all active channels; complete faces when timer elapses."""
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    state = graph_mod.ensure_game_state(game)
    for mouth_key, mouth in state.items():
        for room in (mouth.get("rooms") or {}).values():
            rid = room.get("id")
            for direction, face in list((room.get("exits") or {}).items()):
                ch = face.get("channel") or {}
                joiners = ch.get("joiners") or []
                if not joiners:
                    continue
                ends = ch.get("ends_at_tick")
                if ends is None or now < int(ends):
                    continue
                # Complete carve on this face.
                progress = int(ch.get("progress") or 0) + max(1, 100 // len(joiners))
                ch["progress"] = progress
                if progress < int(ch.get("progress_max") or 100):
                    ch["ends_at_tick"] = now + _channel_ticks(game, len(joiners))
                    continue
                miner = joiners[0]
                dest, _msg = graph_mod.complete_carve(
                    game, mouth_key, mouth, rid, direction, miner,
                )
                ch["joiners"] = []
                ch["progress"] = 0
                ch["ends_at_tick"] = None
                face["carve_hp"] = face.get("carve_hp_max", 100)
                if dest:
                    from engine.systems import mine_rooms as rooms_mod
                    rooms_mod.get_mine_room(game, mouth_key, dest["id"])
    game._mine_graphs_dirty = True
