"""engine/systems/affects.py -- ephemeral character affect / state registry.

Runtime-only buff and state labels (Sneaking, Hidden, …). Producers attach
rows here; ``aff`` / ``affects`` lists them for the player. Not persisted on
logout -- stealth and similar systems re-stamp on enter.
"""

from __future__ import annotations

_AFFECT_ATTR = "affects"


def ensure_affects(character):
    """Attach an empty affect list when missing."""
    if character is None:
        return
    rows = getattr(character, _AFFECT_ATTR, None)
    if not isinstance(rows, list):
        character.affects = []


def clear_affects(character):
    """Drop every affect (tests / full reset)."""
    if character is None:
        return
    character.affects = []


def set_affect(character, affect_id, label, *, detail=None, category=None):
    """Replace any prior row with the same ``affect_id``."""
    if character is None or not affect_id:
        return
    ensure_affects(character)
    remove_affect(character, affect_id)
    row = {
        "id": str(affect_id),
        "label": str(label or affect_id),
    }
    if detail:
        row["detail"] = str(detail)
    if category:
        row["category"] = str(category)
    character.affects.append(row)


def remove_affect(character, affect_id):
    """Drop one affect by id."""
    if character is None or not affect_id:
        return
    ensure_affects(character)
    aid = str(affect_id)
    character.affects = [
        row for row in character.affects
        if str(row.get("id") or "") != aid
    ]


def has_affect(character, affect_id):
    """True when ``affect_id`` is active."""
    if character is None or not affect_id:
        return False
    ensure_affects(character)
    aid = str(affect_id)
    return any(str(row.get("id") or "") == aid for row in character.affects)


def list_affects(character):
    """Shallow copy of active affect rows."""
    if character is None:
        return []
    ensure_affects(character)
    return list(character.affects)


def _resolve_game(character, game):
    """Pick a Game for ETA math from session or room."""
    if game is not None:
        return game
    game = getattr(getattr(character, "session", None), "game", None)
    if game is not None:
        return game
    room = getattr(character, "location", None)
    if room is not None:
        return getattr(room, "game", None)
    return None


def format_affects(
    character,
    *,
    screenreader=False,
    game=None,
    public_only=False,
):
    """Player-facing multi-line summary for ``aff`` / ``affects``."""
    from engine import style

    game = _resolve_game(character, game)
    rows = list_affects(character)
    # Game hooks may append derived rows (timed buffs, combat conditions, …).
    from engine import hooks
    extra = hooks.extra_affect_rows(character) or []
    if extra:
        seen = {str(r.get("id") or "") for r in rows}
        for row in extra:
            rid = str(row.get("id") or "")
            if rid and rid not in seen:
                rows.append(dict(row))
                seen.add(rid)

    buff_lines = []
    debuff_lines = []
    state_lines = []

    for row in rows:
        rid = str(row.get("id") or "")
        category = str(row.get("category") or "")

        # Stealth / lifestyle runtime rows — States band; hidden from others.
        if category in ("stealth", "state") or rid in (
            "hidden", "sneaking", "lifestyle:drunkenness",
        ):
            if public_only:
                continue
            label = str(row.get("label") or rid or "?")
            detail = row.get("detail")
            if detail:
                state_lines.append(f"{label} -- {detail}")
            else:
                state_lines.append(label)
            continue

        buff_line = row.get("buff_line")
        polarity = str(row.get("polarity") or "")

        if category == "timed" or buff_line:
            if public_only and not bool(row.get("public", True)):
                continue
            line = str(buff_line or row.get("label") or rid)
            if polarity == "debuff":
                debuff_lines.append(line)
            else:
                buff_lines.append(line)
            continue

        # Legacy hook rows (KO, Accord, combat conditions) until Wave 3.
        label = str(row.get("label") or rid or "?")
        detail = row.get("detail")
        if detail:
            debuff_lines.append(f"{label} -- {detail}")
        else:
            debuff_lines.append(label)

    if not buff_lines and not debuff_lines and not state_lines:
        return "You have no active affects."

    body = []
    if buff_lines:
        body.append("Buffs")
        for line in buff_lines:
            body.append(f"  {line}")
    if debuff_lines:
        body.append("Debuffs")
        for line in debuff_lines:
            body.append(f"  {line}")
    if state_lines:
        body.append("States")
        for line in state_lines:
            body.append(f"  {line}")

    return style.format_tome(
        "Active affects", body, screenreader=screenreader,
    )
