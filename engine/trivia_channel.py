"""trivia_channel.py -- opt-in global trivia net formatting and broadcast."""

from __future__ import annotations

from engine import channel_history

TRIVIA_PREFIX = "((TRIVIA))"
TRIVIA_TITLE = "Trivia"
TRIVIA_COLOR_ROLE = "gold"


def is_subscribed(character, game) -> bool:
    """True when *character*'s account opted into the trivia channel."""
    from engine import accounts as accounts_mod

    account = accounts_mod.account_for_character(game, character)
    if account is None:
        return False
    return bool(getattr(account, "trivia_subscribed", False))


def set_subscribed(game, account, subscribed: bool) -> None:
    """Persist opt-in flag on *account*."""
    from engine import accounts as accounts_mod

    account.trivia_subscribed = bool(subscribed)
    accounts_mod._try_mark_account_dirty(game, account, label="trivia_subscribed")


def speaker_face_for_character(character, game, viewer=None):
    from engine import ooc_channel

    return ooc_channel.speaker_face_for_character(character, game, viewer=viewer)


def format_trivia_line(face: str, message: str) -> str:
    return f"{TRIVIA_PREFIX} [{face}]: {message}"


def render_trivia_line(character, face: str, message: str) -> str:
    from engine import display_prefs
    from engine import style

    display_prefs.ensure_display_defaults(character)
    plain = display_prefs.format_custom_chat(
        character, TRIVIA_TITLE, TRIVIA_PREFIX, face, message,
    )
    if getattr(character, "screenreader", False) or not getattr(character, "use_color", True):
        return plain
    return style.paint_for(character, TRIVIA_COLOR_ROLE, plain)


def make_trivia_history_entry(
    speaker_key: str,
    message: str,
    *,
    face: str | None = None,
    plain: str | None = None,
) -> dict:
    entry = {"speaker": speaker_key, "message": message}
    if face and str(face).strip():
        entry["face"] = str(face).strip()
    if plain and str(plain).strip():
        entry["plain"] = str(plain).strip()
    return entry


def broadcast_trivia(
    game,
    message: str,
    *,
    speaker=None,
    speaker_face: str | None = None,
    speaker_key: str | None = None,
    system: bool = False,
):
    """Send one trivia line to every subscribed online session."""
    from engine import accounts
    from engine import channels
    from engine import gmcp

    body = (message or "").strip()
    if not body:
        return False
    spec = channels.get_channel("trivia")
    if spec is None:
        return False
    if system:
        public_face = speaker_face or "Trivia"
        public_key = speaker_key or "trivia-bot"
    else:
        public_face = (
            speaker_face
            or (speaker_face_for_character(speaker, game) if speaker else "?")
        )
        public_key = speaker_key or (getattr(speaker, "key", None) if speaker else None)
    public_plain = format_trivia_line(public_face, body)
    entry = make_trivia_history_entry(
        public_key or "",
        body,
        face=public_face,
        plain=public_plain,
    )
    channel_history.append(game, "trivia", entry, gateway_plain=public_plain)
    delivered = False
    for session in list(getattr(game, "sessions", None) or []):
        other = getattr(session, "character", None)
        if other is None:
            continue
        if not channels.can_hear(other, spec, game):
            continue
        if not system and speaker is not None and public_key:
            if accounts.ooc_should_hide_from_viewer(other, game, public_key):
                continue
        if speaker is not None and not system:
            line_face = speaker_face_for_character(speaker, game, viewer=other)
        else:
            line_face = public_face
        line = render_trivia_line(other, line_face, body)
        gmcp.deliver_comm(session, spec.verb, body, line_face, line, "")
        delivered = True
    return delivered


def format_trivia_history_entry(entry, viewer, game) -> str:
    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return str(entry)
    message = str(entry.get("message") or "")
    speaker_key = entry.get("speaker")
    plain = entry.get("plain")
    if not speaker_key and isinstance(plain, str) and plain.strip():
        return plain.strip()
    if speaker_key == "trivia-bot" or not speaker_key:
        fallback = entry.get("face") or "Trivia"
        if viewer is not None:
            return render_trivia_line(viewer, fallback, message)
        return format_trivia_line(fallback, message)
    finder = getattr(game, "find_character", None)
    speaker = finder(speaker_key) if callable(finder) and speaker_key else None
    if speaker is None:
        face = entry.get("face") or "?"
        if viewer is not None:
            return render_trivia_line(viewer, face, message)
        return format_trivia_line(face, message)
    face = speaker_face_for_character(speaker, game, viewer=viewer)
    if viewer is not None:
        return render_trivia_line(viewer, face, message)
    return format_trivia_line(face, message)
