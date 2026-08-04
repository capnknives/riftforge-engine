"""
rp_emote.py -- D57 targeted emote substitution (@name / $me).

Per-viewer lines: the actor may read ``You …`` while others read proper
names. No NLP -- token substitution only.
"""

from __future__ import annotations

import re

_AT_TOKEN = re.compile(r"@([A-Za-z0-9_\-]+)")
_ME_TOKEN = re.compile(r"\$me\b", re.IGNORECASE)


def _actor_face(actor, viewer, game):
    """Emote subject label for *viewer* (You vs public name)."""
    if viewer is actor:
        return "You"
    try:
        from engine.command_support import _display_name
        if viewer is not None:
            return _display_name(actor, viewer=viewer)
        return _display_name(actor)
    except Exception:
        return getattr(actor, "key", "?")


def _target_face(target, viewer, game):
    """@token target label for *viewer* (you vs public name)."""
    if viewer is target:
        return "you"
    try:
        from engine.command_support import _display_name
        if viewer is not None:
            return _display_name(target, viewer=viewer)
        return _display_name(target)
    except Exception:
        return getattr(target, "key", "?")


def _find_room_target(name, room, game):
    """Resolve @token to a Character in *room* (prefix / exact key)."""
    if room is None:
        return None
    needle = (name or "").strip().lower()
    if not needle:
        return None
    from world import Character

    chars = room.characters() if hasattr(room, "characters") else []
    exact = None
    partial = []
    for ch in chars:
        if not isinstance(ch, Character):
            continue
        key = getattr(ch, "key", None) or ""
        low = key.lower()
        if low == needle:
            return ch
        if low.startswith(needle):
            partial.append(ch)
    if len(partial) == 1:
        return partial[0]
    return exact


def _substitute_tokens(text, actor, viewer, game, room):
    """Replace @name and $me in the action fragment."""

    def _at_replace(match):
        token = match.group(1)
        target = _find_room_target(token, room, game)
        if target is None:
            return f"@{token}"
        return _target_face(target, viewer, game)

    out = _AT_TOKEN.sub(_at_replace, text)
    out = _ME_TOKEN.sub(lambda _m: _actor_face(actor, viewer, game), out)
    return out


def format_emote_line(actor, raw_args, viewer, game):
    """Build one emote line for *viewer* watching *actor*.

    Supports prefs #25 leading ``'s`` possessive on the action body.
    """
    text = (raw_args or "").strip()
    if not text:
        return None
    room = getattr(actor, "location", None)
    possessive = False
    body = text
    if body.startswith("'s ") or body.startswith("'s\t"):
        possessive = True
        body = body[3:].lstrip()
    elif body.startswith("'s"):
        possessive = True
        body = body[2:].lstrip()
    body = _substitute_tokens(body, actor, viewer, game, room)
    face = _actor_face(actor, viewer, game)
    if possessive:
        if face == "You":
            return f"Your {body}"
        return f"{face}'s {body}"
    if face == "You":
        # Third-person emote convention: "You pat …" not "You you pat …".
        return f"You {body}"
    return f"{face} {body}"


def broadcast_emote(actor, raw_args, game):
    """Send per-viewer emote lines to the actor and room watchers."""
    room = getattr(actor, "location", None)
    if room is None:
        return
    session = getattr(actor, "session", None)
    if session is not None:
        line = format_emote_line(actor, raw_args, actor, game)
        if line:
            session.send(line)
            session.send("")
            try:
                from engine import rp_transcript as transcript_mod
                transcript_mod.capture(actor, line)
            except Exception:
                pass
    from world import Character

    for watcher in room.characters():
        if watcher is actor:
            continue
        w_sess = getattr(watcher, "session", None)
        if w_sess is None:
            continue
        line = format_emote_line(actor, raw_args, watcher, game)
        if not line:
            continue
        w_sess.send(line)
        w_sess.send("")
        try:
            from engine import rp_transcript as transcript_mod
            transcript_mod.capture(watcher, line)
        except Exception:
            pass
