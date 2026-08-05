"""
grapple.py -- hold, throw, and slam grabbed targets in active combat.

Works with ``active_combat`` telegraphs for the initial ``grab`` strike.
``throw`` / ``slam`` on a held body resolve immediately (no FIFO) once
the holder has someone in a grapple bond.

Stdlib only; zero ``supers`` imports.
"""

from __future__ import annotations

from engine.systems import breach as breach_mod
from engine.systems import readiness as readiness_mod

# Character attrs (composition -- never subclasses).
HOLDER_ATTR = "grapple_victim"   # who this character is holding
HELD_BY_ATTR = "grapple_holder"   # who holds this character

THROW_BALANCE_COST = 2.2
SLAM_BALANCE_COST = 2.6
THROW_DAMAGE = 6.0
SLAM_DAMAGE = 10.0


def _name(character):
    return (
        getattr(character, "key", None)
        or getattr(character, "name", None)
        or "someone"
    )


def get_held_victim(holder):
    """Return the Character ``holder`` is grappling, or None."""
    victim = getattr(holder, HOLDER_ATTR, None)
    if victim is None:
        return None
    if getattr(victim, HELD_BY_ATTR, None) is not holder:
        return None
    if getattr(victim, "location", None) is not getattr(holder, "location", None):
        break_hold(holder, victim)
        return None
    return victim


def is_held(character):
    """True when ``character`` is currently held by another body."""
    holder = getattr(character, HELD_BY_ATTR, None)
    if holder is None:
        return False
    return get_held_victim(holder) is character


def apply_hold(holder, victim):
    """Bond ``holder`` to ``victim`` until throw/slam/release."""
    if holder is None or victim is None or holder is victim:
        return False
    release_victim(holder)
    release_holder(victim)
    setattr(holder, HOLDER_ATTR, victim)
    setattr(victim, HELD_BY_ATTR, holder)
    return True


def break_hold(holder, victim):
    """Clear a grapple bond without side effects."""
    if holder is not None and getattr(holder, HOLDER_ATTR, None) is victim:
        holder.grapple_victim = None
    if victim is not None and getattr(victim, HELD_BY_ATTR, None) is holder:
        victim.grapple_holder = None


def release_victim(holder):
    """Drop whoever ``holder`` is holding."""
    victim = getattr(holder, HOLDER_ATTR, None)
    if victim is not None:
        break_hold(holder, victim)


def release_holder(victim):
    """Free ``victim`` from whoever holds them."""
    holder = getattr(victim, HELD_BY_ATTR, None)
    if holder is not None:
        break_hold(holder, victim)


def break_grapple(character):
    """Clear every grapple bond involving ``character`` (disconnect / move)."""
    if character is None:
        return
    victim = get_held_victim(character)
    if victim is not None:
        break_hold(character, victim)
    holder = getattr(character, HELD_BY_ATTR, None)
    if holder is not None:
        break_hold(holder, character)


def _resolve_direction(room, game, raw):
    """Map a typed fragment to (canonical_dir, dest_room_or_None)."""
    from command_support import DIRECTIONS

    needle = (raw or "").strip().lower()
    if not needle:
        return None, None
    # Multi-word: "into the east wall" -> try direction token first.
    for token in needle.split():
        canon = DIRECTIONS.get(token)
        if canon:
            needle = canon
            break
    else:
        canon = DIRECTIONS.get(needle)
        if canon:
            needle = canon
    dest = None
    exits = getattr(room, "exits", None) or {}
    if needle in exits:
        dest = exits[needle]
        return needle, dest
    import maps

    dest = maps.find_room_by_layout_direction(
        getattr(game, "rooms", None) or {}, room, needle,
    )
    if dest is not None:
        return needle, dest
    return needle, None


def _wall_in_direction(room, direction):
    """Return a slam_targets entry facing ``direction``, if any."""
    if room is None or not direction:
        return None
    canon = str(direction).strip().lower()
    for entry in getattr(room, "slam_targets", None) or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("direction") or "").strip().lower() == canon:
            return entry
    return breach_mod.find_slam_target(room, canon)


def _damage_victim(victim, amount):
    current = float(getattr(victim, "hp", 0.0) or 0.0)
    victim.hp = max(0.0, current - float(amount))


def _broadcast(room, text, *, exclude=None):
    if room is not None and hasattr(room, "broadcast"):
        room.broadcast(text, exclude=exclude)


def throw_held(holder, args, game, *, now_fn=None):
    """Throw the held victim in a direction (wall slam or room toss)."""
    room = getattr(holder, "location", None)
    if room is None:
        return False, "You are nowhere."
    victim = get_held_victim(holder)
    if victim is None:
        return False, "You are not holding anyone. Grab them first."
    if not readiness_mod.is_ready(holder, readiness_mod.TRACK_BALANCE, now_fn=now_fn):
        return False, "You are still off-balance -- wait a beat."
    direction_raw = (args or "").strip()
    if not direction_raw:
        return False, "Throw which direction? (throw east, throw north, ...)"
    direction, dest = _resolve_direction(room, game, direction_raw)
    if direction is None:
        return False, "Throw which direction? (north, east, south, west, ...)"
    readiness_mod.spend_balance(holder, THROW_BALANCE_COST, now_fn=now_fn)
    wall = _wall_in_direction(room, direction)
    break_hold(holder, victim)
    if wall is not None:
        label = wall.get("label") or wall.get("id") or direction
        result = breach_mod.apply_slam_damage(
            game, room, wall["id"], int(THROW_DAMAGE),
        )
        _damage_victim(victim, THROW_DAMAGE)
        _broadcast(
            room,
            f"{_name(holder)} hurls {_name(victim)} into {label}.",
        )
        if result.get("wrecked"):
            breach_mod.breach_eject(victim, room, wall, game=game)
            return True, f"{victim.key} crashes through {label}!"
        hp = result.get("hp")
        hp_max = result.get("hp_max")
        return True, (
            f"You throw {_name(victim)} into {label} "
            f"({hp}/{hp_max} structural HP)."
        )
    if dest is not None:
        old_room = room
        mover = getattr(victim, "move_to", None)
        if callable(mover):
            mover(dest)
        else:
            victim.location = dest
        from engine.systems import combat_pursuit as combat_pursuit_mod
        combat_pursuit_mod.notify_character_relocated(
            victim, old_room, dest, game,
        )
        _damage_victim(victim, THROW_DAMAGE * 0.5)
        _broadcast(
            room,
            f"{_name(holder)} throws {_name(victim)} {direction}.",
            exclude=holder,
        )
        if hasattr(dest, "broadcast"):
            dest.broadcast(
                f"{_name(victim)} tumbles in from the {direction}.",
                exclude=victim,
            )
        return True, f"You throw {_name(victim)} {direction}."
    return False, f"There is no exit or wall {direction!r} to throw them into."


def slam_held(holder, args, game, *, now_fn=None):
    """Slam the held victim into a wall or the nearest hard surface."""
    room = getattr(holder, "location", None)
    if room is None:
        return False, "You are nowhere."
    victim = get_held_victim(holder)
    if victim is None:
        return False, "You are not holding anyone. Grab them first."
    if not readiness_mod.is_ready(holder, readiness_mod.TRACK_BALANCE, now_fn=now_fn):
        return False, "You are still off-balance -- wait a beat."
    fragment = (args or "").strip()
    target = None
    if fragment:
        target = breach_mod.find_slam_target(room, fragment)
    if target is None:
        target = breach_mod.pick_slam_target(room)
    if target is None:
        return False, "There is no hard surface here to slam them into."
    readiness_mod.spend_balance(holder, SLAM_BALANCE_COST, now_fn=now_fn)
    label = target.get("label") or target.get("id")
    result = breach_mod.apply_slam_damage(
        game, room, target["id"], int(SLAM_DAMAGE),
    )
    _damage_victim(victim, SLAM_DAMAGE)
    break_hold(holder, victim)
    _broadcast(
        room,
        f"{_name(holder)} slams {_name(victim)} into {label}.",
    )
    if result.get("wrecked"):
        breach_mod.breach_eject(victim, room, target, game=game)
        return True, f"{victim.key} goes through {label}!"
    hp = result.get("hp")
    hp_max = result.get("hp_max")
    return True, f"You slam {_name(victim)} into {label} ({hp}/{hp_max} HP)."


def list_combat_skills():
    """Return grouped skill lines for the ``skills`` command."""
    from engine.systems import active_combat as ac

    strikes = sorted(v for v in ac.VERB_PROFILES if v != "legkick")
    lines = [
        "Strikes: " + ", ".join(strikes),
        "Grapple: grab, throw <dir>, slam [surface]",
        "Defense: dodge, block, parry [name]",
        "Tactics: aim <name> [zone], load/reload/fire, -- (clear queue)",
        "Pursuit: automatic when your fight target leaves the room",
        "Utility: autodefense dodge|block on|off, skills",
    ]
    return lines
