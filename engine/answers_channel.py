"""answers_channel.py -- helper-only answers net (OOC-adjacent identity labels)."""

from __future__ import annotations

from engine import channel_history

ANSWERS_PREFIX = "((ANSWERS))"
ANSWERS_TITLE = "Answers"
ANSWERS_COLOR_ROLE = "absinthe_green"


def speaker_face_for_character(character, game, viewer=None):
    """Reuse OOC identity rules so helpers show account names by default."""
    from engine import ooc_channel

    return ooc_channel.speaker_face_for_character(character, game, viewer=viewer)


def format_answers_line(face: str, message: str) -> str:
    """Plain answers line -- ``((ANSWERS))`` prefix."""
    return f"{ANSWERS_PREFIX} [{face}]: {message}"


def render_answers_line(character, face: str, message: str) -> str:
    """Paint one answers line for *character* (or plain when SR / color off)."""
    from engine import display_prefs
    from engine import style

    display_prefs.ensure_display_defaults(character)
    line = display_prefs.format_custom_chat(
        character, ANSWERS_TITLE, ANSWERS_PREFIX, face, message,
    )
    if getattr(character, "screenreader", False) or not getattr(character, "use_color", True):
        return line
    return style.paint_for(character, ANSWERS_COLOR_ROLE, line)


def make_answers_history_entry(
    speaker_key: str,
    message: str,
    *,
    face: str | None = None,
    plain: str | None = None,
) -> dict:
    entry = {"speaker": speaker_key, "message": message}
    cached_face = (face or "").strip()
    if cached_face:
        entry["face"] = cached_face
    cached_plain = (plain or "").strip()
    if cached_plain:
        entry["plain"] = cached_plain
    return entry


def _fallback_face(entry: dict) -> str | None:
    face = entry.get("face")
    if isinstance(face, str) and face.strip():
        return face.strip()
    return None


def broadcast_answers(
    game,
    speaker,
    message,
    *,
    speaker_session=None,
):
    """Send one answers line to every eligible helper session; record history."""
    from engine import channels
    from engine import display_prefs
    from engine import gmcp
    from engine.accounts import ooc_should_hide_from_viewer

    body = (message or "").strip()
    if not body:
        return False
    spec = channels.get_channel("answers")
    if spec is None:
        return False
    public_face = speaker_face_for_character(speaker, game)
    public_plain = format_answers_line(public_face, body)
    speaker_key = getattr(speaker, "key", None)
    entry = make_answers_history_entry(
        speaker_key,
        body,
        face=public_face,
        plain=public_plain,
    )
    channel_history.append(
        game, "answers", entry, gateway_plain=public_plain,
    )
    try:
        from engine import channel_transcript

        channel_transcript.record(
            "answers",
            face=public_face,
            message=body,
            game=game,
            speaker=str(speaker_key or ""),
        )
    except Exception:
        pass
    delivered = False

    def _render_for(viewer, face: str) -> str:
        display_prefs.ensure_display_defaults(viewer)
        return render_answers_line(viewer, face, body)

    for session in list(getattr(game, "sessions", None) or []):
        other = getattr(session, "character", None)
        if other is None:
            continue
        if not channels.can_hear(other, spec, game):
            continue
        if speaker_key and ooc_should_hide_from_viewer(other, game, speaker_key):
            continue
        line_face = speaker_face_for_character(speaker, game, viewer=other)
        line = _render_for(other, line_face)
        gmcp.deliver_comm(session, spec.verb, body, line_face, line, "")
        delivered = True
    if not delivered and speaker_session is not None:
        line_face = speaker_face_for_character(speaker, game, viewer=speaker)
        line = _render_for(speaker, line_face)
        gmcp.deliver_comm(speaker_session, spec.verb, body, line_face, line, "")
        delivered = True
    return delivered


def format_answers_history_entry(entry, viewer, game) -> str:
    """Render one history entry for *viewer*."""
    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return str(entry)
    message = str(entry.get("message") or "")
    speaker_key = entry.get("speaker")
    plain = entry.get("plain")
    if not speaker_key and isinstance(plain, str) and plain:
        return plain
    if not speaker_key or game is None:
        if viewer is not None:
            return render_answers_line(viewer, "?", message)
        return format_answers_line("?", message)
    finder = getattr(game, "find_character", None)
    speaker = finder(speaker_key) if callable(finder) else None
    if speaker is None:
        fallback_face = _fallback_face(entry)
        if fallback_face:
            if viewer is not None:
                return render_answers_line(viewer, fallback_face, message)
            return format_answers_line(fallback_face, message)
        if isinstance(plain, str) and plain:
            return plain
        if viewer is not None:
            return render_answers_line(viewer, "?", message)
        return format_answers_line("?", message)
    face = speaker_face_for_character(speaker, game, viewer=viewer)
    if viewer is not None:
        return render_answers_line(viewer, face, message)
    return format_answers_line(face, message)
