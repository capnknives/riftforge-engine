"""
claim_board.py -- generic posted -> claimed -> assist board kit (engine kernel).

House-job boards (any game's posted-job board with a claim-and-assist
workflow) all share one shape: a job is posted, one actor claims it,
other eligible actors join as assistants, and the board needs to show
who holds it in player-facing prose. That plumbing does not know
anything about what kind of job it is -- it just reads/writes a plain
job dict's ``claimer_key`` / ``assistants`` fields and looks up a
display name for whichever character key is on the claim.

``claimer_label`` reaches into ``engine.char_index`` /
``engine.command_support`` (already engine-side) to resolve a claimer
key to a display name -- that is a same-layer import, not a purity
violation.

The game layer keeps its own job-phase vocabulary (e.g. a status label
function) and calls this kernel for the claimer/assistant lines.
Design: docs/plans/engine_liquid_flavor.md (Wave 2 Part B).
"""

from __future__ import annotations


def assistant_keys(job, *, key="assistants"):
    """Return the list of assistant character keys stored on *job*.

    *key* defaults to ``"assistants"`` but is overridable so a future
    board that names its list differently does not have to rename its
    data to fit this helper.
    """
    raw = job.get(key)
    if not raw:
        return []
    # Coerce every entry to str -- persisted JSON can hand back mixed
    # types (e.g. an old int character id) and callers expect keys.
    return [str(k) for k in raw if k]


def format_assistants_line(keys, *, label="Assist"):
    """One board line listing assistant keys, or ``""`` when there are none."""
    if not keys:
        return ""
    if len(keys) == 1:
        return f"  {label}: {keys[0]}"
    return f"  {label}: {', '.join(keys)}"


def claimer_label(game, claimer_key, viewer=None):
    """Public display name for a board claimer key.

    Prefers "you" when the viewer is the claimer, then a resolved
    character display name, and falls back to the bare key only when
    the character cannot be found (offline / never-loaded) -- never a
    dig/VNUM-style internal id, since this is player-facing prose.
    """
    if not claimer_key:
        return ""
    viewer_key = getattr(viewer, "key", None) if viewer is not None else None
    if viewer_key and viewer_key == claimer_key:
        return "you"
    if game is not None:
        from engine.char_index import find_character_by_key
        from engine.command_support import _display_name

        body = find_character_by_key(game, claimer_key)
        if body is not None:
            return _display_name(body)
    return claimer_key


def claim_or_join_lines(
    job,
    viewer,
    *,
    claimer_key_field,
    take_verb,
    join_verb,
    track_verb,
    help_topic,
    claim_roles,
    held_lines=None,
    claimer_label_fn,
):
    """Shared claimer / assistant lines for a posted-or-claimed board job.

    *job* is a plain dict read by field name (``claimer_key_field`` lets
    callers keep whatever key name their job dict already uses, e.g.
    ``"claimer_key"``). *claimer_label_fn* is a ``(claimer_key, viewer=None)
    -> str`` callable -- callers close over their own ``game`` (and, if
    they want, a non-default label strategy) rather than this kernel
    taking a bare ``game`` positional it has no other use for; pass
    ``functools.partial(claimer_label, game)`` (module-level ``claimer_label``
    above) for the stock behavior. ``held_lines`` overrides the "you hold
    this claim" branch's text (a list of lines) when a board wants its
    own held-claim wording; otherwise a generic fallback is used.
    """
    claimer = job.get(claimer_key_field)
    if not claimer:
        return [
            f"Type '{take_verb}' to claim ({claim_roles}). "
            f"Then '{track_verb}'."
        ]
    if viewer is not None and claimer == getattr(viewer, "key", None):
        if held_lines is not None:
            return list(held_lines)
        return [
            "  You hold this claim.",
            f"  '{track_verb}' for a lead, then work the site. "
            f"See 'help {help_topic}'.",
        ]
    label = claimer_label_fn(claimer, viewer=viewer)
    lines = [f"  Claimer: {label}"]
    assist = format_assistants_line(assistant_keys(job)).strip()
    if assist:
        lines.append(assist)
    lines.append(
        f"Type '{join_verb}' to back up the claim ({claim_roles}). "
        f"See 'help {help_topic}'."
    )
    return lines
