"""
output_packet.py -- coalesce one game beat into one send per viewer.

Generic delivery buffer: engine-owned, no game imports. A consumer brackets
a beat with ``open_packet()`` / ``flush()``, and every line written for a
viewer inside that bracket is delivered as one ordered block instead of a
handful of separate writes.

Motivating case (SUPERS combat, ~9s beat): one beat can narrate a primary
swing, an ``[OFFHAND]`` beat, a ``[FLURRY]`` / ``[SWEEP]``, hunter
``[ARTS]``, a ``[COUNTER]``, a ``[RANGE]`` positioning tell, a gore
spatter broadcast and a wound status line. Each used to reach the client
as its own ``Session.send``, so one beat arrived as several interleaved
fragments -- in player words, "everything fires all at once".
Screenreader users feel it worst: TTS restarts on every fragment.

This is a **delivery buffer only**. It does not touch game state, prose
wording, or the order of lines within one viewer -- lines flush in exactly
the order they were staged, so cause still reads before effect. The hard
rule 5 split stays intact: the game builds its brief, renders it, then
hands finished text here.

Contract:

* ``open_packet()`` / ``flush()`` bracket one beat. They are re-entrant via
  a depth counter, so a nested narrator cannot flush the beat early.
* ``stage(...)`` returns True when it buffered the line. **False means no
  packet is open** and the caller must deliver the line itself -- that is
  what keeps a typed verb (a Path signature between beats) and direct smoke
  calls immediate instead of silently swallowed.
* Live ``Session.send`` (and smoke ``FakeSession.send``) call
  ``intercept_send`` first. While a beat is open, a raw ``session.send``
  joins the packet instead of jumping the wire -- that is what keeps
  autoloot / autotap / fightlog-end from printing before ``[EXECUTE]``
  (bug report 1743). Empty lines are not intercepted (blank coalescing
  stays on the live session).
* Consecutive identical lines for one viewer collapse to one. A per-swing
  side effect (blood spatter) that fires twice in a beat reads as one tell,
  the same dedupe-by-identical-tell rule room ``[RANGE]`` text already uses.
* Sighted ``config spacing airy`` (the default) still keeps **one send**,
  but joins staged events with a blank line so a swing, ``[EXECUTE]``,
  fuel tick, and death line do not glue into one wall. Packed spacing and
  screenreader stay tight (``\\r\\n`` only) so speech can read the round
  once. This is the same pref as room say / tell -- not a second combat
  switch.
* Single-threaded asyncio (hard rule 3) is why one module-level packet is
  safe: only the loop ever opens one, and it always closes it in a
  ``finally``.
"""

from __future__ import annotations

# Depth of nested open_packet() calls. 0 means "no beat is being buffered",
# which is the state every non-round code path (typed verbs, smokes) sees.
_depth = 0

# Buffered viewers for the open beat, keyed by id(character). A plain dict
# is deliberate: Python dicts keep insertion order, so viewers flush in the
# order they were first written to, and each viewer's own lines stay in
# chronological order. Value shape:
#   {"viewer": Character, "lines": [str, ...], "blank_after": bool}
_staged: dict[int, dict] = {}


def is_open():
    """True while a combat beat is being buffered (used by smokes/asserts)."""
    return _depth > 0


def open_packet():
    """Begin (or nest inside) one buffered combat beat.

    Callers must pair this with ``flush()`` in a ``finally`` so a raising
    narrator can never strand a player's combat text in the buffer.
    """
    global _depth
    _depth += 1


def stage(viewer, text, *, blank_after=False):
    """Buffer one already-painted line for `viewer`.

    Returns True when the line was buffered, False when no packet is open
    (the caller then delivers it the old way). ``blank_after`` requests one
    trailing blank line after the whole flushed block -- several stages
    asking for it still yield a single blank, because the paragraph gap
    belongs to the beat, not to each fragment inside it.
    """
    if _depth <= 0:
        return False
    if viewer is None or not isinstance(text, str) or not text:
        # Nothing worth buffering. Report True so the caller does not then
        # try to send the same empty/invalid payload directly.
        return True
    key = id(viewer)
    entry = _staged.get(key)
    if entry is None:
        # First line for this viewer in this beat.
        entry = {"viewer": viewer, "lines": [], "blank_after": False}
        _staged[key] = entry
    lines = entry["lines"]
    # Collapse a repeat of the line we just staged: per-swing side effects
    # (gore spatter, an identical range tell) fire once per swing, and two
    # swings in one beat should not print the same sentence twice.
    if not lines or lines[-1] != text:
        lines.append(text)
    if blank_after:
        entry["blank_after"] = True
    return True


def stage_paragraph(viewer, text):
    """Stage a paragraph-style line (blank line after the beat).

    Mirrors ``engine.snoop.tell_paragraph`` semantics for the buffered
    path: the caller falls back to that helper when this returns False.
    """
    return stage(viewer, text, blank_after=True)


def _join_staged_lines(viewer, lines):
    """Join one viewer's staged lines for a single ``Session.send``.

    Packed / screenreader: CRLF between lines (the original packed block).
    Sighted ``config spacing airy``: a blank line between each staged
    event. Telnet clients need CRLF paragraph breaks -- a bare ``\\n\\n``
    inside one colored payload is what made the old combatairy join look
    like a no-op on many clients.
    """
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    sep = "\r\n"
    try:
        from engine import display_prefs
        if display_prefs.wants_airy_spacing(viewer):
            sep = "\r\n\r\n"
    except Exception:
        # Smokes / sessionless stubs without display prefs stay packed.
        pass
    return sep.join(lines)


def intercept_send(viewer, text):
    """Absorb a ``Session.send`` into the open beat packet.

    Returns True when the line was buffered (caller must not write).
    Returns False when there is no open packet, the viewer is missing,
    or the payload is empty -- caller delivers the old way. Empty lines
    stay immediate so live blank-coalesce still sees them.
    """
    if viewer is None or not isinstance(text, str) or not text.strip():
        return False
    return stage(viewer, text)


def tell(viewer, text):
    """Stage `text` for `viewer` inside a beat, or deliver it right now.

    Convenience for call sites that would otherwise write straight to
    ``character.session`` and so jump ahead of the buffered block (a
    knock-out coaching tip printing before the swing that caused it).
    Falls back to ``engine.snoop.tell``, which is the same
    live-session-else-mirror-to-snoopers branch the rest of the game uses.
    """
    if viewer is None or not isinstance(text, str) or not text:
        return
    if stage(viewer, text):
        return
    from engine import snoop
    snoop.tell(viewer, text)


def flush():
    """Close one nesting level; deliver the beat when the outermost closes.

    Delivery uses ``engine.snoop.tell`` per viewer, which is the same
    live-session-else-mirror-to-snoopers branch the unbuffered combat paths
    used -- so an offline Echo with a GM watching still gets its viewpoint
    lines, exactly as before. The only observable change is that one beat
    is one write instead of several.
    """
    global _depth
    if _depth <= 0:
        # Defensive: an unbalanced flush must not go negative and start
        # swallowing later beats.
        _depth = 0
        return
    _depth -= 1
    if _depth > 0:
        # Still inside an outer beat -- keep buffering.
        return
    if not _staged:
        return
    # Take the buffer before delivering: a send that raises (dead socket)
    # must not leave stale lines to replay into the next beat.
    pending = list(_staged.values())
    _staged.clear()
    from engine import snoop

    for entry in pending:
        viewer = entry["viewer"]
        lines = entry["lines"]
        if not lines:
            continue
        # One write per viewer so the 9s beat stays one block. Airy
        # inserts paragraph gaps *inside* that write (see
        # _join_staged_lines); packed/SR stay a tight CRLF join.
        snoop.tell(viewer, _join_staged_lines(viewer, lines))
        if entry["blank_after"]:
            session = getattr(viewer, "session", None)
            if session is not None:
                # Paragraph gap goes to the live client only -- GMs should
                # not read empty "% Name> " noise (same rule as
                # snoop.tell_paragraph).
                session.send("")


def reset_for_tests():
    """Drop all buffered state (targeted smokes between scenarios)."""
    global _depth
    _depth = 0
    _staged.clear()
