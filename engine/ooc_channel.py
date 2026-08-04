"""ooc_channel.py -- shared OOC line formatting for game + gateway relay."""

from __future__ import annotations

import re

from engine import channel_history

OOC_HISTORY_MAX = channel_history.OOC_HISTORY_MAX

# Normal player/staff OOC vs immersion Chuck Author nudges (bug/suggest).
OOC_KIND_NORMAL = "normal"
OOC_KIND_AUTHOR_NUDGE = "author_nudge"

# Highlight these verbs in Author nudge bodies (sighted + color on only).
_AUTHOR_NUDGE_VERB_RE = re.compile(r"\b(bug|suggest)\b", re.IGNORECASE)


def speaker_face_for_character(character, game, viewer=None):
    """OOC label: account display name when pref says account, else character.

    OOC always shows account or character legal name — never viewer-relative
    short-desc / hood / unintroduced appearance (bug #253).

    When the speaker uses the character OOC identity and *viewer* is a staff
    GM in form with ``config seeaccounts on``, the label becomes
    ``Character(Account)`` via ``_maybe_append_account_tag``.

    When the speaker uses the account OOC identity, everyone (including staff)
    sees the account name only -- no ``Character(Account)`` suffix.

    Staff in ``gm on`` always reads as ``Accountname(GM)`` only — never the
    storage key, Echo body login name, or a redundant ``(Account)`` seeaccounts
    suffix (``CapnKnives(GM)(CapnKnives)``).
    """
    try:
        from command_support import _presence_face, _staff_form_label

        if _staff_form_label(character):
            # Fixed staff OOC face — skip _display_name so seeaccounts /
            # (Group) never pile onto Accountname(GM).
            return f"{_presence_face(character)}(GM)"
    except Exception:
        pass
    try:
        from engine import accounts as accounts_mod

        account = accounts_mod.account_for_character(game, character)
        if (
            account is not None
            and account.ooc_identity == accounts_mod.OOC_IDENTITY_ACCOUNT
        ):
            return account.display_name or account.name
    except Exception:
        pass
    from command_support import _maybe_append_account_tag, _presence_face

    face = _presence_face(character)
    if viewer is not None:
        face = _maybe_append_account_tag(face, character, viewer)
    return face


def speaker_face_for_session(session, game, viewer=None):
    """OOC label for a live Session (used when binding gateway slots)."""
    character = getattr(session, "character", None)
    if character is None:
        return "?"
    return speaker_face_for_character(character, game, viewer=viewer)


def format_ooc_line(face: str, message: str, *, kind: str = OOC_KIND_NORMAL) -> str:
    """Plain OOC line — ``((OOC))`` prefix; Author nudges add ``[AUTHOR]`` tag."""
    if kind == OOC_KIND_AUTHOR_NUDGE:
        return f"((OOC)) [AUTHOR] [{face}]: {message}"
    return f"((OOC)) [{face}]: {message}"


def _highlight_author_nudge_verbs(message: str) -> str:
    """Silver accent on bug/suggest for sighted layered paint (no backticks)."""

    def _repl(match):
        return f"<silver>{match.group(0)}<_base>"

    return _AUTHOR_NUDGE_VERB_RE.sub(_repl, message)


def render_ooc_line(character, face: str, message: str, *, kind: str = OOC_KIND_NORMAL) -> str:
    """Paint one OOC line for *character* (or plain when SR / color off)."""
    from engine import display_prefs
    from engine import style

    display_prefs.ensure_display_defaults(character)
    plain = format_ooc_line(face, message, kind=kind)
    if getattr(character, "screenreader", False):
        return plain
    if not getattr(character, "use_color", True):
        return plain
    if kind == OOC_KIND_AUTHOR_NUDGE:
        body = _highlight_author_nudge_verbs(message)
        template = format_ooc_line(face, body, kind=kind)
        return style.paint_layered_for(character, "gold", template)
    role = display_prefs.channel_role(character, "ooc", default="ooc")
    return style.paint_for(character, role, plain)


def make_ooc_history_entry(
    speaker_key: str,
    message: str,
    *,
    kind: str = OOC_KIND_NORMAL,
) -> dict:
    """Structured OOC ring entry (speaker key + message for per-viewer replay)."""
    entry = {"speaker": speaker_key, "message": message}
    if kind != OOC_KIND_NORMAL:
        entry["kind"] = kind
    return entry


def broadcast_ooc(
    game,
    speaker,
    message,
    *,
    speaker_session=None,
    kind: str = OOC_KIND_NORMAL,
):
    """Send one OOC line to every connected session; record history + Discord.

    *speaker* is a Character (``speaker.key`` is stored in the ring buffer).
    When no session receives the line but *speaker_session* is set, deliver
    to that session anyway (``cmd_ooc`` parity).

    *kind* ``author_nudge`` uses gold + ``[AUTHOR]`` (Chuck bug/suggest lines).

    Returns True when at least one session received the line.
    """
    from engine import gmcp

    public_face = speaker_face_for_character(speaker, game)
    public_plain = format_ooc_line(public_face, message, kind=kind)
    entry = make_ooc_history_entry(speaker.key, message, kind=kind)
    channel_history.append(
        game, "ooc", entry, gateway_plain=public_plain,
    )
    delivered = False
    for session in list(getattr(game, "sessions", None) or []):
        other = getattr(session, "character", None)
        if other is None:
            continue
        face = speaker_face_for_character(speaker, game, viewer=other)
        line = render_ooc_line(other, face, message, kind=kind)
        session.send(line)
        session.send("")
        gmcp.push_comm(session, "ooc", message, face)
        delivered = True
    if not delivered and speaker_session is not None:
        other = speaker
        face = speaker_face_for_character(speaker, game, viewer=other)
        line = render_ooc_line(other, face, message, kind=kind)
        speaker_session.send(line)
        speaker_session.send("")
        gmcp.push_comm(speaker_session, "ooc", message, face)
        delivered = True
    try:
        from engine import discord_bridge

        discord_bridge.schedule_ooc(public_plain)
    except Exception as exc:
        print(f"[discord_bridge] ooc schedule skipped: {exc}", flush=True)
    return delivered


def format_ooc_history_entry(entry, viewer, game) -> str:
    """Render one history entry for *viewer* (legacy plain strings pass through)."""
    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return str(entry)
    message = str(entry.get("message") or "")
    kind = entry.get("kind") or OOC_KIND_NORMAL
    speaker_key = entry.get("speaker")
    if not speaker_key or game is None:
        if viewer is not None:
            return render_ooc_line(viewer, "?", message, kind=kind)
        return format_ooc_line("?", message, kind=kind)
    finder = getattr(game, "find_character", None)
    speaker = finder(speaker_key) if callable(finder) else None
    if speaker is None:
        plain = entry.get("plain")
        if isinstance(plain, str) and plain:
            return plain
        if viewer is not None:
            return render_ooc_line(viewer, "?", message, kind=kind)
        return format_ooc_line("?", message, kind=kind)
    face = speaker_face_for_character(speaker, game, viewer=viewer)
    if viewer is not None:
        return render_ooc_line(viewer, face, message, kind=kind)
    return format_ooc_line(face, message, kind=kind)


def session_is_head_gm(session, game) -> bool:
    """True when this session's staff account is head GM."""
    from command_support import _is_head_gm

    character = getattr(session, "character", None)
    if character is None:
        return False
    return _is_head_gm(character)


def session_is_staff_gm(session, game) -> bool:
    """True when this session's character is online staff (not immersion cast)."""
    from command_support import _is_staff_gm

    character = getattr(session, "character", None)
    if character is None:
        return False
    return _is_staff_gm(character)


def export_plain_history(game) -> list[str]:
    """Plain OOC lines for gateway stitch replay (delegates to channel_history)."""
    from engine import channel_history

    snapshot = channel_history.export_gateway_snapshot(game)
    return list(snapshot.get("ooc") or [])
