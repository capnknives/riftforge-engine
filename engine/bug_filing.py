"""
bug_filing.py -- record in-game bug/suggest reports (webhook via reports hook).

Lives in engine/ (not commands.py) so auto_deploy overlays of commands.py from
merged PRs cannot strip the filing path again. The Cursor webhook is fired by
engine/bug_webhook.py's register_after_record hook on reports.record().
"""


def record_and_confirm(character, kind, description, history, report_dir, noun):
    """Append to the JSONL log; confirm to the player (webhook hooks record()).

    Also pings opted-in online staff GMs in dark green so a filed bug or
    suggestion is visible without grepping the log (engine/gm_notify.py).
    """
    from engine import reports
    from engine import gm_notify
    from engine import report_context

    ctx = report_context.build(character, getattr(character.session, "game", None))
    payload = reports.record(
        kind, character.key, description, history, directory=report_dir,
        context=ctx,
    )
    entry_id = payload.get("id", "?")
    if kind == reports.BUG:
        character.session.send(
            f"Thanks — bug ticket #{entry_id} is logged. "
            "Staff will triage it; you'll hear back when it's fixed."
        )
    elif kind == reports.HELP:
        character.session.send(
            f"Thanks — help idea #{entry_id} is logged. A GM will review "
            "it and, if it's added, write it up with 'hedit'."
        )
    else:
        character.session.send(
            f"Thanks — suggestion #{entry_id} is logged."
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
    game = getattr(character.session, "game", None)
    if game is not None:
        gm_notify.ping_gms(
            game,
            f"{character.key} filed {label}: {desc}",
            exclude=character,
        )
    return payload
