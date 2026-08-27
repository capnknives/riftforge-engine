"""
login_briefing.py -- post-look login notification spacing.

After the first ``look``, deferred session attach may deliver several
unrelated blocks (mail, changelog [NEWS], Echo journal, staff nags, …).
Each block is its own ``session.send()`` call. Without intervention they
glue into one wall of text.

``begin`` / ``end`` wrap ``Session.send`` for the deferred-attach window
only: airy players (and screenreader users) get a blank line before each
non-empty block, including one after the room description. ``config
spacing packed`` keeps the old tight login stack for sighted players.

Tips are armed only after ``end`` (see supers/bootstrap.py and
player_tips.tick_tips) so a due [TIP] never interrupts the briefing.
"""

from __future__ import annotations

_ATTR_ACTIVE = "_login_briefing_active"
_ATTR_ORIGINAL = "_login_briefing_original_send"
_ATTR_FIRST = "_login_briefing_first_block"


def wants_briefing_spacing(character) -> bool:
    """Whether login blocks should be separated by blank lines."""
    if character is None:
        return True
    if getattr(character, "screenreader", False):
        # Blank lines help TTS chunk distinct login sections.
        return True
    from engine import display_prefs

    display_prefs.ensure_display_defaults(character)
    return display_prefs.normalize_output_spacing(
        getattr(character, "output_spacing", display_prefs.OUTPUT_SPACING_AIRY),
    ) == display_prefs.OUTPUT_SPACING_AIRY


def active(session) -> bool:
    """True while the post-look login briefing wrapper is installed."""
    return session is not None and bool(getattr(session, _ATTR_ACTIVE, False))


def begin(session, character) -> None:
    """Install spaced ``send`` for deferred login notifications."""
    if session is None or active(session):
        return
    if not wants_briefing_spacing(character):
        setattr(session, _ATTR_ACTIVE, True)
        return

    setattr(session, _ATTR_ACTIVE, True)
    setattr(session, _ATTR_FIRST, True)
    original = session.send
    setattr(session, _ATTR_ORIGINAL, original)

    def briefing_send(message):
        is_empty = not str(message).strip()
        if is_empty:
            original(message)
            return
        if getattr(session, _ATTR_FIRST, True):
            setattr(session, _ATTR_FIRST, False)
            original("")
        else:
            original("")
        original(message)

    session.send = briefing_send


def end(session) -> None:
    """Restore normal ``send`` after deferred login notifications."""
    if session is None:
        return
    if not getattr(session, _ATTR_ACTIVE, False):
        return
    original = getattr(session, _ATTR_ORIGINAL, None)
    if original is not None:
        session.send = original
    setattr(session, _ATTR_ACTIVE, False)
    for attr in (_ATTR_ORIGINAL, _ATTR_FIRST):
        if hasattr(session, attr):
            delattr(session, attr)
