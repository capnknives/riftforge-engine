"""
snoop.py -- GM viewpoint mirroring (classic MUD `snoop`).

A GM who snoops a character/NPC/Echo receives a copy of what that target
*sees* (and, for online players / npc_do actors, what they *type*), tagged
so it is easy to tell apart from the GM's own room text:

    % Bob> You say, 'hello'
    % Bob] say hello

The target is never notified. State is live-only (not persisted): each GM
tracks at most one `snooping` target; each target holds a `snoopers` set of
watching GMs.

Delivery hooks (so call sites do not need to know about snoop):
  - Session / FakeSession / SilentSession `.send` -> mirror_output
  - Session.play / npc_do command lines -> mirror_input
  - Room.broadcast / combat._tell_room for sessionless targets that still
    have snoopers (offline Echo / NPC standing in a room)

Relay lines are written with emit_raw() so a mirrored line does not
re-fanout if the GM is themselves being snooped (avoids A<->B loops).
"""


def emit_raw(session, message):
    """Write `message` to `session` without triggering another snoop fanout.

    Real Session objects expose `_write` (color already applied by the
    caller when needed). FakeSession / SilentSession only have `.lines` --
    append directly so we do not call `.send` and recurse into mirror_output.
    """
    if session is None:
        return
    # Prefer the private write path on a live telnet Session.
    writer = getattr(session, "_write", None)
    if callable(writer):
        writer(message)
        return
    # Smoke-test FakeSession and npc_act.SilentSession: a plain list of lines.
    lines = getattr(session, "lines", None)
    if isinstance(lines, list):
        lines.append(message)


def _strip_for_viewer(session, message):
    """Apply the viewing character's color preference, if any."""
    viewer = getattr(session, "character", None)
    if viewer is not None and not getattr(viewer, "use_color", True):
        from engine import style
        return style.strip_ansi(message)
    return message


def tell(character, message):
    """Deliver a viewpoint line to `character`, and always to their snoopers.

    Online characters go through session.send (which itself mirrors). 
    Sessionless NPCs/Echoes only hit mirror_output -- the same "You hit..."
    prose a player would have seen.
    """
    if character is None or message is None:
        return
    session = getattr(character, "session", None)
    if session is not None:
        session.send(message)
    else:
        mirror_output(character, message)


def tell_paragraph(character, message):
    """Deliver a paragraph-like line, then one blank line for spacing.

    Long cinematic / tip-off prose blends into the next tick or chat line
    without a gap. The blank goes only to the live session (not snoop
    mirrors) so GMs do not see empty ``% Name> `` noise.
    """
    tell(character, message)
    session = getattr(character, "session", None)
    if session is not None:
        session.send("")


def mirror_output(target, message):
    """Fan a viewpoint line out to every GM snooping `target`."""
    if target is None:
        return
    snoopers = getattr(target, "snoopers", None)
    if not snoopers:
        return
    # list() so a mid-loop stop() cannot mutate the set under us.
    for gm in list(snoopers):
        sess = getattr(gm, "session", None)
        if sess is None:
            continue
        # Skip a dead telnet session (FakeSession has no .alive).
        if hasattr(sess, "alive") and not sess.alive:
            continue
        tagged = f"% {target.key}> {message}"
        emit_raw(sess, _strip_for_viewer(sess, tagged))


def mirror_input(target, raw_line):
    """Fan a typed (or npc_do) command line out to snoopers.

    Classic MUDs show input with a different bracket so GMs can tell
    commands apart from the resulting prose (`]` vs `>`).
    """
    if target is None or not raw_line:
        return
    snoopers = getattr(target, "snoopers", None)
    if not snoopers:
        return
    for gm in list(snoopers):
        sess = getattr(gm, "session", None)
        if sess is None:
            continue
        if hasattr(sess, "alive") and not sess.alive:
            continue
        tagged = f"% {target.key}] {raw_line}"
        emit_raw(sess, _strip_for_viewer(sess, tagged))


def start(snooper, target):
    """Point `snooper` at `target`. Returns (ok, message_for_snooper)."""
    if snooper is None or target is None:
        return False, "Snoop whom?"
    if snooper is target:
        return False, "You can't snoop yourself."
    # Switching targets: drop the old link first.
    if getattr(snooper, "snooping", None) is not None:
        stop(snooper, quiet=True)
    snooper.snooping = target
    # set() default on Character; be defensive if an old object lacks it.
    if not hasattr(target, "snoopers") or target.snoopers is None:
        target.snoopers = set()
    target.snoopers.add(snooper)
    where = ""
    loc = getattr(target, "location", None)
    if loc is not None:
        where = f" ({loc.key})"
    kind = "NPC" if getattr(target, "is_npc", False) else (
        "Echo" if getattr(target, "session", None) is None else "player"
    )
    return True, (
        f"You are now snooping {target.key} [{kind}]{where}. "
        f"Type 'snoop' or 'snoop off' to stop."
    )


def stop(snooper, quiet=False):
    """Stop `snooper`'s current snoop. Returns (ok, message)."""
    target = getattr(snooper, "snooping", None)
    if target is None:
        if quiet:
            return True, ""
        return False, "You aren't snooping anyone."
    snooper.snooping = None
    snoopers = getattr(target, "snoopers", None)
    if snoopers is not None:
        snoopers.discard(snooper)
    if quiet:
        return True, ""
    return True, f"You stop snooping {target.key}."


def clear_target(target):
    """Drop every snoop link aimed at `target` (hakai / despawn cleanup)."""
    if target is None:
        return
    snoopers = getattr(target, "snoopers", None)
    if not snoopers:
        target.snooping = getattr(target, "snooping", None)
        return
    for gm in list(snoopers):
        if getattr(gm, "snooping", None) is target:
            gm.snooping = None
    snoopers.clear()
