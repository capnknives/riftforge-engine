"""
breach.py -- generic slam/throw wall-floor breach shell.

Uses ``engine.systems.room_structure`` for HP state and
``maps.find_room_by_layout_direction`` for eject targets. Orthogonal to
the combat-engine plugin registry (Phase 1).

Stdlib only; zero ``supers`` imports.
"""

from __future__ import annotations

import random

from engine.systems import room_structure


def _breachable_targets(room):
    """Return slam_targets entries that have id + direction (non-shop)."""
    out = []
    for entry in getattr(room, "slam_targets", None) or []:
        if not isinstance(entry, dict):
            continue
        if not entry.get("id") or not entry.get("direction"):
            continue
        tags = entry.get("tags") or []
        if "shop_fixture" in tags:
            continue
        out.append(entry)
    return out


def pick_slam_target(room, *, rng=None):
    """Choose a breachable ``slam_targets`` entry, or ``None``."""
    targets = _breachable_targets(room)
    if not targets:
        return None
    if rng is None:
        rng = random.random
    roll = rng()
    idx = int(roll * len(targets)) % len(targets)
    return dict(targets[idx])


def find_slam_target(room, name_fragment):
    """Match a slam target id/label fragment (case-insensitive)."""
    needle = str(name_fragment or "").strip().lower()
    if not needle:
        return None
    for entry in _breachable_targets(room):
        prop_id = str(entry.get("id") or "").lower()
        label = str(entry.get("label") or "").lower()
        if needle in prop_id or needle in label:
            return entry
    return None


def apply_slam_damage(game, room, target_id, damage):
    """Chip structural HP for ``target_id``; return result dict."""
    entry = None
    for candidate in getattr(room, "slam_targets", None) or []:
        if candidate.get("id") == target_id:
            entry = candidate
            break
    if entry is None:
        return {"ok": False, "reason": "unknown target"}
    hp_max = int(entry.get("hp_max") or 10)
    current = room_structure.get_wall_state(
        game, room, target_id, default_hp_max=hp_max,
    )
    dmg = max(0, int(damage))
    new_hp = max(0, current["hp"] - dmg)
    state = room_structure.set_wall_state(
        game, room, target_id, hp=new_hp,
    )
    return {
        "ok": True,
        "target_id": target_id,
        "label": entry.get("label") or target_id,
        "direction": entry.get("direction"),
        "damage": dmg,
        "hp": state["hp"],
        "hp_max": hp_max,
        "wrecked": bool(state["wrecked"]),
    }


def breach_eject(character, room, target, *, game=None):
    """Move ``character`` through ``target``'s layout direction neighbor."""
    if character is None or room is None or target is None or game is None:
        return False
    direction = target.get("direction")
    if not direction:
        return False
    import maps

    neighbor = maps.find_room_by_layout_direction(
        getattr(game, "rooms", None) or {}, room, direction,
    )
    if neighbor is None:
        return False
    old_room = room
    mover = getattr(character, "move_to", None)
    if callable(mover):
        mover(neighbor)
    else:
        character.location = neighbor
    from engine.systems import combat_pursuit as combat_pursuit_mod
    combat_pursuit_mod.notify_character_relocated(
        character, old_room, neighbor, game,
    )
    label = target.get("label") or target.get("id") or "the wall"
    if old_room is not None and hasattr(old_room, "broadcast"):
        old_room.broadcast(
            f"{getattr(character, 'key', 'Someone')} crashes through {label}.",
            exclude=character,
        )
    if hasattr(neighbor, "broadcast"):
        neighbor.broadcast(
            f"{getattr(character, 'key', 'Someone')} tumbles in through {label}.",
            exclude=character,
        )
    session = getattr(character, "session", None)
    if session is not None:
        session.send(f"You burst through {label} into {neighbor.key}.")
    return True
