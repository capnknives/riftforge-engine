"""social_catalog.py -- the engine's generic canned-social perform kit.

Catalog-driven RP verbs (sigh, laugh, hug, …) share one mechanism:
perspective templates (``self`` / ``target`` / ``others``) with
``{actor}`` / ``{target}`` placeholders, solo vs targeted blocks, and
alias resolution. That mechanism -- not any particular game's verb
copy -- is what lives here.

SUPERS keeps ``supers/content/socials.json`` and command registration
(``make_social_commands``); ``supers/socials.py`` loads the catalog and
re-exports thin wrappers (docs/plans/two_repo_purity.md Phase 7 Stage 9).
Free-form ``emote`` already lives in ``engine/verbs/basic.py`` and is
not this module.

Pure dict + room broadcast: zero ``supers`` imports. Callers inject
``find_in_room(name, candidates)`` so room targeting stays game-owned.
"""

from __future__ import annotations

_RESERVED_META = frozenset({"_guide"})


def validate_perspective(block, where, *, need_target=False, require_keys_fn=None):
    """Validate one solo/targeted perspective block.

    ``require_keys_fn(block, keys, where)`` defaults to a local check
    that every key is present; pass ``engine.content_validate.require_keys``
    when you have that helper.
    """
    if not isinstance(block, dict):
        raise AssertionError(f"{where}: must be a dict of perspective lines")
    needed = ["self", "others"]
    if need_target:
        needed.append("target")
    if require_keys_fn is not None:
        require_keys_fn(block, tuple(needed), where)
    else:
        for key in needed:
            if key not in block:
                raise AssertionError(f"{where}: missing '{key}'")
    for key, line in block.items():
        if key not in ("self", "target", "others"):
            raise AssertionError(f"{where}: unknown perspective {key!r}")
        if not isinstance(line, str) or not line.strip():
            raise AssertionError(f"{where}.{key}: must be a non-empty string")


def validate_social_catalog(data, *, require_keys_fn=None):
    """Fail loud if a socials catalog dict is malformed."""
    if not isinstance(data, dict) or not data:
        raise AssertionError("social catalog: must be a non-empty dict")
    for verb_id, spec in data.items():
        if verb_id in _RESERVED_META:
            continue
        where = f"social catalog: '{verb_id}'"
        if not isinstance(verb_id, str) or not verb_id.isidentifier():
            raise AssertionError(
                f"{where}: verb ids must be Python-identifier strings"
            )
        if not isinstance(spec, dict):
            raise AssertionError(f"{where}: must be a dict")
        has_solo = "solo" in spec
        has_targeted = "targeted" in spec
        if not has_solo and not has_targeted:
            raise AssertionError(
                f"{where}: need at least one of 'solo' or 'targeted'"
            )
        if has_solo:
            validate_perspective(
                spec["solo"], f"{where}.solo",
                require_keys_fn=require_keys_fn,
            )
        if has_targeted:
            validate_perspective(
                spec["targeted"], f"{where}.targeted",
                need_target=True, require_keys_fn=require_keys_fn,
            )
        if spec.get("require_target") and not has_targeted:
            raise AssertionError(
                f"{where}: require_target needs a 'targeted' block"
            )
        if "aliases" in spec:
            aliases = spec["aliases"]
            if not isinstance(aliases, list) or not aliases:
                raise AssertionError(
                    f"{where}: 'aliases' must be a non-empty list"
                )
            for alias in aliases:
                if not isinstance(alias, str) or not alias.isidentifier():
                    raise AssertionError(
                        f"{where}: alias {alias!r} must be an identifier string"
                    )
        if "help" in spec and (
            not isinstance(spec["help"], str) or not spec["help"].strip()
        ):
            raise AssertionError(f"{where}: 'help' must be a non-empty string")


def catalog_verbs(data):
    """verb_id -> spec dict, skipping reserved meta keys."""
    if not isinstance(data, dict):
        return {}
    return {
        key: value for key, value in data.items() if key not in _RESERVED_META
    }


def resolve_verb(name, catalog):
    """Map a typed verb (including aliases) to its primary catalog id.

    Returns None if unknown. ``catalog`` is verb_id -> spec.
    """
    needle = (name or "").strip().lower()
    if not needle or not isinstance(catalog, dict):
        return None
    if needle in catalog:
        return needle
    for verb_id, spec in catalog.items():
        for alias in spec.get("aliases") or ():
            if alias == needle:
                return verb_id
    return None


def _social_face(character, viewer=None):
    """Public label for social templates -- never ``gmspirit:`` / ``husk:`` keys.

    Staff GM form reads ``Name(GM)`` (viewer-independent). When *viewer* is
    set, hood/mask and introduce gates match room look / emote behavior.
    """
    if character is None:
        return "?"
    try:
        from engine.command_support import _display_name
        if viewer is not None:
            return _display_name(character, viewer=viewer)
        return _display_name(character)
    except Exception:
        from engine.command_support import strip_ephemeral_storage_prefix
        return strip_ephemeral_storage_prefix(getattr(character, "key", "?"))


def format_template(
    template,
    actor,
    target=None,
    *,
    actor_viewer=None,
    target_viewer=None,
):
    """Fill ``{actor}`` / ``{target}`` placeholders in a template string."""
    return template.format(
        actor=_social_face(actor, actor_viewer),
        target=_social_face(target, target_viewer) if target is not None else "",
    )


def perform(character, catalog, verb_id, target_name, game, *, find_in_room):
    """Run one social. Returns (ok, actor_message_or_error).

    On success, also sends the target and witness lines (side effect).
    ``find_in_room(name, candidates)`` resolves a room target (pass
    ``command_support._find_character`` from a game package).
    """
    if not isinstance(catalog, dict):
        return False, "Unknown social."
    spec = catalog.get(verb_id)
    if spec is None:
        return False, "Unknown social."

    room = character.location
    if room is None:
        return False, "You are nowhere."

    name = (target_name or "").strip()
    target = None
    if name:
        candidates = [c for c in room.characters() if c is not character]
        target = find_in_room(name, candidates)
        if target is None:
            return False, f"No one named '{name}' is here."

    if target is None:
        if spec.get("require_target"):
            return False, f"Whom do you want to {verb_id}? Try: {verb_id} <name>"
        solo = spec.get("solo")
        if not solo:
            return False, f"That social needs a target. Try: {verb_id} <name>"
        self_line = format_template(
            solo["self"], character, actor_viewer=character,
        )
        others_line = format_template(solo["others"], character)
        room.broadcast(others_line, exclude=character)
        return True, self_line

    targeted = spec.get("targeted")
    if not targeted:
        return False, f"You can't use '{verb_id}' on someone."
    self_line = format_template(
        targeted["self"],
        character,
        target,
        actor_viewer=character,
        target_viewer=character,
    )
    target_line = format_template(
        targeted["target"],
        character,
        target,
        actor_viewer=target,
        target_viewer=target,
    )
    others_line = format_template(targeted["others"], character, target)
    if getattr(target, "session", None) is not None and not getattr(
        target, "asleep", False
    ):
        target.session.send(target_line)
    room.broadcast(others_line, exclude=(character, target))
    return True, self_line


def format_list(catalog):
    """Multi-line index of every social for a ``socials`` / help verb."""
    if not isinstance(catalog, dict):
        return ["No socials loaded."]
    lines = ["Socials (optional [name] targets someone in the room):"]
    for verb_id in sorted(catalog):
        spec = catalog[verb_id]
        help_text = spec.get("help") or verb_id
        aliases = spec.get("aliases") or ()
        extra = f" (also: {', '.join(aliases)})" if aliases else ""
        lines.append(f"  {help_text}{extra}")
    lines.append("See: help socials")
    return lines
