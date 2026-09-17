"""engine/verbs/channels.py -- OOC/public-channel blocks + player public nets.

Account-level blocks apply to global OOC and player-created public channels
only — never in-character say, tell, phone, angel radio, or room speech.
"""

from __future__ import annotations


def _require_account(character, game):
    from engine.accounts import account_for_session_character

    account = account_for_session_character(game, character)
    if account is None:
        character.session.send(
            "Link an account first (help account). Mutes and public "
            "channels are stored on your account."
        )
        return None
    return account


def cmd_mute(character, args, game):
    """Ignore an account on OOC and player public channels (not IC speech)."""
    account = _require_account(character, game)
    if account is None:
        return
    from engine.accounts import ooc_block_account

    ok, msg = ooc_block_account(game, account, (args or "").strip())
    character.session.send(msg)


def cmd_unmute(character, args, game):
    """Clear an OOC/public-channel ignore for one account."""
    account = _require_account(character, game)
    if account is None:
        return
    from engine.accounts import ooc_unblock_account

    ok, msg = ooc_unblock_account(game, account, (args or "").strip())
    character.session.send(msg)


def cmd_mutes(character, args, game):
    """List accounts you ignore on OOC and player public channels."""
    account = _require_account(character, game)
    if account is None:
        return
    from engine.accounts import ooc_list_blocked

    labels = ooc_list_blocked(game, account)
    if not labels:
        character.session.send(
            "You are not muting anyone on OOC or player public channels. "
            "Use mute <account|character> to ignore an account out-of-character."
        )
        return
    character.session.send(
        "Muted on OOC / public channels (say, tell, phone unchanged):"
    )
    for label in labels:
        character.session.send(f"  {label}")
    character.session.send("")


def _chans_usage(character):
    character.session.send(
        "Usage: chans list | mine | create <name> <verb> [title] | "
        "remove <name>  (see help chans)"
    )


def cmd_chans(character, args, game):
    """Create or manage player-owned public chat channels."""
    from engine import channels
    from engine.accounts import account_for_session_character

    parts = (args or "").strip().split()
    if not parts:
        _chans_usage(character)
        return
    sub = parts[0].lower()
    account = account_for_session_character(game, character)
    if sub == "list":
        lines = ["Public global channels:"]
        for spec in channels.all_channels():
            if spec.scope != channels.SCOPE_GLOBAL:
                continue
            kind = "built-in" if spec.builtin else "custom"
            owner = (spec.owner_account or "").strip()
            owner_note = f" owner={owner}" if owner else ""
            lines.append(
                f"  {spec.verb:12} {spec.title or spec.name:16} "
                f"audience={spec.audience} ({kind}{owner_note})"
            )
        character.session.send("\r\n".join(lines))
        return
    if account is None:
        character.session.send("Link an account first (help account).")
        return
    if sub == "mine":
        owned = channels.list_player_channels(account.name)
        if not owned:
            character.session.send(
                "You do not own any public channels yet. "
                "Try: chans create music music Music Chat"
            )
            return
        lines = ["Your public channels:"]
        for spec in owned:
            lines.append(
                f"  {spec.name:12} verb={spec.verb} title={spec.title or spec.verb}"
            )
        character.session.send("\r\n".join(lines))
        return
    if sub == "create":
        if len(parts) < 3:
            character.session.send(
                "Usage: chans create <name> <verb> [title]"
            )
            return
        name, verb = parts[1], parts[2]
        title = " ".join(parts[3:]) if len(parts) > 3 else verb
        ok, msg = channels.create_player_channel(
            game, account, name=name, verb=verb, title=title,
        )
        character.session.send(msg)
        if ok:
            conn = getattr(game, "db", None)
            if conn is not None:
                channels.save_custom_registry(conn, game)
        return
    if sub == "remove":
        if len(parts) < 2:
            character.session.send("Usage: chans remove <name>")
            return
        ok, msg = channels.remove_custom_channel(
            game, parts[1], account=account,
        )
        character.session.send(msg)
        if ok:
            conn = getattr(game, "db", None)
            if conn is not None:
                channels.save_custom_registry(conn, game)
        return
    _chans_usage(character)
