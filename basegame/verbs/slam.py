"""slam.py -- slam/throw environmental breach + grapple throw/slam."""

from engine.systems import breach as breach_mod
from engine.systems import grapple as grapple_mod

DEFAULT_SLAM_DAMAGE = 8


def _pick_target(room, args):
    fragment = (args or "").strip()
    if fragment:
        return breach_mod.find_slam_target(room, fragment)
    return breach_mod.pick_slam_target(room)


def cmd_slam(character, args, game):
    """Slam a held victim into a surface, or chip a wall/floor prop."""
    if grapple_mod.get_held_victim(character) is not None:
        ok, msg = grapple_mod.slam_held(character, args, game)
        character.session.send(msg)
        return
    room = getattr(character, "location", None)
    if room is None:
        character.session.send("You are nowhere.")
        return
    target = _pick_target(room, args)
    if target is None:
        character.session.send("There is nothing here to slam.")
        return
    result = breach_mod.apply_slam_damage(
        game, room, target["id"], DEFAULT_SLAM_DAMAGE,
    )
    if not result.get("ok"):
        character.session.send("That does not give way.")
        return
    label = result.get("label") or target.get("id")
    if result.get("wrecked"):
        character.session.send(f"You splinter {label}!")
        breach_mod.breach_eject(character, room, target, game=game)
    else:
        character.session.send(
            f"You hammer {label} ({result['hp']}/{result['hp_max']} HP)."
        )


def cmd_throw(character, args, game):
    """Throw a held victim in a direction, or hurl someone into a wall."""
    if grapple_mod.get_held_victim(character) is not None:
        ok, msg = grapple_mod.throw_held(character, args, game)
        character.session.send(msg)
        return
    room = getattr(character, "location", None)
    if room is None:
        character.session.send("You are nowhere.")
        return
    parts = (args or "").strip().split(maxsplit=1)
    if not parts:
        character.session.send("Throw whom? (or grab someone, then throw <dir>)")
        return
    name = parts[0]
    fragment = parts[1] if len(parts) > 1 else ""
    victim = game.find_character(name)
    if victim is None or getattr(victim, "location", None) is not room:
        character.session.send("You do not see them here.")
        return
    target = _pick_target(room, fragment)
    if target is None:
        character.session.send("There is no surface to throw them into.")
        return
    result = breach_mod.apply_slam_damage(
        game, room, target["id"], DEFAULT_SLAM_DAMAGE,
    )
    if not result.get("ok"):
        character.session.send("The surface does not give.")
        return
    label = result.get("label") or target.get("id")
    room.broadcast(
        f"{character.key} hurls {victim.key} into {label}.",
        exclude=None,
    )
    if result.get("wrecked"):
        breach_mod.breach_eject(victim, room, target, game=game)
        character.session.send(f"{victim.key} goes through {label}!")
    else:
        character.session.send(
            f"{label} holds ({result['hp']}/{result['hp_max']} HP)."
        )
