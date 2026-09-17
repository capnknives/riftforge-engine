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


# Leading prose tokens players type when starting a sentence -- not
# ``bug <character>`` subject names (bug report 1061).  Without this,
# ``I`` substring-matches The Vo**i**ce and ``the`` matches **The** Voice.
_BUG_SUBJECT_STOPWORDS = frozenset({
    "a", "an", "the", "about", "like", "as", "when", "if", "my", "i",
    "me", "we", "it", "is", "was", "are", "be", "to", "at", "in", "on",
    "for", "so", "or", "and", "but", "not", "no", "yes", "just", "also",
    "this", "that", "with", "from", "have", "has", "had", "can", "cant",
    "cannot", "could", "would", "should", "will", "wont", "won't", "im",
    "i'm", "its", "it's", "they", "them", "their", "there", "then", "than",
})


def _bug_subject_prefix_allowed(token_slice):
    """False when a prefix is plain English, not a deliberate name token.

    Single stopwords (``the``, ``I``) and one-letter needles must not
    resolve to a co-located NPC via substring identity matching.
    Multi-word prefixes that include a real name token (``the voice``,
    ``Max Booth``) stay allowed.
    """
    if not token_slice:
        return False
    lowered = [tok.lower() for tok in token_slice]
    if len(lowered) == 1:
        if len(lowered[0]) <= 1:
            return False
        if lowered[0] in _BUG_SUBJECT_STOPWORDS:
            return False
    elif all(tok in _BUG_SUBJECT_STOPWORDS for tok in lowered):
        return False
    return True


def resolve_bug_subject_character(reporter, query, game, history=None):
    """Resolve a ``bug <name>`` subject with local context before world scan.

    ``@Andri`` may be an account login -- attach that account's character
    (bug report 1363). Tells keep their own targeting rules.
    """
    if reporter is None or game is None:
        return None

    from engine.command_support import (
        _collect_character_matches,
        _find_character,
        resolve_gm_target_character,
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
    world_hit = finder(query) if callable(finder) else None
    if world_hit is not None:
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

    # Account login names (Andri → Ayla) when no character key matched.
    return resolve_gm_target_character(game, query)


def parse_bug_subject(reporter, args, game):
    """Split ``bug`` args into ``(subject_character, description)``.

    A subject character is attached only when the reporter **tags** a name:
    ``@Name`` / ``@Max Booth``, or a quoted ``'Name'`` / ``"Name"`` token from
    ``split_shell_args``. Otherwise the full args string is a self-report --
    e.g. ``bug Ash was stuck`` and ``bug the sword vanished`` stay about the
    reporter even when a homonym NPC exists in the world.
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

    def _attach(name, rest):
        """Resolve an explicit tag; None means fall back to a self-report."""
        if is_self_name(name):
            return None, text
        subject = resolve_bug_subject_character(
            reporter, name, game, history=history,
        )
        if subject is not None and subject is not reporter:
            return subject, rest
        return None, text

    # @Name / @Max Booth … explicit tag (IDEA #393).
    if text.startswith("@"):
        remainder = text[1:].strip()
        if not remainder:
            return None, text
        rem_tokens = split_shell_args(remainder)
        for i in range(len(rem_tokens), 0, -1):
            prefix = " ".join(rem_tokens[:i])
            subject, rest = _attach(prefix, " ".join(rem_tokens[i:]))
            if subject is not None:
                return subject, rest
        return None, text

    # Quoted names: shlex already unwraps, so detect the quote on the raw line
    # (bug report 816 -- ``bug "Earl Jacobs" wallet``).
    if text[:1] in ("'", '"'):
        inner = tokens[0].strip()
        rest = " ".join(tokens[1:])
        return _attach(inner, rest)

    return None, text


def _subject_display_name(subject):
    """Player-facing label for a bug subject (GM ping / player confirm)."""
    from engine.char_identity import legal_public_name

    return legal_public_name(subject, force_surname=True)


def _actor_presence(actor):
    """Short presence label for sidecar actor rows (matches report_context)."""
    try:
        from engine.report_context import _presence_mode

        return _presence_mode(actor)
    except Exception:
        pass
    if getattr(actor, "is_npc", False):
        return "npc"
    if getattr(actor, "session", None) is None:
        return "echo"
    if getattr(actor, "idle_mode", False):
        return "idlemode"
    return "live"


def _actor_in_group(actor):
    """True when the body is following someone or in a follow-bond party."""
    if getattr(actor, "following", None):
        return True
    try:
        from engine import group as group_mod

        return group_mod.in_group(actor)
    except Exception:
        return False


def _filed_by_snapshot(reporter):
    """Reporter identity for bug sidecar -- always the filing character.

    ``context["character"]`` may describe a tagged subject instead; this
    block keeps *who filed* separate for staff triage (bug report 203).
    """
    if reporter is None:
        return {}
    room = getattr(reporter, "location", None)
    row = {
        "key": getattr(reporter, "key", None),
        "room_key": getattr(room, "key", None) if room is not None else None,
        "plane": getattr(room, "plane", None) if room is not None else None,
    }
    occupy = getattr(reporter, "staff_occupy_account", None)
    if occupy:
        row["staff_occupy_account"] = str(occupy)
    from engine.char_identity import persist_cnum_text

    cnum = persist_cnum_text(reporter)
    if cnum:
        row["cnum"] = cnum
    sess = getattr(reporter, "session", None)
    if sess is not None and bool(getattr(sess, "playcast_gm_tools", False)):
        row["playcast_gm_tools"] = True
    return {k: v for k, v in row.items() if v is not None}


def _mentioned_actor_row(actor):
    """One capped sidecar row for a body named in the bug text or history."""
    if actor is None:
        return {}
    room = getattr(actor, "location", None)
    row = {
        "key": getattr(actor, "key", None),
        "room": getattr(room, "key", None) if room is not None else None,
        "presence": _actor_presence(actor),
        "in_vehicle": bool(getattr(actor, "in_vehicle", None)),
        "in_group": _actor_in_group(actor),
    }
    cadence_last = getattr(actor, "_cadence_last_decision", None)
    if cadence_last is not None:
        row["cadence_last"] = str(cadence_last)
    return {k: v for k, v in row.items() if v is not None}


def _description_name_queries(description):
    """Capitalized tokens/phrases in bug prose worth resolving as names.

    ``bug Ash was stuck`` does not tag Ash as subject, but staff still
    want Ash in ``mentioned_actors`` when the reporter names them in prose.
    """
    import re

    text = (description or "").strip()
    if not text:
        return []
    # Up to three Title-case words in a row (``Earl Jacobs``, ``Max Booth``).
    pattern = re.compile(
        r"\b(?:[A-Z][a-z']+)(?:\s+(?:[A-Z][a-z']+)){0,2}\b",
    )
    queries = []
    seen = set()
    for match in pattern.finditer(text):
        phrase = match.group(0).strip()
        parts = phrase.split()
        if not _bug_subject_prefix_allowed(parts):
            continue
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        queries.append(phrase)
    return queries


def _history_name_queries(history):
    """Target strings from recent session commands (skip filing verbs)."""
    from engine.command_support import split_shell_args

    queries = []
    seen = set()
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
        target_text = " ".join(tokens[1:]).strip()
        if target_text:
            key = target_text.lower()
            if key not in seen:
                seen.add(key)
                queries.append(target_text)
        for frag in tokens[1:]:
            frag = (frag or "").strip()
            if not frag or not _bug_subject_prefix_allowed([frag]):
                continue
            key = frag.lower()
            if key in seen:
                continue
            seen.add(key)
            queries.append(frag)
    return queries


def _mentioned_actors_snapshot(
    reporter, game, history, description, *, subject_character=None,
):
    """Capped list of bodies named in the report (not the reporter).

    Resolution reuses ``_bug_subject_candidates`` + ``resolve_bug_subject_character``
    so we do not add a second full-world scan beyond what subject resolution
    already allows. Order: explicit subject, prose names, history targets.
    """
    from engine.report_debug import MENTIONED_ACTORS_CAP

    if reporter is None or game is None:
        return []

    if history is None:
        history = _history_for_bug_subject(reporter)

    seen = set()
    ordered = []

    def add(actor):
        if actor is None or actor is reporter:
            return False
        oid = id(actor)
        if oid in seen:
            return False
        seen.add(oid)
        ordered.append(actor)
        return len(ordered) >= MENTIONED_ACTORS_CAP

    if subject_character is not None and add(subject_character):
        return [_mentioned_actor_row(a) for a in ordered]

    for query in _description_name_queries(description):
        hit = resolve_bug_subject_character(
            reporter, query, game, history=history,
        )
        if add(hit):
            return [_mentioned_actor_row(a) for a in ordered]

    for query in _history_name_queries(history):
        hit = resolve_bug_subject_character(
            reporter, query, game, history=history,
        )
        if add(hit):
            return [_mentioned_actor_row(a) for a in ordered]

    return [_mentioned_actor_row(a) for a in ordered]


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
    from engine.char_identity import persist_cnum_text

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
    # Sidecar: reporter vs subject vs other named bodies (Wave 0).
    if isinstance(ctx, dict):
        ctx = dict(ctx)
        ctx["filed_by"] = _filed_by_snapshot(character)
        mentioned = _mentioned_actors_snapshot(
            character, game, history, description,
            subject_character=subject_character,
        )
        if mentioned:
            ctx["mentioned_actors"] = mentioned
    subject_key = None
    if subject_character is not None and subject_character is not character:
        subject_key = subject_character.key
    payload = reports.record(
        kind, character.key, description, history, directory=report_dir,
        context=ctx, subject=subject_key,
        reporter_cnum=persist_cnum_text(character),
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
    elif kind == reports.PREPORT:
        about_clause = ""
        if subject_key and subject_character is not None:
            about_clause = f" about {_subject_display_name(subject_character)}"
        character.session.send(
            f"Logged -- player report #{entry_id}{about_clause} is filed "
            "for staff review."
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
    elif kind == reports.PREPORT:
        label = f"player report #{entry_id}"
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
        headline = reports.staff_ticket_headline(payload, kind=kind)
        if headline:
            gm_notify.ping_gms(game, headline, exclude=character)
        try:
            from engine import discord_staff_reports

            discord_staff_reports.schedule_report(
                kind, payload, directory=report_dir,
            )
        except Exception as exc:
            print(f"[discord_staff_reports] schedule skipped: {exc}", flush=True)
    return payload
