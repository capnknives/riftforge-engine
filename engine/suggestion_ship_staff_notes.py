"""Staff reply lines on suggestion tickets when a Ship suggestion deploy lands.

``deploy_notify`` calls ``append_ship_staff_note`` after marking a
suggestion resolved so reporters see a squash-style note on the ticket.
"""

from __future__ import annotations

from engine import reports

# Id-specific lines for known ships (override generic summary when present).
_BY_ID = {
    427: (
        "Shipped: consume mob uses the same mob/hostile alias as attack "
        "(first hostile NPC in the room)."
    ),
    446: (
        "Shipped: your own emote lines default to your character name "
        "(config emote third off restores You)."
    ),
    476: (
        "Shipped: queued press, guard, unleash, and weave honor the "
        "momentum you committed even if the gauge dips before the beat."
    ),
    479: (
        "Shipped: living consume chews apply bite damage each use "
        "(signature cooldown still applies)."
    ),
    497: (
        "Shipped: Spirit Magic Grandmaster portal heaven and portal "
        "purgatory (chalk + salt + sulfur; cast veil before heaven). "
        "Elemental Grandmaster still opens heaven and hell."
    ),
}


def note_for_suggestion(suggestion_id: int, summary: str = "") -> str:
    """Pick the staff-facing note text for one suggestion id."""
    custom = _BY_ID.get(int(suggestion_id))
    if custom:
        return custom
    body = (summary or "").strip()
    if body:
        return f"Shipped: {body}"
    return "Shipped on main."


def append_ship_staff_note(
    suggestion_id: int,
    *,
    summary: str = "",
    directory: str = ".",
    author_key: str = "staff",
) -> bool:
    """Append one staff comment if this ticket does not already have it."""
    text = note_for_suggestion(suggestion_id, summary)
    try:
        entry = reports.get_by_id(
            reports.SUGGEST, int(suggestion_id), directory=directory,
        )
    except Exception:
        entry = None
    if entry is None:
        return False
    for msg in entry.get("messages") or []:
        if not msg.get("staff"):
            continue
        prior = (msg.get("text") or "").strip()
        if prior == text or prior.startswith("Shipped:"):
            return False
    try:
        reports.append_comment(
            reports.SUGGEST,
            int(suggestion_id),
            author_key,
            text,
            directory=directory,
            staff=True,
        )
    except (ValueError, IndexError, reports.ReportsIOError):
        return False
    return True
