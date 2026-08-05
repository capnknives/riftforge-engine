"""
gm_notify.py -- dark-green GM staff channel for ops events.

Classic MUD feel: online staff GMs get short absinthe-green lines when
someone starts or finishes chargen, connects, disconnects, deletes a
character, changes password, links an account, breaks Tier, or files a
bug/suggest.
Never the only signal -- every line keeps a plain ``[GM]`` prefix so
``color off`` still reads clearly (docs/SYSTEMS_DESIGN.md section 8).

Opt-out: Character.gm_notify False (GM verb ``gmnotify off``). Immersion
cast bodies are skipped -- same staff filter as ``who``'s GM strip
(``_is_staff_gm``).

Client IPs: ``peer_host`` always returns the real address when known
(banlist, head-GM tooling). Connect/disconnect ``from …`` clauses and
``gm users`` / ``gm host`` peer columns are head-GM only -- junior staff
do not see player IPs on the ops channel.
"""

from engine.command_support import _is_head_gm, _is_staff_gm
from engine import channel_history
from engine import style

# Re-export for callers that still import from gm_notify.
WIZNET_HISTORY_MAX = channel_history.WIZNET_HISTORY_MAX


def peer_host(session):
    """Best-effort client IP/host from the session writer, or None.

    asyncio StreamWriter.get_extra_info('peername') is usually
    (host, port) for TCP. Behind the connection gateway the game writer
    is a GatewaySessionWriter that forwards the real public peer from
    CTRL open/welcome. Mocks and copyover edge cases may lack it --
    callers omit the ``from …`` clause when this returns None.

    This is the raw address (banlist, head-GM inspect). Display helpers
    must go through ``format_from`` / ``peer_host_for_viewer`` so junior
    GMs never see it on staff lines.
    """
    writer = getattr(session, "writer", None)
    if writer is None:
        return None
    # get_extra_info exists on real writers; FakeSession has no writer.
    get_info = getattr(writer, "get_extra_info", None)
    if get_info is None:
        return None
    peer = get_info("peername")
    if isinstance(peer, tuple) and peer:
        # IPv4/IPv6: first element is the address string.
        return str(peer[0])
    if isinstance(peer, str) and peer:
        return peer
    return None


def peer_host_for_viewer(session, viewer):
    """Return peer_host only when viewer is head_gm; else None.

    Used by ``gm users`` / ``gm host`` so junior staff see ``-`` instead
    of a real client IP.
    """
    if viewer is None or not _is_head_gm(viewer):
        return None
    return peer_host(session)


def format_from(session, viewer=None):
    """Return `` from 1.2.3.4`` for head_gm viewers only; else empty.

    ``viewer`` is the staff Character receiving the ping. Without a
    head_gm viewer the clause is omitted so junior GMs (and callers that
    forget to pass a viewer) never leak player IPs into a shared line.
    """
    if viewer is None or not _is_head_gm(viewer):
        return ""
    host = peer_host(session)
    if host:
        return f" from {host}"
    return ""


def public_who(character, game=None, *, force_surname=True):
    """Legal public name for staff pings, with optional ``(account)`` suffix.

    When ``game`` is omitted, uses ``character.session.game`` when attached.
    """
    from engine.char_identity import legal_public_name

    who = legal_public_name(character, force_surname=force_surname)
    if game is None:
        session = getattr(character, "session", None)
        game = getattr(session, "game", None) if session is not None else None
    if game is not None:
        try:
            from engine import accounts as accounts_mod

            account = accounts_mod.account_for_character(game, character)
            if account is not None:
                who = f"{who}({account.display_name})"
        except Exception:
            pass
    return who


def paint_gm_line(message):
    """Wrap a staff line in absinthe green with a plain ``[GM]`` tag.

    ``paint`` embeds ANSI; Session.send strips it when use_color is off,
    leaving the readable ``[GM] …`` text.
    """
    return style.paint("absinthe_green", f"[GM] {message}")


def ping_gms(game, message, *, exclude=None, peer_session=None):
    """Send one dark-green staff line to every opted-in online staff GM.

    exclude -- optional Character who should not receive this ping (e.g.
    the player who just disconnected, or a GM who triggered their own
    event). Immersion cast is never pinged.

    peer_session -- optional Session whose client IP is appended via
    ``{from}`` in *message* (replaced per recipient). Head GM gets
    `` from 1.2.3.4``; junior staff get an empty string. If *message*
    has no ``{from}`` placeholder, it is sent unchanged to everyone.
    """
    sessions = getattr(game, "sessions", None) or []
    for session in list(sessions):
        other = getattr(session, "character", None)
        if other is None:
            continue
        if exclude is not None and other is exclude:
            continue
        # Staff only -- immersion GMs stay in-character on who/notify.
        if not _is_staff_gm(other):
            continue
        # Default on; gmnotify off flips this False (persisted).
        if not getattr(other, "gm_notify", True):
            continue
        send = getattr(session, "send", None)
        if send is None:
            continue
        text = message
        if peer_session is not None and "{from}" in message:
            text = message.replace(
                "{from}", format_from(peer_session, viewer=other)
            )
        send(paint_gm_line(text))


def paint_wiz_line(message):
    """Wrap a staff-chat line in absinthe green with a plain ``[WIZ]`` tag.

    Bidirectional Immortal channel (``wiznet``). Same color role as
    ``[GM]`` ops pings so color-off clients still read the tag.
    """
    return style.paint("absinthe_green", f"[WIZ] {message}")


def wiznet_speaker_label(speaker):
    """Public staff label for wiznet -- never ``gmspirit:`` / ``husk:`` keys."""
    from engine.command_support import _is_gm, _public_label
    from world import Character

    who = _public_label(speaker)
    # Mortal-body staff (``gm off``) still read as staff on wiznet.
    if (
        isinstance(speaker, Character)
        and _is_gm(speaker)
        and not who.endswith("(GM)")
    ):
        who = f"{who}(GM)"
    return who


def format_wiznet_plain(speaker, message):
    """Plain ``[WIZ] Name(GM): text`` line for history (no ANSI)."""
    who = wiznet_speaker_label(speaker)
    text = (message or "").strip()
    if not text:
        return None
    return f"[WIZ] {who}: {text}"


def format_wiznet_line(speaker, message):
    """Build one painted ``[WIZ] Name(GM): text`` line for staff chat."""
    plain = format_wiznet_plain(speaker, message)
    if plain is None:
        return None
    return style.paint("absinthe_green", plain)


def append_wiznet_history(game, plain_line):
    """Record one plain wiznet line on the global channel ring."""
    channel_history.append(game, "wiznet", plain_line, gateway_plain=plain_line)


def send_wiznet_history(character, game):
    """Replay the global wiznet ring buffer to one staff session."""
    session = getattr(character, "session", None)
    if session is None:
        return
    if channel_history.is_empty(game, "wiznet"):
        channel_history.send_empty_hint(character, "wiznet")
        return
    channel_history.send_replay_header(character, "wiznet")
    for plain in channel_history.entries(game, "wiznet"):
        session.send(channel_history.replay_wiznet_entry(plain))
    session.send("")


def wiznet_broadcast(game, speaker, message, *, exclude=None):
    """Send one ``[WIZ]`` line to every online staff GM (not immersion cast).

    Unlike ``ping_gms``, this ignores ``gm_notify`` opt-out -- wiznet is
    deliberate staff chat, not an ops ping you mute. Immersion cast stays
    out so in-character bodies never see the channel.
    """
    plain = format_wiznet_plain(speaker, message)
    if plain is None:
        return False
    append_wiznet_history(game, plain)
    line = style.paint("absinthe_green", plain)
    sessions = getattr(game, "sessions", None) or []
    sent = 0
    for session in list(sessions):
        other = getattr(session, "character", None)
        if other is None:
            continue
        if exclude is not None and other is exclude:
            continue
        if not _is_staff_gm(other):
            continue
        send = getattr(session, "send", None)
        if send is None:
            continue
        send(line)
        sent += 1
    return sent > 0
