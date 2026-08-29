"""session_attach.py -- heal Session <-> Character wiring.

Live bug class: ``character.session`` cleared while ``session.character``
and the play loop still run — OOC works (broadcast walks ``game.sessions``)
but verbs that call ``character.session.send`` fail until relog.

Stdlib only; no game imports.
"""

from __future__ import annotations


def detach_stale_sessions(character, game, winner):
    """Drop other ``game.sessions`` rows still bound to *character*.

    Gateway welcome can open several held TCPs for the same login body after
    client reconnect loops; reattach only overwrites ``character.session`` and
    leaves older Session objects in ``game.sessions`` (staff ``gm users`` lists
    the same name repeatedly).
    """
    if character is None or winner is None or game is None:
        return 0
    sessions = getattr(game, "sessions", None)
    if not sessions:
        return 0
    removed = 0
    for session in list(sessions):
        if session is winner:
            continue
        if getattr(session, "character", None) is not character:
            continue
        session.alive = False
        session.character = None
        try:
            sessions.remove(session)
        except ValueError:
            pass
        writer = getattr(session, "writer", None)
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        removed += 1
    character.session = winner
    return removed


def sweep_duplicate_session_attaches(game):
    """Boot / post-welcome sweep: one live Session row per Character attach."""
    sessions = getattr(game, "sessions", None)
    if not sessions:
        return 0
    by_char: dict[int, list] = {}
    for session in list(sessions):
        char = getattr(session, "character", None)
        if char is None:
            continue
        by_char.setdefault(id(char), []).append((char, session))
    removed = 0
    for pairs in by_char.values():
        if len(pairs) <= 1:
            continue
        char = pairs[0][0]
        winner = getattr(char, "session", None)
        if winner is None or getattr(winner, "character", None) is not char:
            winner = None
            for _, session in pairs:
                if getattr(session, "alive", False):
                    winner = session
                    break
            if winner is None:
                winner = pairs[-1][1]
        removed += detach_stale_sessions(char, game, winner)
    return removed


def find_session_for_character(character, game):
    """Return the live Session whose ``.character`` is *character*, if any."""
    if character is None or game is None:
        return None
    for session in list(getattr(game, "sessions", None) or []):
        if not getattr(session, "alive", False):
            continue
        if getattr(session, "character", None) is character:
            return session
    return None


def _session_counts_as_live_player(sess, character):
    """True when *sess* is a real client bound to *character* (not SilentSession)."""
    if sess is None or character is None:
        return False
    try:
        from engine.npc_act import SilentSession
        if isinstance(sess, SilentSession):
            return False
    except ImportError:
        pass
    if not getattr(sess, "alive", False):
        return False
    return getattr(sess, "character", None) is character


def has_live_player_session(character, game):
    """True when *character* has a live client Session (direct or via game.sessions).

    Covers half-cleared attach where ``character.session`` is None but
    ``game.sessions`` still holds an alive Session for this body — OOC and
    room broadcasts work, but Cadence-lite must not calendar-snap them home.
    """
    if character is None:
        return False
    cur = getattr(character, "session", None)
    if _session_counts_as_live_player(cur, character):
        return True
    found = find_session_for_character(character, game)
    return _session_counts_as_live_player(found, character)


def heal_character_session(character, game, *, preferred_session=None):
    """Reattach *character.session* when the reverse link on Session still holds.

    *preferred_session* wins when its ``.character`` is *character* (play loop).

    Returns the Session now bound on *character*, or None when no live attach
    exists (Echoes, NPC dispatch, disconnected bodies).
    """
    if character is None:
        return None
    cur = getattr(character, "session", None)
    if cur is not None:
        if getattr(cur, "character", None) is character:
            return cur
        if getattr(cur, "alive", False):
            # Another live Session owns the pointer — do not steal it.
            return None
        # Stale dead Session left on the Character — drop and re-scan.
        cur.character = None
        character.session = None

    if preferred_session is not None:
        if (
            getattr(preferred_session, "alive", False)
            and getattr(preferred_session, "character", None) is character
        ):
            character.session = preferred_session
            _ensure_session_listed(preferred_session, game)
            return preferred_session

    found = find_session_for_character(character, game)
    if found is not None:
        character.session = found
        return found
    return None


def _ensure_session_listed(session, game):
    """Keep ``game.sessions`` aligned after a heal (reattach drift)."""
    if session is None or game is None:
        return
    sessions = getattr(game, "sessions", None)
    if sessions is not None and session not in sessions:
        sessions.append(session)


def ensure_play_session_ready(session):
    """Guard the play loop before dispatch.

    Returns False when this Session should exit play (takeover cleared
    ``session.character``, or another live Session won the Character).
  """
    char = getattr(session, "character", None)
    if char is None:
        return False

    game = getattr(session, "game", None)
    healed = heal_character_session(char, game, preferred_session=session)
    if healed is not session:
        owner = getattr(char, "session", None)
        if (
            owner is not None
            and owner is not session
            and getattr(owner, "alive", False)
        ):
            session.alive = False
        return False
    return True


def send_to_character(character, message, game=None):
    """Best-effort player send that heals a half-cleared attach first.

    Returns True when a line was queued on a Session, False otherwise.
    """
    if character is None:
        return False
    if game is None:
        sess = getattr(character, "session", None)
        if sess is not None:
            game = getattr(sess, "game", None)
    session = heal_character_session(character, game)
    if session is None:
        return False
    session.send(message)
    return True
