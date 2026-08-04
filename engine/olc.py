"""
engine/olc.py -- in-game menu OLC wizards on the shared kind engine.

Session state lives on ``character._olc_session`` while a wizard is open.
Authorization and catalog persistence register via ``engine.hooks`` so
this module stays game-agnostic.
"""

from __future__ import annotations

import json

from engine import hooks
from engine.content_kinds.engine import (
    _field_names,
    apply_template,
    blank,
    diff_missing,
    explain_kind,
    list_kinds,
    validate_kind,
)


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


def _cheat_sheet():
    return "\r\n".join([
        "GM OLC -- guided content creation (kind profiles)",
        "",
        "  olc kinds                     list registered kinds",
        "  olc explain <kind>            field checklist",
        "  olc new <kind> [id]           start wizard (optional catalog id)",
        "  olc set <field> <value...>    set one field on open wizard",
        "  olc show                      show draft + missing fields",
        "  olc done                      validate and save",
        "  olc cancel                    discard draft",
        "",
        "Kinds refuse incomplete saves and undeclared fields on new rows.",
        "Same rules as tools/content_new.py and Area Studio.",
        "Detail: help olc | help build-kinds",
    ])


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


def cmd_olc(character, args, game):
    """Menu OLC wizards for kind-complete content creation."""
    if not _require_olc(character):
        return
    raw = (args or "").strip()
    if not raw:
        character.session.send(_cheat_sheet())
        return

    parts = raw.split(None, 1)
    head = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if head == "kinds":
        lines = ["Registered kinds:"]
        for kind_id in list_kinds():
            lines.append(f"  {kind_id}")
        lines.append("Use: olc explain <kind>")
        character.session.send("\r\n".join(lines))
        return

    if head == "explain":
        kind_id = rest.strip()
        if not kind_id:
            character.session.send("Usage: olc explain <kind>")
            return
        character.session.send(explain_kind(kind_id))
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
            f"Draft {kind_id} id={sess.get('entity_id') or '?'}\r\n"
            + json.dumps(obj, indent=2)
            + "\r\nMissing: "
            + (", ".join(missing) if missing else "(none)")
        )
        return

    if head == "new":
        bits = rest.split(None, 1)
        if not bits:
            character.session.send("Usage: olc new <kind> [entity_id]")
            return
        kind_id = bits[0]
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
        try:
            validate_kind(kind_id, obj, reject_unknown=True)
            msg = hooks.content_kind_save_entity(kind_id, entity_id, obj)
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
