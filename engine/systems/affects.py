"""engine/systems/affects.py -- ephemeral character affect / state registry.

Runtime-only buff and state labels (Sneaking, Hidden, …). Producers attach
rows here; ``aff`` / ``affects`` lists them for the player. Not persisted on
logout -- stealth and similar systems re-stamp on enter.
"""

from __future__ import annotations

import re

_AFFECT_ATTR = "affects"

# Trailing clock on a long buff_line: "(~3m left)" / "(2 rounds left)".
_ETA_TAIL = re.compile(r"\s*\(([^)]+)\)\s*$")
# Clock token inside a free-form detail (KO, Accord, combat riders).
_TIME_TOKEN = re.compile(
    r"~\d+\s*[smh]|\d+\s+rounds?",
    re.I,
)


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


def _sr_period(text):
    """Add a trailing period so TTS pauses between panel rows."""
    text = str(text).rstrip()
    if not text:
        return text
    if text[-1] not in ".!?:":
        return text + "."
    return text


def _compact_buff_line(buff_line):
    """Name plus remaining time -- drop slash-clause dumps.

    Producers still store the long ``buff_line`` (POW +15% / Extra attack…)
    for schema checks. The player ``aff`` panel is a small UI: bracket name
    and the trailing parenthetical clock, not a topic dump.
    """
    text = str(buff_line or "").strip()
    if not text:
        return ""
    eta = ""
    tail = _ETA_TAIL.search(text)
    if tail:
        eta = tail.group(1).strip()
        text = text[: tail.start()].rstrip()
    if text.startswith("[") and "]" in text:
        name = text[1:text.index("]")].strip() or text
        compact = f"[{name}]"
    elif ":" in text:
        # Path edge lines look like "Hunter edge: FOC +10% …" -- name only.
        compact = text.split(":", 1)[0].strip()
    else:
        compact = text
    if eta:
        return f"{compact}  {eta}"
    return compact


def _compact_label_detail(label, detail):
    """States and combat rows: name, plus a clock when the detail has one."""
    label = str(label or "").strip() or "?"
    detail = str(detail or "").strip()
    if not detail:
        return label
    token = _TIME_TOKEN.search(detail)
    if token:
        return f"{label}  {token.group(0)}"
    return label


def _format_panel(title, body, *, screenreader=False):
    """Compact readout -- not the help TOME frame (bug report 1829).

    Sighted: one header plus indented name/time rows. Screenreader: the
    same words with TTS periods, never a ``Help:`` prefix. Band names
    (Buffs / Debuffs / States) carry meaning; color is not required.
    Always returns a single ``str`` so live ``Session.send`` can encode it.
    """
    if screenreader:
        lines = ["", _sr_period(title), ""]
        for raw in body:
            text = str(raw).strip()
            if not text:
                lines.append("")
                continue
            lines.append(_sr_period(text))
        lines.append("")
        return "\r\n".join(lines)
    parts = [str(title)]
    parts.extend(str(line) for line in body)
    return "\r\n".join(parts)


def format_affects(
    character,
    *,
    screenreader=False,
    game=None,
    public_only=False,
):
    """Compact player-facing buff/debuff list for ``aff`` / ``affects``."""
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
            state_lines.append(
                _compact_label_detail(label, row.get("detail")),
            )
            continue

        buff_line = row.get("buff_line")
        polarity = str(row.get("polarity") or "")

        if category == "timed" or buff_line:
            if public_only and not bool(row.get("public", True)):
                continue
            raw = str(buff_line or row.get("label") or rid)
            line = _compact_buff_line(raw)
            if polarity == "debuff":
                debuff_lines.append(line)
            else:
                buff_lines.append(line)
            continue

        # Legacy hook rows (KO, Accord) -- name plus clock, not the help dump.
        label = str(row.get("label") or rid or "?")
        line = _compact_label_detail(label, row.get("detail"))
        debuff_lines.append(line)

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

    return _format_panel(
        "Active effects", body, screenreader=screenreader,
    )
