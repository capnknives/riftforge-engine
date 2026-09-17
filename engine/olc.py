"""
engine/olc.py -- in-game menu OLC wizards on the shared kind engine.

Session state lives on ``character._olc_session`` while a wizard is open.
Authorization and catalog persistence register via ``engine.hooks`` so
this module stays game-agnostic.
"""

from __future__ import annotations

import json

from engine import hooks
from engine import style
from engine.content_kinds.engine import (
    _field_names,
    apply_template,
    blank,
    diff_missing,
    explain_kind,
    list_kinds,
    resolve_kind_id,
    validate_kind,
)


def _format_kind_miss(token, hints):
    """Player-facing unknown-kind line with optional did-you-mean list."""
    msg = f"Unknown kind {token!r}."
    if hints:
        msg += " Did you mean: " + ", ".join(hints)
    return msg


def _resolve_kind_or_send(character, token):
    """Resolve a typed kind id; send a miss line and return None on failure."""
    kind_id, hints = resolve_kind_id(token)
    if not kind_id:
        character.session.send(_format_kind_miss(token, hints))
        return None
    return kind_id


def _require_olc(character):
    if hooks.olc_authorizer(character):
        return True
    character.session.send("You aren't authorized to use OLC.")
    return False


def _session(character):
    return getattr(character, "_olc_session", None)


def _set_session(character, data):
    character._olc_session = data


def _clear_session(character):
    if hasattr(character, "_olc_session"):
        del character._olc_session


def _screenreader(character):
    """True when this staffer wants flattened menus and field lines."""
    return bool(getattr(character, "screenreader", False))


def _send_cheat_sheet(character):
    """Bare ``olc`` hub — catalogs, draft verbs, rooms, hedit, Studio.

    This is a command index, not a numbered picker (``format_menu`` would
    invite typing 1–5, which OLC does not dispatch). Same band style as
    the bare ``gm`` sheet so screenreader staff hear section names then verbs.
    """
    sr = _screenreader(character)
    width = int(getattr(character, "sheet_width", None) or 67)
    lines = []
    if sr:
        lines.append(style._tts_period("OLC. In-game building."))
    else:
        lines.append(style.sheet_band("OLC — in-game building", width=width))
    lines.extend([
        "  Catalogs   olc kinds | explain <kind> | new <kind> <id> | "
        "edit <kind> <id>",
        "  Draft      olc set <field> <value> | olc show | olc done | "
        "olc cancel",
        "  Rooms      olc dig <dir> <ROOM NAME> | olc create <ROOM NAME>",
        "             olc link <dir> <VNUM> | olc unlink <dir>",
        "             olc exit | olc extra | olc reset | olc shop",
        "             olc rset <field> <value> | olc here",
        "  Help       hedit <keyword>  (hunder / hnews / hrefresh)",
        "  Maps       Area Studio for grids, atlas, pockets",
        "",
        "'new' starts a catalog draft. 'create' makes a room.",
        "Bare olc exit / extra / rset list this room's options.",
        "Detail: gmhelp olc | gmhelp olc-exits | gmhelp olc-extra | "
        "olc room help",
    ])
    if sr:
        lines = [style._tts_period(line) if line else line for line in lines]
    for line in lines:
        character.session.send(line)


def _kind_leaf_label(kind_id):
    """Short leaf id in parentheses when it differs from the full kind id."""
    leaf = kind_id.split(".")[-1]
    if leaf != kind_id:
        return f"{kind_id} ({leaf})"
    return kind_id


def _format_kinds_by_capability():
    """Group registered kinds by persist capability for honest olc kinds."""
    full_rows = []
    create_rows = []
    none_rows = []
    for kind_id in list_kinds():
        cap = hooks.content_kind_capability(kind_id)
        label = _kind_leaf_label(kind_id)
        if cap == "full":
            full_rows.append(label)
        elif cap == "create":
            create_rows.append(label)
        else:
            none_rows.append(label)

    lines = [
        "Registered kinds — what OLC can do with each:",
        "",
    ]
    if full_rows:
        lines.append("  Create and edit (olc new / olc edit):")
        lines.extend(f"    {row}" for row in full_rows)
        lines.append("")
    if create_rows:
        lines.append("  Create only (olc new; no olc edit yet):")
        lines.extend(f"    {row}" for row in create_rows)
        lines.append("")
    if none_rows:
        lines.append(
            "  Reference only (olc explain / lint; author in Area Studio or JSON):"
        )
        lines.extend(f"    {row}" for row in none_rows)
        lines.append("")
    lines.append(
        "  Rooms are not a kind save: use olc dig / olc link / olc exit "
        "(see olc rooms)."
    )
    lines.append("Use: olc explain <kind>")
    return "\r\n".join(lines)


def _next_missing_field(kind_id, obj):
    missing = diff_missing(kind_id, obj)
    return missing[0] if missing else None


def _prompt_field(character, kind_id, field_name):
    spec = _field_names(kind_id).get(field_name, {})
    doc = spec.get("doc", "")
    ftype = spec.get("type", "string")
    character.session.send(
        f"OLC [{kind_id}] set {field_name} ({ftype}). {doc}"
    )


def _format_show_draft(character, kind_id, entity_id, obj, missing):
    """Draft review — JSON for sighted staff; field lines for screenreader."""
    header = f"Draft {kind_id} id={entity_id or '?'}"
    if _screenreader(character):
        lines = [header]
        for key, value in obj.items():
            lines.append(f"{key}: {value}")
        lines.append(
            "Missing: " + (", ".join(missing) if missing else "(none)")
        )
        return "\r\n".join(lines)
    return (
        f"{header}\r\n"
        + json.dumps(obj, indent=2)
        + "\r\nMissing: "
        + (", ".join(missing) if missing else "(none)")
    )


def cmd_olc(character, args, game):
    """Menu OLC wizards for kind-complete content creation."""
    if not _require_olc(character):
        return
    raw = (args or "").strip()
    if not raw:
        _send_cheat_sheet(character)
        return

    parts = raw.split(None, 1)
    head = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if head in ("help", "?"):
        _send_cheat_sheet(character)
        return

    if head == "kinds":
        character.session.send(_format_kinds_by_capability())
        return

    if head == "explain":
        kind_token = rest.strip()
        if not kind_token:
            character.session.send(
                "Usage: olc explain <kind>\r\n"
                "Type olc kinds for the list."
            )
            return
        kind_id = _resolve_kind_or_send(character, kind_token)
        if not kind_id:
            return
        try:
            character.session.send(explain_kind(kind_id))
        except Exception as err:
            character.session.send(str(err))
        return

    if head == "cancel":
        _clear_session(character)
        character.session.send("OLC draft cancelled.")
        return

    if head == "show":
        sess = _session(character)
        if not sess:
            character.session.send("No OLC draft open. Use: olc new <kind>")
            return
        kind_id = sess["kind"]
        obj = sess["obj"]
        missing = diff_missing(kind_id, obj)
        character.session.send(
            _format_show_draft(
                character,
                kind_id,
                sess.get("entity_id"),
                obj,
                missing,
            )
        )
        return

    if head == "new":
        bits = rest.split(None, 1)
        if not bits:
            character.session.send(
                "Usage: olc new <kind> [entity_id]\r\n"
                "Type olc kinds for Create and edit / Create only."
            )
            return
        kind_token = bits[0]
        kind_id = _resolve_kind_or_send(character, kind_token)
        if not kind_id:
            return
        cap = hooks.content_kind_capability(kind_id)
        if cap == "none":
            character.session.send(
                f"Kind {kind_id!r} is reference-only — no olc new/done save. "
                "Use olc explain <kind> or Area Studio / hand JSON."
            )
            return
        entity_id = bits[1].strip() if len(bits) > 1 else None
        try:
            obj = blank(kind_id)
        except Exception as err:
            character.session.send(str(err))
            return
        _set_session(character, {
            "kind": kind_id,
            "entity_id": entity_id,
            "obj": obj,
        })
        character.session.send(
            f"OLC started: {kind_id}"
            + (f" id={entity_id}" if entity_id else "")
            + ". Use olc set <field> <value> for each required field, "
            "then olc done."
        )
        nxt = _next_missing_field(kind_id, obj)
        if nxt:
            _prompt_field(character, kind_id, nxt)
        return

    if head == "edit":
        bits = rest.split(None, 1)
        if len(bits) < 2:
            character.session.send(
                "Usage: olc edit <kind> <entity_id>\r\n"
                "Type olc kinds for Create and edit."
            )
            return
        kind_token = bits[0]
        kind_id = _resolve_kind_or_send(character, kind_token)
        if not kind_id:
            return
        cap = hooks.content_kind_capability(kind_id)
        if cap != "full":
            if cap == "create":
                character.session.send(
                    f"Kind {kind_id!r} is create-only — use olc new, not edit."
                )
            else:
                character.session.send(
                    f"Kind {kind_id!r} has no olc edit load path."
                )
            return
        entity_id = bits[1].strip()
        if not entity_id:
            character.session.send(
                "Usage: olc edit <kind> <entity_id>\r\n"
                "Type olc kinds for Create and edit."
            )
            return
        try:
            obj = hooks.content_kind_load_entity(kind_id, entity_id)
        except Exception as err:
            character.session.send(str(err))
            return
        if not isinstance(obj, dict):
            character.session.send(
                f"OLC edit refused: loader returned {type(obj).__name__}, "
                "expected dict."
            )
            return
        _set_session(character, {
            "kind": kind_id,
            "entity_id": entity_id,
            "obj": dict(obj),
            "editing": True,
        })
        character.session.send(
            f"OLC edit: {kind_id} id={entity_id}. "
            "Use olc set / olc show, then olc done to upsert."
        )
        return

    if head == "set":
        sess = _session(character)
        if not sess:
            character.session.send("No OLC draft. Use: olc new <kind>")
            return
        bits = rest.split(None, 1)
        if len(bits) < 2:
            character.session.send("Usage: olc set <field> <value...>")
            return
        field_name, value = bits[0], bits[1]
        kind_id = sess["kind"]
        try:
            merged = apply_template(
                kind_id,
                {field_name: value},
                base=sess["obj"],
                reject_unknown=True,
            )
            sess["obj"] = merged
        except Exception as err:
            character.session.send(str(err))
            return
        character.session.send(f"Set {field_name}.")
        nxt = _next_missing_field(kind_id, sess["obj"])
        if nxt:
            _prompt_field(character, kind_id, nxt)
        else:
            character.session.send("All required fields set. Type: olc done")
        return

    if head == "done":
        sess = _session(character)
        if not sess:
            character.session.send("No OLC draft. Use: olc new <kind>")
            return
        kind_id = sess["kind"]
        obj = sess["obj"]
        entity_id = sess.get("entity_id")
        if not entity_id:
            character.session.send(
                "Catalog id required. olc cancel, then "
                "olc new <kind> <entity_id>"
            )
            return
        # Chargen-critical catalogs can brick boot — require explicit confirm.
        confirmed = (rest or "").strip().lower() == "confirm"
        if kind_id in ("catalog.origin", "catalog.discipline") and not confirmed:
            character.session.send(
                "This catalog can stop the game from booting. "
                "Type: olc done confirm"
            )
            return
        try:
            validate_kind(kind_id, obj, reject_unknown=True)
            msg = hooks.content_kind_save_entity(
                kind_id,
                entity_id,
                obj,
                update=bool(sess.get("editing")),
                confirm=confirmed,
            )
        except Exception as err:
            character.session.send(f"OLC save refused: {err}")
            missing = diff_missing(kind_id, obj)
            if missing:
                character.session.send(
                    "Still missing: " + ", ".join(missing)
                )
            return
        _clear_session(character)
        character.session.send(msg)
        print(f"[GM] {character.key} olc saved {kind_id} {entity_id!r}.")
        return

    character.session.send(
        f"Unknown olc subcommand '{head}'. Type 'olc' for help."
    )
