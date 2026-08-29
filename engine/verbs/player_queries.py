"""Player query ticket verbs (``query`` / ``queryread`` / …)."""

from __future__ import annotations

from engine import hooks
from engine import player_queries as pq_mod
from engine.command_support import _is_staff_gm
from engine.player_helpers import is_player_helper


def _directory(game):
    return getattr(game, "report_dir", ".")


def _can_read_query(character, entry, game):
    if entry is None:
        return False
    if is_player_helper(game, character):
        return True
    key = (getattr(character, "key", None) or "").strip()
    return key and key == entry.get("reporter_key")


def _can_close(character, game):
    return is_player_helper(game, character) or _is_staff_gm(character)


def cmd_query(character, args, game):
    """Open a player help ticket (async when helpers are offline)."""
    from engine import accounts as accounts_mod

    text = (args or "").strip()
    if not text:
        character.session.send(
            "Usage: query <your question>  -- opens a helper ticket "
            "(see help query). For live chat try question <message>."
        )
        return
    directory = _directory(game)
    pq_mod.ensure_log(directory)
    account = accounts_mod.account_for_character(game, character)
    account_name = account.name if account is not None else ""
    try:
        entry = pq_mod.open_query(
            character.key,
            text,
            directory,
            account=account_name,
        )
    except (pq_mod.PlayerQueriesIOError, ValueError) as exc:
        character.session.send(f"Could not open query: {exc}")
        return
    qid = entry.get("id", "?")
    character.session.send(
        f"Query #{qid} opened. Helpers will reply here or on the "
        f"questions channel when someone is online. "
        f"Type queryread {qid} to review the thread."
    )
    hooks.notify_query_event(game, entry, event="open")
    try:
        from engine import questions_channel

        questions_channel.broadcast_questions(
            game,
            character,
            text,
            speaker_session=character.session,
            prefix=f"[Query #{qid}]",
        )
    except Exception:
        pass


def cmd_querylist(character, args, game):
    """List open helper tickets (helpers and staff)."""
    if not is_player_helper(game, character):
        character.session.send("Helpers only. Try question <message> for live help.")
        return
    entries = pq_mod.recent(_directory(game), open_only=True)
    if not entries:
        character.session.send("No open player queries.")
        return
    lines = ["Open player queries:"]
    for entry in entries:
        lines.append("  " + pq_mod.format_list_line(entry, game=game))
    character.session.send("\r\n".join(lines))


def cmd_queries(character, args, game):
    """Alias for querylist."""
    return cmd_querylist(character, args, game)


def cmd_queryread(character, args, game):
    """Show one query thread (owner or helper)."""
    raw = (args or "").strip()
    if not raw:
        character.session.send("Usage: queryread <id>")
        return
    try:
        qid = int(raw.split()[0])
    except ValueError:
        character.session.send(f"'{raw}' is not a query number.")
        return
    entry = pq_mod.get_by_id(qid, _directory(game))
    if entry is None:
        character.session.send(f"No query #{qid}.")
        return
    if not _can_read_query(character, entry, game):
        character.session.send("That query is not yours.")
        return
    character.session.send(pq_mod.format_thread(entry, game=game))


def cmd_querycomment(character, args, game):
    """Append a comment to a query thread."""
    parts = (args or "").strip().split(None, 1)
    if len(parts) < 2:
        character.session.send("Usage: querycomment <id> <message>")
        return
    try:
        qid = int(parts[0])
    except ValueError:
        character.session.send(f"'{parts[0]}' is not a query number.")
        return
    text = parts[1].strip()
    entry = pq_mod.get_by_id(qid, _directory(game))
    if entry is None:
        character.session.send(f"No query #{qid}.")
        return
    reporter = (entry.get("reporter_key") or "").strip()
    key = (character.key or "").strip()
    helper = is_player_helper(game, character)
    if key != reporter and not helper:
        character.session.send("That query is not yours.")
        return
    role = "helper" if helper and key != reporter else "player"
    try:
        updated = pq_mod.append_comment(
            qid, key, text, _directory(game), role=role,
        )
    except (pq_mod.PlayerQueriesIOError, IndexError, ValueError) as exc:
        character.session.send(str(exc))
        return
    character.session.send(f"Query #{qid} updated.")
    if role == "helper":
        hooks.notify_reporter_live(
            game, updated, author_key=key, text=text,
        )
    else:
        hooks.notify_query_event(game, updated, event="comment")


def cmd_queryclose(character, args, game):
    """Close a query ticket (helpers and staff)."""
    if not _can_close(character, game):
        character.session.send("Helpers only.")
        return
    raw = (args or "").strip()
    if not raw:
        character.session.send("Usage: queryclose <id>")
        return
    try:
        qid = int(raw.split()[0])
    except ValueError:
        character.session.send(f"'{raw}' is not a query number.")
        return
    try:
        entry = pq_mod.close_query(
            qid, _directory(game), closer_key=character.key,
        )
    except (pq_mod.PlayerQueriesIOError, IndexError) as exc:
        character.session.send(str(exc))
        return
    character.session.send(f"Query #{qid} closed.")
    hooks.notify_query_event(game, entry, event="closed")
