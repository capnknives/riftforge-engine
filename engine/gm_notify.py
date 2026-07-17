"""
gm_notify.py -- dark-green GM staff channel for ops events.

Classic MUD feel: online staff GMs get short absinthe-green lines when
someone starts chargen, connects, disconnects, or files a bug/suggest.
Never the only signal -- every line keeps a plain ``[GM]`` prefix so
``color off`` still reads clearly (docs/SYSTEMS_DESIGN.md section 8).

Opt-out: Character.gm_notify False (GM verb ``gmnotify off``). Immersion
cast bodies are skipped -- same staff filter as ``who``'s GM strip
(``_is_staff_gm``).
"""

from engine.command_support import _is_staff_gm
from engine import style


def peer_host(session):
    """Best-effort client IP/host from the session writer, or None.

    asyncio StreamWriter.get_extra_info('peername') is usually
    (host, port) for TCP. Mocks and copyover edge cases may lack it --
    callers omit the ``from …`` clause when this returns None.
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


def format_from(session):
    """Return `` from 1.2.3.4`` or `` `` (empty) when peer is unknown."""
    host = peer_host(session)
    if host:
        return f" from {host}"
    return ""


def paint_gm_line(message):
    """Wrap a staff line in absinthe green with a plain ``[GM]`` tag.

    ``paint`` embeds ANSI; Session.send strips it when use_color is off,
    leaving the readable ``[GM] …`` text.
    """
    return style.paint("absinthe_green", f"[GM] {message}")


def ping_gms(game, message, *, exclude=None):
    """Send one dark-green staff line to every opted-in online staff GM.

    exclude -- optional Character who should not receive this ping (e.g.
    the player who just disconnected, or a GM who triggered their own
    event). Immersion cast is never pinged.
    """
    sessions = getattr(game, "sessions", None) or []
    line = paint_gm_line(message)
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
        send(line)
