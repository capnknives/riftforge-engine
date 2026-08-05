"""
active_combat_verbs.py -- thin handlers that enqueue twitch combat commands.

Handlers do not resolve math inline -- they stamp + enqueue (decision #2)
and let ``active_combat.tick_active_combat`` drain on the heartbeat.

Registered into ``BASEGAME_COMMANDS`` (not ENGINE_COMMANDS) so SUPERS keeps
its own combat verb surface. The ``--`` clear-queue verb is engine-owned
logic but still exposed via basegame for the demo.
"""

from __future__ import annotations

from engine.systems import active_combat as ac
from engine.systems import active_combat_defense as defense_mod
from engine.systems import combat_runtime as combat_runtime_mod
from engine.systems import fight as fight_mod


def _send(character, text):
    session = getattr(character, "session", None)
    if session is not None:
        session.send(text)


def _find_target(character, args, game):
    """Resolve a same-room target by name, or the character's current target."""
    name = (args or "").strip()
    room = getattr(character, "location", None)
    if room is None:
        return None, "You are nowhere."
    if not name:
        focus = getattr(character, "target", None)
        if focus is not None and getattr(focus, "location", None) is room:
            return focus, None
        return None, "Attack whom? (punch <name>, or set a target first)"
    needle = name.lower()
    for other in getattr(room, "contents", []) or []:
        if other is character:
            continue
        key = (getattr(other, "key", None) or "").lower()
        if key.startswith(needle) or needle in key:
            return other, None
    return None, f"You don't see '{name}' here."


def _ensure_active_fight(character, target, game=None):
    """Join/create a Fight using combat_runtime engagement resolution."""
    existing = fight_mod.get_fight(character) or fight_mod.get_fight(target)
    if existing is not None:
        fight_mod.join_fight(character, target)
        return existing
    room = getattr(character, "location", None)
    mode = combat_runtime_mod.resolve_engagement_fight_mode(
        game, room=room, target=target,
    )
    return fight_mod.join_fight(character, target, combat_mode=mode)


def _enqueue_offense(character, args, game, verb):
    """Shared path for jab/punch/sweep/uppercut/kick/headbutt."""
    target, err = _find_target(character, args, game)
    if err:
        _send(character, err)
        return
    fight = _ensure_active_fight(character, target, game)
    if fight is None or fight.combat_mode != fight_mod.MODE_ACTIVE:
        _send(
            character,
            "This bout isn't using active combat. "
            "Type loadcombat active_combat, or fight in an active-combat room.",
        )
        return
    character.target = target
    ok, msg = ac.enqueue(character, {
        "kind": "offense",
        "verb": verb,
        "target": target,
        "readiness_track": "balance",
    })
    if not ok:
        _send(character, msg)
        return
    _send(character, f"You queue a {verb} at {ac._name(target)}.")


def cmd_jab(character, args, game):
    """Fast probe strike -- short Balance, low damage."""
    _enqueue_offense(character, args, game, "jab")


def cmd_punch(character, args, game):
    """Standard kinetic strike."""
    _enqueue_offense(character, args, game, "punch")


def cmd_sweep(character, args, game):
    """Ground-only leg sweep -- can apply prone on a clean hit."""
    _enqueue_offense(character, args, game, "sweep")


def cmd_uppercut(character, args, game):
    """Heavy rising strike -- long Balance, high damage."""
    _enqueue_offense(character, args, game, "uppercut")


def cmd_kick(character, args, game):
    """Ground-only leg attack."""
    _enqueue_offense(character, args, game, "kick")


def cmd_legkick(character, args, game):
    """Alias for kick."""
    _enqueue_offense(character, args, game, "legkick")


def cmd_headbutt(character, args, game):
    """Close-range head strike."""
    _enqueue_offense(character, args, game, "headbutt")


def cmd_aim(character, args, game):
    """Spend Equilibrium to aim the next strike at a body zone."""
    zone = (args or "").strip().lower()
    if zone in ("", "clear", "none"):
        ac.ensure_defaults(character)
        setattr(character, ac.AIM_ZONE_ATTR, None)
        _send(character, "Aim cleared.")
        return
    if zone not in ac.AIM_ZONES:
        _send(
            character,
            "Aim where? "
            + ", ".join(sorted(ac.AIM_ZONES))
            + " (or aim clear).",
        )
        return
    ok, msg = ac.enqueue(character, {
        "kind": "aim",
        "zone": zone,
        "readiness_track": "equilibrium",
    })
    if not ok:
        _send(character, msg)
        return
    _send(character, f"You queue an aim at {zone}.")


def _enqueue_defense(character, args, defense_kind):
    """Shared path for dodge/block/parry -- readiness-free (§10.1)."""
    attacker_name = (args or "").strip() or None
    ok, msg = ac.enqueue(character, {
        "kind": "manual_defense",
        "defense_kind": defense_kind,
        "attacker_name": attacker_name,
        "readiness_track": None,
    })
    if not ok:
        _send(character, msg)
        return
    label = defense_kind
    if attacker_name:
        _send(character, f"You brace to {label} against {attacker_name}.")
    else:
        _send(character, f"You brace to {label}.")


def cmd_dodge(character, args, game):
    """Manual dodge -- upgrade over auto-dodge when timed in-window."""
    _enqueue_defense(character, args, defense_mod.DEFENSE_DODGE)


def cmd_block(character, args, game):
    """Manual block -- upgrade over auto-block when timed in-window."""
    _enqueue_defense(character, args, defense_mod.DEFENSE_BLOCK)


def cmd_parry(character, args, game):
    """Manual-only parry -- never rolled by auto-defense (decision #16)."""
    _enqueue_defense(character, args, defense_mod.DEFENSE_PARRY)


def cmd_clear_combat_queue(character, args, game):
    """``--`` -- drop pending queued actions (not in-flight telegraphs)."""
    # Enqueue a clear so it participates in global ordering, OR clear
    # immediately for snappy UX. Plan decision #20: clears pending buffer
    # immediately. Immediate clear matches player expectation for "--".
    n = ac.clear_queue(character)
    _send(character, f"[QUEUE] Cleared {n} pending action{'s' if n != 1 else ''}.")


def cmd_autodefense(character, args, game):
    """Toggle auto-dodge / auto-block: ``autodefense block off``."""
    parts = (args or "").strip().lower().split()
    if len(parts) < 2:
        _send(
            character,
            "Usage: autodefense <dodge|block> <on|off>",
        )
        return
    kind, state = parts[0], parts[1]
    if kind not in defense_mod.AUTO_DEFENSES:
        _send(character, "Only dodge and block can be auto-toggled (not parry).")
        return
    if state not in ("on", "off"):
        _send(character, "Use on or off.")
        return
    defense_mod.set_auto_defense(character, kind, enabled=(state == "on"))
    _send(character, f"Auto-{kind} is now {state}.")
