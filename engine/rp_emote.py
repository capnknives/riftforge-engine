"""
rp_emote.py -- targeted emote substitution (@name / $me / pronouns).

Per-viewer lines: the actor may read ``You …`` while others read proper
names. No NLP -- token substitution only (D57 + prefs #25 extensions).
"""

from __future__ import annotations

import re

# @name, @name's, @name/subj|obj|poss
_AT_TOKEN = re.compile(
    r"@([A-Za-z0-9_\-]+)(?:'s|/(subj|obj|poss))?",
    re.IGNORECASE,
)
_ME_TOKEN = re.compile(r"\$me\b", re.IGNORECASE)
_ACTOR_PRONOUN = re.compile(r"\$(subj|obj|poss)\b", re.IGNORECASE)

_PRONOUN_TABLE = {
    "he": {"subj": "he", "obj": "him", "poss": "his"},
    "she": {"subj": "she", "obj": "her", "poss": "her"},
    "they": {"subj": "they", "obj": "them", "poss": "their"},
}

_SMOTE_I_PREFIXES = ("i ", "i'm ", "i am ")


def _pronoun_keys(character):
    """Map Character.pronoun to subj/obj/poss strings."""
    raw = str(getattr(character, "pronoun", "they") or "they").strip().lower()
    return _PRONOUN_TABLE.get(raw, _PRONOUN_TABLE["they"])


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


def _viewer_subj(character, viewer):
    if viewer is character:
        return "you"
    return _pronoun_keys(character)["subj"]


def _viewer_obj(character, viewer):
    if viewer is character:
        return "you"
    return _pronoun_keys(character)["obj"]


def _viewer_poss(character, viewer):
    if viewer is character:
        return "your"
    return _pronoun_keys(character)["poss"]


def _find_room_target(name, room, game):
    """Resolve @token to a Character in *room* (prefix / exact key)."""
    if room is None:
        return None
    needle = (name or "").strip().lower()
    if not needle:
        return None
    from world import Character

    chars = room.characters() if hasattr(room, "characters") else []
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
    return None


def normalize_smote_body(raw_args):
    """Smote strips redundant first-person lead-ins (``smote I grin``)."""
    body = (raw_args or "").strip()
    if not body:
        return body
    low = body.lower()
    for prefix in _SMOTE_I_PREFIXES:
        if low.startswith(prefix):
            return body[len(prefix):].lstrip()
    return body


def _substitute_tokens(text, actor, viewer, game, room):
    """Replace @name / $me / $subj|$obj|$poss in the action fragment."""

    def _at_replace(match):
        token = match.group(1)
        kind = (match.group(2) or "").lower()
        full = match.group(0)
        target = _find_room_target(token, room, game)
        if target is None:
            return full
        if full.endswith("'s"):
            if viewer is target:
                return "your"
            face = _target_face(target, viewer, game)
            if face == "you":
                return "your"
            return f"{face}'s"
        if kind == "subj":
            return _viewer_subj(target, viewer)
        if kind == "obj":
            return _viewer_obj(target, viewer)
        if kind == "poss":
            return _viewer_poss(target, viewer)
        return _target_face(target, viewer, game)

    def _actor_pronoun_replace(match):
        kind = match.group(1).lower()
        if kind == "subj":
            return _viewer_subj(actor, viewer)
        if kind == "obj":
            return _viewer_obj(actor, viewer)
        return _viewer_poss(actor, viewer)

    out = _AT_TOKEN.sub(_at_replace, text)
    out = _ME_TOKEN.sub(lambda _m: _actor_face(actor, viewer, game), out)
    out = _ACTOR_PRONOUN.sub(_actor_pronoun_replace, out)
    return out


def format_emote_line(actor, raw_args, viewer, game, *, mode="emote"):
    """Build one emote line for *viewer* watching *actor*.

    Supports prefs #25 leading ``'s`` possessive on the action body.
    ``mode`` ``smote`` normalizes redundant ``I …`` lead-ins.
    """
    text = (raw_args or "").strip()
    if not text:
        return None
    if mode == "smote":
        text = normalize_smote_body(text)
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
        return f"You {body}"
    return f"{face} {body}"


def broadcast_emote(actor, raw_args, game, *, mode="emote"):
    """Send per-viewer emote lines to the actor and room watchers."""
    room = getattr(actor, "location", None)
    if room is None:
        return

    def _send_emote_line(viewer, line):
        from engine import display_prefs as display_prefs_mod

        painted = display_prefs_mod.paint_channel_line(
            viewer, "emote", line, default="emote",
        )
        viewer.session.send(painted)
        viewer.session.send("")

    session = getattr(actor, "session", None)
    if session is not None:
        line = format_emote_line(actor, raw_args, actor, game, mode=mode)
        if line:
            _send_emote_line(actor, line)
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
        line = format_emote_line(actor, raw_args, watcher, game, mode=mode)
        if not line:
            continue
        _send_emote_line(watcher, line)
        try:
            from engine import rp_transcript as transcript_mod
            transcript_mod.capture(watcher, line)
        except Exception:
            pass
