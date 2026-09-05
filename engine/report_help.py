"""Staff diagnostic: which help lookup stage would answer a query.

Mirrors ``cmd_help`` order in ``engine/verbs/basic.py`` without sending
player output -- used by bug-report ``help_lookup`` snapshots (bug 883).
"""

from __future__ import annotations


def diagnose_help_lookup(character, game, query):
    """Return a compact dict: stage, title, optional first_line.

    Stages: index, db_overlay, static_topic, topic_filter,
    topic_filter_miss, command_oneliner, fts, fuzzy, miss.
    """
    from command_support import _is_gm
    from engine.hooks import get_help_topics, is_gm_only_help
    from engine import help_db

    verb = (query or "").strip().lower()
    verb = verb.strip(" \t\"'`.,;:!?()[]{}")
    is_gm_viewer = _is_gm(character)
    out = {"query": verb or "(bare help)"}
    if not verb:
        out["stage"] = "index"
        out["title"] = "help index"
        return out

    if verb in ("topics", "index", "catalog", "all"):
        out["stage"] = "index"
        out["title"] = "help topics (A-Z)"
        return out

    if verb == "gmtopics":
        out["stage"] = "gmhelp_redirect" if is_gm_viewer else "miss"
        out["title"] = "gmhelp topics" if is_gm_viewer else verb
        return out

    topics = get_help_topics() or {}
    db = getattr(game, "db", None) if game is not None else None
    from engine import help_overlay_mode as help_overlay_mode_mod
    if db is not None:
        try:
            db_entry = help_db.get_entry(db, verb, is_gm=is_gm_viewer)
        except Exception:
            db_entry = None
        if db_entry and help_overlay_mode_mod.skip_overlay_at_help_lookup(
            db_entry.get("body_text"), topics.get(verb), game=game,
        ):
            db_entry = None
        if db_entry:
            out["stage"] = "db_overlay"
            out["title"] = db_entry.get("primary_keyword") or verb
            first = (db_entry.get("body_text") or "").strip().split("\n", 1)[0]
            if first:
                out["first_line"] = first[:200]
            return out
        parent = None
        low = verb
        for suffix in (" more", " lore", " extra"):
            if low.endswith(suffix) and len(low) > len(suffix):
                parent = low[: -len(suffix)].strip()
                break
        if parent:
            try:
                parent_entry = help_db.get_entry(
                    db, parent, is_gm=is_gm_viewer,
                )
            except Exception:
                parent_entry = None
            if parent_entry and help_overlay_mode_mod.skip_overlay_at_help_lookup(
                parent_entry.get("body_text"), topics.get(parent), game=game,
            ):
                parent_entry = None
            if parent_entry:
                out["stage"] = "db_overlay"
                out["title"] = parent_entry.get("primary_keyword") or parent
                first = (parent_entry.get("body_text") or "").strip().split("\n", 1)[0]
                if first:
                    out["first_line"] = first[:200]
                return out
    topic = topics.get(verb)
    if topic and is_gm_only_help(verb) and not is_gm_viewer:
        topic = None
    if topic:
        body = topic.strip("\n")
        first = ""
        for line in body.split("\n"):
            if line.strip():
                first = line.strip()
                break
        out["stage"] = "static_topic"
        out["title"] = (first or verb)[:200]
        return out

    if " " in verb:
        head, q = verb.split(None, 1)
        page = topics.get(head)
        if page is not None and is_gm_only_help(head) and not is_gm_viewer:
            page = None
        if page is not None:
            from engine.help_filter import filter_topic_lines

            filtered = filter_topic_lines(page, q)
            out["stage"] = "topic_filter" if filtered else "topic_filter_miss"
            out["title"] = f"{head} -- matches for {q}"
            out["filter_hits"] = len(filtered or [])
            return out

    try:
        from commands import COMMANDS
    except Exception:
        COMMANDS = {}
    entry = COMMANDS.get(verb)
    if entry:
        _, help_text = entry
        if (
            not is_gm_viewer
            and (
                str(help_text).startswith("GM:")
                or str(help_text).startswith("head GM:")
            )
        ):
            entry = None
    if entry:
        _, help_text = entry
        out["stage"] = "command_oneliner"
        out["title"] = verb
        out["first_line"] = str(help_text)[:200]
        return out

    if db is not None:
        try:
            from engine import hooks as hooks_mod

            fts_entry = None
            if verb not in hooks_mod.get_help_fts_blocklist():
                fts_entry = help_db.search_fts(db, verb, is_gm=is_gm_viewer)
            if fts_entry:
                out["stage"] = "fts"
                out["title"] = fts_entry.get("primary_keyword") or verb
                return out
            topic_keys = set(topics)
            if not is_gm_viewer:
                topic_keys = {
                    key for key in topic_keys
                    if not hooks_mod.is_gm_only_help(key)
                }
            suggestion = help_db.fuzzy_suggest(
                db, verb, is_gm=is_gm_viewer,
                extra_candidates=topic_keys | set(COMMANDS),
            )
            if suggestion:
                out["stage"] = "fuzzy"
                out["title"] = suggestion
                return out
        except Exception as exc:
            out["diagnose_error"] = repr(exc)

    out["stage"] = "miss"
    return out
