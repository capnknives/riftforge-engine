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

    God bilocate twins and sleep dream-selves borrow the Earth owner's
    account + OOC pref (bug reports 1149, 1643) so channel traffic does
    not leak the Threshold copy's character face while the Session rides
    the dream-self.

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

    Immersion catalog cast (Chuck, Dean, …) always OOC as the character
    legal name — never the owning staff account from ``config oocname
    account``, including ``gm force <cast> ooc`` and Author nudges.
    Staff ``gm on`` spirit form still reads ``Accountname(GM)`` via
    ``_staff_form_label`` above; a cast body only gets ``(GM)`` when that
    body itself is in staff form (gmspirit / gm_mode), not from occupy
    eyes/powers alone.

    Player roster alts (non-immersion) still honor ``config oocname``.
    """
    try:
        from engine import hooks as hooks_mod

        character = hooks_mod.resolve_account_character(character, game)
    except Exception:
        pass
    try:
        from command_support import _presence_face, _staff_form_label

        if _staff_form_label(character):
            # Fixed staff OOC face — skip _display_name so seeaccounts /
            # (Group) never pile onto Accountname(GM).
            return f"{_presence_face(character)}(GM)"
    except Exception:
        pass
    if getattr(character, "immersion", False):
        from command_support import _maybe_append_account_tag, _presence_face

        face = _presence_face(character)
        if viewer is not None:
            face = _maybe_append_account_tag(face, character, viewer)
        return face
    try:
        from engine import accounts as accounts_mod

        occupy_face = accounts_mod.ooc_account_label(game, character)
        if occupy_face:
            return occupy_face
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
    """Paint one OOC line for *character* (or plain when SR / color off).

    Default sighted color is the whole line bright aqua (role ``ooc``,
    RGB 0,255,200) so the channel cannot hide in room prose. Player text
    is never run through ``paint_layered`` (URLs stay clickable via
    ``paint_preserving_urls``). ``config channel ooc <role>`` still paints
    the whole line a different color. Author nudges stay gold with silver
    verb pops. The official Mudlet package also recolors ``((OOC))`` lines
    to the same RGB in the main window.
    """
    from engine import display_prefs
    from engine import style

    display_prefs.ensure_display_defaults(character)
    plain = display_prefs.format_ooc_chat(character, face, message, kind=kind)
    if getattr(character, "screenreader", False):
        return plain
    if not getattr(character, "use_color", True):
        return plain
    if kind == OOC_KIND_AUTHOR_NUDGE:
        body = _highlight_author_nudge_verbs(message)
        template = format_ooc_line(face, body, kind=kind)
        return style.paint_layered_for(character, "gold", template)
    role = display_prefs.channel_role(character, "ooc", default="ooc")
    depth = display_prefs.color_depth(character)
    return style.paint_preserving_urls(role, plain, depth=depth)


def make_ooc_history_entry(
    speaker_key: str,
    message: str,
    *,
    kind: str = OOC_KIND_NORMAL,
    face: str | None = None,
    plain: str | None = None,
) -> dict:
    """Structured OOC ring entry (speaker key + message for per-viewer replay).

    *face* and *plain* cache the public label at send time so bare ``ooc``
    replay still shows ``Accountname(GM)`` after a folded ``gmspirit:`` row
    is no longer on the live roster.
    """
    entry = {"speaker": speaker_key, "message": message}
    if kind != OOC_KIND_NORMAL:
        entry["kind"] = kind
    cached_face = (face or "").strip()
    if cached_face:
        entry["face"] = cached_face
    cached_plain = (plain or "").strip()
    if cached_plain:
        entry["plain"] = cached_plain
    return entry


def _ooc_fallback_face(entry: dict) -> str | None:
    """Public OOC label stored at send time (when speaker lookup may fail later)."""
    face = entry.get("face")
    if isinstance(face, str) and face.strip():
        return face.strip()
    return None


def broadcast_ooc(
    game,
    speaker,
    message,
    *,
    speaker_session=None,
    kind: str = OOC_KIND_NORMAL,
    skip_discord_mirror: bool = False,
):
    """Send one OOC line to every connected session; record history + Discord.

    *speaker* is a Character (``speaker.key`` is stored in the ring buffer).
    When no session receives the line but *speaker_session* is set, deliver
    to that session anyway (``cmd_ooc`` parity).

    *kind* ``author_nudge`` uses gold + ``[AUTHOR]`` (Chuck bug/suggest lines).

    *skip_discord_mirror* avoids re-posting lines that already arrived from
    Discord (prevents webhook echo loops).

    Returns True when at least one session received the line.
    """
    from engine import gmcp

    public_face = speaker_face_for_character(speaker, game)
    public_plain = format_ooc_line(public_face, message, kind=kind)
    entry = make_ooc_history_entry(
        speaker.key,
        message,
        kind=kind,
        face=public_face,
        plain=public_plain,
    )
    channel_history.append(
        game, "ooc", entry, gateway_plain=public_plain,
    )
    try:
        from engine import channel_transcript

        channel_transcript.record(
            "ooc",
            face=public_face,
            message=message,
            game=game,
            speaker=str(getattr(speaker, "key", "") or ""),
            extra={"kind": kind} if kind != OOC_KIND_NORMAL else None,
        )
    except Exception:
        pass
    delivered = False
    from engine.accounts import ooc_should_hide_from_viewer
    for session in list(getattr(game, "sessions", None) or []):
        other = getattr(session, "character", None)
        if other is None:
            continue
        if ooc_should_hide_from_viewer(other, game, speaker.key):
            continue
        face = speaker_face_for_character(speaker, game, viewer=other)
        line = render_ooc_line(other, face, message, kind=kind)
        # Blank line after OOC is part of the same captured burst -- skip
        # both when the browser HUD gags captured comms from the main log.
        gmcp.deliver_comm(session, "ooc", message, face, line, "")
        delivered = True
    if not delivered and speaker_session is not None:
        other = speaker
        face = speaker_face_for_character(speaker, game, viewer=other)
        line = render_ooc_line(other, face, message, kind=kind)
        gmcp.deliver_comm(speaker_session, "ooc", message, face, line, "")
        delivered = True
    if not skip_discord_mirror:
        try:
            from engine import discord_bridge

            discord_bridge.schedule_ooc(public_plain)
        except Exception as exc:
            print(f"[discord_bridge] ooc schedule skipped: {exc}", flush=True)
    return delivered


def broadcast_ooc_from_face(
    game,
    face: str,
    message: str,
    *,
    skip_discord_mirror: bool = False,
    from_discord: bool = False,
    respect_mutes: bool = True,
) -> bool:
    """Send OOC as a named face without a Character speaker.

    Used for Discord relay and system staff lines (deploy heads-up from
    ``Ash(GM)``). ``from_discord`` marks the history entry so it is not
    mirrored back out; ``respect_mutes`` applies account OOC ignore when
    the face matches a known account name.
    """
    from engine import gmcp

    public_plain = format_ooc_line(face, message)
    entry = {
        "speaker": None,
        "message": message,
        "plain": public_plain,
    }
    if from_discord:
        entry["from_discord"] = True
    channel_history.append(
        game, "ooc", entry, gateway_plain=public_plain,
    )
    try:
        from engine import channel_transcript

        channel_transcript.record(
            "ooc",
            face=face,
            message=message,
            game=game,
            extra={"from_discord": True} if from_discord else None,
        )
    except Exception:
        pass
    delivered = False
    from engine.accounts import find_account, ooc_is_blocked, account_for_session_character
    for session in list(getattr(game, "sessions", None) or []):
        other = getattr(session, "character", None)
        if other is None:
            continue
        if respect_mutes:
            listener = account_for_session_character(game, other)
            if listener is not None:
                discord_acct = find_account(game, face)
                if (
                    discord_acct is not None
                    and ooc_is_blocked(listener, discord_acct.name)
                ):
                    continue
        line = render_ooc_line(other, face, message)
        gmcp.deliver_comm(session, "ooc", message, face, line, "")
        delivered = True
    if not skip_discord_mirror:
        try:
            from engine import discord_bridge

            discord_bridge.schedule_ooc(public_plain)
        except Exception as exc:
            print(f"[discord_bridge] ooc schedule skipped: {exc}", flush=True)
    return delivered


def broadcast_ooc_from_discord(game, face: str, message: str) -> bool:
    """Relay one Discord #ooc line into the global OOC channel.

    Uses a synthetic history entry (no Character speaker) and never
    mirrors back out to Discord.
    """
    return broadcast_ooc_from_face(
        game,
        face,
        message,
        skip_discord_mirror=True,
        from_discord=True,
        respect_mutes=True,
    )


def format_ooc_history_entry(entry, viewer, game) -> str:
    """Render one history entry for *viewer* (legacy plain strings pass through)."""
    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return str(entry)
    message = str(entry.get("message") or "")
    kind = entry.get("kind") or OOC_KIND_NORMAL
    speaker_key = entry.get("speaker")
    plain = entry.get("plain")
    if not speaker_key and isinstance(plain, str) and plain:
        return plain
    if not speaker_key or game is None:
        if viewer is not None:
            return render_ooc_line(viewer, "?", message, kind=kind)
        return format_ooc_line("?", message, kind=kind)
    finder = getattr(game, "find_character", None)
    speaker = finder(speaker_key) if callable(finder) else None
    if speaker is None:
        fallback_face = _ooc_fallback_face(entry)
        if fallback_face:
            if viewer is not None:
                return render_ooc_line(
                    viewer, fallback_face, message, kind=kind,
                )
            return format_ooc_line(fallback_face, message, kind=kind)
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
    """True when this session may use staff stitch (wiznet / gm) while IPC is down.

    Gateway ``bound`` metadata uses this. Do **not** call ``_is_staff_gm``
    here -- that filter skips immersion catalog bodies so who / city-threat
    stay IC. Occupying Gabriel / Ash still has GM verbs (``_is_gm`` via
    staff_account / staff_occupy_account / playcast). Using the who-strip
    filter stamped ``staff_gm`` False and copyover ``gm`` replied
    "You aren't a GM."
    """
    from command_support import _is_gm

    character = getattr(session, "character", None)
    if character is None:
        return False
    return _is_gm(character)


def export_plain_history(game) -> list[str]:
    """Plain OOC lines for gateway stitch replay (delegates to channel_history)."""
    from engine import channel_history

    snapshot = channel_history.export_gateway_snapshot(game)
    return list(snapshot.get("ooc") or [])
