"""
bug_filing.py -- record in-game bug/suggest reports (webhook via reports hook).

Lives in engine/ (not commands.py) so auto_deploy overlays of commands.py from
merged PRs cannot strip the filing path again. The Cursor webhook is fired by
engine/bug_webhook.py's register_after_record hook on reports.record().
"""


def _history_for_bug_subject(reporter):
    """Recent session commands for subject disambiguation (no current bug line)."""
    history = getattr(getattr(reporter, "session", None), "history", None)
    if not history:
        return []
    entries = list(history)
    if entries:
        last_line = entries[-1][0].strip().lower()
        if last_line.startswith("bug ") or last_line in ("bug",):
            entries = entries[:-1]
    from engine.connection import history_line_for_storage

    cleaned = []
    for line, tb in entries:
        cleaned.append([history_line_for_storage(line), tb])
    return cleaned


def _bug_subject_candidates(reporter, game, history=None):
    """Priority-ordered bodies for ``bug <name>`` resolution (bug report 925).

    Global ``game.find_character`` can grab the wrong homonym across the
    atlas (peaceful desk NPC vs nest crook both answering to ``max``).
    Prefer combat focus, co-located bodies, then recent command targets.
    """
    if reporter is None:
        return []

    from engine.command_support import (
        _collect_character_matches,
        _find_character,
        split_shell_args,
    )

    seen = set()
    ordered = []

    def add(ch):
        if ch is None or ch is reporter:
            return
        oid = id(ch)
        if oid in seen:
            return
        seen.add(oid)
        ordered.append(ch)

    room = getattr(reporter, "location", None)

    target = getattr(reporter, "target", None)
    add(target)
    if target is not None and getattr(target, "target", None) is reporter:
        add(target)

    if room is not None:
        try:
            occupants = list(room.characters())
        except Exception:
            occupants = []
        for ch in occupants:
            add(ch)
            if getattr(ch, "target", None) is reporter:
                add(ch)

    pool_so_far = list(ordered)
    for line, _tb in history or []:
        text = (line or "").strip()
        if not text:
            continue
        tokens = split_shell_args(text)
        if len(tokens) < 2:
            continue
        verb = tokens[0].lower()
        if verb in ("bug", "suggest", "typo", "ooc", "say", "tell", "emote"):
            continue
        target_text = " ".join(tokens[1:])
        hit = _find_character(target_text, pool_so_far, self_character=reporter)
        if hit is None and pool_so_far:
            hit = _find_character(target_text, ordered, self_character=reporter)
        add(hit)
        for frag in tokens[1:]:
            for match in _collect_character_matches(
                frag, pool_so_far, self_character=reporter,
            ):
                add(match)

    return ordered


def resolve_bug_subject_character(reporter, query, game, history=None):
    """Resolve a ``bug <name>`` subject with local context before world scan."""
    if reporter is None or game is None:
        return None

    from engine.command_support import (
        _collect_character_matches,
        _find_character,
        sort_character_target_matches,
    )

    if history is None:
        history = _history_for_bug_subject(reporter)

    pool = _bug_subject_candidates(reporter, game, history)
    if pool:
        hit = _find_character(query, pool, self_character=reporter)
        if hit is not None:
            return hit

    finder = getattr(game, "find_character", None)
    if not callable(finder):
        return None
    world_hit = finder(query)
    if world_hit is None:
        return None

    if not pool:
        return world_hit

    pool_matches = _collect_character_matches(
        query, pool, self_character=reporter,
    )
    if not pool_matches:
        return world_hit
    if world_hit in pool_matches:
        return world_hit
    if len(pool_matches) == 1:
        return pool_matches[0]
    return sort_character_target_matches(pool_matches)[0]


def parse_bug_subject(reporter, args, game):
    """Split ``bug`` args into ``(subject_character, description)``.

    When a leading name resolves to another character, the report is *about*
    that body (their diagnostic context is snapshotted). Otherwise the full
    args string is a self-report description -- e.g. ``bug the sword vanished``
    stays about the reporter even when ``the`` is not a character name
    (bug report 203).

    Multi-word names and local context win over bare global lookup (bug
    reports 816, 925): ``bug Max Booth was collared…`` prefers the crook
  Pierre just ``collar``ed in-room over a homonym NPC across town.
    """
    text = (args or "").strip()
    if not text:
        return None, ""

    from engine.command_support import is_self_name, split_shell_args

    tokens = split_shell_args(text)
    if not tokens:
        return None, text

    if is_self_name(tokens[0]) and len(tokens) == 1:
        return None, text

    history = _history_for_bug_subject(reporter)
    pool = _bug_subject_candidates(reporter, game, history)
    world_fallback = None

    for i in range(len(tokens), 0, -1):
        prefix = " ".join(tokens[:i])
        if is_self_name(prefix):
            return None, text
        subject = resolve_bug_subject_character(
            reporter, prefix, game, history=history,
        )
        if subject is None or subject is reporter:
            continue
        rest = " ".join(tokens[i:])
        if subject in pool:
            return subject, rest
        if world_fallback is None:
            world_fallback = (subject, rest)

    if world_fallback is not None:
        return world_fallback

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
    ctx = report_context.build(
        context_character, game,
        history=history, description=description, kind=kind,
    )
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
            f"Add notes with bugs comment {entry_id} <text>. "
            "Staff will triage it; you'll hear back when it's fixed."
        )
    elif kind == reports.HELP:
        character.session.send(
            f"Thanks — help idea #{entry_id} is logged. A GM will review "
            "it and, if it's added, write it up with 'hedit'."
        )
    elif kind == reports.TYPO:
        character.session.send(
            f"Thanks — typo ticket #{entry_id} is logged. "
            f"Add notes with typos comment {entry_id} <text>. "
            "Staff will triage it separately from crash reports."
        )
    else:
        character.session.send(
            f"Thanks — suggestion #{entry_id} is logged. "
            f"Add notes with ideas comment {entry_id} <text>. "
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
    elif kind == reports.TYPO:
        label = f"typo #{entry_id}"
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
        try:
            from engine import discord_staff_reports

            discord_staff_reports.schedule_report(
                kind, payload, directory=report_dir,
            )
        except Exception as exc:
            print(f"[discord_staff_reports] schedule skipped: {exc}", flush=True)
    return payload
