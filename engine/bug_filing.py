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

    When the reporter is linked to an Account, the account display name is
    stored on the payload (and shown to staff) -- account names are allowed
    on bug/suggest reports (feature E).
    """
    from engine import reports
    from engine import gm_notify
    from engine import report_context
    from engine import accounts as accounts_mod

    game = getattr(character.session, "game", None)
    ctx = report_context.build(character, game)
    account = accounts_mod.account_for_character(game, character)
    account_name = (
        account.display_name if account is not None else None
    )
    if account_name and isinstance(ctx, dict):
        ctx = dict(ctx)
        ctx["account"] = account_name
    payload = reports.record(
        kind, character.key, description, history, directory=report_dir,
        context=ctx,
    )
    # Also stamp a top-level account field for triage tools.
    if account_name:
        payload["account"] = account_name
        # Rewrite the last JSONL line is awkward; context already has it.
        # Top-level is only on the returned payload / webhook hook copy.
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
        # Refresh account features_suggested from the suggestions log
        # (engine-pure -- no supers import for two-repo purity).
        if account is not None and game is not None:
            try:
                suggest_counts = {}
                for entry in reports.recent(
                    reports.SUGGEST, None, directory=game.report_dir
                ):
                    key = (entry.get("reporter") or "").strip()
                    if key:
                        suggest_counts[key] = suggest_counts.get(key, 0) + 1
                total = 0
                for key in list(account.character_keys):
                    total += int(
                        suggest_counts.get((key or "").strip(), 0)
                    )
                account.features_suggested = total
            except Exception:
                pass
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
        who = character.key
        if account_name:
            who = f"{character.key}({account_name})"
        gm_notify.ping_gms(
            game,
            f"{who} filed {label}: {desc}",
            exclude=character,
        )
    return payload
