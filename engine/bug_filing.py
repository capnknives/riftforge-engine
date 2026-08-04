"""
bug_filing.py -- record in-game bug/suggest reports (webhook via reports hook).

Lives in engine/ (not commands.py) so auto_deploy overlays of commands.py from
merged PRs cannot strip the filing path again. The Cursor webhook is fired by
engine/bug_webhook.py's register_after_record hook on reports.record().
"""


def parse_bug_subject(reporter, args, game):
    """Split ``bug`` args into ``(subject_character, description)``.

    When the first token resolves to another character in the world, the
    report is *about* that body (their diagnostic context is snapshotted).
    Otherwise the full args string is a self-report description -- e.g.
    ``bug the sword vanished`` stays about the reporter even when ``the``
    is not a character name (bug report 203).
    """
    text = (args or "").strip()
    if not text:
        return None, ""

    from engine.command_support import is_self_name, resolve_named_character

    parts = text.split(None, 1)
    first = parts[0]
    rest = parts[1].strip() if len(parts) > 1 else ""

    if is_self_name(first):
        return None, text

    subject = resolve_named_character(reporter, first, game=game)
    if subject is not None and subject is not reporter:
        return subject, rest

    return None, text


def _subject_display_name(subject):
    """Player-facing label for a bug subject (GM ping / player confirm)."""
    from engine.char_identity import legal_public_name

    return legal_public_name(subject, force_surname=True)


def record_and_confirm(
    character, kind, description, history, report_dir, noun, *,
    subject_character=None,
):
    """Append to the JSONL log; confirm to the player (webhook hooks record()).

    Also pings opted-in online staff GMs in dark green so a filed bug or
    suggestion is visible without grepping the log (engine/gm_notify.py).

    When the reporter is linked to an Account, the account display name is
    stored on the payload (and shown to staff) -- account names are allowed
    on bug/suggest reports (feature E).
    """
    from engine import reports
    from engine import gm_notify
    from engine import report_context
    from engine import accounts as accounts_mod

    game = getattr(character.session, "game", None)
    # Snapshot the subject's room/vitals when filing about someone else;
    # otherwise keep the reporter's own context (bug report 203).
    context_character = (
        subject_character
        if subject_character is not None else character
    )
    ctx = report_context.build(context_character, game)
    account = accounts_mod.account_for_character(game, character)
    account_name = (
        account.display_name if account is not None else None
    )
    if account_name and isinstance(ctx, dict):
        ctx = dict(ctx)
        ctx["account"] = account_name
    subject_key = None
    if subject_character is not None and subject_character is not character:
        subject_key = subject_character.key
    payload = reports.record(
        kind, character.key, description, history, directory=report_dir,
        context=ctx, subject=subject_key,
    )
    # Also stamp a top-level account field for triage tools.
    if account_name:
        payload["account"] = account_name
        # Rewrite the last JSONL line is awkward; context already has it.
        # Top-level is only on the returned payload / webhook hook copy.
    entry_id = payload.get("id", "?")
    if kind == reports.BUG:
        about_clause = ""
        if subject_key and subject_character is not None:
            about_clause = f" about {_subject_display_name(subject_character)}"
        character.session.send(
            f"Thanks — bug ticket #{entry_id}{about_clause} is logged. "
            "Staff will triage it; you'll hear back when it's fixed."
        )
    elif kind == reports.HELP:
        character.session.send(
            f"Thanks — help idea #{entry_id} is logged. A GM will review "
            "it and, if it's added, write it up with 'hedit'."
        )
    else:
        character.session.send(
            f"Thanks — suggestion #{entry_id} is logged. "
            "Staff will triage it; you'll hear back when it's shipped."
        )
    # Truncate long paste bodies so the staff line stays client-wrappable.
    desc = (description or "").replace("\n", " ").strip()
    if len(desc) > 80:
        desc = desc[:77] + "..."
    if kind == reports.BUG:
        label = f"bug #{entry_id}"
    elif kind == reports.HELP:
        label = f"help idea #{entry_id}"
    else:
        label = f"suggestion #{entry_id}"
    if game is not None:
        # Storage key stays in the JSONL reporter field; staff pings show
        # the legal public name (Zack Markson, not ZackMarkson).
        from engine.char_identity import legal_public_name

        who = legal_public_name(character, force_surname=True)
        if account_name:
            who = f"{who}({account_name})"
        about = ""
        if subject_key and subject_character is not None:
            about = f" about {_subject_display_name(subject_character)}"
        gm_notify.ping_gms(
            game,
            f"{who} filed {label}{about}: {desc}",
            exclude=character,
        )
    return payload
