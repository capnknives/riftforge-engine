"""questions_channel.py -- global player-questions net (OOC-adjacent formatting)."""

from __future__ import annotations

from engine import channel_history


def speaker_face_for_character(character, game, viewer=None):
    """Reuse OOC identity rules for the questions net."""
    from engine import ooc_channel

    return ooc_channel.speaker_face_for_character(character, game, viewer=viewer)


def format_questions_line(face: str, message: str) -> str:
    """Plain questions line -- ``((QUESTIONS))`` prefix."""
    return f"((QUESTIONS)) [{face}]: {message}"


def render_questions_line(character, face: str, message: str) -> str:
    """Paint one questions line for *character* (or plain when SR / color off).

    Layered ``((QUESTIONS))`` chrome (dark-grey parens, gold letters,
    parchment body via the ``exit`` role). OOC itself is whole-line aqua
    -- this net stays gothic so tutoring does not look like chat. Player
    text is never tag-parsed. ``config channel questions <role>`` paints
    the whole line.
    """
    from engine import display_prefs
    from engine import style

    display_prefs.ensure_display_defaults(character)
    plain = display_prefs.format_questions_chat(character, face, message)
    if getattr(character, "screenreader", False):
        return plain
    if not getattr(character, "use_color", True):
        return plain
    if display_prefs.wants_plain_comms(character):
        return display_prefs.paint_plaincomms_channel_lead(
            character, plain, "Questions.", body_role="exit",
        )
    prefix = "((QUESTIONS))"
    if not plain.startswith(prefix):
        return style.paint_for(character, "exit", plain)
    return display_prefs.paint_double_bracket_channel_line(
        character, "QUESTIONS", plain[len(prefix):],
        channel="questions", body_role="exit",
    )


def make_questions_history_entry(
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


def broadcast_questions(
    game,
    speaker,
    message,
    *,
    speaker_session=None,
    prefix: str | None = None,
    face: str | None = None,
    speaker_key: str | None = None,
    enqueue_ash: bool = True,
):
    """Send one questions line to every eligible session; record history.

    *prefix* prepends to the message body (e.g. ``[Query #3]`` cross-posts).
    *face* / *speaker_key* override the speaker label (Ash helper replies).
    Returns True when at least one session received the line.
    """
    from engine import gmcp

    body = (message or "").strip()
    if prefix:
        body = f"{prefix} {body}".strip()
    if not body:
        return False
    if face:
        public_face = str(face).strip() or "?"
    else:
        public_face = speaker_face_for_character(speaker, game) if speaker else "?"
    public_plain = format_questions_line(public_face, body)
    if speaker_key is None:
        speaker_key = getattr(speaker, "key", None) if speaker is not None else None
    entry = make_questions_history_entry(
        speaker_key,
        body,
        face=public_face,
        plain=public_plain,
    )
    channel_history.append(
        game, "questions", entry, gateway_plain=public_plain,
    )
    try:
        from engine import channel_transcript

        channel_transcript.record(
            "questions",
            face=public_face,
            message=body,
            game=game,
            speaker=str(speaker_key or ""),
        )
    except Exception:
        pass
    delivered = False
    from engine import channels
    from engine.accounts import ooc_should_hide_from_viewer

    for session in list(getattr(game, "sessions", None) or []):
        other = getattr(session, "character", None)
        if other is None:
            continue
        spec = channels.get_channel("questions")
        if spec is not None and not channels.can_hear(other, spec, game):
            continue
        if speaker_key and ooc_should_hide_from_viewer(other, game, speaker_key):
            continue
        if speaker is not None and not face:
            line_face = speaker_face_for_character(speaker, game, viewer=other)
        else:
            line_face = public_face
        line = render_questions_line(other, line_face, body)
        session.send(line)
        session.send("")
        gmcp.push_comm(session, "questions", body, line_face)
        delivered = True
    if not delivered and speaker_session is not None and speaker is not None:
        line_face = public_face
        if not face:
            line_face = speaker_face_for_character(speaker, game, viewer=speaker)
        line = render_questions_line(speaker, line_face, body)
        speaker_session.send(line)
        speaker_session.send("")
        delivered = True
    if enqueue_ash and speaker is not None and speaker_key != "ash-help":
        try:
            from engine import ash_help_inbox

            ash_help_inbox.enqueue_question(game, speaker, body)
        except Exception:
            pass
    return delivered


def format_questions_history_entry(entry, viewer, game) -> str:
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
            return render_questions_line(viewer, "?", message)
        return format_questions_line("?", message)
    finder = getattr(game, "find_character", None)
    speaker = finder(speaker_key) if callable(finder) else None
    if speaker is None:
        fallback_face = _fallback_face(entry)
        if fallback_face:
            if viewer is not None:
                return render_questions_line(viewer, fallback_face, message)
            return format_questions_line(fallback_face, message)
        if isinstance(plain, str) and plain:
            return plain
        if viewer is not None:
            return render_questions_line(viewer, "?", message)
        return format_questions_line("?", message)
    face = speaker_face_for_character(speaker, game, viewer=viewer)
    if viewer is not None:
        return render_questions_line(viewer, face, message)
    return format_questions_line(face, message)
