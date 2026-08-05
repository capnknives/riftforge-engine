"""relate.py -- directed relationship tags (engine/systems/relationships H7d)."""

from engine.systems import relationships as rel_mod


def _format_relationships(character, game):
    """One line per tagged target for bare ``relate``."""
    rel_mod.ensure_defaults(character)
    entries = rel_mod.list_of(character)
    if not entries:
        return "You have not tagged anyone yet."
    lines = ["Your relationship tags:"]
    for other_key, kind in entries:
        theirs = None
        other = game.find_character(other_key)
        if other is not None:
            theirs = rel_mod.get_kind(other, character)
        line = f"  {other_key}: {kind}"
        if theirs:
            line += f" (they see you as {theirs})"
        lines.append(line)
    fav = rel_mod.get_favorite_person_key(character)
    if fav:
        lines.append(f"  favorite: {fav}")
    return "\r\n".join(lines)


def cmd_relate(character, args, game):
    """List or set one-sided relationship tags."""
    rel_mod.ensure_defaults(character)
    parts = (args or "").strip().split()
    if not parts:
        character.session.send(_format_relationships(character, game))
        return

    if parts[0].lower() in ("clear", "remove", "drop") and len(parts) >= 2:
        name = " ".join(parts[1:])
        target = game.find_character(name)
        if target is None:
            character.session.send(f"No one named '{name}' exists.")
            return
        if rel_mod.clear(character, target):
            character.session.send(f"You clear your tag toward {target.key}.")
        else:
            character.session.send(f"You had no tag toward {target.key}.")
        return

    if len(parts) < 2:
        kinds = ", ".join(rel_mod.KINDS)
        character.session.send(
            f"Usage: relate <name> <{kinds}>  -- or relate clear <name>"
        )
        return

    kind_raw = parts[-1]
    name = " ".join(parts[:-1])
    kind_id = rel_mod.normalize_kind(kind_raw)
    if kind_id is None:
        kinds = ", ".join(rel_mod.KINDS)
        character.session.send(f"Unknown kind. Try: {kinds}")
        return
    target = game.find_character(name)
    if target is None:
        character.session.send(f"No one named '{name}' exists.")
        return
    if target is character:
        character.session.send("You can't tag yourself.")
        return
    rel_mod.set_kind(character, target, kind_id)
    theirs = rel_mod.reciprocal_kind(character, target)
    msg = f"You mark {target.key} as {kind_id}."
    if theirs == kind_id:
        msg += " They mark you the same way."
    elif theirs:
        msg += f" They see you as {theirs}."
    character.session.send(msg)


def cmd_friend(character, args, game):
    """Shortcut: friend <name> -> relate <name> friend."""
    name = (args or "").strip()
    if not name:
        character.session.send("Friend who? (see 'help relationships')")
        return
    cmd_relate(character, f"{name} friend", game)
